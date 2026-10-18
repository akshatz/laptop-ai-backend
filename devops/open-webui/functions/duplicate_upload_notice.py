"""
title: Duplicate Upload Notice
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Records every uploaded file's content hash in fn_file_uploads and tells the person, above the answer, when a file they just attached is one they uploaded before.

Open WebUI filter Function (Admin → Functions → import this file, enable it and switch on Global,
so it runs for every model). No restart needed. Needs the fn_file_uploads table (migration
d3f8a1c6e472, applied by open-webui-fn-migrate on `up`).

Why: Open WebUI refuses duplicate content only inside a knowledge base; in a chat every upload is
stored, extracted and put into Milvus again, with no hint that the same file is already there
(2026-10-18: one menu PDF three times, a COI PDF twice).

The inlet looks at the request's files (form_data["files"]: the chat's attachments; the model's own
knowledge entries are skipped) and, for each file of this user not yet in fn_file_uploads, records
it from Open WebUI's file row (meta.file_hash is the sha256 of the uploaded bytes, so renaming
doesn't hide a copy). A newly recorded file whose hash this user uploaded before, as a file that
didn't fail processing, gets a status line naming the earlier upload's date. Each file is reported
once, when it's first seen; files already in the table (all uploads before the migration) aren't.
Any error is logged and the request goes on unchanged.
"""

import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.files import Files

log = logging.getLogger("duplicate_upload_notice")


async def _known(file_ids: list) -> set:
    async with get_async_db_context() as session:
        rows = await session.execute(
            text("SELECT file_id FROM fn_file_uploads WHERE file_id = ANY(:ids)"), {"ids": file_ids}
        )
        return {r[0] for r in rows}


async def _record_and_find_earlier(row: dict) -> dict | None:
    """Insert the upload; return the earliest earlier upload of the same content by the same user."""
    async with get_async_db_context() as session:
        await session.execute(
            text(
                "INSERT INTO fn_file_uploads (file_id, user_id, file_hash, filename, content_type, size, uploaded_at) "
                "VALUES (:file_id, :user_id, :file_hash, :filename, :content_type, :size, :uploaded_at) "
                "ON CONFLICT (file_id) DO NOTHING"
            ),
            row,
        )
        earlier = await session.execute(
            text(
                "SELECT u.file_id, u.filename, u.uploaded_at FROM fn_file_uploads u "
                "LEFT JOIN file f ON f.id = u.file_id "
                "WHERE u.user_id = :user_id AND u.file_hash = :file_hash AND u.file_id <> :file_id "
                "AND u.uploaded_at <= :uploaded_at AND coalesce(f.data->>'status', '') <> 'failed' "
                "ORDER BY u.uploaded_at, u.file_id LIMIT 1"
            ),
            row,
        )
        await session.commit()
        first = earlier.first()
        return dict(first._mapping) if first else None


def _attached_ids(body: dict) -> list:
    ids = []
    for f in body.get("files") or []:
        if isinstance(f, dict) and not f.get("legacy") and f.get("type") not in ("folder", "collection"):
            fid = f.get("id") or (f.get("file") or {}).get("id")
            if fid and fid not in ids:
                ids.append(fid)
    return ids


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(default=True, description="Record uploads and show the notice")
        message: str = Field(
            default="You uploaded {name} before, on {date}. Nothing is wrong; it was just read again.",
            description="Status line; {name} = earlier file name, {date} = its upload date (UTC)",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict = None, __event_emitter__=None) -> dict:
        if not self.valves.enabled or not __user__:
            return body
        try:
            ids = _attached_ids(body)
            if not ids:
                return body
            for fid in [i for i in ids if i not in await _known(ids)]:
                file = await Files.get_file_by_id(fid)
                meta = (file.meta or {}) if file else {}
                if not file or file.user_id != __user__.get("id") or not meta.get("file_hash"):
                    continue
                earlier = await _record_and_find_earlier({
                    "file_id": file.id, "user_id": file.user_id, "file_hash": meta["file_hash"],
                    "filename": file.filename, "content_type": meta.get("content_type"),
                    "size": meta.get("size"), "uploaded_at": file.created_at or int(time.time()),
                })
                if earlier and __event_emitter__:
                    date = datetime.fromtimestamp(earlier["uploaded_at"], timezone.utc).strftime("%d %b %Y")
                    log.warning("Duplicate Upload Notice: %s repeats %s", file.id, earlier["file_id"])
                    await __event_emitter__({"type": "status", "data": {
                        "description": self.valves.message.format(name=earlier["filename"] or "this file", date=date),
                        "done": True}})
        except Exception:
            log.exception("duplicate_upload_notice: could not check uploads")
        return body
