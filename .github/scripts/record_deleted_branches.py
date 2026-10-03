#!/usr/bin/env python3
"""Adds branches deleted on GitHub to DELETED_BRANCHES.md, with the commit each one ended at.

Run by .github/workflows/deleted-branches.yml after every branch deletion (and once a day for any
it missed), or by hand from anywhere in the repo with an authenticated `gh`:

  python3 .github/scripts/record_deleted_branches.py

It only adds rows, and fills in a deletion date that wasn't known yet. It never removes or rewrites
a row, so the file stays a record after GitHub's event log (90 days) has forgotten a deletion.

A branch counts as deleted when it isn't on GitHub now but a pull request was made from it or the
event log recorded its deletion. Its last commit is the head of its last pull request (GitHub keeps
those as refs/pull/<n>/head after the branch is gone); without a pull request, the head of its last
push in the event log, if that's still there.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / "DELETED_BRANCHES.md"
START, END = "<!-- deleted-branches:start -->", "<!-- deleted-branches:end -->"
TABLE_HEAD = ["| Branch | Last commit | PR | Merged | Deleted | What it was |", "|---|---|---|---|---|---|"]
ROW = re.compile(r"^\| `(?P<branch>[^`]+)` \| (?P<commit>[^|]+) \| (?P<pr>[^|]*) \| (?P<merged>[^|]*) \| (?P<deleted>[^|]*) \| (?P<what>.*) \|$")
UNKNOWN = "—"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, cwd=FILE.parent).stdout


def gh_lines(path: str, jq: str) -> list:
    """Every item of a paginated REST list, one JSON value per line through --jq."""
    # tojson: --jq prints bare strings (branch names) unquoted.
    output = gh("api", "--paginate", path, "--jq", f"{jq} | tojson")
    return [json.loads(line) for line in output.splitlines() if line]


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def parse(table: str) -> list[dict]:
    rows = []
    for line in table.splitlines():
        match = ROW.match(line.strip())
        if match:
            rows.append(match.groupdict())
    return rows


def render(row: dict) -> str:
    return f"| `{row['branch']}` | {row['commit']} | {row['pr']} | {row['merged']} | {row['deleted']} | {row['what']} |"


def sort_key(row: dict):
    numbers = [int(n) for n in re.findall(r"#(\d+)", row["pr"])]
    # Branches with a PR by their last PR number; the others after them, by deletion date.
    return (0, max(numbers), "") if numbers else (1, 0, row["deleted"])


def found_rows(repo: str) -> list[dict]:
    on_github = set(gh_lines(f"repos/{repo}/branches?per_page=100", ".[].name"))
    prs = json.loads(gh("pr", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json",
                        "number,title,headRefName,headRefOid,mergedAt,closedAt,isCrossRepository"))
    events = gh_lines(
        f"repos/{repo}/events?per_page=100",
        '.[] | select(.type == "DeleteEvent" or .type == "PushEvent")'
        " | {type, created_at, ref: .payload.ref, ref_type: .payload.ref_type, head: .payload.head}",
    )

    deleted_on, last_push = {}, {}
    for event in sorted(events, key=lambda e: e["created_at"]):
        if event["type"] == "DeleteEvent" and event["ref_type"] == "branch":
            deleted_on[event["ref"]] = event["created_at"][:10]
        elif event["type"] == "PushEvent":
            last_push[(event["ref"] or "").removeprefix("refs/heads/")] = event["head"]
    # The workflow passes the branch whose deletion started it; the event log can lag behind.
    just_deleted = os.environ.get("DELETED_BRANCH")
    if just_deleted and just_deleted not in deleted_on:
        deleted_on[just_deleted] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    by_branch = {}
    for pr in sorted(prs, key=lambda p: p["number"]):
        if not pr["isCrossRepository"]:  # a fork's branch isn't one of this repo's
            by_branch.setdefault(pr["headRefName"], []).append(pr)

    rows = []
    for branch in sorted((set(by_branch) | set(deleted_on)) - on_github):
        branch_prs = by_branch.get(branch, [])
        if branch_prs:
            last = branch_prs[-1]
            sha = last["headRefOid"]
            if last["mergedAt"]:
                merged = last["mergedAt"][:10]
            else:
                merged = f"not merged (closed {last['closedAt'][:10]})" if last["closedAt"] else "not merged"
            pr = ", ".join(f"#{p['number']}" for p in branch_prs)
            what = cell(last["title"])
        else:
            sha = last_push.get(branch)
            merged, pr, what = UNKNOWN, UNKNOWN, "(no pull request)"
        rows.append({
            "branch": branch,
            "commit": f"`{sha}`" if sha else "unknown",
            "pr": pr,
            "merged": merged,
            "deleted": deleted_on.get(branch, UNKNOWN),
            "what": what,
        })
    return rows


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY") or gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    text = FILE.read_text()
    before, rest = text.split(START, 1)
    table, after = rest.split(END, 1)
    rows = parse(table)

    added, dated = [], []
    for new in found_rows(repo):
        same = [r for r in rows if r["branch"] == new["branch"] and r["commit"] in (new["commit"], "unknown")]
        if not same:
            rows.append(new)
            added.append(new["branch"])
            continue
        old = same[-1]
        if old["commit"] == "unknown" and new["commit"] != "unknown":
            old["commit"] = new["commit"]
        if old["deleted"] == UNKNOWN and new["deleted"] != UNKNOWN:
            old["deleted"] = new["deleted"]
            dated.append(new["branch"])

    rows.sort(key=sort_key)
    body = "\n".join(TABLE_HEAD + [render(r) for r in rows])
    FILE.write_text(f"{before}{START}\n{body}\n{END}{after}")
    print(f"{len(rows)} deleted branches listed; added: {', '.join(added) or 'none'}; "
          f"deletion date filled in: {', '.join(dated) or 'none'}")


if __name__ == "__main__":
    main()
