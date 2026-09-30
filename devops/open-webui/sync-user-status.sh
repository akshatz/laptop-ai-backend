#!/usr/bin/env bash
# Syncs each person's SSO onboarding stage into fn_user_sso_status (open_webui DB, created by
# devops/open-webui/migrations/), from authentik's DB (invitations, users, TOTP devices) and
# Open WebUI's user/oauth_session tables, then mirrors it into Open WebUI profile statuses:
#
#   linked   ✅ SSO: linked                               first SSO login done (user.oauth set)
#   mfa      🔐 SSO: MFA set up, not signed in yet        authentik account with confirmed TOTP
#   account  🔑 SSO: account created, MFA not set up      authentik account, no TOTP yet
#   invited  📨 SSO: invited                              unexpired invite pre-filled with the email
#   none     ⛔ SSO: not invited                          none of the above; can't sign in
#   removed  (table only)                                 gone from both authentik and Open WebUI
#
# Matched by lowercased email. The profile status is only overwritten when empty or written by
# this script (message starting "SSO: "), so a status a user set themselves is kept; it's
# user-editable and visible to other users, while the table is admin-only. Point-in-time:
# re-run after inviting/onboarding users. Needs the migrations applied (docker compose up).
#
# Usage: devops/open-webui/sync-user-status.sh
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
source ../.env
set +a

psql_in() { docker compose exec -T postgres-db psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -At "$@"; }

authentik_state=$(psql_in -d authentik -c "
  WITH people AS (
    SELECT lower(u.email) AS email,
           min(extract(epoch FROM u.date_joined))::bigint AS account_at,
           min(extract(epoch FROM t.created)) FILTER (WHERE t.confirmed)::bigint AS mfa_at
    FROM authentik_core_user u
    LEFT JOIN authentik_stages_authenticator_totp_totpdevice t ON t.user_id = u.id
    WHERE u.email <> '' AND u.type IN ('internal', 'external')
    GROUP BY lower(u.email)
  ),
  invites AS (
    SELECT DISTINCT lower(fixed_data->>'email') AS email
    FROM authentik_stages_invitation_invitation
    WHERE fixed_data->>'email' <> '' AND (NOT expiring OR expires > now())
  )
  SELECT coalesce(json_agg(r), '[]')
  FROM (
    SELECT coalesce(p.email, i.email) AS email, p.account_at, p.mfa_at, i.email IS NOT NULL AS invited
    FROM people p FULL JOIN invites i USING (email)
  ) r")

psql_in -q -d open_webui -v state="$authentik_state" <<'SQL'
BEGIN;

CREATE TEMP TABLE sso_now ON COMMIT DROP AS
WITH a AS (
  SELECT * FROM jsonb_to_recordset(:'state'::jsonb)
    AS x(email text, account_at bigint, mfa_at bigint, invited boolean)
),
o AS (
  SELECT lower(u.email) AS email, u.id AS user_id,
         (u.oauth IS NOT NULL AND u.oauth::text <> 'null') AS linked,
         (SELECT min(s.created_at) FROM oauth_session s WHERE s.user_id = u.id) AS first_session_at
  FROM "user" u
  WHERE u.email IS NOT NULL
)
SELECT coalesce(o.email, a.email) AS email, o.user_id, a.invited, a.account_at, a.mfa_at,
       CASE WHEN o.linked THEN coalesce(o.first_session_at, extract(epoch FROM now())::bigint) END AS linked_at,
       CASE WHEN o.linked              THEN 'linked'
            WHEN a.mfa_at IS NOT NULL  THEN 'mfa'
            WHEN a.account_at IS NOT NULL THEN 'account'
            WHEN a.invited             THEN 'invited'
            ELSE 'none' END AS stage
FROM o FULL JOIN a USING (email);

INSERT INTO fn_user_sso_status AS t
  (email, user_id, stage, invited_at, account_created_at, mfa_at, linked_at, stage_changed_at, synced_at)
SELECT email, user_id, stage,
       CASE WHEN invited THEN extract(epoch FROM now())::bigint END,
       account_at, mfa_at, linked_at,
       extract(epoch FROM now())::bigint, extract(epoch FROM now())::bigint
FROM sso_now
ON CONFLICT (email) DO UPDATE SET
  user_id            = coalesce(excluded.user_id, t.user_id),
  stage              = excluded.stage,
  invited_at         = coalesce(t.invited_at, excluded.invited_at),
  account_created_at = coalesce(t.account_created_at, excluded.account_created_at),
  mfa_at             = coalesce(t.mfa_at, excluded.mfa_at),
  linked_at          = coalesce(t.linked_at, excluded.linked_at),
  stage_changed_at   = CASE WHEN t.stage IS DISTINCT FROM excluded.stage
                            THEN excluded.stage_changed_at ELSE t.stage_changed_at END,
  synced_at          = excluded.synced_at;

UPDATE fn_user_sso_status t
SET stage = 'removed', stage_changed_at = extract(epoch FROM now())::bigint,
    synced_at = extract(epoch FROM now())::bigint
WHERE t.stage <> 'removed' AND NOT EXISTS (SELECT 1 FROM sso_now n WHERE n.email = t.email);

WITH label(stage, emoji, message) AS (VALUES
  ('linked',  '✅', 'SSO: linked'),
  ('mfa',     '🔐', 'SSO: MFA set up, not signed in yet'),
  ('account', '🔑', 'SSO: account created, MFA not set up'),
  ('invited', '📨', 'SSO: invited'),
  ('none',    '⛔', 'SSO: not invited')
)
UPDATE "user" u
SET status_emoji = l.emoji, status_message = l.message, status_expires_at = NULL
FROM fn_user_sso_status s JOIN label l USING (stage)
WHERE s.user_id = u.id
  AND (u.status_message IS NULL OR u.status_message = '' OR u.status_message LIKE 'SSO: %')
  AND u.status_message IS DISTINCT FROM l.message;

COMMIT;
SQL

psql_in -d open_webui -F ' | ' -c "
  SELECT email, stage,
         coalesce(to_char(to_timestamp(invited_at), 'YYYY-MM-DD HH24:MI'), '-') AS invited,
         coalesce(to_char(to_timestamp(account_created_at), 'YYYY-MM-DD HH24:MI'), '-') AS account,
         coalesce(to_char(to_timestamp(mfa_at), 'YYYY-MM-DD HH24:MI'), '-') AS mfa,
         coalesce(to_char(to_timestamp(linked_at), 'YYYY-MM-DD HH24:MI'), '-') AS linked
  FROM fn_user_sso_status ORDER BY stage_changed_at, email"
