"""Test setup for the Open WebUI Functions in devops/open-webui/functions/.

The Functions import Open WebUI internals at module level. Open WebUI isn't installed here, so
stand-ins go into sys.modules first:
- data-access namespaces (Users, Chats, Auths, Functions, Groups, …) whose methods are AsyncMocks,
  reset before every test; a test sets what it needs, e.g. `owui.Users.get_user_by_id.return_value`;
- classes that some Functions patch in place (ChatTable, UsersTable, AuditLoggingMiddleware) and the
  routers.retrieval module (Embedding Cache patches get_embedding_function): tests that install those
  patches get fresh copies from `fresh_patch_targets()`, so a patch never carries over;
- get_async_db_context, which refuses: tests replace a Function's own database helpers instead.

`load_function(name)` imports a Function file fresh, so module-level state starts clean.
"""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

FUNCTIONS_DIR = Path(__file__).resolve().parents[2] / "devops" / "open-webui" / "functions"


def _module(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _no_db(*args, **kwargs):
    raise RuntimeError("database access isn't available in these tests; replace the Function's DB helper")


async def _no_user(*args, **kwargs):
    return None


# Data-access namespaces: one object each, shared with every loaded Function; reset per test.
owui = SimpleNamespace(
    Users=SimpleNamespace(), Chats=SimpleNamespace(), Auths=SimpleNamespace(), Functions=SimpleNamespace(),
    Groups=SimpleNamespace(), Knowledges=SimpleNamespace(), Storage=SimpleNamespace(), Files=SimpleNamespace(),
    ASYNC_VECTOR_DB_CLIENT=SimpleNamespace(),
)
_ASYNC_METHODS = {
    "Users": ["get_user_by_id", "get_user_by_email", "update_user_role_by_id"],
    "Chats": ["get_chat_by_id"],
    "Auths": ["authenticate_user", "update_user_password_by_id"],
    "Functions": ["get_function_by_id", "get_function_valves_by_id"],
    "Groups": ["get_groups_by_member_id"],
    "Knowledges": [],
    "Storage": [],
    "Files": ["get_file_by_id"],
    "ASYNC_VECTOR_DB_CLIENT": ["delete_collection"],
}


def reset_owui():
    for name, methods in _ASYNC_METHODS.items():
        namespace = getattr(owui, name)
        namespace.__dict__.clear()
        for method in methods:
            setattr(namespace, method, AsyncMock(return_value=None))


reset_owui()


class Auth:
    """Stand-in for open_webui.models.auths.Auth (only used as a session.get() key)."""


EVENTS = SimpleNamespace(AUTH_PASSWORD_CHANGED="auth.password_changed")


def fresh_patch_targets():
    """New ChatTable / UsersTable / AuditLoggingMiddleware / routers.retrieval stand-ins."""

    class ChatTable:
        calls: list = []

        async def delete_chat_by_id(self, id):
            ChatTable.calls.append(("delete_chat_by_id", id))
            return True

        async def delete_chat_by_id_and_user_id(self, id, user_id):
            ChatTable.calls.append(("delete_chat_by_id_and_user_id", id, user_id))
            return True

        async def delete_chats_by_user_id(self, user_id):
            ChatTable.calls.append(("delete_chats_by_user_id", user_id))
            return True

        async def delete_chats_by_user_id_and_folder_id(self, user_id, folder_id):
            ChatTable.calls.append(("delete_chats_by_user_id_and_folder_id", user_id, folder_id))
            return True

    class UsersTable:
        async def delete_user_by_id(self, id):
            # Open WebUI deletes the user's chats as part of deleting the user.
            return await ChatTable().delete_chats_by_user_id(id)

    class AuditLoggingMiddleware:
        logged: list = []

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)
            request = SimpleNamespace(scope=scope)
            context = SimpleNamespace(metadata={"response_status_code": scope.get("status_from_open_webui")})
            await self._log_audit_entry(request, context)

        async def _log_audit_entry(self, request, context):
            AuditLoggingMiddleware.logged.append(dict(context.metadata))

    ChatTable.calls = []
    AuditLoggingMiddleware.logged = []
    retrieval = types.ModuleType("open_webui.routers.retrieval")
    retrieval.embed_calls = []

    def get_embedding_function(engine, model, *args, **kwargs):
        async def embed(query, prefix=None, user=None):
            retrieval.embed_calls.append(query)
            texts = query if isinstance(query, list) else [query]
            vectors = [[float(len(t)), 0.5] for t in texts]
            return vectors if isinstance(query, list) else vectors[0]

        return embed

    retrieval.get_embedding_function = get_embedding_function

    _module("open_webui.models.chats", ChatTable=ChatTable)
    _module("open_webui.models.users", UsersTable=UsersTable)
    _module("open_webui.utils.audit", AuditLoggingMiddleware=AuditLoggingMiddleware)
    sys.modules["open_webui.routers.retrieval"] = retrieval
    _module("open_webui.routers", retrieval=retrieval)
    return SimpleNamespace(ChatTable=ChatTable, UsersTable=UsersTable,
                           AuditLoggingMiddleware=AuditLoggingMiddleware, retrieval=retrieval)


for package in ("open_webui", "open_webui.models", "open_webui.internal", "open_webui.utils",
                "open_webui.retrieval", "open_webui.retrieval.vector", "open_webui.storage", "open_webui.routers"):
    _module(package)
_module("open_webui.env", DATA_DIR="/nonexistent")
_module("open_webui.internal.db", get_async_db_context=_no_db)
_module("open_webui.models.chats", Chats=owui.Chats)
_module("open_webui.models.users", Users=owui.Users)
_module("open_webui.models.auths", Auth=Auth, Auths=owui.Auths)
_module("open_webui.models.functions", Functions=owui.Functions)
_module("open_webui.models.groups", Groups=owui.Groups)
_module("open_webui.models.knowledge", Knowledges=owui.Knowledges)
_module("open_webui.models.files", Files=owui.Files)
_module("open_webui.retrieval.vector.async_client", ASYNC_VECTOR_DB_CLIENT=owui.ASYNC_VECTOR_DB_CLIENT)
_module("open_webui.storage.provider", Storage=owui.Storage)
_module("open_webui.events", EVENTS=EVENTS, publish_event=AsyncMock())
_module(
    "open_webui.utils.auth",
    get_optional_verified_user_from_request=_no_user,
    get_admin_user=_no_user,
    decode_token=lambda token: None,
    verify_password=lambda plain, hashed: plain == hashed,
    get_password_hash=AsyncMock(side_effect=lambda pw: f"hashed:{pw}"),
    revoke_user_tokens=AsyncMock(),
    validate_password=lambda pw: True,
)
fresh_patch_targets()


def load_function(name: str):
    spec = importlib.util.spec_from_file_location(f"owui_function_{name}", FUNCTIONS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_owui():
    reset_owui()
    yield


@pytest.fixture
def chats():
    """The chats Chats.get_chat_by_id returns, by id."""
    stored = {}

    async def get_chat_by_id(chat_id):
        return stored.get(chat_id)

    owui.Chats.get_chat_by_id = AsyncMock(side_effect=get_chat_by_id)
    return stored


@pytest.fixture
def events():
    """An __event_emitter__ that records what a Function emits."""
    emitted = []

    async def emit(event):
        emitted.append(event)

    emit.emitted = emitted
    return emit


@pytest.fixture
def secret_key(monkeypatch):
    """WEBUI_SECRET_KEY, which the token-signing Functions read."""
    monkeypatch.setenv("WEBUI_SECRET_KEY", "fake-signing-key-for-tests")
    return "fake-signing-key-for-tests"
