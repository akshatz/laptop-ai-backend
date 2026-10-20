"""document_versions: content hash, version and embedding status on chat_documents

Revision ID: c9e2f4a7b318
Revises: b4d8e1f6a273
"""

from alembic import op
import sqlalchemy as sa

revision = "c9e2f4a7b318"
down_revision = "b4d8e1f6a273"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_documents", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("chat_documents", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("chat_documents", sa.Column("status", sa.String(20), server_default="ready", nullable=False))
    op.add_column("chat_documents", sa.Column("error", sa.Text(), nullable=True))
    op.execute("UPDATE chat_documents SET sha256 = encode(sha256(data), 'hex')")


def downgrade() -> None:
    for column in ("error", "status", "version", "sha256"):
        op.drop_column("chat_documents", column)
