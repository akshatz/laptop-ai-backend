"""
title: Photo Router
author: akshatz
version: 1.1.0
required_open_webui_version: 0.11.3
description: Sends a question with a picture to photo-ai (Gemma 3 4B, reads images) instead of fast-ai, which can't read images, and skips web search for it. Also sends questions with an attached document to photo-ai, without web search.

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

Documents (2026-10-18): with web search always on, a question about an attached PDF also searched
the web, and the web chunks shared rag.top_k (6) with the PDF's chunks and added 20-100 s. With
`skip_web_search_for_files`, a request whose `files` (form_data["files"] at inlet time: what the
user attached to this chat, or a knowledge base picked with #) holds a document turns web search off,
goes to `target_model` (with `route_documents`; gemma3:4b quotes documents more accurately than the
3B fast-ai, and RAG's ~6 chunks fit its 4096-token context) and shows `files_status`. The model's own knowledge (merged into the same list) and folder
knowledge don't count. The frontend sends the files of the whole chat, so follow-ups in that chat
also skip the web; switch the toggle back on, or start a new chat, for a web question.
"""

import logging

from pydantic import BaseModel, Field

log = logging.getLogger("photo_router")


def _has_image(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
    return False


# model knowledge entries Open WebUI merges into `files` carry 'legacy'; folders are type 'folder'
_NOT_A_DOCUMENT = {"image", "folder", "web_search"}


def _has_document(body: dict, model: dict | None) -> bool:
    knowledge = ((model or {}).get("info") or {}).get("meta", {}).get("knowledge") or []
    model_ids = {k.get("id") for k in knowledge if isinstance(k, dict)}
    for f in body.get("files") or []:
        if not isinstance(f, dict) or f.get("legacy") or f.get("type") in _NOT_A_DOCUMENT:
            continue
        if f.get("id") and f.get("id") in model_ids:
            continue
        return True
    return False


class Filter:
    class Valves(BaseModel):
        target_model: str = Field(default="photo-ai:latest", description="Model that answers questions with a picture")
        skip_web_search: bool = Field(default=True, description="No web search for questions with a picture")
        status: str = Field(default="Reading the picture with photo-ai…", description="Shown above the answer; empty = none")
        skip_web_search_for_files: bool = Field(default=True, description="No web search when a document is attached")
        route_documents: bool = Field(default=True, description="Also send questions with a document to target_model")
        files_status: str = Field(default="Reading your document with photo-ai (web search skipped)", description="Shown above the answer; empty = none")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __event_emitter__=None, __model__=None) -> dict:
        users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
        if not users or not _has_image(users[-1]) or body.get("model") == self.valves.target_model:
            return await self._skip_web_for_document(body, __event_emitter__, __model__)
        log.warning("Photo Router: %s -> %s (picture attached)", body.get("model"), self.valves.target_model)
        body["model"] = self.valves.target_model
        if self.valves.skip_web_search and isinstance(body.get("features"), dict):
            body["features"]["web_search"] = False
        if self.valves.status and __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": self.valves.status, "done": True}})
        return body

    async def _skip_web_for_document(self, body: dict, emitter, model) -> dict:
        features = body.get("features")
        if not (self.valves.skip_web_search_for_files and isinstance(features, dict) and features.get("web_search")):
            return body
        if not _has_document(body, model):
            return body
        features["web_search"] = False
        if self.valves.route_documents and body.get("model") != self.valves.target_model:
            log.warning("Photo Router: %s -> %s (document attached)", body.get("model"), self.valves.target_model)
            body["model"] = self.valves.target_model
        if self.valves.files_status and emitter:
            await emitter({"type": "status", "data": {"description": self.valves.files_status, "done": True}})
        return body
