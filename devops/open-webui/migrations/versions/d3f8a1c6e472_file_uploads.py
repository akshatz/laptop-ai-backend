"""file uploads

Revision ID: d3f8a1c6e472
Revises: b7e4c2a9f350
Create Date: 2026-10-18

Creates fn_file_uploads for the Duplicate Upload Notice Function: one row per file uploaded in
Open WebUI, keyed by Open WebUI's file id, with the sha256 of the uploaded bytes (Open WebUI's
file.meta.file_hash), so copies of the same file are found by an indexed lookup on
(user_id, file_hash) whatever their names. Open WebUI processes every chat upload again (no
duplicate check outside knowledge bases), so the same PDF uploaded twice is two files.

fn_duplicate_uploads lists every upload whose content the same person had uploaded before:
copy_number 2 is the first repeat, first_file_id/first_uploaded_at the original.

Existing uploads are copied from Open WebUI's file table when it exists (it doesn't in the
migrations test database). Rows stay when Open WebUI deletes a file (no FK on purpose, like the
other fn_ tables), so the history of repeats survives; the User Cleanup Function deletes a user's
rows with their account.
"""
from alembic import op
import sqlalchemy as sa

revision = "d3f8a1c6e472"
down_revision = "b7e4c2a9f350"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fn_file_uploads",
        sa.Column("file_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("uploaded_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("fn_file_uploads_user_hash_idx", "fn_file_uploads", ["user_id", "file_hash", "uploaded_at"])
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.file') IS NOT NULL THEN
                INSERT INTO fn_file_uploads (file_id, user_id, file_hash, filename, content_type, size, uploaded_at)
                SELECT id, user_id, meta->>'file_hash', filename, meta->>'content_type',
                       NULLIF(meta->>'size', '')::bigint, created_at
                FROM file
                WHERE meta->>'file_hash' IS NOT NULL AND user_id IS NOT NULL AND created_at IS NOT NULL
                ON CONFLICT (file_id) DO NOTHING;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE VIEW fn_duplicate_uploads AS
        SELECT * FROM (
            SELECT u.*,
                   row_number() OVER w AS copy_number,
                   first_value(file_id) OVER w AS first_file_id,
                   first_value(uploaded_at) OVER w AS first_uploaded_at
            FROM fn_file_uploads u
            WINDOW w AS (PARTITION BY user_id, file_hash ORDER BY uploaded_at, file_id)
        ) ranked
        WHERE copy_number > 1
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW fn_duplicate_uploads")
    op.drop_index("fn_file_uploads_user_hash_idx", table_name="fn_file_uploads")
    op.drop_table("fn_file_uploads")
