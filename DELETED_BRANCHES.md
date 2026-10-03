# Deleted branches

Branches that no longer exist on GitHub (`akshatz/laptop-ai-backend`), with the commit each one ended at, so any of them can be recreated. Started 2026-10-03; since then the **Record deleted branches** workflow (`.github/workflows/deleted-branches.yml`) adds a row whenever a branch is deleted on GitHub, and once a day for any it missed. Rows are never removed.

A branch whose pull request was merged (a date in the Merged column) has its changes in `main` already; restoring it only brings back the branch name and its own commit history. A pull request's last commit stays on GitHub after its branch is deleted (as `refs/pull/<PR>/head`), so those branches can always be restored; the 40 branches listed on 2026-10-03 were all merged and checked against those refs. For a branch deleted without a pull request, the last commit is known only if GitHub's event log still had its last push; otherwise the table says unknown.

## Restoring a branch

Use the full commit from the table below.

On GitHub, without a local copy:

```bash
gh api repos/akshatz/laptop-ai-backend/git/refs -f ref=refs/heads/<branch> -f sha=<last commit>
```

Or open the pull request on GitHub and press **Restore branch** (shown on merged or closed PRs whose branch was deleted).

Locally only:

```bash
git fetch origin pull/<PR>/head      # only if the commit isn't in your clone
git branch <branch> <last commit>
```

For a branch with two PRs, the last commit belongs to the later one.

## Branches

"Deleted" is the UTC date GitHub's event log recorded for the deletion (or the date the workflow ran for it); "—" means it wasn't recorded, which is the case for branches deleted before 2026-10-03.

<!-- deleted-branches:start -->
| Branch | Last commit | PR | Merged | Deleted | What it was |
|---|---|---|---|---|---|
| `ci/add-build-check-workflow` | `d4343b33a80fc9e9a98f1e373734c53e37d2654e` | #1 | 2026-09-26 | — | Add build-check CI workflow for docker-compose and backend image |
| `openbao/create-readonly-role` | `43c278d515c8e9e99248f92a34b134834f0bd243` | #3 | 2026-09-26 | — | Add separate read/write OpenBao AppRoles for custom-backend |
| `bug/grafana` | `6fde34f79ba88560f7d6a5d3102b37af001e14e7` | #4 | 2026-09-26 | — | fixed grafana bug |
| `rag_embedding_model` | `c9097a29db44704ebdfd58c1e68fef4f2938a418` | #5 | 2026-09-26 | — | Added support for rag embedding model |
| `feature/password-enhancement` | `c175349fe0c4b34560fb56e90eaf6ebe0f098b51` | #6 | 2026-09-27 | 2026-10-03 | Feature/password enhancement |
| `bug/build-check-workflow` | `dcfd8b0c35866a32989bcf7cde1bf83cd74049cc` | #7 | 2026-09-27 | — | Fix CI build to match custom-backend's new repo-root build context |
| `feature/workflow_dispatch` | `4580388ae2aa6620f208e496f394300769fde6ef` | #8 | 2026-09-27 | 2026-10-03 | Add manual workflow_dispatch trigger with branch selection to build-check |
| `openwebui-to-use-milvus-as-vector-store` | `b1d8c4e042a70e9664951c117ab80c6c957e8b0d` | #9 | 2026-09-27 | 2026-10-03 | Add Milvus as Open WebUI's RAG vector store with root auth enabled |
| `feature/open-observe-support` | `1b524f0523bc694a973c8b80490fdec7b176670d` | #10 | 2026-09-27 | — | Add OpenObserve (O2) as a standalone observability service |
| `feature/langchain-chat` | `bea6d3dd4b94c05537458e94896d73e7e0699d12` | #11 | 2026-09-27 | — | Add LangChain RAG chat backed by Milvus and split custom-backend into modules |
| `refactor-of-code-and-readme.md` | `a550dfe5bb81b7995bbc11968b32a42b3bd9d851` | #12, #13 | 2026-09-28 | — | Make compose paths relative to devops/ so --project-directory is not needed |
| `feature/open-webui-email-verification` | `549f26d1f261e803a2f3209dd6dda56be8e60c71` | #14 | 2026-09-30 | — | Open WebUI: signup email verification, password reset and expiry, HTTPS proxy |
| `fix/codeql-alerts` | `3f12cbfd78595863ff3d4aa6fe67750e8309c68f` | #15 | 2026-09-30 | 2026-10-03 | Fix CodeQL alerts: stop printing DB URL, restrict workflow token |
| `fix/logs_not_visible_o2_open_observe` | `3295d6a62984dca34844cf6ee4ac27080ace575c` | #16 | 2026-09-30 | — | Send Open WebUI logs to OpenObserve stream openwebui_backend |
| `feat/password-expiry-reset-link` | `56ae74d409cf376a5b0f70ee7be5c7dd8e303995` | #17 | 2026-09-30 | 2026-10-03 | Feat/password expiry reset link |
| `feat/authentik-mfa` | `003735ecd875bc3a3d4e0e16f9b37403698e9096` | #18 | 2026-09-30 | — | Add authentik SSO with required TOTP for Open WebUI |
| `feat/openwebui-audit-logs-o2` | `62b5220169806224ecc427313b4570e3e1e8163f` | #19 | 2026-09-30 | 2026-10-03 | Ship Open WebUI audit log to OpenObserve stream openwebui_audit |
| `fix/passbolt-mariadb` | `a89a4166969e4403504f54971568356bbe6593b1` | #20 | 2026-09-30 | — | Run Passbolt on its own MariaDB with a persistent HTTPS cert |
| `feat/password-policy` | `b809ea3ff8b25e6e1578b4665dd30ec02f089aab` | #21 | 2026-09-30 | — | Feat/password policy |
| `feat/embeddinggemma` | `0b515347f1dca3841c2d05fe88c523bcf28b69f7` | #22 | 2026-09-30 | — | Switch RAG embeddings to embeddinggemma with task prefixes |
| `feat/searxng-web-search` | `7b1632c8e82fa41b4dc630cf7afa0e71bf82b852` | #23 | 2026-09-30 | — | Add SearXNG web search for Open WebUI |
| `feat/authentik-events-o2` | `efb500c5c6ab695a15580e660003c2f0428b709f` | #24 | 2026-09-30 | 2026-10-03 | Ship authentik audit events to OpenObserve stream authentik_events |
| `feat/authentik-invitations` | `c58d97526237ba04f8a28003a1b588face7c52f8` | #25 | 2026-09-30 | 2026-10-03 | Add invite-only sign-up flow to authentik |
| `fix/ollama-timeout-context` | `e2265f62582e3d4ad06947d6ad831aa24aa12fc0` | #26 | 2026-09-30 | — | Raise Open WebUI response timeout and Ollama context window |
| `fix/sso-signup-verification` | `51ad8a6cc1d3220358a843b51a4b0260819dcec0` | #27 | 2026-09-30 | — | Send signup verification email for SSO-created Open WebUI accounts |
| `feat/sso-user-status` | `ea88b6b442bdb3fe65e491c9856fb86d6c52425f` | #28 | 2026-09-30 | 2026-10-03 | Track SSO onboarding stage per user in fn_user_sso_status |
| `feat/openbao-human-users` | `9a2fe12c62ca48029e7862c39b47f2f746cd46fe` | #29 | 2026-09-30 | 2026-10-03 | Add separate read-only and admin OpenBao logins |
| `feat/add-passbolt` | `92b9b3d37c68812d991eb55fad6ca01b96fc1371` | #2, #30 | 2026-10-02 | 2026-10-03 | Add Passbolt recovery kit encryption workflow |
| `feat/authentik-self-signup` | `a6f4db336c507bc07cb0c8f9f4cd1db10a98d2a5` | #31 | 2026-10-02 | — | Feat/authentik self signup |
| `feat/hide-chat-controls` | `a0ea731fc6f77068a837e43d222e9c7197221b7d` | #32 | 2026-10-02 | 2026-10-03 | Hide the chat Controls panel from regular users |
| `feat/openbao-apps-layout` | `43c9a2ca5bdfe917f55ad2202573cb82aad1001c` | #33 | 2026-10-02 | 2026-10-03 | Store OpenBao secrets under apps/<environment>/<app> |
| `feat/archive-deleted-user-chats` | `73484f45c17aa726e11bba6b3fc3d76e96830d77` | #34 | 2026-10-02 | — | Archive a deleted user's chats and purge what Open WebUI leaves behind |
| `feat/openbao-oidc` | `05749df8b079cf760b001aa695c3b774110ac95e` | #35, #36 | 2026-10-02 | — | Pick OpenBao access by OIDC role: readonly by default, admin for openbao-admins |
| `feat/openwebui-audit-status` | `c07ab658f340ad0a320f09a5428aa4abbc8ec38b` | #37 | 2026-10-02 | 2026-10-03 | Ship Ollama logs to O2 and record status codes in Open WebUI's audit log |
| `feat/chat-soft-delete` | `267c824d054a4615734548333c70ce6172a427af` | #38 | 2026-10-02 | 2026-10-03 | Soft-delete chats: archive before Open WebUI deletes them, admin restore |
| `chore/lgtm-opt-in` | `d33d62dfdff46abd986d51cdd4a78250e75cde96` | #40 | 2026-10-02 | 2026-10-03 | Make Grafana LGTM opt-in to save memory |
| `feat/feedback-review` | `ac6614525da30b8aab62d7123dabd0d016f67520` | #41, #42 | 2026-10-03 | — | Document answer feedback and the review page in README |
| `feat/mfa-env-switch` | `8898c1c4ff2557b4060bd30e1a6dfdc7571bb16a` | #43, #44 | 2026-10-03 | 2026-10-03 | Copy AUTHENTIK_MFA_REQUIRED into OpenBao's apps/default/authentik |
| `feat/reranker-env` | `21eddb6ab4112bdff0b55a176612da66fb4d5ac0` | #45, #46 | 2026-10-03 | — | Feat/reranker env |
| `feat/settings-as-code` | `fbb4ded01c726be431ab98702bb218479362c795` | #47 | 2026-10-03 | — | Add LLM roadmap and Open WebUI settings as code; fix SSO sign-in loop… |
<!-- deleted-branches:end -->

## Updating this list

The workflow commits new rows to `main` as `github-actions[bot]`, so pull before editing this file. The table between the `deleted-branches` markers is maintained by `.github/scripts/record_deleted_branches.py`; to run it by hand (needs an authenticated `gh`):

```bash
python3 .github/scripts/record_deleted_branches.py
```

It only adds rows and fills in missing deletion dates. A branch deleted without a pull request is found only while GitHub's event log (90 days) still has the deletion; its commit, only while the log still has its last push. Otherwise a clone that still has the branch can recover it with `git reflog`.
