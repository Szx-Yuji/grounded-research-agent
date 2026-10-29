import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import EmailStr, field_validator
from sqlalchemy import JSON, DateTime, Index, Text, UniqueConstraint, text
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)
    knowledge_bases: list[KnowledgeBase] = Relationship(
        back_populates="owner",
        cascade_delete=True,
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


class KnowledgeBaseBase(SQLModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)


class KnowledgeBaseCreate(KnowledgeBaseBase):
    pass


class KnowledgeBaseUpdate(SQLModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
    )


class KnowledgeBase(KnowledgeBaseBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    owner_id: uuid.UUID = Field(
        foreign_key="user.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    owner: User | None = Relationship(back_populates="knowledge_bases")

    documents: list[KnowledgeDocument] = Relationship(
        back_populates="knowledge_base",
        cascade_delete=True,
    )

    conversations: list[Conversation] = Relationship(
        back_populates="knowledge_base",
        cascade_delete=True,
    )


class KnowledgeBasePublic(KnowledgeBaseBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime


class KnowledgeBasesPublic(SQLModel):
    data: list[KnowledgeBasePublic]
    count: int


class ConversationBase(SQLModel):
    title: str = Field(
        default="New conversation",
        min_length=1,
        max_length=200,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()

        if not title:
            raise ValueError("Conversation title must not be blank")

        return title


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(SQLModel):
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()

        if not title:
            raise ValueError("Conversation title must not be blank")

        return title


class Conversation(ConversationBase, table=True):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )

    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    knowledge_base: KnowledgeBase | None = Relationship(
        back_populates="conversations",
    )

    messages: list[ChatMessage] = Relationship(
        back_populates="conversation",
        cascade_delete=True,
    )


class ConversationPublic(ConversationBase):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ConversationsPublic(SQLModel):
    data: list[ConversationPublic]
    count: int


class ChatMessage(SQLModel, table=True):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )

    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    role: str = Field(max_length=16)

    content: str = Field(
        sa_type=Text,
    )

    sources: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_type=JSON,
    )

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    conversation: Conversation | None = Relationship(
        back_populates="messages",
    )


class ChatMessagePublic(SQLModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict[str, Any]]
    created_at: datetime


class ChatMessagesPublic(SQLModel):
    data: list[ChatMessagePublic]
    count: int


class KnowledgeDocumentBase(SQLModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=100)
    size_bytes: int = Field(default=0, ge=0)


class KnowledgeDocument(KnowledgeDocumentBase, table=True):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )

    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    storage_key: str = Field(max_length=500)
    content_hash: str | None = Field(
        default=None,
        max_length=64,
        index=True,
    )
    status: str = Field(default="uploaded", max_length=32)
    chunk_count: int = Field(default=0, ge=0)
    error_message: str | None = Field(default=None, max_length=2000)

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    knowledge_base: KnowledgeBase | None = Relationship(
        back_populates="documents",
    )


class KnowledgeDocumentPublic(KnowledgeDocumentBase):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    status: str
    chunk_count: int
    error_message: str | None
    created_at: datetime


class KnowledgeDocumentsPublic(SQLModel):
    data: list[KnowledgeDocumentPublic]
    count: int


class KnowledgeSearchResult(SQLModel):
    page_content: str
    source_metadata: dict[
        str,
        str | int | float | bool | None,
    ]


class KnowledgeSearchResults(SQLModel):
    data: list[KnowledgeSearchResult]
    count: int


class KnowledgeAskRequest(SQLModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
    )
    k: int = Field(default=4, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def normalize_question(
        cls,
        value: str,
    ) -> str:
        question = value.strip()

        if not question:
            raise ValueError("Question must not be blank")

        return question


class KnowledgeAnswerSource(KnowledgeSearchResult):
    citation: int = Field(ge=1)


class KnowledgeAskResponse(SQLModel):
    answer: str
    sources: list[KnowledgeAnswerSource]


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


ResearchStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class ResearchRun(SQLModel, table=True):
    """Research uses the same identity and database as knowledge bases."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    query: str = Field(max_length=4000)
    knowledge_base_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    use_web: bool = True
    status: str = Field(default="queued", index=True, max_length=16)
    final_report: str = Field(default="", sa_type=Text)
    error_message: str | None = Field(default=None, max_length=1000)
    sub_tasks: list[str] = Field(default_factory=list, sa_type=JSON)
    sources: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class ResearchEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: uuid.UUID = Field(
        foreign_key="researchrun.id", index=True, ondelete="CASCADE"
    )
    event_type: str = Field(max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class ResearchCreate(SQLModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    use_web: bool = True

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A research question is required")
        return value.strip()


class ResearchRunPublic(SQLModel):
    id: uuid.UUID
    query: str
    knowledge_base_ids: list[str]
    use_web: bool
    status: ResearchStatus
    final_report: str
    error_message: str | None
    sub_tasks: list[str]
    sources: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class ResearchRunsPublic(SQLModel):
    data: list[ResearchRunPublic]
    count: int


class AssistantConversation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    title: str = Field(default="New conversation", max_length=200)
    knowledge_base_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    use_web: bool = False
    summary: str = Field(default="", sa_type=Text)
    summarized_count: int = Field(default=0)
    summary_sources: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class AssistantTurn(SQLModel, table=True):
    __table_args__ = (
        Index(
            "ix_assistantturn_one_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="assistantconversation.id", index=True, ondelete="CASCADE"
    )
    status: str = Field(default="queued", max_length=16, index=True)
    answer: str = Field(default="", sa_type=Text)
    error_message: str | None = Field(default=None, max_length=1000)
    sources: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    delegate_notes: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class AssistantMessage(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("turn_id", "role", name="uq_assistantmessage_turn_role"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="assistantconversation.id", index=True, ondelete="CASCADE"
    )
    turn_id: uuid.UUID = Field(
        foreign_key="assistantturn.id", index=True, ondelete="CASCADE"
    )
    role: str = Field(max_length=16)
    content: str = Field(sa_type=Text)
    sources: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class AssistantEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    turn_id: uuid.UUID = Field(
        foreign_key="assistantturn.id", index=True, ondelete="CASCADE"
    )
    event_type: str = Field(max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class AssistantConversationCreate(SQLModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    use_web: bool = False


class AssistantConversationUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_base_ids: list[uuid.UUID] | None = Field(default=None, max_length=5)
    use_web: bool | None = None


class AssistantConversationPublic(SQLModel):
    id: uuid.UUID
    title: str
    knowledge_base_ids: list[str]
    use_web: bool
    summary: str
    created_at: datetime
    updated_at: datetime


class AssistantConversationsPublic(SQLModel):
    data: list[AssistantConversationPublic]
    count: int


class AssistantMessageCreate(SQLModel):
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A message is required")
        return value.strip()


class AssistantMessagePublic(SQLModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    turn_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict[str, Any]]
    created_at: datetime


class AssistantMessagesPublic(SQLModel):
    data: list[AssistantMessagePublic]
    count: int


class AssistantTurnPublic(SQLModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: ResearchStatus
    answer: str
    error_message: str | None
    sources: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
