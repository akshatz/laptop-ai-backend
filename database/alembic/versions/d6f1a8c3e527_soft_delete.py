"""soft_delete: deleted chats and documents are kept (deleted_at) and can be restored

Revision ID: d6f1a8c3e527
Revises: b8d2f5a9c741
"""

from alembic import op
import sqlalchemy as sa

revision = "d6f1a8c3e527"
down_revision = "b8d2f5a9c741"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_chats", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_documents", "deleted_at")
    op.drop_column("user_chats", "deleted_at")
