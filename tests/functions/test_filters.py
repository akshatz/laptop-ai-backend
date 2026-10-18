"""Photo Router and Web Search Notice: small filters, tested together."""

from types import SimpleNamespace

import pytest

from conftest import load_function, owui

photo_router = load_function("photo_router")
web_search_notice = load_function("web_search_notice")

PICTURE = [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": "data:..."}}]


async def test_a_picture_goes_to_photo_ai_without_web_search(events):
    body = {"model": "fast-ai:latest", "features": {"web_search": True},
            "messages": [{"role": "user", "content": PICTURE}]}

    body = await photo_router.Filter().inlet(body, events)

    assert body["model"] == "photo-ai:latest"
    assert body["features"]["web_search"] is False
    assert events.emitted[0]["data"]["description"].startswith("Reading the picture")


async def test_only_the_latest_question_counts(events):
    body = {"model": "fast-ai:latest", "messages": [
        {"role": "user", "content": PICTURE},
        {"role": "assistant", "content": "A cat."},
        {"role": "user", "content": "Who won the match?"},
    ]}
    assert (await photo_router.Filter().inlet(body, events))["model"] == "fast-ai:latest"
    assert events.emitted == []


async def test_text_only_and_already_routed_requests_are_unchanged(events):
    f = photo_router.Filter()
    text = {"model": "fast-ai:latest", "messages": [{"role": "user", "content": "hi"}]}
    assert (await f.inlet(text, events))["model"] == "fast-ai:latest"
    routed = {"model": "photo-ai:latest", "messages": [{"role": "user", "content": PICTURE}]}
    assert (await f.inlet(routed, events))["model"] == "photo-ai:latest"
    assert events.emitted == []


PDF = {"type": "file", "id": "f1", "name": "bill.pdf"}


async def test_an_attached_document_goes_to_photo_ai_without_web_search(events):
    body = {"model": "fast-ai:latest", "features": {"web_search": True}, "files": [PDF],
            "messages": [{"role": "user", "content": "What is the total?"}]}

    body = await photo_router.Filter().inlet(body, events)

    assert body["model"] == "photo-ai:latest"
    assert body["features"]["web_search"] is False
    assert events.emitted[0]["data"]["description"].startswith("Reading your document")


async def test_document_routing_can_be_turned_off_separately(events):
    f = photo_router.Filter()
    f.valves.route_documents = False
    body = {"model": "fast-ai:latest", "features": {"web_search": True}, "files": [PDF],
            "messages": [{"role": "user", "content": "q"}]}
    body = await f.inlet(body, events)
    assert body["model"] == "fast-ai:latest" and body["features"]["web_search"] is False


async def test_model_knowledge_and_folders_are_not_attached_documents(events):
    model = {"info": {"meta": {"knowledge": [{"id": "k1", "type": "collection"}]}}}
    body = {"model": "fast-ai:latest", "features": {"web_search": True},
            "files": [{"id": "k1", "type": "collection"}, {"id": "c", "legacy": True},
                      {"type": "folder", "id": "x"}, {"type": "image", "id": "i"}],
            "messages": [{"role": "user", "content": "news?"}]}

    body = await photo_router.Filter().inlet(body, events, model)

    assert body["features"]["web_search"] is True
    assert events.emitted == []


async def test_document_rule_can_be_turned_off_and_ignores_search_already_off(events):
    f = photo_router.Filter()
    f.valves.skip_web_search_for_files = False
    on = {"features": {"web_search": True}, "files": [PDF], "messages": [{"role": "user", "content": "q"}]}
    assert (await f.inlet(on, events))["features"]["web_search"] is True
    off = {"features": {"web_search": False}, "files": [PDF], "messages": [{"role": "user", "content": "q"}]}
    assert (await photo_router.Filter().inlet(off, events))["features"]["web_search"] is False
    assert events.emitted == []


def _chat_with_previous_question(at):
    owui.Chats.get_chat_by_id.return_value = SimpleNamespace(chat={"history": {"messages": {
        "q1": {"role": "user", "timestamp": at},
        "a1": {"role": "assistant", "parentId": "q1"},
        "q2": {"role": "user", "parentId": "a1", "timestamp": at + 100},
    }}})
    return {"chat_id": "c1", "user_message_id": "q2"}


@pytest.fixture
def classifier(monkeypatch):
    """Replaces the Ollama call; set .answer to what the classifier says."""
    calls = SimpleNamespace(answer="WEB", prompts=[])

    class Response:
        def raise_for_status(self): pass
        async def json(self): return {"response": calls.answer}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class Session:
        def __init__(self, **kw): pass
        def post(self, url, json):
            calls.prompts.append(json["prompt"])
            return Response()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(photo_router.aiohttp, "ClientSession", Session)
    owui.Files.get_file_by_id.return_value = SimpleNamespace(filename="menu.pdf", data={"content": "Idli 60 Dosa 90"})
    return calls


async def test_a_general_question_after_an_earlier_document_keeps_web_search(events, classifier):
    metadata = _chat_with_previous_question(at=2000)
    body = {"model": "fast-ai:latest", "features": {"web_search": True},
            "files": [{"type": "file", "id": "menu", "file": {"created_at": 1500}}],
            "messages": [{"role": "user", "content": "How do I make a good idli at home?"}]}

    body = await photo_router.Filter().inlet(body, events, None, metadata)

    assert body["model"] == "fast-ai:latest" and body["features"]["web_search"] is True
    assert events.emitted == []
    assert "How do I make a good idli at home?" in classifier.prompts[0] and "Idli 60 Dosa 90" in classifier.prompts[0]


async def test_a_question_about_an_earlier_document_goes_to_the_document(events, classifier):
    classifier.answer = "DOC"
    metadata = _chat_with_previous_question(at=2000)
    body = {"model": "fast-ai:latest", "features": {"web_search": True},
            "files": [{"type": "file", "id": "menu", "file": {"created_at": 1500}}],
            "messages": [{"role": "user", "content": "What are their prices?"}]}

    body = await photo_router.Filter().inlet(body, events, None, metadata)

    assert body["model"] == "photo-ai:latest" and body["features"]["web_search"] is False


async def test_classifier_errors_and_the_valve_keep_web_search(events, classifier, monkeypatch):
    metadata = _chat_with_previous_question(at=2000)
    def body():
        return {"features": {"web_search": True}, "files": [{"type": "file", "id": "menu", "file": {"created_at": 1500}}],
                "messages": [{"role": "user", "content": "What are their prices?"}]}

    class Broken:
        def __init__(self, **kw): raise RuntimeError("ollama down")
    monkeypatch.setattr(photo_router.aiohttp, "ClientSession", Broken)
    assert (await photo_router.Filter().inlet(body(), events, None, metadata))["features"]["web_search"] is True

    f = photo_router.Filter()
    f.valves.classify_followups = False
    assert (await f.inlet(body(), events, None, metadata))["features"]["web_search"] is True


async def test_a_document_attached_since_the_previous_question_counts(events, classifier):
    metadata = _chat_with_previous_question(at=2000)
    body = {"model": "fast-ai:latest", "features": {"web_search": True},
            "files": [{"type": "file", "id": "menu", "file": {"created_at": 1500}},
                      {"type": "file", "id": "new", "file": {"created_at": 2050}}],
            "messages": [{"role": "user", "content": "What does this say?"}]}

    body = await photo_router.Filter().inlet(body, events, None, metadata)

    assert body["model"] == "photo-ai:latest" and body["features"]["web_search"] is False


async def test_the_first_question_and_unreadable_chats_count_every_document(events):
    owui.Chats.get_chat_by_id.return_value = SimpleNamespace(chat={"history": {"messages": {
        "q1": {"role": "user", "timestamp": 2000}}}})
    first = {"features": {"web_search": True}, "files": [{"type": "file", "id": "f", "file": {"created_at": 1}}],
             "messages": [{"role": "user", "content": "q"}]}
    assert (await photo_router.Filter().inlet(first, events, None, {"chat_id": "c1", "user_message_id": "q1"}))["features"]["web_search"] is False

    owui.Chats.get_chat_by_id.side_effect = RuntimeError("db down")
    broken = {"features": {"web_search": True}, "files": [{"type": "file", "id": "f", "file": {"created_at": 1}}],
              "messages": [{"role": "user", "content": "q"}]}
    assert (await photo_router.Filter().inlet(broken, events, None, {"chat_id": "c1", "user_message_id": "q9"}))["features"]["web_search"] is False


async def test_notice_when_web_search_found_nothing(events):
    await web_search_notice.Filter().request({}, {"sources": []}, {"web_search": True}, events)
    assert events.emitted[0]["data"]["error"] is True


async def test_no_notice_when_web_sources_reached_the_model(events):
    sources = [{"source": {"type": "web_search"}, "metadata": [{"source": "https://x.example"}]}]
    await web_search_notice.Filter().request({}, {"sources": sources}, {"web_search": True}, events)
    assert events.emitted == []


async def test_no_notice_when_web_search_was_off_or_only_empty_web_results(events):
    f = web_search_notice.Filter()
    await f.request({}, {"sources": []}, {"web_search": False}, events)
    assert events.emitted == []
    empty_web = [{"source": {"type": "web_search"}, "metadata": []}]
    await f.request({}, {"sources": empty_web}, {"web_search": True}, events)
    assert len(events.emitted) == 1
