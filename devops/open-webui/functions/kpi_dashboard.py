"""
title: KPI Dashboard
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Admin page with answer speed, failures, regenerations and 👍/👎 per day, to see whether a settings change made answers faster or better.

Open WebUI event Function (Admin → Functions → import this file and enable it, then restart
`open-webui` so `system.startup.completed` registers the page). Event Functions can't declare
routes, so it registers one page on Open WebUI's app, for admins and one group:

  GET /api/v1/kpi                         last 14 days, all models
  GET /api/v1/kpi?days=30                 last 30 days (1-365)
  GET /api/v1/kpi?model=fast-ai:latest    one model only
  GET /api/v1/kpi?view=revisions          one row per settings revision instead of per day
  GET /api/v1/kpi?view=users              one row per person (admins + `person_view_group`; takes days/model)
  GET /api/v1/kpi?view=me                 the signed-in person's own row only (any signed-in user)
  ...&format=json                         the same as JSON

Admins can always open it; other signed-in users only if they're in the Open WebUI group named by
the `viewer_group` Valve (default "kpi-viewers", matched case-insensitively; Admin → Users → Groups),
otherwise 403. Empty Valve = admins only. Uses Open WebUI's get_optional_verified_user_from_request,
which accepts the browser's login cookie, so it opens straight from the address bar; signed-out
browsers are sent to /auth?redirect=<this page> (JSON requests get 401 instead). The page shows only daily totals
and medians (no questions, answers, names or comments), but on a quiet day small counts can hint at
who asked or rated. Feedback Review stays admin-only. Read-only: it changes nothing.

Metrics (days in UTC, from Open WebUI's own `chat_message` and `feedback` tables):
- Answer time: Ollama's own `total_duration` for the answer (reading the prompt + writing), median
  and p90, and its writing speed (`response_token/s`). Query generation, web search and embedding the
  pages come on top and aren't recorded per answer: Open WebUI stores no finish time (`updated_at`
  moves whenever the chat is saved again), so there's no reliable end-to-end wait here.
- Prompt tokens: median prompt size, mostly the web/RAG chunks; the main driver of answer time on CPU.
- Failed: answers saved with an error.
- No web sources: answers that didn't fail but carry no `web_search` source, i.e. the search returned
  nothing (SearXNG engines blocked, every result filtered) or the request needed no search (greetings,
  poems). A jump here usually means search broke; check SearXNG's log and O2 `openwebui_queries`.
- Regenerated: share of questions with more than one answer from the same model; "at limit" counts
  those with 3 or more (the Regenerate Limit Function's default `max_answers`).
- 👍/👎: ratings given that day. Feedback Review (/api/v1/feedback-review) shows the 👎 details.

Revisions view: devops/open-webui/settings.py records in `fn_settings_revisions` when each version of
settings.yaml went live (apply/export/record). An answer belongs to the revision live when it was
created; answers from before the first recorded revision are shown as "before tracking". Every
revision is listed (the `days` filter doesn't apply there), newest first, the live one marked, with
each value's change from the revision before it. Settings changed in the Admin UI only start a new
revision once exported, and Function code or model changes aren't revisions.

Only chats that still exist count (chat_message rows go with their chat). Temporary chats aren't
saved, so they're not included. Touches Open WebUI internals (chat_message/feedback layout, the
usage JSON Ollama fills): re-check the page after bumping the pinned image.
"""

import html
import logging
import time
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.groups import Groups
from open_webui.utils.auth import get_optional_verified_user_from_request

log = logging.getLogger("kpi_dashboard")

PAGE_PATH = "/api/v1/kpi"
ROUTE_NAME = "kpi_dashboard_page"
DAY = 24 * 3600

# An answer that didn't fail and has no web search source: the search found nothing (e.g. SearXNG's
# engines blocked), or it was a non-search request such as a greeting.
NO_WEB_SQL = """(NOT (m.error IS NOT NULL AND m.error::text <> 'null') AND NOT CASE WHEN json_typeof(m.sources) = 'array'
  THEN EXISTS (SELECT 1 FROM json_array_elements(m.sources) s WHERE s->'source'->>'type' = 'web_search') ELSE false END)"""

# One row per UTC day. `a` = answers, `q` = questions (an answer's parent) per model.
ANSWERS_SQL = """
WITH a AS (
  SELECT m.created_at, m.parent_id, m.model_id,
         m.error IS NOT NULL AND m.error::text <> 'null' AS failed,
         (m.usage->>'total_duration')::float8 / 1e9 AS model_secs,
         (m.usage->>'response_token/s')::float8 AS tps,
         (m.usage->>'prompt_tokens')::float8 AS prompt_tokens,
         NO_WEB AS no_web
  FROM chat_message m
  WHERE m.role = 'assistant' AND m.created_at >= :since
    AND (CAST(:model AS text) IS NULL OR m.model_id = :model)
), q AS (
  SELECT min(created_at) AS created_at, count(*) AS answers
  FROM a WHERE parent_id IS NOT NULL GROUP BY parent_id, model_id
), ad AS (
  SELECT to_char(to_timestamp(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
         count(*) AS answers,
         count(*) FILTER (WHERE failed) AS failed, count(*) FILTER (WHERE no_web) AS no_web,
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

# Same metrics per settings revision. r_id 0 = before the first recorded revision.
REVISIONS_SQL = """
WITH r AS (
  SELECT id AS r_id, content_hash, git_commit, uncommitted, label, source, started_at,
         lead(started_at) OVER (ORDER BY started_at, id) AS ended_at
  FROM fn_settings_revisions
  UNION ALL
  SELECT 0, NULL, NULL, false, 'before tracking', NULL, 0, (SELECT min(started_at) FROM fn_settings_revisions)
), a AS (
  SELECT r.r_id, m.created_at, m.parent_id, m.model_id,
         m.error IS NOT NULL AND m.error::text <> 'null' AS failed,
         (m.usage->>'total_duration')::float8 / 1e9 AS model_secs,
         (m.usage->>'response_token/s')::float8 AS tps,
         (m.usage->>'prompt_tokens')::float8 AS prompt_tokens,
         NO_WEB AS no_web
  FROM chat_message m
  JOIN r ON m.created_at >= r.started_at AND (r.ended_at IS NULL OR m.created_at < r.ended_at)
  WHERE m.role = 'assistant' AND (CAST(:model AS text) IS NULL OR m.model_id = :model)
), q AS (
  SELECT r_id, count(*) AS answers FROM a WHERE parent_id IS NOT NULL GROUP BY r_id, parent_id, model_id
), ar AS (
  SELECT r_id, count(*) AS answers, count(*) FILTER (WHERE failed) AS failed, count(*) FILTER (WHERE no_web) AS no_web,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY model_secs) AS model_p50,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY model_secs) AS model_p90,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY tps) AS tps_p50,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY prompt_tokens) AS prompt_p50
  FROM a GROUP BY r_id
), qr AS (
  SELECT r_id, count(*) AS questions, count(*) FILTER (WHERE answers > 1) AS regenerated,
         count(*) FILTER (WHERE answers >= 3) AS at_limit
  FROM q GROUP BY r_id
), fr AS (
  SELECT r.r_id, count(*) FILTER (WHERE (f.data->>'rating')::int > 0) AS up,
         count(*) FILTER (WHERE (f.data->>'rating')::int < 0) AS down
  FROM feedback f
  JOIN r ON f.created_at >= r.started_at AND (r.ended_at IS NULL OR f.created_at < r.ended_at)
  WHERE f.type = 'rating' AND (CAST(:model AS text) IS NULL OR f.data->>'model_id' = :model)
  GROUP BY r.r_id
)
SELECT r.r_id, r.content_hash, r.git_commit, r.uncommitted, r.label, r.source, r.started_at, r.ended_at,
       coalesce(ar.answers, 0) AS answers, coalesce(ar.failed, 0) AS failed, coalesce(ar.no_web, 0) AS no_web,
       ar.model_p50, ar.model_p90, ar.tps_p50, ar.prompt_p50,
       coalesce(qr.questions, 0) AS questions, coalesce(qr.regenerated, 0) AS regenerated,
       coalesce(qr.at_limit, 0) AS at_limit, coalesce(fr.up, 0) AS up, coalesce(fr.down, 0) AS down
FROM r LEFT JOIN ar USING (r_id) LEFT JOIN qr USING (r_id) LEFT JOIN fr USING (r_id)
WHERE r.r_id <> 0 OR ar.answers IS NOT NULL OR fr.up + fr.down > 0
ORDER BY r.started_at DESC, r.r_id DESC
"""

# Same metrics per person (the chat's owner), plus how many chats they used. Ratings by who gave them.
USERS_SQL = """
WITH a AS (
  SELECT c.user_id, m.chat_id, m.parent_id, m.model_id,
         m.error IS NOT NULL AND m.error::text <> 'null' AS failed,
         (m.usage->>'total_duration')::float8 / 1e9 AS model_secs,
         (m.usage->>'response_token/s')::float8 AS tps,
         (m.usage->>'prompt_tokens')::float8 AS prompt_tokens,
         NO_WEB AS no_web
  FROM chat_message m JOIN chat c ON c.id = m.chat_id
  WHERE m.role = 'assistant' AND m.created_at >= :since
    AND (CAST(:model AS text) IS NULL OR m.model_id = :model)
    AND (CAST(:uid AS text) IS NULL OR c.user_id = :uid)
), q AS (
  SELECT user_id, count(*) AS answers FROM a WHERE parent_id IS NOT NULL GROUP BY user_id, parent_id, model_id
), au AS (
  SELECT user_id, count(*) AS answers, count(*) FILTER (WHERE failed) AS failed, count(*) FILTER (WHERE no_web) AS no_web,
         count(DISTINCT chat_id) AS chats,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY model_secs) AS model_p50,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY tps) AS tps_p50,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY prompt_tokens) AS prompt_p50
  FROM a GROUP BY user_id
), qu AS (
  SELECT user_id, count(*) AS questions, count(*) FILTER (WHERE answers > 1) AS regenerated,
         count(*) FILTER (WHERE answers >= 3) AS at_limit
  FROM q GROUP BY user_id
), fu AS (
  SELECT user_id, count(*) FILTER (WHERE (data->>'rating')::int > 0) AS up,
         count(*) FILTER (WHERE (data->>'rating')::int < 0) AS down
  FROM feedback
  WHERE type = 'rating' AND created_at >= :since
    AND (CAST(:model AS text) IS NULL OR data->>'model_id' = :model)
    AND (CAST(:uid AS text) IS NULL OR user_id = :uid)
  GROUP BY user_id
), ids AS (SELECT user_id FROM au UNION SELECT user_id FROM fu)
SELECT ids.user_id, u.name, u.email,
       coalesce(au.answers, 0) AS answers, coalesce(au.failed, 0) AS failed, coalesce(au.no_web, 0) AS no_web,
       coalesce(au.chats, 0) AS chats,
       au.model_p50, au.tps_p50, au.prompt_p50,
       coalesce(qu.questions, 0) AS questions, coalesce(qu.regenerated, 0) AS regenerated,
       coalesce(qu.at_limit, 0) AS at_limit, coalesce(fu.up, 0) AS up, coalesce(fu.down, 0) AS down
FROM ids LEFT JOIN "user" u ON u.id = ids.user_id
LEFT JOIN au USING (user_id) LEFT JOIN qu USING (user_id) LEFT JOIN fu USING (user_id)
ORDER BY coalesce(qu.questions, 0) DESC, u.name
"""

ANSWERS_SQL, REVISIONS_SQL, USERS_SQL = (q.replace("NO_WEB", NO_WEB_SQL) for q in (ANSWERS_SQL, REVISIONS_SQL, USERS_SQL))

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
    <thead><tr><th>Day (UTC)</th><th>Answers</th><th>Failed</th><th>No web</th><th>Answer p50</th><th>Answer p90</th>
      <th>Prompt tokens</th><th>Tokens/s</th><th>Questions</th><th>Regenerated</th><th>At limit</th>
      <th>👍</th><th>👎</th></tr></thead>
    <tbody>ROWS</tbody>
  </table></div>
  <p class="muted">Answer = Ollama's own time for the answer (reading the prompt + writing); web search and
    embedding come on top and aren't recorded per answer. Prompt tokens and tokens/s are medians. Regenerated = questions with more than
    one answer from the same model; at limit = 3 or more. No web = answers (not failed) without web search
    sources: the search found nothing, or a greeting/poem that needed none. 👎 details:
    <a href="/api/v1/feedback-review">Feedback Review</a>. <a href="/">Back to Open WebUI</a></p>
</main></body></html>
"""


REVISIONS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KPI by revision</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:1300px;margin:0 auto}
  .wrap{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #eee;white-space:nowrap;font-variant-numeric:tabular-nums;vertical-align:top}
  th:first-child,td:first-child{text-align:left;white-space:normal;min-width:220px}
  th{font-weight:600;color:#555}
  tr.live td{background:#eef6ff}
  small{display:block;color:#777;font-size:12px}
  .better{color:#1b6b34}.worse{color:#a4161a}
  .muted{color:#777;font-size:13px}
  a{color:inherit}
  @media (prefers-color-scheme:dark){
    body{background:#111;color:#eee}.wrap{background:#1c1c1e}th,td{border-color:#2c2c2e}th{color:#aaa}
    tr.live td{background:#14263d}small,.muted{color:#999}.better{color:#9be3b0}.worse{color:#ffb3ae}}
</style></head>
<body><main>
  <h1>KPI by settings revision</h1>
  <p class="muted">NAV</p>
  <div class="wrap"><table>
    <thead><tr><th>Revision</th><th>Live (UTC)</th><th>Answers</th><th>Failed</th><th>No web</th><th>Answer p50</th><th>Answer p90</th>
      <th>Prompt tokens</th><th>Tokens/s</th><th>Regenerated</th><th>👍 share</th></tr></thead>
    <tbody>ROWS</tbody>
  </table></div>
  <p class="muted">A revision is a version of devops/open-webui/settings.yaml, recorded by settings.py when it's
    applied or exported (or by <code>settings.py record</code>). An answer counts for the revision that was live when it
    was asked. Small text = change from the revision below; green = better, red = worse. Few answers in a revision
    make its numbers unreliable. <a href="/">Back to Open WebUI</a></p>
</main></body></html>
"""


USERS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TITLE</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:1200px;margin:0 auto}
  .wrap{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #eee;white-space:nowrap;font-variant-numeric:tabular-nums}
  th:first-child,td:first-child{text-align:left}
  th{font-weight:600;color:#555}
  small{display:block;color:#777;font-size:12px}
  .muted{color:#777;font-size:13px}
  a{color:inherit}
  @media (prefers-color-scheme:dark){
    body{background:#111;color:#eee}.wrap{background:#1c1c1e}th,td{border-color:#2c2c2e}th{color:#aaa}small,.muted{color:#999}}
</style></head>
<body><main>
  <h1>TITLE</h1>
  <p class="muted">NAV</p>
  <div class="wrap"><table>
    <thead><tr><th>Person</th><th>Chats</th><th>Questions</th><th>Answers</th><th>Failed</th><th>No web</th><th>Answer p50</th>
      <th>Prompt tokens</th><th>Tokens/s</th><th>Regenerated</th><th>At limit</th><th>👍</th><th>👎</th></tr></thead>
    <tbody>ROWS</tbody>
  </table></div>
  <p class="muted">NOTE Answers count for the chat's owner; Chats = chats with at least one answer in the
    period; ratings count for whoever gave them. Deleted people show their id. <a href="/">Back to Open WebUI</a></p>
</main></body></html>
"""


def _secs(value) -> str:
    return "—" if value is None else f"{value:.0f} s"


def _pct(part: int, whole: int) -> str:
    return "—" if not whole else f"{100 * part / whole:.0f}%"


def _delta(new, old, unit: str = "", lower_is_better: bool = True, digits: int = 0) -> str:
    """"<small>" with the change from the previous revision, coloured when it's better or worse."""
    if new is None or old is None:
        return ""
    diff = new - old
    if round(diff, digits) == 0:
        return "<small>±0</small>"
    better = (diff < 0) == lower_is_better
    return f"<small class='{'better' if better else 'worse'}'>{diff:+.{digits}f}{unit}</small>"


def _ratio(part: int, whole: int):
    return None if not whole else 100 * part / whole


class Event:
    class Valves(BaseModel):
        viewer_group: str = Field(
            default="kpi-viewers",
            description="Open WebUI group whose members may open the page besides admins (empty = admins only).",
        )
        person_view_group: str = Field(
            default="kpi-person-viewers",
            description="Open WebUI group whose members may also open the per-person view, which shows names and "
                        "emails (empty = admins only).",
        )

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
                "day": r["day"], "answers": 0, "failed": 0, "no_web": 0, "model_p50": None, "model_p90": None,
                "tps_p50": None, "prompt_p50": None, "questions": 0, "regenerated": 0, "at_limit": 0,
            })
            row["up"], row["down"] = r["up"], r["down"]
        rows = sorted(by_day.values(), key=lambda r: r["day"], reverse=True)

        total = lambda key: sum(r[key] for r in rows)
        totals = {
            "answers": total("answers"), "failed": total("failed"), "no_web": total("no_web"), "questions": total("questions"),
            "regenerated": total("regenerated"), "at_limit": total("at_limit"),
            "up": total("up"), "down": total("down"),
            "model_p50": model_p50, "model_p90": model_p90, "prompt_p50": prompt_p50,
        }
        return {"days": days, "model": model, "models": models, "totals": totals, "daily": rows}

    async def _revisions(self, model: str | None) -> list[dict]:
        async with get_async_db_context() as session:
            exists = (await session.execute(text("SELECT to_regclass('fn_settings_revisions') IS NOT NULL"))).scalar()
            if not exists:
                return []
            return [dict(r) for r in (await session.execute(text(REVISIONS_SQL), {"model": model})).mappings().all()]

    def _revisions_page(self, revs: list[dict], model: str | None, models: list[str]) -> HTMLResponse:
        day = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))
        body = []
        for i, r in enumerate(revs):
            prev = revs[i + 1] if i + 1 < len(revs) else None
            p = prev or {}
            if r["r_id"] == 0:
                name = "<b>before tracking</b>"
                live = f"until {day(r['ended_at'])}" if r["ended_at"] else "—"
            else:
                commit = f" · {html.escape(r['git_commit'])}" if r["git_commit"] else ""
                dirty = " (uncommitted edits)" if r["uncommitted"] else ""
                name = (f"<b>{html.escape(r['content_hash'])}</b>{commit}{dirty}"
                        + (f"<small>{html.escape(r['label'])}</small>" if r["label"] else "")
                        + f"<small>{html.escape(r['source'])}</small>")
                live = day(r["started_at"]) + " →<br>" + (day(r["ended_at"]) if r["ended_at"] else "<b>now (live)</b>")
            fail, pfail = _ratio(r["failed"], r["answers"]), _ratio(p.get("failed", 0), p.get("answers", 0))
            noweb, pnoweb = _ratio(r["no_web"], r["answers"]), _ratio(p.get("no_web", 0), p.get("answers", 0))
            regen, pregen = _ratio(r["regenerated"], r["questions"]), _ratio(p.get("regenerated", 0), p.get("questions", 0))
            up, pup = _ratio(r["up"], r["up"] + r["down"]), _ratio(p.get("up", 0), p.get("up", 0) + p.get("down", 0))
            fmt = lambda v, f: "—" if v is None else f.format(v)
            cells = [
                name, live, str(r["answers"]),
                fmt(fail, "{:.0f}%") + _delta(fail, pfail, " pt"),
                fmt(noweb, "{:.0f}%") + _delta(noweb, pnoweb, " pt"),
                _secs(r["model_p50"]) + _delta(r["model_p50"], p.get("model_p50"), " s"),
                _secs(r["model_p90"]) + _delta(r["model_p90"], p.get("model_p90"), " s"),
                fmt(r["prompt_p50"], "{:.0f}") + _delta(r["prompt_p50"], p.get("prompt_p50")),
                fmt(r["tps_p50"], "{:.1f}") + _delta(r["tps_p50"], p.get("tps_p50"), lower_is_better=False, digits=1),
                fmt(regen, "{:.0f}%") + _delta(regen, pregen, " pt"),
                (fmt(up, "{:.0f}%") + f" ({r['up']}/{r['up'] + r['down']})") + _delta(up, pup, " pt", lower_is_better=False),
            ]
            live_row = r["r_id"] != 0 and r["ended_at"] is None
            body.append(f"<tr{' class=live' if live_row else ''}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

        link = lambda label, m: (f"<a href='{PAGE_PATH}?view=revisions"
                                 + (f"&model={html.escape(m, quote=True)}" if m else "") + f"'>{html.escape(label)}</a>")
        nav = (
            f"{html.escape(model or 'All models')}. Model: " + " ".join(
                [link("all", None) if model else "<b>all</b>"]
                + [link(m, m) if m != model else f"<b>{html.escape(m)}</b>" for m in models]
            )
            + f" · <a href='{PAGE_PATH}'>By day</a> · "
            + f"<a href='{PAGE_PATH}?view=revisions&format=json" + (f"&model={html.escape(model, quote=True)}" if model else "")
            + "'>JSON</a>"
        )
        empty = ('<tr><td colspan="11">No revisions recorded yet: run <code>python3 devops/open-webui/settings.py '
                 'record --from-git</code> (needs the open-webui-fn-migrate service to have run).</td></tr>')
        page = REVISIONS_PAGE.replace("NAV", nav).replace("ROWS", "".join(body) or empty)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    async def _users_page(self, days: int, model: str | None, format: str, uid: str | None = None):
        """Per-person table; with `uid`, only that person's own row (the ?view=me page)."""
        view = "me" if uid else "users"
        params = {"since": int(time.time()) - days * DAY, "model": model, "uid": uid}
        async with get_async_db_context() as session:
            rows = [dict(r) for r in (await session.execute(text(USERS_SQL), params)).mappings().all()]
            models = [r[0] for r in (await session.execute(text(MODELS_SQL), params)).all()]
        if format == "json":
            return JSONResponse({"days": days, "model": model, "users": rows}, headers={"Cache-Control": "no-store"})
        if uid and not rows:
            rows = [{"user_id": uid, "name": None, "email": None, "chats": 0, "questions": 0, "answers": 0, "failed": 0,
                     "no_web": 0, "model_p50": None, "prompt_p50": None, "tps_p50": None, "regenerated": 0,
                     "at_limit": 0, "up": 0, "down": 0}]

        fmt = lambda v, f: "—" if v is None else f.format(v)
        body = []
        for r in rows:
            name = (f"{html.escape(r['name'] or r['user_id'])}"
                    + (f"<small>{html.escape(r['email'])}</small>" if r["email"] else ""))
            cells = [
                name, r["chats"], r["questions"], r["answers"], _pct(r["failed"], r["answers"]),
                _pct(r["no_web"], r["answers"]), _secs(r["model_p50"]), fmt(r["prompt_p50"], "{:.0f}"), fmt(r["tps_p50"], "{:.1f}"),
                _pct(r["regenerated"], r["questions"]), r["at_limit"], r["up"], r["down"],
            ]
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

        def link(label: str, d: int = days, m: str | None = model, fmt: str = "") -> str:
            query = f"view={view}&days={d}" + (f"&model={html.escape(m, quote=True)}" if m else "") + fmt
            return f"<a href='{PAGE_PATH}?{query}'>{html.escape(label)}</a>"

        nav = (
            f"Last {days} days · {html.escape(model or 'all models')}. Period: "
            + " ".join(link(f"{d}d", d=d) if d != days else f"<b>{d}d</b>" for d in (7, 14, 30, 90))
            + " · Model: " + " ".join(
                [link("all", m=None) if model else "<b>all</b>"]
                + [link(m, m=m) if m != model else f"<b>{html.escape(m)}</b>" for m in models]
            )
            + " · " + link("JSON", fmt="&format=json")
            + ("" if uid else f" · <a href='{PAGE_PATH}?days={days}'>By day</a>")
        )
        title, note = (("My usage", "Only your own chats and ratings.") if uid
                       else ("KPI by person", "Admins and the person-view group only."))
        page = (USERS_PAGE.replace("TITLE", title).replace("NOTE", note).replace("NAV", nav)
                .replace("ROWS", "".join(body) or '<tr><td colspan="13">No answers in this period.</td></tr>'))
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    async def _in_group(self, user, group: str) -> bool:
        wanted = group.strip().lower()
        if not wanted:
            return False
        return any(g.name.strip().lower() == wanted for g in await Groups.get_groups_by_member_id(user.id))

    async def _may_view(self, user) -> bool:
        return (user.role == "admin" or await self._in_group(user, self.valves.viewer_group)
                or await self._in_group(user, self.valves.person_view_group))

    async def _may_view_people(self, user) -> bool:
        return user.role == "admin" or await self._in_group(user, self.valves.person_view_group)

    async def _page(self, request: Request, days: int = 14, model: str | None = None, format: str = "html",
                    view: str = "days"):
        user = await get_optional_verified_user_from_request(request)
        if user is None:
            if format == "json":
                raise HTTPException(status_code=401, detail="Not authenticated")
            # Signed out (e.g. a phone without a session): sign in, then come back here.
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/auth?redirect={quote(target, safe='')}", status_code=303)
        days = max(1, min(days, 365))
        model = model or None
        if view == "me":  # every signed-in person, own numbers only
            return await self._users_page(days, model, format, uid=user.id)
        if not await self._may_view(user):
            raise HTTPException(status_code=403, detail="Only admins and the KPI viewer group can open this page.")
        if view == "users":
            if not await self._may_view_people(user):
                raise HTTPException(status_code=403, detail="The per-person view is for admins and the person-view group only.")
            return await self._users_page(days, model, format)
        if view == "revisions":
            revs = await self._revisions(model)
            if format == "json":
                return JSONResponse({"model": model, "revisions": revs}, headers={"Cache-Control": "no-store"})
            async with get_async_db_context() as session:
                models = [r[0] for r in (await session.execute(text(MODELS_SQL), {"since": 0})).all()]
            return self._revisions_page(revs, model, models)
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
            ("No web sources", _pct(t["no_web"], t["answers"])),
            ("Regenerated", _pct(t["regenerated"], t["questions"])),
            ("👍 share", _pct(t["up"], t["up"] + t["down"]) + f" ({t['up']}/{t['up'] + t['down']})"),
        ]
        tiles_html = "".join(f"<div class='tile'>{html.escape(k)}<b>{html.escape(v)}</b></div>" for k, v in tiles)

        body = []
        for r in data["daily"]:
            cells = [
                r["day"], r["answers"], r["failed"], _pct(r["no_web"], r["answers"]), _secs(r["model_p50"]), _secs(r["model_p90"]),
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
            + f" · <a href='{PAGE_PATH}?view=revisions" + (f"&model={html.escape(model, quote=True)}" if model else "")
            + "'>By settings revision</a>"
            + (f" · <a href='{PAGE_PATH}?view=users&days={days}" + (f"&model={html.escape(model, quote=True)}" if model else "")
               + "'>By person</a>" if await self._may_view_people(user) else "")
        )
        page = (PAGE.replace("NAV", nav).replace("TILES", tiles_html)
                .replace("ROWS", "".join(body) or '<tr><td colspan="13">No answers in this period.</td></tr>'))
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
