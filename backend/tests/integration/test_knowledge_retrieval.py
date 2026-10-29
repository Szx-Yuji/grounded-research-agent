"""Real ownership/document records with vector retrieval replaced in memory."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.documents import Document
from sqlmodel import Session

from app.core.db import engine
from app.models import KnowledgeDocument
from app.services import knowledge_retrieval


def add_document(kb_id, *, status="ready", filename="policy.txt"):
    with Session(engine) as session:
        document = KnowledgeDocument(
            knowledge_base_id=kb_id,
            original_filename=filename,
            storage_key=f"fixture/{uuid.uuid4()}",
            status=status,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return document.id


def test_retrieval_filters_nonready_missing_and_cross_knowledge_documents(
    actors, monkeypatch
):
    actor_a, actor_b = actors
    ready_id = add_document(actor_a.knowledge_base_id)
    processing_id = add_document(actor_a.knowledge_base_id, status="processing")
    failed_id = add_document(actor_a.knowledge_base_id, status="failed")
    other_id = add_document(actor_b.knowledge_base_id)
    chunks = [
        Document(
            page_content="Allowed evidence",
            metadata={
                "document_id": str(ready_id),
                "chunk_index": 0,
                "source": "untrusted-name",
            },
        ),
        Document(
            page_content="Allowed evidence",
            metadata={"document_id": str(ready_id), "chunk_index": 0},
        ),
        Document(
            page_content="Still indexing", metadata={"document_id": str(processing_id)}
        ),
        Document(
            page_content="Failed indexing", metadata={"document_id": str(failed_id)}
        ),
        Document(
            page_content="Deleted document", metadata={"document_id": str(uuid.uuid4())}
        ),
        Document(page_content="Other user", metadata={"document_id": str(other_id)}),
        Document(page_content="Invalid UUID", metadata={"document_id": "not-a-uuid"}),
        Document(page_content="No provenance", metadata={}),
    ]
    calls = []

    def retriever(kb_id, *, k):
        calls.append((kb_id, k))
        return SimpleNamespace(invoke=lambda query: chunks)

    monkeypatch.setattr(knowledge_retrieval, "create_retriever", retriever)
    evidence = knowledge_retrieval.retrieve_evidence(
        actor_a.id, [actor_a.knowledge_base_id], "Policy", k=4
    )
    assert calls == [(actor_a.knowledge_base_id, 4)]
    assert len(evidence) == 1
    assert evidence[0].document_id == str(ready_id)
    assert evidence[0].content == "Allowed evidence"
    assert evidence[0].title == "policy.txt"
    assert evidence[0].kind == "knowledge"
    assert evidence[0].url is None
    assert evidence[0].chunk_index == 0


def test_unauthorized_knowledge_is_rejected_before_vector_or_embedding_call(
    actors, monkeypatch
):
    actor_a, actor_b = actors

    def forbidden_retriever(*_args, **_kwargs):
        raise AssertionError("Unauthorized request reached vector retrieval")

    monkeypatch.setattr(knowledge_retrieval, "create_retriever", forbidden_retriever)
    with pytest.raises(HTTPException) as error:
        knowledge_retrieval.retrieve_evidence(
            actor_a.id, [actor_b.knowledge_base_id], "Secret"
        )
    assert error.value.status_code == 404
