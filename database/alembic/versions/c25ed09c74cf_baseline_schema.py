"""baseline schema

Revision ID: c25ed09c74cf
Revises:
Create Date: 2026-09-27

Creates the users, user_chats, and messages tables from scratch, matching
database/init_db.py's models. Only meant to run against a brand new,
empty database - an already-patched dev database should be stamped at
this revision instead of having this migration applied (see the follow-up
"sync_existing_dev_db" revision / README note).
"""
from alembic import op
import sqlalchemy as sa

revision = "c25ed09c74cf"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), server_default="user", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verification_token", sa.String(64), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("mfa_otp_code", sa.String(6), nullable=True),
        sa.Column("mfa_otp_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_reset_otp", sa.String(6), nullable=True),
        sa.Column("password_reset_otp_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_chats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), server_default="New Chat", nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_chats_user", "user_chats", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("user_chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_msgs_chat", "messages", ["chat_id"])


def downgrade() -> None:
    op.drop_index("idx_msgs_chat", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_chats_user", table_name="user_chats")
    op.drop_table("user_chats")
    op.drop_table("users")
