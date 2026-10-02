"""
title: authentik User Cleanup
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: When an admin deletes a user in Open WebUI, also deletes that user's authentik (SSO) account.

Open WebUI event Function (Admin → Functions → import this file, then enable it).

How it works:
- Open WebUI's `user.deleted` event only carries the deleted user's id, after the row is gone, so it
  can't say who the user was. Instead, on `system.startup.completed` (and, defensively, on any event)
  this wraps the ASGI app of Open WebUI's own DELETE /api/v1/users/{user_id} route (Admin → Users →
  Delete): it reads the user's SSO link and email first, lets Open WebUI delete the user, and only if
  that succeeded deletes the authentik account. The original handler is kept on
  `__authentik_cleanup_original__` so reloading the Function doesn't wrap twice.
- The authentik account is found by the OIDC `sub` Open WebUI stored at the user's first SSO login
  (`user.oauth.oidc.sub`, authentik's per-user `uid` with the provider's `hashed_user_id` sub mode).
  Accounts that never signed in with SSO have no `sub`; for those it falls back to the one authentik
  user with exactly that email (`match_by_email`), and does nothing if there are several.
- authentik superusers and service accounts are never deleted, so deleting an Open WebUI account
  can't remove authentik's admin.
- authentik is called with a token for the `open-webui-user-sync` service account
  (authentik/blueprints/open-webui-user-sync.yaml), which may only view and delete users.

Limits: if the authentik call fails (authentik down, wrong token), the Open WebUI user is still deleted
and the failure is only logged — delete the authentik user by hand under Directory → Users. Users
deleted outside the admin endpoint (e.g. through SCIM) aren't handled.
"""

import asyncio
import logging
import os

import aiohttp
from pydantic import BaseModel, Field

from open_webui.models.users import Users

log = logging.getLogger("authentik_user_cleanup")

DELETE_PATH = "/api/v1/users/{user_id}"
PAGE_SIZE = 100

_background_tasks: set[asyncio.Task] = set()


class Event:
    class Valves(BaseModel):
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

    # ---- route wrapper ------------------------------------------------------------------------

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

            status = None

            async def capture(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            await original(scope, receive, capture)

            if user is None or status != 200:
                return
            sub = ((user.oauth or {}).get("oidc") or {}).get("sub")
            email = (user.email or "").strip().lower() or None
            # Don't hold up the admin's request on authentik.
            task = asyncio.create_task(cleanup._delete_in_authentik(sub, email))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        delete_with_authentik_cleanup.__authentik_cleanup_original__ = original
        route.app = delete_with_authentik_cleanup
        log.info("authentik user cleanup enabled on DELETE %s", DELETE_PATH)

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_wrapped_for", None) != id(__app__):
            self._wrap_delete(__app__)
            self._wrapped_for = id(__app__)
