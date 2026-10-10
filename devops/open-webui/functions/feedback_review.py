"""
title: Feedback Review
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Admin page listing rated answers (👎 by default) with the question, the answer, the web search queries, the sources and the user's reason, to find what to fix.

Open WebUI event Function (Admin → Functions → import this file and enable it). Event Functions
can't declare routes, so on `system.startup.completed` (and, defensively, on any event) it
registers one page on Open WebUI's app:

  GET /api/v1/feedback-review                 👎 answers, newest first
  GET /api/v1/feedback-review?rating=all      👍 and 👎
  GET /api/v1/feedback-review?rating=up       👍 only (candidates for a "verified answers" collection)
  ...&days=30                                 only the last 30 days (default 90)
  ...&mine=1                                  only the signed-in person's own ratings ("My feedback")
  ...&format=json                             the same as JSON

Admins see everyone's ratings; every other signed-in user sees only the ratings they gave
themselves, as if ?mine=1 (loader.js links it in the sidebar as "My feedback"). Uses Open WebUI's
get_optional_verified_user_from_request, which accepts the browser's login cookie, so it opens
straight from the address bar; signed-out browsers go to /auth?redirect=<this page>.

Reads Open WebUI's own `feedback` table (filled when someone clicks 👍/👎 under an answer and
optionally picks a reason / writes a comment). Each rating stores a snapshot of the whole chat at
rating time, so a rated answer stays reviewable after the chat is edited or deleted; the live chat
is only used when the snapshot is missing. Read-only: it changes nothing.

Purpose: the weekly "what went wrong" review. Patterns in 👎 (wrong numbers, mixed topics, no
search, stale sources) point at the setting to change — the query/follow-up prompts, the
Chat History Trim filter, search result counts, or the model. Touches Open WebUI internals (feedback
table layout, chat JSON shape): re-check the page after bumping the pinned image.
"""

import html
import json
import logging
import time
from datetime import datetime, timezone

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.utils.auth import get_optional_verified_user_from_request

log = logging.getLogger("feedback_review")

PAGE_PATH = "/api/v1/feedback-review"
ROUTE_NAME = "feedback_review_page"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback review</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:960px;margin:0 auto}
  .card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:14px 16px;margin:12px 0}
  .muted{color:#777;font-size:13px}
  .q{font-weight:600;margin:6px 0}
  pre{white-space:pre-wrap;word-wrap:break-word;font:inherit;margin:4px 0;max-height:16em;overflow:auto}
  .label{font-size:12px;font-weight:600;color:#666;text-transform:uppercase;margin-top:10px}
  .why{background:#fff4e5;border-radius:8px;padding:6px 10px;margin-top:8px}
  nav a{margin-right:12px}
  a{color:inherit}
  @media (prefers-color-scheme:dark){body{background:#111;color:#eee}.card{background:#1c1c1e}.muted,.label{color:#999}.why{background:#3a2a10}}
</style></head><body><main>BODY</main></body></html>"""


def _fmt(ts) -> str:
    if not ts:
        return "—"
    ts = int(ts)
    if ts > 10**12:  # some Open WebUI timestamps are in nanoseconds
        ts //= 10**9
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _messages(chat: dict | None) -> dict:
    """The history.messages dict of a chat, whether given the chat row/response or its inner JSON."""
    if not isinstance(chat, dict):
        return {}
    inner = chat.get("chat") if isinstance(chat.get("chat"), dict) else chat
    return ((inner or {}).get("history") or {}).get("messages") or {}


def _context(chat: dict | None, message_id: str | None) -> dict:
    msgs = _messages(chat)
    answer = msgs.get(message_id or "") or {}
    question = msgs.get(answer.get("parentId") or "") or {}
    queries = []
    for status in answer.get("statusHistory") or []:
        for q in status.get("queries") or []:
            if q not in queries:
                queries.append(q)
    sources = []
    for src in answer.get("sources") or []:
        for meta in src.get("metadata") or []:
            url = meta.get("source") or meta.get("name") or ""
            if url and url not in sources:
                sources.append(url)
        if not src.get("metadata"):
            name = (src.get("source") or {}).get("name") or ""
            if name and name not in sources:
                sources.append(name)
    return {
        "question": question.get("content") or "",
        "answer": answer.get("content") or "",
        "search_queries": queries,
        "sources": sources[:10],
        "found_in_chat": bool(answer),
    }


class Event:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def _rows(self, rating: str, days: int, uid: str | None = None) -> list[dict]:
        where = ["f.type = 'rating'", "f.created_at >= :since"]
        if uid:
            where.append("f.user_id = :uid")
        if rating == "down":
            where.append("(f.data->>'rating') = '-1'")
        elif rating == "up":
            where.append("(f.data->>'rating') = '1'")
        async with get_async_db_context() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT f.id, f.created_at, f.data, f.meta, f.snapshot, u.name AS user_name, c.chat AS live_chat "
                        "FROM feedback f "
                        'LEFT JOIN "user" u ON u.id = f.user_id '
                        "LEFT JOIN chat c ON c.id = f.meta->>'chat_id' "
                        f"WHERE {' AND '.join(where)} ORDER BY f.created_at DESC LIMIT 200"
                    ),
                    {"since": int(time.time()) - max(1, days) * 86400, "uid": uid},
                )
            ).mappings().all()
        out = []
        for r in rows:
            data, meta = r["data"] or {}, r["meta"] or {}
            snap_chat = (r["snapshot"] or {}).get("chat")
            ctx = _context(snap_chat, meta.get("message_id"))
            if not ctx["found_in_chat"] and r["live_chat"]:
                ctx = _context({"chat": r["live_chat"]}, meta.get("message_id"))
            out.append(
                {
                    "id": r["id"],
                    "rated_at": r["created_at"],
                    "rating": "up" if str(data.get("rating")) == "1" else "down",
                    "user": r["user_name"],
                    "model": data.get("model_id"),
                    "reason": data.get("reason"),
                    "comment": data.get("comment"),
                    "tags": meta.get("tags") or data.get("tags") or [],
                    "chat_id": meta.get("chat_id"),
                    **ctx,
                }
            )
        return out

    async def _page(self, request: Request, rating: str = "down", days: int = 90, format: str = "html", mine: bool = False):
        user = await get_optional_verified_user_from_request(request)
        if user is None:
            if format == "json":
                raise HTTPException(status_code=401, detail="Not authenticated")
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/auth?redirect={quote(target, safe='')}", status_code=303)
        # Everyone but admins sees only the ratings they gave themselves; admins too with ?mine=1.
        mine = mine or user.role != "admin"
        rating = rating if rating in ("down", "up", "all") else "down"
        rows = await self._rows(rating, days, uid=user.id if mine else None)
        if format == "json":
            return JSONResponse(rows)

        def block(label, value):
            return f"<div class=label>{label}</div><pre>{html.escape(value)}</pre>" if value else ""

        cards = "".join(
            "<div class=card>"
            f"<div class=muted>{'👍' if r['rating'] == 'up' else '👎'} {_fmt(r['rated_at'])} · "
            f"{html.escape(r['user'] or '(deleted user)')} · {html.escape(r['model'] or '')}</div>"
            f"<div class=q>{html.escape(r['question'] or '(question not found)')}</div>"
            + (
                f"<div class=why><b>{html.escape(r['reason'] or '')}</b> {html.escape(r['comment'] or '')}"
                f"{(' · ' + html.escape(', '.join(map(str, r['tags'])))) if r['tags'] else ''}</div>"
                if (r["reason"] or r["comment"] or r["tags"])
                else ""
            )
            + block("Searched for", "\n".join(r["search_queries"]) or "(no web search)")
            + block("Sources", "\n".join(r["sources"]))
            + block("Answer", r["answer"])
            + "</div>"
            for r in rows
        ) or "<div class='card muted'>No rated answers in this period yet.</div>"
        downs = sum(r["rating"] == "down" for r in rows)
        extra = "&mine=1" if mine else ""
        nav = " ".join(
            f"<a href='{PAGE_PATH}?rating={k}&days={days}{extra}'>{'<b>' + t + '</b>' if k == rating else t}</a>"
            for k, t in (("down", "👎 only"), ("up", "👍 only"), ("all", "All"))
        )
        body = (
            f"<h1>{'My feedback' if mine else 'Feedback review'}</h1>"
            f"<p class=muted>Last {days} days · {len(rows)} rating(s), {downs} 👎 · "
            f"<a href='{PAGE_PATH}?rating={rating}&days={days}{extra}&format=json'>JSON</a> · <a href='/'>Back to Open WebUI</a></p>"
            f"<nav>{nav}</nav>{cards}"
        )
        return HTMLResponse(PAGE.replace("BODY", body), headers={"Cache-Control": "no-store"})

    def _register_routes(self, app) -> None:
        routes = app.router.routes
        routes[:] = [r for r in routes if getattr(r, "name", None) != ROUTE_NAME]
        # Insert at the front so it wins over the SPA static mount at "/".
        routes.insert(0, APIRoute(PAGE_PATH, self._page, methods=["GET"], name=ROUTE_NAME))
        self._routes_for = id(app)

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_routes_for", None) != id(__app__):
            self._register_routes(__app__)
