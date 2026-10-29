import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.db import engine
from app.models import (
    ResearchCreate,
    ResearchEvent,
    ResearchRun,
    ResearchRunPublic,
    ResearchRunsPublic,
)
from app.services import research_runs

router = APIRouter(prefix="/research/runs", tags=["research"])


@router.post("", response_model=ResearchRunPublic, status_code=202)
def create_run(request: ResearchCreate, current_user: CurrentUser) -> ResearchRunPublic:
    return research_runs.start_run(current_user.id, request)


@router.get("", response_model=ResearchRunsPublic)
def list_runs(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> ResearchRunsPublic:
    research_runs.reconcile_finishes()
    count = session.exec(
        select(func.count())
        .select_from(ResearchRun)
        .where(ResearchRun.owner_id == current_user.id)
    ).one()
    runs = session.exec(
        select(ResearchRun)
        .where(ResearchRun.owner_id == current_user.id)
        .order_by(col(ResearchRun.created_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return ResearchRunsPublic(
        data=[ResearchRunPublic.model_validate(run) for run in runs], count=count
    )


@router.get("/{run_id}", response_model=ResearchRunPublic)
def read_run(
    run_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> ResearchRun:
    return research_runs.owned_run(session, run_id, current_user.id)


@router.post("/{run_id}/cancel", response_model=ResearchRunPublic)
def cancel_run(
    run_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> ResearchRunPublic:
    return research_runs.cancel_run(session, run_id, current_user.id)


@router.delete("/{run_id}", status_code=204)
def delete_run(
    run_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Response:
    run = research_runs.owned_run(session, run_id, current_user.id)
    session.refresh(run, with_for_update=True)
    if run.status not in research_runs.TERMINAL:
        raise HTTPException(
            status_code=409, detail="Cancel the running research before deleting it"
        )
    session.delete(run)
    session.commit()
    return Response(status_code=204)


def _poll(
    run_id: uuid.UUID, owner_id: uuid.UUID, cursor: int
) -> tuple[list[dict], str]:
    with Session(engine) as session:
        run = research_runs.owned_run(session, run_id, owner_id)
        events = session.exec(
            select(ResearchEvent)
            .where(ResearchEvent.run_id == run_id, ResearchEvent.id > cursor)
            .order_by(ResearchEvent.id)
            .limit(200)
        ).all()
        return [
            {"id": event.id, "event": event.event_type, "data": event.payload}
            for event in events
        ], run.status


@router.get("/{run_id}/events")
def stream_events(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None),
) -> StreamingResponse:
    research_runs.owned_run(session, run_id, current_user.id)
    try:
        cursor = max(after, int(last_event_id or "0"))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid Last-Event-ID") from None
    owner_id = current_user.id
    # Release the authorization query transaction before opening a long SSE stream.
    session.close()

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        heartbeat = 0
        while not await request.is_disconnected():
            try:
                batch, status = await asyncio.to_thread(_poll, run_id, owner_id, cursor)
            except HTTPException:
                return
            for item in batch:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
            if status in research_runs.TERMINAL and len(batch) < 200:
                return
            heartbeat += 1
            if heartbeat % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(settings.RESEARCH_STREAM_POLL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
