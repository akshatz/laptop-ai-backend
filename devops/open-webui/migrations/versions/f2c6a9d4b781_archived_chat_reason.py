"""archived chat reason

Revision ID: f2c6a9d4b781
Revises: e5b1d7a3c902
Create Date: 2026-10-02

Adds fn_archived_chats.reason, now that two Functions write to the table:
- account_deleted: copied by the authentik User Cleanup Function just before an admin deleted the
  whole user (all rows that existed before this revision);
- chat_deleted: copied by the Chat Soft Delete Function just before a chat was deleted (one chat,
  "delete all chats", or a folder deleted with its contents). These can be restored for their owner.
"""
from alembic import op
import sqlalchemy as sa

revision = "f2c6a9d4b781"
down_revision = "e5b1d7a3c902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fn_archived_chats", sa.Column("reason", sa.Text(), nullable=True))
    op.execute("UPDATE fn_archived_chats SET reason = 'account_deleted' WHERE reason IS NULL")


def downgrade() -> None:
    op.drop_column("fn_archived_chats", "reason")
