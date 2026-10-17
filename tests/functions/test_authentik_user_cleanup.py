import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import load_function, owui

cleanup_module = load_function("authentik_user_cleanup")

USERS = [
    {"pk": 1, "uid": "sub-asha", "email": "asha@example.com", "username": "asha", "type": "internal"},
    {"pk": 2, "uid": "sub-ravi", "email": "Ravi@example.com", "username": "ravi", "type": "internal"},
    {"pk": 3, "uid": "sub-twin1", "email": "twin@example.com", "username": "twin1", "type": "internal"},
    {"pk": 4, "uid": "sub-twin2", "email": "twin@example.com", "username": "twin2", "type": "internal"},
    {"pk": 5, "uid": "sub-admin", "email": "admin@example.com", "username": "akadmin", "type": "internal",
     "is_superuser": True},
]


class FakeAuthentik:
    """Enough of aiohttp.ClientSession for the users API: two pages, filter by email, delete."""

    def __init__(self):
        self.deleted = []

    def get(self, url, params):
        users = [u for u in USERS if "email" not in params or u["email"].lower() == params["email"]]
        page = params["page"]
        data = {"results": users[(page - 1) * 3: page * 3], "pagination": {"next": page + 1 if page * 3 < len(users) else 0}}
        return Response(data)

    def delete(self, url):
        self.deleted.append(url)
        return Response({})


class Response:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


@pytest.fixture
def cleanup():
    return cleanup_module.Event()


async def test_finds_by_sub_across_pages(cleanup):
    assert (await cleanup._find(FakeAuthentik(), "sub-admin", None))["pk"] == 5


async def test_finds_by_email_only_when_exactly_one_matches(cleanup):
    session = FakeAuthentik()
    assert (await cleanup._find(session, None, "ravi@example.com"))["pk"] == 2
    assert await cleanup._find(session, None, "twin@example.com") is None
    cleanup.valves.match_by_email = False
    assert await cleanup._find(session, None, "ravi@example.com") is None


async def test_never_deletes_superusers(cleanup, monkeypatch):
    session = FakeAuthentik()
    monkeypatch.setenv("AUTHENTIK_API_TOKEN", "t")
    monkeypatch.setattr(cleanup_module.aiohttp, "ClientSession", lambda **kw: AsyncContext(session))

    await cleanup._delete_in_authentik("sub-admin", "admin@example.com")
    assert session.deleted == []
    await cleanup._delete_in_authentik("sub-asha", "asha@example.com")
    assert session.deleted == ["http://authentik-server:9000/api/v3/core/users/1/"]


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


async def test_no_api_token_skips_authentik(cleanup, monkeypatch):
    monkeypatch.delenv("AUTHENTIK_API_TOKEN", raising=False)
    monkeypatch.setattr(cleanup_module.aiohttp, "ClientSession", lambda **kw: pytest.fail("called authentik"))
    await cleanup._delete_in_authentik("sub-asha", "asha@example.com")


class DeleteApp:
    def __init__(self, status):
        async def original(scope, receive, send):
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b"true"})

        self.route = SimpleNamespace(path=cleanup_module.DELETE_PATH, methods={"DELETE"}, app=original)
        self.router = SimpleNamespace(routes=[self.route])


async def delete_user(app):
    sent = []

    async def send(message):
        sent.append(message)

    await app.route.app({"type": "http", "path_params": {"user_id": "u1"}, "headers": []}, None, send)
    for task in list(cleanup_module._background_tasks):
        await task
    return sent


@pytest.fixture
def wrapped(cleanup):
    owui.Users.get_user_by_id.return_value = SimpleNamespace(
        id="u1", email="Asha@example.com", oauth={"oidc": {"sub": "sub-asha"}}
    )
    cleanup._archive_chats = AsyncMock(return_value=2)
    cleanup._unarchive = AsyncMock()
    cleanup._actor_email = AsyncMock(return_value="admin@example.com")
    cleanup._delete_in_authentik = AsyncMock()
    cleanup._purge_leftovers = AsyncMock()
    return cleanup


async def test_delete_archives_then_cleans_up_authentik_and_leftovers(wrapped):
    app = DeleteApp(200)
    wrapped._wrap_delete(app)

    sent = await delete_user(app)

    assert sent[0]["status"] == 200
    wrapped._archive_chats.assert_awaited_once()
    wrapped._delete_in_authentik.assert_awaited_once_with("sub-asha", "asha@example.com")
    wrapped._purge_leftovers.assert_awaited_once_with("u1")
    wrapped._unarchive.assert_not_awaited()


async def test_a_failed_archive_stops_the_delete(wrapped):
    app = DeleteApp(200)
    wrapped._wrap_delete(app)
    wrapped._archive_chats.side_effect = RuntimeError("db down")

    sent = await delete_user(app)

    assert sent[0]["status"] == 500 and "not deleted" in json.loads(sent[1]["body"])["detail"]
    wrapped._delete_in_authentik.assert_not_awaited()


async def test_a_failed_delete_removes_the_archive_copies_again(wrapped):
    app = DeleteApp(403)
    wrapped._wrap_delete(app)

    await delete_user(app)

    wrapped._unarchive.assert_awaited_once()
    wrapped._delete_in_authentik.assert_not_awaited()


async def test_archive_off_skips_archiving(wrapped):
    app = DeleteApp(200)
    wrapped.valves.archive_chats = False
    wrapped._wrap_delete(app)
    await delete_user(app)
    wrapped._archive_chats.assert_not_awaited()
    wrapped._delete_in_authentik.assert_awaited_once()


def test_transcript_prefers_the_chat_json_then_the_message_rows():
    transcript = cleanup_module._transcript
    chat = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": ["part"]}]}
    assert transcript(chat, None) == [("user", "hi"), ("assistant", '["part"]')]
    assert transcript({}, [{"role": "user", "content": "from rows"}]) == [("user", "from rows")]
    assert transcript(None, None) == []


def test_restore_button_only_for_chats_deleted_by_their_owner():
    assert "restore(\"c1\")" in cleanup_module._restore_button({"reason": "chat_deleted", "chat_id": "c1"})
    assert cleanup_module._restore_button({"reason": "account_deleted", "chat_id": "c1"}) == ""
