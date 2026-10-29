"""Read-only integration boundary for MCP clients and execution inspection."""

import uuid
from collections import Counter
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.assistant.skills import list_skills
from app.models import ExecutionJob
from app.runtime.models import RuntimeCall
from app.services.knowledge_retrieval import Evidence, retrieve_evidence

router = APIRouter(prefix="/integrations", tags=["integrations"])


class EvidenceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_base_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=4, ge=1, le=20)


class EvidenceResults(BaseModel):
    sources: list[Evidence]


class CallView(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    kind: str
    operation: str
    model: str | None
    status: str
    reserved_output_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: float | None
    first_token_ms: float | None
    error_type: str | None
    started_at: datetime
    finished_at: datetime | None


class UsageView(BaseModel):
    call_counts: dict[str, int]
    known_input_tokens: int
    known_output_tokens: int
    usage_complete: bool
    reserved_output_tokens: int
    # No price table is configured; do not turn unknown usage into a zero bill.
    estimated_cost: None = None


class ExecutionView(BaseModel):
    job_id: uuid.UUID
    target_id: uuid.UUID
    kind: Literal["assistant", "research"]
    status: str
    attempt_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    calls: list[CallView]
    usage: UsageView
    calls_truncated: bool


@router.post("/search-evidence", response_model=EvidenceResults)
def search_evidence(body: EvidenceQuery, current_user: CurrentUser):
    """Authenticate locally; models never get to choose the caller identity."""
    from app.runtime.context import ExecutionLimits, execution_context

    # This read-only entry point also needs a bounded provider budget. It is a
    # synchronous lookup, not a background execution, so it has no job ledger.
    with execution_context(limits=ExecutionLimits(embedding=5, seconds=60)):
        return EvidenceResults(
            sources=retrieve_evidence(
                current_user.id, body.knowledge_base_ids, body.query, k=body.k
            )
        )


@router.get("/skills")
def reviewed_skills(_current_user: CurrentUser) -> list[dict]:
    return list_skills()


def _execution(
    session: SessionDep,
    current_user: CurrentUser,
    kind: Literal["assistant", "research"],
    target_id: uuid.UUID,
) -> ExecutionView:
    job = session.exec(
        select(ExecutionJob).where(
            ExecutionJob.kind == kind,
            ExecutionJob.target_id == target_id,
            ExecutionJob.owner_id == current_user.id,
        )
    ).first()
    if job is None:
        raise HTTPException(404, "Execution not found")
    rows = session.exec(
        select(RuntimeCall)
        .where(RuntimeCall.job_id == job.id, RuntimeCall.owner_id == current_user.id)
        .order_by(col(RuntimeCall.started_at), col(RuntimeCall.id))
        .limit(201)
    ).all()
    calls = rows[:200]
    views = [CallView.model_validate(row, from_attributes=True) for row in calls]
    for view in views:
        if view.status == "started" and job.status not in {"queued", "running"}:
            # A missing response cannot establish whether a provider billed it.
            view.status = "unknown"
    provider_calls = [row for row in calls if row.kind != "tool"]
    model_calls = [row for row in provider_calls if row.kind in {"model", "embedding"}]
    counts = Counter(row.kind for row in calls)
    return ExecutionView(
        job_id=job.id,
        target_id=job.target_id,
        kind=kind,
        status=job.status,
        attempt_count=job.attempt_count,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        deadline_at=job.deadline_at,
        calls=views,
        usage=UsageView(
            call_counts=dict(counts),
            known_input_tokens=sum(row.input_tokens or 0 for row in model_calls),
            known_output_tokens=sum(row.output_tokens or 0 for row in model_calls),
            usage_complete=bool(model_calls)
            and all(
                row.input_tokens is not None
                and (row.kind == "embedding" or row.output_tokens is not None)
                for row in model_calls
            )
            and len(rows) <= 200,
            reserved_output_tokens=sum(row.reserved_output_tokens for row in calls),
        ),
        calls_truncated=len(rows) > 200,
    )


@router.get("/turns/{turn_id}/execution", response_model=ExecutionView)
def turn_execution(turn_id: uuid.UUID, session: SessionDep, current_user: CurrentUser):
    return _execution(session, current_user, "assistant", turn_id)


@router.get("/research/{run_id}/execution", response_model=ExecutionView)
def research_execution(
    run_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    return _execution(session, current_user, "research", run_id)
