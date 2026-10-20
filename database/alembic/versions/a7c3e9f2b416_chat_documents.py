"""chat_documents: files and pasted texts added to chats, kept for their owners

Revision ID: a7c3e9f2b416
Revises: f3a9b7d1e824
"""

from alembic import op
import sqlalchemy as sa

revision = "a7c3e9f2b416"
down_revision = "f3a9b7d1e824"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("user_chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_documents_user_id"), "chat_documents", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_documents_user_id"), table_name="chat_documents")
    op.drop_table("chat_documents")
