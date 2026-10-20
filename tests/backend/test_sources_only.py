import pytest
from langchain_core.documents import Document

import routers.chats


@pytest.fixture(autouse=True)
def sources_only(monkeypatch):
    monkeypatch.setattr(routers.chats, "ANSWER_FROM_SOURCES_ONLY", True)


@pytest.fixture
async def chat_id(client, signed_in):
    return (await client.post("/chats", json={})).json()["id"]


async def test_nothing_found_means_no_model_call(client, chat_id, chain, vector_store, web):
    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Write me a poem"})

    assert response.status_code == 201
    assert response.json()["content"] == routers.chats.NO_SOURCES_REPLY
    assert chain.calls == []
    # The web was searched without asking the model whether to.
    assert web.decisions == [] and web.queries == ["Write me a poem"]
    assert response.json()["web_searched"] is True


async def test_web_results_answer_with_the_strict_prompt(client, chat_id, chain, vector_store, web):
    web.results = [{"title": "Sensex", "url": "https://example.com/s", "content": "Sensex closed at 81,000."}]

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Sensex today?"})

    assert response.json()["content"] == "Test reply" and chain.sources_only is True
    assert "Sensex closed at 81,000." in chain.calls[0]["context"]


async def test_documents_answer_without_a_forced_web_search(client, chat_id, chain, vector_store, web):
    vector_store.results = [Document(page_content="Paneer tikka 250", metadata={"chat_id": chat_id})]

    await client.post(f"/chats/{chat_id}/messages", json={"content": "Price of paneer tikka?"})

    # Documents found: the web only if the decision asks for it (it doesn't here).
    assert web.decisions == ["Price of paneer tikka?"] and web.queries == []
    assert "Paneer tikka 250" in chain.calls[0]["context"]
