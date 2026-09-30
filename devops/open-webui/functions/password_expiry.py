"""
title: Password Expiry
author: akshatz
version: 1.2.0
required_open_webui_version: 0.11.3
description: Blocks password sign-in once a password is older than max_age_days (default 180) and emails reminders before it expires.

Open WebUI event Function (Admin → Functions → import this file, then enable it). Pair it with the
Password Reset Function — an expired user's way back in is /api/v1/auths/forgot-password (or an admin
setting a new password in Admin → Users).

NIST SP 800-63B advises against forced periodic password changes (they push people toward predictable
passwords); this exists because a policy asked for it. Prefer MFA via an identity provider if you can.

How it works:
- Open WebUI doesn't record when a password was set, so this keeps its own record in the
  `fn_password_age` table in Open WebUI's database (created by the repo's separate Function
  migrations, devops/open-webui/migrations/, run by the `open-webui-fn-migrate` compose service):
  `auth.signup` and `auth.password_changed` (self-service change, admin change, or a reset through the
  Password Reset Function) set it to now. Accounts with no record yet (existing users when this is
  first enabled) start their clock from their account creation date (`start_from_account_creation`) —
  Open WebUI doesn't record password changes, so creation is the earliest the password can date from —
  but always get at least `grace_days_for_existing` days (with reminders) from when the Function first
  sees them, so enabling it never locks anyone out without warning. With `start_from_account_creation`
  off, their clock starts at that first sighting instead.
- Functions can't veto a login, so on `system.startup.completed` it wraps the ASGI app of Open WebUI's
  own POST /api/v1/auths/signin route. For an expired account, a *correct* password gets a 403 telling
  the user to reset it; a wrong password falls through to Open WebUI's normal error, so this never
  reveals whether an account exists or has expired.
- On `auth.login`, if the password expires within `warn_days`, the user is emailed a reminder (at most
  once per day).

Limits: only the email/password form is checked (not LDAP, OAuth, trusted-header or API keys), and
sessions that are already signed in stay valid until they expire (JWT_EXPIRES_IN, default 4 weeks).
"""

import asyncio
import json
import logging
import math
import os
import smtplib
import time
from email.message import EmailMessage

from pydantic import BaseModel, Field
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.auths import Auths
from open_webui.models.users import Users
from open_webui.utils.auth import verify_password

log = logging.getLogger("password_expiry")

SIGNIN_PATH = "/api/v1/auths/signin"
FORGOT_PATH = "/api/v1/auths/forgot-password"
DAY = 24 * 3600

_background_tasks: set[asyncio.Task] = set()


class Event:
    class Valves(BaseModel):
        max_age_days: int = Field(default=180, description="Days after which a password must be changed.")
        warn_days: int = Field(default=14, description="Email a reminder at sign-in this many days before expiry.")
        start_from_account_creation: bool = Field(
            default=True,
            description="For accounts with no record yet, count password age from account creation "
            "(off: from when this Function first sees them).",
        )
        grace_days_for_existing: int = Field(
            default=14,
            description="Accounts with no record yet get at least this many days before expiring, so "
            "old accounts aren't locked out the moment this is enabled.",
        )
        exempt_admins: bool = Field(
            default=False, description="Skip expiry for admins (avoids locking out the only admin)."
        )
        base_url: str = Field(
            default="http://localhost:8082", description="Public URL of Open WebUI, used to build links in emails."
        )
        smtp_host: str = Field(default="", description="Empty → $SMTP_HOST, else smtp.gmail.com.")
        smtp_port: int = Field(default=0, description="0 → $SMTP_PORT, else 465 (implicit TLS).")
        smtp_user: str = Field(default="", description="Empty → $SMTP_USER.")
        smtp_password: str = Field(default="", description="Empty → $SMTP_PASSWORD (recommended).")
        smtp_from: str = Field(default="", description="Empty → $SMTP_FROM, else the SMTP user.")

    def __init__(self):
        self.valves = self.Valves()

    # ---- password-age store (fn_password_age in Open WebUI's database) ------------------------

    async def _set_changed(self, user_id: str) -> None:
        async with get_async_db_context() as session:
            await session.execute(
                text(
                    "INSERT INTO fn_password_age (user_id, changed_at) VALUES (:u, :t) "
                    "ON CONFLICT (user_id) DO UPDATE SET changed_at = excluded.changed_at, last_warned_at = 0"
                ),
                {"u": user_id, "t": int(time.time())},
            )
            await session.commit()

    def _baseline(self, user) -> int:
        """Where the clock starts for an account with no record yet (see the module docstring)."""
        now = int(time.time())
        created_at = getattr(user, "created_at", None)
        if not self.valves.start_from_account_creation or not created_at:
            return now
        # Latest start that still leaves grace_days_for_existing days before expiry.
        latest = now - max(0, self.valves.max_age_days - self.valves.grace_days_for_existing) * DAY
        return min(now, max(int(created_at), latest))

    async def _changed_at(self, user) -> int:
        """When the password was last set; records a baseline for accounts with no record yet."""
        async with get_async_db_context() as session:
            await session.execute(
                text("INSERT INTO fn_password_age (user_id, changed_at) VALUES (:u, :t) ON CONFLICT (user_id) DO NOTHING"),
                {"u": user.id, "t": self._baseline(user)},
            )
            await session.commit()
            result = await session.execute(
                text("SELECT changed_at FROM fn_password_age WHERE user_id = :u"), {"u": user.id}
            )
            return result.scalar_one()

    async def _claim_warning(self, user_id: str) -> bool:
        """True (and records it) if no reminder went out in the last day."""
        now = int(time.time())
        async with get_async_db_context() as session:
            result = await session.execute(
                text("UPDATE fn_password_age SET last_warned_at = :now WHERE user_id = :u AND last_warned_at < :cutoff"),
                {"now": now, "u": user_id, "cutoff": now - DAY},
            )
            await session.commit()
            return result.rowcount == 1

    async def _seconds_left(self, user) -> float | None:
        """Seconds until the password expires, or None if this user is exempt."""
        if self.valves.exempt_admins and user.role == "admin":
            return None
        changed_at = await self._changed_at(user)
        return changed_at + self.valves.max_age_days * DAY - time.time()

    # ---- email ------------------------------------------------------------------------------

    def _send_email_sync(self, to_address: str, subject: str, body: str) -> None:
        v = self.valves
        user = v.smtp_user or os.environ.get("SMTP_USER", "")
        message = EmailMessage()
        message["From"] = v.smtp_from or os.environ.get("SMTP_FROM", "") or user
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)

        host = v.smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
        port = v.smtp_port or int(os.environ.get("SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(host, port) as client:
            client.login(user, v.smtp_password or os.environ.get("SMTP_PASSWORD", ""))
            client.send_message(message)

    async def _send(self, to_address: str, subject: str, body: str) -> None:
        try:
            await asyncio.to_thread(self._send_email_sync, to_address, subject, body)
            log.info("Sent password expiry reminder to %s", to_address)
        except Exception:
            log.exception("Failed to send password expiry reminder to %s", to_address)

    # ---- sign-in guard ------------------------------------------------------------------------

    async def _expired_signin(self, body: bytes) -> bool:
        """True only for a correct password on an expired, non-exempt account."""
        try:
            form = json.loads(body)
            email, password = str(form["email"]).strip().lower(), str(form["password"])
        except Exception:
            return False  # let Open WebUI produce its usual validation error
        user = await Users.get_user_by_email(email)
        if not user:
            return False
        left = await self._seconds_left(user)
        if left is None or left > 0:
            return False
        # Only reveal expiry to someone who knows the password.
        return bool(await Auths.authenticate_user(email, lambda pw: verify_password(password, pw)))

    def _wrap_signin(self, app) -> None:
        route = next(
            (r for r in app.router.routes if getattr(r, "path", None) == SIGNIN_PATH and "POST" in (r.methods or ())),
            None,
        )
        if route is None:
            log.error("Password expiry not enforced: %s route not found", SIGNIN_PATH)
            return
        # Wrap Open WebUI's original handler, not a wrapper left by an older copy of this module.
        original = getattr(route.app, "__password_expiry_original__", route.app)
        guard = self

        async def signin_with_expiry(scope, receive, send):
            chunks, more = [], True
            while more:
                message = await receive()
                chunks.append(message.get("body", b""))
                more = message.get("more_body", False)
            body = b"".join(chunks)

            try:
                expired = await guard._expired_signin(body)
            except Exception:
                log.exception("Password expiry check failed; allowing sign-in")
                expired = False

            if expired:
                detail = (
                    f"Your password is more than {guard.valves.max_age_days} days old and has expired. "
                    f"Set a new one at {guard.valves.base_url.rstrip('/')}{FORGOT_PATH}"
                )
                payload = json.dumps({"detail": detail}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(payload)).encode())]})
                await send({"type": "http.response.body", "body": payload})
                return

            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await original(scope, replay, send)

        signin_with_expiry.__password_expiry_original__ = original
        route.app = signin_with_expiry
        self._wrapped_for = id(app)
        log.info("Password expiry enforced on %s (max age %d days)", SIGNIN_PATH, self.valves.max_age_days)

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_wrapped_for", None) != id(__app__):
            self._wrap_signin(__app__)

        name = event.get("event")
        user_id = (event.get("subject") or {}).get("id")
        if not user_id:
            return

        if name in ("auth.signup", "auth.password_changed"):
            await self._set_changed(user_id)
            return

        if name == "auth.login":
            user = await Users.get_user_by_id(user_id)
            left = await self._seconds_left(user) if user else None
            if left is None or not (0 < left <= self.valves.warn_days * DAY):
                return
            if not await self._claim_warning(user.id):
                return
            days = max(1, math.ceil(left / DAY))
            body = (
                f"Hi {user.name},\n\n"
                f"Your Open WebUI password expires in {days} day{'s' if days != 1 else ''}. After that you won't be "
                f"able to sign in until you set a new one.\n\n"
                f"Change it now in Open WebUI under Settings → Account, or reset it at\n"
                f"{self.valves.base_url.rstrip('/')}{FORGOT_PATH}\n"
            )
            task = asyncio.create_task(self._send(user.email, "Your Open WebUI password expires soon", body))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
