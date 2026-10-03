#!/usr/bin/env python3
"""Open WebUI's tuned settings as code.

Open WebUI keeps most settings in its database (the config, model and function tables of the
open_webui Postgres DB), and once a setting is saved there it wins over docker-compose's env. This
keeps the ones this stack tunes in devops/open-webui/settings.yaml, so they're versioned, reviewable
and tied to eval runs:

  python3 devops/open-webui/settings.py export   # DB → settings.yaml (after changing settings in the Admin UI)
  python3 devops/open-webui/settings.py diff     # what apply would change; exit code 1 if anything
  python3 devops/open-webui/settings.py apply    # settings.yaml → DB, after showing the diff and asking

Only what settings.yaml lists is managed; everything else in the DB is left alone. apply writes in
one transaction, after saving the current values to devops/backups/open-webui-settings/ (gitignored).
Changes are live at once, since Open WebUI reads these rows on every request.

Covered: the config keys in CONFIG_KEYS; each model's params, is_active and the meta keys in
MODEL_META_KEYS (not its picture); each Function's is_active, is_global and valves. A Function's code
isn't (import it from devops/open-webui/functions/). Valves whose name looks like a secret are
exported as REDACTED, and apply keeps the DB's value for them.

Each version that goes live is recorded in the fn_settings_revisions table (start time, content hash,
git commit), which the KPI Dashboard Function uses to compare answer speed and ratings per revision:
apply and export record one when the content changed, `record` does it by hand (`--label` to name it),
and `record --from-git` adds one per past commit of settings.yaml, at its commit time. Settings changed
in the Admin UI count as a new revision only once exported.

Talks to Postgres through `docker exec laptop-postgres psql`, like evals/run.py, so the host needs
only PyYAML.
"""

import argparse
import difflib
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SETTINGS = HERE / "settings.yaml"
BACKUPS = REPO / "devops" / "backups" / "open-webui-settings"

CONFIG_KEYS = [
    # answering
    "rag.template",
    "models.default_params",
    # retrieval
    "rag.embedding_engine",
    "rag.embedding_model",
    "rag.chunk_size",
    "rag.chunk_overlap",
    "rag.enable_hybrid_search",
    "rag.hybrid_bm25_weight",
    "rag.reranking_engine",
    "rag.reranking_model",
    "rag.top_k",
    "rag.top_k_reranker",
    "rag.relevance_threshold",
    # web search
    "web.search.result_count",
    "web.search.domain.filter_list",
    # background model calls
    "task.model.default",
    "task.model.external",
    "task.model.params",
    "task.query.prompt_template",
    "task.query.search.enable",
    "task.query.retrieval.enable",
    "task.follow_up.enable",
    "task.follow_up.prompt_template",
    "task.title.enable",
    "task.tags.enable",
    "task.autocomplete.enable",
    # what users see and may do
    "ui.banners",
    "user.permissions",
]
MODEL_META_KEYS = ["description", "capabilities", "knowledge", "filterIds", "defaultFeatureIds",
                   "suggestion_prompts", "tags"]
SECRET_WORDS = ("password", "secret", "api_key", "apikey")
REDACTED = "REDACTED (kept in the DB)"
MISSING = object()  # not set in the DB
QUOTE = "$owui$"  # dollar-quoting tag for values in generated SQL

HEADER = """\
# Open WebUI settings tuned for this stack. Open WebUI keeps them in its database, where a saved
# value wins over docker-compose's env; devops/open-webui/settings.py syncs them with this file:
#   export: DB → this file    diff: what apply would change    apply: this file → DB
# Change a setting here and apply it, or change it in the Admin UI and export, then commit.
# Only what's listed here is managed. Secret-looking Function valves stay in the DB (REDACTED).
"""


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


def psql(env: dict, sql: str) -> str:
    cmd = ["docker", "exec", "-i", env.get("OPEN_WEBUI_POSTGRES_CONTAINER", "laptop-postgres"), "psql",
           "-U", env.get("POSTGRES_USER", "postgres"), "-d", "open_webui", "-At", "-v", "ON_ERROR_STOP=1"]
    result = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return result.stdout


def rows(env: dict, query: str) -> list:
    return json.loads(psql(env, f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({query}) t;"))


def git(*args: str, strip: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=30).stdout
    return out.strip() if strip else out


def record_revision(env: dict, source: str, content: str, *, commit: str | None, uncommitted: bool,
                    label: str | None = None, at: int | None = None) -> None:
    """Adds a fn_settings_revisions row unless the latest one (before `at`) has the same content."""
    if psql(env, "SELECT to_regclass('fn_settings_revisions') IS NOT NULL;").strip() != "t":
        print("note: fn_settings_revisions doesn't exist yet (run the open-webui-fn-migrate service); revision not recorded")
        return
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    at = int(at if at is not None else datetime.now().timestamp())
    latest = psql(env, "SELECT content_hash FROM fn_settings_revisions "
                       f"WHERE started_at <= {at} ORDER BY started_at DESC, id DESC LIMIT 1;").strip()
    if latest == digest:
        return
    psql(env, "INSERT INTO fn_settings_revisions (content_hash, git_commit, uncommitted, label, source, started_at) "
              f"VALUES ({sql_literal(digest)}, {sql_literal(commit) if commit else 'NULL'}, {str(uncommitted).lower()}, "
              f"{sql_literal(label) if label else 'NULL'}, {sql_literal(source)}, {at});")
    print(f"recorded settings revision {digest}" + (f" ({commit})" if commit else "") + (f": {label}" if label else ""))


def record_current(env: dict, source: str, label: str | None = None) -> None:
    rel = str(SETTINGS.relative_to(REPO))
    record_revision(env, source, SETTINGS.read_text(), commit=git("log", "-1", "--format=%h", "--", rel) or None,
                    uncommitted=bool(git("status", "--porcelain", "--", rel)), label=label)


def text_json(value):
    """model.params/meta and function.valves are text columns holding JSON (or nothing)."""
    return json.loads(value) if value else None


def is_secret(name: str) -> bool:
    return any(word in name.lower() for word in SECRET_WORDS)


def read_db(env: dict) -> dict:
    keys = ", ".join(sql_literal(k) for k in CONFIG_KEYS)
    config = {r["key"]: r["value"] for r in rows(env, f"SELECT key, value FROM config WHERE key IN ({keys})")}
    models = {}
    for r in rows(env, "SELECT id, is_active, params, meta FROM model ORDER BY id"):
        meta = text_json(r["meta"]) or {}
        models[r["id"]] = {
            "is_active": r["is_active"],
            "params": text_json(r["params"]) or {},
            "meta": {k: meta[k] for k in MODEL_META_KEYS if k in meta},
        }
    functions = {
        r["id"]: {"is_active": r["is_active"], "is_global": r["is_global"], "valves": text_json(r["valves"])}
        for r in rows(env, "SELECT id, is_active, is_global, valves FROM function ORDER BY id")
    }
    return {
        "config": {k: config[k] for k in CONFIG_KEYS if k in config},
        "models": models,
        "functions": functions,
    }


def redacted(db: dict) -> dict:
    """The DB snapshot as it may appear in the repo: secret-looking valves replaced."""
    functions = {}
    for fid, f in db["functions"].items():
        valves = f["valves"]
        if isinstance(valves, dict):
            valves = {k: (REDACTED if is_secret(k) and v not in (None, "") else v) for k, v in valves.items()}
        functions[fid] = {**f, "valves": valves}
    return {**db, "functions": functions}


class Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _represent_str(dumper, value):
    # Prompts as readable literal blocks, so a change shows as a normal line diff in git.
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


Dumper.add_representer(str, _represent_str)


def changes(wanted: dict, current: dict) -> list:
    """(path, current, wanted) for each managed setting that differs. current uses MISSING when unset."""
    out = []
    for key, value in (wanted.get("config") or {}).items():
        if key not in CONFIG_KEYS:
            sys.exit(f"settings.yaml: config key {key!r} isn't in CONFIG_KEYS in settings.py")
        have = current["config"].get(key, MISSING)
        if have != value:
            out.append((f"config.{key}", have, value))
    for mid, model in (wanted.get("models") or {}).items():
        have = current["models"].get(mid)
        if have is None:
            out.append((f"models.{mid}", MISSING, "(model not in Open WebUI: create it there first)"))
            continue
        for field in ("is_active", "params"):
            if field in model and model[field] != have[field]:
                out.append((f"models.{mid}.{field}", have[field], model[field]))
        for key, value in (model.get("meta") or {}).items():
            if have["meta"].get(key, MISSING) != value:
                out.append((f"models.{mid}.meta.{key}", have["meta"].get(key, MISSING), value))
    for fid, function in (wanted.get("functions") or {}).items():
        have = current["functions"].get(fid)
        if have is None:
            out.append((f"functions.{fid}", MISSING, "(Function not installed: import it first)"))
            continue
        for field in ("is_active", "is_global", "valves"):
            if field in function and function[field] != have[field]:
                out.append((f"functions.{fid}.{field}", have[field], function[field]))
    return out


def show(path: str, old, new) -> None:
    print(f"~ {path}")
    if isinstance(old, str) and isinstance(new, str) and "\n" in old + new:
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "DB", "settings.yaml", lineterm="", n=1)
        for line in list(diff)[2:]:
            print(f"    {line}")
        return

    def short(value):
        text = "(not set)" if value is MISSING else json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 300 else text[:300] + "…"

    print(f"    DB:            {short(old)}\n    settings.yaml: {short(new)}")


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_json(value) -> str:
    payload = json.dumps(value, ensure_ascii=False)
    if QUOTE in payload:
        sys.exit(f"a value contains {QUOTE}, which this script uses for quoting")
    return f"{QUOTE}{payload}{QUOTE}"


def apply_sql(wanted: dict, current: dict) -> tuple[str, list]:
    """One transaction that writes every managed setting that differs; also returns what it skipped."""
    statements, skipped = [], []
    now = "extract(epoch FROM now())::bigint"
    for path, _, _ in changes(wanted, current):
        if path.startswith("config."):
            key = path.removeprefix("config.")
            statements.append(
                f"INSERT INTO config (key, value, updated_at) VALUES ({sql_literal(key)}, "
                f"{sql_json(wanted['config'][key])}::json, {now}) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;")
    for mid, model in (wanted.get("models") or {}).items():
        have = current["models"].get(mid)
        if have is None:
            skipped.append(f"model {mid} (not in Open WebUI)")
            continue
        merged = {"is_active": model.get("is_active", have["is_active"]),
                  "params": model.get("params", have["params"]),
                  "meta": {**have["meta"], **(model.get("meta") or {})}}
        if merged == have:
            continue
        statements.append(
            f"UPDATE model SET is_active = {str(merged['is_active']).lower()}, "
            f"params = {sql_json(merged['params'])}, "
            f"meta = (coalesce(nullif(meta, ''), '{{}}')::jsonb || {sql_json(merged['meta'])}::jsonb)::text, "
            f"updated_at = {now} WHERE id = {sql_literal(mid)};")
    for fid, function in (wanted.get("functions") or {}).items():
        have = current["functions"].get(fid)
        if have is None:
            skipped.append(f"Function {fid} (not installed)")
            continue
        valves = function.get("valves", have["valves"])
        if isinstance(valves, dict):  # a REDACTED valve keeps the DB's value
            db_valves = have["valves"] if isinstance(have["valves"], dict) else {}
            valves = {k: db_valves.get(k) if v == REDACTED else v for k, v in valves.items()}
        merged = {"is_active": function.get("is_active", have["is_active"]),
                  "is_global": function.get("is_global", have["is_global"]),
                  "valves": valves}
        if merged == have:
            continue
        valves_sql = "NULL" if merged["valves"] is None else sql_json(merged["valves"])
        statements.append(
            f"UPDATE function SET is_active = {str(merged['is_active']).lower()}, "
            f"is_global = {str(merged['is_global']).lower()}, valves = {valves_sql}, "
            f"updated_at = {now} WHERE id = {sql_literal(fid)};")
    return "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", skipped


def load_settings() -> dict:
    if not SETTINGS.exists():
        sys.exit(f"{SETTINGS.relative_to(REPO)} doesn't exist yet: run `settings.py export` first")
    return yaml.safe_load(SETTINGS.read_text()) or {}


def cmd_export(env: dict, _args) -> None:
    snapshot = redacted(read_db(env))
    SETTINGS.write_text(HEADER + yaml.dump(snapshot, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=100))
    print(f"wrote {SETTINGS.relative_to(REPO)}: {len(snapshot['config'])} config keys, "
          f"{len(snapshot['models'])} models, {len(snapshot['functions'])} Functions")
    record_current(env, "export")


def cmd_diff(env: dict, args) -> None:
    found = changes(load_settings(), redacted(read_db(env)))
    if not args.quiet:
        for path, old, new in found:
            show(path, old, new)
        print(f"{len(found)} difference(s)" if found else "in sync")
    sys.exit(1 if found else 0)


def cmd_apply(env: dict, args) -> None:
    wanted, db = load_settings(), read_db(env)
    found = changes(wanted, redacted(db))
    if not found:
        print("in sync, nothing to apply")
        record_current(env, "apply")
        return
    for path, old, new in found:
        show(path, old, new)
    if not args.yes and input(f"\nApply {len(found)} change(s) to Open WebUI's DB? [y/N] ").strip().lower() != "y":
        sys.exit("not applied")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(db, ensure_ascii=False, indent=2))
    backup.chmod(0o600)  # unredacted: may hold secret valves
    sql, skipped = apply_sql(wanted, db)
    psql(env, sql)
    left = changes(wanted, redacted(read_db(env)))
    print(f"applied; previous values saved to {backup.relative_to(REPO)}")
    for item in skipped:
        print(f"skipped {item}")
    if left:
        sys.exit(f"{len(left)} setting(s) still differ after apply: run diff")
    record_current(env, "apply")


def cmd_record(env: dict, args) -> None:
    if not args.from_git:
        load_settings()
        record_current(env, "record", args.label)
        return
    rel = str(SETTINGS.relative_to(REPO))
    for line in reversed(git("log", "--format=%h %ct %s", "--", rel).splitlines()):
        commit, at, subject = line.split(" ", 2)
        record_revision(env, "git", git("show", f"{commit}:{rel}", strip=False), commit=commit, uncommitted=False,
                        label=subject[:120], at=int(at))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="DB → settings.yaml")
    diff = sub.add_parser("diff", help="what apply would change (exit 1 if anything)")
    diff.add_argument("--quiet", action="store_true", help="no output, exit code only")
    apply = sub.add_parser("apply", help="settings.yaml → DB")
    apply.add_argument("--yes", action="store_true", help="don't ask")
    record = sub.add_parser("record", help="note the current settings.yaml as live (for the KPI dashboard)")
    record.add_argument("--label", help="short name for this revision")
    record.add_argument("--from-git", action="store_true", help="add one revision per past commit of settings.yaml")
    args = parser.parse_args()
    {"export": cmd_export, "diff": cmd_diff, "apply": cmd_apply, "record": cmd_record}[args.command](load_env(), args)


if __name__ == "__main__":
    main()
