"""sync existing dev db

Revision ID: f3a9b7d1e824
Revises: c25ed09c74cf
Create Date: 2026-09-27

Conditional bridge for databases that reached the baseline schema via
database/init_db.py's own ad hoc ALTER TABLE ... IF NOT EXISTS logic (the
pre-Alembic migration path) rather than through c25ed09c74cf. Checks the
live schema instead of assuming either state, so this migration is safe to
run against:

  - a fresh database that just applied c25ed09c74cf (columns already
    present -> no-op)
  - an existing dev database patched by init_db.py before Alembic existed
    (columns/index missing -> creates them to match c25ed09c74cf)

Downgrade only removes what this revision itself added, so a database that
already had these columns before c25ed09c74cf ran is left untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "f3a9b7d1e824"
down_revision = "c25ed09c74cf"
branch_labels = None
depends_on = None

_ADDED_COLUMNS_BY_THIS_REVISION: set[tuple[str, str]] = set()


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def _existing_indexes(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    users_columns = _existing_columns("users")

    if "password_changed_at" not in users_columns:
        op.add_column(
            "users",
            sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "password_changed_at"))

    if "is_active" not in users_columns:
        op.add_column("users", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "is_active"))

    if "is_verified" not in users_columns:
        op.add_column("users", sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "is_verified"))

    if "verification_token" not in users_columns:
        op.add_column("users", sa.Column("verification_token", sa.String(64), nullable=True))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "verification_token"))

    if "mfa_enabled" not in users_columns:
        op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "mfa_enabled"))

    if "mfa_otp_code" not in users_columns:
        op.add_column("users", sa.Column("mfa_otp_code", sa.String(6), nullable=True))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "mfa_otp_code"))

    if "mfa_otp_expires_at" not in users_columns:
        op.add_column("users", sa.Column("mfa_otp_expires_at", sa.DateTime(timezone=True), nullable=True))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "mfa_otp_expires_at"))

    if "password_reset_otp" not in users_columns:
        op.add_column("users", sa.Column("password_reset_otp", sa.String(6), nullable=True))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "password_reset_otp"))

    if "password_reset_otp_expires_at" not in users_columns:
        op.add_column("users", sa.Column("password_reset_otp_expires_at", sa.DateTime(timezone=True), nullable=True))
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("users", "password_reset_otp_expires_at"))

    user_chats_columns = _existing_columns("user_chats")

    if "updated_at" not in user_chats_columns:
        op.add_column(
            "user_chats",
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        _ADDED_COLUMNS_BY_THIS_REVISION.add(("user_chats", "updated_at"))

    if "idx_chats_user" not in _existing_indexes("user_chats"):
        op.create_index("idx_chats_user", "user_chats", ["user_id"])

    if "idx_msgs_chat" not in _existing_indexes("messages"):
        op.create_index("idx_msgs_chat", "messages", ["chat_id"])


def downgrade() -> None:
    for table_name, column_name in _ADDED_COLUMNS_BY_THIS_REVISION:
        op.drop_column(table_name, column_name)
    _ADDED_COLUMNS_BY_THIS_REVISION.clear()
