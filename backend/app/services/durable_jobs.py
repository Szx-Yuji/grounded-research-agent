"""PostgreSQL durable intent queue, leases and fenced worker mutations.

A queued intent is safe to claim after a restart. An expired running attempt is
failed as outcome-unknown, never automatically replayed against a paid provider.
"""

import contextvars
import hashlib
import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import (
    AssistantEvent,
    AssistantTurn,
    AssistantTurnPublic,
    ExecutionJob,
    KnowledgeBase,
    KnowledgeDocument,
    ResearchEvent,
    ResearchRun,
    ResearchRunPublic,
)

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
UNKNOWN_MESSAGE = "The worker stopped before its external-call outcome was known. No paid operation was automatically replayed. Send a new request to retry."


class LeaseLost(RuntimeError):
    """This attempt no longer owns the right to emit or commit a result."""


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    token: uuid.UUID
    kind: str
    target_id: uuid.UUID
    owner_id: uuid.UUID
    payload: dict[str, Any]


_current: contextvars.ContextVar[Claim | None] = contextvars.ContextVar(
    "execution_claim", default=None
)


@contextmanager
def execution_context(claim: Claim) -> Iterator[None]:
    marker = _current.set(claim)
    try:
        yield
    finally:
        _current.reset(marker)


def current_claim() -> Claim | None:
    return _current.get()


def database_now(session: Session) -> datetime:
    return session.exec(select(func.clock_timestamp())).one()


def request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def validate_key(key: str | None) -> str | None:
    if key is not None and (
        not 1 <= len(key) <= 128
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
    ):
        raise HTTPException(
            422, "Idempotency-Key must contain 1-128 visible ASCII characters"
        )
    return key


def lock_conversation_intents(session: Session, conversation_id: uuid.UUID) -> None:
    """Serialize enqueue/delete without inverting worker job→conversation locks."""
    gate = int.from_bytes(
        hashlib.sha256(f"conversation-intent:{conversation_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    session.execute(text("SELECT pg_advisory_xact_lock(:gate)"), {"gate": gate})


def existing_intent(
    session: Session,
    owner_id: uuid.UUID,
    kind: str,
    scope: str,
    key: str | None,
    digest: str,
) -> ExecutionJob | None:
    if key is None:
        return None
    # Serializes identical intent keys across API processes before target creation.
    identity = f"{owner_id}:{kind}:{scope}:{key}"
    gate = int.from_bytes(
        hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True
    )
    session.execute(text("SELECT pg_advisory_xact_lock(:gate)"), {"gate": gate})
    job = session.exec(
        select(ExecutionJob).where(
            ExecutionJob.owner_id == owner_id,
            ExecutionJob.kind == kind,
            ExecutionJob.idempotency_scope == scope,
            ExecutionJob.idempotency_key == key,
        )
    ).first()
    if job is not None and job.request_hash != digest:
        raise HTTPException(
            409, "Idempotency-Key was already used for a different request"
        )
    return job


def enforce_capacity(session: Session, owner_id: uuid.UUID) -> None:
    # A short transaction lock protects the queue admission counts, not execution.
    session.execute(text("SELECT pg_advisory_xact_lock(748382952)"))
    pending = session.exec(
        select(func.count())
        .select_from(ExecutionJob)
        .where(ExecutionJob.status.in_(["queued", "running"]))
    ).one()
    owned = session.exec(
        select(func.count())
        .select_from(ExecutionJob)
        .where(
            ExecutionJob.owner_id == owner_id,
            ExecutionJob.status.in_(["queued", "running"]),
        )
    ).one()
    if (
        pending >= settings.WORKER_QUEUE_LIMIT
        or owned >= settings.WORKER_OWNER_QUEUE_LIMIT
    ):
        raise HTTPException(
            429, "The durable execution queue is full. Retry this intent later."
        )


def enqueue(
    session: Session,
    *,
    owner_id: uuid.UUID,
    kind: str,
    target_id: uuid.UUID,
    payload: dict[str, Any],
    key: str | None,
    scope: str,
    digest: str,
) -> ExecutionJob:
    job = ExecutionJob(
        owner_id=owner_id,
        kind=kind,
        target_id=target_id,
        payload=payload,
        idempotency_key=key,
        idempotency_scope=scope,
        request_hash=digest,
    )
    session.add(job)
    session.flush()
    return job


def _target(session: Session, job: ExecutionJob):
    model = {
        "assistant": AssistantTurn,
        "research": ResearchRun,
        "document": KnowledgeDocument,
    }[job.kind]
    return session.exec(
        select(model).where(model.id == job.target_id).with_for_update()
    ).first()


def _fail_target(
    session: Session, job: ExecutionJob, message: str, status: str = "failed"
) -> None:
    target = _target(session, job)
    if target is None or target.status in {"succeeded", "failed", "cancelled", "ready"}:
        return
    target.status = "failed" if job.kind == "document" else status
    target.error_message = message if status != "cancelled" else None
    if job.kind != "document":
        target.updated_at = database_now(session)
    session.add(target)
    if job.kind == "document":
        return
    event_model = AssistantEvent if job.kind == "assistant" else ResearchEvent
    id_field = {"turn_id" if job.kind == "assistant" else "run_id": target.id}
    payload = {"message": message}
    if status == "cancelled":
        payload = (
            {"turn": AssistantTurnPublic.model_validate(target).model_dump(mode="json")}
            if job.kind == "assistant"
            else ResearchRunPublic.model_validate(target).model_dump(mode="json")
        )
    session.add(
        event_model(
            **id_field,
            event_type="cancelled" if status == "cancelled" else "error",
            payload=payload,
        )
    )


def expire_abandoned(limit: int = 100) -> int:
    with Session(engine) as session:
        now = database_now(session)
        jobs = session.exec(
            select(ExecutionJob)
            .where(
                ExecutionJob.status == "running", ExecutionJob.lease_expires_at <= now
            )
            .order_by(ExecutionJob.lease_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            job.status = "cancelled" if job.cancel_requested_at else "interrupted"
            job.error_message = None if job.cancel_requested_at else UNKNOWN_MESSAGE
            job.finished_at = now
            _fail_target(
                session,
                job,
                UNKNOWN_MESSAGE,
                "cancelled" if job.cancel_requested_at else "failed",
            )
            session.add(job)
        session.commit()
        return len(jobs)


def claim_next(worker_id: str, kinds: list[str] | None = None) -> Claim | None:
    with Session(engine) as session:
        statement = select(ExecutionJob).where(ExecutionJob.status == "queued")
        if kinds:
            statement = statement.where(ExecutionJob.kind.in_(kinds))
        job = session.exec(
            statement.order_by(ExecutionJob.created_at, ExecutionJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if job is None:
            return None
        target = _target(session, job)
        if (
            target is None
            or target.status in {"succeeded", "failed", "cancelled", "ready"}
            or job.cancel_requested_at
        ):
            job.status = "cancelled"
            job.finished_at = database_now(session)
            session.add(job)
            session.commit()
            return None
        now = database_now(session)
        job.status = "running"
        job.lease_token = uuid.uuid4()
        job.worker_id = worker_id
        job.attempt_count += 1
        job.started_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.WORKER_LEASE_SECONDS)
        job.deadline_at = now + timedelta(seconds=settings.RESEARCH_TIMEOUT_SECONDS)
        session.add(job)
        claim = Claim(
            job.id,
            job.lease_token,
            job.kind,
            job.target_id,
            job.owner_id,
            dict(job.payload),
        )
        session.commit()
        return claim


def assert_lease(
    session: Session,
    job_id: uuid.UUID | None = None,
    lease_token: uuid.UUID | None = None,
    *,
    permit_cancelled: bool = False,
) -> ExecutionJob:
    claim = current_claim()
    if job_id is None or lease_token is None:
        if claim is None:
            raise LeaseLost("Execution requires a claimed durable job")
        job_id, lease_token = claim.job_id, claim.token
    job = session.exec(
        select(ExecutionJob).where(ExecutionJob.id == job_id).with_for_update()
    ).first()
    now = database_now(session)
    if (
        job is None
        or job.status != "running"
        or job.lease_token != lease_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= now
    ):
        raise LeaseLost("The worker lease expired or was superseded")
    if job.cancel_requested_at and not permit_cancelled:
        raise LeaseLost("Execution was cancelled")
    return job


def finish_in_transaction(
    session: Session, status: str, error: str | None = None
) -> None:
    job = assert_lease(session, permit_cancelled=True)
    if job.cancel_requested_at and status != "cancelled":
        raise LeaseLost("Cancelled attempts cannot commit another outcome")
    job.status = status
    job.error_message = error
    job.finished_at = database_now(session)
    session.add(job)


def should_stop(claim: Claim) -> bool:
    try:
        with Session(engine) as session:
            job = session.get(ExecutionJob, claim.job_id)
            now = database_now(session)
            return (
                job is None
                or job.status != "running"
                or job.lease_token != claim.token
                or job.cancel_requested_at is not None
                or job.lease_expires_at is None
                or job.lease_expires_at <= now
                or (job.deadline_at is not None and job.deadline_at <= now)
            )
    except Exception:
        # Failure to confirm ownership must not permit another external call.
        return True


def heartbeat(claim: Claim) -> bool:
    with Session(engine) as session:
        job = session.exec(
            select(ExecutionJob)
            .where(ExecutionJob.id == claim.job_id)
            .with_for_update()
        ).first()
        now = database_now(session)
        if (
            job is None
            or job.status != "running"
            or job.lease_token != claim.token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
            or job.cancel_requested_at is not None
            or (job.deadline_at is not None and job.deadline_at <= now)
        ):
            return False
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.WORKER_LEASE_SECONDS)
        session.add(job)
        session.commit()
        return True


def cancel_target(session: Session, kind: str, target_id: uuid.UUID) -> None:
    job = session.exec(
        select(ExecutionJob)
        .where(ExecutionJob.kind == kind, ExecutionJob.target_id == target_id)
        .with_for_update()
    ).first()
    if job is None or job.status in TERMINAL:
        return
    now = database_now(session)
    job.cancel_requested_at = now
    # A running job retains its lease until its worker actually exits; no new
    # worker can start the same operation while a synchronous request winds down.
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = now
    _fail_target(session, job, "Cancelled by the user", "cancelled")
    session.add(job)


def execute_claim(claim: Claim, stop: threading.Event | None = None) -> None:
    local_stop = stop or threading.Event()
    heartbeat_stop = threading.Event()

    def renew():
        while not heartbeat_stop.wait(settings.WORKER_HEARTBEAT_SECONDS):
            try:
                if not heartbeat(claim):
                    local_stop.set()
                    return
            except Exception:
                local_stop.set()
                return

    pulse = threading.Thread(target=renew, daemon=True, name=f"lease-{claim.job_id}")
    pulse.start()
    try:
        from app.runtime.context import execution_context as budget_context

        with execution_context(claim):
            with Session(engine) as session:
                job = assert_lease(session, permit_cancelled=True)
                deadline = job.deadline_at
            with budget_context(
                claim.job_id,
                claim.owner_id,
                lease_token=claim.token,
                deadline_at=deadline,
            ):
                if claim.kind == "assistant":
                    from app.services.assistant_conversations import _execute_turn

                    _execute_turn(claim.target_id, local_stop)
                elif claim.kind == "research":
                    from app.services.research_runs import _execute_run

                    _execute_run(claim.target_id, local_stop)
                elif claim.kind == "document":
                    from app.research.events import emitter_context
                    from app.services.document_processor import ingest_document

                    with emitter_context(
                        lambda *_args: None,
                        lambda: local_stop.is_set() or should_stop(claim),
                    ):
                        ingest_document(claim.target_id)
                else:
                    raise ValueError("Unknown durable job kind")
    finally:
        heartbeat_stop.set()
        pulse.join(timeout=2)


def enqueue_document(session: Session, document: KnowledgeDocument) -> ExecutionJob:
    """Atomically enqueue an upload or an explicit retry of a failed index job.

    Caller has already authenticated ownership and sets document.status to
    uploaded. The helper checks server ownership again and serializes concurrent
    retries. A live old lease is never reset while its external request runs.
    """
    with session.no_autoflush:
        job = session.exec(
            select(ExecutionJob)
            .where(
                ExecutionJob.kind == "document", ExecutionJob.target_id == document.id
            )
            .with_for_update()
        ).first()
    kb = session.get(KnowledgeBase, document.knowledge_base_id)
    if kb is None:
        raise HTTPException(404, "Knowledge base not found")
    if job is not None and job.status in {"queued", "running"}:
        raise HTTPException(409, "Document indexing is still queued or running")
    enforce_capacity(session, kb.owner_id)
    payload = {
        "document_id": str(document.id),
        "knowledge_base_id": str(document.knowledge_base_id),
    }
    if job is None:
        return enqueue(
            session,
            owner_id=kb.owner_id,
            kind="document",
            target_id=document.id,
            payload=payload,
            key=None,
            scope=str(document.id),
            digest=request_hash(payload),
        )
    job.status = "queued"
    job.payload = payload
    job.lease_token = None
    job.worker_id = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.cancel_requested_at = None
    job.deadline_at = None
    job.started_at = None
    job.finished_at = None
    job.error_message = None
    job.created_at = database_now(session)
    session.add(job)
    session.flush()
    return job


def delete_target_jobs(
    session: Session, kind: str, target_ids: list[uuid.UUID], owner_id: uuid.UUID
) -> None:
    """Delete snapshots/call metadata before locking the polymorphic targets."""
    if not target_ids:
        return
    rows = session.exec(
        select(ExecutionJob)
        .where(
            ExecutionJob.kind == kind,
            ExecutionJob.target_id.in_(target_ids),
            ExecutionJob.owner_id == owner_id,
        )
        .order_by(ExecutionJob.id)
        .with_for_update()
    ).all()
    for job in rows:
        session.delete(job)
    session.flush()
