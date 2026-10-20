"""answer_versions: answers replaced by Regenerate stay readable

Revision ID: b8d2f5a9c741
Revises: a3c6e8f1d295
"""

from alembic import op
import sqlalchemy as sa

revision = "b8d2f5a9c741"
down_revision = "a3c6e8f1d295"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("earlier_versions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "earlier_versions")
