# Answer-quality evals

`run.py` sends each case in `cases.yaml` through Open WebUI's real chat pipeline (`fast-ai:latest`,
web search on, as family members have it) and scores the answer with simple checks: facts that must
appear, things that must not, whether it searched, how many different websites it drew on, and
whether it said "I don't know" when it should (and only then). Every run is saved to `runs/` with
the Open WebUI settings in force, so a change can be judged by comparing two runs.

## One-time setup

1. Admin Panel → Settings → Authentication: turn on **API Keys**, turn on **API Key Endpoint
   Restrictions** and set **Allowed Endpoints** to `/api/chat/completions` (the key can then do
   nothing else, even though it is an admin's key).
2. Settings → Account → API Keys: create a key and put it in the repo's `.env`:
   `OPEN_WEBUI_API_KEY=sk-...`
3. Optional, also in `.env`: `OPEN_WEBUI_URL` (default `http://127.0.0.1:8082`), `EVAL_MODEL`
   (default `fast-ai:latest`). The settings snapshot reads Open WebUI's DB with
   `docker exec laptop-postgres psql -U $POSTGRES_USER`.

Needs Python 3.11+ with PyYAML.

## Running

```bash
python3 evals/run.py --label baseline                  # all cases, ~30-90 s each
python3 evals/run.py --only tata-trusts-role --label quick
python3 evals/run.py --category abstain
python3 evals/run.py --compare evals/runs/<A>.jsonl evals/runs/<B>.jsonl
```

Calls aren't saved as chats. They use the same Ollama as everyone else, so run a full set when
nobody is chatting.

## Workflow

1. Run a baseline.
2. Change one thing (a prompt, `rag.top_k`, the blocklist, the model), run again with a label
   saying what changed, and `--compare` the two runs. `--compare` also lists which settings differ.
3. Keep the change only if nothing that passed before now fails.
4. When someone rates an answer 👎 (see `/api/v1/feedback-review`), add it as a case first:
   `python3 evals/feedback_to_cases.py` appends every new 👎 to `cases.local.yaml` as a
   `status: draft` case with the question, comment, rated answer and its sources. `run.py` skips
   drafts unless `--include-drafts`. Review each one: fix the category, write `expect`, add
   `history` for follow-up questions, drop `rated_answer`/`rated_sources`, and remove `status`.
   Set `status: rejected` (not delete) to dismiss one; ratings are matched by `feedback_id`.

Private questions (family names, the Family knowledge collection) go in `cases.local.yaml`, same
format, gitignored. `current-data` cases (prices, weather) can't have fixed answers, so they check
the shape of a good answer instead: it searched, gave a figure, used 2+ sites, and didn't fall back
to the model's December 2023 knowledge.
