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

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db import get_db
from models import UserModel

SESSION_COOKIE = "session"
STATE_COOKIE = "sso_state"
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
STATE_MAX_AGE = 600


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
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user
