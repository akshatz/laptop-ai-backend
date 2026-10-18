"""Duplicate Upload Notice: records uploads, reports a repeat once. Its SQL is replaced by a dict."""

from types import SimpleNamespace

import pytest

from conftest import load_function, owui

dun = load_function("duplicate_upload_notice")

USER = {"id": "u1", "role": "user"}


@pytest.fixture
def table(monkeypatch):
    rows = {}

    async def known(ids):
        return {i for i in ids if i in rows}

    async def record(row):
        rows.setdefault(row["file_id"], row)
        same = [r for r in rows.values() if r["user_id"] == row["user_id"] and r["file_hash"] == row["file_hash"]
                and r["file_id"] != row["file_id"] and r["uploaded_at"] <= row["uploaded_at"]]
        return min(same, key=lambda r: r["uploaded_at"]) if same else None

    monkeypatch.setattr(dun, "_known", known)
    monkeypatch.setattr(dun, "_record_and_find_earlier", record)
    return rows


def _file(fid, file_hash="h1", user_id="u1", created_at=1792300000, name="menu.pdf"):
    return SimpleNamespace(id=fid, user_id=user_id, filename=name, created_at=created_at,
                           meta={"file_hash": file_hash, "content_type": "application/pdf", "size": 10})


def _serve(*files):
    by_id = {f.id: f for f in files}
    owui.Files.get_file_by_id.side_effect = lambda fid: by_id.get(fid)


def _body(*ids):
    return {"files": [{"type": "file", "id": i} for i in ids], "messages": [{"role": "user", "content": "q"}]}


async def test_a_repeat_upload_is_reported_with_the_first_date(table, events):
    table["old"] = {"file_id": "old", "user_id": "u1", "file_hash": "h1", "filename": "Menu (1).pdf",
                    "uploaded_at": 1792200000}
    _serve(_file("new", created_at=1792300000))

    await dun.Filter().inlet(_body("new"), USER, events)

    assert "new" in table
    assert events.emitted[0]["data"]["description"].startswith("You uploaded Menu (1).pdf before, on ")


async def test_each_file_is_reported_only_when_first_seen(table, events):
    table["old"] = {"file_id": "old", "user_id": "u1", "file_hash": "h1", "filename": "a", "uploaded_at": 1}
    _serve(_file("new"))
    f = dun.Filter()
    await f.inlet(_body("new"), USER, events)
    await f.inlet(_body("new"), USER, events)
    assert len(events.emitted) == 1


async def test_new_content_other_users_and_model_knowledge_are_quiet(table, events):
    table["theirs"] = {"file_id": "theirs", "user_id": "u2", "file_hash": "h1", "filename": "a", "uploaded_at": 1}
    _serve(_file("mine"), _file("someone-elses", user_id="u2", file_hash="h9"))
    body = _body("mine", "someone-elses")
    body["files"] += [{"id": "k1", "legacy": True}, {"type": "collection", "id": "kb"}]

    await dun.Filter().inlet(body, USER, events)

    assert set(table) == {"theirs", "mine"}
    assert events.emitted == []


async def test_errors_never_block_the_chat(monkeypatch, events):
    async def broken(ids):
        raise RuntimeError("no table")
    monkeypatch.setattr(dun, "_known", broken)
    body = _body("x")
    assert await dun.Filter().inlet(body, USER, events) is body
    assert events.emitted == []
