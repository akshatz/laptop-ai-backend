"""Sign-in through authentik (OIDC authorization code + PKCE, confidential client), for
custom-frontend. The browser is sent to authentik's public URL; the code exchange and userinfo go
to authentik directly over the Docker network. Users are matched by email (authentik confirms
emails before activating accounts) and created on first sign-in."""

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
# Origins custom-frontend is served from; the callback must be one of them (and registered in
# authentik/blueprints/custom-frontend-sso.yaml).
FRONTEND_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()}
# custom-frontend's nginx strips /api before forwarding here.
CALLBACK_PATH = "/api/auth/callback"


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

    user = (await db.execute(select(UserModel).where(func.lower(UserModel.email) == email))).scalar_one_or_none()
    if user is None:
        # No usable password: this account signs in only through authentik.
        user = UserModel(email=email, name=name, is_verified=True,
                         password_hash=pwd_context.hash(secrets.token_urlsafe(32)))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.is_active:
        return RedirectResponse(f"{origin}/?signin=deactivated", status_code=302)

    response = RedirectResponse(f"{origin}/", status_code=302)
    cookie = await start_session(db, user.id, request.headers.get("user-agent"))
    _set_cookie(response, request, SESSION_COOKIE, cookie, SESSION_MAX_AGE)
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: UserModel = Depends(current_user)):
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
