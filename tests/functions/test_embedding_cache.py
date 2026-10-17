from unittest.mock import AsyncMock

import pytest

from conftest import fresh_patch_targets, load_function


@pytest.fixture
def cache():
    """Embedding Cache installed on a fresh routers.retrieval, with an in-memory table."""
    targets = fresh_patch_targets()
    module = load_function("embedding_cache")
    table = {}

    async def lookup(keys):
        return {k: table[k] for k in keys if k in table}

    async def store(model, new):
        table.update(new)

    module._lookup = AsyncMock(side_effect=lookup)
    module._store = AsyncMock(side_effect=store)
    module._cleanup = AsyncMock()
    owner = module.Event()
    embed = targets.retrieval.get_embedding_function("ollama", "embeddinggemma")
    return module, owner, embed, targets.retrieval.embed_calls, table


def test_key_depends_on_engine_model_prefix_and_text():
    module = load_function("embedding_cache")
    keys = {
        module._key("ollama", "embeddinggemma", None, "text"),
        module._key("openai", "embeddinggemma", None, "text"),
        module._key("ollama", "other", None, "text"),
        module._key("ollama", "embeddinggemma", "title: none | text: ", "text"),
        module._key("ollama", "embeddinggemma", None, "other text"),
    }
    assert len(keys) == 5


def test_vectors_round_trip_as_float32():
    module = load_function("embedding_cache")
    assert module._unpack(module._pack([0.5, -1.25, 3.0])) == [0.5, -1.25, 3.0]


async def test_second_time_only_new_chunks_are_embedded(cache):
    module, owner, embed, calls, table = cache

    first = await embed(["a", "bb", "ccc"])
    second = await embed(["bb", "dddd", "a"])

    assert calls == [["a", "bb", "ccc"], ["dddd"]]
    assert second == [[2.0, 0.5], [4.0, 0.5], [1.0, 0.5]]  # in the order asked for
    assert first[0] == second[2]
    assert len(table) == 4


async def test_a_repeated_chunk_is_embedded_once(cache):
    module, owner, embed, calls, table = cache
    assert len(await embed(["same", "same"])) == 2
    assert calls == [["same"]]


async def test_queries_and_disabled_cache_bypass_it(cache):
    module, owner, embed, calls, table = cache
    await embed("a single query")
    owner.valves.enabled = False
    await embed(["a", "b"])
    await embed(["a", "b"])
    assert calls == ["a single query", ["a", "b"], ["a", "b"]]
    module._lookup.assert_not_awaited()


async def test_a_cache_failure_falls_back_to_embedding_everything(cache):
    module, owner, embed, calls, table = cache
    module._lookup.side_effect = RuntimeError("db down")
    assert len(await embed(["a", "b"])) == 2

    module._lookup.side_effect = None
    module._lookup.return_value = {}
    module._store.side_effect = RuntimeError("db down")
    assert len(await embed(["c"])) == 1
    assert calls == [["a", "b"], ["c"]]


def test_installs_once():
    targets = fresh_patch_targets()
    module = load_function("embedding_cache")
    module.Event()
    patched = targets.retrieval.get_embedding_function
    load_function("embedding_cache").Event()  # re-imported Function
    assert targets.retrieval.get_embedding_function is patched
