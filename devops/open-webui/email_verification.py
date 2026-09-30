"""
Email verification for Open WebUI signups.

Wraps open_webui.main:app (start.sh is patched to launch `email_verification:app`
instead) so no Open WebUI source file is modified. When a signup produces a
`pending` user, a signed verification link is emailed to them; following it
promotes the user from `pending` to `user`.

Endpoints (all under Open WebUI's own port):
  GET  /api/v1/auths/verify-email?token=...   link from the email (redirects browsers to the UI)
  POST /api/v1/auths/verify-email             {"token": "..."} for API clients
  POST /api/v1/auths/verify-email/resend      {"email": "..."} re-sends the link to a pending user

Tokens are signed with WEBUI_SECRET_KEY (itsdangerous) and expire after
EMAIL_VERIFY_TOKEN_MAX_AGE seconds (default 2 hours). Each user can be verified
only once: verified user ids are recorded in EMAIL_VERIFY_DB_PATH (a SQLite file
on the data volume, outside the user-editable `info`/`variables` fields). An
admin can therefore still move a verified user back to `pending` to suspend
them, and neither an old link nor a resend will re-activate the account.
"""

import asyncio
import json
import logging
import os
import smtplib
import sqlite3
import time
from email.message import EmailMessage

from fastapi import Body, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from open_webui.main import app
from open_webui.models.users import Users

log = logging.getLogger("email_verification")

SIGNUP_PATH = "/api/v1/auths/signup"
VERIFY_PATH = "/api/v1/auths/verify-email"
RESEND_PATH = "/api/v1/auths/verify-email/resend"

BASE_URL = os.environ.get("EMAIL_VERIFY_BASE_URL", "http://localhost:8082").rstrip("/")
TOKEN_MAX_AGE = int(os.environ.get("EMAIL_VERIFY_TOKEN_MAX_AGE", str(2 * 3600)))
VERIFY_DB_PATH = os.environ.get("EMAIL_VERIFY_DB_PATH", "/app/backend/data/email_verification.db")
RESEND_COOLDOWN = 60  # seconds between resends to the same address

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER

_serializer = URLSafeTimedSerializer(os.environ["WEBUI_SECRET_KEY"], salt="open-webui-email-verify")
_last_resend: dict[str, float] = {}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(VERIFY_DB_PATH, timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT PRIMARY KEY, verified_at INTEGER NOT NULL)")
    return conn


def _is_verified_sync(user_id: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM verified_users WHERE user_id = ?", (user_id,)).fetchone() is not None


def _mark_verified_sync(user_id: str) -> bool:
    """Records user_id as verified; returns False if it already was (so each user is promoted at most once)."""
    with _db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO verified_users (user_id, verified_at) VALUES (?, ?)", (user_id, int(time.time()))
        )
        return cursor.rowcount == 1


async def _is_verified(user_id: str) -> bool:
    return await asyncio.to_thread(_is_verified_sync, user_id)


async def _mark_verified(user_id: str) -> bool:
    return await asyncio.to_thread(_mark_verified_sync, user_id)


def _format_duration(seconds: int) -> str:
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _send_email(to_address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(message)


async def _send_verification(user_id: str, email: str, name: str) -> None:
    token = _serializer.dumps(user_id)
    link = f"{BASE_URL}{VERIFY_PATH}?token={token}"
    body = (
        f"Hi {name},\n\n"
        f"Verify your email to activate your Open WebUI account:\n{link}\n\n"
        f"This link expires in {_format_duration(TOKEN_MAX_AGE)}.\n"
    )
    try:
        await asyncio.to_thread(_send_email, email, "Verify your Open WebUI account", body)
        log.info("Sent verification email to %s", email)
    except Exception:
        log.exception("Failed to send verification email to %s", email)


async def _verify(token: str):
    try:
        user_id = _serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(400, detail="Verification link has expired. Request a new one.")
    except BadSignature:
        raise HTTPException(400, detail="Invalid verification token.")

    user = await Users.get_user_by_id(user_id)
    if not user:
        raise HTTPException(400, detail="Invalid verification token.")
    # Only a first-time verification promotes; a pending user who was verified before was
    # put back to pending by an admin (suspended), and stays that way.
    if user.role == "pending" and await _mark_verified(user.id):
        user = await Users.update_user_role_by_id(user.id, "user")
        log.info("Verified %s, role pending -> user", user.email)
    return user


async def verify_email_link(request: Request, token: str):
    user = await _verify(token)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"{BASE_URL}/", status_code=303)
    return {"status": "verified", "email": user.email, "role": user.role}


async def verify_email_api(token: str = Body(..., embed=True)):
    user = await _verify(token)
    return {"status": "verified", "email": user.email, "role": user.role}


async def resend_verification(email: str = Body(..., embed=True)):
    email = email.lower()
    # Same response whether or not the address exists, so this can't be used to probe accounts.
    response = {"status": "If a pending account exists for that email, a verification link was sent."}

    now = time.monotonic()
    if now - _last_resend.get(email, 0) < RESEND_COOLDOWN:
        return response
    _last_resend[email] = now

    user = await Users.get_user_by_email(email)
    if user and user.role == "pending" and not await _is_verified(user.id):
        asyncio.create_task(_send_verification(user.id, user.email, user.name))
    return response


class SignupEmailMiddleware:
    """Watches successful signup responses and emails pending users a verification link."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if not (scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == SIGNUP_PATH):
            return await self.app(scope, receive, send)

        # Open WebUI's CompressMiddleware sits inside this one, so drop Accept-Encoding to get
        # the signup response back as plain JSON (the client just receives it uncompressed).
        scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k.lower() != b"accept-encoding"]}

        status = 0
        chunks: list[bytes] = []

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and status == 200:
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    try:
                        user = json.loads(b"".join(chunks))
                        if user.get("role") == "pending":
                            asyncio.create_task(_send_verification(user["id"], user["email"], user["name"]))
                    except Exception:
                        log.exception("Could not parse signup response for verification email")
            await send(message)

        await self.app(scope, receive, capture)


# Insert at the front so these win over the SPA static mount at "/".
for path, endpoint, methods in (
    (VERIFY_PATH, verify_email_link, ["GET"]),
    (VERIFY_PATH, verify_email_api, ["POST"]),
    (RESEND_PATH, resend_verification, ["POST"]),
):
    app.router.routes.insert(0, APIRoute(path, endpoint, methods=methods, response_class=JSONResponse))

app.add_middleware(SignupEmailMiddleware)
