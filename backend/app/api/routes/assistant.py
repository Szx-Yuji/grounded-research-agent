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
    AssistantConversation,
    AssistantConversationCreate,
    AssistantConversationPublic,
    AssistantConversationsPublic,
    AssistantConversationUpdate,
    AssistantEvent,
    AssistantMessage,
    AssistantMessageCreate,
    AssistantMessagePublic,
    AssistantMessagesPublic,
    AssistantTurnPublic,
    get_datetime_utc,
)
from app.services import assistant_conversations as service
from app.services.knowledge_retrieval import validate_knowledge_access

router = APIRouter(prefix="/assistant/conversations", tags=["assistant"])


@router.post("", response_model=AssistantConversationPublic, status_code=201)
def create_conversation(
    body: AssistantConversationCreate, session: SessionDep, current_user: CurrentUser
):
    kb_ids = list(dict.fromkeys(body.knowledge_base_ids))
    validate_knowledge_access(session, current_user.id, kb_ids)
    conversation = AssistantConversation(
        owner_id=current_user.id,
        title=body.title.strip() or "New conversation",
        knowledge_base_ids=[str(value) for value in kb_ids],
        use_web=body.use_web,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.get("", response_model=AssistantConversationsPublic)
def list_conversations(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    service.reconcile_finishes()
    count = session.exec(
        select(func.count())
        .select_from(AssistantConversation)
        .where(AssistantConversation.owner_id == current_user.id)
    ).one()
    conversations = session.exec(
        select(AssistantConversation)
        .where(AssistantConversation.owner_id == current_user.id)
        .order_by(col(AssistantConversation.updated_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return AssistantConversationsPublic(
        data=[
            AssistantConversationPublic.model_validate(value) for value in conversations
        ],
        count=count,
    )


@router.get("/{conversation_id}", response_model=AssistantConversationPublic)
def read_conversation(
    conversation_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    return service.owned_conversation(session, conversation_id, current_user.id)


@router.patch("/{conversation_id}", response_model=AssistantConversationPublic)
def update_conversation(
    conversation_id: uuid.UUID,
    body: AssistantConversationUpdate,
    session: SessionDep,
    current_user: CurrentUser,
):
    conversation = service.owned_conversation(
        session, conversation_id, current_user.id, lock=True
    )
    if service.active_turn(session, conversation_id):
        raise HTTPException(409, "Wait for the current reply or cancel it first")
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(422, "A conversation title is required")
        conversation.title = body.title.strip()
    if body.knowledge_base_ids is not None:
        kb_ids = list(dict.fromkeys(body.knowledge_base_ids))
        validate_knowledge_access(session, current_user.id, kb_ids)
        conversation.knowledge_base_ids = [str(value) for value in kb_ids]
    if body.use_web is not None:
        conversation.use_web = body.use_web
    conversation.updated_at = get_datetime_utc()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    conversation = service.owned_conversation(
        session, conversation_id, current_user.id, lock=True
    )
    if service.active_turn(session, conversation_id):
        raise HTTPException(
            409, "Cancel the current reply before deleting the conversation"
        )
    session.delete(conversation)
    session.commit()
    return Response(status_code=204)


@router.get("/{conversation_id}/messages", response_model=AssistantMessagesPublic)
def list_messages(
    conversation_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    service.owned_conversation(session, conversation_id, current_user.id)
    messages = session.exec(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation_id)
        .order_by(AssistantMessage.created_at, AssistantMessage.id)
    ).all()
    return AssistantMessagesPublic(
        data=[AssistantMessagePublic.model_validate(message) for message in messages],
        count=len(messages),
    )


@router.post(
    "/{conversation_id}/messages", response_model=AssistantTurnPublic, status_code=202
)
def send_message(
    conversation_id: uuid.UUID, body: AssistantMessageCreate, current_user: CurrentUser
):
    return service.start_turn(current_user.id, conversation_id, body.content)


@router.get(
    "/{conversation_id}/turns/active", response_model=AssistantTurnPublic | None
)
def get_active_turn(
    conversation_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    service.reconcile_finishes()
    service.owned_conversation(session, conversation_id, current_user.id)
    return service.active_turn(session, conversation_id)


@router.get("/{conversation_id}/turns/{turn_id}", response_model=AssistantTurnPublic)
def read_turn(
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    return service.owned_turn(session, conversation_id, turn_id, current_user.id)


@router.post(
    "/{conversation_id}/turns/{turn_id}/cancel", response_model=AssistantTurnPublic
)
def cancel_turn(
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    return service.cancel_turn(session, conversation_id, turn_id, current_user.id)


def _poll(
    conversation_id: uuid.UUID, turn_id: uuid.UUID, owner_id: uuid.UUID, cursor: int
):
    with Session(engine) as session:
        turn = service.owned_turn(session, conversation_id, turn_id, owner_id)
        events = session.exec(
            select(AssistantEvent)
            .where(AssistantEvent.turn_id == turn_id, AssistantEvent.id > cursor)
            .order_by(AssistantEvent.id)
            .limit(200)
        ).all()
        return [
            {"id": event.id, "event": event.event_type, "data": event.payload}
            for event in events
        ], turn.status


@router.get("/{conversation_id}/turns/{turn_id}/events")
def stream_events(
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None),
):
    service.owned_turn(session, conversation_id, turn_id, current_user.id)
    try:
        cursor = max(after, int(last_event_id or "0"))
    except ValueError:
        raise HTTPException(422, "Invalid Last-Event-ID") from None
    owner_id = current_user.id
    session.close()

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        heartbeat = 0
        while not await request.is_disconnected():
            try:
                batch, status = await asyncio.to_thread(
                    _poll, conversation_id, turn_id, owner_id, cursor
                )
            except HTTPException:
                return
            for item in batch:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
            if status in service.TERMINAL and len(batch) < 200:
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
