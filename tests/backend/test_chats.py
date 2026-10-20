import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

import chat_service
import routers.chats


@pytest.fixture
async def chat_id(client, signed_in):
    response = await client.post("/chats", json={"model_id": "llama3.2"})
    assert response.status_code == 201
    assert response.json()["user_id"] == signed_in
    return response.json()["id"]


async def test_health(client):
    assert (await client.get("/health")).json()["status"] == "healthy"


async def test_chat_endpoints_need_a_session(client):
    assert (await client.post("/chats", json={})).status_code == 401
    assert (await client.get("/chats")).status_code == 401
    assert (await client.get(f"/chats/{uuid.uuid4()}/messages")).status_code == 401
    assert (await client.post(f"/chats/{uuid.uuid4()}/messages", json={"content": "hi"})).status_code == 401


async def test_forged_or_expired_session_is_refused(client, signed_in):
    import sessions

    client.cookies.set(sessions.SESSION_COOKIE, sessions.sign({"uid": signed_in}, -1))
    assert (await client.get("/chats")).status_code == 401
    good = sessions.sign({"uid": signed_in}, 3600)
    client.cookies.set(sessions.SESSION_COOKIE, good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert (await client.get("/chats")).status_code == 401


async def test_user_id_in_the_body_is_ignored(client, signed_in):
    response = await client.post("/chats", json={"user_id": str(uuid.uuid4())})
    assert response.status_code == 201 and response.json()["user_id"] == signed_in


async def test_chats_lists_only_your_own(client, chat_id, sign_in, make_client):
    async with await make_client() as other:
        await sign_in(other, "meera@example.com", "Meera")
        assert (await other.post("/chats", json={})).status_code == 201
        assert [c["id"] for c in (await client.get("/chats")).json()] == [chat_id]
        # Someone else's chat looks like it doesn't exist.
        assert (await other.get(f"/chats/{chat_id}/messages")).status_code == 404
        assert (await other.post(f"/chats/{chat_id}/messages", json={"content": "hi"})).status_code == 404
        response = await other.post(f"/chats/{chat_id}/documents", json={"documents": [{"text": "x"}]})
        assert response.status_code == 404


async def test_new_chat_has_no_messages(client, chat_id):
    response = await client.get(f"/chats/{chat_id}/messages")
    assert response.status_code == 200 and response.json() == []


async def test_messages_of_an_unknown_chat(client, signed_in):
    assert (await client.get(f"/chats/{uuid.uuid4()}/messages")).status_code == 404
    response = await client.post(f"/chats/{uuid.uuid4()}/messages", json={"content": "hi"})
    assert response.status_code == 404


async def test_send_message_stores_question_and_answer(client, chat_id, chain, vector_store):
    vector_store.results = [Document(page_content="Mumbai is on the west coast.")]

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Where is Mumbai?"})

    assert response.status_code == 201
    assert response.json()["role"] == "assistant" and response.json()["content"] == "Test reply"
    assert chain.models == ["llama3.2"]
    assert chain.calls[0]["context"] == "From the chat's documents:\nMumbai is on the west coast."
    assert response.json()["web_searched"] is False and response.json()["web_sources"] == []
    # Retrieval is limited to this chat's documents.
    assert vector_store.searches[0]["expr"] == f'chat_id == "{chat_id}"'
    messages = (await client.get(f"/chats/{chat_id}/messages")).json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Where is Mumbai?"), ("assistant", "Test reply"),
    ]


async def test_question_limit_per_chat(client, chat_id, chain, vector_store, monkeypatch):
    monkeypatch.setattr(routers.chats, "MAX_QUESTIONS_PER_CHAT", 1)
    assert (await client.post(f"/chats/{chat_id}/messages", json={"content": "First question"})).status_code == 201
    second = await client.post(f"/chats/{chat_id}/messages", json={"content": "Second question"})

    assert second.status_code == 409 and "new chat" in second.json()["detail"]
    assert len(chain.calls) == 1
    assert [m["content"] for m in (await client.get(f"/chats/{chat_id}/messages")).json()] == ["First question", "Test reply"]


async def test_earlier_messages_are_sent_as_history(client, chat_id, chain, vector_store):
    await client.post(f"/chats/{chat_id}/messages", json={"content": "First question"})
    await client.post(f"/chats/{chat_id}/messages", json={"content": "Second question"})

    history = chain.calls[1]["history"]
    assert [type(m) for m in history] == [HumanMessage, AIMessage]
    assert [m.content for m in history] == ["First question", "Test reply"]
    assert chain.calls[1]["input"] == "Second question"


async def test_failed_retrieval_still_answers(client, chat_id, chain, vector_store):
    vector_store.search_error = RuntimeError("milvus down")

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Hello"})

    assert response.status_code == 201
    assert chain.calls[0]["context"] == "No relevant context found."


async def test_model_failure_is_a_502_but_the_question_is_kept(client, chat_id, chain, vector_store):
    chain.error = RuntimeError("ollama down")

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Hello"})

    assert response.status_code == 502
    messages = (await client.get(f"/chats/{chat_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user"]


async def test_ingest_splits_documents_and_tags_them_with_the_chat(client, chat_id, vector_store):
    long_text = "word " * 500  # ~2,500 characters: more than one 1,000-character chunk
    response = await client.post(
        f"/chats/{chat_id}/documents",
        json={"documents": [{"text": long_text, "source": "notes.txt"}, {"text": "short"}]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["documents_ingested"] == 2 and len(vector_store.added) > 2
    assert {d.metadata["chat_id"] for d in vector_store.added} == {chat_id}
    assert vector_store.added[0].metadata["source"] == "notes.txt"


async def test_ingest_limits(client, chat_id, vector_store):
    empty = await client.post(f"/chats/{chat_id}/documents", json={"documents": []})
    assert empty.status_code == 400
    too_many = await client.post(
        f"/chats/{chat_id}/documents", json={"documents": [{"text": "x"}] * 6}
    )
    assert too_many.status_code == 400
    unknown = await client.post(f"/chats/{uuid.uuid4()}/documents", json={"documents": [{"text": "x"}]})
    assert unknown.status_code == 404
    assert vector_store.added == []


async def test_ingest_milvus_failure_marks_the_document_failed(client, chat_id, vector_store):
    # Embedding runs after the response, so a Milvus failure shows up as the document's status.
    vector_store.add_error = RuntimeError("milvus down")
    response = await client.post(f"/chats/{chat_id}/documents", json={"documents": [{"text": "x"}]})
    assert response.status_code == 201
    assert (await client.get("/documents")).json()[0]["status"] == "failed"


def test_embeddinggemma_task_prefixes(monkeypatch):
    seen = []

    def fake_embed(self, texts):
        seen.extend(texts)
        return [[0.0] for _ in texts]

    monkeypatch.setattr(chat_service.OllamaEmbeddings, "embed_documents", fake_embed)
    embeddings = chat_service.PrefixedOllamaEmbeddings(base_url="http://ollama.invalid", model="embeddinggemma")

    embeddings.embed_documents(["a page"])
    embeddings.embed_query("a question")

    assert seen == ["title: none | text: a page", "task: search result | query: a question"]


def test_collection_is_named_per_embedding_model():
    assert chat_service.MILVUS_COLLECTION == "custom_backend_docs_embeddinggemma"


def _fake_ollama(monkeypatch, handler):
    real_client = chat_service.httpx.AsyncClient
    monkeypatch.setattr(
        chat_service.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=chat_service.httpx.MockTransport(handler), **kwargs),
    )


async def test_models_lists_only_installed_chat_models(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/api/tags"
        names = ["llama3.2:latest", "embeddinggemma:latest", "fast-ai:latest", "gemma3:12b", "photo-ai:latest"]
        return chat_service.httpx.Response(200, json={"models": [{"name": n} for n in names]})

    _fake_ollama(monkeypatch, handler)
    monkeypatch.setattr(chat_service, "CHAT_MODELS", "fast-ai, llama3.2,gemma3:4b")
    response = await client.get("/models")
    assert response.status_code == 200
    assert response.json() == {"default": chat_service.DEFAULT_CHAT_MODEL, "models": ["fast-ai:latest", "llama3.2:latest"],
                               "max_questions_per_chat": 0, "max_regenerations": 2}


async def test_models_ollama_down_is_a_502(client, monkeypatch):
    _fake_ollama(monkeypatch, lambda request: chat_service.httpx.Response(500))
    assert (await client.get("/models")).status_code == 502


async def test_web_search_when_the_question_needs_it(client, chat_id, chain, vector_store, web):
    web.needed = True
    web.results = [{"title": "Sensex today", "url": "https://example.com/sensex", "content": "Sensex closed at 81,000."}]

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Sensex today?"})

    assert response.status_code == 201
    assert web.decisions == ["Sensex today?"] and web.queries == ["Sensex today?"]
    context = chain.calls[0]["context"]
    assert context.startswith("Web search results:\n[1] Sensex today (https://example.com/sensex)")
    assert "Sensex closed at 81,000." in context
    assert response.json()["web_searched"] is True
    assert response.json()["web_sources"] == [{"title": "Sensex today", "url": "https://example.com/sensex"}]


async def test_no_web_search_when_not_needed(client, chat_id, chain, vector_store, web):
    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "What is 2 + 2?"})
    assert web.decisions == ["What is 2 + 2?"] and web.queries == []
    assert response.json()["web_searched"] is False


async def test_web_search_without_results_still_answers(client, chat_id, chain, vector_store, web):
    web.needed = True
    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Weather in Pune?"})
    assert response.status_code == 201
    assert chain.calls[0]["context"] == "No relevant context found."
    assert response.json()["web_searched"] is True and response.json()["web_sources"] == []


async def test_decision_and_search_fail_safe(monkeypatch, web):
    """Ollama or SearXNG unreachable: no web search, no exception."""
    import httpx

    import web_search

    class Down:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ConnectError("down")
        async def get(self, *a, **k): raise httpx.ConnectError("down")

    monkeypatch.setattr(web_search.httpx, "AsyncClient", Down)
    monkeypatch.setattr(web_search, "WEB_SEARCH", "auto")
    assert await web.real_needs("news?") is False
    assert await web.real_search("news") == []


async def test_first_question_names_the_chat(client, chat_id, chain, vector_store):
    await client.post(f"/chats/{chat_id}/messages", json={"content": "What  is the\ncapital of Gujarat?"})
    await client.post(f"/chats/{chat_id}/messages", json={"content": "And its population?"})
    chats = (await client.get("/chats")).json()
    assert chats[0]["title"] == "What is the capital of Gujarat?"


async def test_long_first_question_is_shortened(client, chat_id, chain, vector_store):
    await client.post(f"/chats/{chat_id}/messages", json={"content": "word " * 30})
    title = (await client.get("/chats")).json()[0]["title"]
    assert len(title) <= 60 and title.endswith("…")


async def test_regenerate_replaces_the_latest_answer(client, chat_id, chain, vector_store):
    answer = (await client.post(f"/chats/{chat_id}/messages", json={"content": "Capital of Gujarat?"})).json()
    await client.put(f"/chats/{chat_id}/messages/{answer['id']}/feedback", json={"rating": -1})
    chain.reply = "Gandhinagar"

    response = await client.post(f"/chats/{chat_id}/messages/{answer['id']}/regenerate")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == answer["id"] and body["content"] == "Gandhinagar"
    assert body["regenerations"] == 1 and body["feedback"] is None
    assert chain.calls[1]["input"] == "Capital of Gujarat?" and chain.calls[1]["history"] == []
    stored = (await client.get(f"/chats/{chat_id}/messages")).json()
    assert [m["content"] for m in stored] == ["Capital of Gujarat?", "Gandhinagar"]


async def test_regenerate_limit(client, chat_id, chain, vector_store):
    answer = (await client.post(f"/chats/{chat_id}/messages", json={"content": "Hi"})).json()
    url = f"/chats/{chat_id}/messages/{answer['id']}/regenerate"
    assert (await client.post(url)).status_code == 200
    assert (await client.post(url)).status_code == 200
    third = await client.post(url)
    assert third.status_code == 409 and len(chain.calls) == 3


async def test_only_the_latest_answer_of_your_own_chat(client, chat_id, chain, vector_store, sign_in, make_client):
    answer = (await client.post(f"/chats/{chat_id}/messages", json={"content": "Hi"})).json()
    question = (await client.get(f"/chats/{chat_id}/messages")).json()[0]
    assert (await client.post(f"/chats/{chat_id}/messages/{question['id']}/regenerate")).status_code == 404
    async with await make_client() as other:
        await sign_in(other, "meera@example.com", "Meera")
        assert (await other.post(f"/chats/{chat_id}/messages/{answer['id']}/regenerate")).status_code == 404
