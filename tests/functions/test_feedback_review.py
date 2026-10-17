from conftest import load_function

feedback_review = load_function("feedback_review")

CHAT = {
    "history": {
        "messages": {
            "q1": {"role": "user", "content": "What is the Sensex today?"},
            "a1": {
                "role": "assistant",
                "parentId": "q1",
                "content": "The Sensex closed at 81,200.",
                "statusHistory": [
                    {"queries": ["sensex today", "sensex close"]},
                    {"queries": ["sensex today"]},
                ],
                "sources": [
                    {"metadata": [{"source": "https://a.example/s"}, {"source": "https://a.example/s"}]},
                    {"source": {"name": "Family"}, "metadata": []},
                ],
            },
        }
    }
}


def test_fmt_handles_seconds_nanoseconds_and_empty():
    assert feedback_review._fmt(None) == "—"
    assert feedback_review._fmt(0) == "—"
    assert feedback_review._fmt(1_700_000_000) == "2023-11-14 22:13 UTC"
    assert feedback_review._fmt(1_700_000_000 * 10**9) == "2023-11-14 22:13 UTC"


def test_messages_accepts_the_row_or_its_inner_json():
    assert feedback_review._messages({"chat": CHAT}) == CHAT["history"]["messages"]
    assert feedback_review._messages(CHAT) == CHAT["history"]["messages"]
    assert feedback_review._messages(None) == {}
    assert feedback_review._messages("not a chat") == {}


def test_context_pairs_the_answer_with_its_question():
    context = feedback_review._context({"chat": CHAT}, "a1")

    assert context["question"] == "What is the Sensex today?"
    assert context["answer"] == "The Sensex closed at 81,200."
    assert context["search_queries"] == ["sensex today", "sensex close"]
    assert context["sources"] == ["https://a.example/s", "Family"]
    assert context["found_in_chat"] is True


def test_context_of_a_missing_answer():
    context = feedback_review._context({"chat": CHAT}, "gone")
    assert context["found_in_chat"] is False and context["question"] == "" and context["sources"] == []


def test_context_keeps_at_most_ten_sources():
    chat = {"history": {"messages": {"a": {"sources": [
        {"metadata": [{"source": f"https://s{i}.example"} for i in range(15)]}
    ]}}}}
    assert len(feedback_review._context(chat, "a")["sources"]) == 10
