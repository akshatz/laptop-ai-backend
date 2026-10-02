"""user sso status

Revision ID: c4a8e2f61b93
Revises: 7b3e9c1d5a20
Create Date: 2026-09-30

Creates fn_user_sso_status: each person's SSO onboarding stage, filled by
devops/open-webui/sync-user-status.sh from authentik's DB and Open WebUI's user table. Admin-only
(unlike the user-editable profile status the script also writes).

Keyed by lowercased email rather than Open WebUI user id, because an invited person has no Open
WebUI account until their first SSO login; user_id is filled in once they have one.

stage: none (no invite or authentik account), invited, account (authentik account, no TOTP), mfa
(TOTP confirmed, never signed in to Open WebUI), linked (first SSO login done), removed (no longer
in authentik or Open WebUI). Timestamps are epoch seconds like the other fn_ tables; each is set the
first time that step is seen and kept afterwards. account_created_at/mfa_at/linked_at come from
authentik's date_joined, the TOTP device's creation time and Open WebUI's earliest oauth_session;
invitations have no creation time, so invited_at is when the sync first saw the invite.

No foreign key to Open WebUI's "user" table, same as the other fn_ tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4a8e2f61b93"
down_revision = "7b3e9c1d5a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fn_user_sso_status",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("invited_at", sa.BigInteger(), nullable=True),
        sa.Column("account_created_at", sa.BigInteger(), nullable=True),
        sa.Column("mfa_at", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.BigInteger(), nullable=True),
        sa.Column("stage_changed_at", sa.BigInteger(), nullable=False),
        sa.Column("synced_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("email = lower(email)", name="fn_user_sso_status_email_lower"),
        sa.CheckConstraint(
            "stage IN ('none', 'invited', 'account', 'mfa', 'linked', 'removed')",
            name="fn_user_sso_status_stage",
        ),
    )


def downgrade() -> None:
    op.drop_table("fn_user_sso_status")
