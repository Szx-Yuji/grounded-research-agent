"""Persist runs/events around a bounded, single-process research worker pool.

This first integration deliberately does not promise durable graph execution.
A restart marks unfinished runs failed. Completed reports and SSE events survive.
"""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from app.services.knowledge_retrieval import validate_knowledge_access

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed", "cancelled"}
RESTART_MESSAGE = "Research was interrupted by a server restart. Start a new run."
_lock = threading.RLock()
_active: dict[uuid.UUID, threading.Event] = {}
_executor: ThreadPoolExecutor | None = None
_pending_finishes: dict[uuid.UUID, tuple[str, dict[str, Any] | None, str | None]] = {}


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
    """Only valid with the documented one-ASGI-worker deployment."""
    with Session(engine) as session:
        interrupted = session.exec(
            select(ResearchRun).where(ResearchRun.status.in_(["queued", "running"]))
        ).all()
        for run in interrupted:
            run.status = "failed"
            run.error_message = RESTART_MESSAGE
            run.updated_at = get_datetime_utc()
            session.add(run)
            _event(session, run, "error", {"message": RESTART_MESSAGE})
        session.commit()


def shutdown_research() -> None:
    global _executor
    with _lock:
        for signal in _active.values():
            signal.set()
        pool, _executor = _executor, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def start_run(owner_id: uuid.UUID, request: ResearchCreate) -> ResearchRunPublic:
    global _executor
    reconcile_finishes()
    kb_ids = list(dict.fromkeys(request.knowledge_base_ids))
    if not request.use_web and not kb_ids:
        raise HTTPException(
            status_code=422, detail="Select a knowledge base or enable web search"
        )
    with Session(engine) as session:
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
            status_code=503,
            detail=f"Configure {', '.join(missing)} in .env before starting research",
        )
    with _lock:
        if len(_active) + len(_pending_finishes) >= settings.RESEARCH_MAX_WORKERS:
            raise HTTPException(
                status_code=429, detail="Research workers are busy. Try again shortly."
            )
        with Session(engine) as session:
            run = ResearchRun(
                owner_id=owner_id,
                query=request.query,
                knowledge_base_ids=[str(value) for value in kb_ids],
                use_web=request.use_web,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            result = ResearchRunPublic.model_validate(run)
        cancel = threading.Event()
        _active[run.id] = cancel
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=settings.RESEARCH_MAX_WORKERS, thread_name_prefix="research"
            )
        try:
            _executor.submit(_execute_run, run.id, cancel)
        except Exception:
            _active.pop(run.id, None)
            _finish(
                run.id,
                "failed",
                error="The research worker could not start. Try again.",
            )
            raise HTTPException(
                status_code=503, detail="Research worker unavailable"
            ) from None
    return result


def _append_event(run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    with Session(engine) as session:
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
        run = session.exec(
            select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
        ).first()
        if run is None or run.status in TERMINAL:
            return
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
        session.commit()


def _finish(
    run_id: uuid.UUID,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    # Retry only persistence, never paid model execution. Keep the outcome in
    # memory for the next API/stream poll if the database is temporarily down.
    for attempt in range(3):
        try:
            _persist_finish(run_id, status, result=result, error=error)
            with _lock:
                _pending_finishes.pop(run_id, None)
            return
        except SQLAlchemyError:
            if attempt < 2:
                time.sleep(0.1 * (2**attempt))
    logger.error("Research run %s is waiting for terminal-state persistence", run_id)
    with _lock:
        _pending_finishes[run_id] = (status, result, error)


def reconcile_finishes() -> None:
    with _lock:
        pending = list(_pending_finishes.items())
    for run_id, (status, result, error) in pending:
        try:
            _persist_finish(run_id, status, result=result, error=error)
        except SQLAlchemyError:
            continue
        with _lock:
            _pending_finishes.pop(run_id, None)


def _execute_run(run_id: uuid.UUID, cancel: threading.Event) -> None:
    from app.research.errors import ResearchCancelled

    started = time.monotonic()
    buffers: dict[str, str] = {}
    last_flush = time.monotonic()

    def should_cancel() -> bool:
        return (
            cancel.is_set()
            or time.monotonic() - started > settings.RESEARCH_TIMEOUT_SECONDS
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
                run.query,
                run.owner_id,
                run.knowledge_base_ids,
                run.use_web,
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
    except Exception:
        logger.exception("Research run %s failed", run_id)
        _finish(
            run_id,
            "failed",
            error="Research failed. Check model/search configuration or try again.",
        )
    finally:
        with _lock:
            _active.pop(run_id, None)


def cancel_run(
    session: Session, run_id: uuid.UUID, owner_id: uuid.UUID
) -> ResearchRunPublic:
    run = owned_run(session, run_id, owner_id)
    # The row lock serializes cancellation with worker completion and event writes.
    session.refresh(run, with_for_update=True)
    if run.status not in TERMINAL:
        run.status = "cancelled"
        run.updated_at = get_datetime_utc()
        session.add(run)
        _event(session, run, "cancelled", _payload(run))
        session.commit()
        session.refresh(run)
        with _lock:
            signal = _active.get(run_id)
            if signal:
                signal.set()
    return ResearchRunPublic.model_validate(run)
