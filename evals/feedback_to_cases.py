#!/usr/bin/env python3
"""Turn 👎 ratings in Open WebUI into draft eval cases.

Reads Open WebUI's `feedback` table (rating -1) with the rated answer and the question it answered
from `chat_message`, and appends one draft per new rating to the gitignored cases.local.yaml
(family wording stays local). Drafts carry `status: draft`, which run.py skips unless
--include-drafts; review each one, fill in `expect` and remove `status`. To drop a rating for good,
set `status: rejected` instead of deleting the case, or the next run adds it again.

  python3 evals/feedback_to_cases.py            # append new drafts
  python3 evals/feedback_to_cases.py --dry-run  # print them instead
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

import yaml

from run import HERE, load_env

LOCAL = HERE / "cases.local.yaml"

# One row per 👎: feedback id, time, comment, reason, model, answer, question, web sources used.
# chat_message.id is "<chat_id>-<message_id>" while parent_id is the bare message id.
SQL = """
select json_build_object(
  'id', f.id, 'created_at', f.created_at, 'comment', f.data->>'comment', 'reason', f.data->>'reason',
  'model', f.data->>'model_id', 'answer', a.content #>> '{}', 'question', q.content #>> '{}',
  'sources', (select json_agg(distinct s->'source'->>'name') from json_array_elements(
    case when json_typeof(a.sources) = 'array' then a.sources else '[]'::json end) s))
from feedback f
left join chat_message a on a.id = (f.meta->>'chat_id') || '-' || (f.meta->>'message_id')
left join chat_message q on q.id = a.chat_id || '-' || a.parent_id
where (f.data->>'rating') = '-1'
order by f.created_at
"""


def fetch(env: dict) -> list:
    out = subprocess.run(
        ["docker", "exec", env.get("EVAL_POSTGRES_CONTAINER", "laptop-postgres"), "psql",
         "-U", env.get("POSTGRES_USER", "postgres"), "-d", "open_webui", "-At", "-c", SQL],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def known_feedback_ids() -> set:
    ids = set()
    for name in ("cases.yaml", "cases.local.yaml"):
        path = HERE / name
        if path.exists():
            ids |= {c.get("feedback_id") for c in yaml.safe_load(path.read_text()) or []}
    return ids - {None}


def draft(row: dict) -> dict:
    day = datetime.fromtimestamp(row["created_at"], timezone.utc).strftime("%Y-%m-%d")
    comment = (row.get("comment") or "").strip() or f"(no comment, reason: {row.get('reason')})"
    case = {
        "id": f"fb-{row['id'][:8]}",
        "feedback_id": row["id"],
        "status": "draft",
        "category": "grounded-fact",
        "note": f"👎 {day} on {row.get('model')}: {comment}",
        "question": (row.get("question") or "").strip() or "TODO: question not found (chat deleted?)",
        "expect": {"search": True},
        # Shown for review only; run.py ignores unknown keys. Trim before committing anything.
        "rated_answer": (row.get("answer") or "").strip()[:1500],
    }
    if row.get("sources"):
        case["rated_sources"] = [s for s in row["sources"] if s]
    return case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print new drafts, don't write")
    args = parser.parse_args()

    try:
        rows = fetch(load_env())
    except (subprocess.SubprocessError, OSError) as e:
        sys.exit(f"could not read feedback from Postgres: {e}")
    known = known_feedback_ids()
    drafts = [draft(r) for r in rows if r["id"] not in known]
    if not drafts:
        print(f"no new 👎 ratings ({len(rows)} in total, all already cases)")
        return

    text = yaml.safe_dump(drafts, allow_unicode=True, sort_keys=False, width=100)
    if args.dry_run:
        print(text)
        return
    existing = LOCAL.read_text() if LOCAL.exists() else (
        "# Private eval cases (gitignored), same format as cases.yaml.\n")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    LOCAL.write_text(existing.rstrip("\n") + f"\n\n# Drafts from 👎 ratings, added {stamp}\n" + text)
    print(f"added {len(drafts)} draft(s) to {LOCAL.relative_to(HERE.parent)}: "
          + ", ".join(d["id"] for d in drafts))


if __name__ == "__main__":
    main()
