"""Photo Router and Web Search Notice: small filters, tested together."""

from conftest import load_function

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
