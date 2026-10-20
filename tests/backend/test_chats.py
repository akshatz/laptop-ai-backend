import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

import chat_service


@pytest.fixture
async def user_id(client, emails):
    response = await client.post(
        "/users", json={"email": "ravi@example.com", "password": "test-only-password-1", "name": "Ravi"}
    )
    return response.json()["id"]


@pytest.fixture
async def chat_id(client, user_id):
    response = await client.post("/chats", json={"user_id": user_id, "model_id": "llama3.2"})
    assert response.status_code == 201
    return response.json()["id"]


async def test_health(client):
    assert (await client.get("/health")).json()["status"] == "healthy"


async def test_create_chat_needs_an_existing_user(client):
    response = await client.post("/chats", json={"user_id": str(uuid.uuid4())})
    assert response.status_code == 404


async def test_new_chat_has_no_messages(client, chat_id):
    response = await client.get(f"/chats/{chat_id}/messages")
    assert response.status_code == 200 and response.json() == []


async def test_messages_of_an_unknown_chat(client):
    assert (await client.get(f"/chats/{uuid.uuid4()}/messages")).status_code == 404
    response = await client.post(f"/chats/{uuid.uuid4()}/messages", json={"content": "hi"})
    assert response.status_code == 404


async def test_send_message_stores_question_and_answer(client, chat_id, chain, vector_store):
    vector_store.results = [Document(page_content="Mumbai is on the west coast.")]

    response = await client.post(f"/chats/{chat_id}/messages", json={"content": "Where is Mumbai?"})

    assert response.status_code == 201
    assert response.json()["role"] == "assistant" and response.json()["content"] == "Test reply"
    assert chain.models == ["llama3.2"]
    assert chain.calls[0]["context"] == "Mumbai is on the west coast."
    # Retrieval is limited to this chat's documents.
    assert vector_store.searches[0]["expr"] == f'chat_id == "{chat_id}"'
    messages = (await client.get(f"/chats/{chat_id}/messages")).json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Where is Mumbai?"), ("assistant", "Test reply"),
    ]


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
    assert body["documents_ingested"] == 2 and body["chunks_ingested"] == len(vector_store.added) > 2
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


async def test_ingest_milvus_failure_is_a_502(client, chat_id, vector_store):
    vector_store.add_error = RuntimeError("milvus down")
    response = await client.post(f"/chats/{chat_id}/documents", json={"documents": [{"text": "x"}]})
    assert response.status_code == 502


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


async def test_models_lists_chat_models_without_embedding_models(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/api/tags"
        names = ["llama3.2:latest", "embeddinggemma:latest", "fast-ai:latest", "nomic-embed-text:latest"]
        return chat_service.httpx.Response(200, json={"models": [{"name": n} for n in names]})

    _fake_ollama(monkeypatch, handler)
    response = await client.get("/models")
    assert response.status_code == 200
    assert response.json() == {"default": chat_service.DEFAULT_CHAT_MODEL, "models": ["fast-ai:latest", "llama3.2:latest"]}


async def test_models_ollama_down_is_a_502(client, monkeypatch):
    _fake_ollama(monkeypatch, lambda request: chat_service.httpx.Response(500))
    assert (await client.get("/models")).status_code == 502
