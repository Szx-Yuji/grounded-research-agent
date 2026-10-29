"""Authorized evidence retrieval shared by HTTP and research tools.

Identity is supplied by the authenticated application, never by model output.
Only ready, still-owned documents may contribute evidence.
"""

import hashlib
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.db import engine
from app.models import KnowledgeBase, KnowledgeDocument
from app.services.rag_service import create_retriever


class Evidence(BaseModel):
    source_id: str
    kind: Literal["knowledge", "web"]
    content: str
    title: str
    url: str | None = None
    document_id: str | None = None
    chunk_index: int | None = None


def validate_knowledge_access(
    session: Session, user_id: uuid.UUID, knowledge_base_ids: list[uuid.UUID]
) -> None:
    for knowledge_base_id in set(knowledge_base_ids):
        found = session.exec(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.owner_id == user_id,
            )
        ).first()
        if found is None:
            raise HTTPException(status_code=404, detail="Knowledge base not found")


def retrieve_evidence(
    user_id: uuid.UUID,
    knowledge_base_ids: list[uuid.UUID],
    query: str,
    k: int = 4,
) -> list[Evidence]:
    if not query.strip() or not 1 <= k <= 20 or len(knowledge_base_ids) > 5:
        raise ValueError("Invalid knowledge retrieval request")
    evidence: dict[str, Evidence] = {}
    with Session(engine) as session:
        validate_knowledge_access(session, user_id, knowledge_base_ids)
        for knowledge_base_id in dict.fromkeys(knowledge_base_ids):
            documents = create_retriever(knowledge_base_id, k=k).invoke(query)
            for chunk in documents:
                try:
                    document_id = uuid.UUID(str(chunk.metadata.get("document_id", "")))
                except ValueError:
                    continue
                document = session.exec(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.id == document_id,
                        KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                        KnowledgeDocument.status == "ready",
                    )
                ).first()
                if document is None:
                    continue
                index = chunk.metadata.get("chunk_index")
                chunk_index = (
                    index
                    if isinstance(index, int) and not isinstance(index, bool)
                    else None
                )
                suffix = (
                    str(chunk_index)
                    if chunk_index is not None
                    else hashlib.sha256(chunk.page_content.encode()).hexdigest()[:16]
                )
                source_id = f"kb:{document.id}:{suffix}"
                evidence[source_id] = Evidence(
                    source_id=source_id,
                    kind="knowledge",
                    content=chunk.page_content[:12000],
                    title=document.original_filename,
                    document_id=str(document.id),
                    chunk_index=chunk_index,
                )
    return list(evidence.values())
