# Open WebUI account Functions

## Open WebUI signup verification

New Open WebUI password signups start as `pending` (`DEFAULT_USER_ROLE=pending`) and are emailed a verification link; confirming it promotes them to `user`. Accounts created by SSO sign-in are promoted to `user` immediately without the email (Valve `activate_sso_users`, on by default), because authentik's sign-up and invitation flows already confirm the address. This is an Open WebUI event Function, [devops/open-webui/functions/signup_email_verification.py](../devops/open-webui/functions/signup_email_verification.py) (Open WebUI declined adding it to core — [discussion #31626](https://github.com/open-webui/open-webui/discussions/31626)).

One-time setup, after the stack is up:

1. In Open WebUI, go to **Admin → Functions → Import**, choose that file, save, and switch it on.
2. Open the Function's settings (Valves) and set `base_url` to the address users open Open WebUI at — `https://<OPEN_WEBUI_HOST>:8444` for LAN users (default `http://localhost:8082`). Leave the SMTP fields empty to use `SMTP_USER`/`SMTP_PASSWORD` from compose.
3. Restart once so it registers its routes: `docker restart laptop-open-webui`, then check `docker logs laptop-open-webui 2>&1 | grep "verification routes"`.
4. Check **Admin → Settings → General → Default User Role** is `pending` — a value saved there overrides the env var.

How it behaves:

- Links expire after 2 hours (`token_max_age_seconds` Valve). The token sits in the link's `#` fragment and the page asks the user to click **Verify email**, so tokens don't reach server logs and mail scanners that open links don't verify accounts.
- Pending users see a "Check your email" screen (`PENDING_USER_OVERLAY_TITLE`/`PENDING_USER_OVERLAY_CONTENT` in compose) linking to `/api/v1/auths/verify-email/resend`, a page where they can request a new link (rate-limited, same response whether or not the account exists). If **Admin → Settings → Authentication** already has saved overlay text, that wins over the compose values — clear it or paste the same text there.
- Each account is verified at most once (recorded in the `fn_email_verified` table — see [Function tables](#function-tables)), so an admin can suspend a user by setting them back to `pending` without an old link or a resend re-activating them.
- The Function is stored in Open WebUI's database, not read from the repo — re-import it after editing the file.
- The `open-webui` image is pinned by digest because the Function uses Open WebUI internals. When bumping it, re-test a signup end to end.

### Password reset

Open WebUI has no "forgot password" of its own. A second, independent event Function, [devops/open-webui/functions/password_reset.py](../devops/open-webui/functions/password_reset.py), adds it — import and enable it the same way, set its `base_url` Valve, and restart once.

- Users go to `/api/v1/auths/forgot-password` (Open WebUI's login page can't be customised to link it, so share or bookmark the URL), enter their email, and get a reset link that expires in 30 minutes and works once — any password change invalidates older links. Pending and deactivated accounts can't reset.
- Whenever a password changes (by the user, an admin, or a reset), the user gets a notice email with the forgot-password link, so an unexpected change doesn't go unnoticed. Turn off with the `notify_on_password_change` Valve.
- A reset only logs out the user's other sessions if Open WebUI has Redis configured; this stack doesn't, so existing sessions stay valid until they expire (`JWT_EXPIRES_IN`, default 4 weeks).

### Password expiry

A third event Function, [devops/open-webui/functions/password_expiry.py](../devops/open-webui/functions/password_expiry.py), makes passwords expire after 180 days (`max_age_days` Valve). It needs the Password Reset Function, since expired users recover through the forgot-password page (or an admin sets a new password). NIST SP 800-63B advises against forced periodic changes; enable this only if a policy requires it.

- Open WebUI doesn't record password age, so the Function keeps its own record in the `fn_password_age` table (see [Function tables](#function-tables)), reset on signup and on every password change. Existing users (no record yet) are counted from their account creation date — Open WebUI's `user.created_at`, since it doesn't record password changes — but always get at least 14 days, with reminders, from when the Function first sees them (`grace_days_for_existing`), so enabling it doesn't lock out old accounts. Turn off `start_from_account_creation` to count from that first sighting instead.
- An expired user who enters the correct password gets an error on the login page saying the password has expired, and is **emailed a one-time link to set a new one** (the Password Reset Function's page; valid 30 minutes, at most one email per 10 minutes). Open WebUI's login page can't be redirected, so the email is the way forward. If the Password Reset Function isn't enabled, the message tells them to ask an admin instead. A wrong password gets Open WebUI's normal error, so nothing leaks about whether an account exists.
- Users signing in within 14 days of expiry (`warn_days`) get a reminder email, at most once a day.
- Admin passwords never expire (`exempt_admins`, on by default).
- **Overview:** while signed in to Open WebUI as an admin, open `/api/v1/auths/password-expiry` (e.g. http://localhost:8082/api/v1/auths/password-expiry) for every user's password date, expiry date, days left and status, soonest first; add `?format=json` for JSON. Dates marked * are estimated for users the Function hasn't recorded yet, and viewing the page doesn't start anyone's clock.
- The 180 days and other settings are the Function's Valves (**Admin → Functions → Password Expiry → ⚙**), stored in Open WebUI's database — not environment variables.
- Only the email/password sign-in is checked (not LDAP, OAuth or API keys), and already-signed-in sessions last until they expire.

### Deleting users

Another event Function, [devops/open-webui/functions/authentik_user_cleanup.py](../devops/open-webui/functions/authentik_user_cleanup.py), makes **Admin → Users → Delete** in Open WebUI also delete the person's authentik (SSO) account, so they can't sign in again or come back as a new pending user. Import and enable it the same way, then restart `open-webui` once (it hooks Open WebUI's delete endpoint at startup).

- It finds the authentik account by the SSO link Open WebUI stored at the user's first login. For users who never signed in with SSO, it uses the single authentik account with the same email (Valve `match_by_email`) and does nothing if several share it.
- authentik superusers (such as `akadmin`) and service accounts are never deleted.
- It calls authentik's API as the `open-webui-user-sync` service account ([authentik/blueprints/open-webui-user-sync.yaml](../authentik/blueprints/open-webui-user-sync.yaml)), which may only view and delete users. Its token is `OPEN_WEBUI_AUTHENTIK_API_TOKEN` in `.env` (generate with `openssl rand -hex 32`), passed to authentik and to `open-webui` as `AUTHENTIK_API_TOKEN`.
- If authentik can't be reached, the Open WebUI user is still deleted and the failure is logged. Delete the authentik user by hand under **Directory → Users**.
- **Their chats are kept.** Open WebUI deletes a user's chats with the account, so the Function first copies them into an admin-only archive (`fn_archived_chats`). If that copy fails, the user isn't deleted. Browse it at **`/api/v1/archived-chats`** on Open WebUI while signed in as an admin (e.g. `https://<OPEN_WEBUI_HOST>:8444/api/v1/archived-chats`): each chat opens as a readable transcript, can be downloaded as JSON, or deleted. Attachments aren't kept. Archived chats stay until you delete them.
- **Deleting someone who asked to be erased completely:** switch off the Function's `archive_chats` Valve (**Admin → Functions → authentik User Cleanup → ⚙**) before deleting them, then switch it back on.
- It also removes what Open WebUI leaves behind after a delete: the user's memories, notes, tags and uploaded files, including their embeddings in Milvus (Open WebUI's own file delete leaves the Milvus collection), and their rows in `fn_email_verified` / `fn_password_age`. `fn_user_sso_status` keeps them, marked `removed` by its sync script.

### Deleted chats

Open WebUI deletes chats for good: there's no trash. The Chat Soft Delete Function, [devops/open-webui/functions/chat_soft_delete.py](../devops/open-webui/functions/chat_soft_delete.py), copies a chat into the same admin-only archive just before Open WebUI deletes it, so it can be brought back. Import and enable it like the others, then restart `open-webui` once.

- Covers deleting one chat, **Delete all chats** in Settings, and deleting a folder with its chats. Deleting a single message inside a chat isn't covered.
- Archived chats show up at **`/api/v1/archived-chats`** next to deleted users' chats, marked by reason. A deleted chat has a **Restore** button that gives it back to its owner (out of any folder, unpinned and unshared) and removes it from the archive.
- If the copy fails, the chat is still deleted and the error is logged.
- A whole user's chats are left to the User Cleanup Function's `archive_chats` Valve (above), so switching that off still erases someone completely.

### Duplicate uploads

In a chat, Open WebUI stores, reads and embeds every upload again, even when it's a file you already uploaded (it refuses duplicates only inside a knowledge base). The Duplicate Upload Notice Function, [devops/open-webui/functions/duplicate_upload_notice.py](../devops/open-webui/functions/duplicate_upload_notice.py), records every chat upload in the `fn_file_uploads` table and, when a file you just attached has the same content as one you uploaded before, shows a line above the answer like "You uploaded menu.pdf before, on 17 Oct 2026". Import and enable it, and switch on **Global** so it covers every model. No restart needed.

- Copies are matched by content (a SHA-256 of the file's bytes), so renaming a file doesn't hide a copy. Only your own earlier uploads count, and earlier copies whose processing failed don't.
- Each file is reported once, on the first question it's attached to.
- To see every repeat, query the view: `SELECT filename, copy_number, first_file_id FROM fn_duplicate_uploads;` (in the `open_webui` database). Uploads from before the table existed are included.

### Function tables

The Functions (and the SSO status script) keep their state in Postgres, in Open WebUI's `open_webui` database, so it's backed up with the rest of Open WebUI's data:

| Table | Used by | Holds |
|---|---|---|
| `fn_email_verified` | Signup Email Verification | Users verified once (`user_id`, `verified_at`) |
| `fn_password_age` | Password Expiry | When each password was last set, and the last reminder (`user_id`, `changed_at`, `last_warned_at`) |
| `fn_archived_chats` | authentik User Cleanup, Chat Soft Delete | Chats of deleted users and deleted chats, copied just before deletion (full chat JSON and messages, the user's email/name, when, by whom and why) — see [Deleting users](#deleting-users) and [Deleted chats](#deleted-chats) |
| `fn_embedding_cache` | Embedding Cache | One vector per web-page or file chunk already embedded, keyed by a hash of model, prefix and text; rows unused for 30 days are deleted (see [Web search](web-search-and-rag.md#web-search)) |
| `fn_file_uploads` | Duplicate Upload Notice | One row per chat upload: file id, user, content hash, name, type, size, time; view `fn_duplicate_uploads` lists the repeats (see [Duplicate uploads](#duplicate-uploads)) |
| `fn_user_sso_status` | `devops/open-webui/sync-user-status.sh` (not a Function) | Each person's SSO onboarding stage and when they reached each step, keyed by lowercased email (see [Inviting users](sso.md#inviting-users)) |

They're created by a separate Alembic setup, [devops/open-webui/migrations/](../devops/open-webui/migrations/), which tracks its history in `fn_alembic_version` so it never touches Open WebUI's own migrations in the same database. The one-shot `open-webui-fn-migrate` service applies it on every `docker compose up` (a no-op once current), and `open-webui` waits for it. Its first revision also imports rows from the Functions' earlier SQLite files (`email_verification.db` / `password_expiry.db` on the `open-webui-data` volume), if present. Once that's done, those files are unused and can be deleted.

To add a table or column, add a new revision under `devops/open-webui/migrations/versions/` (the Functions don't create tables themselves).
