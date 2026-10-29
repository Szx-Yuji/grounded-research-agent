"""Persist permission-scoped lexical chunks without touching existing documents.

Revision ID: d2e3f4a50202
Revises: d1e2f3a40101
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d2e3f4a50202"
down_revision = "d1e2f3a40101"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledgechunk",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("index_version", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("term_frequencies", postgresql.JSONB(), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("dense_indexed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledgedocument.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledgebase.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledgechunk_document_ordinal"),
        sa.CheckConstraint("chunk_index >= 0 AND token_count >= 0", name="ck_knowledgechunk_nonnegative"),
    )
    op.create_index("ix_knowledgechunk_knowledge_base_id", "knowledgechunk", ["knowledge_base_id"])


def downgrade():
    op.drop_index("ix_knowledgechunk_knowledge_base_id", table_name="knowledgechunk")
    op.drop_table("knowledgechunk")
