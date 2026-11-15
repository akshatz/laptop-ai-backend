import logging
import os
import time

import logging_setup

logging_setup.configure()  # LOG_LEVEL, LOG_FILE, … (before the routers, so their import-time logs count)

from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from routers import admin, auth, chats, sso

app = FastAPI(title="Laptop Dev Backend Engine", default_response_class=ORJSONResponse)

# One line per request from laptop-frontend (through its nginx): method, path, status and how long the
# backend took, e.g. "POST /chats/<id>/messages -> 201 in 48213 ms" (answers on CPU take a while).
# Path only, never the query string (the sign-in callback carries a code there). LOG_REQUEST_TIMING=false
# turns it off; /health is left out.
timing_log = logging.getLogger("timing")
LOG_REQUEST_TIMING = os.environ.get("LOG_REQUEST_TIMING", "true").strip().lower() not in ("false", "0", "no")

if LOG_REQUEST_TIMING:
    @app.middleware("http")
    async def log_request_timing(request: Request, call_next):
        started = time.perf_counter()
        status_code = 500  # if the handler raises
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            if request.url.path != "/health":
                timing_log.info("%s %s -> %s in %.0f ms", request.method, request.url.path, status_code,
                                (time.perf_counter() - started) * 1000)

# API only: the chat UI is the separate laptop-frontend app.
app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(sso.router)
app.include_router(admin.router)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": "laptop-sandbox"}
