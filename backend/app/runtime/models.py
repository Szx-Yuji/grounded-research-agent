"""Call metadata only: never persist prompts, credentials or provider bodies."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index
from sqlmodel import Field, SQLModel

from app.models import get_datetime_utc


class RuntimeCall(SQLModel, table=True):
    __table_args__ = (Index("ix_runtimecall_job_started", "job_id", "started_at"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="executionjob.id", ondelete="CASCADE")
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    lease_token: uuid.UUID
    parent_id: uuid.UUID | None = Field(default=None, index=True)
    kind: str = Field(max_length=24)
    operation: str = Field(max_length=80)
    model: str | None = Field(default=None, max_length=160)
    status: str = Field(default="started", max_length=24)
    reserved_output_tokens: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: float | None = None
    first_token_ms: float | None = None
    error_type: str | None = Field(default=None, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    started_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
