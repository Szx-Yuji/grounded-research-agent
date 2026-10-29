"""Publish a relational chunk generation and its optional dense projection."""

import logging
import uuid

from sqlmodel import Session, select

from app.core.db import engine
from app.models import ExecutionJob, KnowledgeDocument
from app.research.errors import ResearchCancelled
from app.research.events import check_cancelled
from app.runtime.context import BudgetExceeded
from app.services import durable_jobs
from app.services.document_ingestion import split_document
from app.services.knowledge_index import build_chunks, publish_chunks
from app.services.rag_service import create_vectorstore, delete_document_vectors

logger = logging.getLogger(__name__)


def ingest_document(document_id: uuid.UUID, *, lexical_only: bool = False) -> None:
    """The ready flag and all PG chunks become visible in one transaction.

    Dense indexing is a projection: missing provider credentials or an index
    outage can leave a usable lexical index with an explicit status warning.
    Resource-limit/cancellation exceptions are never downgraded to a fallback.
    """
    claimed = durable_jobs.current_claim() is not None
    with Session(engine) as session:
        if claimed:
            durable_jobs.assert_lease(session)
        elif session.bind.dialect.name == "postgresql":
            # Administrative reindex must not overlap a durable attempt. Job
            # locks precede the document lock just as in the worker path.
            job = session.exec(
                select(ExecutionJob)
                .where(
                    ExecutionJob.kind == "document",
                    ExecutionJob.target_id == document_id,
                )
                .with_for_update()
            ).first()
            if job is not None and job.status in {"queued", "running"}:
                raise RuntimeError(
                    "This document has an active durable job; wait or cancel it before explicit reindex"
                )
        document = session.get(KnowledgeDocument, document_id, with_for_update=True)
        if document is None:
            logger.warning("Document not found during ingestion: %s", document_id)
            if claimed:
                durable_jobs.finish_in_transaction(
                    session, "failed", "Document was deleted"
                )
                session.commit()
            return
        document.status = "processing"
        document.error_message = None
        session.add(document)
        session.commit()
        session.refresh(document)
        kb_id = document.knowledge_base_id
        try:
            chunks = split_document(
                storage_key=document.storage_key,
                original_filename=document.original_filename,
                knowledge_base_id=kb_id,
                document_id=document.id,
            )
            if not chunks:
                raise ValueError("Document contains no readable text")
            published = build_chunks(document.id, kb_id, chunks)
            check_cancelled()
            if claimed:
                # Parsing may outlive the lease. Verify again before touching
                # either a provider or the local dense projection.
                durable_jobs.assert_lease(session)
                session.commit()
            dense_indexed = False
            warning = (
                "Lexical-only index; dense indexing was explicitly disabled."
                if lexical_only
                else None
            )
            if not lexical_only:
                try:
                    vectorstore = create_vectorstore(kb_id)
                    check_cancelled()
                    vectorstore.add_documents(
                        documents=chunks, ids=[chunk.id for chunk in published]
                    )
                    dense_indexed = True
                except ResearchCancelled, BudgetExceeded, durable_jobs.LeaseLost:
                    raise
                except Exception as error:
                    warning = "Dense index unavailable; lexical retrieval remains available. Reindex explicitly when the provider is configured."
                    logger.error(
                        "Dense projection unavailable; publishing lexical chunks for document %s (%s)",
                        document_id,
                        type(error).__name__,
                    )
            for chunk in published:
                chunk.dense_indexed = dense_indexed
            # Refresh after external I/O: a deletion may have completed meanwhile.
            session.expire_all()
            if claimed:
                durable_jobs.assert_lease(session)
            document = session.get(KnowledgeDocument, document_id, with_for_update=True)
            if document is None:
                session.rollback()
                try:
                    delete_document_vectors(kb_id, document_id)
                except Exception as error:
                    logger.error(
                        "Could not clean dense orphan for deleted document %s (%s)",
                        document_id,
                        type(error).__name__,
                    )
                return
            publish_chunks(session, document.id, published)
            document.status = "ready"
            document.chunk_count = len(published)
            document.error_message = warning
            session.add(document)
            if claimed:
                durable_jobs.finish_in_transaction(session, "succeeded")
            session.commit()
        except Exception as error:
            session.rollback()
            if isinstance(error, durable_jobs.LeaseLost):
                raise
            if claimed:
                durable_jobs.assert_lease(session)
            failed = session.get(KnowledgeDocument, document_id, with_for_update=True)
            if failed is not None:
                failed.status = "failed"
                failed.chunk_count = 0
                failed.error_message = (
                    "Document indexing failed. Check the file and retry explicitly."
                )
                session.add(failed)
            if claimed:
                durable_jobs.finish_in_transaction(
                    session, "failed", "Document indexing failed; retry explicitly"
                )
            session.commit()
            logger.error(
                "Document ingestion failed: %s (%s)", document_id, type(error).__name__
            )
            if isinstance(error, (ResearchCancelled, BudgetExceeded)):
                raise
