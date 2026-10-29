import logging
import uuid

from sqlmodel import Session

from app.core.db import engine
from app.models import KnowledgeDocument
from app.services.document_ingestion import split_document
from app.services.rag_service import (
    create_vectorstore,
    delete_document_vectors,
)

logger = logging.getLogger(__name__)


def ingest_document(document_id: uuid.UUID) -> None:
    with Session(engine) as session:
        document = session.get(
            KnowledgeDocument,
            document_id,
        )

        if document is None:
            logger.warning(
                "Document not found during ingestion: %s",
                document_id,
            )
            return

        document.status = "processing"
        document.error_message = None

        session.add(document)
        session.commit()
        session.refresh(document)

        try:
            delete_document_vectors(
                document.knowledge_base_id,
                document.id,
            )

            chunks = split_document(
                storage_key=document.storage_key,
                original_filename=document.original_filename,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
            )

            if not chunks:
                raise ValueError("Document contains no readable text")

            vectorstore = create_vectorstore(document.knowledge_base_id)

            chunk_ids = [f"{document.id}_{index}" for index in range(len(chunks))]

            vectorstore.add_documents(
                documents=chunks,
                ids=chunk_ids,
            )

            document.status = "ready"
            document.chunk_count = len(chunks)
            document.error_message = None

            session.add(document)
            session.commit()

        except Exception as exc:
            session.rollback()

            failed_document = session.get(
                KnowledgeDocument,
                document_id,
            )

            if failed_document is not None:
                failed_document.status = "failed"
                failed_document.chunk_count = 0
                failed_document.error_message = str(exc)[:2000]

                session.add(failed_document)
                session.commit()

            logger.exception(
                "Document ingestion failed: %s",
                document_id,
            )
