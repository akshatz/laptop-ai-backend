from urllib.parse import parse_qs, urlparse

import pytest

import sessions
from routers import sso


@pytest.fixture
def userinfo(monkeypatch):
    """authentik's answer to the code exchange; tests set `info` or `error`."""
    state = {"info": {"email": "Ravi@Example.com", "name": "Ravi"}, "error": None, "calls": []}

    async def fake(code, verifier, redirect_uri):
        state["calls"].append({"code": code, "verifier": verifier, "redirect_uri": redirect_uri})
        if state["error"]:
            raise state["error"]
        return state["info"]

    monkeypatch.setattr(sso, "_fetch_userinfo", fake)
    return state


async def start_login(client):
    response = await client.get("/auth/login")
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query), response


async def test_login_redirects_to_authentik_with_pkce(client):
    query, response = await start_login(client)
    assert response.headers["location"].startswith("https://auth.example.test:9443/application/o/authorize/?")
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://test/api/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    saved = sessions.unsign(client.cookies.get(sessions.STATE_COOKIE))
    assert saved["state"] == query["state"][0]
    assert "httponly" in response.headers["set-cookie"].lower()


async def test_login_without_configuration_returns_to_the_app(client, monkeypatch):
    monkeypatch.setattr(sso, "CLIENT_SECRET", "")
    response = await client.get("/auth/login")
    assert response.status_code == 302 and response.headers["location"] == "http://test/?signin=unavailable"


async def test_login_refuses_an_unknown_origin(client):
    response = await client.get("/auth/login", headers={"host": "evil.example"})
    assert response.status_code == 400


async def test_callback_creates_the_user_and_signs_in(client, userinfo):
    query, _ = await start_login(client)
    response = await client.get("/auth/callback", params={"code": "abc", "state": query["state"][0]})

    assert response.status_code == 302 and response.headers["location"] == "http://test/"
    assert userinfo["calls"][0]["redirect_uri"] == "http://test/api/auth/callback"
    me = (await client.get("/auth/me")).json()
    assert me["email"] == "ravi@example.com" and me["name"] == "Ravi" and me["is_verified"]
    # Signing in again finds the same user.
    query, _ = await start_login(client)
    await client.get("/auth/callback", params={"code": "def", "state": query["state"][0]})
    assert (await client.get("/auth/me")).json()["id"] == me["id"]


async def test_callback_matches_an_existing_user_by_email(client, userinfo, sign_in, make_client):
    async with await make_client() as other:
        existing = await sign_in(other, "ravi@example.com", "Ravi")
    query, _ = await start_login(client)
    await client.get("/auth/callback", params={"code": "abc", "state": query["state"][0]})
    assert (await client.get("/auth/me")).json()["id"] == existing


async def test_callback_with_a_wrong_state_does_not_sign_in(client, userinfo):
    await start_login(client)
    response = await client.get("/auth/callback", params={"code": "abc", "state": "not-the-state"})
    assert response.headers["location"] == "http://test/?signin=failed"
    assert userinfo["calls"] == []
    assert (await client.get("/auth/me")).status_code == 401


async def test_callback_when_authentik_fails(client, userinfo):
    import httpx

    userinfo["error"] = httpx.ConnectError("down")
    query, _ = await start_login(client)
    response = await client.get("/auth/callback", params={"code": "abc", "state": query["state"][0]})
    assert response.headers["location"] == "http://test/?signin=failed"
    assert (await client.get("/auth/me")).status_code == 401


async def test_logout_clears_the_session(client, signed_in):
    assert (await client.get("/auth/me")).status_code == 200
    response = await client.post("/auth/logout")
    assert response.json()["authentik_logout_url"] == "https://auth.example.test:9443/flows/-/default/invalidation/"
    # The browser is told to drop the cookie (the test client's hand-set cookie isn't matched by it).
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{sessions.SESSION_COOKIE}=") and "Max-Age=0" in set_cookie


@pytest.fixture
def open_webui(monkeypatch):
    """Who Open WebUI says its `token` cookie belongs to; tests set `user` (None = not signed in)."""
    state = {"user": None, "tokens": []}

    async def fake(token):
        state["tokens"].append(token)
        return state["user"] if token else None

    monkeypatch.setattr(sso, "_open_webui_user", fake)
    return state


async def test_me_follows_open_webui_without_a_session_of_its_own(client, open_webui):
    open_webui["user"] = {"email": "meera@example.com", "name": "Meera"}
    client.cookies.set("token", "owui-jwt")

    response = await client.get("/auth/me")

    assert response.status_code == 200 and response.json()["email"] == "meera@example.com"
    assert open_webui["tokens"] == ["owui-jwt"]
    assert sessions.SESSION_COOKIE in response.headers.get("set-cookie", "")
    # The new session works on its own afterwards.
    client.cookies.delete("token")
    assert (await client.get("/auth/me")).json()["email"] == "meera@example.com"


async def test_me_switches_to_the_open_webui_user(client, signed_in, open_webui):
    open_webui["user"] = {"email": "meera@example.com", "name": "Meera"}
    client.cookies.set("token", "owui-jwt")
    assert (await client.get("/auth/me")).json()["email"] == "meera@example.com"


async def test_me_keeps_its_own_session_without_open_webui(client, signed_in, open_webui):
    assert (await client.get("/auth/me")).json()["email"] == "ravi@example.com"
    client.cookies.set("token", "expired-jwt")  # Open WebUI doesn't recognise it
    assert (await client.get("/auth/me")).json()["email"] == "ravi@example.com"
    assert "set-cookie" not in (await client.get("/auth/me")).headers


async def test_me_without_any_session_is_401(client, open_webui):
    assert (await client.get("/auth/me")).status_code == 401


async def test_open_webui_user_is_checked_by_open_webui(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        role = "pending" if request.headers["authorization"].endswith("pending") else "user"
        return sso.httpx.Response(200, json={"email": "Meera@Example.com", "name": "Meera", "role": role})

    real_client = sso.httpx.AsyncClient
    monkeypatch.setattr(sso.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=sso.httpx.MockTransport(handler), **kw))
    sso._owui_cache.clear()
    assert await sso._open_webui_user("abc") == {"email": "meera@example.com", "name": "Meera"}
    assert await sso._open_webui_user("abc") == {"email": "meera@example.com", "name": "Meera"}  # cached
    assert await sso._open_webui_user("t-pending") is None  # pending accounts aren't followed
    assert seen == ["Bearer abc", "Bearer t-pending"]
    assert await sso._open_webui_user(None) is None
