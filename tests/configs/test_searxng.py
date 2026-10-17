"""devops/searxng/settings.yml: the settings Open WebUI's web search depends on (see CLAUDE.md)."""

from pathlib import Path

import yaml

SETTINGS = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "devops" / "searxng" / "settings.yml").read_text()
)


def test_json_results_are_allowed():
    """Open WebUI asks for ?format=json; without it in formats every search gets 403."""
    assert "json" in SETTINGS["search"]["formats"]


def test_google_and_bing_are_on():
    """The default web engines alone got blocked together on 2026-10-03 and searches came back empty."""
    enabled = {e["name"] for e in SETTINGS["engines"] if not e.get("disabled")}
    assert {"google", "bing"} <= enabled


def test_internal_only_settings():
    """No published port, so the bot limiter (which would also need Valkey) stays off."""
    assert SETTINGS["server"]["limiter"] is False
    assert SETTINGS["use_default_settings"] is True
    # The real key comes from SEARXNG_SECRET; the file only holds a placeholder.
    assert SETTINGS["server"]["secret_key"] == "overridden-by-SEARXNG_SECRET"
