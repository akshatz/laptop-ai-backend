"""
title: Password Expiry
author: akshatz
version: 1.3.0
required_open_webui_version: 0.11.3
description: Blocks password sign-in once a password is older than max_age_days (default 180), emails the user a reset link, and shows admins an expiry overview.

Open WebUI event Function (Admin → Functions → import this file, then enable it). Pair it with the
Password Reset Function — expired users set a new password through its reset page (or an admin sets
one in Admin → Users).

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
- Admin passwords never expire while `exempt_admins` is on (the default).
- Functions can't veto a login, so on `system.startup.completed` it wraps the ASGI app of Open WebUI's
  own POST /api/v1/auths/signin route. For an expired account, a *correct* password gets a 403 and the
  user is emailed a one-time link to the Password Reset Function's "set a new password" page (Open
  WebUI's login page shows the 403 as an error popup and can't be redirected, so the email is the way
  forward). A wrong password falls through to Open WebUI's normal error, so this never reveals whether
  an account exists or has expired. The link uses Password Reset's token format (same salt and
  password-hash fingerprint), so it only works while that Function is enabled — if it isn't, the
  popup tells the user to ask an admin instead.
- On `auth.login`, if the password expires within `warn_days`, the user is emailed a reminder (at most
  once per day).
- Admin overview at GET /api/v1/auths/password-expiry (add `?format=json` for JSON): every user's
  password date, expiry date, days left and status. Admin-only via Open WebUI's own get_admin_user, which
  accepts the browser's login cookie, so it opens directly while signed in as an admin. Viewing it
  doesn't start anyone's clock: accounts with no record yet show their estimated dates.

Limits: only the email/password form is checked (not LDAP, OAuth, trusted-header or API keys), and
sessions that are already signed in stay valid until they expire (JWT_EXPIRES_IN, default 4 weeks).
"""

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from fastapi import Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from itsdangerous import URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import text

from open_webui.internal.db import get_async_db_context
from open_webui.models.auths import Auth, Auths
from open_webui.models.functions import Functions
from open_webui.models.users import Users
from open_webui.utils.auth import get_admin_user, verify_password

log = logging.getLogger("password_expiry")

SIGNIN_PATH = "/api/v1/auths/signin"
FORGOT_PATH = "/api/v1/auths/forgot-password"
RESET_PATH = "/api/v1/auths/reset-password"  # served by the Password Reset Function
RESET_SALT = "open-webui-password-reset"  # must match the Password Reset Function
RESET_DEFAULT_MAX_AGE = 30 * 60  # the Password Reset Function's default token_max_age_seconds
OVERVIEW_PATH = "/api/v1/auths/password-expiry"
OVERVIEW_ROUTE_NAME = "password_expiry_overview"
DAY = 24 * 3600
EXPIRED_EMAIL_COOLDOWN = 10 * 60  # seconds between reset emails to the same expired account

_background_tasks: set[asyncio.Task] = set()
_last_expired_email: dict[str, float] = {}

OVERVIEW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Password expiry</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f6f6f7;color:#1c1c1e;margin:0;padding:24px 16px}
  main{max-width:960px;margin:0 auto}
  .wrap{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #eee;white-space:nowrap}
  th{font-weight:600;color:#555}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .s{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
  .expired{background:#fde2e1;color:#a4161a}.soon{background:#fff1cc;color:#8a5a00}
  .ok{background:#dcf5e3;color:#1b6b34}.exempt{background:#e8e8ed;color:#555}
  .muted{color:#777;font-size:13px}
  a{color:inherit}
  @media (prefers-color-scheme:dark){
    body{background:#111;color:#eee}.wrap{background:#1c1c1e}th,td{border-color:#2c2c2e}th{color:#aaa}
    .expired{background:#4a1514;color:#ffb3ae}.soon{background:#473500;color:#ffd978}
    .ok{background:#12391f;color:#9be3b0}.exempt{background:#2c2c2e;color:#bbb}.muted{color:#999}}
</style></head>
<body><main>
  <h1>Password expiry</h1>
  <p class="muted">SUMMARY</p>
  <div class="wrap"><table>
    <thead><tr><th>User</th><th>Email</th><th>Role</th><th>Password from</th><th>Expires</th>
      <th style="text-align:right">Days left</th><th>Status</th></tr></thead>
    <tbody>ROWS</tbody>
  </table></div>
  <p class="muted">Dates are UTC. * = no record yet, so the date is estimated (see
    <code>start_from_account_creation</code> / <code>grace_days_for_existing</code>); it's fixed the first time
    the user signs in. <a href="/">Back to Open WebUI</a></p>
</main></body></html>
"""


def _format_duration(seconds: int) -> str:
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


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
            default=True, description="Admin passwords never expire (also avoids locking out the only admin)."
        )
        reset_function_id: str = Field(
            default="password_reset",
            description="ID of the Password Reset Function whose page the expired-password email links to.",
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

    def _url(self, path: str) -> str:
        return f"{self.valves.base_url.rstrip('/')}{path}"

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

    # ---- expired-password reset link --------------------------------------------------------

    async def _reset_link_max_age(self) -> int | None:
        """The Password Reset Function's link lifetime, or None if it isn't installed and enabled."""
        function = await Functions.get_function_by_id(self.valves.reset_function_id)
        if not function or not function.is_active:
            return None
        valves = await Functions.get_function_valves_by_id(self.valves.reset_function_id) or {}
        return int(valves.get("token_max_age_seconds") or RESET_DEFAULT_MAX_AGE)

    async def _handle_expired(self, user, password_hash: str) -> str:
        """Emails the expired user a reset link if possible; returns the sign-in error to show."""
        expired = f"Your password is more than {self.valves.max_age_days} days old and has expired."
        max_age = await self._reset_link_max_age()
        if max_age is None:
            return f"{expired} Ask your Open WebUI admin to set a new one."

        now = time.monotonic()
        if now - _last_expired_email.get(user.id, 0) >= EXPIRED_EMAIL_COOLDOWN:
            _last_expired_email[user.id] = now
            # Same format as the Password Reset Function's tokens, so its reset page accepts it — and
            # the hash fingerprint makes it single-use.
            fingerprint = hashlib.sha256(password_hash.encode()).hexdigest()[:16]
            token = URLSafeTimedSerializer(os.environ["WEBUI_SECRET_KEY"], salt=RESET_SALT).dumps(
                {"id": user.id, "fp": fingerprint}
            )
            body = (
                f"Hi {user.name},\n\n"
                f"Your Open WebUI password is more than {self.valves.max_age_days} days old and has expired, so "
                f"you can't sign in until you set a new one. To choose a new password, open:\n"
                f"{self._url(RESET_PATH)}#token={token}\n\n"
                f"This link expires in {_format_duration(max_age)} and works once. If it has expired, request a "
                f"new one at {self._url(FORGOT_PATH)}\n"
            )
            self._send_in_background(user.email, "Your Open WebUI password has expired", body, "expired password")
        return f"{expired} We've emailed you a link to set a new one (check your spam folder too)."

    # ---- sign-in guard ------------------------------------------------------------------------

    async def _expired_signin(self, body: bytes):
        """(user, password_hash) only for a correct password on an expired, non-exempt account, else None."""
        try:
            form = json.loads(body)
            email, password = str(form["email"]).strip().lower(), str(form["password"])
        except Exception:
            return None  # let Open WebUI produce its usual validation error
        user = await Users.get_user_by_email(email)
        if not user:
            return None
        left = await self._seconds_left(user)
        if left is None or left > 0:
            return None
        # Only reveal expiry to someone who knows the password.
        if not await Auths.authenticate_user(email, lambda pw: verify_password(password, pw)):
            return None
        async with get_async_db_context() as session:
            credential = await session.get(Auth, user.id)
            return (user, credential.password) if credential and credential.password else None

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
                detail = await guard._handle_expired(*expired) if expired else None
            except Exception:
                log.exception("Password expiry check failed; allowing sign-in")
                detail = None

            if detail:
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
        log.info("Password expiry enforced on %s (max age %d days)", SIGNIN_PATH, self.valves.max_age_days)

    # ---- admin overview -----------------------------------------------------------------------

    async def _overview_rows(self) -> list[dict]:
        """One row per user, soonest expiry first. Read-only: doesn't record baselines."""
        async with get_async_db_context() as session:
            result = await session.execute(text("SELECT user_id, changed_at FROM fn_password_age"))
            recorded = {user_id: changed_at for user_id, changed_at in result.all()}
        users = (await Users.get_users())["users"]

        now = time.time()
        rows = []
        for user in users:
            estimated = user.id not in recorded
            changed_at = self._baseline(user) if estimated else recorded[user.id]
            expires_at = changed_at + self.valves.max_age_days * DAY
            exempt = self.valves.exempt_admins and user.role == "admin"
            left = expires_at - now
            if exempt:
                status = "exempt"
            elif left <= 0:
                status = "expired"
            elif left <= self.valves.warn_days * DAY:
                status = "expiring soon"
            else:
                status = "ok"
            rows.append({
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "password_from": int(changed_at),
                "expires_at": None if exempt else int(expires_at),
                "days_left": None if exempt else math.ceil(left / DAY) if left > 0 else 0,
                "status": status,
                "estimated": estimated,
            })
        rows.sort(key=lambda r: (r["expires_at"] is None, r["expires_at"] or 0))
        return rows

    async def _overview(self, format: str = "html", user=Depends(get_admin_user)):
        rows = await self._overview_rows()
        if format == "json":
            return JSONResponse({"max_age_days": self.valves.max_age_days, "users": rows},
                                headers={"Cache-Control": "no-store"})

        day = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        css = {"expired": "expired", "expiring soon": "soon", "ok": "ok", "exempt": "exempt"}
        body = []
        for r in rows:
            star = "*" if r["estimated"] else ""
            body.append(
                "<tr>"
                f"<td>{html.escape(r['name'])}</td><td>{html.escape(r['email'])}</td><td>{html.escape(r['role'])}</td>"
                f"<td>{day(r['password_from'])}{star}</td>"
                f"<td>{'—' if r['expires_at'] is None else day(r['expires_at']) + star}</td>"
                f"<td class=\"num\">{'—' if r['days_left'] is None else r['days_left']}</td>"
                f"<td><span class=\"s {css[r['status']]}\">{r['status']}</span></td>"
                "</tr>"
            )
        counts = {s: sum(r["status"] == s for r in rows) for s in ("expired", "expiring soon", "ok", "exempt")}
        summary = (
            f"Passwords expire after {self.valves.max_age_days} days"
            f"{' (admins exempt)' if self.valves.exempt_admins else ''}. "
            + ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        )
        page = OVERVIEW_PAGE.replace("SUMMARY", html.escape(summary)).replace(
            "ROWS", "".join(body) or '<tr><td colspan="7">No users.</td></tr>'
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    def _register_routes(self, app) -> None:
        """(Re)installs the overview route; replaces one from an older copy of the module."""
        routes = app.router.routes
        routes[:] = [r for r in routes if getattr(r, "name", None) != OVERVIEW_ROUTE_NAME]
        # Insert at the front so it wins over the SPA static mount at "/".
        routes.insert(0, APIRoute(OVERVIEW_PATH, self._overview, methods=["GET"], name=OVERVIEW_ROUTE_NAME))

    # ---- event hook ---------------------------------------------------------------------------

    async def event(self, event: dict, __app__=None):
        if __app__ is not None and getattr(self, "_wrapped_for", None) != id(__app__):
            self._wrap_signin(__app__)
            self._register_routes(__app__)
            self._wrapped_for = id(__app__)

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
                f"{self._url(FORGOT_PATH)}\n"
            )
            self._send_in_background(user.email, "Your Open WebUI password expires soon", body, "password expiry reminder")
