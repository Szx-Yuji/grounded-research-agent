import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import (
    AssistantConversation,
    KnowledgeAnswerSource,
    KnowledgeAskRequest,
    KnowledgeAskResponse,
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBasePublic,
    KnowledgeBasesPublic,
    KnowledgeBaseUpdate,
    KnowledgeDocument,
    KnowledgeDocumentPublic,
    KnowledgeDocumentsPublic,
    KnowledgeSearchResult,
    KnowledgeSearchResults,
    Message,
    get_datetime_utc,
)
from app.services.document_processor import ingest_document
from app.services.document_storage import (
    delete_document_file,
    save_document_file,
)
from app.services.knowledge_retrieval import retrieve_evidence
from app.services.rag_graph import RAGState, rag_graph
from app.services.rag_service import (
    delete_document_vectors,
    delete_knowledge_base_vectors,
)

router = APIRouter(
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)

logger = logging.getLogger(__name__)


@router.post("/", response_model=KnowledgeBasePublic, status_code=201)
def create_knowledge_base(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    knowledge_base_in: KnowledgeBaseCreate,
) -> KnowledgeBase:
    name = knowledge_base_in.name.strip()

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Knowledge base name must not be blank",
        )

    knowledge_base = KnowledgeBase.model_validate(
        knowledge_base_in,
        update={
            "name": name,
            "owner_id": current_user.id,
        },
    )

    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)

    return knowledge_base


@router.get("/", response_model=KnowledgeBasesPublic)
def read_knowledge_bases(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> KnowledgeBasesPublic:
    count_statement = (
        select(func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.owner_id == current_user.id)
    )
    count = session.exec(count_statement).one()

    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_id == current_user.id)
        .order_by(col(KnowledgeBase.created_at).desc())
        .offset(skip)
        .limit(limit)
    )

    knowledge_bases = session.exec(statement).all()

    data = [KnowledgeBasePublic.model_validate(item) for item in knowledge_bases]

    return KnowledgeBasesPublic(data=data, count=count)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBasePublic)
def read_knowledge_base(
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


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBasePublic)
def update_knowledge_base(
    knowledge_base_id: uuid.UUID,
    knowledge_base_in: KnowledgeBaseUpdate,
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

    update_data = knowledge_base_in.model_dump(exclude_unset=True)

    if "name" in update_data:
        name = update_data["name"]

        if name is None or not name.strip():
            raise HTTPException(
                status_code=422,
                detail="Knowledge base name must not be blank",
            )

        update_data["name"] = name.strip()

    knowledge_base.sqlmodel_update(update_data)

    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)

    return knowledge_base


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentPublic,
    status_code=201,
)
async def upload_knowledge_document(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> KnowledgeDocument:
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

    document_id = uuid.uuid4()
    content_type = file.content_type

    (
        storage_key,
        size_bytes,
        original_filename,
        content_hash,
    ) = await save_document_file(
        file,
        owner_id=current_user.id,
        knowledge_base_id=knowledge_base.id,
        document_id=document_id,
    )

    duplicate_statement = select(KnowledgeDocument).where(
        KnowledgeDocument.knowledge_base_id == knowledge_base.id,
        KnowledgeDocument.content_hash == content_hash,
    )

    duplicate_document = session.exec(duplicate_statement).first()

    if duplicate_document is not None:
        delete_document_file(storage_key)

        raise HTTPException(
            status_code=409,
            detail="This document already exists in the knowledge base",
        )

    document = KnowledgeDocument(
        id=document_id,
        knowledge_base_id=knowledge_base.id,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        content_hash=content_hash,
        status="uploaded",
    )

    try:
        session.add(document)
        session.commit()
        session.refresh(document)

    except Exception:
        session.rollback()
        delete_document_file(storage_key)
        raise

    background_tasks.add_task(
        ingest_document,
        document.id,
    )

    return document


@router.get(
    "/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentsPublic,
)
def read_knowledge_documents(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> KnowledgeDocumentsPublic:
    knowledge_base_statement = select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == current_user.id,
    )

    knowledge_base = session.exec(knowledge_base_statement).first()

    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail="Knowledge base not found",
        )

    count_statement = (
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(KnowledgeDocument.knowledge_base_id == knowledge_base.id)
    )

    count = session.exec(count_statement).one()

    statement = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.knowledge_base_id == knowledge_base.id)
        .order_by(col(KnowledgeDocument.created_at).desc())
        .offset(skip)
        .limit(limit)
    )

    documents = session.exec(statement).all()

    data = [KnowledgeDocumentPublic.model_validate(document) for document in documents]

    return KnowledgeDocumentsPublic(
        data=data,
        count=count,
    )


@router.get(
    "/{knowledge_base_id}/documents/{document_id}/download",
    response_class=FileResponse,
)
def download_knowledge_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> FileResponse:
    statement = (
        select(KnowledgeDocument)
        .join(KnowledgeBase)
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.owner_id == current_user.id,
        )
    )

    document = session.exec(statement).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    storage_root = settings.DOCUMENT_STORAGE_DIR.resolve()
    target_path = (storage_root / document.storage_key).resolve()

    if not target_path.is_relative_to(storage_root) or not target_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Document file not found",
        )

    return FileResponse(
        path=target_path,
        media_type=(document.content_type or "application/octet-stream"),
        filename=document.original_filename,
    )


@router.delete("/{knowledge_base_id}", response_model=Message)
def delete_knowledge_base(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
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

    storage_keys_statement = select(KnowledgeDocument.storage_key).where(
        KnowledgeDocument.knowledge_base_id == knowledge_base.id
    )

    storage_keys = session.exec(storage_keys_statement).all()

    try:
        delete_knowledge_base_vectors(knowledge_base.id)

        # A removed source must not make its owner's ongoing chat unusable.
        # Keep historical messages; their evidence is revalidated before reuse.
        conversations = session.exec(
            select(AssistantConversation).where(
                AssistantConversation.owner_id == current_user.id
            )
        ).all()
        for conversation in conversations:
            if str(knowledge_base.id) in conversation.knowledge_base_ids:
                conversation.knowledge_base_ids = [
                    value
                    for value in conversation.knowledge_base_ids
                    if value != str(knowledge_base.id)
                ]
                conversation.updated_at = get_datetime_utc()
                session.add(conversation)
        session.delete(knowledge_base)
        session.commit()

    except Exception as exc:
        session.rollback()

        logger.exception(
            "Failed to delete knowledge base: %s",
            knowledge_base_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete knowledge base",
        ) from exc

    for storage_key in storage_keys:
        try:
            delete_document_file(storage_key)
        except OSError, ValueError:
            logger.exception(
                "Failed to delete stored document: %s",
                storage_key,
            )

    return Message(
        message="Knowledge base deleted successfully",
    )


@router.get(
    "/{knowledge_base_id}/search",
    response_model=KnowledgeSearchResults,
)
def search_knowledge_base(
    knowledge_base_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    query: str = Query(min_length=1, max_length=2000),
    k: int = Query(default=4, ge=1, le=20),
) -> KnowledgeSearchResults:
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

    evidence = retrieve_evidence(current_user.id, [knowledge_base_id], query, k)
    return KnowledgeSearchResults(
        data=[
            KnowledgeSearchResult(
                page_content=item.content,
                source_metadata={
                    "source": item.title,
                    "document_id": item.document_id,
                    "chunk_index": item.chunk_index,
                    "knowledge_base_id": str(knowledge_base_id),
                },
            )
            for item in evidence
        ],
        count=len(evidence),
    )


@router.post(
    "/{knowledge_base_id}/ask",
    response_model=KnowledgeAskResponse,
)
def ask_knowledge_base(
    knowledge_base_id: uuid.UUID,
    question_in: KnowledgeAskRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> KnowledgeAskResponse:
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

    result = rag_graph.invoke(
        RAGState(
            knowledge_base_id=knowledge_base.id,
            question=question_in.question,
            k=question_in.k,
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

    return KnowledgeAskResponse(
        answer=state.answer,
        sources=sources,
    )


@router.delete(
    "/{knowledge_base_id}/documents/{document_id}",
    response_model=Message,
)
def delete_knowledge_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    statement = (
        select(KnowledgeDocument)
        .join(KnowledgeBase)
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.owner_id == current_user.id,
        )
    )

    document = session.exec(statement).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    storage_key = document.storage_key

    try:
        delete_document_vectors(
            knowledge_base_id,
            document.id,
        )

        session.delete(document)
        session.commit()

    except Exception as exc:
        session.rollback()

        logger.exception(
            "Failed to delete document: %s",
            document_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete document",
        ) from exc

    try:
        delete_document_file(storage_key)

    except OSError, ValueError:
        logger.exception(
            "Failed to delete stored document file: %s",
            storage_key,
        )

    return Message(
        message="Document deleted successfully",
    )


@router.post(
    "/{knowledge_base_id}/documents/{document_id}/retry",
    response_model=KnowledgeDocumentPublic,
)
def retry_knowledge_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> KnowledgeDocument:
    statement = (
        select(KnowledgeDocument)
        .join(KnowledgeBase)
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.owner_id == current_user.id,
        )
    )

    document = session.exec(statement).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    if document.status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only failed documents can be retried",
        )

    document.status = "uploaded"
    document.chunk_count = 0
    document.error_message = None

    session.add(document)
    session.commit()
    session.refresh(document)

    background_tasks.add_task(
        ingest_document,
        document.id,
    )

    return document
