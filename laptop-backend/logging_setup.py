"""Logging for laptop-backend, set from environment variables (read once, at start):

LOG_LEVEL         DEBUG, INFO (default), WARNING, ERROR or CRITICAL. Applies to the app's own logs and
                  to uvicorn's (at WARNING the per-request lines, which are INFO, disappear).
LOG_FILE          Path of a log file, in addition to the console (stderr, i.e. `docker logs`). Empty
                  (default) = console only. The file is rotated.
LOG_FILE_MAX_MB   Size at which the file is rotated (default 10).
LOG_FILE_BACKUPS  Rotated files kept next to it: app.log.1 … (default 5).

A wrong LOG_LEVEL falls back to INFO, and a LOG_FILE that can't be opened to the console only; both
say so in the log instead of stopping the app.
"""
import logging
import logging.handlers
import os
import sys
from typing import Mapping

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
# Uvicorn's loggers keep their own handlers and don't pass records on to the root logger, so they
# get the level and the file handler directly.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_OURS = "_laptop_backend_handler"


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


def configure(env: Mapping[str, str] = os.environ) -> None:
    """Applies the settings above; calling it again replaces what an earlier call set up."""
    asked = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    asked = "WARNING" if asked == "WARN" else asked
    level = getattr(logging, asked) if asked in LEVELS else logging.INFO
    formatter = logging.Formatter(FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)

    path = (env.get("LOG_FILE") or "").strip()
    file_handler, file_error = None, None
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                path, encoding="utf-8",
                maxBytes=_positive_int(env, "LOG_FILE_MAX_MB", 10) * 1024 * 1024,
                backupCount=_positive_int(env, "LOG_FILE_BACKUPS", 5),
            )
            file_handler.setFormatter(formatter)
        except OSError as exc:
            file_error = exc

    root = logging.getLogger()
    loggers = [root, *(logging.getLogger(name) for name in UVICORN_LOGGERS)]
    old = {h for lg in loggers for h in lg.handlers if getattr(h, _OURS, False)}
    for lg in loggers:
        for h in old:
            lg.removeHandler(h)
    for h in old:
        h.close()

    console._laptop_backend_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)
    if file_handler:
        file_handler._laptop_backend_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    for lg in loggers:
        lg.setLevel(level)
        if file_handler and lg is not root:
            lg.addHandler(file_handler)

    log = logging.getLogger(__name__)
    if asked not in LEVELS and asked != "":
        log.warning("Unknown LOG_LEVEL %r (use %s); using INFO.", asked, ", ".join(LEVELS))
    if file_error:
        log.warning("Cannot write LOG_FILE %s (%s); logging to the console only.", path, file_error)
    log.info("Logging at %s%s", logging.getLevelName(level), f", also to {path}" if file_handler else "")
