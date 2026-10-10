"""
title: Photo Router
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Sends a question with a picture to photo-ai (Gemma 3 4B, reads images) instead of fast-ai, which can't read images, and skips web search for it.

Open WebUI filter Function (Admin → Functions → import this file and enable it; not Global — attach
it to fast-ai:latest under Admin → Settings → Models → fast-ai → Filters). No restart needed.

Why: fast-ai (Llama 3.2 3B) is text-only, so a pasted screenshot failed with "Multimodal data
provided, but model does not support multimodal requests" (2026-10-10). smart-ai (Gemma 3 12B)
reads images but needs ~8 GB, more than the laptop has free; photo-ai (devops/ollama/
photo-ai.Modelfile) is the 4B size of the same family.

Open WebUI 0.11.3 puts attached images into the latest user message as image_url parts before the
inlet filters run, runs web search only after them (process_chat_payload), and picks the model to
call from form_data["model"] after that (utils/chat.py generate_chat_completion). So this inlet,
when the latest user message has an image, sets body["model"] to `target_model` and, with
`skip_web_search`, turns web search off for that request (a search for a picture question finds
pages about the words, not the picture, and costs a minute). A status line above the answer says
which model read it. The answer is still labelled fast-ai in the chat and counted under fast-ai on
the KPI page, since the frontend set the label before sending.

Only the latest message counts: a follow-up without a new picture goes to fast-ai, which, with the
Chat History Trim filter, sees only that follow-up. Attach the picture again to ask more about it.
`target_model` must be a model regular users may use (it's checked for their access).
"""

import logging

from pydantic import BaseModel, Field

log = logging.getLogger("photo_router")


def _has_image(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
    return False


class Filter:
    class Valves(BaseModel):
        target_model: str = Field(default="photo-ai:latest", description="Model that answers questions with a picture")
        skip_web_search: bool = Field(default=True, description="No web search for questions with a picture")
        status: str = Field(default="Reading the picture with photo-ai…", description="Shown above the answer; empty = none")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __event_emitter__=None) -> dict:
        users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
        if not users or not _has_image(users[-1]) or body.get("model") == self.valves.target_model:
            return body
        log.warning("Photo Router: %s -> %s (picture attached)", body.get("model"), self.valves.target_model)
        body["model"] = self.valves.target_model
        if self.valves.skip_web_search and isinstance(body.get("features"), dict):
            body["features"]["web_search"] = False
        if self.valves.status and __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": self.valves.status, "done": True}})
        return body
