from conftest import load_function

chat_history_trim = load_function("chat_history_trim")


def conversation():
    return [
        {"role": "system", "content": "Today is Saturday."},
        {"role": "user", "content": "Who was Gandhi?"},
        {"role": "assistant", "content": "A leader of India's independence movement."},
        {"role": "user", "content": "What is TCS's share price?"},
        {"role": "assistant", "content": "About 3,100."},
        {"role": "user", "content": "And Infosys? <search results>"},
    ]


def contents(body):
    return [m["content"] for m in body["messages"]]


def test_default_sends_only_system_and_the_latest_question():
    body = chat_history_trim.Filter().request({"messages": conversation()})
    assert contents(body) == ["Today is Saturday.", "And Infosys? <search results>"]


def test_keep_previous_turns():
    f = chat_history_trim.Filter()
    f.valves.keep_previous_turns = 1
    body = f.request({"messages": conversation()})
    assert contents(body) == [
        "Today is Saturday.", "What is TCS's share price?", "About 3,100.", "And Infosys? <search results>",
    ]


def test_negative_keep_counts_as_zero():
    f = chat_history_trim.Filter()
    f.valves.keep_previous_turns = -2
    assert len(f.request({"messages": conversation()})["messages"]) == 2


def test_messages_after_the_latest_question_are_kept():
    messages = conversation() + [{"role": "assistant", "content": "partial"}]
    body = chat_history_trim.Filter().request({"messages": messages})
    assert contents(body)[-2:] == ["And Infosys? <search results>", "partial"]


def test_no_user_message_leaves_the_body_alone():
    messages = [{"role": "system", "content": "s"}]
    assert chat_history_trim.Filter().request({"messages": messages})["messages"] == messages
    assert chat_history_trim.Filter().request({}) == {}
