import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import load_function, owui

DAY = 24 * 3600
NOW = 1_800_000_000
USER = SimpleNamespace(id="u1", name="Asha", email="asha@example.com", role="user", created_at=NOW - 400 * DAY)


@pytest.fixture
def expiry(secret_key, monkeypatch):
    module = load_function("password_expiry")
    monkeypatch.setattr(module.time, "time", lambda: NOW)
    fn = module.Event()
    fn.sent = []
    fn._send_in_background = lambda to, subject, body, what: fn.sent.append({"to": to, "subject": subject, "body": body})
    owui.Functions.get_function_by_id.return_value = SimpleNamespace(is_active=True)
    owui.Functions.get_function_valves_by_id.return_value = {}
    fn.module = module
    return fn


def test_baseline_gives_old_accounts_a_grace_period(expiry):
    # Created 400 days ago, no record: counted as if set (180 - 14) days ago, so 14 days remain.
    assert expiry._baseline(USER) == NOW - (180 - 14) * DAY
    recent = SimpleNamespace(created_at=NOW - 10 * DAY)
    assert expiry._baseline(recent) == NOW - 10 * DAY
    expiry.valves.start_from_account_creation = False
    assert expiry._baseline(USER) == NOW


async def test_admins_are_exempt(expiry):
    expiry._changed_at = AsyncMock(return_value=NOW - 500 * DAY)
    assert await expiry._seconds_left(SimpleNamespace(id="a", role="admin")) is None
    assert await expiry._seconds_left(USER) == -320 * DAY


async def test_expired_password_email_link_works_on_the_password_reset_page(expiry):
    """The two Functions must agree on the token format (salt + fingerprint); this checks they do."""
    message = await expiry._handle_expired(USER, "hash-1")

    assert "emailed you a link" in message
    token = expiry.sent[0]["body"].split("#token=")[1].split()[0]

    reset = load_function("password_reset").Event()
    reset._resettable = AsyncMock(return_value=(USER, "hash-1"))
    owui.Auths.update_user_password_by_id.return_value = True
    result = await reset._reset(SimpleNamespace(), token=token, password="New-Password-7")
    assert result == {"status": "password_updated"}


async def test_no_link_when_password_reset_is_off(expiry):
    owui.Functions.get_function_by_id.return_value = SimpleNamespace(is_active=False)
    message = await expiry._handle_expired(USER, "hash-1")
    assert "Ask your Open WebUI admin" in message and expiry.sent == []


async def test_expired_email_cooldown(expiry):
    await expiry._handle_expired(USER, "hash-1")
    await expiry._handle_expired(USER, "hash-1")
    assert len(expiry.sent) == 1


async def test_expired_signin_needs_the_correct_password(expiry, monkeypatch):
    owui.Users.get_user_by_email.return_value = USER
    expiry._seconds_left = AsyncMock(return_value=-1)

    @asynccontextmanager
    async def db():
        yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(password="hash-1")))

    monkeypatch.setattr(expiry.module, "get_async_db_context", db)
    body = json.dumps({"email": "ASHA@example.com", "password": "pw"}).encode()

    owui.Auths.authenticate_user.return_value = None  # wrong password
    assert await expiry._expired_signin(body) is None
    owui.Auths.authenticate_user.return_value = USER
    assert await expiry._expired_signin(body) == (USER, "hash-1")
    assert await expiry._expired_signin(b"not json") is None

    expiry._seconds_left = AsyncMock(return_value=10 * DAY)  # not expired
    assert await expiry._expired_signin(body) is None


class SigninApp:
    """An app with Open WebUI's sign-in route, whose original handler records what reached it."""

    def __init__(self):
        self.received = []

        async def original(scope, receive, send):
            message = await receive()
            self.received.append(message["body"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        self.route = SimpleNamespace(path="/api/v1/auths/signin", methods={"POST"}, app=original)
        self.router = SimpleNamespace(routes=[self.route])


async def call(app, *chunks):
    sent, pending = [], [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
                         for i, c in enumerate(chunks)]

    async def receive():
        return pending.pop(0)

    async def send(message):
        sent.append(message)

    await app.route.app({"type": "http"}, receive, send)
    return sent


async def test_signin_wrapper_blocks_expired_and_replays_the_body_otherwise(expiry):
    app = SigninApp()
    expiry._wrap_signin(app)
    expiry._wrap_signin(app)  # re-wrapping keeps one layer

    expiry._expired_signin = AsyncMock(return_value=None)
    sent = await call(app, b'{"email": "asha@', b'example.com"}')
    assert sent[0]["status"] == 200 and app.received == [b'{"email": "asha@example.com"}']

    expiry._expired_signin = AsyncMock(return_value=(USER, "hash-1"))
    sent = await call(app, b"{}")
    assert sent[0]["status"] == 403 and "expired" in json.loads(sent[1]["body"])["detail"]
    assert len(app.received) == 1  # the original handler wasn't called


async def test_a_failing_check_never_blocks_sign_in(expiry):
    app = SigninApp()
    expiry._wrap_signin(app)
    expiry._expired_signin = AsyncMock(side_effect=RuntimeError("db down"))
    assert (await call(app, b"{}"))[0]["status"] == 200


async def test_reminder_at_login_within_warn_days(expiry):
    owui.Users.get_user_by_id.return_value = USER
    expiry._claim_warning = AsyncMock(return_value=True)

    expiry._seconds_left = AsyncMock(return_value=3 * DAY - 60)
    await expiry.event({"event": "auth.login", "subject": {"id": "u1"}})
    assert "expires in 3 days" in expiry.sent[-1]["body"]

    expiry._seconds_left = AsyncMock(return_value=30 * DAY)
    await expiry.event({"event": "auth.login", "subject": {"id": "u1"}})
    assert len(expiry.sent) == 1


async def test_signup_and_password_change_restart_the_clock(expiry):
    expiry._set_changed = AsyncMock()
    await expiry.event({"event": "auth.password_changed", "subject": {"id": "u1"}})
    expiry._set_changed.assert_awaited_once_with("u1")


async def test_email_is_sent_right_after_a_reboot(expiry, monkeypatch):
    """time.monotonic() counts from boot: "never emailed" must not look like "emailed at boot"."""
    monkeypatch.setattr(expiry.module.time, "monotonic", lambda: 5.0)
    await expiry._handle_expired(USER, "hash-1")
    assert len(expiry.sent) == 1
