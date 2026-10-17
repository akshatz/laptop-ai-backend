import json

from conftest import load_function

query_log = load_function("query_log")

USER = {"name": "Asha", "email": "asha@example.com"}

SOURCES = [
    {
        "source": {"type": "web_search", "name": "tcs share price, tcs share price today, tcs share price"},
        "metadata": [
            {"source": "https://www.moneycontrol.com/tcs"},
            {"source": "https://economictimes.com/tcs"},
            {"source": "https://moneycontrol.com/tcs-2"},
        ],
    },
    {"source": {"type": "collection", "name": "Family"}, "metadata": [{"source": "guide.md"}]},
]


def test_entry_summarises_queries_sites_and_chunks():
    entry = query_log.Filter()._entry(USER, {"chat_id": "c1", "sources": SOURCES}, {"id": "fast-ai:latest"})

    assert entry["search_queries"] == "tcs share price | tcs share price today"
    assert entry["sites"] == "moneycontrol.com, economictimes.com"
    assert entry["sites_count"] == 2
    assert entry["chunks"] == 4
    assert entry["knowledge"] == "Family"
    assert entry["model"] == "fast-ai:latest"
    assert entry["via"] == "chat"
    assert entry["user_email"] == "asha@example.com"


def test_requests_without_a_chat_are_api_calls():
    entry = query_log.Filter()._entry({}, {}, None)
    assert entry["via"] == "api" and entry["chunks"] == 0 and entry["search_queries"] == ""


def test_message_text_is_never_logged(tmp_path):
    f = query_log.Filter()
    f.path = str(tmp_path / "query.log")
    body = {"messages": [{"role": "user", "content": "my private question"}]}

    assert f.request(body, USER, {"chat_id": "c1", "sources": SOURCES}, {"id": "m"}) is body

    line = (tmp_path / "query.log").read_text()
    assert "my private question" not in line
    assert json.loads(line)["chat_id"] == "c1"


def test_rotates_when_the_log_is_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(query_log, "MAX_BYTES", 10)
    f = query_log.Filter()
    f.path = str(tmp_path / "query.log")
    (tmp_path / "query.log").write_text("x" * 20)

    f.request({}, USER, {}, None)

    assert (tmp_path / "query.log.1").read_text() == "x" * 20
    assert len((tmp_path / "query.log").read_text().splitlines()) == 1


def test_a_write_error_never_fails_the_chat(tmp_path):
    f = query_log.Filter()
    f.path = str(tmp_path)  # a directory: open() fails
    body = {"messages": []}
    assert f.request(body, USER, {}, None) is body


def test_disabled_writes_nothing(tmp_path):
    f = query_log.Filter()
    f.path = str(tmp_path / "query.log")
    f.valves.enabled = False
    f.request({}, USER, {}, None)
    assert not (tmp_path / "query.log").exists()
