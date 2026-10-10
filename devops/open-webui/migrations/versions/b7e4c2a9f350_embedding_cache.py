"""embedding cache

Revision ID: b7e4c2a9f350
Revises: a9d3f5b2c617
Create Date: 2026-10-10

Creates fn_embedding_cache for the Embedding Cache Function: one row per document chunk already
embedded, so a web page fetched again (about 25% of fetched pages in the week to 2026-10-10, nearly
all within a day) isn't sent to Ollama again.

key: sha256 hex of engine, model, prefix and the chunk text (a changed page, model or prefix is a
miss, never a wrong vector); vector: float32 little-endian bytes; dims: vector length, checked on
read. created_at/last_hit are epoch seconds like the other fn_ tables; rows unused for the Function's
max_age_days are deleted by the Function.
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e4c2a9f350"
down_revision = "a9d3f5b2c617"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fn_embedding_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_hit", sa.BigInteger(), nullable=False),
    )
    op.create_index("fn_embedding_cache_last_hit_idx", "fn_embedding_cache", ["last_hit"])


def downgrade() -> None:
    op.drop_index("fn_embedding_cache_last_hit_idx", table_name="fn_embedding_cache")
    op.drop_table("fn_embedding_cache")
