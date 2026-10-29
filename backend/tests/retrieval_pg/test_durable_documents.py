import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.security import create_access_token
from app.main import app
from app.models import ExecutionJob, KnowledgeDocument
from app.retrieval_models import KnowledgeChunk
from app.services import document_processor, durable_jobs


def claim_target(target_id):
    """Claim only this fixture's target; never consume another suite's queue."""
    with Session(engine) as session:
        job = session.exec(
            select(ExecutionJob)
            .where(ExecutionJob.kind == "document", ExecutionJob.target_id == target_id)
            .with_for_update()
        ).one()
        assert job.status == "queued"
        job.status = "running"
        job.lease_token = uuid.uuid4()
        job.attempt_count += 1
        job.worker_id = "retrieval-pg-test"
        job.lease_expires_at = durable_jobs.database_now(session) + timedelta(
            seconds=120
        )
        job.deadline_at = durable_jobs.database_now(session) + timedelta(seconds=120)
        session.add(job)
        session.commit()
        return durable_jobs.Claim(
            job.id, job.lease_token, job.kind, job.target_id, job.owner_id, job.payload
        )


def test_real_upload_enqueues_without_running_parser_and_publication_is_atomic(
    owner, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", tmp_path)
    calls = []

    def parser(**_kwargs):
        calls.append("parse")
        return [Document(page_content="RX-700 电池型号 CR2032")]

    monkeypatch.setattr(document_processor, "split_document", parser)
    monkeypatch.setattr(document_processor, "delete_document_vectors", lambda *_args: 0)
    monkeypatch.setattr(
        document_processor,
        "create_vectorstore",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("No paid provider in fixture")
        ),
    )
    token = create_access_token(owner.user_id, timedelta(minutes=5))
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/knowledge-bases/{owner.kb_id}/documents",
            headers=headers,
            files={
                "file": ("device.txt", b"RX-700 battery specification", "text/plain")
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "uploaded"
        assert calls == []
        target_id = uuid.UUID(response.json()["id"])
        claim = claim_target(target_id)
        durable_jobs.execute_claim(claim)
        with Session(engine) as session:
            document = session.get(KnowledgeDocument, target_id)
            job = session.get(ExecutionJob, claim.job_id)
            assert document.status == "ready" and job.status == "succeeded"
            assert (
                session.exec(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == target_id
                    )
                )
                .one()
                .content
                == "RX-700 电池型号 CR2032"
            )
        monkeypatch.setattr(
            "app.api.routes.knowledge_bases.delete_document_vectors", lambda *_args: 0
        )
        deleted = client.delete(
            f"/api/v1/knowledge-bases/{owner.kb_id}/documents/{target_id}",
            headers=headers,
        )
        assert deleted.status_code == 200
        with Session(engine) as session:
            assert session.get(ExecutionJob, claim.job_id) is None
            assert session.get(KnowledgeDocument, target_id) is None


def test_superseded_lease_cannot_publish_or_mark_a_new_attempt_failed(
    owner, monkeypatch
):
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, owner.document_id)
        document.status = "uploaded"
        session.add(document)
        durable_jobs.enqueue_document(session, document)
        session.commit()
    claim = claim_target(owner.document_id)

    def parser(**_kwargs):
        with Session(engine) as session:
            job = session.get(ExecutionJob, claim.job_id)
            job.lease_token = uuid.uuid4()
            session.add(job)
            session.commit()
        return [Document(page_content="stale attempt must never publish")]

    monkeypatch.setattr(document_processor, "split_document", parser)

    def forbidden_projection(*_args):
        raise AssertionError("A stale worker must not touch dense storage")

    monkeypatch.setattr(document_processor, "create_vectorstore", forbidden_projection)
    with durable_jobs.execution_context(claim), pytest.raises(durable_jobs.LeaseLost):
        document_processor.ingest_document(owner.document_id)
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, owner.document_id)
        job = session.get(ExecutionJob, claim.job_id)
        assert document.status == "processing"
        assert job.status == "running" and job.lease_token != claim.token
        assert not session.exec(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == owner.document_id
            )
        ).all()
