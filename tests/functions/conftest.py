"""Test setup for the Open WebUI Functions in devops/open-webui/functions/.

The Functions import a few Open WebUI internals at module level. Open WebUI isn't installed here,
so small stand-ins go into sys.modules first; tests set what they need on them (e.g. the chat that
Chats.get_chat_by_id returns). `load_function(name)` imports a Function file fresh.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

FUNCTIONS_DIR = Path(__file__).resolve().parents[2] / "devops" / "open-webui" / "functions"


class FakeChats:
    chats: dict = {}

    @classmethod
    async def get_chat_by_id(cls, chat_id):
        return cls.chats.get(chat_id)


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


async def _no_user(*args, **kwargs):
    return None


def _no_db(*args, **kwargs):
    raise RuntimeError("database access isn't available in these tests")


_module("open_webui")
_module("open_webui.env", DATA_DIR="/nonexistent")
_module("open_webui.models")
_module("open_webui.models.chats", Chats=FakeChats)
_module("open_webui.internal")
_module("open_webui.internal.db", get_async_db_context=_no_db)
_module("open_webui.utils")
_module("open_webui.utils.auth", get_optional_verified_user_from_request=_no_user)


def load_function(name: str):
    spec = importlib.util.spec_from_file_location(f"owui_function_{name}", FUNCTIONS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def chats():
    """The chats Chats.get_chat_by_id returns, by id."""
    FakeChats.chats = {}
    return FakeChats.chats


@pytest.fixture
def events():
    """An __event_emitter__ that records what a Function emits."""
    emitted = []

    async def emit(event):
        emitted.append(event)

    emit.emitted = emitted
    return emit
