"""function tables

Revision ID: 7b3e9c1d5a20
Revises:
Create Date: 2026-09-30

Creates the tables the Open WebUI event Functions keep their state in, replacing the SQLite files
they used before:

- fn_email_verified — users verified by signup_email_verification.py (verified at most once, so an
  admin can suspend a verified user by setting them back to pending)
- fn_password_age — when each password was last set, for password_expiry.py

No foreign key to Open WebUI's "user" table on purpose: that table belongs to Open WebUI's own
migrations, and a constraint could block them. Rows for deleted users are harmless.

Also copies rows from the old SQLite files if they're present at $LEGACY_FUNCTION_DATA_DIR (the
open-webui data volume, mounted read-only into the migrate service), so nobody's verification
status or password-age clock resets. Existing rows win on conflict, so re-running is safe.
"""
import logging
import os
import sqlite3

from alembic import op
import sqlalchemy as sa

revision = "7b3e9c1d5a20"
down_revision = None
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _legacy_rows(filename: str, query: str) -> list[tuple]:
    data_dir = os.environ.get("LEGACY_FUNCTION_DATA_DIR", "")
    path = os.path.join(data_dir, filename) if data_dir else ""
    if not path or not os.path.exists(path):
        return []
    try:
        # Read-only URI, so a missing table or file never gets created on the volume.
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            return conn.execute(query).fetchall()
    except sqlite3.Error as e:
        log.warning("Skipping legacy data in %s: %s", path, e)
        return []


def upgrade() -> None:
    op.create_table(
        "fn_email_verified",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("verified_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "fn_password_age",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("changed_at", sa.BigInteger(), nullable=False),
        sa.Column("last_warned_at", sa.BigInteger(), nullable=False, server_default="0"),
    )

    conn = op.get_bind()
    verified = _legacy_rows("email_verification.db", "SELECT user_id, verified_at FROM verified_users")
    for user_id, verified_at in verified:
        conn.execute(
            sa.text(
                "INSERT INTO fn_email_verified (user_id, verified_at) VALUES (:u, :t) ON CONFLICT (user_id) DO NOTHING"
            ),
            {"u": user_id, "t": verified_at},
        )
    ages = _legacy_rows("password_expiry.db", "SELECT user_id, changed_at, last_warned_at FROM password_age")
    for user_id, changed_at, last_warned_at in ages:
        conn.execute(
            sa.text(
                "INSERT INTO fn_password_age (user_id, changed_at, last_warned_at) VALUES (:u, :c, :w) "
                "ON CONFLICT (user_id) DO NOTHING"
            ),
            {"u": user_id, "c": changed_at, "w": last_warned_at},
        )
    log.info("Imported %d verified users and %d password ages from legacy SQLite files", len(verified), len(ages))


def downgrade() -> None:
    op.drop_table("fn_password_age")
    op.drop_table("fn_email_verified")
