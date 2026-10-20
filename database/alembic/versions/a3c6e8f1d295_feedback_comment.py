"""feedback_comment: what the user said was wrong with an answer

Revision ID: a3c6e8f1d295
Revises: f7b3d9e2a184
"""

from alembic import op
import sqlalchemy as sa

revision = "a3c6e8f1d295"
down_revision = "f7b3d9e2a184"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("feedback_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "feedback_comment")
