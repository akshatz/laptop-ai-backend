"""
title: Signup Email Verification
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Emails pending signups a signed, expiring verification link; confirming it promotes them from pending to user once.

Open WebUI event Function (Admin → Functions → import this file, then enable it).

- On `auth.signup` for a `pending` user, emails a verification link.
- On `system.startup.completed` (and, defensively, on any event) it registers three routes on
  Open WebUI's own app, since event Functions can't declare HTTP routes themselves:
    GET  /api/v1/auths/verify-email            confirm page; the token is in the URL fragment
                                               (#token=...), so it never reaches server/access logs,
                                               and mail scanners that pre-open links don't verify anyone
    POST /api/v1/auths/verify-email            {"token": "..."} → promotes pending → user
    POST /api/v1/auths/verify-email/resend     {"email": "..."} → re-sends the link (rate-limited,
                                               same response whether or not the account exists)

Tokens are signed with WEBUI_SECRET_KEY and expire after `token_max_age_seconds`. Each user is
verified at most once (ids recorded in `verified_db_path`, outside user-editable fields), so an admin
can put a verified user back to `pending` to suspend them without an old link or a resend undoing it.

SMTP settings are Valves; empty values fall back to the SMTP_* environment variables so the
password can stay in the container environment instead of the Functions table.
"""

import asyncio
import logging
import os
import smtplib
import sqlite3
import time
from email.message import EmailMessage

from fastapi import Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from open_webui.models.users import Users

log = logging.getLogger("signup_email_verification")

VERIFY_PATH = "/api/v1/auths/verify-email"
RESEND_PATH = "/api/v1/auths/verify-email/resend"
ROUTE_TAG = "signup_email_verification"
RESEND_COOLDOWN = 60  # seconds between resends to the same address

_last_resend: dict[str, float] = {}
_background_tasks: set[asyncio.Task] = set()

CONFIRM_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verify email</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
  main{background:#fff;border-radius:12px;padding:32px;max-width:420px;width:100%;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center}
  button{font:inherit;background:#1c1c1e;color:#fff;border:0;border-radius:8px;padding:10px 20px;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  @media (prefers-color-scheme:dark){body{background:#111;color:#eee}main{background:#1c1c1e}button{background:#eee;color:#111}}
</style></head>
<body><main>
  <h1>Verify your email</h1>
  <p id="msg">Confirm this is your email address to activate your account.</p>
  <button id="go">Verify email</button>
</main>
<script>
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  const msg = document.getElementById("msg"), btn = document.getElementById("go");
  history.replaceState(null, "", location.pathname);  // drop the token from the address bar / history
  if (!token) { msg.textContent = "This link is missing its token. Request a new verification email."; btn.hidden = true; }
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const r = await fetch(location.pathname, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
      const body = await r.json().catch(() => ({}));
      if (r.ok) { msg.textContent = "Email verified. Redirecting…"; btn.hidden = true; setTimeout(() => location.href = "/", 1500); }
      else { msg.textContent = body.detail || "Verification failed."; btn.disabled = false; }
    } catch { msg.textContent = "Network error, try again."; btn.disabled = false; }
  };
</script>
</body></html>
"""


def _format_duration(seconds: int) -> str:
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


class Event:
    class Valves(BaseModel):
        base_url: str = Field(
            default="http://localhost:8082", description="Public URL of Open WebUI, used to build the link."
        )
        token_max_age_seconds: int = Field(default=2 * 3600, description="How long a verification link stays valid.")
        verified_db_path: str = Field(
            default="/app/backend/data/email_verification.db",
            description="SQLite file recording verified user ids (keep it on the data volume).",
        )
        smtp_host: str = Field(default="", description="Empty → $SMTP_HOST, else smtp.gmail.com.")
        smtp_port: int = Field(default=0, description="0 → $SMTP_PORT, else 465 (implicit TLS).")
        smtp_user: str = Field(default="", description="Empty → $SMTP_USER.")
        smtp_password: str = Field(default="", description="Empty → $SMTP_PASSWORD (recommended).")
        smtp_from: str = Field(default="", description="Empty → $SMTP_FROM, else the SMTP user.")

    def __init__(self):
        self.valves = self.Valves()

    # ---- settings -------------------------------------------------------------------------

    def _smtp(self) -> dict:
        v = self.valves
        user = v.smtp_user or os.environ.get("SMTP_USER", "")
        return {
            "host": v.smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            "port": v.smtp_port or int(os.environ.get("SMTP_PORT", "465")),
            "user": user,
            "password": v.smtp_password or os.environ.get("SMTP_PASSWORD", ""),
            "from": v.smtp_from or os.environ.get("SMTP_FROM", "") or user,
        }

    def _serializer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(os.environ["WEBUI_SECRET_KEY"], salt="open-webui-email-verify")

    # ---- verified-user store --------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.valves.verified_db_path, timeout=10)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT PRIMARY KEY, verified_at INTEGER NOT NULL)"
        )
        return conn

    def _is_verified_sync(self, user_id: str) -> bool:
        with self._db() as conn:
            return conn.execute("SELECT 1 FROM verified_users WHERE user_id = ?", (user_id,)).fetchone() is not None

    def _mark_verified_sync(self, user_id: str) -> bool:
        """Records user_id as verified; returns False if it already was (so each user is promoted at most once)."""
        with self._db() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO verified_users (user_id, verified_at) VALUES (?, ?)",
                (user_id, int(time.time())),
            )
            return cursor.rowcount == 1

    # ---- email ------------------------------------------------------------------------------

    def _send_email_sync(self, to_address: str, subject: str, body: str) -> None:
        smtp = self._smtp()
        message = EmailMessage()
        message["From"] = smtp["from"]
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP_SSL(smtp["host"], smtp["port"]) as client:
            client.login(smtp["user"], smtp["password"])
            client.send_message(message)

    async def _send_verification(self, user_id: str, email: str, name: str) -> None:
        token = self._serializer().dumps(user_id)
        link = f"{self.valves.base_url.rstrip('/')}{VERIFY_PATH}#token={token}"
        body = (
            f"Hi {name},\n\n"
            f"Verify your email to activate your Open WebUI account:\n{link}\n\n"
            f"This link expires in {_format_duration(self.valves.token_max_age_seconds)}.\n"
        )
        try:
            await asyncio.to_thread(self._send_email_sync, email, "Verify your Open WebUI account", body)
            log.info("Sent verification email to %s", email)
        except Exception:
            log.exception("Failed to send verification email to %s", email)

    def _send_in_background(self, user_id: str, email: str, name: str) -> None:
        task = asyncio.create_task(self._send_verification(user_id, email, name))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    # ---- HTTP endpoints -----------------------------------------------------------------------

    async def _confirm_page(self):
        return HTMLResponse(CONFIRM_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def _verify(self, token: str = Body(..., embed=True)):
        try:
            user_id = self._serializer().loads(token, max_age=self.valves.token_max_age_seconds)
        except SignatureExpired:
            raise HTTPException(400, detail="Verification link has expired. Request a new one.")
        except BadSignature:
            raise HTTPException(400, detail="Invalid verification token.")

        user = await Users.get_user_by_id(user_id)
        if not user:
            raise HTTPException(400, detail="Invalid verification token.")
        # Only a first-time verification promotes; a pending user who was verified before was
        # put back to pending by an admin (suspended), and stays that way.
        if user.role == "pending" and await asyncio.to_thread(self._mark_verified_sync, user.id):
            user = await Users.update_user_role_by_id(user.id, "user")
            log.info("Verified %s, role pending -> user", user.email)
        return {"status": "verified", "email": user.email, "role": user.role}

    async def _resend(self, email: str = Body(..., embed=True)):
        email = email.lower()
        # Same response whether or not the address exists, so this can't be used to probe accounts.
        response = {"status": "If a pending account exists for that email, a verification link was sent."}

        now = time.monotonic()
        if now - _last_resend.get(email, 0) < RESEND_COOLDOWN:
            return response
        _last_resend[email] = now

        user = await Users.get_user_by_email(email)
        if user and user.role == "pending" and not await asyncio.to_thread(self._is_verified_sync, user.id):
            self._send_in_background(user.id, user.email, user.name)
        return response

    def _register_routes(self, app) -> None:
        """(Re)installs this Function's routes; replaces any from an older copy of the module."""
        routes = app.router.routes
        routes[:] = [r for r in routes if getattr(r, "name", None) not in {f"{ROUTE_TAG}_{n}" for n in ("page", "verify", "resend")}]
        # Insert at the front so these win over the SPA static mount at "/".
        for path, endpoint, methods, name in (
            (VERIFY_PATH, self._confirm_page, ["GET"], "page"),
            (VERIFY_PATH, self._verify, ["POST"], "verify"),
            (RESEND_PATH, self._resend, ["POST"], "resend"),
        ):
            routes.insert(
                0, APIRoute(path, endpoint, methods=methods, name=f"{ROUTE_TAG}_{name}", response_class=JSONResponse)
            )
        self._routes_for = id(app)
        log.info("Registered email verification routes")

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_routes_for", None) != id(__app__):
            self._register_routes(__app__)

        if event.get("event") != "auth.signup":
            return

        user_id = (event.get("subject") or {}).get("id")
        if not user_id:
            return
        user = await Users.get_user_by_id(user_id)
        if user and user.role == "pending":
            self._send_in_background(user.id, user.email, user.name)
