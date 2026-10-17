
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from conftest import load_function, owui

USER = SimpleNamespace(id="u1", name="Asha", email="asha@example.com", role="user")


@pytest.fixture
def reset(secret_key):
    module = load_function("password_reset")
    fn = module.Event()
    fn.sent = []
    fn._send_in_background = lambda to, subject, body, what: fn.sent.append({"to": to, "subject": subject, "body": body})
    fn._resettable = AsyncMock(return_value=(USER, "hash-1"))
    owui.Auths.update_user_password_by_id.return_value = True
    fn.module = module
    return fn


def token_for(fn, password_hash="hash-1", user_id="u1"):
    return fn._serializer().dumps({"id": user_id, "fp": fn.module._fingerprint(password_hash)})


def test_format_duration(reset):
    fmt = reset.module._format_duration
    assert [fmt(30), fmt(60), fmt(30 * 60), fmt(3600), fmt(2 * 3600)] == [
        "1 minute", "1 minute", "30 minutes", "1 hour", "2 hours",
    ]


def test_fingerprint_is_short_and_tied_to_the_hash(reset):
    fp = reset.module._fingerprint
    assert len(fp("hash-1")) == 16 and fp("hash-1") == fp("hash-1") != fp("hash-2")


async def test_reset_with_a_valid_link(reset):
    result = await reset._reset(SimpleNamespace(), token=token_for(reset), password="New-Password-7")

    assert result == {"status": "password_updated"}
    owui.Auths.update_user_password_by_id.assert_awaited_once_with("u1", "hashed:New-Password-7")


async def test_a_link_stops_working_once_the_password_changed(reset):
    stale = token_for(reset, password_hash="hash-before-change")
    with pytest.raises(HTTPException, match="invalid or was already used"):
        await reset._reset(SimpleNamespace(), token=stale, password="New-Password-7")
    owui.Auths.update_user_password_by_id.assert_not_awaited()


async def test_tampered_and_expired_links(reset):
    with pytest.raises(HTTPException, match="invalid"):
        await reset._reset(SimpleNamespace(), token=token_for(reset) + "x", password="New-Password-7")
    reset.valves.token_max_age_seconds = -1
    with pytest.raises(HTTPException, match="expired"):
        await reset._reset(SimpleNamespace(), token=token_for(reset), password="New-Password-7")


async def test_short_passwords_are_refused(reset):
    with pytest.raises(HTTPException, match="at least 8"):
        await reset._reset(SimpleNamespace(), token=token_for(reset), password="short")


async def test_forgot_emails_a_working_link_and_hides_unknown_emails(reset):
    owui.Users.get_user_by_email.side_effect = lambda email: USER if email == "asha@example.com" else None

    unknown = await reset._forgot(email="nobody@example.com")
    known = await reset._forgot(email="  Asha@Example.com ")

    assert unknown == known  # same answer either way
    assert len(reset.sent) == 1 and reset.sent[0]["to"] == "asha@example.com"
    token = reset.sent[0]["body"].split("#token=")[1].split()[0]
    assert (await reset._reset(SimpleNamespace(), token=token, password="New-Password-7"))["status"] == "password_updated"


async def test_forgot_has_a_cooldown_per_email(reset):
    owui.Users.get_user_by_email.return_value = USER
    await reset._forgot(email="asha@example.com")
    await reset._forgot(email="asha@example.com")
    assert len(reset.sent) == 1


async def test_password_change_notice(reset):
    owui.Users.get_user_by_id.return_value = USER
    await reset.event({"event": "auth.password_changed", "subject": {"id": "u1"}})
    assert reset.sent[-1]["subject"] == "Your Open WebUI password was changed"

    reset.valves.notify_on_password_change = False
    await reset.event({"event": "auth.password_changed", "subject": {"id": "u1"}})
    assert len(reset.sent) == 1


async def test_forgot_works_right_after_a_reboot(reset, monkeypatch):
    """time.monotonic() counts from boot: "never requested" must not look like "requested at boot"."""
    monkeypatch.setattr(reset.module.time, "monotonic", lambda: 5.0)
    owui.Users.get_user_by_email.return_value = USER
    await reset._forgot(email="asha@example.com")
    assert len(reset.sent) == 1
