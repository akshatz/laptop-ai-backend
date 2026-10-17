import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

import models

PASSWORD = "Correct-Horse-9"


def _code(email: dict) -> str:
    return re.search(r"\b(\d{6})\b", email["body"]).group(1)


async def register(client, email="asha@example.com", password=PASSWORD, name="Asha"):
    return await client.post("/users", json={"email": email, "password": password, "name": name})


async def verified_user(client, emails, email="asha@example.com"):
    assert (await register(client, email=email)).status_code == 201
    token = emails[-1]["body"].strip().splitlines()[-1]
    assert (await client.post("/users/verify", json={"token": token})).status_code == 200
    emails.clear()


async def login(client, email="asha@example.com", password=PASSWORD):
    return await client.post("/login", json={"email": email, "password": password})


async def test_register_stores_hash_and_emails_a_token(client, emails, session_maker):
    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "asha@example.com"
    assert body["is_verified"] is False
    assert "password" not in body and "password_hash" not in body
    assert len(emails) == 1 and emails[0]["to"] == "asha@example.com"
    async with session_maker() as session:
        user = await session.get(models.UserModel, body["id"])
        assert user.password_hash != PASSWORD and user.password_hash.startswith("$2")


async def test_register_rejects_a_duplicate_email(client):
    await register(client)
    response = await register(client)
    assert response.status_code == 400


async def test_verify_rejects_an_unknown_token(client):
    response = await client.post("/users/verify", json={"token": "not-a-token"})
    assert response.status_code == 400


async def test_login_needs_a_verified_email(client):
    await register(client)
    response = await login(client)
    assert response.status_code == 403


async def test_login_with_a_wrong_password(client, emails):
    await verified_user(client, emails)
    assert (await login(client, password="wrong")).status_code == 401
    assert (await login(client, email="nobody@example.com")).status_code == 401


async def test_login_without_mfa(client, emails):
    await verified_user(client, emails)

    response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is False and body["password_reset_required"] is False
    assert body["user"]["email"] == "asha@example.com"
    assert emails == []


async def test_deactivated_users_cannot_log_in_until_reactivated(client, emails):
    await verified_user(client, emails)
    credentials = {"email": "asha@example.com", "password": PASSWORD}

    assert (await client.post("/users/deactivate", json=credentials)).json()["is_active"] is False
    assert (await login(client)).status_code == 403
    assert (await client.post("/users/reactivate", json=credentials)).json()["is_active"] is True
    assert (await login(client)).status_code == 200


async def test_mfa_login_emails_a_code_that_works_once(client, emails):
    await verified_user(client, emails)
    await client.post("/mfa/enable", json={"email": "asha@example.com", "password": PASSWORD})

    response = await login(client)
    assert response.json() == {"mfa_required": True, "password_reset_required": False, "user": None}
    code = _code(emails[-1])

    wrong = "000000" if code != "000000" else "111111"
    bad = await client.post("/mfa/verify-otp", json={"email": "asha@example.com", "otp_code": wrong})
    assert bad.status_code == 401
    ok = await client.post("/mfa/verify-otp", json={"email": "asha@example.com", "otp_code": code})
    assert ok.status_code == 200
    again = await client.post("/mfa/verify-otp", json={"email": "asha@example.com", "otp_code": code})
    assert again.status_code == 401


async def test_expired_mfa_code_is_refused(client, emails, session_maker):
    await verified_user(client, emails)
    await client.post("/mfa/enable", json={"email": "asha@example.com", "password": PASSWORD})
    await login(client)
    code = _code(emails[-1])
    async with session_maker() as session:
        await session.execute(
            update(models.UserModel).values(mfa_otp_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await session.commit()

    response = await client.post("/mfa/verify-otp", json={"email": "asha@example.com", "otp_code": code})
    assert response.status_code == 401


async def test_password_older_than_180_days_must_be_reset(client, emails, session_maker):
    await verified_user(client, emails)
    async with session_maker() as session:
        await session.execute(
            update(models.UserModel).values(password_changed_at=datetime.now(timezone.utc) - timedelta(days=181))
        )
        await session.commit()

    response = await login(client)

    assert response.status_code == 200
    assert response.json() == {"mfa_required": False, "password_reset_required": True, "user": None}
    assert emails[-1]["subject"] == "Your password reset code"


async def test_forgot_password_does_not_reveal_unknown_emails(client, emails):
    response = await client.post("/password/forgot", json={"email": "nobody@example.com"})
    assert response.status_code == 200
    assert emails == []


async def test_password_reset_with_the_emailed_code(client, emails):
    await verified_user(client, emails)
    known = await client.post("/password/forgot", json={"email": "asha@example.com"})
    assert known.json() == (await client.post("/password/forgot", json={"email": "nobody@example.com"})).json()
    code = _code(emails[-1])

    response = await client.post(
        "/password/reset",
        json={"email": "asha@example.com", "otp_code": code, "new_password": "New-Password-7"},
    )

    assert response.status_code == 200
    assert (await login(client)).status_code == 401
    assert (await login(client, password="New-Password-7")).status_code == 200
    assert emails[-1]["subject"] == "Your password was reset"


async def test_password_change_needs_the_current_password(client, emails):
    await verified_user(client, emails)
    wrong = await client.post(
        "/password/change",
        json={"email": "asha@example.com", "current_password": "wrong", "new_password": "New-Password-7"},
    )
    assert wrong.status_code == 401

    ok = await client.post(
        "/password/change",
        json={"email": "asha@example.com", "current_password": PASSWORD, "new_password": "New-Password-7"},
    )
    assert ok.status_code == 200
    assert (await login(client, password="New-Password-7")).status_code == 200
