"""
title: Photo Router
author: akshatz
version: 1.3.0
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
knowledge don't count. Only a newly attached document counts: Open WebUI keeps a chat's
attachments and sends them with every later question, so a menu PDF attached earlier made "How do I
make a good idli at home?" skip the web (2026-10-18). A file counts as new when it was uploaded after
the chat's previous question (file created_at vs. that message's timestamp, found from
__metadata__["user_message_id"] via Chats); #-picked knowledge bases have no upload time and always
count, and if the chat can't be read (temporary chats) every document counts. For a document attached
earlier, the query decides (Valve `classify_followups`): a short yes/no prompt to `classifier_model`
(fast-ai, through Ollama's /api/generate, temperature 0, a few tokens, ~2-5 s on CPU) with the
question and each document's name and opening text (Open WebUI's extracted `file.data.content`)
answers DOC or WEB (2026-10-18, live fast-ai on a restaurant menu and a tax certificate: 17/20
right, ~0.6 s once loaded; the misses were general questions on the document's topic, e.g. "income
tax slab for 2026", judged DOC). DOC → as above; WEB, a timeout or any error → web search stays on (the document
is still retrieved), which is how such follow-ups behaved before this Function handled documents.
"""

import logging

import aiohttp

from open_webui.models.chats import Chats
from open_webui.models.files import Files

from pydantic import BaseModel, Field

log = logging.getLogger("photo_router")


def _has_image(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
    return False


# model knowledge entries Open WebUI merges into `files` carry 'legacy'; folders are type 'folder'
_NOT_A_DOCUMENT = {"image", "folder", "web_search"}


def _uploaded_at(f: dict):
    return f.get("created_at") or (f.get("file") or {}).get("created_at")


async def _previous_question_at(metadata: dict | None):
    """Timestamp of the question before the current one in this chat, or None."""
    metadata = metadata or {}
    chat_id, question_id = metadata.get("chat_id"), metadata.get("user_message_id")
    if not chat_id or not question_id or chat_id.startswith("local:"):
        return None
    chat = await Chats.get_chat_by_id(chat_id)
    messages = ((getattr(chat, "chat", None) or {}).get("history") or {}).get("messages") or {}
    answer = messages.get((messages.get(question_id) or {}).get("parentId") or "")
    previous = messages.get((answer or {}).get("parentId") or "")
    return (previous or {}).get("timestamp")


def _documents(body: dict, model: dict | None) -> list:
    knowledge = ((model or {}).get("info") or {}).get("meta", {}).get("knowledge") or []
    model_ids = {k.get("id") for k in knowledge if isinstance(k, dict)}
    docs = []
    for f in body.get("files") or []:
        if not isinstance(f, dict) or f.get("legacy") or f.get("type") in _NOT_A_DOCUMENT:
            continue
        if f.get("id") and f.get("id") in model_ids:
            continue
        docs.append(f)
    return docs


def _question(body: dict) -> str:
    users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
    content = users[-1].get("content") if users else ""
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return (content or "").strip()


CLASSIFY_PROMPT = """Earlier in this chat the user attached a document:
{documents}

Decide where the answer to the user's new message comes from.
DOC = the message asks about something IN that document: what it says, its items, amounts, prices, names, dates, or a summary, table or list of it. Words like "it", "this", "they", "their", "listed", "the file" usually point to the document.
WEB = anything else: how-to questions, recipes, news, weather, prices of things in the market, sports, general knowledge, even when the topic is related to the document.

Examples:
"What is the total amount?" -> DOC
"Summarize it" -> DOC
"List the items with their prices" -> DOC
"What date is on it?" -> DOC
"How do I make dosa at home?" -> WEB
"What is the weather today?" -> WEB
"What is the latest news?" -> WEB
"What does GST mean?" -> WEB

New message: "{question}"
Answer with one word, DOC or WEB:"""


class Filter:
    class Valves(BaseModel):
        target_model: str = Field(default="photo-ai:latest", description="Model that answers questions with a picture")
        skip_web_search: bool = Field(default=True, description="No web search for questions with a picture")
        status: str = Field(default="Reading the picture with photo-ai…", description="Shown above the answer; empty = none")
        skip_web_search_for_files: bool = Field(default=True, description="No web search when a document is attached")
        route_documents: bool = Field(default=True, description="Also send questions with a document to target_model")
        files_status: str = Field(default="Reading your document with photo-ai (web search skipped)", description="Shown above the answer; empty = none")
        classify_followups: bool = Field(default=True, description="For a document attached earlier in the chat, ask classifier_model whether the question is about it")
        classifier_model: str = Field(default="fast-ai:latest", description="Ollama model that answers DOC or WEB")
        ollama_url: str = Field(default="http://ollama:11434", description="Ollama base URL")
        classifier_timeout: float = Field(default=20.0, description="Seconds; on timeout web search stays on")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __event_emitter__=None, __model__=None, __metadata__=None) -> dict:
        users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
        if not users or not _has_image(users[-1]) or body.get("model") == self.valves.target_model:
            return await self._skip_web_for_document(body, __event_emitter__, __model__, __metadata__)
        log.warning("Photo Router: %s -> %s (picture attached)", body.get("model"), self.valves.target_model)
        body["model"] = self.valves.target_model
        if self.valves.skip_web_search and isinstance(body.get("features"), dict):
            body["features"]["web_search"] = False
        if self.valves.status and __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": self.valves.status, "done": True}})
        return body

    async def _skip_web_for_document(self, body: dict, emitter, model, metadata=None) -> dict:
        features = body.get("features")
        if not (self.valves.skip_web_search_for_files and isinstance(features, dict) and features.get("web_search")):
            return body
        docs = _documents(body, model)
        if not docs:
            return body
        try:
            since = await _previous_question_at(metadata)
        except Exception:
            log.exception("Photo Router: couldn't read the chat; counting every document")
            since = None
        new = [d for d in docs if not since or not _uploaded_at(d) or _uploaded_at(d) >= since]
        if not new and not await self._about_documents(_question(body), docs):
            return body
        features["web_search"] = False
        if self.valves.route_documents and body.get("model") != self.valves.target_model:
            log.warning("Photo Router: %s -> %s (document attached)", body.get("model"), self.valves.target_model)
            body["model"] = self.valves.target_model
        if self.valves.files_status and emitter:
            await emitter({"type": "status", "data": {"description": self.valves.files_status, "done": True}})
        return body

    async def _about_documents(self, question: str, docs: list) -> bool:
        """Ask classifier_model whether the question is about documents attached earlier. False on any doubt."""
        if not self.valves.classify_followups or not question:
            return False
        try:
            parts = []
            for d in docs[:3]:
                fid = d.get("id") or (d.get("file") or {}).get("id")
                file = await Files.get_file_by_id(fid) if fid else None
                name = d.get("name") or (file.filename if file else "") or "document"
                text = ((getattr(file, "data", None) or {}).get("content") or "")[:600] if file else ""
                parts.append(f"- {name}: {' '.join(text.split())}")
            prompt = CLASSIFY_PROMPT.format(documents="\n".join(parts), question=question[:500])
            payload = {"model": self.valves.classifier_model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": 3}}
            timeout = aiohttp.ClientTimeout(total=self.valves.classifier_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.valves.ollama_url.rstrip('/')}/api/generate", json=payload) as r:
                    r.raise_for_status()
                    answer = ((await r.json()).get("response") or "").strip().upper()
            about = answer.startswith("DOC")
            log.warning("Photo Router: follow-up classified %s (%r)", "DOC" if about else "WEB", answer[:10])
            return about
        except Exception:
            log.exception("Photo Router: couldn't classify the follow-up; keeping web search")
            return False
