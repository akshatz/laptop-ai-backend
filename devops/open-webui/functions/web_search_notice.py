"""
title: Web Search Notice
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Tells the person, above the answer, when web search was on but no web sources reached the model, so they know the answer comes from the model's own (possibly outdated) knowledge.

Open WebUI filter Function (Admin → Functions → import this file, enable it and switch on Global,
so it runs for every model). No restart needed.

Why: the KPI dashboard's "No web" share counts answers written without web sources. Open WebUI
says "No search results found" only when the search returned nothing at all; when results came
back but none made it into the prompt (every page filtered, blocked or empty) the answer just
appears without sources. Asked for by a family user (2026-10-04): the system should say so.

Runs as a `request` filter, which Open WebUI calls after web search and retrieval have put their
sources in __metadata__["sources"] (same hook as Query Log). Only when the request had web search
on (__features__["web_search"]), so chats with search switched off get no notice. A failing
`request` hook would fail the chat, so every error is swallowed.
"""

import logging

from pydantic import BaseModel, Field

log = logging.getLogger("web_search_notice")


class Filter:
    class Valves(BaseModel):
        message: str = Field(
            default="No web results found. This answer is from the model's own knowledge and may be outdated or wrong.",
            description="Shown above the answer when web search was on but found no usable sources.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def request(self, body: dict, __metadata__: dict = None, __features__: dict = None,
                      __event_emitter__=None) -> dict:
        try:
            if (__features__ or {}).get("web_search") and __event_emitter__:
                sources = (__metadata__ or {}).get("sources") or []
                if not any((s.get("source") or {}).get("type") == "web_search" and s.get("metadata") for s in sources):
                    await __event_emitter__({"type": "status", "data": {
                        "action": "web_search", "description": self.valves.message, "done": True, "error": True}})
        except Exception:
            log.exception("web_search_notice: could not check sources")
        return body
