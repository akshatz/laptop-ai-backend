"""evals/feedback_to_cases.py: its SQL against Open WebUI-shaped tables in a real Postgres, and the
draft cases it writes."""

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

import feedback_to_cases as ftc

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DB = "feedback_test"
T = 1_791_000_000  # 2026-10-03


def row(fid="f1aaaaaa-0000", comment="Wrong number", question="What is the Sensex?", answer="It is 3,046.",
        sources=("sensex today",)):
    return {"id": fid, "created_at": T, "comment": comment, "reason": "inaccurate", "model": "fast-ai:latest",
            "answer": answer, "question": question, "sources": list(sources)}


def test_draft_case():
    case = ftc.draft(row())
    assert case == {
        "id": "fb-f1aaaaaa", "feedback_id": "f1aaaaaa-0000", "status": "draft", "category": "grounded-fact",
        "note": "👎 2026-10-03 on fast-ai:latest: Wrong number", "question": "What is the Sensex?",
        "expect": {"search": True}, "rated_answer": "It is 3,046.", "rated_sources": ["sensex today"],
    }


def test_draft_without_comment_or_question():
    case = ftc.draft(row(comment=" ", question=None, sources=()))
    assert case["note"].endswith("(no comment, reason: inaccurate)")
    assert case["question"].startswith("TODO") and "rated_sources" not in case


@pytest.fixture
def evals_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ftc, "HERE", tmp_path)
    monkeypatch.setattr(ftc, "LOCAL", tmp_path / "cases.local.yaml")
    (tmp_path / "cases.yaml").write_text(yaml.safe_dump([{"id": "old", "feedback_id": "f-public"}]))
    monkeypatch.setattr(ftc, "load_env", lambda: {})
    return tmp_path


def main(monkeypatch, rows, *argv):
    monkeypatch.setattr(ftc, "fetch", lambda env: rows)
    monkeypatch.setattr("sys.argv", ["feedback_to_cases.py", *argv])
    ftc.main()


def test_new_ratings_are_appended_once(evals_dir, monkeypatch, capsys):
    rows = [row("f-public"), row("f2bbbbbb-0000")]

    main(monkeypatch, rows)
    main(monkeypatch, rows)  # nothing new the second time

    local = (evals_dir / "cases.local.yaml").read_text()
    assert local.startswith("# Private eval cases (gitignored)")
    assert [c["id"] for c in yaml.safe_load(local)] == ["fb-f2bbbbbb"]
    assert "no new 👎 ratings (2 in total" in capsys.readouterr().out


def test_dry_run_writes_nothing(evals_dir, monkeypatch, capsys):
    main(monkeypatch, [row()], "--dry-run")
    assert not (evals_dir / "cases.local.yaml").exists()
    assert "fb-f1aaaaaa" in capsys.readouterr().out


# ---- the SQL, against Open WebUI-shaped tables -------------------------------------------------

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL or not shutil.which("psql"),
                              reason="needs TEST_DATABASE_URL and psql")

SCHEMA = """
CREATE TABLE feedback (id text PRIMARY KEY, user_id text, version bigint, type text, data json, meta json,
                       snapshot json, created_at bigint NOT NULL, updated_at bigint NOT NULL);
CREATE TABLE chat_message (id text PRIMARY KEY, chat_id text NOT NULL, user_id text, role text NOT NULL,
                           parent_id text, content json, output json, model_id text, files json, sources json,
                           embeds json, done boolean, status_history json, error json, usage json,
                           created_at bigint, updated_at bigint, context_summary text, meta json);
-- chat_message.id is "<chat_id>-<message_id>"; parent_id is the bare message id.
INSERT INTO chat_message (id, chat_id, role, parent_id, content, sources) VALUES
  ('c1-q1', 'c1', 'user', NULL, '"What is the Sensex?"', NULL),
  ('c1-a1', 'c1', 'assistant', 'q1', '"It is 3,046."',
   '[{"source": {"name": "sensex today"}}, {"source": {"name": "sensex today"}}, {"source": {"name": "Family"}}]'),
  ('c1-a2', 'c1', 'assistant', 'q1', '"Second answer"', '{"not": "a list"}');
INSERT INTO feedback (id, data, meta, created_at, updated_at) VALUES
  ('down-1', '{"rating": -1, "comment": "Wrong number", "model_id": "fast-ai:latest"}',
   '{"chat_id": "c1", "message_id": "a1"}', 300, 300),
  ('up-1', '{"rating": 1}', '{"chat_id": "c1", "message_id": "a1"}', 200, 200),
  ('down-2', '{"rating": -1, "reason": "other"}', '{"chat_id": "c1", "message_id": "a2"}', 100, 100),
  ('down-gone', '{"rating": -1}', '{"chat_id": "deleted", "message_id": "x"}', 400, 400);
"""


@needs_db
def test_the_feedback_query():
    url = make_url(TEST_DATABASE_URL)
    env = {**os.environ, "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
           "PGUSER": url.username, "PGPASSWORD": url.password or ""}

    def psql(db, sql, *flags):
        return subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", *flags], input=sql, env=env,
                              capture_output=True, text=True, check=True, timeout=60).stdout

    psql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE); CREATE DATABASE {DB};")
    try:
        psql(DB, SCHEMA)
        rows = [json.loads(line) for line in psql(DB, ftc.SQL, "-At").splitlines() if line.strip()]
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE);")

    assert [r["id"] for r in rows] == ["down-2", "down-1", "down-gone"]  # 👎 only, oldest first
    first, second, gone = rows
    assert second["question"] == "What is the Sensex?" and second["answer"] == "It is 3,046."
    assert sorted(second["sources"]) == ["Family", "sensex today"]  # distinct names
    assert first["answer"] == "Second answer" and first["sources"] is None  # sources not a list
    assert gone["answer"] is None and gone["question"] is None  # chat deleted: draft says TODO
    assert ftc.draft(gone)["question"].startswith("TODO")
