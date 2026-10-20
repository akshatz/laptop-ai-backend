"""Signed cookies for the authentik sign-in: the session (user id) and the short-lived sign-in
state (OIDC state + PKCE verifier). Values are `<base64 payload>.<HMAC-SHA256>` with
the session secret, so the browser can hold them but not forge or change them. The secret is
`session_secret` in OpenBao's apps/default/custom-backend (written by openbao/bootstrap.sh), else
the SESSION_SECRET environment variable."""

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db import get_db
from models import UserModel, UserSessionModel

SESSION_COOKIE = "session"
STATE_COOKIE = "sso_state"
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
STATE_MAX_AGE = 600
# last_seen_at is written at most this often per session.
LAST_SEEN_EVERY = timedelta(seconds=60)


def _secret() -> bytes:
    secret = db.secrets.get("session_secret") or os.environ.get("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("session_secret (OpenBao) or SESSION_SECRET must be set, at least 32 characters")
    return secret.encode()


def sign(data: dict, max_age: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({**data, "exp": int(time.time()) + max_age}).encode()).decode()
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def unsign(value: str | None) -> dict | None:
    """The signed data, or None when the value is missing, altered or expired."""
    if not value or "." not in value:
        return None
    payload, mac = value.rsplit(".", 1)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> UserModel:
    """The signed-in user, from the session cookie; 401 otherwise."""
    data = unsign(request.cookies.get(SESSION_COOKIE))
    user = None
    if data:
        try:
            user_id = uuid.UUID(data.get("uid", ""))
        except ValueError:
            user_id = None
        if user_id:
            user = (await db.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
        # Cookies from before user_sessions carry no sid and stay valid until they expire.
        if user and data.get("sid"):
            if not await _touch_session(db, data["sid"], user.id):
                user = None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user


async def _touch_session(db: AsyncSession, sid: str, user_id: uuid.UUID) -> bool:
    """False when the session was signed out (or isn't this user's); otherwise updates last_seen_at."""
    try:
        session_id = uuid.UUID(sid)
    except ValueError:
        return False
    row = (await db.execute(
        select(UserSessionModel).where(UserSessionModel.id == session_id, UserSessionModel.user_id == user_id)
    )).scalar_one_or_none()
    if not row or row.ended_at is not None:
        return False
    now = datetime.now(timezone.utc)
    if now - row.last_seen_at >= LAST_SEEN_EVERY:
        await db.execute(update(UserSessionModel).where(UserSessionModel.id == session_id).values(last_seen_at=now))
        await db.commit()
    return True


async def start_session(db: AsyncSession, user_id: uuid.UUID, user_agent: str | None) -> str:
    """A new user_sessions row; returns the signed session cookie value carrying its id."""
    now = datetime.now(timezone.utc)
    row = UserSessionModel(user_id=user_id, created_at=now, last_seen_at=now, user_agent=(user_agent or "")[:300] or None)
    db.add(row)
    await db.commit()
    return sign({"uid": str(user_id), "sid": str(row.id)}, SESSION_MAX_AGE)


async def end_session(db: AsyncSession, cookie: str | None) -> None:
    data = unsign(cookie)
    try:
        session_id = uuid.UUID((data or {}).get("sid", ""))
    except ValueError:
        return
    await db.execute(update(UserSessionModel).where(UserSessionModel.id == session_id, UserSessionModel.ended_at.is_(None))
                     .values(ended_at=datetime.now(timezone.utc)))
    await db.commit()
