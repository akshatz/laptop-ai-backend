"""archived chats

Revision ID: e5b1d7a3c902
Revises: c4a8e2f61b93
Create Date: 2026-10-02

Creates fn_archived_chats: copies of a user's chats, taken by the authentik User Cleanup Function
(devops/open-webui/functions/authentik_user_cleanup.py) just before an admin deletes that user in
Open WebUI, which would otherwise delete the chats with the account. Admin-only, shown at
/api/v1/archived-chats.

One row per chat, keyed by Open WebUI's chat id. `chat` is the chat row's full JSON history and
`messages` the chat's rows from Open WebUI's newer chat_message table (null if it had none), so
nothing is lost whichever one a given Open WebUI version reads. The user's email and name are copied
because the user row is deleted right after. Attachments aren't copied: the user's uploaded files
are deleted with the account. Timestamps are epoch seconds like the other fn_ tables.

No foreign keys to Open WebUI's tables, same as the other fn_ tables — the rows outlive the user.
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b1d7a3c902"
down_revision = "c4a8e2f61b93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fn_archived_chats",
        sa.Column("chat_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("chat", sa.JSON(), nullable=True),
        sa.Column("messages", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
        sa.Column("archived_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_by", sa.Text(), nullable=True),
    )
    op.create_index("fn_archived_chats_user_email", "fn_archived_chats", ["user_email"])


def downgrade() -> None:
    op.drop_index("fn_archived_chats_user_email", table_name="fn_archived_chats")
    op.drop_table("fn_archived_chats")
