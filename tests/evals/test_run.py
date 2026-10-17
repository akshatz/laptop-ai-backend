"""evals/run.py: scoring, case loading, the streamed answer parser and whole runs, against a fake
Open WebUI (conftest.py). No model or Docker needed."""

import json
import re
import urllib.error
from argparse import Namespace

import pytest
import yaml

import run

WEB = {"source": {"type": "web_search", "name": "sensex today, sensex close, sensex today"},
       "metadata": [{"source": "https://www.moneycontrol.com/a"}, {"source": "https://economictimes.com/b"},
                    {"source": "https://moneycontrol.com/c"}]}


# ---- scoring -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I don't know the score of that match.", "There is no record of such a law.",
    "I couldn't find anything about it.", "That company is fictional.", "The final hasn't happened yet.",
    "No information is available.", "I'm unable to confirm that.",
])
def test_abstain_phrases(text):
    assert run.ABSTAIN.search(text)


@pytest.mark.parametrize("text", [
    "The Sensex closed at 81,200 on Friday.", "Gandhi Jayanti is on 2 October.",
    "Knowledge is power.",  # "know" inside another word
])
def test_ordinary_answers_are_not_abstaining(text):
    assert not run.ABSTAIN.search(text)


def test_source_info():
    info = run.source_info([WEB, {"source": {"type": "collection", "name": "Family"}, "metadata": [{"source": "guide.md"}]}])
    assert info == {"searched": True, "domains": ["moneycontrol.com", "economictimes.com"],
                    "queries": ["sensex today", "sensex close"], "chunks": 4}
    assert run.source_info([])["searched"] is False


def test_score_checks_each_expectation():
    case = {"expect": {"search": True, "facts": [["66 ?%", "two[- ]thirds"], "1919"],
                       "exclude": ["Jamsetji[^.]{0,80}willed"], "min_domains": 2, "abstain": False}}
    info = run.source_info([WEB])

    good = run.score(case, "The trusts own two-thirds, since 1919.", info)
    assert all(good.values()) and set(good) == {
        "non-empty", "search", "fact 66 ?%", "fact 1919", "not Jamsetji[^.]{0,80}willed", "2+ sites", "answers"}

    bad = run.score(case, "Jamsetji Tata willed it. I don't know when.", run.source_info([]))
    assert {k for k, ok in bad.items() if not ok} == {
        "search", "fact 66 ?%", "fact 1919", "not Jamsetji[^.]{0,80}willed", "2+ sites", "answers"}


def test_abstain_cases_must_say_they_do_not_know():
    case = {"expect": {"abstain": True}}
    assert run.score(case, "There is no such law.", {"searched": False, "domains": []})["abstains"]
    assert not run.score(case, "The law was passed in 2025.", {"searched": False, "domains": []})["abstains"]
    assert not run.score(case, "  ", {"searched": False, "domains": []})["non-empty"]


# ---- the committed cases --------------------------------------------------------------------------

CASES = yaml.safe_load((run.HERE / "cases.yaml").read_text())
CATEGORIES = {"grounded-fact", "current-data", "follow-up", "topic-switch", "abstain", "general"}
EXPECT_KEYS = {"facts", "exclude", "search", "min_domains", "abstain"}


def test_committed_cases_are_well_formed():
    """Every case in evals/cases.yaml: unique id, known category and checks, regexes that compile."""
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["category"] in CATEGORIES, case["id"]
        assert case["question"].strip(), case["id"]
        expect = case.get("expect") or {}
        assert set(expect) <= EXPECT_KEYS, (case["id"], set(expect) - EXPECT_KEYS)
        for fact in expect.get("facts") or []:
            for pattern in fact if isinstance(fact, list) else [fact]:
                re.compile(pattern)
        for pattern in expect.get("exclude") or []:
            re.compile(pattern)
        for turn in case.get("history") or []:
            assert turn["role"] in ("user", "assistant") and turn["content"], case["id"]


# ---- loading cases -------------------------------------------------------------------------------

@pytest.fixture
def cases_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "HERE", tmp_path)

    def write(public, local=None):
        (tmp_path / "cases.yaml").write_text(yaml.safe_dump(public))
        if local is not None:
            (tmp_path / "cases.local.yaml").write_text(yaml.safe_dump(local))

    return write


def case(cid, category="general", **extra):
    return {"id": cid, "category": category, "question": f"q {cid}", **extra}


def test_load_cases_merges_local_and_filters(cases_dir):
    cases_dir([case("a"), case("b", "abstain")],
              [case("c"), case("d", status="draft"), case("e", status="rejected")])

    assert [c["id"] for c in run.load_cases(None, None)] == ["a", "b", "c"]
    assert [c["id"] for c in run.load_cases(None, None, include_drafts=True)] == ["a", "b", "c", "d"]
    assert [c["id"] for c in run.load_cases("a,c", None)] == ["a", "c"]
    assert [c["id"] for c in run.load_cases(None, "abstain")] == ["b"]


def test_load_cases_refuses_duplicates_and_unknown_ids(cases_dir):
    cases_dir([case("a")], [case("a")])
    with pytest.raises(SystemExit, match="duplicate case ids: a"):
        run.load_cases(None, None)
    cases_dir([case("a")], [])
    with pytest.raises(SystemExit, match="unknown case ids: zz"):
        run.load_cases("zz", None)


# ---- talking to Open WebUI ------------------------------------------------------------------------

def test_ask_parses_the_stream(owui):
    owui.replies = [{"answer": "The Sensex closed at 81,200.", "sources": [WEB]}]

    reply = run.ask({"OPEN_WEBUI_URL": owui.url, "OPEN_WEBUI_API_KEY": "k"}, "fast-ai:latest",
                    [{"role": "user", "content": "Sensex?"}], True, timeout=10)

    assert reply["answer"] == "The Sensex closed at 81,200."
    assert reply["sources"] == [WEB] and reply["error"] is None
    assert reply["first_token_s"] is not None
    sent = owui.requests[0]
    assert sent["auth"] == "Bearer k"
    assert sent["body"]["features"] == {"web_search": True} and sent["body"]["stream"] is True


def args(**extra):
    return Namespace(model="fast-ai:latest", timeout=10, label="test run", only=None, category=None,
                     include_drafts=False, **extra)


def test_auth_errors_stop_the_run(owui):
    owui.replies = [{"status": 401}]
    with pytest.raises(SystemExit, match="HTTP 401"):
        run.run_case(args(), {"OPEN_WEBUI_URL": owui.url, "OPEN_WEBUI_API_KEY": "bad"}, [], True)


def test_server_errors_are_a_failed_case(owui):
    owui.replies = [{"status": 500}]
    reply = run.run_case(args(), {"OPEN_WEBUI_URL": owui.url, "OPEN_WEBUI_API_KEY": "k"}, [], True)
    assert reply["error"].startswith("HTTP 500") and reply["unreachable"] is False


def test_unreachable_open_webui_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(run, "wait_for_health", lambda env: False)
    reply = run.run_case(args(), {"OPEN_WEBUI_URL": "http://127.0.0.1:9", "OPEN_WEBUI_API_KEY": "k"}, [], True)
    assert reply["unreachable"] is True


# ---- whole runs -----------------------------------------------------------------------------------

@pytest.fixture
def run_env(owui, cases_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(run, "REPO", tmp_path)
    monkeypatch.setattr(run, "settings_snapshot", lambda env, model: {"rag.top_k": 6})
    monkeypatch.setattr(run, "settings_file_state", lambda: {"commit": "abc1234", "uncommitted_edits": False,
                                                             "db_matches_file": True})
    return {"OPEN_WEBUI_URL": owui.url, "OPEN_WEBUI_API_KEY": "k"}


def test_a_run_saves_header_and_results(run_env, owui, cases_dir, tmp_path):
    cases_dir([
        case("sensex", "current-data", expect={"search": True, "facts": ["81,200"]}),
        case("fiction", "abstain", web_search=False, expect={"abstain": True},
             history=[{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]),
    ])
    owui.replies = [{"answer": "It closed at 81,200.", "sources": [WEB]}, {"answer": "It closed at 5."}]

    run.run(args(), run_env)

    [saved] = (tmp_path / "runs").iterdir()
    assert saved.name.endswith("-test-run.jsonl")
    lines = [json.loads(line) for line in saved.read_text().splitlines()]
    assert lines[0]["run"]["settings"] == {"rag.top_k": 6} and lines[0]["run"]["cases"] == 2
    assert [(r["id"], r["passed"]) for r in lines[1:]] == [("sensex", True), ("fiction", False)]
    # History goes before the question; web search follows the case.
    second = owui.requests[1]["body"]
    assert [m["content"] for m in second["messages"]] == ["earlier", "reply", "q fiction"]
    assert second["features"] == {"web_search": False}


def test_a_run_needs_an_api_key(cases_dir):
    with pytest.raises(SystemExit, match="OPEN_WEBUI_API_KEY"):
        run.run(args(), {})


def test_three_unreachable_cases_in_a_row_stop_the_run(run_env, cases_dir, tmp_path, monkeypatch):
    cases_dir([case(str(i)) for i in range(5)])
    calls = []
    monkeypatch.setattr(run, "run_case", lambda *a: calls.append(1) or run.no_reply("down", unreachable=True))

    run.run(args(), run_env)

    assert len(calls) == 3
    [saved] = (tmp_path / "runs").iterdir()
    assert len(saved.read_text().splitlines()) == 1  # header only: not-run cases aren't failures


def test_compare_two_runs(tmp_path, capsys):
    def save(name, label, results, top_k):
        path = tmp_path / name
        lines = [{"run": {"label": label, "settings": {"rag.top_k": top_k}}}] + results
        path.write_text("\n".join(json.dumps(line) for line in lines))
        return str(path)

    a = save("a.jsonl", "before", [{"id": "x", "passed": False, "latency_s": 60},
                                   {"id": "y", "passed": True, "latency_s": 40}], 6)
    b = save("b.jsonl", "after", [{"id": "x", "passed": True, "latency_s": 50},
                                  {"id": "y", "passed": False, "latency_s": 30}], 8)

    run.compare(a, b)

    out = capsys.readouterr().out
    assert "rag.top_k: 6 → 8" in out and "passed: 1/2 → 1/2" in out
    assert "now passing:\n  x" in out and "now failing:\n  y" in out
    assert "median answer time: 50 s → 40 s" in out
