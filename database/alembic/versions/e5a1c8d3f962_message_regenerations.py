"""message_regenerations: how often an answer was regenerated

Revision ID: e5a1c8d3f962
Revises: c9e2f4a7b318
"""

from alembic import op
import sqlalchemy as sa

revision = "e5a1c8d3f962"
down_revision = "c9e2f4a7b318"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("regenerations", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("messages", "regenerations")
