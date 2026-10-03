"""
title: Query Log
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Logs each chat request's web search queries, source sites and chunk count to query.log, which openwebui-audit-shipper sends to OpenObserve stream openwebui_queries.

Open WebUI filter Function (Admin → Functions → import this file, enable it and switch on Global,
so it runs for every model). No restart needed.

Why: Open WebUI logs neither the web search queries it generates nor where the answer's sources
came from (its OTel logs stop at WARNING here, and the audit log has no request bodies), so a bad
answer couldn't be traced in O2 to "it searched for the wrong thing" or "all sources came from one
site". This writes one JSON line per chat request to <DATA_DIR>/query.log (the open-webui-data
volume), tailed by devops/otel-collector/openwebui-audit.yaml.

Deliberately not logged: the person's own message and the answer (family members' wording stays in
Open WebUI only). The search queries are the model's rewording of the question, so they still say
what was asked about.

Runs as a `request` filter, which Open WebUI calls at the end of building the request
(utils/middleware.py), after web search and knowledge retrieval have put their sources in
__metadata__["sources"]. A failing `request` hook would fail the chat, so every error here is
swallowed. API calls without a chat (e.g. evals/run.py) are logged with via=api.
"""

import json
import logging
import os
import time
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from open_webui.env import DATA_DIR

log = logging.getLogger("query_log")

MAX_BYTES = 10 * 1024 * 1024  # then query.log → query.log.1 (the shipper follows by fingerprint)


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(default=True, description="Write query.log entries.")

    def __init__(self):
        self.valves = self.Valves()
        self.path = os.path.join(str(DATA_DIR), "query.log")

    def _entry(self, user: dict, metadata: dict, model: dict | None) -> dict:
        queries, sites, knowledge, chunks = [], [], [], 0
        for src in metadata.get("sources") or []:
            info = src.get("source") or {}
            if info.get("type") == "web_search":
                # Open WebUI names the web-search source after its queries, comma-joined.
                for q in (info.get("name") or "").split(","):
                    if q.strip() and q.strip() not in queries:
                        queries.append(q.strip())
            elif info.get("name") and info["name"] not in knowledge:
                knowledge.append(info["name"])
            for meta in src.get("metadata") or []:
                chunks += 1
                url = str(meta.get("source") or "")
                host = urlparse(url).hostname if url.startswith("http") else None
                if host and host.removeprefix("www.") not in sites:
                    sites.append(host.removeprefix("www."))
        chat_id = metadata.get("chat_id") or ""
        return {
            "timestamp": int(time.time()),
            "user_name": user.get("name") or "",
            "user_email": user.get("email") or "",
            "model": (model or {}).get("id") or "",
            "chat_id": chat_id,
            "via": "chat" if chat_id else "api",
            # Strings rather than lists, so they're plain searchable columns in O2.
            "search_queries": " | ".join(queries),
            "sites": ", ".join(sites),
            "sites_count": len(sites),
            "chunks": chunks,
            "knowledge": ", ".join(knowledge),
        }

    def _write(self, entry: dict) -> None:
        try:
            if os.path.getsize(self.path) > MAX_BYTES:
                os.replace(self.path, self.path + ".1")
        except FileNotFoundError:
            pass
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def request(self, body: dict, __user__: dict = None, __metadata__: dict = None, __model__: dict = None) -> dict:
        if self.valves.enabled:
            try:
                self._write(self._entry(__user__ or {}, __metadata__ or {}, __model__))
            except Exception:
                log.exception("query_log: could not write entry")
        return body
