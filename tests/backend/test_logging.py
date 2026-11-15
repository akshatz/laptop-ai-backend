"""logging_setup: LOG_LEVEL, LOG_FILE, LOG_FILE_MAX_MB and LOG_FILE_BACKUPS."""
import logging
import logging.handlers

import pytest

import logging_setup


@pytest.fixture(autouse=True)
def restore_logging():
    names = ["", *logging_setup.UVICORN_LOGGERS]
    saved = {n: (logging.getLogger(n).level, list(logging.getLogger(n).handlers)) for n in names}
    yield
    for n, (level, handlers) in saved.items():
        lg = logging.getLogger(n)
        for h in list(lg.handlers):
            if h not in handlers:
                lg.removeHandler(h)
                h.close()
        lg.setLevel(level)


def flush():
    for h in logging.getLogger().handlers:
        h.flush()


def test_level_filters_the_file_and_uvicorn(tmp_path):
    path = tmp_path / "logs" / "app.log"  # the folder is created
    logging_setup.configure({"LOG_LEVEL": "warning", "LOG_FILE": str(path)})
    logging.getLogger("app").info("quiet")
    logging.getLogger("app").warning("loud")
    logging.getLogger("uvicorn.access").info("GET /health")
    logging.getLogger("uvicorn.error").error("broken")
    flush()
    text = path.read_text()
    assert "loud" in text and "broken" in text and "quiet" not in text and "GET /health" not in text
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING


def test_default_is_info_and_console_only():
    logging_setup.configure({})
    assert logging.getLogger().level == logging.INFO
    assert not [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


def test_wrong_level_falls_back_to_info_and_says_so(caplog):
    with caplog.at_level(logging.WARNING):
        logging_setup.configure({"LOG_LEVEL": "LOUD"})
    assert logging.getLogger().level == logging.INFO
    assert "Unknown LOG_LEVEL 'LOUD'" in caplog.text


def test_unwritable_file_means_console_only(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        logging_setup.configure({"LOG_FILE": str(tmp_path)})  # a folder, not a file
    assert "Cannot write LOG_FILE" in caplog.text
    assert not [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


def test_rotation_settings_and_calling_twice(tmp_path):
    path = tmp_path / "app.log"
    env = {"LOG_FILE": str(path), "LOG_FILE_MAX_MB": "2", "LOG_FILE_BACKUPS": "3"}
    logging_setup.configure(env)
    logging_setup.configure(env)  # replaces the first setup: no doubled lines
    logging.getLogger("app").info("once")
    flush()
    [handler] = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert (handler.maxBytes, handler.backupCount) == (2 * 1024 * 1024, 3)
    assert path.read_text().count("once") == 1
    # Nonsense numbers fall back to the defaults.
    logging_setup.configure({**env, "LOG_FILE_MAX_MB": "x", "LOG_FILE_BACKUPS": "-1"})
    [handler] = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert (handler.maxBytes, handler.backupCount) == (10 * 1024 * 1024, 5)


async def test_each_request_gets_a_timing_line_without_its_query_string(client, caplog):
    with caplog.at_level(logging.INFO, logger="timing"):
        await client.get("/auth/me")  # 401, not signed in
        await client.get("/models?code=secret-sign-in-code")
        await client.get("/health")
    lines = [r.getMessage() for r in caplog.records if r.name == "timing"]
    assert len(lines) == 2  # /health is left out
    assert lines[0].startswith("GET /auth/me -> 401 in ") and lines[0].endswith(" ms")
    assert lines[1].startswith("GET /models -> ") and "secret" not in lines[1]
