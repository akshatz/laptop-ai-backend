#!/usr/bin/env python3
"""Answer-quality evals for fast-ai, through Open WebUI's real chat pipeline.

Each case in cases.yaml (plus the gitignored cases.local.yaml) is sent to Open WebUI's
/api/chat/completions with web search on, so it gets the same query generation, SearXNG search,
domain blocklist, reranker, Chat History Trim filter and RAG template a family member gets. The
answer and its sources are scored with the case's checks, and every run is saved to runs/ as JSONL:
first line the run header (label, model, the Open WebUI settings in force), then one line per case.

  python3 evals/run.py --label baseline              # all cases
  python3 evals/run.py --only tata-trusts-role,sbi-close
  python3 evals/run.py --category abstain
  python3 evals/run.py --compare evals/runs/A.jsonl evals/runs/B.jsonl

Needs OPEN_WEBUI_API_KEY in the environment or the repo's .env (see evals/README.md). Cases run one
at a time (~30-90 s each on this laptop), so a full run takes a while; run it when nobody is chatting.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNS = HERE / "runs"

# Phrases that mean "I don't know / that doesn't exist". Used both ways: abstain cases must match,
# cases with abstain: false must not.
ABSTAIN = re.compile(
    r"\bi (do not|don't|couldn't|could not|can't|cannot) (know|find|confirm|verify|provide)"
    r"|\bno (information|reliable information|results|record|data|mention)"
    r"|\b(is|are)n't (available|aware)|\bnot (available|aware|sure)\b|\bunable to\b"
    r"|\b(does not|doesn't|did not|didn't) (exist|mention|contain)|\bthere (is|are|was) no\b"
    r"|\bfictional\b|\bhasn't (happened|taken place)|\bhas not (happened|taken place)",
    re.I,
)

# Open WebUI settings recorded in each run header, so a result can be tied to what was in force.
# Long prompts are stored as a short hash: enough to see that they changed between two runs.
SETTING_KEYS = [
    "rag.top_k",
    "rag.top_k_reranker",
    "rag.reranking_engine",
    "rag.reranking_model",
    "rag.relevance_threshold",
    "rag.enable_hybrid_search",
    "rag.template",
    "web.search.result_count",
    "web.search.domain.filter_list",
    "task.query.prompt_template",
    "models.default_params",
]


def load_env() -> dict:
    """The repo's .env (simple KEY=value lines), overridden by the real environment."""
    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    return {**env, **os.environ}


def settings_snapshot(env: dict, model: str) -> dict:
    """Best effort: read the settings from Open WebUI's DB through the postgres container."""

    def psql(sql: str) -> str:
        return subprocess.run(
            ["docker", "exec", env.get("EVAL_POSTGRES_CONTAINER", "laptop-postgres"), "psql",
             "-U", env.get("POSTGRES_USER", "postgres"), "-d", "open_webui", "-AtF", "\t", "-c", sql],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout

    def short(value):
        if isinstance(value, str) and len(value) > 200:
            return "sha1:" + hashlib.sha1(value.encode()).hexdigest()[:12]
        return value

    try:
        keys = ",".join(f"'{k}'" for k in SETTING_KEYS)
        snap = {}
        for line in psql(f"select key, value::text from config where key in ({keys})").splitlines():
            key, _, value = line.partition("\t")
            snap[key] = short(json.loads(value))
        model_id = model.replace("'", "''")
        # model.params/meta are text columns holding JSON
        row = psql(f"select params, (meta::jsonb->'filterIds')::text from model where id = '{model_id}'").strip()
        if row:
            params, filters = row.split("\t")
            snap["model.params"] = json.loads(params or "null")
            snap["model.filterIds"] = json.loads(filters or "null")
        return snap
    except Exception as e:  # no docker access, different container name, ...
        return {"error": str(e)[:200]}


def settings_file_state() -> dict:
    """Which version of devops/open-webui/settings.yaml this run tested: its last commit, whether it has
    uncommitted edits, and whether Open WebUI's DB matched it (settings.py diff; None if that failed)."""
    path = REPO / "devops" / "open-webui" / "settings.yaml"

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=30).stdout.strip()

    try:
        sync = subprocess.run([sys.executable, str(path.with_name("settings.py")), "diff", "--quiet"],
                              capture_output=True, timeout=120).returncode
    except Exception:
        sync = None
    return {
        "commit": git("log", "-1", "--format=%h", "--", str(path)) or None,
        "uncommitted_edits": bool(git("status", "--porcelain", "--", str(path))),
        "db_matches_file": {0: True, 1: False}.get(sync),
    }


def ask(env: dict, model: str, messages: list, web_search: bool, timeout: int) -> dict:
    """One streamed chat completion. Open WebUI sends the sources as the first SSE events."""
    body = json.dumps(
        {"model": model, "messages": messages, "stream": True, "features": {"web_search": web_search}}
    ).encode()
    request = urllib.request.Request(
        base_url(env) + "/api/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {env['OPEN_WEBUI_API_KEY']}", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    first_token, pieces, sources, error = None, [], [], None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            sources.extend(event.get("sources") or [])
            if event.get("error"):
                error = str(event["error"])[:300]
            for choice in event.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    first_token = first_token or time.monotonic()
                    pieces.append(piece)
    end = time.monotonic()
    return {
        "answer": "".join(pieces),
        "sources": sources,
        "error": error,
        "latency_s": round(end - start, 1),
        "first_token_s": round(first_token - start, 1) if first_token else None,
    }


def source_info(sources: list) -> dict:
    domains, queries, chunks, searched = [], [], 0, False
    for src in sources:
        meta_source = src.get("source") or {}
        if meta_source.get("type") == "web_search":
            searched = True
            # Open WebUI names the web-search source after the generated queries, comma-joined.
            name = meta_source.get("name") or ""
            queries += [q.strip() for q in name.split(",") if q.strip() and q.strip() not in queries]
        for meta in src.get("metadata") or []:
            chunks += 1
            url = meta.get("source") or ""
            host = urlparse(url).hostname if url.startswith("http") else None
            if host:
                host = host.removeprefix("www.")
                if host not in domains:
                    domains.append(host)
    return {"searched": searched, "domains": domains, "queries": queries, "chunks": chunks}


def score(case: dict, answer: str, info: dict) -> dict:
    expect = case.get("expect") or {}
    found = lambda pattern: re.search(pattern, answer, re.I) is not None  # noqa: E731
    checks = {"non-empty": bool(answer.strip())}
    if "search" in expect:
        checks["search"] = info["searched"] == expect["search"]
    for fact in expect.get("facts") or []:
        options = fact if isinstance(fact, list) else [fact]
        checks[f"fact {options[0]}"] = any(found(o) for o in options)
    for pattern in expect.get("exclude") or []:
        checks[f"not {pattern}"] = not found(pattern)
    if "min_domains" in expect:
        checks[f"{expect['min_domains']}+ sites"] = len(info["domains"]) >= expect["min_domains"]
    if "abstain" in expect:
        said_unknown = ABSTAIN.search(answer) is not None
        checks["abstains" if expect["abstain"] else "answers"] = said_unknown == expect["abstain"]
    return checks


def load_cases(only: str | None, category: str | None) -> list:
    cases = yaml.safe_load((HERE / "cases.yaml").read_text()) or []
    local = HERE / "cases.local.yaml"
    if local.exists():
        cases += yaml.safe_load(local.read_text()) or []
    ids = [c["id"] for c in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        sys.exit(f"duplicate case ids: {', '.join(sorted(duplicates))}")
    if only:
        wanted = {i.strip() for i in only.split(",")}
        unknown = wanted - set(ids)
        if unknown:
            sys.exit(f"unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in wanted]
    if category:
        cases = [c for c in cases if c.get("category") == category]
    return cases


def summarize(results: list) -> None:
    if not results:
        return
    print()
    by_category = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["passed"])
    for cat, passed in sorted(by_category.items()):
        print(f"  {cat:15} {sum(passed):2}/{len(passed)}")
    total = sum(r["passed"] for r in results)
    latencies = [r["latency_s"] for r in results if r.get("latency_s") is not None]
    sites = [len(r["domains"]) for r in results if r.get("searched")]
    print(f"  {'total':15} {total:2}/{len(results)}  ({100 * total // len(results)}%)")
    if latencies:
        print(f"  median answer time {statistics.median(latencies):.0f} s, slowest {max(latencies):.0f} s")
    if sites:
        print(f"  sites per searched answer: mean {statistics.mean(sites):.1f}")
    failed = [r for r in results if not r["passed"]]
    if failed:
        print("\n  failed:")
        for r in failed:
            why = r.get("error") or ", ".join(k for k, ok in r["checks"].items() if not ok)
            print(f"    {r['id']}: {why}")


def base_url(env: dict) -> str:
    return env.get("OPEN_WEBUI_URL", "http://127.0.0.1:8082").rstrip("/")


def wait_for_health(env: dict, max_wait: int = 300) -> bool:
    """Poll Open WebUI's /health until it answers 200 (e.g. while the container is recreated)."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url(env) + "/health", timeout=10) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(5)
    return False


def no_reply(error: str, unreachable: bool = False) -> dict:
    return {"answer": "", "sources": [], "error": error, "unreachable": unreachable,
            "latency_s": None, "first_token_s": None}


def run_case(args, env: dict, messages: list, web_search: bool) -> dict:
    """ask() with one retry when Open WebUI drops the connection (restart/recreate mid-run).
    A case that still can't reach Open WebUI is marked unreachable: not run, rather than failed."""
    for attempt in (1, 2):
        try:
            return {**ask(env, args.model, messages, web_search, args.timeout), "unreachable": False}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code in (401, 403):
                sys.exit(f"\nHTTP {e.code} from Open WebUI: {detail}\n(check the API key and the "
                         "API key settings in evals/README.md)")
            return no_reply(f"HTTP {e.code}: {detail}")
        except TimeoutError as e:  # the answer took longer than --timeout: a real result, not an outage
            return no_reply(f"TimeoutError: {e}"[:300])
        except (urllib.error.URLError, ConnectionError) as e:
            error = f"{type(e).__name__}: {e}"[:300]
            if attempt == 1:
                print("(Open WebUI unreachable, waiting for it ...)", end=" ", flush=True)
                if wait_for_health(env):
                    continue
            return no_reply(error, unreachable=True)
    return no_reply("unreachable", unreachable=True)


def run(args, env: dict) -> None:
    if not env.get("OPEN_WEBUI_API_KEY"):
        sys.exit("OPEN_WEBUI_API_KEY is not set (environment or .env) — see evals/README.md")
    cases = load_cases(args.only, args.category)
    if not cases:
        sys.exit("no cases selected")
    RUNS.mkdir(exist_ok=True)
    started = datetime.now()
    label = re.sub(r"[^\w.-]+", "-", args.label).strip("-") if args.label else ""
    out_path = RUNS / (started.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "") + ".jsonl")
    header = {
        "run": {
            "label": args.label,
            "started": started.isoformat(timespec="seconds"),
            "model": args.model,
            "url": env.get("OPEN_WEBUI_URL", "http://127.0.0.1:8082"),
            "cases": len(cases),
            "settings": settings_snapshot(env, args.model),
            "settings_file": settings_file_state(),
        }
    }
    print(f"{len(cases)} case(s), model {args.model} → {out_path.relative_to(REPO)}")
    results = []
    unreachable_in_a_row = 0
    with out_path.open("w") as out:
        out.write(json.dumps(header, ensure_ascii=False) + "\n")
        try:
            for n, case in enumerate(cases, 1):
                messages = list(case.get("history") or []) + [{"role": "user", "content": case["question"]}]
                print(f"[{n}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
                reply = run_case(args, env, messages, case.get("web_search", True))
                if reply["unreachable"]:
                    unreachable_in_a_row += 1
                    print("NOT RUN —", reply["error"])
                    if unreachable_in_a_row >= 3:
                        print("Open WebUI unreachable for 3 cases in a row; stopping")
                        break
                    continue
                unreachable_in_a_row = 0
                info = source_info(reply["sources"])
                checks = score(case, reply["answer"], info)
                passed = not reply["error"] and all(checks.values())
                result = {
                    "id": case["id"],
                    "category": case.get("category", ""),
                    "question": case["question"],
                    "passed": passed,
                    "checks": checks,
                    "answer": reply["answer"],
                    "error": reply["error"],
                    "latency_s": reply["latency_s"],
                    "first_token_s": reply["first_token_s"],
                    **info,
                }
                results.append(result)
                out.write(json.dumps(result, ensure_ascii=False) + "\n")
                out.flush()
                failing = [k for k, ok in checks.items() if not ok]
                print(("PASS" if passed else "FAIL"), f"{reply['latency_s'] or '-'} s,",
                      f"{len(info['domains'])} site(s)", ("— " + (reply["error"] or ", ".join(failing))) if not passed else "")
        except KeyboardInterrupt:
            print("\ninterrupted; results so far are saved")
    summarize(results)
    if len(results) < len(cases):
        print(f"\n  not run: {len(cases) - len(results)} of {len(cases)} (Open WebUI unreachable, or stopped early)")
    print(f"\nsaved {out_path.relative_to(REPO)}")


def load_run(path: str) -> tuple[dict, dict]:
    lines = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    header = lines[0].get("run", {}) if lines and "run" in lines[0] else {}
    return header, {r["id"]: r for r in lines if "id" in r}


def compare(path_a: str, path_b: str) -> None:
    (head_a, a), (head_b, b) = load_run(path_a), load_run(path_b)
    print(f"A: {Path(path_a).name}  ({head_a.get('label') or 'no label'})")
    print(f"B: {Path(path_b).name}  ({head_b.get('label') or 'no label'})")
    for name, head in (("A", head_a), ("B", head_b)):
        state = head.get("settings_file")
        if state:
            print(f"{name} settings.yaml: commit {state.get('commit') or 'none'}"
                  f"{', uncommitted edits' if state.get('uncommitted_edits') else ''}"
                  f", DB {'matched' if state.get('db_matches_file') else 'did NOT match' if state.get('db_matches_file') is False else 'not checked'}")
    settings_a, settings_b = head_a.get("settings", {}), head_b.get("settings", {})
    changed = sorted(k for k in set(settings_a) | set(settings_b) if settings_a.get(k) != settings_b.get(k))
    if changed:
        print("\nsettings changed:")
        for k in changed:
            print(f"  {k}: {json.dumps(settings_a.get(k), ensure_ascii=False)} → "
                  f"{json.dumps(settings_b.get(k), ensure_ascii=False)}")
    common = [i for i in a if i in b]
    if not common:
        sys.exit("no cases in common")
    pass_a = sum(a[i]["passed"] for i in common)
    pass_b = sum(b[i]["passed"] for i in common)
    print(f"\npassed: {pass_a}/{len(common)} → {pass_b}/{len(common)} ({len(common)} cases in both runs)")
    for title, ids in (
        ("now passing", [i for i in common if b[i]["passed"] and not a[i]["passed"]]),
        ("now failing", [i for i in common if a[i]["passed"] and not b[i]["passed"]]),
    ):
        if ids:
            print(f"\n{title}:")
            for i in ids:
                print(f"  {i}")
    lat_a = [a[i]["latency_s"] for i in common if a[i].get("latency_s")]
    lat_b = [b[i]["latency_s"] for i in common if b[i].get("latency_s")]
    if lat_a and lat_b:
        print(f"\nmedian answer time: {statistics.median(lat_a):.0f} s → {statistics.median(lat_b):.0f} s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", help="name for this run, e.g. 'grounded-template'")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--category", help="run one category")
    parser.add_argument("--model", default=None, help="model id (default EVAL_MODEL or fast-ai:latest)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per case (default 300)")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="compare two saved runs")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    env = load_env()
    args.model = args.model or env.get("EVAL_MODEL", "fast-ai:latest")
    run(args, env)


if __name__ == "__main__":
    main()
