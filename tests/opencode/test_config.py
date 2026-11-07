"""devops/opencode/opencode.json: local models plus pinned OpenRouter/OpenAI ones, and every model it names exists."""

import json
import re
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parents[2] / "devops" / "opencode" / "opencode.json").read_text())
LOCAL_PROVIDERS = {"openwebui", "ollama"}
PAID_PROVIDERS = {"openrouter", "openai"}
PROVIDERS = LOCAL_PROVIDERS | PAID_PROVIDERS


def listed_models():
    return {f"{p}/{m}" for p, provider in CONFIG["provider"].items() for m in provider["models"]}


def test_only_listed_providers_are_enabled():
    """No opencode/* cloud models (some Chinese, some of undisclosed origin)."""
    assert set(CONFIG["enabled_providers"]) == PROVIDERS
    assert set(CONFIG["provider"]) <= PROVIDERS


def test_paid_providers_are_opt_in():
    """The default and title models stay local, so nothing spends credit unless picked."""
    assert CONFIG["provider"]["openrouter"]["options"] == {
        "baseURL": "https://openrouter.ai/api/v1", "apiKey": "{env:OPENROUTER_API_KEY}"}
    assert CONFIG["provider"]["openai"]["options"] == {
        "baseURL": "https://api.openai.com/v1", "apiKey": "{env:OPENAI_API_KEY}"}
    for model in (CONFIG["model"], CONFIG["small_model"]):
        assert model.split("/", 1)[0] in LOCAL_PROVIDERS


def test_providers_point_inside_the_docker_network():
    urls = {p: provider["options"]["baseURL"] for p, provider in CONFIG["provider"].items()}
    assert urls["openwebui"] == "http://open-webui:8080/api"  # container port, not Caddy's 8444
    assert urls["ollama"] == "http://ollama:11434/v1"
    assert CONFIG["provider"]["openwebui"]["options"]["apiKey"] == "{env:OPENWEBUI_API_KEY}"


def test_default_and_title_models_are_listed():
    assert CONFIG["model"] in listed_models()
    assert CONFIG["small_model"] in listed_models()


def test_model_ids_look_like_ollama_names():
    """Ollama ids are name:tag; `photo-ai-latest` (a hyphen for the colon) gave "Model not found"."""
    for model in listed_models():
        if model.split("/", 1)[0] in PAID_PROVIDERS:
            continue
        assert re.fullmatch(r"[a-z0-9.-]+:[a-z0-9.-]+", model.split("/", 1)[1]), model


def test_searxng_add_on_is_pinned_and_internal():
    searxng = CONFIG["mcp"]["searxng"]
    assert re.fullmatch(r"mcp-searxng@\d+\.\d+\.\d+", searxng["command"][-1])
    assert searxng["environment"]["SEARXNG_URL"] == "http://searxng:8080"
    assert searxng["environment"]["BUN_BE_BUN"] == "1"  # no Node in the image


def test_sharing_and_autoupdate_are_off():
    assert CONFIG["share"] == "disabled" and CONFIG["autoupdate"] is False
