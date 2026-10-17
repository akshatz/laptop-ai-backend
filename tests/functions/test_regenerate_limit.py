from types import SimpleNamespace

import pytest

from conftest import load_function

regenerate_limit = load_function("regenerate_limit")

USER = {"role": "user"}


def chat_with(answers: int, model="fast-ai:latest", other_model_answers=0):
    """A saved chat: question q1, `answers` earlier answers from `model`, and the new answer `new`."""
    messages = {"q1": {"role": "user", "parentId": None}}
    for i in range(answers):
        messages[f"a{i}"] = {"role": "assistant", "parentId": "q1", "model": model}
    for i in range(other_model_answers):
        messages[f"o{i}"] = {"role": "assistant", "parentId": "q1", "model": "other:latest"}
    messages["new"] = {"role": "assistant", "parentId": "q1", "model": model}
    return SimpleNamespace(chat={"history": {"messages": messages}})


def metadata(**extra):
    return {"chat_id": "c1", "message_id": "new", **extra}


async def test_allows_answers_below_the_limit(chats):
    chats["c1"] = chat_with(answers=2)
    body = {"model": "fast-ai:latest"}
    assert await regenerate_limit.Filter().inlet(body, USER, metadata()) is body


async def test_refuses_once_the_question_has_max_answers(chats):
    chats["c1"] = chat_with(answers=3)
    with pytest.raises(Exception, match="already has 3 answers"):
        await regenerate_limit.Filter().inlet({"model": "fast-ai:latest"}, USER, metadata(user_message_id="q1"))


async def test_finds_the_question_from_the_answers_parent(chats):
    chats["c1"] = chat_with(answers=3)
    with pytest.raises(Exception):
        await regenerate_limit.Filter().inlet({"model": "fast-ai:latest"}, USER, metadata())


async def test_answers_from_other_models_do_not_count(chats):
    chats["c1"] = chat_with(answers=2, other_model_answers=5)
    await regenerate_limit.Filter().inlet({"model": "fast-ai:latest"}, USER, metadata())


async def test_admins_are_exempt_unless_switched_off(chats):
    chats["c1"] = chat_with(answers=5)
    f = regenerate_limit.Filter()
    await f.inlet({"model": "fast-ai:latest"}, {"role": "admin"}, metadata())

    f.valves.exempt_admins = False
    with pytest.raises(Exception):
        await f.inlet({"model": "fast-ai:latest"}, {"role": "admin"}, metadata())


async def test_max_answers_valve(chats):
    chats["c1"] = chat_with(answers=1)
    f = regenerate_limit.Filter()
    f.valves.max_answers = 1
    with pytest.raises(Exception, match="already has 1 answers"):
        await f.inlet({"model": "fast-ai:latest"}, USER, metadata())


async def test_unsaved_chats_and_missing_ids_are_not_limited(chats):
    f = regenerate_limit.Filter()
    await f.inlet({}, USER, metadata())  # temporary chat: not found
    await f.inlet({}, USER, {"chat_id": "c1"})  # no message id
    await f.inlet({}, USER, None)
