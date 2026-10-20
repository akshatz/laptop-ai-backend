"""Both Alembic setups, run for real against an empty Postgres database.

- database/alembic (laptop-backend's tables): upgrade to head, then the database must match
  database/init_db.py's models exactly (no drift), downgrade to base, and up again.
- devops/open-webui/migrations (the Functions' fn_* tables in Open WebUI's database): same, using
  its own fn_alembic_version table and never Open WebUI's alembic_version.
- laptop-backend/models.py and database/init_db.py declare the same models by hand (CLAUDE.md,
  "Known inconsistencies"); they must stay identical.

Uses database `migrations_test` (dropped and recreated) on the server in TEST_DATABASE_URL.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DB = "migrations_test"
FN_TABLES = {
    "fn_email_verified", "fn_password_age", "fn_archived_chats", "fn_user_sso_status",
    "fn_settings_revisions", "fn_embedding_cache", "fn_file_uploads",
}

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# init_db.py reads INIT_DB_DATABASE_URL at import (it only connects when run as a script).
_had_url = "INIT_DB_DATABASE_URL" in os.environ
os.environ.setdefault("INIT_DB_DATABASE_URL", "postgresql+asyncpg://unused@localhost/unused")
init_db = load(ROOT / "database" / "init_db.py", "init_db_models")
if not _had_url:
    del os.environ["INIT_DB_DATABASE_URL"]
backend_models = load(ROOT / "laptop-backend" / "models.py", "backend_models")


@pytest.fixture
def database():
    """Sync (psycopg) and async (asyncpg) URLs of a fresh, empty `migrations_test` database."""
    base = make_url(TEST_DATABASE_URL)
    admin = create_engine(base.set(drivername="postgresql+psycopg", database="postgres"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {DB}"))
    sync_url = base.set(drivername="postgresql+psycopg", database=DB)
    yield sync_url, base.set(drivername="postgresql+asyncpg", database=DB)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
    admin.dispose()


def alembic(ini: Path, *args, **env):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=ini.parent, env={**os.environ, **env}, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result


def tables(url) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


# Created by the baseline migration (and once by init_db.py's script code) but not declared on the
# models. Known and harmless; any other difference fails.
KNOWN_UNDECLARED_INDEXES = {"idx_chats_user", "idx_msgs_chat"}


def drift(url) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn), init_db.Base.metadata)
    finally:
        engine.dispose()
    return [d for d in diffs if not (d[0] == "remove_index" and d[1].name in KNOWN_UNDECLARED_INDEXES)]


def single_head(ini: Path) -> None:
    heads = ScriptDirectory.from_config(Config(str(ini))).get_heads()
    assert len(heads) == 1, f"{ini}: {len(heads)} heads {heads} (merge them)"


# ---- laptop-backend (database/alembic) -----------------------------------------------------------

BACKEND_INI = ROOT / "database" / "alembic.ini"


def test_backend_migrations_have_one_head():
    single_head(BACKEND_INI)


@needs_db
def test_backend_migrations_match_the_models(database):
    sync_url, async_url = database
    url = async_url.render_as_string(hide_password=False)

    alembic(BACKEND_INI, "upgrade", "head", INIT_DB_DATABASE_URL=url)
    assert {"users", "user_chats", "messages"} <= tables(sync_url)
    assert drift(sync_url) == []  # init_db.py and the migrations describe the same schema

    alembic(BACKEND_INI, "downgrade", "base", INIT_DB_DATABASE_URL=url)
    assert tables(sync_url) == {"alembic_version"}
    alembic(BACKEND_INI, "upgrade", "head", INIT_DB_DATABASE_URL=url)


def describe(metadata) -> dict:
    """Tables → columns with type, nullability, key, uniqueness and foreign keys, comparable as plain data."""
    return {
        name: {
            c.name: (str(c.type), c.nullable, c.primary_key, bool(c.unique),
                     sorted(fk.target_fullname for fk in c.foreign_keys))
            for c in table.columns
        }
        for name, table in metadata.tables.items()
    }


def test_backend_models_and_init_db_models_are_identical():
    assert describe(backend_models.Base.metadata) == describe(init_db.Base.metadata)


# ---- Open WebUI Functions (devops/open-webui/migrations) ----------------------------------------

FN_INI = ROOT / "devops" / "open-webui" / "migrations" / "alembic.ini"


def test_function_migrations_have_one_head():
    single_head(FN_INI)


@needs_db
def test_function_migrations_up_down_up(database, tmp_path):
    sync_url, _ = database
    env = {"OPEN_WEBUI_FN_DATABASE_URL": sync_url.render_as_string(hide_password=False),
           "LEGACY_FUNCTION_DATA_DIR": str(tmp_path)}  # empty: no old SQLite files to import

    alembic(FN_INI, "upgrade", "head", **env)
    created = tables(sync_url)
    assert FN_TABLES <= created
    # Its own history table, so it never touches Open WebUI's alembic_version in the same database.
    assert "fn_alembic_version" in created and "alembic_version" not in created
    assert created - FN_TABLES == {"fn_alembic_version"}  # only fn_ tables

    alembic(FN_INI, "downgrade", "base", **env)
    assert tables(sync_url) == {"fn_alembic_version"}
    alembic(FN_INI, "upgrade", "head", **env)
    assert FN_TABLES <= tables(sync_url)
