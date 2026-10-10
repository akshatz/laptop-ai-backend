"""
title: Embedding Cache
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Reuses embeddings of document chunks already embedded (web pages fetched again), so they aren't sent to Ollama twice.

Open WebUI event Function (Admin → Functions → import this file, then enable it, then restart
open-webui once so it's active from the first request).

Embedding the fetched web pages is the slowest step of a web-search answer (~0.28 s per chunk,
18–100 s per question), and about 25% of fetched pages had been fetched before, nearly always the
same day (news pages that several questions land on). Open WebUI embeds them again every time,
because each search gets its own collection.

save_docs_to_vector_db (open_webui.routers.retrieval), which embeds web pages and uploaded files,
builds its embedding function with that module's get_embedding_function on every call. This
replaces that module attribute with a wrapper whose embedding function, for a list of texts, looks
each one up in fn_embedding_cache (key: sha256 of engine, model, prefix and text), sends only the
misses to the real function, stores their vectors and returns all vectors in the original order.
Single texts (search queries, memories) go straight through. A changed page, model or
RAG_EMBEDDING_CONTENT_PREFIX gives different keys, so it's a miss, never a stale vector. Any cache
error is logged and the texts are embedded normally.

Each list call logs "Embedding Cache: <hits>/<chunks> chunks from cache" at WARNING (visible at
GLOBAL_LOG_LEVEL=WARNING, so it reaches O2's openwebui_backend stream). Rows not used for
max_age_days are deleted, checked at most once an hour.

The original is kept on the module (__embedding_cache_original__), so re-importing doesn't wrap
twice; the current Valves are read through the module too, so the `enabled` Valve works without a
restart. Disabling the Function doesn't unpatch; restart open-webui after. It touches Open WebUI
internals: re-test (ask the same web question twice, see hits in the log) when bumping the pinned
image. The table comes from devops/open-webui/migrations/.
"""

import functools
import hashlib
import logging
import time
from array import array

from pydantic import BaseModel, Field
from sqlalchemy import text

import open_webui.routers.retrieval as retrieval
from open_webui.internal.db import get_async_db_context

log = logging.getLogger("embedding_cache")

CLEANUP_EVERY = 3600  # seconds between deletions of unused rows


def _key(engine: str, model: str, prefix, chunk: str) -> str:
    return hashlib.sha256("\0".join((engine or "", model or "", prefix or "", chunk)).encode()).hexdigest()


def _pack(vector) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list:
    vector = array("f")
    vector.frombytes(blob)
    return vector.tolist()


async def _lookup(keys: list) -> dict:
    async with get_async_db_context() as session:
        rows = await session.execute(
            text("SELECT key, dims, vector FROM fn_embedding_cache WHERE key = ANY(:keys)"), {"keys": keys}
        )
        found = {}
        for key, dims, blob in rows:
            vector = _unpack(bytes(blob))
            if len(vector) == dims:
                found[key] = vector
        if found:
            await session.execute(
                text("UPDATE fn_embedding_cache SET last_hit = :now WHERE key = ANY(:keys)"),
                {"now": int(time.time()), "keys": list(found)},
            )
            await session.commit()
        return found


async def _store(model: str, new: dict) -> None:
    now = int(time.time())
    async with get_async_db_context() as session:
        await session.execute(
            text(
                "INSERT INTO fn_embedding_cache (key, model, dims, vector, created_at, last_hit) "
                "VALUES (:key, :model, :dims, :vector, :now, :now) ON CONFLICT (key) DO NOTHING"
            ),
            [{"key": k, "model": model, "dims": len(v), "vector": _pack(v), "now": now} for k, v in new.items()],
        )
        await session.commit()


async def _cleanup(max_age_days: int) -> None:
    last = getattr(retrieval, "__embedding_cache_cleanup__", 0)
    if time.time() - last < CLEANUP_EVERY:
        return
    retrieval.__embedding_cache_cleanup__ = time.time()
    async with get_async_db_context() as session:
        result = await session.execute(
            text("DELETE FROM fn_embedding_cache WHERE last_hit < :cutoff"),
            {"cutoff": int(time.time()) - max_age_days * 86400},
        )
        await session.commit()
    if result.rowcount:
        log.warning("Embedding Cache: deleted %d chunks unused for %d days", result.rowcount, max_age_days)


def _with_cache(embed, engine: str, model: str):
    async def cached(query, prefix=None, user=None):
        owner = getattr(retrieval, "__embedding_cache_owner__", None)
        if owner is None or not owner.valves.enabled or not isinstance(query, list) or not query:
            return await embed(query, prefix=prefix, user=user)

        keys = [_key(engine, model, prefix, chunk) for chunk in query]
        try:
            found = await _lookup(list(set(keys)))
        except Exception:
            log.exception("Embedding Cache: lookup failed; embedding everything")
            return await embed(query, prefix=prefix, user=user)

        # Each missing text once, even if it appears twice in this call.
        missing = {}
        for chunk, key in zip(query, keys):
            if key not in found:
                missing.setdefault(key, chunk)
        if missing:
            vectors = await embed(list(missing.values()), prefix=prefix, user=user)
            new = dict(zip(missing, vectors))
            try:
                await _store(model, new)
            except Exception:
                log.exception("Embedding Cache: storing %d new chunks failed", len(new))
            found.update(new)

        log.warning(
            "Embedding Cache: %d/%d chunks from cache (%d embedded)",
            len(query) - sum(1 for k in keys if k in missing), len(query), len(missing),
        )
        try:
            await _cleanup(owner.valves.max_age_days)
        except Exception:
            log.exception("Embedding Cache: cleanup failed")
        return [found[k] for k in keys]

    return cached


def _install() -> bool:
    """Patches routers.retrieval.get_embedding_function once per process; True if it did so now."""
    if hasattr(retrieval, "__embedding_cache_original__"):
        return False
    original = retrieval.get_embedding_function

    @functools.wraps(original)
    def get_embedding_function(embedding_engine, embedding_model, *args, **kwargs):
        embed = original(embedding_engine, embedding_model, *args, **kwargs)
        return _with_cache(embed, embedding_engine, embedding_model)

    retrieval.get_embedding_function = get_embedding_function
    retrieval.__embedding_cache_original__ = original
    return True


class Event:
    class Valves(BaseModel):
        enabled: bool = Field(default=True, description="Use the cache (off: every chunk is embedded, as without this Function)")
        max_age_days: int = Field(default=30, ge=1, description="Delete cached chunks not used for this many days")

    def __init__(self):
        self.valves = self.Valves()
        # The patch reads the Valves from here, so a re-imported Function takes over without re-patching.
        retrieval.__embedding_cache_owner__ = self
        if _install():
            log.warning("Embedding Cache: document embeddings now go through fn_embedding_cache")

    async def event(self, event: dict, __app__=None):
        retrieval.__embedding_cache_owner__ = self
        _install()
