"""Sign-in through authentik (OIDC authorization code + PKCE, confidential client), for
laptop-frontend. The browser is sent to authentik's public URL; the code exchange and userinfo go
to authentik directly over the Docker network. Users are matched by email (authentik confirms
emails before activating accounts) and created on first sign-in."""

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import ORJSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db import get_db
from models import UserModel
from routers.auth import pwd_context
from schemas import UserResponse
from sessions import (
    SESSION_COOKIE, SESSION_MAX_AGE, STATE_COOKIE, STATE_MAX_AGE, current_user, sign, unsign,
    end_session, start_session,
)

router = APIRouter(prefix="/auth", tags=["sso"])

# Where the browser meets authentik (Caddy's :9443), and where the backend reaches it.
AUTHENTIK_PUBLIC_URL = os.environ.get("AUTHENTIK_PUBLIC_URL", "").rstrip("/")
AUTHENTIK_INTERNAL_URL = os.environ.get("AUTHENTIK_INTERNAL_URL", "http://authentik-server:9000").rstrip("/")
# The client secret comes from OpenBao (apps/default/custom-backend, written by bootstrap.sh) like
# the database credentials; the environment variables are a fallback.
CLIENT_ID = db.secrets.get("custom_frontend_oidc_client_id") or os.environ.get("CUSTOM_FRONTEND_OIDC_CLIENT_ID", "")
CLIENT_SECRET = (db.secrets.get("custom_frontend_oidc_client_secret")
                 or os.environ.get("CUSTOM_FRONTEND_OIDC_CLIENT_SECRET", ""))
# Origins laptop-frontend is served from; the callback must be one of them (and registered in
# authentik/blueprints/custom-frontend-sso.yaml).
FRONTEND_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()}
# laptop-frontend's nginx strips /api before forwarding here.
CALLBACK_PATH = "/api/auth/callback"

# Follow Open WebUI's session: Open WebUI's sign-in cookie (`token`, not HTTPS-only, SameSite=Lax)
# reaches this app too on the same host (cookies ignore the port). When it belongs to a signed-in
# Open WebUI user other than this app's, /auth/me switches this app to that person (matched by
# email, like the authentik callback). Open WebUI checks the token; nothing here trusts it alone.
FOLLOW_OPEN_WEBUI_SESSION = os.environ.get("FOLLOW_OPEN_WEBUI_SESSION", "true").lower() not in ("false", "0", "no")
OPEN_WEBUI_INTERNAL_URL = os.environ.get("OPEN_WEBUI_INTERNAL_URL", "http://open-webui:8080").rstrip("/")
OPEN_WEBUI_COOKIE = "token"
_owui_cache: dict[str, tuple[float, dict | None]] = {}
OWUI_CACHE_SECONDS = 60


async def _open_webui_user(token: str | None) -> dict | None:
    """{"email", "name"} of the active Open WebUI user the token belongs to, else None (no token,
    expired, pending account, Open WebUI unreachable). Answers are cached for a minute per token."""
    if not FOLLOW_OPEN_WEBUI_SESSION or not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _owui_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    found = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OPEN_WEBUI_INTERNAL_URL}/api/v1/auths/",
                                        headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 200:
            data = response.json()
            email = (data.get("email") or "").strip().lower()
            if email and data.get("role") in ("user", "admin"):
                found = {"email": email, "name": (data.get("name") or email.split("@")[0])[:100]}
    except (httpx.HTTPError, ValueError):
        return None
    if len(_owui_cache) > 500:
        _owui_cache.clear()
    _owui_cache[key] = (time.monotonic() + OWUI_CACHE_SECONDS, found)
    return found


async def _user_for_email(db: AsyncSession, email: str, name: str) -> UserModel:
    """The users row for this email, created (no usable password) when it doesn't exist yet."""
    user = (await db.execute(select(UserModel).where(func.lower(UserModel.email) == email))).scalar_one_or_none()
    if user is None:
        # No usable password: this account signs in only through authentik / Open WebUI's session.
        user = UserModel(email=email, name=name, is_verified=True,
                         password_hash=pwd_context.hash(secrets.token_urlsafe(32)))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


def _origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "")
    origin = f"{proto}://{host}"
    if origin not in FRONTEND_ORIGINS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown frontend origin {origin}.")
    return origin


def _set_cookie(response, request: Request, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, path="/", httponly=True, samesite="lax",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
    )


@router.get("/login")
async def login(request: Request):
    origin = _origin(request)
    if not (AUTHENTIK_PUBLIC_URL and CLIENT_ID and CLIENT_SECRET):
        # Back to the app with a message rather than an error page (the app redirects here by itself).
        return RedirectResponse(f"{origin}/?signin=unavailable", status_code=302)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": origin + CALLBACK_PATH,
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    response = RedirectResponse(f"{AUTHENTIK_PUBLIC_URL}/application/o/authorize/?{query}", status_code=302)
    _set_cookie(response, request, STATE_COOKIE, sign({"state": state, "verifier": verifier}, STATE_MAX_AGE), STATE_MAX_AGE)
    return response


async def _fetch_userinfo(code: str, verifier: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        token = await client.post(
            f"{AUTHENTIK_INTERNAL_URL}/application/o/token/",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        token.raise_for_status()
        info = await client.get(
            f"{AUTHENTIK_INTERNAL_URL}/application/o/userinfo/",
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        )
        info.raise_for_status()
        return info.json()


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = "",
                   db: AsyncSession = Depends(get_db)):
    origin = _origin(request)
    saved = unsign(request.cookies.get(STATE_COOKIE))
    if error or not code or not saved or not secrets.compare_digest(saved.get("state", ""), state):
        return RedirectResponse(f"{origin}/?signin=failed", status_code=302)

    try:
        info = await _fetch_userinfo(code, saved["verifier"], origin + CALLBACK_PATH)
    except (httpx.HTTPError, KeyError, ValueError):
        return RedirectResponse(f"{origin}/?signin=failed", status_code=302)

    email = (info.get("email") or "").strip().lower()
    if not email:
        return RedirectResponse(f"{origin}/?signin=failed", status_code=302)
    name = (info.get("name") or info.get("preferred_username") or email.split("@")[0])[:100]

    user = await _user_for_email(db, email, name)
    if not user.is_active:
        return RedirectResponse(f"{origin}/?signin=deactivated", status_code=302)

    response = RedirectResponse(f"{origin}/", status_code=302)
    cookie = await start_session(db, user.id, request.headers.get("user-agent"))
    _set_cookie(response, request, SESSION_COOKIE, cookie, SESSION_MAX_AGE)
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserResponse)
async def me(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """The signed-in user. Follows Open WebUI's session (see FOLLOW_OPEN_WEBUI_SESSION): when the
    browser is signed in to Open WebUI as someone else, or this app has no session, this app's
    session switches to that person. Without an Open WebUI sign-in, this app's own session counts."""
    try:
        user = await current_user(request, db)
    except HTTPException:
        user = None
    owui = await _open_webui_user(request.cookies.get(OPEN_WEBUI_COOKIE))
    if owui and (user is None or user.email.lower() != owui["email"]):
        followed = await _user_for_email(db, owui["email"], owui["name"])
        if followed.is_active:
            await end_session(db, request.cookies.get(SESSION_COOKIE))
            cookie = await start_session(db, followed.id, request.headers.get("user-agent"))
            _set_cookie(response, request, SESSION_COOKIE, cookie, SESSION_MAX_AGE)
            return followed
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    """Ends this app's session (user_sessions.ended_at). authentik's own session is ended by the browser visiting
    `authentik_logout_url`, otherwise the next sign-in goes straight through."""
    response = ORJSONResponse({
        "authentik_logout_url": f"{AUTHENTIK_PUBLIC_URL}/flows/-/default/invalidation/" if AUTHENTIK_PUBLIC_URL else None,
    })
    await end_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
