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
