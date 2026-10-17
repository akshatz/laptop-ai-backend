from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from conftest import load_function, owui


def pending(role="pending"):
    return SimpleNamespace(id="u1", name="Asha", email="asha@example.com", role=role)


@pytest.fixture
def verify(secret_key):
    fn = load_function("signup_email_verification").Event()
    fn.sent = []
    fn._send_in_background = lambda user_id, email, name: fn.sent.append(email)
    fn._mark_verified = AsyncMock(return_value=True)
    fn._is_verified = AsyncMock(return_value=False)
    owui.Users.get_user_by_id.return_value = pending()
    owui.Users.update_user_role_by_id.side_effect = lambda user_id, role: pending(role)
    return fn


async def test_a_valid_link_promotes_a_pending_user(verify):
    result = await verify._verify(token=verify._serializer().dumps("u1"))
    assert result["role"] == "user"
    owui.Users.update_user_role_by_id.assert_awaited_once_with("u1", "user")


async def test_a_suspended_user_stays_pending(verify):
    """Verified before, then set back to pending by an admin: the old link must not reactivate them."""
    verify._mark_verified.return_value = False
    result = await verify._verify(token=verify._serializer().dumps("u1"))
    assert result["role"] == "pending"
    owui.Users.update_user_role_by_id.assert_not_awaited()


async def test_bad_and_expired_links(verify):
    with pytest.raises(HTTPException, match="Invalid"):
        await verify._verify(token="garbage")
    verify.valves.token_max_age_seconds = -1
    with pytest.raises(HTTPException, match="expired"):
        await verify._verify(token=verify._serializer().dumps("u1"))


async def test_password_signup_gets_an_email(verify):
    await verify.event({"event": "auth.signup", "subject": {"id": "u1"}})
    assert verify.sent == ["asha@example.com"]


async def test_sso_accounts_are_activated_without_an_email(verify):
    await verify.event({"event": "user.created", "source": "oauth", "subject": {"id": "u1"}})
    assert verify.sent == []
    owui.Users.update_user_role_by_id.assert_awaited_once_with("u1", "user")


async def test_sso_accounts_get_the_email_when_activation_is_off(verify):
    verify.valves.activate_sso_users = False
    await verify.event({"event": "user.created", "source": "oauth", "subject": {"id": "u1"}})
    assert verify.sent == ["asha@example.com"]


async def test_other_events_and_active_users_are_ignored(verify):
    await verify.event({"event": "user.created", "source": "signup", "subject": {"id": "u1"}})
    owui.Users.get_user_by_id.return_value = pending(role="user")
    await verify.event({"event": "auth.signup", "subject": {"id": "u1"}})
    assert verify.sent == []


async def test_resend_is_the_same_answer_for_everyone_and_has_a_cooldown(verify):
    owui.Users.get_user_by_email.side_effect = lambda email: pending() if email == "asha@example.com" else None
    unknown = await verify._resend(email="nobody@example.com")
    known = await verify._resend(email="Asha@example.com")
    again = await verify._resend(email="asha@example.com")
    assert unknown == known == again
    assert verify.sent == ["asha@example.com"]
