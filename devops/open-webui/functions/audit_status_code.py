"""
title: Audit Status Code
author: akshatz
version: 1.0.0
required_open_webui_version: 0.11.3
description: Records the HTTP response status in Open WebUI's audit log at every audit level, not only REQUEST_RESPONSE.

Open WebUI event Function (Admin → Functions → import this file, then enable it, then restart
open-webui once so it's active from the first request).

Open WebUI's audit middleware (open_webui/utils/audit.py, AuditLoggingMiddleware) only looks at the
response when AUDIT_LOG_LEVEL=REQUEST_RESPONSE, so at METADATA (this stack's level) every audit entry
has "response_status_code": null and a failed sign-in can't be told from a successful one. Raising
the level isn't an option: REQUEST_RESPONSE also logs request and response bodies.

This patches the middleware class, which also covers the instance already in the running app,
since Python looks up __call__ and _log_audit_entry on the class for every request:
- __call__ wraps `send` to note the status of `http.response.start` in the request's ASGI scope
  (the same dict the middleware's Request reads), nothing else — no bodies are touched.
- _log_audit_entry copies that status into the entry when Open WebUI left it empty, so at
  REQUEST_RESPONSE Open WebUI's own value still wins.
Requests that end in an unhandled exception never send a response start through the middleware, so
they keep null (shown as "-" by devops/otel-collector/openwebui-audit.yaml).

The originals are kept on the class (__audit_status_original_call__ / ..._log__), so re-importing the
Function doesn't wrap twice. Disabling the Function doesn't unpatch the running process; restart
open-webui after disabling it. It touches Open WebUI internals, so re-test (an audited request shows
a status in audit.log) when bumping the pinned open-webui image.
"""

import logging

from pydantic import BaseModel

from open_webui.utils.audit import AuditLoggingMiddleware

log = logging.getLogger("audit_status_code")

SCOPE_KEY = "fn_audit_response_status"


def _install() -> bool:
    """Patches AuditLoggingMiddleware once per process; returns True if it did so now."""
    cls = AuditLoggingMiddleware
    if hasattr(cls, "__audit_status_original_call__"):
        return False
    original_call = cls.__call__
    original_log = cls._log_audit_entry

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await original_call(self, scope, receive, send)

        async def send_recording_status(message):
            if message["type"] == "http.response.start":
                scope[SCOPE_KEY] = message["status"]
            await send(message)

        return await original_call(self, scope, receive, send_recording_status)

    async def _log_audit_entry(self, request, context):
        status = request.scope.get(SCOPE_KEY)
        if status is not None and context.metadata.get("response_status_code") is None:
            context.metadata["response_status_code"] = status
        return await original_log(self, request, context)

    cls.__audit_status_original_call__ = original_call
    cls.__audit_status_original_log__ = original_log
    cls.__call__ = __call__
    cls._log_audit_entry = _log_audit_entry
    return True


class Event:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        if _install():
            log.warning("Audit Status Code: audit entries now record the response status")

    async def event(self, event: dict, __app__=None):
        # Installed on load (__init__); repeated here in case the class was reloaded without it.
        _install()
