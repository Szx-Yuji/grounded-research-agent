"""Storage cleanup, database finish recovery, and the full local evidence path."""

import asyncio
import hashlib
import importlib
import threading
import time
import uuid
from contextlib import AbstractContextManager
from types import SimpleNamespace

import httpx
import pytest
import requests
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

import app.research
from app.api.routes import research as research_routes
from app.core.config import settings
from app.core.db import engine
from app.models import KnowledgeBase, KnowledgeDocument, ResearchEvent, ResearchRun
from app.services import rag_service, research_runs

RUNS = "/api/v1/research/runs"
KNOWLEDGE = "/api/v1/knowledge-bases"


def await_worker(run_id: uuid.UUID) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with research_runs._lock:
            if run_id not in research_runs._active:
                return
        time.sleep(0.01)
    raise AssertionError("The integration worker did not finish")


class DeterministicLocalEmbeddings(Embeddings):
    """Local deterministic vectors; no model, HTTP request, or downloaded weights."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [float(value + 1) / 256 for value in digest[:16]]


class FakeStream(AbstractContextManager):
    def __init__(self, parts: list[str]):
        self.parts = parts

    def __iter__(self):
        for text in self.parts:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
            )

    def __exit__(self, *_args):
        return False


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    # An accidental default Chroma embedding or external HTTP call must fail,
    # even when a model happened to be cached on the host already.
    from chromadb.telemetry.product.posthog import Posthog
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
        ONNXMiniLM_L6_V2,
    )

    def forbidden_default_model(*_args, **_kwargs):
        pytest.fail("Integration test attempted to download/use default embeddings")

    monkeypatch.setattr(
        ONNXMiniLM_L6_V2, "_download_model_if_not_exists", forbidden_default_model
    )
    monkeypatch.setattr(Posthog, "capture", lambda *_args, **_kwargs: None)
    network_attempts = []
    original_send = httpx.Client.send

    def no_external_send(client, *_args, **_kwargs):
        if isinstance(client, TestClient):
            return original_send(client, *_args, **_kwargs)
        network_attempts.append("httpx")
        raise AssertionError("Unexpected external HTTP request")

    def no_external_requests(*_args, **_kwargs):
        network_attempts.append("requests")
        raise AssertionError("Unexpected external HTTP request")

    monkeypatch.setattr(httpx.Client, "send", no_external_send)
    monkeypatch.setattr(requests.sessions.Session, "request", no_external_requests)
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")
    monkeypatch.setattr(settings, "CHROMA_PERSIST_DIRECTORY", tmp_path / "chroma")
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", tmp_path / "documents")
    yield tmp_path
    assert network_attempts == []


def test_empty_knowledge_base_deletion_needs_no_embedding_key(
    client, actors, local_storage, monkeypatch
):
    actor_a, _ = actors
    assert settings.CHROMA_PERSIST_DIRECTORY.is_relative_to(local_storage)
    monkeypatch.setattr(settings, "ZHIPUAI_API_KEY", None)

    def forbidden_embeddings():
        pytest.fail("Deleting an empty KB must not initialize embeddings")

    monkeypatch.setattr(rag_service, "create_embeddings", forbidden_embeddings)
    response = client.delete(
        f"{KNOWLEDGE}/{actor_a.knowledge_base_id}", headers=actor_a.headers
    )
    assert response.status_code == 200
    with Session(engine) as session:
        assert session.get(KnowledgeBase, actor_a.knowledge_base_id) is None


def test_failed_finish_persistence_is_reconciled_without_repeating_model(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    model_calls = []
    persist_attempts = []
    original_persist = research_runs._persist_finish

    def fake_execution(*_args, **_kwargs):
        model_calls.append("executed")
        return {"final_report": "Already generated", "sub_tasks": [], "sources": []}

    def database_unavailable(*_args, **_kwargs):
        persist_attempts.append("attempt")
        raise SQLAlchemyError("Simulated temporary persistence outage")

    monkeypatch.setattr(app.research, "execute_research", fake_execution)
    monkeypatch.setattr(research_runs, "_persist_finish", database_unavailable)
    response = client.post(
        RUNS,
        headers=actor_a.headers,
        json={"query": "Persistence recovery", "use_web": True},
    )
    assert response.status_code == 202
    run_id = uuid.UUID(response.json()["id"])
    try:
        await_worker(run_id)
        assert persist_attempts == ["attempt"] * 3
        with research_runs._lock:
            assert run_id in research_runs._pending_finishes
        with Session(engine) as session:
            run = session.get(ResearchRun, run_id)
            assert run is not None and run.status == "running"
        monkeypatch.setattr(research_runs, "_persist_finish", original_persist)
        recovered = client.get(f"{RUNS}/{run_id}", headers=actor_a.headers)
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "succeeded"
        assert recovered.json()["final_report"] == "Already generated"
        assert model_calls == ["executed"]
        with research_runs._lock:
            assert run_id not in research_runs._pending_finishes
        client.get(f"{RUNS}/{run_id}", headers=actor_a.headers)
        with Session(engine) as session:
            events = session.exec(
                select(ResearchEvent).where(ResearchEvent.run_id == run_id)
            ).all()
            assert [event.event_type for event in events] == ["complete"]
    finally:
        # Cleanup even if an assertion fails while persistence was disabled.
        monkeypatch.setattr(research_runs, "_persist_finish", original_persist)
        research_runs.reconcile_finishes()


def test_upload_to_real_chroma_to_research_report_and_sse(
    client, actors, local_storage, monkeypatch
):
    actor_a, _ = actors
    assert settings.CHROMA_PERSIST_DIRECTORY.is_relative_to(local_storage)
    private_marker = "INTERNAL_FIXTURE_EVIDENCE_NEVER_SEND_TO_PUBLIC_SEARCH"
    document_text = (
        f"{private_marker}\nOur internal storage policy keeps reports for 30 days."
    )
    monkeypatch.setattr(
        rag_service, "create_embeddings", lambda: DeterministicLocalEmbeddings()
    )
    upload = client.post(
        f"{KNOWLEDGE}/{actor_a.knowledge_base_id}/documents",
        headers=actor_a.headers,
        files={"file": ("retention-policy.txt", document_text.encode(), "text/plain")},
    )
    assert upload.status_code == 201
    document_id = uuid.UUID(upload.json()["id"])
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.status == "ready"
        assert document.chunk_count == 1
        assert (
            settings.DOCUMENT_STORAGE_DIR / document.storage_key
        ).read_text() == document_text

    search = client.get(
        f"{KNOWLEDGE}/{actor_a.knowledge_base_id}/search",
        headers=actor_a.headers,
        params={"query": "report retention", "k": 4},
    )
    assert search.status_code == 200
    assert search.json()["count"] == 1
    assert private_marker in search.json()["data"][0]["page_content"]
    assert search.json()["data"][0]["source_metadata"]["document_id"] == str(
        document_id
    )

    execution_engine = importlib.import_module("app.research.engine")
    monkeypatch.setattr(
        app.research, "execute_research", execution_engine.execute_research
    )
    model_calls, public_searches = [], []
    source_id = f"kb:{document_id}:0"

    def create(**kwargs):
        model_calls.append(kwargs)
        if not kwargs.get("stream"):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='["public data retention overview"]'
                        )
                    )
                ]
            )
        if "research analyst" in kwargs["messages"][0]["content"]:
            return FakeStream(["Analysis ", f"[{source_id}]"])
        return FakeStream(["## Report\n", f"Retention is 30 days [{source_id}]."])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    for name in ("planner", "refiner", "analyst", "writer"):
        module = importlib.import_module(f"app.research.agents.{name}")
        monkeypatch.setattr(module, "get_client", lambda: fake_client)
    researcher = importlib.import_module("app.research.agents.researcher")

    def fake_search(query):
        public_searches.append(query)
        return [
            {
                "title": "Public retention overview",
                "url": "https://example.com/retention",
                "content": "Public general guidance",
            }
        ]

    monkeypatch.setattr(researcher, "search_web", fake_search)
    created = client.post(
        RUNS,
        headers=actor_a.headers,
        json={
            "query": "Compare report retention",
            "knowledge_base_ids": [str(actor_a.knowledge_base_id)],
            "use_web": True,
        },
    )
    assert created.status_code == 202
    run_id = uuid.UUID(created.json()["id"])
    await_worker(run_id)
    completed = client.get(f"{RUNS}/{run_id}", headers=actor_a.headers).json()
    assert completed["status"] == "succeeded"
    assert (
        completed["final_report"] == f"## Report\nRetention is 30 days [{source_id}]."
    )
    assert {source["kind"] for source in completed["sources"]} == {"knowledge", "web"}
    private_source = next(
        source for source in completed["sources"] if source["kind"] == "knowledge"
    )
    assert private_source["document_id"] == str(document_id)
    assert private_source["source_id"] == source_id
    assert private_marker in private_source["content"]
    assert public_searches == ["public data retention overview"]
    assert len(model_calls) == 3
    assert private_marker not in str(model_calls[0])
    assert all(private_marker in str(call) for call in model_calls[1:])
    streamed = client.get(f"{RUNS}/{run_id}/events", headers=actor_a.headers)
    assert streamed.status_code == 200
    assert "event: analysis_token" in streamed.text
    assert "event: report_token" in streamed.text
    assert "event: complete" in streamed.text
    assert "Retention is 30 days" in streamed.text
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None and run.owner_id == actor_a.id


def test_sse_releases_authorization_transaction_before_polling(actors, monkeypatch):
    actor_a, _ = actors
    with Session(engine) as session:
        run = ResearchRun(owner_id=actor_a.id, query="Finished", status="succeeded")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    authorization_session = Session(engine)
    polled = threading.Event()

    def poll(_run_id, _owner_id, _cursor):
        assert not authorization_session.in_transaction()
        polled.set()
        return [], "succeeded"

    async def connected():
        return False

    monkeypatch.setattr(research_routes, "_poll", poll)
    try:
        response = research_routes.stream_events(
            run_id,
            request=SimpleNamespace(is_disconnected=connected),
            session=authorization_session,
            current_user=SimpleNamespace(id=actor_a.id),
            after=0,
            last_event_id=None,
        )
        assert not authorization_session.in_transaction()

        async def consume():
            return [event async for event in response.body_iterator]

        assert asyncio.run(consume()) == []
        assert polled.is_set()
    finally:
        authorization_session.close()
