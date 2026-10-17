"""Test setup for custom-backend.

custom-backend talks to OpenBao (db.py, at import) and Milvus (chat_service.py, at import), so the
tests put stand-ins in sys.modules before importing it: a `db` module with fake secrets and a
`get_db` the tests override, and a fake `langchain_milvus.Milvus`. Everything else is the real
code, including chat_service's embeddings and text splitting, running against a real Postgres
(timestamptz behaves differently from SQLite) given by TEST_DATABASE_URL. The tests drop and
recreate the tables, so only a database whose name ends in `_test` is accepted.
"""

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[2] / "custom-backend"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    # Locally without a database: skip these instead of failing. CI always sets it.
    collect_ignore_glob = ["test_*.py"]
    print("TEST_DATABASE_URL not set: skipping custom-backend tests", file=sys.stderr)
else:
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a database ending in _test (its tables are dropped)")

    # Stand-in for db.py: no OpenBao, no engine. get_db is overridden per test.
    db_stub = types.ModuleType("db")
    db_stub.secrets = {
        "smtp_from": "backend@example.com",
        "smtp_user": "backend@example.com",
        "smtp_password": "unused",
        "milvus_root_password": "unused",
    }

    async def _unconfigured_get_db():
        raise RuntimeError("get_db must be overridden in tests")
        yield  # pragma: no cover

    db_stub.get_db = _unconfigured_get_db
    sys.modules["db"] = db_stub

    class FakeMilvus:
        """Records what chat_service sends to Milvus; tests set results or errors."""

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.reset()

        def reset(self):
            self.added, self.searches, self.results = [], [], []
            self.add_error = self.search_error = None

        async def aadd_documents(self, documents):
            if self.add_error:
                raise self.add_error
            self.added.extend(documents)

        async def asimilarity_search(self, query, k=4, expr=None):
            self.searches.append({"query": query, "k": k, "expr": expr})
            if self.search_error:
                raise self.search_error
            return self.results

    sys.modules["langchain_milvus"] = types.SimpleNamespace(Milvus=FakeMilvus)
    os.environ.setdefault("MILVUS_URI", "http://milvus.invalid:19530")

    sys.path.insert(0, str(BACKEND_DIR))
    cwd = os.getcwd()
    os.chdir(BACKEND_DIR)  # main.py mounts the relative "static" directory
    try:
        import chat_service  # noqa: E402
        import main  # noqa: E402
        import models  # noqa: E402
        from routers import auth  # noqa: E402
    finally:
        os.chdir(cwd)

    @pytest.fixture
    async def session_maker():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.drop_all)
            await conn.run_sync(models.Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    @pytest.fixture
    async def client(session_maker):
        async def get_test_db():
            async with session_maker() as session:
                yield session

        main.app.dependency_overrides[db_stub.get_db] = get_test_db
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        main.app.dependency_overrides.clear()

    @pytest.fixture(autouse=True)
    def emails(monkeypatch):
        """Every email the backend would send, instead of sending it."""
        sent = []
        monkeypatch.setattr(
            auth, "_send_email",
            lambda to_address, subject, body: sent.append({"to": to_address, "subject": subject, "body": body}),
        )
        return sent

    @pytest.fixture
    def vector_store():
        chat_service.vector_store.reset()
        return chat_service.vector_store

    class FakeChain:
        def __init__(self, reply="Test reply"):
            self.reply, self.calls, self.error = reply, [], None

        async def ainvoke(self, inputs):
            self.calls.append(inputs)
            if self.error:
                raise self.error
            return SimpleNamespace(content=self.reply)

    @pytest.fixture
    def chain(monkeypatch):
        """The LangChain chain send_message runs, without Ollama. Records the model asked for."""
        fake = FakeChain()
        fake.models = []

        def build(model):
            fake.models.append(model)
            return fake

        monkeypatch.setattr(chat_service, "build_rag_chat_chain", build)
        return fake
