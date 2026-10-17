"""devops/open-webui/settings.py against a real Postgres.

Open WebUI's config/model/function tables are recreated with the same column types in database
`settings_test`, plus fn_settings_revisions from the Functions' real migration. settings.py's
psql() (normally `docker exec laptop-postgres psql`) is pointed at that database, so its generated
SQL really runs. Files (settings.yaml, backups) go to a temporary directory.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DB = "settings_test"

if not TEST_DATABASE_URL or not shutil.which("psql"):
    if os.environ.get("CI"):
        raise RuntimeError("settings.py tests need TEST_DATABASE_URL and the psql client")
    pytest.skip("needs TEST_DATABASE_URL and psql", allow_module_level=True)

URL = make_url(TEST_DATABASE_URL)
PG_ENV = {**os.environ, "PGHOST": URL.host or "localhost", "PGPORT": str(URL.port or 5432),
          "PGUSER": URL.username, "PGPASSWORD": URL.password or ""}

# Same columns as Open WebUI 0.11's tables (only the ones settings.py touches matter).
SCHEMA = """
CREATE TABLE config (key text PRIMARY KEY, value json NOT NULL, updated_at bigint);
CREATE TABLE model (id text PRIMARY KEY, user_id text, base_model_id text, name text, params text,
                    meta text, updated_at bigint, created_at bigint, is_active boolean NOT NULL);
CREATE TABLE function (id varchar PRIMARY KEY, user_id varchar, name text, type text, content text,
                       meta text, valves text, is_active boolean, is_global boolean,
                       updated_at bigint, created_at bigint);
INSERT INTO config (key, value) VALUES
  ('rag.template', '"Answer from the sources.\\nCite [id]."'),
  ('rag.top_k', '6'),
  ('ui.banners', '[{"id": "family-guide-1", "type": "info"}]');
INSERT INTO model (id, params, meta, is_active) VALUES
  ('fast-ai:latest', '{"temperature": 0.1}', '{"filterIds": ["chat_history_trim"], "profile_image_url": "data:big"}', true),
  ('llama3.2:latest', NULL, NULL, false);
INSERT INTO function (id, valves, is_active, is_global) VALUES
  ('regenerate_limit', '{"max_answers": 3}', true, true),
  ('authentik_user_cleanup', '{"api_token": "fake-token-for-tests", "archive_chats": true}', true, false);
"""


def psql(sql: str, db: str = DB) -> str:
    result = subprocess.run(["psql", "-d", db, "-At", "-v", "ON_ERROR_STOP=1"], input=sql,
                            env=PG_ENV, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def settings(tmp_path, monkeypatch):
    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE); ", db="postgres")
    psql(f"CREATE DATABASE {DB};", db="postgres")
    psql(SCHEMA)
    migrations = ROOT / "devops" / "open-webui" / "migrations"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(migrations / "alembic.ini"), "upgrade", "head"],
        cwd=migrations, check=True, capture_output=True, timeout=120,
        env={**os.environ, "LEGACY_FUNCTION_DATA_DIR": str(tmp_path),
             "OPEN_WEBUI_FN_DATABASE_URL": URL.set(drivername="postgresql+psycopg", database=DB)
             .render_as_string(hide_password=False)},
    )

    spec = importlib.util.spec_from_file_location("owui_settings", ROOT / "devops" / "open-webui" / "settings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / "repo"
    (repo / "devops" / "open-webui").mkdir(parents=True)
    monkeypatch.setattr(module, "REPO", repo)
    monkeypatch.setattr(module, "SETTINGS", repo / "devops" / "open-webui" / "settings.yaml")
    monkeypatch.setattr(module, "BACKUPS", repo / "devops" / "backups" / "open-webui-settings")
    monkeypatch.setattr(module, "psql", lambda env, sql: _psql_or_exit(sql))
    yield module
    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE);", db="postgres")


def _psql_or_exit(sql):
    result = subprocess.run(["psql", "-d", DB, "-At", "-v", "ON_ERROR_STOP=1"], input=sql,
                            env=PG_ENV, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return result.stdout


def run(module, command, **args):
    """Runs a settings.py command; returns its exit code (0 when it doesn't call sys.exit)."""
    handler = getattr(module, f"cmd_{command}")
    try:
        handler({}, SimpleNamespace(quiet=True, yes=True, from_git=False, label=None, **args))
    except SystemExit as exit:
        return exit.code if isinstance(exit.code, int) else 1
    return 0


def edit(module, change):
    data = yaml.safe_load(module.SETTINGS.read_text())
    change(data)
    module.SETTINGS.write_text(yaml.dump(data, sort_keys=False))


def revisions() -> int:
    return int(psql("SELECT count(*) FROM fn_settings_revisions;").strip())


# ---------------------------------------------------------------------------------------------------

def test_export_then_diff_is_in_sync(settings):
    assert run(settings, "export") == 0
    text = settings.SETTINGS.read_text()

    assert text.startswith("# Open WebUI settings tuned for this stack.")
    assert "rag.template: |-\n    Answer from the sources.\n    Cite [id]." in text  # prompts as line blocks
    assert "profile_image_url" not in text  # only the listed meta keys
    assert run(settings, "diff") == 0


def test_secret_valves_never_reach_the_file(settings):
    run(settings, "export")
    data = yaml.safe_load(settings.SETTINGS.read_text())
    assert data["functions"]["authentik_user_cleanup"]["valves"]["api_token"] == settings.REDACTED
    assert "fake-token-for-tests" not in settings.SETTINGS.read_text()


def test_apply_writes_the_changes_and_keeps_redacted_secrets(settings):
    run(settings, "export")

    def change(data):
        data["config"]["rag.top_k"] = 8
        data["config"]["rag.template"] = "It's a new prompt\nwith a quote."  # ' must be escaped
        data["models"]["fast-ai:latest"]["params"]["temperature"] = 0.2
        data["models"]["fast-ai:latest"]["meta"]["filterIds"].append("photo_router")
        data["functions"]["regenerate_limit"]["valves"]["max_answers"] = 4
        data["functions"]["authentik_user_cleanup"]["is_global"] = True

    edit(settings, change)
    assert run(settings, "diff") == 1

    assert run(settings, "apply") == 0

    assert run(settings, "diff") == 0
    assert psql("SELECT value FROM config WHERE key = 'rag.top_k';").strip() == "8"
    assert json.loads(psql("SELECT value FROM config WHERE key = 'rag.template';")) == "It's a new prompt\nwith a quote."
    meta = json.loads(psql("SELECT meta FROM model WHERE id = 'fast-ai:latest';"))
    assert meta["filterIds"] == ["chat_history_trim", "photo_router"]
    assert meta["profile_image_url"] == "data:big"  # unmanaged meta keys are kept
    valves = json.loads(psql("SELECT valves FROM function WHERE id = 'authentik_user_cleanup';"))
    assert valves["api_token"] == "fake-token-for-tests"  # REDACTED in the file → DB value kept


def test_apply_backs_up_the_previous_values_unredacted(settings):
    run(settings, "export")
    edit(settings, lambda d: d["config"].update({"rag.top_k": 9}))

    run(settings, "apply")

    [backup] = settings.BACKUPS.iterdir()
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    saved = json.loads(backup.read_text())
    assert saved["config"]["rag.top_k"] == 6
    assert saved["functions"]["authentik_user_cleanup"]["valves"]["api_token"] == "fake-token-for-tests"


def test_unset_config_keys_are_created(settings):
    run(settings, "export")
    edit(settings, lambda d: d["config"].update({"task.tags.enable": False}))
    run(settings, "apply")
    assert psql("SELECT value FROM config WHERE key = 'task.tags.enable';").strip() == "false"


def test_unknown_config_keys_are_refused(settings):
    run(settings, "export")
    edit(settings, lambda d: d["config"].update({"not.managed": 1}))
    with pytest.raises(SystemExit, match="isn't in CONFIG_KEYS"):
        settings.changes(settings.load_settings(), settings.redacted(settings.read_db({})))


def test_missing_models_and_functions_are_skipped(settings):
    run(settings, "export")

    def change(data):
        data["models"]["gone:latest"] = {"is_active": True, "params": {}}
        data["functions"]["not_installed"] = {"is_active": True}

    edit(settings, change)
    sql, skipped = settings.apply_sql(settings.load_settings(), settings.read_db({}))
    assert skipped == ["model gone:latest (not in Open WebUI)", "Function not_installed (not installed)"]
    assert "gone:latest" not in sql


def test_revisions_are_recorded_once_per_content(settings):
    run(settings, "export")
    assert revisions() == 1
    run(settings, "apply")  # in sync, same content
    assert revisions() == 1
    edit(settings, lambda d: d["config"].update({"rag.top_k": 7}))
    run(settings, "apply")
    assert revisions() == 2
    assert psql("SELECT string_agg(source, ',' ORDER BY id) FROM fn_settings_revisions;").strip() == "export,apply"


def test_without_the_revisions_table_it_only_notes_it(settings, capsys):
    psql("DROP TABLE fn_settings_revisions;")
    assert run(settings, "export") == 0
    assert "doesn't exist yet" in capsys.readouterr().out


def test_values_containing_the_quote_tag_are_refused(settings):
    with pytest.raises(SystemExit, match="quoting"):
        settings.sql_json({"x": settings.QUOTE})
    assert settings.sql_literal("it's") == "'it''s'"


def test_which_valve_names_count_as_secret(settings):
    secret = ["smtp_password", "api_key", "openai_apikey", "client_secret", "api_token", "AUTH_TOKEN"]
    not_secret = ["token_max_age_seconds", "max_tokens", "max_answers", "smtp_user", "base_url"]
    assert all(settings.is_secret(name) for name in secret)
    assert not any(settings.is_secret(name) for name in not_secret)
