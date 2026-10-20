"""message_feedback: like/dislike on answers

Revision ID: b4d8e1f6a273
Revises: a7c3e9f2b416
"""

from alembic import op
import sqlalchemy as sa

revision = "b4d8e1f6a273"
down_revision = "a7c3e9f2b416"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("feedback", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "feedback")
