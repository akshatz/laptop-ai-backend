# LLM engineering roadmap

What this stack still lacks as an AI/LLM engineering setup, in the order it makes sense on this laptop (16 GB soldered RAM, CPU only, already swapping). Every step must fit in memory and be measurable with `evals/run.py`. Started 2026-10-03.

| # | Step | Done when | Extra RAM | Status |
|---|---|---|---|---|
| 1 | **Prompts and settings as code** | Open WebUI's tuned settings (answer and search prompts, retrieval settings, model params, Function switches) live in `devops/open-webui/settings.yaml`; `settings.py export/diff/apply` syncs them with the DB; eval runs record which settings version they tested | none | done 2026-10-03 |
| 2 | **Tests in CI** | `pytest` covers laptop-backend endpoints and the Functions' logic (regenerate limit, history trim, query log, feedback review); GitHub Actions runs them on every push | none | done 2026-10-17 (198 tests incl. migrations, configs, settings.py, evals, opencode; found the 2 laptop-backend login bugs, 3 Function email cooldowns that failed after boot, and an unredacted `api_token` path in settings.py) |
| 3 | **Model-graded evals** | Besides the regex checks, each eval answer gets a faithfulness score (every claim supported by its sources) and a retrieval score (the expected fact was in the chunks the model saw) | none locally (judge model to be chosen: local is slow, cloud sees only the public eval questions) | |
| 4 | **LLM tracing** | Each question's steps (query generation, search, fetch, embedding, rerank, answer) show up as one trace with timings and token counts in OpenObserve, which already accepts traces | small (O2 storage) | |
| 5 | **Tools and function calling** | A stock and index price tool answers price questions from market data instead of web pages; evals measure how reliably the 3B model calls it | small | |
| 6 | **MCP server** | One MCP server (for example the price tool or OpenObserve log search) usable from Open WebUI and Claude Code | small | |
| 7 | **Agent workflow** | A multi-step research flow in laptop-backend (LangGraph: search → read → check → answer), compared with the plain pipeline on the evals | small | |
| 8 | **Guardrails** | Web chunks screened for prompt injection before they reach the model; PII kept out of logs; tests for both | small | |
| 9 | **Model layer** | One LangChain model layer in laptop-backend for every model call (Ollama, optionally a cloud model) with fallbacks and per-request latency and token logs in O2 — replaces the planned LiteLLM gateway (2026-10-04: laptop-backend will be the only app calling models after the Open WebUI replacement, it saves ~200 MB RAM, and LiteLLM 1.82.7/1.82.8 were compromised on PyPI in March 2026) | none | |
| 10 | **Fine-tuning (off this machine)** | A small model LoRA-tuned on a free cloud GPU (Colab or Kaggle) from feedback and eval data, exported to GGUF, run here in Ollama and compared on the evals | none here | |

Not planned on this laptop: serving with vLLM or TGI (needs a GPU), models above about 4B at usable speed, self-hosted Langfuse (its compose setup wants a 16 GB machine of its own).

## Open WebUI replacement (target 15 Dec 2026)

Move the family from Open WebUI to laptop-frontend + laptop-backend, then retire Open WebUI (see [docs/laptop-frontend.md](docs/laptop-frontend.md)). Planned 2026-11-14, weekends only, ~3 h a day; estimates include testing in the browser.

Already in laptop-frontend (2026-11-14): authentik sign-in, chats and history, model picker (paid models admin-only), web search with sources, sources-only answers for facts, regenerate limit, 👍/👎 and a Feedback page, suggested questions, documents and pictures (OCR + `gemma3:4b` description) attached from the message box, versions across chats, deleting chats and documents, admin views, hideable sidebar.

| # | Feature | Estimate | Weekend | Status |
|---|---|---|---|---|
| 1 | **Streaming answers** (send + regenerate, Stop button) | 3–4 h | 15 Nov | |
| 2 | **Restore deleted chats** (archive table, restore endpoint, Deleted chats view) | 2 h | 21–22 Nov | |
| 3 | **Follow-up suggestions** after each answer | 1–1.5 h | 21–22 Nov | |
| 4 | **Idle sign-out** after 3 h | 1 h | 21–22 Nov | |
| 5 | **"My usage" for everyone, KPI page for admins** | 2–3 h | 28–29 Nov | |
| 6 | **Search across chats** (Postgres full-text) | 1.5–2 h | 28–29 Nov | |
| 7 | **Edit a question and ask again** (earlier answers kept) | 2 h | 5–6 Dec | |
| 8 | **Knowledge shared across chats** (collections picked per chat) | 3–4 h | 5–6 Dec | |
| 9 | **Banners, per-user permissions, groups** | 3–4 h | 12–13 Dec | |
| 10 | **Move existing Open WebUI data across** (chats, feedback) | 2–4 h | 12–13 Dec | |

Steps 1–4 are the core switch (family can move over after 22 Nov); 9–10 may slip past 15 Dec without blocking it. Not ported on purpose: Direct Connections and the Functions/filters system (they extend Open WebUI). Open WebUI keeps running alongside until the switch is done; removing it needs the rewiring listed in the project notes (Caddy :8444, the authentik `open-webui` app, `openwebui-audit-shipper`, `open-webui-fn-migrate`).
