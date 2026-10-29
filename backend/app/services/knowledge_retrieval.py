"""Permission-first lexical/dense retrieval with bounded, explainable RRF.

PostgreSQL is authoritative for new index generations. Legacy Chroma documents
remain readable until explicitly reindexed; no migration calls an embedding API.
"""

import hashlib
import logging
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.db import engine
from app.models import KnowledgeBase, KnowledgeDocument
from app.research.errors import ResearchCancelled
from app.retrieval_models import KnowledgeChunk
from app.runtime.context import BudgetExceeded
from app.services.rag_service import create_retriever
from app.services.retrieval_ranking import lexical_rank, reciprocal_rank_fusion

logger = logging.getLogger(__name__)
MAX_AUTHORIZED_CHUNKS = 10000
MAX_CANDIDATES = 80


class RetrievalMetadata(BaseModel):
    method: Literal["dense", "lexical", "hybrid", "dense_legacy"]
    rank: int
    fusion_score: float | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    index_version: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    source_id: str
    kind: Literal["knowledge", "web"]
    content: str
    title: str
    url: str | None = None
    document_id: str | None = None
    chunk_index: int | None = None
    retrieval: RetrievalMetadata | None = None


class RetrievalCapacityError(ValueError):
    pass


class RetrievalUnavailableError(RuntimeError):
    pass


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
    *,
    method: Literal["hybrid", "lexical", "dense"] = "hybrid",
) -> list[Evidence]:
    if (
        not query.strip()
        or len(query) > 8000
        or not 1 <= k <= 20
        or len(knowledge_base_ids) > 5
        or method not in {"hybrid", "lexical", "dense"}
    ):
        raise ValueError("Invalid knowledge retrieval request")
    scope = list(dict.fromkeys(knowledge_base_ids))
    if not scope:
        return []
    with Session(engine) as session:
        # This precedes lexical reads and every embedding/provider request.
        validate_knowledge_access(session, user_id, scope)
        rows = session.exec(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .join(
                KnowledgeBase, KnowledgeDocument.knowledge_base_id == KnowledgeBase.id
            )
            .where(
                KnowledgeChunk.knowledge_base_id.in_(scope),
                KnowledgeChunk.knowledge_base_id == KnowledgeDocument.knowledge_base_id,
                KnowledgeBase.owner_id == user_id,
                KnowledgeDocument.status == "ready",
            )
            .order_by(KnowledgeChunk.id)
            .limit(MAX_AUTHORIZED_CHUNKS + 1)
        ).all()
        if len(rows) > MAX_AUTHORIZED_CHUNKS:
            raise RetrievalCapacityError(
                "Selected knowledge bases exceed the 10000-chunk lexical limit; narrow the scope"
            )
        chunks = {chunk.id: (chunk, document) for chunk, document in rows}
        by_document_ordinal = {
            (chunk.document_id, chunk.chunk_index): chunk for chunk, _ in rows
        }
        indexed_documents = {chunk.document_id for chunk, _ in rows}
        candidates: dict[str, Evidence] = {}
        for chunk, document in rows:
            candidates[chunk.id] = Evidence(
                source_id=f"kb:{document.id}:{chunk.chunk_index}:{chunk.content_hash[:12]}",
                kind="knowledge",
                content=chunk.content[:12000],
                title=document.original_filename,
                document_id=str(document.id),
                chunk_index=chunk.chunk_index,
            )
        lexical = (
            lexical_rank(
                query,
                [
                    {
                        "id": chunk.id,
                        "term_frequencies": chunk.term_frequencies,
                        "token_count": chunk.token_count,
                    }
                    for chunk, _ in rows
                ],
                limit=MAX_CANDIDATES,
            )
            if method != "dense"
            else []
        )
        lexical_scores = dict(lexical)
        lexical_positions = {
            identity: rank for rank, (identity, _) in enumerate(lexical, 1)
        }
        dense_rankings: list[list[str]] = []
        dense_positions: dict[str, int] = {}
        warnings: list[str] = []
        legacy_ids: set[str] = set()
        if method != "lexical":
            for knowledge_base_id in scope:
                try:
                    retrieved = create_retriever(
                        knowledge_base_id, k=min(MAX_CANDIDATES, max(k * 4, 16))
                    ).invoke(query)
                except ResearchCancelled, BudgetExceeded, HTTPException:
                    raise
                except Exception as error:
                    if method == "dense":
                        raise RetrievalUnavailableError(
                            "Dense retrieval is unavailable"
                        ) from error
                    warnings.append(
                        "dense_unavailable: using lexical evidence only where dense search failed"
                    )
                    logger.warning(
                        "Dense retrieval unavailable for one authorized collection; lexical fallback is explicit"
                    )
                    continue
                parsed = []
                for item in retrieved:
                    try:
                        document_id = uuid.UUID(
                            str(item.metadata.get("document_id", ""))
                        )
                    except ValueError, TypeError, AttributeError:
                        continue
                    parsed.append((item, document_id))
                # Batch provenance checks replace the old query-per-candidate path.
                ids = {document_id for _, document_id in parsed}
                ready = (
                    {
                        document.id: document
                        for document in session.exec(
                            select(KnowledgeDocument).where(
                                KnowledgeDocument.id.in_(ids),
                                KnowledgeDocument.knowledge_base_id
                                == knowledge_base_id,
                                KnowledgeDocument.status == "ready",
                            )
                        ).all()
                    }
                    if ids
                    else {}
                )
                ranking = []
                for item, document_id in parsed:
                    document = ready.get(document_id)
                    if document is None:
                        continue
                    raw_index = item.metadata.get("chunk_index")
                    index = (
                        raw_index
                        if isinstance(raw_index, int)
                        and not isinstance(raw_index, bool)
                        else None
                    )
                    active_chunk = by_document_ordinal.get((document_id, index))
                    if document_id in indexed_documents:
                        # An old external generation cannot override a published PG generation.
                        if (
                            active_chunk is None
                            or item.metadata.get("index_version")
                            != active_chunk.index_version
                            or item.page_content != active_chunk.content
                        ):
                            continue
                        identity = active_chunk.id
                    else:
                        # A versioned vector without its PG publication is incomplete, never legacy.
                        if item.metadata.get("index_version"):
                            continue
                        suffix = (
                            str(index)
                            if index is not None
                            else hashlib.sha256(item.page_content.encode()).hexdigest()[
                                :16
                            ]
                        )
                        identity = f"legacy:{document_id}:{suffix}"
                        legacy_ids.add(identity)
                        candidates[identity] = Evidence(
                            source_id=f"kb:{document_id}:{suffix}",
                            kind="knowledge",
                            content=item.page_content[:12000],
                            title=document.original_filename,
                            document_id=str(document_id),
                            chunk_index=index,
                        )
                    if identity not in ranking:
                        ranking.append(identity)
                        dense_positions[identity] = min(
                            dense_positions.get(identity, MAX_CANDIDATES + 1),
                            len(ranking),
                        )
                dense_rankings.append(ranking)
        if method == "hybrid" and warnings and not lexical and not any(dense_rankings):
            raise RetrievalUnavailableError(
                "Dense search failed and lexical search found no matching evidence; retrieval is incomplete"
            )
        rankings = ([list(lexical_scores)] if lexical else []) + dense_rankings
        fused = reciprocal_rank_fusion(rankings, limit=k)
        result = []
        for rank, (identity, fusion_score) in enumerate(fused, 1):
            evidence = candidates[identity]
            legacy = identity in legacy_ids
            evidence.retrieval = RetrievalMetadata(
                method="dense_legacy" if legacy else method,
                rank=rank,
                fusion_score=fusion_score,
                lexical_score=lexical_scores.get(identity),
                dense_rank=dense_positions.get(identity),
                lexical_rank=lexical_positions.get(identity),
                index_version=chunks[identity][0].index_version
                if identity in chunks
                else None,
                warnings=list(
                    dict.fromkeys(
                        warnings
                        + (
                            [
                                "legacy_vector_only: explicitly reindex this document to enable lexical retrieval"
                            ]
                            if legacy
                            else []
                        )
                    )
                ),
            )
            result.append(evidence)
        return result
