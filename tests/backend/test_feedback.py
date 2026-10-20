import uuid

import pytest
from sqlalchemy import update

import models


@pytest.fixture
async def answered(client, signed_in, chain, vector_store, web):
    """A chat with one question and its answer: (chat_id, question message, answer message)."""
    chat_id = (await client.post("/chats", json={"model_id": "llama3.2"})).json()["id"]
    answer = (await client.post(f"/chats/{chat_id}/messages", json={"content": "Capital of France?"})).json()
    question = (await client.get(f"/chats/{chat_id}/messages")).json()[0]
    return chat_id, question, answer


async def test_first_rating_is_final(client, answered):
    chat_id, _, answer = answered
    url = f"/chats/{chat_id}/messages/{answer['id']}/feedback"
    assert answer["feedback"] is None

    assert (await client.put(url, json={"rating": 1})).json()["feedback"] == 1
    assert (await client.get(f"/chats/{chat_id}/messages")).json()[1]["feedback"] == 1
    assert (await client.put(url, json={"rating": -1})).status_code == 409
    assert (await client.put(url, json={"rating": None})).status_code == 409
    assert (await client.get(f"/chats/{chat_id}/messages")).json()[1]["feedback"] == 1


async def test_a_rating_is_needed(client, answered):
    chat_id, _, answer = answered
    assert (await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": None})).status_code == 400


async def test_only_ratings_1_and_minus_1(client, answered):
    chat_id, _, answer = answered
    response = await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": 5})
    assert response.status_code == 422


async def test_questions_and_other_peoples_answers_cant_be_rated(client, answered, sign_in, make_client):
    chat_id, question, answer = answered
    assert (await client.put(f"/chats/{chat_id}/messages/{question['id']}/feedback", json={"rating": 1})).status_code == 404
    assert (await client.put(f"/chats/{chat_id}/messages/{uuid.uuid4()}/feedback", json={"rating": 1})).status_code == 404
    async with await make_client() as other:
        await sign_in(other, "meera@example.com", "Meera")
        response = await other.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": -1})
        assert response.status_code == 404


async def test_dashboard_needs_a_session(client):
    assert (await client.get("/feedback")).status_code == 401


async def test_dashboard_counts_and_lists_with_the_question(client, answered):
    chat_id, _, answer = answered
    await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": -1})

    data = (await client.get("/feedback")).json()

    assert data["scope"] == "mine" and data["days"] == 30
    assert (data["answers"], data["likes"], data["dislikes"]) == (1, 0, 1)
    assert data["by_model"] == [{"model_id": "llama3.2", "answers": 1, "likes": 0, "dislikes": 1}]
    [item] = data["recent"]
    assert item["question"] == "Capital of France?" and item["rating"] == -1
    assert item["chat_id"] == chat_id and item["user_name"] == "Ravi"


async def test_dashboard_mine_vs_admin_everyone(client, answered, session_maker, sign_in, make_client):
    chat_id, _, answer = answered
    await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": 1})
    async with await make_client() as other:
        meera = await sign_in(other, "meera@example.com", "Meera")
        mine = (await other.get("/feedback")).json()
        assert mine["scope"] == "mine" and mine["answers"] == 0 and mine["recent"] == []

        async with session_maker() as session:
            await session.execute(update(models.UserModel).where(models.UserModel.id == uuid.UUID(meera)).values(role="admin"))
            await session.commit()
        everyone = (await other.get("/feedback")).json()
        assert everyone["scope"] == "everyone" and everyone["likes"] == 1
        assert everyone["recent"][0]["user_name"] == "Ravi"


async def test_dislike_with_a_comment(client, answered):
    chat_id, _, answer = answered
    url = f"/chats/{chat_id}/messages/{answer['id']}/feedback"

    saved = (await client.put(url, json={"rating": -1, "comment": "  Wrong price, it's 250  "})).json()
    assert saved["feedback"] == -1 and saved["feedback_comment"] == "Wrong price, it's 250"
    assert (await client.get("/feedback")).json()["recent"][0]["comment"] == "Wrong price, it's 250"

    assert (await client.put(url, json={"rating": -1, "comment": "x" * 1001})).status_code == 422


async def test_regenerating_a_disliked_answer_tells_the_model_why(client, answered, chain):
    chat_id, _, answer = answered
    await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": -1, "comment": "It's outdated"})

    regenerated = (await client.post(f"/chats/{chat_id}/messages/{answer['id']}/regenerate")).json()

    context = chain.calls[-1]["context"]
    assert 'marked this earlier answer to the same question as bad and said: "It\'s outdated"' in context
    assert context.endswith("Earlier answer:\n" + answer["content"])
    assert regenerated["feedback"] is None and regenerated["feedback_comment"] is None


async def test_regenerating_an_unrated_answer_adds_nothing(client, answered, chain):
    chat_id, _, answer = answered
    await client.post(f"/chats/{chat_id}/messages/{answer['id']}/regenerate")
    assert "marked this earlier answer" not in chain.calls[-1]["context"]
