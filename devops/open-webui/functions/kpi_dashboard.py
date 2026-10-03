"""
title: KPI Dashboard
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Admin page with answer speed, failures, regenerations and 👍/👎 per day, to see whether a settings change made answers faster or better.

Open WebUI event Function (Admin → Functions → import this file and enable it, then restart
`open-webui` so `system.startup.completed` registers the page). Event Functions can't declare
routes, so it registers one admin-only page on Open WebUI's app:

  GET /api/v1/kpi                         last 14 days, all models
  GET /api/v1/kpi?days=30                 last 30 days (1-365)
  GET /api/v1/kpi?model=fast-ai:latest    one model only
  ...&format=json                         the same as JSON

Guarded by Open WebUI's get_admin_user, which accepts the browser's login cookie, so it opens
straight from the address bar while signed in as admin. Read-only: it changes nothing.

Metrics (days in UTC, from Open WebUI's own `chat_message` and `feedback` tables):
- Answer time: Ollama's own `total_duration` for the answer (reading the prompt + writing), median
  and p90, and its writing speed (`response_token/s`). Query generation, web search and embedding the
  pages come on top and aren't recorded per answer: Open WebUI stores no finish time (`updated_at`
  moves whenever the chat is saved again), so there's no reliable end-to-end wait here.
- Prompt tokens: median prompt size, mostly the web/RAG chunks; the main driver of answer time on CPU.
- Failed: answers saved with an error.
- Regenerated: share of questions with more than one answer from the same model; "at limit" counts
  those with 3 or more (the Regenerate Limit Function's default `max_answers`).
- 👍/👎: ratings given that day. Feedback Review (/api/v1/feedback-review) shows the 👎 details.

Only chats that still exist count (chat_message rows go with their chat). Temporary chats aren't
saved, so they're not included. Touches Open WebUI internals (chat_message/feedback layout, the
usage JSON Ollama fills): re-check the page after bumping the pinned image.
"""

import html
import logging
import time

from fastapi import Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.utils.auth import get_admin_user

log = logging.getLogger("kpi_dashboard")

PAGE_PATH = "/api/v1/kpi"
ROUTE_NAME = "kpi_dashboard_page"
DAY = 24 * 3600

# One row per UTC day. `a` = answers, `q` = questions (an answer's parent) per model.
ANSWERS_SQL = """
WITH a AS (
  SELECT m.created_at, m.parent_id, m.model_id,
         m.error IS NOT NULL AND m.error::text <> 'null' AS failed,
         (m.usage->>'total_duration')::float8 / 1e9 AS model_secs,
         (m.usage->>'response_token/s')::float8 AS tps,
         (m.usage->>'prompt_tokens')::float8 AS prompt_tokens
  FROM chat_message m
  WHERE m.role = 'assistant' AND m.created_at >= :since
    AND (CAST(:model AS text) IS NULL OR m.model_id = :model)
), q AS (
  SELECT min(created_at) AS created_at, count(*) AS answers
  FROM a WHERE parent_id IS NOT NULL GROUP BY parent_id, model_id
), ad AS (
  SELECT to_char(to_timestamp(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
         count(*) AS answers,
         count(*) FILTER (WHERE failed) AS failed,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY model_secs) AS model_p50,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY model_secs) AS model_p90,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY tps) AS tps_p50,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY prompt_tokens) AS prompt_p50
  FROM a GROUP BY 1
), qd AS (
  SELECT to_char(to_timestamp(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
         count(*) AS questions,
         count(*) FILTER (WHERE answers > 1) AS regenerated,
         count(*) FILTER (WHERE answers >= 3) AS at_limit
  FROM q GROUP BY 1
)
SELECT ad.*, coalesce(qd.questions, 0) AS questions, coalesce(qd.regenerated, 0) AS regenerated,
       coalesce(qd.at_limit, 0) AS at_limit
FROM ad LEFT JOIN qd USING (day)
"""

# Period totals: percentiles can't be averaged from the daily rows.
TOTALS_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (usage->>'total_duration')::float8 / 1e9),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY (usage->>'total_duration')::float8 / 1e9),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (usage->>'prompt_tokens')::float8)
FROM chat_message
WHERE role = 'assistant' AND created_at >= :since
  AND (CAST(:model AS text) IS NULL OR model_id = :model)
"""

FEEDBACK_SQL = """
SELECT to_char(to_timestamp(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
       count(*) FILTER (WHERE (data->>'rating')::int > 0) AS up,
       count(*) FILTER (WHERE (data->>'rating')::int < 0) AS down
FROM feedback
WHERE type = 'rating' AND created_at >= :since
  AND (CAST(:model AS text) IS NULL OR data->>'model_id' = :model)
GROUP BY 1
"""

MODELS_SQL = """
SELECT DISTINCT model_id FROM chat_message
WHERE role = 'assistant' AND model_id IS NOT NULL AND created_at >= :since ORDER BY 1
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KPI dashboard</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:1100px;margin:0 auto}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
  .tile{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px 14px}
  .tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
  .wrap{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #eee;white-space:nowrap;font-variant-numeric:tabular-nums}
  th:first-child,td:first-child{text-align:left}
  th{font-weight:600;color:#555}
  .muted{color:#777;font-size:13px}
  a{color:inherit}
  @media (prefers-color-scheme:dark){
    body{background:#111;color:#eee}.tile,.wrap{background:#1c1c1e}th,td{border-color:#2c2c2e}th{color:#aaa}.muted{color:#999}}
</style></head>
<body><main>
  <h1>KPI dashboard</h1>
  <p class="muted">NAV</p>
  <div class="tiles">TILES</div>
  <div class="wrap"><table>
    <thead><tr><th>Day (UTC)</th><th>Answers</th><th>Failed</th><th>Answer p50</th><th>Answer p90</th>
      <th>Prompt tokens</th><th>Tokens/s</th><th>Questions</th><th>Regenerated</th><th>At limit</th>
      <th>👍</th><th>👎</th></tr></thead>
    <tbody>ROWS</tbody>
  </table></div>
  <p class="muted">Answer = Ollama's own time for the answer (reading the prompt + writing); web search and
    embedding come on top and aren't recorded per answer. Prompt tokens and tokens/s are medians. Regenerated = questions with more than
    one answer from the same model; at limit = 3 or more. 👎 details:
    <a href="/api/v1/feedback-review">Feedback Review</a>. <a href="/">Back to Open WebUI</a></p>
</main></body></html>
"""


def _secs(value) -> str:
    return "—" if value is None else f"{value:.0f} s"


def _pct(part: int, whole: int) -> str:
    return "—" if not whole else f"{100 * part / whole:.0f}%"


class Event:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def _data(self, days: int, model: str | None) -> dict:
        params = {"since": int(time.time()) - days * DAY, "model": model}
        async with get_async_db_context() as session:
            answer_rows = (await session.execute(text(ANSWERS_SQL), params)).mappings().all()
            feedback_rows = (await session.execute(text(FEEDBACK_SQL), params)).mappings().all()
            model_p50, model_p90, prompt_p50 = (await session.execute(text(TOTALS_SQL), params)).one()
            models = [r[0] for r in (await session.execute(text(MODELS_SQL), params)).all()]

        by_day: dict[str, dict] = {}
        for r in answer_rows:
            by_day[r["day"]] = {**dict(r), "up": 0, "down": 0}
        for r in feedback_rows:
            row = by_day.setdefault(r["day"], {
                "day": r["day"], "answers": 0, "failed": 0, "model_p50": None, "model_p90": None,
                "tps_p50": None, "prompt_p50": None, "questions": 0, "regenerated": 0, "at_limit": 0,
            })
            row["up"], row["down"] = r["up"], r["down"]
        rows = sorted(by_day.values(), key=lambda r: r["day"], reverse=True)

        total = lambda key: sum(r[key] for r in rows)
        totals = {
            "answers": total("answers"), "failed": total("failed"), "questions": total("questions"),
            "regenerated": total("regenerated"), "at_limit": total("at_limit"),
            "up": total("up"), "down": total("down"),
            "model_p50": model_p50, "model_p90": model_p90, "prompt_p50": prompt_p50,
        }
        return {"days": days, "model": model, "models": models, "totals": totals, "daily": rows}

    async def _page(self, days: int = 14, model: str | None = None, format: str = "html",
                    user=Depends(get_admin_user)):
        days = max(1, min(days, 365))
        model = model or None
        data = await self._data(days, model)
        if format == "json":
            return JSONResponse(data, headers={"Cache-Control": "no-store"})

        t = data["totals"]
        tiles = [
            ("Answer p50", _secs(t["model_p50"])),
            ("Answer p90", _secs(t["model_p90"])),
            ("Prompt tokens", "—" if t["prompt_p50"] is None else f"{t['prompt_p50']:.0f}"),
            ("Answers", str(t["answers"])),
            ("Failed", _pct(t["failed"], t["answers"])),
            ("Regenerated", _pct(t["regenerated"], t["questions"])),
            ("👍 share", _pct(t["up"], t["up"] + t["down"]) + f" ({t['up']}/{t['up'] + t['down']})"),
        ]
        tiles_html = "".join(f"<div class='tile'>{html.escape(k)}<b>{html.escape(v)}</b></div>" for k, v in tiles)

        body = []
        for r in data["daily"]:
            cells = [
                r["day"], r["answers"], r["failed"], _secs(r["model_p50"]), _secs(r["model_p90"]),
                "—" if r["prompt_p50"] is None else f"{r['prompt_p50']:.0f}", "—" if r["tps_p50"] is None else f"{r['tps_p50']:.1f}",
                r["questions"], _pct(r["regenerated"], r["questions"]), r["at_limit"], r["up"], r["down"],
            ]
            body.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")

        def link(label: str, d: int = days, m: str | None = model, fmt: str = "") -> str:
            query = f"days={d}" + (f"&model={html.escape(m, quote=True)}" if m else "") + fmt
            return f"<a href='{PAGE_PATH}?{query}'>{html.escape(label)}</a>"

        nav = (
            f"Last {days} days · {html.escape(model or 'all models')}. Period: "
            + " ".join(link(f"{d}d", d=d) if d != days else f"<b>{d}d</b>" for d in (7, 14, 30, 90))
            + " · Model: " + " ".join(
                [link("all", m=None) if model else "<b>all</b>"]
                + [link(m, m=m) if m != model else f"<b>{html.escape(m)}</b>" for m in data["models"]]
            )
            + " · " + link("JSON", fmt="&format=json")
        )
        page = (PAGE.replace("NAV", nav).replace("TILES", tiles_html)
                .replace("ROWS", "".join(body) or '<tr><td colspan="12">No answers in this period.</td></tr>'))
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    def _register_routes(self, app) -> None:
        """(Re)installs the page route; replaces one from an older copy of the module."""
        routes = app.router.routes
        routes[:] = [r for r in routes if getattr(r, "name", None) != ROUTE_NAME]
        # Insert at the front so it wins over the SPA static mount at "/".
        routes.insert(0, APIRoute(PAGE_PATH, self._page, methods=["GET"], name=ROUTE_NAME))

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_registered_for", None) != id(__app__):
            self._register_routes(__app__)
            self._registered_for = id(__app__)
            log.info("KPI dashboard at %s", PAGE_PATH)
