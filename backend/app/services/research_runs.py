"""Persist runs/events around a bounded, single-process research worker pool.

This first integration deliberately does not promise durable graph execution.
A restart marks unfinished runs failed. Completed reports and SSE events survive.
"""

import logging
import threading
import time
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import (
    ResearchCreate,
    ResearchEvent,
    ResearchRun,
    ResearchRunPublic,
    get_datetime_utc,
)
from app.research.errors import ResearchExecutionError
from app.services import durable_jobs
from app.services.knowledge_retrieval import validate_knowledge_access

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed", "cancelled"}
RESTART_MESSAGE = "Research was interrupted by a server restart. Start a new run."


def owned_run(session: Session, run_id: uuid.UUID, owner_id: uuid.UUID) -> ResearchRun:
    reconcile_finishes()
    run = session.exec(
        select(ResearchRun).where(
            ResearchRun.id == run_id, ResearchRun.owner_id == owner_id
        )
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return run


def _payload(run: ResearchRun) -> dict[str, Any]:
    return ResearchRunPublic.model_validate(run).model_dump(mode="json")


def _event(
    session: Session, run: ResearchRun, event: str, data: dict[str, Any]
) -> None:
    session.add(ResearchEvent(run_id=run.id, event_type=event, payload=data))


def recover_interrupted_runs() -> None:
    """Only expired claimed jobs may be interrupted; queued intents survive."""
    durable_jobs.expire_abandoned()


def shutdown_research() -> None:
    """API shutdown does not own independent worker execution."""


def start_run(
    owner_id: uuid.UUID, request: ResearchCreate, idempotency_key: str | None = None
) -> ResearchRunPublic:
    key = durable_jobs.validate_key(idempotency_key)
    payload = request.model_dump(mode="json")
    digest = durable_jobs.request_hash(payload)
    kb_ids = list(dict.fromkeys(request.knowledge_base_ids))
    if not request.use_web and not kb_ids:
        raise HTTPException(422, "Select a knowledge base or enable web search")
    with Session(engine) as session:
        previous = durable_jobs.existing_intent(
            session, owner_id, "research", "", key, digest
        )
        if previous is not None:
            run = session.get(ResearchRun, previous.target_id)
            if run is None:
                raise HTTPException(410, "The original research run has been deleted")
            return ResearchRunPublic.model_validate(run)
        durable_jobs.enforce_capacity(session, owner_id)
        validate_knowledge_access(session, owner_id, kb_ids)
        missing = []
        if not settings.DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
        if request.use_web and not settings.TAVILY_API_KEY:
            missing.append("TAVILY_API_KEY")
        if kb_ids and not settings.ZHIPUAI_API_KEY:
            missing.append("ZHIPUAI_API_KEY")
        if missing:
            raise HTTPException(
                503, "Configure " + ", ".join(missing) + " before research"
            )
        run = ResearchRun(
            owner_id=owner_id,
            query=request.query,
            knowledge_base_ids=[str(value) for value in kb_ids],
            use_web=request.use_web,
        )
        session.add(run)
        session.flush()
        durable_jobs.enqueue(
            session,
            owner_id=owner_id,
            kind="research",
            target_id=run.id,
            payload=payload,
            key=key,
            scope="",
            digest=digest,
        )
        session.commit()
        session.refresh(run)
        return ResearchRunPublic.model_validate(run)


def _append_event(run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    with Session(engine) as session:
        durable_jobs.assert_lease(session)
        run = session.exec(
            select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
        ).first()
        if run is None or run.status in TERMINAL:
            return
        _event(session, run, event, data)
        run.updated_at = get_datetime_utc()
        session.add(run)
        session.commit()


def _persist_finish(
    run_id: uuid.UUID,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with Session(engine) as session:
        job = durable_jobs.assert_lease(session, permit_cancelled=True)
        run = session.exec(
            select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
        ).first()
        if run is None or run.status in TERMINAL:
            if run is not None and run.status == "cancelled":
                durable_jobs.finish_in_transaction(session, "cancelled")
                session.commit()
            return
        if job.cancel_requested_at:
            status, result, error = "cancelled", None, None
        run.status = status
        run.error_message = error
        run.updated_at = get_datetime_utc()
        if result:
            run.final_report = result.get("final_report", "")
            run.sub_tasks = result.get("sub_tasks", [])
            run.sources = result.get("sources", [])
        session.add(run)
        kind = (
            "complete"
            if status == "succeeded"
            else "cancelled"
            if status == "cancelled"
            else "error"
        )
        _event(
            session, run, kind, {"message": error} if kind == "error" else _payload(run)
        )
        durable_jobs.finish_in_transaction(session, status, error)
        session.commit()


def _finish(
    run_id: uuid.UUID,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    for attempt in range(3):
        try:
            _persist_finish(run_id, status, result=result, error=error)
            return
        except durable_jobs.LeaseLost:
            return
        except SQLAlchemyError:
            if attempt < 2:
                time.sleep(0.1 * (2**attempt))
    logger.error(
        "Research run %s could not persist its outcome; expired attempt will not be replayed",
        run_id,
    )


def reconcile_finishes() -> None:
    """Recovery belongs to worker lease maintenance, never API reads."""


def _execute_run(run_id: uuid.UUID, cancel: threading.Event) -> None:
    from app.research.errors import ResearchCancelled

    if durable_jobs.current_claim() is None:
        raise durable_jobs.LeaseLost(
            "Research execution requires a claimed durable job"
        )
    started = time.monotonic()
    buffers: dict[str, str] = {}
    last_flush = time.monotonic()

    def should_cancel() -> bool:
        return (
            cancel.is_set()
            or time.monotonic() - started > settings.RESEARCH_TIMEOUT_SECONDS
            or durable_jobs.should_stop(durable_jobs.current_claim())
        )

    def flush() -> None:
        nonlocal last_flush
        for kind, value in list(buffers.items()):
            if value:
                _append_event(run_id, kind, {"text": value})
        buffers.clear()
        last_flush = time.monotonic()

    def emit(kind: str, data: dict[str, Any]) -> None:
        if should_cancel():
            raise ResearchCancelled("Research stopped")
        if kind in {"analysis_token", "report_token"}:
            buffers[kind] = buffers.get(kind, "") + str(data.get("text", ""))
            if (
                sum(map(len, buffers.values())) >= 160
                or time.monotonic() - last_flush >= 0.2
            ):
                flush()
        else:
            flush()
            _append_event(run_id, kind, data)

    try:
        from app.research import execute_research

        with Session(engine) as session:
            job = durable_jobs.assert_lease(session)
            run = session.exec(
                select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
            ).first()
            if run is None or run.status in TERMINAL or cancel.is_set():
                return
            run.status = "running"
            run.updated_at = get_datetime_utc()
            session.add(run)
            session.commit()
            query, owner, kb_ids, use_web = (
                job.payload["query"],
                run.owner_id,
                job.payload["knowledge_base_ids"],
                job.payload["use_web"],
            )
        result = execute_research(
            query,
            owner,
            [uuid.UUID(value) for value in kb_ids],
            emit,
            use_web=use_web,
            should_cancel=should_cancel,
        )
        flush()
        if should_cancel():
            raise ResearchCancelled("Research stopped")
        _finish(run_id, "succeeded", result=result)
    except ResearchCancelled:
        if cancel.is_set():
            _finish(run_id, "cancelled")
        else:
            _finish(
                run_id,
                "failed",
                error="Research timed out. Narrow the question and try again.",
            )
    except Exception as exc:
        logger.error("Research run %s failed (%s)", run_id, type(exc).__name__)
        _finish(
            run_id,
            "failed",
            error=str(exc)
            if isinstance(exc, ResearchExecutionError)
            else "Research failed. Check model/search configuration or try again.",
        )


def cancel_run(
    session: Session, run_id: uuid.UUID, owner_id: uuid.UUID
) -> ResearchRunPublic:
    run = owned_run(session, run_id, owner_id)
    durable_jobs.cancel_target(session, "research", run.id)
    session.commit()
    session.refresh(run)
    return ResearchRunPublic.model_validate(run)
