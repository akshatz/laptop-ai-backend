"""
title: Chat History Trim
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Sends the model only the latest question (with its web search results), so small models don't answer an earlier question in the same chat.

Open WebUI filter Function (Admin → Functions → import this file and enable it, NOT global; then
attach it to the models that need it: Admin → Settings → Models → <model> → Filters). In this
stack it's attached to fast-ai (Llama 3.2 3B), which in multi-topic chats kept answering the
previous question (asked about TCS after Gandhi Jayanti → answered about Gandhi Jayanti) even with
the right search results in front of it.

Runs as a `request` filter: Open WebUI calls that hook at the very end of building the request
(utils/middleware.py, after the web search and after the search results are put into the latest
user message), right before the model call. So:
- web search query generation still sees the recent chat, and follow-ups like "where is he from?"
  are still resolved into a proper query (the query prompt is task.query.prompt_template);
- the answer step gets only system messages + the last `keep_previous_turns` question/answer
  pairs (default 0) + the latest user message, which already carries the search results.
Trade-off: with 0, questions that only refer back to the conversation without needing a lookup
("summarize your last answer", "make that shorter") have nothing to work from with this model.
The chat itself is unchanged — only what's sent to the model is trimmed.
"""

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        keep_previous_turns: int = Field(
            default=0,
            description="Earlier question/answer pairs to keep before the latest question (0 = latest question only).",
        )

    def __init__(self):
        self.valves = self.Valves()

    def request(self, body: dict) -> dict:
        messages = body.get("messages") or []
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
        if last_user is None:
            return body

        system = [m for m in messages[:last_user] if m.get("role") == "system"]
        earlier = [m for m in messages[:last_user] if m.get("role") in ("user", "assistant")]
        keep = max(0, int(self.valves.keep_previous_turns)) * 2
        kept_earlier = earlier[-keep:] if keep else []
        # Anything after the latest user message (normally nothing at this stage) is kept as is.
        body["messages"] = system + kept_earlier + messages[last_user:]
        return body
