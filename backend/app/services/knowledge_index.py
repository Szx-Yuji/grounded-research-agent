"""Build immutable chunk identities, then publish one generation atomically."""

import hashlib
import json
import uuid
from collections.abc import Sequence

from langchain_core.documents import Document
from sqlalchemy import delete
from sqlmodel import Session

from app.retrieval_models import KnowledgeChunk
from app.services.retrieval_ranking import TOKENIZER_VERSION, term_data

INDEX_SCHEMA_VERSION = "grounded-chunks-v1"


def build_chunks(
    document_id: uuid.UUID, knowledge_base_id: uuid.UUID, documents: Sequence[Document]
) -> list[KnowledgeChunk]:
    fingerprint = json.dumps(
        {
            "schema": INDEX_SCHEMA_VERSION,
            "tokenizer": TOKENIZER_VERSION,
            "chunks": [document.page_content for document in documents],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    index_version = hashlib.sha256(fingerprint.encode()).hexdigest()
    result = []
    for index, document in enumerate(documents):
        content_hash = hashlib.sha256(document.page_content.encode()).hexdigest()
        identity = hashlib.sha256(
            f"{document_id}:{index_version}:{index}".encode()
        ).hexdigest()
        counts, length = term_data(document.page_content)
        metadata = {
            **document.metadata,
            "document_id": str(document_id),
            "knowledge_base_id": str(knowledge_base_id),
            "chunk_index": index,
            "index_version": index_version,
            "content_hash": content_hash,
            "chunk_id": identity,
        }
        # Give both stores exactly the same provenance and stable generation ID.
        document.metadata = metadata
        result.append(
            KnowledgeChunk(
                id=identity,
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
                chunk_index=index,
                content=document.page_content,
                content_hash=content_hash,
                index_version=index_version,
                tokenizer_version=TOKENIZER_VERSION,
                token_count=length,
                term_frequencies=counts,
                source_metadata=metadata,
            )
        )
    return result


def publish_chunks(
    session: Session, document_id: uuid.UUID, chunks: Sequence[KnowledgeChunk]
) -> None:
    """Caller commits with document.ready; no intermediate generation is visible."""
    if any(chunk.document_id != document_id for chunk in chunks):
        raise ValueError("Chunks do not belong to the target document")
    session.exec(
        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
    )
    session.add_all(chunks)
