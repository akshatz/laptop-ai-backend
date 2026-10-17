"""Runs devops/opencode/idle-stop.sh and sync-to-postgres.sh with a fake `docker` (fake-docker here).

Each test gets a temporary copy of the scripts (so their repo-root .env is a test one), its own
state directory, and a fresh `opencode_test` database on the Postgres in TEST_DATABASE_URL, which
the scripts reach through fake-docker's psql. Skipped without TEST_DATABASE_URL or psql.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OPENCODE_DB = "opencode_test"  # dropped and recreated by these tests

if not TEST_DATABASE_URL or not shutil.which("psql"):
    if os.environ.get("CI"):  # GitHub Actions: never skip silently
        raise RuntimeError("opencode script tests need TEST_DATABASE_URL and the psql client")
    collect_ignore = ["test_scripts.py"]

T0 = 1_792_224_000_000  # opencode timestamps are milliseconds


def sample_data():
    """Two sessions with one question and answer each, like `opencode db --format json` returns."""
    sessions, messages, parts = [], [], []
    for n in (1, 2):
        sessions.append({
            "id": f"ses_{n}", "project_id": "prj_1", "parent_id": None, "title": f"Session {n}",
            "directory": "/root/workspace", "agent": "build", "model": "openwebui/llama3.2:latest",
            "tokens_input": 100 * n, "tokens_output": 10 * n, "cost": 0,
            "time_created": T0 + n * 1000, "time_updated": T0 + n * 2000,
        })
        for role, text in (("user", f"question {n}"), ("assistant", f"answer {n}")):
            mid = f"msg_{n}_{role}"
            messages.append({"id": mid, "session_id": f"ses_{n}", "time_created": T0, "time_updated": T0,
                             "data": json.dumps({"role": role})})
            parts.append({"id": f"prt_{n}_{role}", "message_id": mid, "session_id": f"ses_{n}",
                          "time_created": T0, "time_updated": T0,
                          "data": json.dumps({"type": "text", "text": text})})
    return {"session": sessions, "message": messages, "part": parts}


class OpencodeScripts:
    def __init__(self, tmp_path: Path):
        url = make_url(TEST_DATABASE_URL)
        self.tmp = tmp_path
        scripts = tmp_path / "repo" / "devops" / "opencode"
        scripts.mkdir(parents=True)
        for name in ("idle-stop.sh", "sync-to-postgres.sh"):
            shutil.copy2(ROOT / "devops" / "opencode" / name, scripts / name)
        (tmp_path / "repo" / ".env").write_text(f"POSTGRES_USER={url.username}\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shutil.copy2(HERE / "fake-docker", bin_dir / "docker")
        self.scripts = scripts
        self.log = tmp_path / "docker.log"
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir()
        self.state = tmp_path / "state" / "opencode-idle-stop" / "laptop-opencode"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(self.log), "FAKE_DATA": str(self.data_dir),
            "FAKE_RUNNING": "true", "FAKE_CONNECTIONS": "0", "FAKE_MARKER": "1/1/1/2/4/4",
            "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
            "PGPASSWORD": url.password or "",
            "OPENCODE_PG_DB": OPENCODE_DB,
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
        self.pg_user = url.username
        self.set_data(sample_data())

    def set_data(self, data: dict):
        for table, rows in data.items():
            (self.data_dir / f"{table}.json").write_text(json.dumps(rows, indent=2))

    def run(self, script: str, **env):
        return subprocess.run(
            [str(self.scripts / script)], env={**self.env, **env}, capture_output=True, text=True, timeout=120,
        )

    def sql(self, query: str, db: str = OPENCODE_DB) -> list[str]:
        out = subprocess.run(
            ["psql", "-U", self.pg_user, "-d", db, "-tAc", query],
            env=self.env, capture_output=True, text=True, check=True,
        ).stdout
        return [line for line in out.splitlines() if line]

    def docker_calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def opencode(tmp_path):
    scripts = OpencodeScripts(tmp_path)
    scripts.sql(f"drop database if exists {OPENCODE_DB} with (force)", db="postgres")
    yield scripts
    scripts.sql(f"drop database if exists {OPENCODE_DB} with (force)", db="postgres")
