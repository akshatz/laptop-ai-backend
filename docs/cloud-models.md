# Cloud models (optional, paid)

Everything runs on local Ollama models by default. Paid cloud models can be added for the admin, each paid from that provider's prepaid credit. Set a credit or budget limit with every provider and leave auto top-up off.

| Provider | Key from | Base URL | Credit it spends |
|---|---|---|---|
| OpenRouter | openrouter.ai → Keys | `https://openrouter.ai/api/v1` | OpenRouter (one key for Claude, GPT and others) |
| OpenAI | platform.openai.com → API keys | `https://api.openai.com/v1` | OpenAI |

A provider's credit only works with that provider: Anthropic's own API credit is used through Claude Code with an API key, not through OpenRouter.

## Open WebUI

Admin → Settings → Connections, next to the Ollama connection. These are saved in Open WebUI's database, not in compose or `.env`.

1. Click **+** next to **Manage OpenAI API Connections** (OpenRouter speaks the OpenAI format; added as an Ollama connection, or with `/v1/api` instead of `/api/v1`, its models never appear).
2. Enter the base URL and key. Under **Model IDs** list only the models you want (e.g. `anthropic/claude-haiku-5.5`), otherwise OpenRouter adds hundreds; an optional **Prefix ID** such as `or` marks them in the model list. Verify, then Save.
3. Admin → Settings → Models: make every paid model **Private** (only admins and users or groups you grant can pick it), so family members on `fast-ai` can't spend the credit.

Current setup: OpenAI with `gpt-4o` and `gpt-4o-mini` (Private), and an OpenRouter connection.

Requests through these connections go through Open WebUI's Functions and logs like local ones. A user's own Direct Connection (Settings → Connections) goes from the browser straight to the provider instead and is invisible to them.

## opencode

The admin's `laptop-opencode` has an `openrouter` provider in [devops/opencode/opencode.json](../devops/opencode/opencode.json) (Claude Haiku/Sonnet 5.5, GPT-5.4 mini) using `OPENROUTER_API_KEY` from `.env`. The default and title models stay local, so nothing is spent until a paid model is picked with `/models`. Personal `opencode-u<pk>` containers stay Ollama-only. After setting the key:

```bash
docker compose -f devops/docker-compose.yml up -d --no-deps --force-recreate opencode
```

To add a model, add its OpenRouter id under `provider.openrouter.models` and recreate `opencode`.

## Which model answered, and what it cost

- Open WebUI shows the model name above each answer; opencode shows it in the model picker.
- openrouter.ai → Activity lists every request with app, model and cost; platform.openai.com → Usage does the same for OpenAI.
- Cheapest first: Claude Haiku 5.5 and GPT-5.4 mini for everyday use, Sonnet or gpt-4o only for hard questions.
