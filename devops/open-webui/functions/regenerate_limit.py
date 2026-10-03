"""
title: Regenerate Limit
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Lets regular users regenerate an answer at most 3 times; admins are exempt.

Open WebUI filter Function (Admin → Functions → import this file, enable it and switch on Global,
so it covers every model). Open WebUI's own permission (chat.regenerate_response) is all or nothing.

Regenerate adds another answer under the same question. Open WebUI 0.11 saves the question and a
placeholder for the new answer in the chat before running filters, so the `inlet` hook loads the
chat and counts the question's other answers from the same model. More than `max_regenerations` of
them (the first answer plus that many regenerations) → the request is refused, and Open WebUI shows
the message in place of the new answer, without calling the model. Asking again in a new message,
or editing the question, starts a new count. Temporary chats aren't saved, so they aren't limited.
Uses internals (Chats model, chat history layout): re-test when bumping the image.
"""

from pydantic import BaseModel, Field

from open_webui.models.chats import Chats


class Filter:
    class Valves(BaseModel):
        max_regenerations: int = Field(
            default=3,
            description="Regenerations allowed per question (per model) for regular users.",
        )
        exempt_admins: bool = Field(default=True, description="Admins can regenerate without limit.")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict | None = None, __metadata__: dict | None = None) -> dict:
        metadata = __metadata__ or {}
        if self.valves.exempt_admins and (__user__ or {}).get("role") == "admin":
            return body
        chat_id = metadata.get("chat_id")
        answer_id = metadata.get("message_id")
        if not chat_id or not answer_id:
            return body

        chat = await Chats.get_chat_by_id(chat_id)
        if not chat:  # temporary chat, channel, or not saved
            return body
        messages = ((chat.chat or {}).get("history") or {}).get("messages") or {}
        answer = messages.get(answer_id) or {}
        question_id = metadata.get("user_message_id") or answer.get("parentId")
        if not question_id:
            return body
        model = answer.get("model") or body.get("model")

        other_answers = [
            m
            for message_id, m in messages.items()
            if message_id != answer_id
            and m.get("role") == "assistant"
            and m.get("parentId") == question_id
            and m.get("model") == model
        ]
        if len(other_answers) > self.valves.max_regenerations:
            raise Exception(
                f"This answer can be regenerated up to {self.valves.max_regenerations} times. "
                "To try again, ask the question in a new message."
            )
        return body
