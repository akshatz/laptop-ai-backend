# LLM engineering roadmap

What this stack still lacks as an AI/LLM engineering setup, in the order it makes sense on this laptop (16 GB soldered RAM, CPU only, already swapping). Every step must fit in memory and be measurable with `evals/run.py`. Started 2026-10-03.

| # | Step | Done when | Extra RAM | Status |
|---|---|---|---|---|
| 1 | **Prompts and settings as code** | Open WebUI's tuned settings (answer and search prompts, retrieval settings, model params, Function switches) live in `devops/open-webui/settings.yaml`; `settings.py export/diff/apply` syncs them with the DB; eval runs record which settings version they tested | none | done 2026-10-03 |
| 2 | **Tests in CI** | `pytest` covers custom-backend endpoints and the Functions' logic (regenerate limit, history trim, query log, feedback review); GitHub Actions runs them on every push | none | |
| 3 | **Model-graded evals** | Besides the regex checks, each eval answer gets a faithfulness score (every claim supported by its sources) and a retrieval score (the expected fact was in the chunks the model saw) | none locally (judge model to be chosen: local is slow, cloud sees only the public eval questions) | |
| 4 | **LLM tracing** | Each question's steps (query generation, search, fetch, embedding, rerank, answer) show up as one trace with timings and token counts in OpenObserve, which already accepts traces | small (O2 storage) | |
| 5 | **Tools and function calling** | A stock and index price tool answers price questions from market data instead of web pages; evals measure how reliably the 3B model calls it | small | |
| 6 | **MCP server** | One MCP server (for example the price tool or OpenObserve log search) usable from Open WebUI and Claude Code | small | |
| 7 | **Agent workflow** | A multi-step research flow in custom-backend (LangGraph: search → read → check → answer), compared with the plain pipeline on the evals | small | |
| 8 | **Guardrails** | Web chunks screened for prompt injection before they reach the model; PII kept out of logs; tests for both | small | |
| 9 | **Model gateway** | LiteLLM between the apps and Ollama (optionally a cloud model) with fallbacks and per-request latency and cost logs in O2 | ~200 MB | |
| 10 | **Fine-tuning (off this machine)** | A small model LoRA-tuned on a free cloud GPU (Colab or Kaggle) from feedback and eval data, exported to GGUF, run here in Ollama and compared on the evals | none here | |

Not planned on this laptop: serving with vLLM or TGI (needs a GPU), models above about 4B at usable speed, self-hosted Langfuse (its compose setup wants a 16 GB machine of its own).
