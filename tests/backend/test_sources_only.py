import pytest
from langchain_core.documents import Document

import routers.chats
import web_search


@pytest.fixture(autouse=True)
def sources_only(monkeypatch):
    monkeypatch.setattr(routers.chats, "ANSWER_FROM_SOURCES_ONLY", True)


@pytest.fixture
async def chat_id(client, signed_in):
    return (await client.post("/chats", json={})).json()["id"]


async def test_requests_without_facts_use_the_models_knowledge(client, chat_id, chain, vector_store, web):
    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Suggest a vegetarian dinner recipe"})

    assert response.json()["content"] == "Test reply" and chain.sources_only is False
    assert web.decisions == ["Suggest a vegetarian dinner recipe"] and web.queries == []
    assert response.json()["web_searched"] is False


async def test_factual_question_with_nothing_found_means_no_model_call(client, chat_id, chain, vector_store, web):
    web.needed = True
    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Sensex today?"})

    assert response.json()["content"] == routers.chats.NO_SOURCES_REPLY
    assert chain.calls == [] and web.queries == ["Sensex today?"]


async def test_factual_question_answers_from_sources_with_the_strict_prompt(client, chat_id, chain, vector_store, web):
    web.needed = True
    web.results = [{"title": "Sensex", "url": "https://example.com/s", "content": "Sensex closed at 81,000."}]

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Sensex today?"})

    assert response.json()["content"] == "Test reply" and chain.sources_only is True
    assert "Sensex closed at 81,000." in chain.calls[0]["context"]


async def test_documents_answer_factual_questions_without_the_web(client, chat_id, chain, vector_store, web):
    vector_store.results = [Document(page_content="Paneer tikka 250", metadata={"chat_id": chat_id})]

    await client.post(f"/chats/{chat_id}/messages", json={"content": "Price of paneer tikka?"})

    assert web.queries == [] and "Paneer tikka 250" in chain.calls[0]["context"]


def test_page_text_keeps_the_content_and_drops_the_page_furniture():
    html = """<html><head><style>body{}</style><script>var x = 1;</script></head><body>
      <nav>Home | Recipes | About us and other links</nav>
      <h1>Paneer butter masala for four people</h1>
      <p>Heat two tablespoons of butter and fry the onions until golden.</p>
      <footer>Copyright 2026 and some footer text here</footer></body></html>"""
    text = web_search.page_text(html)
    assert text == ("Paneer butter masala for four people\n"
                    "Heat two tablespoons of butter and fry the onions until golden.")


async def test_with_pages_uses_page_text_and_keeps_snippets_on_failure(monkeypatch):
    async def fake_fetch(client, url):
        return "Full recipe text " * 5 if url.endswith("/good") else ""

    monkeypatch.setattr(web_search, "_fetch_page", fake_fetch)
    results = [{"title": "A", "url": "https://x/good", "content": "snippet a"},
               {"title": "B", "url": "https://x/bad", "content": "snippet b"}]
    out = await web_search.with_pages(results)
    assert out[0]["content"].startswith("Full recipe text") and out[1]["content"] == "snippet b"
