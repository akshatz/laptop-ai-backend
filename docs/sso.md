# Sign-in: SSO, MFA and users (authentik)

## Open WebUI SSO + MFA (authentik)

Open WebUI sign-in goes through [authentik](https://goauthentik.io) (`authentik-server` + `authentik-worker`, using a separate `authentik` database in `postgres-db`), which asks for a TOTP code from Google Authenticator or any other TOTP app on every login (`AUTHENTIK_MFA_REQUIRED=false` makes it optional: only authentik admins and people who chose to set up an authenticator are asked, see below). Open WebUI's own email/password sign-in is turned off.

- authentik is served by `open-webui-proxy` at `https://<OPEN_WEBUI_HOST>:9443` (same Caddy CA as Open WebUI on 8444). The login page's password field has an eye icon to show/hide the password (`allow_show_password` in [open-webui-sso.yaml](../authentik/blueprints/open-webui-sso.yaml); authentik has no such option for the password boxes on the sign-up, invitation and reset forms). Opening Open WebUI goes straight to authentik's login page (`OAUTH_AUTO_REDIRECT`, done early by [devops/open-webui/static/loader.js](../devops/open-webui/static/loader.js) so Open WebUI's own sign-in page doesn't flash first), so there's one sign-in, not two. Flows that finish without a destination (password reset, signing in at `:9443` directly, logout) would end on authentik's app dashboard at `/`; Caddy redirects that bare `/` to Open WebUI, which signs the user straight in. Use `https://<OPEN_WEBUI_HOST>:9443/if/admin/` for authentik's admin UI and `/if/user/` for a user's own authentik settings (password, authenticators). Open WebUI's own page, with **Continue with authentik** and **Sign up**, only shows after logging out or after a sign-in error.
- <a id="one-sign-in-for-all-apps"></a>**One sign-in for all apps** (everyone, admins and regular users): signing in to authentik once (email or username, password, authenticator code) also signs you in to Open WebUI, custom-frontend, OpenBao, opencode and Attu without asking again. Every app's provider uses implicit consent, and authentik's sign-in lasts **7 days**, even after the browser is closed (`session_duration` on `default-authentication-login`, set in [authentik/blueprints/seamless-sign-in.yaml](../authentik/blueprints/seamless-sign-in.yaml); it was a browser-session cookie before, so every app asked again after closing the browser). Sign in with the full email or the exact username: for people who signed up themselves the username *is* their email (e.g. `name@gmail.com`, not `name`), and a name that matches no account now says so straight away instead of showing a password page that always answers "Invalid password" (`pretend_user_exists: false`). After editing the blueprint: `docker exec laptop-authentik-worker ak apply_blueprint /blueprints/custom/seamless-sign-in.yaml`; it only affects new sign-ins. The idle sign-out below still ends the session early.
- **Signed out after 3 hours without activity** (everyone except Open WebUI admins): no mouse, keyboard, scroll or touch in any Open WebUI tab for 3 hours signs the person out of Open WebUI and of authentik, so the next visit asks for the password and authenticator code again. Done in the browser by [devops/open-webui/static/loader.js](../devops/open-webui/static/loader.js) (`IDLE_LIMIT_MS`); Open WebUI itself has no idle timeout, so a copied sign-in token would stay valid for its normal 4 weeks.
- Its configuration is the blueprint [authentik/blueprints/open-webui-sso.yaml](../authentik/blueprints/open-webui-sso.yaml), applied by the worker on start and whenever the file changes. It makes MFA mandatory in authentik's default login flow (users without a TOTP device get a QR code to set one up before their first login completes; TOTP and static recovery codes only; after a code is entered, that browser isn't asked again for 3 hours — the password still is, and other browsers/devices still need a code) and registers the Open WebUI OIDC client.
- **Switching MFA off or on:** set `AUTHENTIK_MFA_REQUIRED` in `.env`: `true`/`1` (default) means everyone, `false`/`0` makes TOTP optional: authentik admins (akadmin) are always asked, and so is anyone who set up an authenticator (the checkbox on the sign-up page, or later in their authentik settings at `/if/user/`); everyone else signs in with just the password. authentik picks up the value only when the blueprints are applied, so after changing it run:

  ```bash
  docker compose -f devops/docker-compose.yml up -d --no-deps authentik-server authentik-worker
  docker exec laptop-authentik-worker ak apply_blueprint /blueprints/custom/open-webui-sso.yaml
  docker exec laptop-authentik-worker ak apply_blueprint /blueprints/custom/password-recovery.yaml
  ```

  With `false`, **Forgot password** still asks for a code from people who have an authenticator, and lets those without one reset with the emailed code alone.
- Open WebUI calls authentik server-side at the same `https://<OPEN_WEBUI_HOST>:9443` URL browsers use, so issuer and endpoint URLs match. It trusts Caddy's CA through `caddy-ca-export`, a one-shot service that copies only Caddy's public root cert (never the CA key) into the `caddy-ca-public` volume.
- Anyone can sign up: Open WebUI's sign-in page has a **Sign up** button under **Continue with authentik** (added by [devops/open-webui/static/loader.js](../devops/open-webui/static/loader.js); recreate `open-webui` after editing it), and authentik's own login page has a **Sign up** link. Both go to the `self-enrollment` flow ([authentik/blueprints/self-enrollment.yaml](../authentik/blueprints/self-enrollment.yaml)); afterwards authentik sends the user on to Open WebUI. It asks for name, email and password only (the email doubles as the authentik username), refuses an email that's already registered, and creates the account **inactive** until the emailed confirmation link (valid 30 minutes) is opened. Only then does it sign them in. A checkbox, **Protect my account with an authenticator app** (unchecked by default), adds TOTP setup right after that first sign-in; from then on they're asked for a code at every sign-in. People who skip it can add an authenticator later in their authentik settings (`/if/user/`). With `AUTHENTIK_MFA_REQUIRED=true` everyone is asked to set one up at their first sign-in anyway. Confirming the email matters because of the account linking below. Admins can also invite people (see [Inviting users](#inviting-users)). The first SSO login links an existing Open WebUI account with the same email (`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`). A new email gets a new Open WebUI account that's active (`user`) straight away: authentik has already confirmed the address, so the signup verification Function skips its own email for SSO accounts (Valve `activate_sso_users`).

### First-time setup

1. Add the authentik variables to `.env` (see `.env.example`), e.g.
   ```bash
   echo "AUTHENTIK_SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')" >> .env
   echo "OPEN_WEBUI_OIDC_CLIENT_ID=$(openssl rand -hex 20)" >> .env
   echo "OPEN_WEBUI_OIDC_CLIENT_SECRET=$(openssl rand -base64 60 | tr -d '\n')" >> .env
   ```
   plus `AUTHENTIK_BOOTSTRAP_EMAIL` / `AUTHENTIK_BOOTSTRAP_PASSWORD`. Use your Open WebUI admin email as `AUTHENTIK_BOOTSTRAP_EMAIL`: then `akadmin` signs in to Open WebUI as the existing admin.
2. Let containers reach the host's published port 9443. A host firewall (ufw by default denies incoming) blocks container → host traffic, and Open WebUI's server-side OIDC calls go that way:
   ```bash
   sudo ufw allow from 172.16.0.0/12 to any port 9443 proto tcp comment 'open-webui -> authentik'
   ```
3. `docker compose -f devops/docker-compose.yml up -d`. From here, Open WebUI password sign-in is rejected (`ENABLE_PASSWORD_AUTH=false`).
4. Open `https://<OPEN_WEBUI_HOST>:9443/if/admin/`, sign in as `akadmin`, and scan the QR code with Google Authenticator when asked.
5. Invite each existing Open WebUI user (see [Inviting users](#inviting-users)) with their **same email**, so their accounts get linked. They choose their own password while signing up (and set up TOTP there when `AUTHENTIK_MFA_REQUIRED=true`).
6. Turn off Open WebUI's login form. `ENABLE_LOGIN_FORM=false` in compose is ignored because Open WebUI has already saved `ui.enable_login_form` in its database, and while that form is on, `/api/v1/auths/signup` still accepts password signups. Delete the saved value so the compose setting applies, then restart:
   ```bash
   docker exec laptop-postgres psql -U "$POSTGRES_USER" -d open_webui -c "DELETE FROM config WHERE key = 'ui.enable_login_form'"
   docker compose -f devops/docker-compose.yml restart open-webui
   ```

**Rollback:** set `ENABLE_PASSWORD_AUTH=true` and `ENABLE_LOGIN_FORM=true` on `open-webui` and run `up -d` again. If authentik is down, nobody can sign in to Open WebUI, and the rollback is how you get back in.

**Forgot password:** on authentik's login page, click **Forgot username or password?** (also offered on the password step), or an admin sends a recovery link from **Directory → Users**. Flow `password-recovery` ([authentik/blueprints/password-recovery.yaml](../authentik/blueprints/password-recovery.yaml)): enter email or username → type the **6-digit code** emailed to the account's address (valid 15 minutes, wrong guesses slow down further attempts; same tab, no link). The email ([authentik/email-templates/password_reset_code.html](../authentik/email-templates/password_reset_code.html), mounted at `/templates` in authentik) puts the code in the preview line, so Gmail and iOS Mail offer **Copy code**, and one tap or double-click selects the whole code; a real copy button isn't possible because email clients strip scripts → enter a TOTP or static recovery code → set a new password (same rules as sign-up) → signed in. Unknown emails/usernames are refused straight away (the sign-up form already reveals which emails are registered). The TOTP step means a stolen mailbox alone can't reset an account; someone who lost both password and authenticator needs an admin (below, then an admin recovery link).

**Lost authenticator:** in authentik, open the user under **Directory → Users → MFA Authenticators** and delete the TOTP device. The user enrolls a new one on their next login.

### Inviting users

Besides self sign-up, you can send new users a one-time invite link ([authentik/blueprints/invitations.yaml](../authentik/blueprints/invitations.yaml)):

1. In authentik's admin UI (`https://<OPEN_WEBUI_HOST>:9443/if/admin/`), go to **Directory → Invitations → Create**.
2. Fill in:
   - **Name**: e.g. `invite-alex`
   - **Flow**: `invitation-enrollment`
   - **Expires**: a date a few days out
   - **Single use**: on
   - **Custom attributes**: `{"email": "their@email.com"}`. Use the email of their existing Open WebUI account, if they have one, so the accounts get linked.
3. Open the new invitation's row, copy the **link**, and send it to them.
4. They open the link, choose a username, name and password (the [password rules](#password-rules) apply), confirm their email from the link authentik sends (valid 24 hours; the account stays inactive until then, since the pre-filled email can be changed — if the link expires, delete the inactive user under **Directory → Users** and send a new invite, because the single-use invite is already spent), and scan a QR code with Google Authenticator. That creates and signs in their authentik account.
5. They click **Continue with authentik** on Open WebUI. People with an existing Open WebUI account (same email) go straight in. New people get an active account straight away.

Links without a valid invitation are refused ("Invalid invite/invite not found"). Delete an unused invitation to revoke it. Don't open a link yourself to check it: opening it uses up a single-use invitation.

To see how far each person has got, run `devops/open-webui/sync-user-status.sh`. It records each person's stage (not invited → invited → authentik account → MFA set up → linked to Open WebUI) and when they reached it in the `fn_user_sso_status` table (see [Function tables](open-webui-functions.md#function-tables)), prints that table, and shows the stage as each Open WebUI user's profile status (e.g. "📨 SSO: invited"). It only replaces a profile status that's empty or one it wrote itself. Profile statuses are visible to other users and editable by their owner; the table is admin-only. It's a snapshot, so re-run it after inviting or onboarding someone.

### Password rules

New passwords must be **at least 8 characters, with an uppercase letter, a lowercase letter, a number and a symbol**.

- **authentik** ([authentik/blueprints/password-policy.yaml](../authentik/blueprints/password-policy.yaml)) also rejects passwords found in known data breaches (Have I Been Pwned; only the first 5 characters of the password's SHA-1 hash leave the server). Its zxcvbn strength check is off, since it rejected random 8-character passwords that meet the rules. The rules apply when users sign up through an invite and when they change their own password. Passwords an admin sets under **Directory → Users → Set password** are not checked, so follow the rules there by hand.
- **Open WebUI** checks the same length and character rules (`ENABLE_PASSWORD_VALIDATION` / `PASSWORD_VALIDATION_REGEX_PATTERN` in compose) on signup, password change, admin create/edit and the Password Reset Function. With SSO on, these only matter if password sign-in is re-enabled.
- Existing passwords aren't affected until they're next changed.
- **After upgrading authentik**, re-apply the blueprint: authentik re-applies its own default password-change blueprint when it changes, which resets the policy to its default (8 characters, no character rules). Use **Customization → Blueprints → laptop-ai-backend - Password creation rules → Apply**, or:
  ```bash
  docker exec laptop-authentik-worker ak apply_blueprint /blueprints/custom/password-policy.yaml
  ```
- To change the rules, edit both places and keep them in sync. authentik picks up blueprint edits by itself; Open WebUI needs `docker compose -f devops/docker-compose.yml up -d open-webui`.
