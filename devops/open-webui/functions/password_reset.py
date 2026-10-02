"""
title: Password Reset
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Self-service "forgot password" by email, plus an email notice whenever a password changes.

Open WebUI event Function (Admin → Functions → import this file, then enable it). Independent of the
Signup Email Verification Function; both can be enabled together.

- On `system.startup.completed` (and, defensively, on any event) it registers routes on Open WebUI's
  own app, since event Functions can't declare HTTP routes themselves:
    GET  /api/v1/auths/forgot-password     page asking for the account's email
    POST /api/v1/auths/forgot-password     {"email": "..."} → emails a reset link (rate-limited, same
                                           response whether or not the account exists)
    GET  /api/v1/auths/reset-password      "set new password" page; the token is in the URL fragment
                                           (#token=...), so it never reaches server/access logs
    POST /api/v1/auths/reset-password      {"token": "...", "password": "..."} → sets the password
- On `auth.password_changed` (self-service change, admin change, or a reset here) it emails the user a
  notice, so a change they didn't make doesn't go unnoticed.

Reset links are signed with WEBUI_SECRET_KEY, expire after `token_max_age_seconds`, and carry a
fingerprint of the current password hash, so each link works once: after any password change, older
links stop working. Pending (unverified or suspended) and deactivated accounts can't reset.

After a reset, Open WebUI's revoke_user_tokens logs out the user's other sessions — only when Redis is
configured; without it, existing sessions stay valid until they expire.

SMTP settings are Valves; empty values fall back to the SMTP_* environment variables so the password
can stay in the container environment instead of the Functions table.
"""

import asyncio
import hashlib
import logging
import os
import smtplib
import time
from email.message import EmailMessage

from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_db_context
from open_webui.models.auths import Auth, Auths
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash, revoke_user_tokens, validate_password

log = logging.getLogger("password_reset")

FORGOT_PATH = "/api/v1/auths/forgot-password"
RESET_PATH = "/api/v1/auths/reset-password"
ROUTE_TAG = "password_reset"
REQUEST_COOLDOWN = 60  # seconds between reset emails to the same address

_last_request: dict[str, float] = {}
_background_tasks: set[asyncio.Task] = set()

PAGE_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
  main{background:#fff;border-radius:12px;padding:32px;max-width:420px;width:100%;box-sizing:border-box;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center}
  button{font:inherit;background:#1c1c1e;color:#fff;border:0;border-radius:8px;padding:10px 20px;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  input{font:inherit;width:100%;box-sizing:border-box;padding:10px;margin:0 0 12px;border:1px solid #ccc;border-radius:8px;background:inherit;color:inherit}
  a{color:inherit}
  @media (prefers-color-scheme:dark){body{background:#111;color:#eee}main{background:#1c1c1e}button{background:#eee;color:#111}input{border-color:#444}}
</style></head>
"""

FORGOT_PAGE = PAGE_HEAD.replace("{title}", "Forgot password") + """
<body><main>
  <h1>Forgot password</h1>
  <p id="msg">Enter your account's email and we'll send you a link to set a new password.</p>
  <form id="f">
    <input id="email" type="email" required autocomplete="email" placeholder="you@example.com">
    <button id="go">Send reset link</button>
  </form>
  <p><a href="/">Back to Open WebUI</a></p>
</main>
<script>
  const f = document.getElementById("f"), msg = document.getElementById("msg"), btn = document.getElementById("go");
  f.onsubmit = async (e) => {
    e.preventDefault();
    btn.disabled = true;
    try {
      const r = await fetch(location.pathname, {method: "POST", headers: {"Content-Type": "application/json"},
                                                body: JSON.stringify({email: document.getElementById("email").value})});
      const body = await r.json().catch(() => ({}));
      msg.textContent = r.ok ? body.status + " Check your inbox (and spam folder)." : (body.detail || "Something went wrong.");
      if (r.ok) f.hidden = true; else btn.disabled = false;
    } catch { msg.textContent = "Network error, try again."; btn.disabled = false; }
  };
</script>
</body></html>
"""

RESET_PAGE = PAGE_HEAD.replace("{title}", "Set a new password") + """
<body><main>
  <h1>Set a new password</h1>
  <p id="msg">Choose a new password for your Open WebUI account.</p>
  <form id="f">
    <input id="pw" type="password" required autocomplete="new-password" placeholder="New password">
    <input id="pw2" type="password" required autocomplete="new-password" placeholder="Confirm new password">
    <button id="go">Set password</button>
  </form>
  <p><a href="/">Back to Open WebUI</a></p>
</main>
<script>
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  const f = document.getElementById("f"), msg = document.getElementById("msg"), btn = document.getElementById("go");
  history.replaceState(null, "", location.pathname);  // drop the token from the address bar / history
  if (!token) { msg.innerHTML = 'This link is missing its token. <a href="FORGOT_PATH">Request a new one</a>.'; f.hidden = true; }
  f.onsubmit = async (e) => {
    e.preventDefault();
    const password = document.getElementById("pw").value;
    if (password !== document.getElementById("pw2").value) { msg.textContent = "The passwords don't match."; return; }
    btn.disabled = true;
    try {
      const r = await fetch(location.pathname, {method: "POST", headers: {"Content-Type": "application/json"},
                                                body: JSON.stringify({token, password})});
      const body = await r.json().catch(() => ({}));
      if (r.ok) { msg.textContent = "Password updated. Redirecting to sign in…"; f.hidden = true; setTimeout(() => location.href = "/auth", 1500); }
      else { msg.textContent = body.detail || "Couldn't update the password."; btn.disabled = false; }
    } catch { msg.textContent = "Network error, try again."; btn.disabled = false; }
  };
</script>
</body></html>
""".replace("FORGOT_PATH", FORGOT_PATH)


def _format_duration(seconds: int) -> str:
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class Event:
    class Valves(BaseModel):
        base_url: str = Field(
            default="http://localhost:8082", description="Public URL of Open WebUI, used to build links in emails."
        )
        token_max_age_seconds: int = Field(default=30 * 60, description="How long a reset link stays valid.")
        min_password_length: int = Field(
            default=8, description="Minimum new-password length (on top of Open WebUI's own password rules)."
        )
        notify_on_password_change: bool = Field(
            default=True, description="Email users whenever their password changes."
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
        return URLSafeTimedSerializer(os.environ["WEBUI_SECRET_KEY"], salt="open-webui-password-reset")

    def _url(self, path: str) -> str:
        return f"{self.valves.base_url.rstrip('/')}{path}"

    # ---- account lookup -------------------------------------------------------------------

    async def _resettable(self, user_id: str):
        """Returns (user, password_hash) if this account may reset its password, else None."""
        if os.environ.get("WEBUI_AUTH_TRUSTED_EMAIL_HEADER"):
            return None  # passwords are delegated to the reverse proxy in trusted-header mode
        user = await Users.get_user_by_id(user_id)
        if not user or user.role == "pending":
            return None
        async with get_async_db_context() as session:
            credential = await session.get(Auth, user_id)
            if not credential or not credential.active or not credential.password:
                return None
            return user, credential.password

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

    async def _send(self, to_address: str, subject: str, body: str, what: str) -> None:
        try:
            await asyncio.to_thread(self._send_email_sync, to_address, subject, body)
            log.info("Sent %s email to %s", what, to_address)
        except Exception:
            log.exception("Failed to send %s email to %s", what, to_address)

    def _send_in_background(self, to_address: str, subject: str, body: str, what: str) -> None:
        task = asyncio.create_task(self._send(to_address, subject, body, what))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    # ---- HTTP endpoints -----------------------------------------------------------------------

    async def _forgot_page(self):
        return HTMLResponse(FORGOT_PAGE, headers={"Cache-Control": "no-store"})

    async def _reset_page(self):
        return HTMLResponse(RESET_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def _forgot(self, email: str = Body(..., embed=True)):
        email = email.strip().lower()
        # Same response whether or not the address exists, so this can't be used to probe accounts.
        response = {"status": "If an account exists for that email, a password reset link was sent."}

        now = time.monotonic()
        if now - _last_request.get(email, 0) < REQUEST_COOLDOWN:
            return response
        _last_request[email] = now

        user = await Users.get_user_by_email(email)
        found = await self._resettable(user.id) if user else None
        if found:
            user, password_hash = found
            token = self._serializer().dumps({"id": user.id, "fp": _fingerprint(password_hash)})
            body = (
                f"Hi {user.name},\n\n"
                f"Someone asked to reset the password for your Open WebUI account. To choose a new one, open:\n"
                f"{self._url(RESET_PATH)}#token={token}\n\n"
                f"This link expires in {_format_duration(self.valves.token_max_age_seconds)} and works once.\n"
                f"If you didn't ask for this, ignore this email; your password stays the same.\n"
            )
            self._send_in_background(user.email, "Reset your Open WebUI password", body, "password reset")
        return response

    async def _reset(self, request: Request, token: str = Body(...), password: str = Body(...)):
        invalid = HTTPException(400, detail="This reset link is invalid or was already used. Request a new one.")
        try:
            payload = self._serializer().loads(token, max_age=self.valves.token_max_age_seconds)
        except SignatureExpired:
            raise HTTPException(400, detail="This reset link has expired. Request a new one.")
        except BadSignature:
            raise invalid

        found = await self._resettable(payload.get("id", ""))
        # The fingerprint ties the link to the password it was issued for, so it stops working
        # once the password changes (including through this very link).
        if not found or _fingerprint(found[1]) != payload.get("fp"):
            raise invalid
        user = found[0]

        if len(password) < self.valves.min_password_length:
            raise HTTPException(400, detail=f"Use at least {self.valves.min_password_length} characters.")
        try:
            validate_password(password)
        except Exception as e:
            raise HTTPException(400, detail=str(e))

        if not await Auths.update_user_password_by_id(user.id, await get_password_hash(password)):
            raise invalid
        await revoke_user_tokens(request, user.id)
        await publish_event(request, EVENTS.AUTH_PASSWORD_CHANGED, actor=user, subject_id=user.id, subject_type="user")
        log.info("Password reset for %s", user.email)
        return {"status": "password_updated"}

    def _register_routes(self, app) -> None:
        """(Re)installs this Function's routes; replaces any from an older copy of the module."""
        routes = app.router.routes
        route_names = {f"{ROUTE_TAG}_{n}" for n in ("forgot_page", "forgot", "reset_page", "reset")}
        routes[:] = [r for r in routes if getattr(r, "name", None) not in route_names]
        # Insert at the front so these win over the SPA static mount at "/".
        for path, endpoint, methods, name in (
            (FORGOT_PATH, self._forgot_page, ["GET"], "forgot_page"),
            (FORGOT_PATH, self._forgot, ["POST"], "forgot"),
            (RESET_PATH, self._reset_page, ["GET"], "reset_page"),
            (RESET_PATH, self._reset, ["POST"], "reset"),
        ):
            routes.insert(
                0, APIRoute(path, endpoint, methods=methods, name=f"{ROUTE_TAG}_{name}", response_class=JSONResponse)
            )
        self._routes_for = id(app)
        log.info("Registered password reset routes")

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_routes_for", None) != id(__app__):
            self._register_routes(__app__)

        if event.get("event") != "auth.password_changed" or not self.valves.notify_on_password_change:
            return

        user_id = (event.get("subject") or {}).get("id")
        user = await Users.get_user_by_id(user_id) if user_id else None
        if not user:
            return
        body = (
            f"Hi {user.name},\n\n"
            f"The password for your Open WebUI account was just changed.\n\n"
            f"If this was you, there's nothing to do. If it wasn't, reset your password now at\n"
            f"{self._url(FORGOT_PATH)}\n"
            f"and tell your Open WebUI admin.\n"
        )
        self._send_in_background(user.email, "Your Open WebUI password was changed", body, "password changed")
