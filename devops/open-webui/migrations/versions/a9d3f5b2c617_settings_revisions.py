"""settings revisions

Revision ID: a9d3f5b2c617
Revises: f2c6a9d4b781
Create Date: 2026-10-03

Creates fn_settings_revisions: when each version of devops/open-webui/settings.yaml went live in
Open WebUI's DB, written by devops/open-webui/settings.py (`apply`, `export`, and `record` /
`record --from-git` for revisions from before this table). The KPI Dashboard Function groups answers
by these periods (an answer belongs to the revision live when it was created) to compare versions.

content_hash: first 12 hex chars of sha256 of settings.yaml's text, so the same content is the same
revision whether committed or not; git_commit: last commit touching the file when recorded;
uncommitted: the file had edits not yet committed; source: apply, export, record or git.
started_at is epoch seconds like the other fn_ tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "a9d3f5b2c617"
down_revision = "f2c6a9d4b781"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fn_settings_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("git_commit", sa.Text(), nullable=True),
        sa.Column("uncommitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("fn_settings_revisions_started_at_idx", "fn_settings_revisions", ["started_at"])


def downgrade() -> None:
    op.drop_index("fn_settings_revisions_started_at_idx", table_name="fn_settings_revisions")
    op.drop_table("fn_settings_revisions")
