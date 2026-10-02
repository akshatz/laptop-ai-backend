"""
title: authentik User Cleanup
author: akshatz
version: 1.1.0
required_open_webui_version: 0.11.3
description: When an admin deletes a user in Open WebUI, archives their chats, deletes their authentik (SSO) account, and removes what Open WebUI leaves behind.

Open WebUI event Function (Admin → Functions → import this file, then enable it, then restart
open-webui once so the delete endpoint is wrapped).

How it works:
- Open WebUI's `user.deleted` event only carries the deleted user's id, after the row is gone, so it
  can't say who the user was. Instead, on `system.startup.completed` (and, defensively, on any event)
  this wraps the ASGI app of Open WebUI's own DELETE /api/v1/users/{user_id} route (Admin → Users →
  Delete). The original handler is kept on `__authentik_cleanup_original__` so reloading the
  Function doesn't wrap twice. For each delete:
  1. With `archive_chats` on (default), the user's chats are copied into the `fn_archived_chats`
     table (created by the repo's Function migrations, devops/open-webui/migrations/) — Open WebUI
     deletes chats with the account. If archiving fails, the user is NOT deleted (500). Switch it
     off before deleting someone who asked to be erased completely.
  2. Open WebUI deletes the user (account, login, chats, group memberships, API keys). If that
     fails, the archive copies from step 1 are removed again.
  3. Only after a successful delete, in the background: the authentik account is deleted, and what
     Open WebUI leaves behind is purged — memories (and their `user-memory-<id>` vectors), notes,
     tags, chat-file links, uploaded files (knowledge-base links, the file on disk and its
     `file-<id>` vectors; Open WebUI's own file delete doesn't drop the Milvus collection), and this
     repo's fn_email_verified / fn_password_age rows. fn_user_sso_status is kept on purpose: its sync
     script marks people as `removed`.
- The authentik account is found by the OIDC `sub` Open WebUI stored at the user's first SSO login
  (`user.oauth.oidc.sub`, authentik's per-user `uid` with the provider's `hashed_user_id` sub mode).
  Accounts that never signed in with SSO have no `sub`; for those it falls back to the one authentik
  user with exactly that email (`match_by_email`), and does nothing if there are several.
- authentik superusers and service accounts are never deleted, so deleting an Open WebUI account
  can't remove authentik's admin.
- authentik is called with a token for the `open-webui-user-sync` service account
  (authentik/blueprints/open-webui-user-sync.yaml), which may only view and delete users.
- Archived chats are listed for admins at GET /api/v1/archived-chats (add `?format=json` for JSON),
  one chat at /api/v1/archived-chats/{chat_id} (readable transcript; `?format=json` downloads it),
  and DELETE /api/v1/archived-chats/{chat_id} removes one. Admin-only via Open WebUI's own
  get_admin_user, which accepts the browser's login cookie. Attachments aren't archived.

Limits: if the authentik call or the purge fails, the Open WebUI user is still deleted and the failure
is logged — delete the authentik user by hand under Directory → Users. Users deleted outside the
admin endpoint (e.g. through SCIM) aren't handled.
"""

import asyncio
import html
import json
import logging
import os
import time
from datetime import datetime, timezone

import aiohttp
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.storage.provider import Storage
from open_webui.utils.auth import decode_token, get_admin_user

log = logging.getLogger("authentik_user_cleanup")

DELETE_PATH = "/api/v1/users/{user_id}"
ARCHIVE_PATH = "/api/v1/archived-chats"
ROUTE_TAG = "authentik_user_cleanup"
PAGE_SIZE = 100

_background_tasks: set[asyncio.Task] = set()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TITLE</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:960px;margin:0 auto}
  .wrap{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}
  th{font-weight:600;color:#555;white-space:nowrap}
  .muted{color:#777;font-size:13px}
  .msg{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px 16px;margin:10px 0}
  .role{font-size:12px;font-weight:600;color:#666;text-transform:uppercase;margin-bottom:6px}
  .msg pre{white-space:pre-wrap;word-wrap:break-word;font:inherit;margin:0}
  a{color:inherit}
  button{font:inherit;font-size:13px;padding:4px 10px;border-radius:6px;border:1px solid #c33;background:#fff;color:#c33;cursor:pointer}
  @media (prefers-color-scheme:dark){
    body{background:#111;color:#eee}.wrap,.msg{background:#1c1c1e}th,td{border-color:#2c2c2e}th{color:#aaa}
    .muted,.role{color:#999}button{background:#1c1c1e}}
</style></head>
<body><main>BODY</main>
<script>
async function del(id){
  if(!confirm("Delete this archived chat permanently?")) return;
  const r = await fetch("ARCHIVE_PATH/" + encodeURIComponent(id), {method: "DELETE", credentials: "include"});
  if (r.ok) location.href = "ARCHIVE_PATH"; else alert("Delete failed: " + r.status);
}
</script></body></html>
""".replace("ARCHIVE_PATH", ARCHIVE_PATH)


def _fmt(ts) -> str:
    if not ts:
        return "—"
    ts = int(ts)
    if ts > 10**12:  # some Open WebUI timestamps are in nanoseconds
        ts //= 10**9
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        PAGE.replace("TITLE", html.escape(title)).replace("BODY", body), headers={"Cache-Control": "no-store"}
    )


def _transcript(chat: dict | None, messages: list | None) -> list[tuple[str, str]]:
    """(role, text) pairs, from the chat JSON's message list, else the chat_message rows."""
    out = []
    for m in (chat or {}).get("messages") or []:
        out.append((m.get("role", "?"), m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))))
    if not out:
        for m in messages or []:
            content = m.get("content")
            out.append((m.get("role", "?"), content if isinstance(content, str) else json.dumps(content)))
    return out


class Event:
    class Valves(BaseModel):
        archive_chats: bool = Field(
            default=True,
            description="Copy a deleted user's chats into the admin-only archive first. Switch off before deleting "
            "someone who asked to be erased completely.",
        )
        authentik_url: str = Field(
            default="",
            description="Empty → $AUTHENTIK_API_URL, else http://authentik-server:9000 (authentik inside the "
            "Docker network).",
        )
        api_token: str = Field(
            default="", description="Empty → $AUTHENTIK_API_TOKEN (recommended, set from .env in compose)."
        )
        match_by_email: bool = Field(
            default=True,
            description="For users who never signed in with SSO, delete the single authentik user with the same "
            "email.",
        )

    def __init__(self):
        self.valves = self.Valves()

    # ---- authentik API ------------------------------------------------------------------------

    def _base_url(self) -> str:
        url = self.valves.authentik_url or os.environ.get("AUTHENTIK_API_URL") or "http://authentik-server:9000"
        return f"{url.rstrip('/')}/api/v3"

    def _headers(self) -> dict | None:
        token = self.valves.api_token or os.environ.get("AUTHENTIK_API_TOKEN")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"} if token else None

    async def _users(self, session: aiohttp.ClientSession, **params) -> list[dict]:
        users, page = [], 1
        while page:
            async with session.get(
                f"{self._base_url()}/core/users/", params={**params, "page": page, "page_size": PAGE_SIZE}
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
            users.extend(data.get("results", []))
            page = (data.get("pagination") or {}).get("next") or 0
        return users

    async def _find(self, session: aiohttp.ClientSession, sub: str | None, email: str | None) -> dict | None:
        if sub:
            # The users API can't filter by uid, but returns it; fine for a small user base.
            return next((u for u in await self._users(session) if u.get("uid") == sub), None)
        if email and self.valves.match_by_email:
            matches = [u for u in await self._users(session, email=email) if (u.get("email") or "").lower() == email]
            if len(matches) > 1:
                log.warning("Not deleting authentik user for %s: %d accounts share that email", email, len(matches))
                return None
            return matches[0] if matches else None
        return None

    async def _delete_in_authentik(self, sub: str | None, email: str | None) -> None:
        headers = self._headers()
        if headers is None:
            log.error("authentik user cleanup skipped for %s: no API token (AUTHENTIK_API_TOKEN)", email)
            return
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
                user = await self._find(session, sub, email)
                if user is None:
                    log.info("No authentik user to delete for %s", email)
                    return
                if user.get("is_superuser") or user.get("type") not in ("internal", "external"):
                    log.warning("Not deleting authentik user %s: superuser or service account", user.get("username"))
                    return
                async with session.delete(f"{self._base_url()}/core/users/{user['pk']}/") as resp:
                    resp.raise_for_status()
                log.info("Deleted authentik user %s (%s)", user.get("username"), email)
        except Exception:
            log.exception("Failed to delete authentik user for %s; delete it by hand in authentik", email)

    # ---- chat archive (fn_archived_chats) -----------------------------------------------------

    async def _archive_chats(self, user, archived_by: str | None, stamp: int) -> int:
        async with get_async_db_context() as session:
            result = await session.execute(
                text(
                    """
                    INSERT INTO fn_archived_chats (chat_id, user_id, user_email, user_name, title, chat, messages,
                                                   meta, created_at, updated_at, archived_at, archived_by)
                    SELECT c.id, c.user_id, :email, :name, c.title, c.chat,
                           (SELECT json_agg(row_to_json(m) ORDER BY m.created_at)
                              FROM chat_message m WHERE m.chat_id = c.id),
                           c.meta, c.created_at, c.updated_at, :stamp, :by
                      FROM chat c WHERE c.user_id = :uid
                    ON CONFLICT (chat_id) DO NOTHING
                    """
                ),
                {"uid": user.id, "email": (user.email or "").lower(), "name": user.name, "stamp": stamp, "by": archived_by},
            )
            await session.commit()
            return result.rowcount or 0

    async def _unarchive(self, user_id: str, stamp: int) -> None:
        async with get_async_db_context() as session:
            await session.execute(
                text("DELETE FROM fn_archived_chats WHERE user_id = :u AND archived_at = :s"), {"u": user_id, "s": stamp}
            )
            await session.commit()

    # ---- leftovers Open WebUI doesn't delete --------------------------------------------------

    async def _purge_leftovers(self, user_id: str) -> None:
        try:
            async with get_async_db_context() as session:
                files = (
                    await session.execute(text("SELECT id, path FROM file WHERE user_id = :u"), {"u": user_id})
                ).all()
            for file_id, path in files:
                for knowledge in await Knowledges.get_knowledges_by_file_id(file_id):
                    await Knowledges.remove_file_from_knowledge_by_id(knowledge.id, file_id)
                    await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge.id, filter={"file_id": file_id})
                if path:
                    try:
                        await asyncio.to_thread(Storage.delete_file, path)
                    except Exception:
                        log.warning("Couldn't delete uploaded file %s", path)
                if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=f"file-{file_id}"):
                    await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=f"file-{file_id}")
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=f"user-memory-{user_id}"):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=f"user-memory-{user_id}")
            counts = {}
            async with get_async_db_context() as session:
                for table in ("file", "chat_file", "memory", "note", "tag", "fn_email_verified", "fn_password_age"):
                    result = await session.execute(text(f'DELETE FROM "{table}" WHERE user_id = :u'), {"u": user_id})
                    counts[table] = result.rowcount or 0
                await session.commit()
            log.info("Purged leftovers of deleted user %s: %s", user_id, {k: v for k, v in counts.items() if v})
        except Exception:
            log.exception("Failed to purge leftovers of deleted user %s", user_id)

    # ---- route wrapper ------------------------------------------------------------------------

    @staticmethod
    async def _actor_email(scope) -> str | None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = None
        if headers.get("authorization", "").lower().startswith("bearer "):
            token = headers["authorization"][7:]
        else:
            for part in headers.get("cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "token":
                    token = value
        claims = decode_token(token) if token else None
        actor = await Users.get_user_by_id(claims["id"]) if claims and claims.get("id") else None
        return actor.email if actor else None

    def _wrap_delete(self, app) -> None:
        route = next(
            (r for r in app.router.routes if getattr(r, "path", None) == DELETE_PATH and "DELETE" in (r.methods or ())),
            None,
        )
        if route is None:
            log.error("authentik user cleanup disabled: %s route not found", DELETE_PATH)
            return
        # Wrap Open WebUI's original handler, not a wrapper left by an older copy of this module.
        original = getattr(route.app, "__authentik_cleanup_original__", route.app)
        cleanup = self

        async def delete_with_authentik_cleanup(scope, receive, send):
            user_id = (scope.get("path_params") or {}).get("user_id")
            try:
                user = await Users.get_user_by_id(user_id) if user_id else None
            except Exception:
                log.exception("Couldn't look up user %s before deletion", user_id)
                user = None

            stamp = int(time.time())
            archived = 0
            if user is not None and cleanup.valves.archive_chats:
                try:
                    archived = await cleanup._archive_chats(user, await cleanup._actor_email(scope), stamp)
                    log.info("Archived %d chat(s) of %s before deletion", archived, user.email)
                except Exception:
                    log.exception("Archiving chats of %s failed; not deleting the user", user.email)
                    body = json.dumps(
                        {"detail": "Couldn't archive this user's chats, so the user was not deleted. See the logs."}
                    ).encode()
                    await send({"type": "http.response.start", "status": 500,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                    return

            status = None

            async def capture(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            await original(scope, receive, capture)

            if user is None:
                return
            if status != 200:
                if archived:
                    await cleanup._unarchive(user.id, stamp)
                return
            sub = ((user.oauth or {}).get("oidc") or {}).get("sub")
            email = (user.email or "").strip().lower() or None

            async def after_delete():
                await cleanup._delete_in_authentik(sub, email)
                await cleanup._purge_leftovers(user.id)

            # Don't hold up the admin's request on authentik and file cleanup.
            task = asyncio.create_task(after_delete())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        delete_with_authentik_cleanup.__authentik_cleanup_original__ = original
        route.app = delete_with_authentik_cleanup
        log.info("authentik user cleanup enabled on DELETE %s", DELETE_PATH)

    # ---- admin archive pages ------------------------------------------------------------------

    async def _archive_list(self, format: str = "html", user=Depends(get_admin_user)):
        async with get_async_db_context() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT chat_id, user_email, user_name, title, created_at, updated_at, archived_at, archived_by "
                        "FROM fn_archived_chats ORDER BY archived_at DESC, updated_at DESC"
                    )
                )
            ).mappings().all()
        rows = [dict(r) for r in rows]
        if format == "json":
            return JSONResponse(rows)
        body = "".join(
            f"<tr><td>{html.escape(r['user_name'] or '')}<br><span class=muted>{html.escape(r['user_email'])}</span></td>"
            f"<td><a href='{ARCHIVE_PATH}/{html.escape(r['chat_id'])}'>{html.escape(r['title'] or '(untitled)')}</a></td>"
            f"<td>{_fmt(r['updated_at'])}</td><td>{_fmt(r['archived_at'])}"
            f"<br><span class=muted>{html.escape(r['archived_by'] or '')}</span></td>"
            f"<td><button onclick='del({json.dumps(r['chat_id'])})'>Delete</button></td></tr>"
            for r in rows
        ) or "<tr><td colspan=5 class=muted>No archived chats.</td></tr>"
        return _page(
            "Archived chats",
            "<h1>Archived chats</h1><p class=muted>Chats of deleted users, copied just before deletion. "
            f"{len(rows)} chat(s). <a href='{ARCHIVE_PATH}?format=json'>JSON</a> · <a href='/'>Back to Open WebUI</a></p>"
            "<div class=wrap><table><thead><tr><th>User</th><th>Chat</th><th>Last message</th><th>Archived</th>"
            f"<th></th></tr></thead><tbody>{body}</tbody></table></div>",
        )

    async def _archive_get(self, chat_id: str, format: str = "html", user=Depends(get_admin_user)):
        async with get_async_db_context() as session:
            row = (
                await session.execute(text("SELECT * FROM fn_archived_chats WHERE chat_id = :c"), {"c": chat_id})
            ).mappings().first()
        if row is None:
            raise HTTPException(404, detail="Archived chat not found.")
        row = dict(row)
        if format == "json":
            return JSONResponse(
                row, headers={"Content-Disposition": f'attachment; filename="archived-chat-{chat_id}.json"'}
            )
        messages = "".join(
            f"<div class=msg><div class=role>{html.escape(role)}</div><pre>{html.escape(content or '')}</pre></div>"
            for role, content in _transcript(row.get("chat"), row.get("messages"))
        ) or "<p class=muted>No messages.</p>"
        return _page(
            row.get("title") or "Archived chat",
            f"<p class=muted><a href='{ARCHIVE_PATH}'>← Archived chats</a></p>"
            f"<h1>{html.escape(row.get('title') or '(untitled)')}</h1>"
            f"<p class=muted>{html.escape(row.get('user_name') or '')} &lt;{html.escape(row['user_email'])}&gt; · "
            f"created {_fmt(row.get('created_at'))} · archived {_fmt(row.get('archived_at'))} · "
            f"<a href='{ARCHIVE_PATH}/{html.escape(chat_id)}?format=json'>Download JSON</a> · "
            f"<button onclick='del({json.dumps(chat_id)})'>Delete</button></p>{messages}",
        )

    async def _archive_delete(self, chat_id: str, user=Depends(get_admin_user)):
        async with get_async_db_context() as session:
            result = await session.execute(text("DELETE FROM fn_archived_chats WHERE chat_id = :c"), {"c": chat_id})
            await session.commit()
        if not result.rowcount:
            raise HTTPException(404, detail="Archived chat not found.")
        return {"deleted": chat_id}

    def _register_routes(self, app) -> None:
        """(Re)installs the archive routes; replaces any from an older copy of the module."""
        routes = app.router.routes
        names = {f"{ROUTE_TAG}_{n}" for n in ("list", "get", "delete")}
        routes[:] = [r for r in routes if getattr(r, "name", None) not in names]
        # Insert at the front so these win over the SPA static mount at "/".
        for path, endpoint, methods, name in (
            (ARCHIVE_PATH, self._archive_list, ["GET"], "list"),
            (ARCHIVE_PATH + "/{chat_id}", self._archive_get, ["GET"], "get"),
            (ARCHIVE_PATH + "/{chat_id}", self._archive_delete, ["DELETE"], "delete"),
        ):
            routes.insert(0, APIRoute(path, endpoint, methods=methods, name=f"{ROUTE_TAG}_{name}"))

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_wrapped_for", None) != id(__app__):
            self._wrap_delete(__app__)
            self._register_routes(__app__)
            self._wrapped_for = id(__app__)
