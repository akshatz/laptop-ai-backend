import uuid

from sqlalchemy import update

import models
import sessions
from test_sso import start_login, userinfo  # noqa: F401 (fixture)


async def sign_in_via_authentik(client, userinfo, email, name):
    userinfo["info"] = {"email": email, "name": name}
    query, _ = await start_login(client)
    await client.get("/auth/callback", params={"code": "c", "state": query["state"][0]},
                     headers={"user-agent": "Mozilla/5.0 (Test phone)"})
    return (await client.get("/auth/me")).json()["id"]


async def make_admin(session_maker, user_id):
    async with session_maker() as session:
        await session.execute(update(models.UserModel).where(models.UserModel.id == uuid.UUID(user_id)).values(role="admin"))
        await session.commit()


async def test_sessions_are_admin_only(client, signed_in):
    assert (await client.get("/admin/sessions")).status_code == 403


async def test_admin_sees_who_is_signed_in(client, userinfo, session_maker, make_client):
    admin_id = await sign_in_via_authentik(client, userinfo, "akshat@example.com", "Akshat")
    await make_admin(session_maker, admin_id)
    async with await make_client() as other:
        await sign_in_via_authentik(other, userinfo, "meera@example.com", "Meera")

        listed = (await client.get("/admin/sessions")).json()

        assert sorted(s["email"] for s in listed) == ["akshat@example.com", "meera@example.com"]
        meera = next(s for s in listed if s["email"] == "meera@example.com")
        assert meera["active_now"] and not meera["is_you"] and meera["user_agent"] == "Mozilla/5.0 (Test phone)"
        assert meera["signed_in_at"] and meera["last_seen_at"] and meera["expires_at"] > meera["signed_in_at"]
        assert next(s for s in listed if s["email"] == "akshat@example.com")["is_you"]

        # Signing out ends the session: gone from the list, and the old cookie no longer works.
        cookie = other.cookies.get(sessions.SESSION_COOKIE)
        await other.post("/auth/logout")
        assert [s["email"] for s in (await client.get("/admin/sessions")).json()] == ["akshat@example.com"]
        other.cookies.set(sessions.SESSION_COOKIE, cookie)
        assert (await other.get("/auth/me")).status_code == 401


async def test_old_cookies_without_a_session_id_still_work(client, signed_in):
    # conftest's sign_in_as signs {"uid": ...} like cookies from before user_sessions.
    assert (await client.get("/auth/me")).status_code == 200
