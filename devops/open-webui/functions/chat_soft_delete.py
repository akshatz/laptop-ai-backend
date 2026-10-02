"""
title: Chat Soft Delete
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Copies chats into fn_archived_chats before Open WebUI deletes them, so an admin can restore a deleted chat.

Open WebUI event Function (Admin → Functions → import this file, then enable it, then restart
open-webui once so it's active from the first request).

Open WebUI deletes chats for good (rows removed, no trash). This patches its chat repository
(open_webui.models.chats.ChatTable) at class level, so every way a chat is deleted goes through it:
- delete_chat_by_id / delete_chat_by_id_and_user_id: one chat (sidebar → Delete), and the internal
  child chats Open WebUI deletes along with it;
- delete_chats_by_user_id: Settings → Chats → Delete all chats;
- delete_chats_by_user_id_and_folder_id: a folder deleted with its contents.
Before calling the original, the affected chats (row + chat_message rows) are copied into
fn_archived_chats with reason `chat_deleted` (upsert, so a chat deleted again after a restore is
refreshed). If the copy fails, the delete still goes ahead and the error is logged.

Deleting a whole user (Admin → Users → Delete) also calls delete_chats_by_user_id; there the
authentik User Cleanup Function decides (its `archive_chats` Valve, reason `account_deleted`), so
this Function marks UsersTable.delete_user_by_id with a context variable and copies nothing then —
switching that Valve off still erases a user who asked for it.

Restore (admin only): POST /api/v1/archived-chats/{chat_id}/restore puts a `chat_deleted` chat back
for its owner (needs the owner to still exist and the chat id to be free) and removes the archive
row; the archive pages (authentik User Cleanup Function) show a Restore button. Restored chats come
back out of any folder (it may be gone), unpinned and unshared. Deleting a single message inside a
chat is not covered.

Originals are kept on the classes (__soft_delete_original__), so re-importing doesn't wrap twice.
Disabling the Function doesn't unpatch the running process; restart open-webui after. It touches
Open WebUI internals: re-test (delete a chat, see it under /api/v1/archived-chats, restore it) when
bumping the pinned image. Tables come from devops/open-webui/migrations/.
"""

import contextvars
import functools
import logging
import time

from fastapi import Depends, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.chats import ChatTable
from open_webui.models.users import UsersTable
from open_webui.utils.auth import get_admin_user

log = logging.getLogger("chat_soft_delete")

ARCHIVE_PATH = "/api/v1/archived-chats"
ROUTE_TAG = "chat_soft_delete"

# True while Open WebUI is deleting a whole user; the User Cleanup Function archives (or not) then.
_deleting_user = contextvars.ContextVar("chat_soft_delete_deleting_user", default=False)

_ARCHIVE_SQL = """
INSERT INTO fn_archived_chats (chat_id, user_id, user_email, user_name, title, chat, messages,
                               meta, created_at, updated_at, archived_at, archived_by, reason)
SELECT c.id, c.user_id, coalesce(lower(u.email), ''), u.name, c.title, c.chat,
       (SELECT json_agg(row_to_json(m) ORDER BY m.created_at) FROM chat_message m WHERE m.chat_id = c.id),
       c.meta, c.created_at, c.updated_at, :stamp, NULL, 'chat_deleted'
  FROM chat c LEFT JOIN "user" u ON u.id = c.user_id
 WHERE {where}
ON CONFLICT (chat_id) DO UPDATE SET
  user_id = EXCLUDED.user_id, user_email = EXCLUDED.user_email, user_name = EXCLUDED.user_name,
  title = EXCLUDED.title, chat = EXCLUDED.chat, messages = EXCLUDED.messages, meta = EXCLUDED.meta,
  created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at,
  archived_at = EXCLUDED.archived_at, archived_by = NULL, reason = 'chat_deleted'
"""


async def _archive(where: str, params: dict) -> None:
    try:
        async with get_async_db_context() as session:
            result = await session.execute(text(_ARCHIVE_SQL.format(where=where)), {**params, "stamp": int(time.time())})
            await session.commit()
        if result.rowcount:
            log.warning("Chat Soft Delete: archived %d chat(s) before deletion", result.rowcount)
    except Exception:
        log.exception("Chat Soft Delete: archiving failed; the chat(s) are deleted without a copy")


def _install() -> bool:
    """Patches ChatTable/UsersTable once per process; returns True if it did so now."""
    if hasattr(ChatTable, "__soft_delete_original__"):
        return False
    originals = {
        name: getattr(ChatTable, name)
        for name in (
            "delete_chat_by_id",
            "delete_chat_by_id_and_user_id",
            "delete_chats_by_user_id",
            "delete_chats_by_user_id_and_folder_id",
        )
    }
    original_delete_user = UsersTable.delete_user_by_id

    def wrap(name, where, params_of):
        original = originals[name]

        @functools.wraps(original)
        async def patched(self, *args, **kwargs):
            if not _deleting_user.get():
                await _archive(where, params_of(*args, **kwargs))
            return await original(self, *args, **kwargs)

        return patched

    ChatTable.delete_chat_by_id = wrap("delete_chat_by_id", "c.id = :id", lambda id, **_: {"id": id})
    ChatTable.delete_chat_by_id_and_user_id = wrap(
        "delete_chat_by_id_and_user_id", "c.id = :id AND c.user_id = :uid", lambda id, user_id, **_: {"id": id, "uid": user_id}
    )
    ChatTable.delete_chats_by_user_id = wrap(
        "delete_chats_by_user_id", "c.user_id = :uid", lambda user_id, **_: {"uid": user_id}
    )
    ChatTable.delete_chats_by_user_id_and_folder_id = wrap(
        "delete_chats_by_user_id_and_folder_id",
        "c.user_id = :uid AND c.folder_id = :fid",
        lambda user_id, folder_id, **_: {"uid": user_id, "fid": folder_id},
    )

    @functools.wraps(original_delete_user)
    async def delete_user_by_id(self, *args, **kwargs):
        token = _deleting_user.set(True)
        try:
            return await original_delete_user(self, *args, **kwargs)
        finally:
            _deleting_user.reset(token)

    UsersTable.delete_user_by_id = delete_user_by_id
    ChatTable.__soft_delete_original__ = originals
    UsersTable.__soft_delete_original__ = original_delete_user
    return True


class Event:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        if _install():
            log.warning("Chat Soft Delete: deleted chats are now copied to fn_archived_chats first")

    async def _restore(self, chat_id: str, user=Depends(get_admin_user)):
        async with get_async_db_context() as session:
            row = (
                await session.execute(text("SELECT * FROM fn_archived_chats WHERE chat_id = :c"), {"c": chat_id})
            ).mappings().first()
            if row is None:
                raise HTTPException(404, detail="Archived chat not found.")
            if row["reason"] != "chat_deleted":
                raise HTTPException(409, detail="Only deleted chats can be restored; this one belonged to a deleted account.")
            if not (await session.execute(text('SELECT 1 FROM "user" WHERE id = :u'), {"u": row["user_id"]})).first():
                raise HTTPException(409, detail="The chat's owner no longer exists.")
            if (await session.execute(text("SELECT 1 FROM chat WHERE id = :c"), {"c": chat_id})).first():
                raise HTTPException(409, detail="A chat with this id already exists.")
            await session.execute(
                text(
                    "INSERT INTO chat (id, user_id, title, chat, meta, created_at, updated_at, archived, pinned) "
                    "SELECT chat_id, user_id, title, chat, coalesce(meta, '{}'::json), created_at, updated_at, false, false "
                    "FROM fn_archived_chats WHERE chat_id = :c"
                ),
                {"c": chat_id},
            )
            await session.execute(
                text(
                    "INSERT INTO chat_message SELECT * FROM json_populate_recordset(NULL::chat_message, "
                    "(SELECT messages FROM fn_archived_chats WHERE chat_id = :c)) ON CONFLICT DO NOTHING"
                ),
                {"c": chat_id},
            )
            await session.execute(text("DELETE FROM fn_archived_chats WHERE chat_id = :c"), {"c": chat_id})
            await session.commit()
        log.warning("Chat Soft Delete: restored chat %s for user %s", chat_id, row["user_email"])
        return {"restored": chat_id, "user_email": row["user_email"]}

    def _register_routes(self, app) -> None:
        routes = app.router.routes
        name = f"{ROUTE_TAG}_restore"
        routes[:] = [r for r in routes if getattr(r, "name", None) != name]
        # Insert at the front so it wins over the SPA static mount at "/".
        routes.insert(0, APIRoute(ARCHIVE_PATH + "/{chat_id}/restore", self._restore, methods=["POST"], name=name))
        self._routes_for = id(app)

    async def event(self, event: dict, __app__=None):
        _install()
        if __app__ is not None and getattr(self, "_routes_for", None) != id(__app__):
            self._register_routes(__app__)
