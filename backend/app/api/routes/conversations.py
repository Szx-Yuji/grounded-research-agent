import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    ChatMessage,
    ChatMessagePublic,
    ChatMessagesPublic,
    Conversation,
    ConversationCreate,
    ConversationPublic,
    ConversationsPublic,
    ConversationUpdate,
    KnowledgeAnswerSource,
    KnowledgeAskRequest,
    KnowledgeAskResponse,
    KnowledgeBase,
    Message,
    get_datetime_utc,
)
from app.services.rag_graph import RAGState, rag_graph

logger = logging.getLogger(__name__)


def format_sse(
    event: str,
    data: dict[str, object],
) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
    )

    return f"event: {event}\ndata: {payload}\n\n"


router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/conversations",
    tags=["conversations"],
)


def get_owned_knowledge_base(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> KnowledgeBase:
    statement = select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == current_user.id,
    )

    knowledge_base = session.exec(statement).first()

    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail="Knowledge base not found",
        )

    return knowledge_base


def get_owned_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Conversation:
    statement = (
        select(Conversation)
        .join(KnowledgeBase)
        .where(
            Conversation.id == conversation_id,
            Conversation.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.owner_id == current_user.id,
        )
    )

    conversation = session.exec(statement).first()

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation


@router.post(
    "/",
    response_model=ConversationPublic,
    status_code=201,
)
def create_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_in: ConversationCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> Conversation:
    knowledge_base = get_owned_knowledge_base(
        knowledge_base_id,
        session,
        current_user,
    )

    conversation = Conversation.model_validate(
        conversation_in,
        update={
            "knowledge_base_id": knowledge_base.id,
        },
    )

    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    return conversation


@router.get(
    "/",
    response_model=ConversationsPublic,
)
def read_conversations(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> ConversationsPublic:
    knowledge_base = get_owned_knowledge_base(
        knowledge_base_id,
        session,
        current_user,
    )

    count_statement = (
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.knowledge_base_id == knowledge_base.id)
    )
    count = session.exec(count_statement).one()

    statement = (
        select(Conversation)
        .where(Conversation.knowledge_base_id == knowledge_base.id)
        .order_by(col(Conversation.updated_at).desc())
        .offset(skip)
        .limit(limit)
    )

    conversations = session.exec(statement).all()

    data = [
        ConversationPublic.model_validate(conversation)
        for conversation in conversations
    ]

    return ConversationsPublic(
        data=data,
        count=count,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationPublic,
)
def read_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Conversation:
    return get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=ChatMessagesPublic,
)
def read_conversation_messages(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> ChatMessagesPublic:
    conversation = get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )

    count_statement = (
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
    )
    count = session.exec(count_statement).one()

    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(col(ChatMessage.created_at).asc())
        .offset(skip)
        .limit(limit)
    )

    messages = session.exec(statement).all()

    data = [ChatMessagePublic.model_validate(message) for message in messages]

    return ChatMessagesPublic(
        data=data,
        count=count,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=KnowledgeAskResponse,
)
def ask_in_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question_in: KnowledgeAskRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> KnowledgeAskResponse:
    conversation = get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )

    history_statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(col(ChatMessage.created_at).desc())
        .limit(10)
    )

    history_records = session.exec(history_statement).all()

    chat_history = [
        HumanMessage(content=message.content)
        if message.role == "user"
        else AIMessage(content=message.content)
        for message in reversed(history_records)
    ]

    result = rag_graph.invoke(
        RAGState(
            knowledge_base_id=knowledge_base_id,
            question=question_in.question,
            k=question_in.k,
            chat_history=chat_history,
        )
    )

    state = RAGState.model_validate(result)

    if not state.answer:
        raise HTTPException(
            status_code=502,
            detail="The language model returned no answer",
        )

    sources = [
        KnowledgeAnswerSource(
            citation=index,
            page_content=document.page_content,
            source_metadata=document.metadata,
        )
        for index, document in enumerate(
            state.documents,
            start=1,
        )
    ]

    user_message = ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=question_in.question,
        sources=[],
    )

    assistant_message = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=state.answer,
        sources=[source.model_dump(mode="json") for source in sources],
    )

    conversation.updated_at = get_datetime_utc()

    try:
        session.add(user_message)
        session.add(assistant_message)
        session.add(conversation)
        session.commit()

    except Exception:
        session.rollback()
        raise

    return KnowledgeAskResponse(
        answer=state.answer,
        sources=sources,
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationPublic,
)
def update_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    conversation_in: ConversationUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> Conversation:
    conversation = get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )

    conversation.title = conversation_in.title
    conversation.updated_at = get_datetime_utc()

    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    return conversation


@router.delete(
    "/{conversation_id}",
    response_model=Message,
)
def delete_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    conversation = get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )

    session.delete(conversation)
    session.commit()

    return Message(
        message="Conversation deleted successfully",
    )


@router.post(
    "/{conversation_id}/messages/stream",
    response_class=StreamingResponse,
)
async def stream_answer_in_conversation(
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question_in: KnowledgeAskRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> StreamingResponse:
    conversation = get_owned_conversation(
        knowledge_base_id,
        conversation_id,
        session,
        current_user,
    )

    history_statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(col(ChatMessage.created_at).desc())
        .limit(10)
    )

    history_records = session.exec(history_statement).all()

    chat_history = [
        HumanMessage(content=message.content)
        if message.role == "user"
        else AIMessage(content=message.content)
        for message in reversed(history_records)
    ]

    initial_state = RAGState(
        knowledge_base_id=knowledge_base_id,
        question=question_in.question,
        k=question_in.k,
        chat_history=chat_history,
    )

    async def event_stream() -> AsyncIterator[str]:
        final_state = initial_state
        token_was_sent = False

        try:
            async for stream_mode, chunk in rag_graph.astream(
                initial_state,
                stream_mode=["messages", "values"],
            ):
                if stream_mode == "messages":
                    message_chunk, metadata = cast(
                        tuple[Any, dict[str, Any]],
                        chunk,
                    )

                    if metadata.get("langgraph_node") != "generate":
                        continue

                    content = message_chunk.content

                    if isinstance(content, str) and content:
                        token_was_sent = True

                        yield format_sse(
                            "token",
                            {"content": content},
                        )

                elif stream_mode == "values":
                    final_state = RAGState.model_validate(chunk)

            if not final_state.answer:
                raise RuntimeError("The language model returned no answer")

            # No-document answers do not call the model,
            # so they do not produce message chunks.
            if not token_was_sent:
                yield format_sse(
                    "token",
                    {"content": final_state.answer},
                )

            sources = [
                KnowledgeAnswerSource(
                    citation=index,
                    page_content=document.page_content,
                    source_metadata=document.metadata,
                )
                for index, document in enumerate(
                    final_state.documents,
                    start=1,
                )
            ]

            source_payload = [source.model_dump(mode="json") for source in sources]

            user_message = ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content=question_in.question,
                sources=[],
            )

            assistant_message = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=final_state.answer,
                sources=source_payload,
            )

            conversation.updated_at = get_datetime_utc()

            session.add(user_message)
            session.add(assistant_message)
            session.add(conversation)
            session.commit()

            yield format_sse(
                "sources",
                {"sources": source_payload},
            )

            yield format_sse(
                "done",
                {
                    "user_message_id": str(user_message.id),
                    "assistant_message_id": str(assistant_message.id),
                },
            )

        except Exception:
            session.rollback()

            logger.exception(
                "Failed to stream conversation answer: %s",
                conversation.id,
            )

            yield format_sse(
                "error",
                {"detail": ("Failed to generate the answer")},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
