"""Published lexical chunks. The owning document controls visibility/lifetime."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class KnowledgeChunk(SQLModel, table=True):
    __tablename__ = "knowledgechunk"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledgechunk_document_ordinal"
        ),
        CheckConstraint(
            "chunk_index >= 0 AND token_count >= 0",
            name="ck_knowledgechunk_nonnegative",
        ),
        Index("ix_knowledgechunk_knowledge_base_id", "knowledge_base_id"),
    )

    id: str = Field(primary_key=True, max_length=64)
    document_id: uuid.UUID = Field(
        foreign_key="knowledgedocument.id", ondelete="CASCADE"
    )
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id", ondelete="CASCADE"
    )
    chunk_index: int
    content: str = Field(sa_type=Text)
    content_hash: str = Field(max_length=64)
    index_version: str = Field(max_length=64)
    tokenizer_version: str = Field(max_length=64)
    token_count: int
    term_frequencies: dict[str, int] = Field(
        sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    )
    source_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )
    dense_indexed: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
