from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.documents import Document
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models import KnowledgeBase, KnowledgeDocument, User
from app.research.errors import ResearchCancelled
from app.retrieval_models import KnowledgeChunk
from app.runtime.context import BudgetExceeded
from app.services import document_processor, knowledge_retrieval
from app.services.knowledge_index import build_chunks, publish_chunks


@pytest.fixture
def corpus(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            KnowledgeBase.__table__,
            KnowledgeDocument.__table__,
            KnowledgeChunk.__table__,
        ],
    )
    monkeypatch.setattr(knowledge_retrieval, "engine", engine)
    monkeypatch.setattr(document_processor, "engine", engine)
    with Session(engine, expire_on_commit=False) as session:
        users = [
            User(email=f"retrieval-{index}@example.com", hashed_password="fixture-only")
            for index in range(2)
        ]
        session.add_all(users)
        session.flush()
        bases = [
            KnowledgeBase(name=f"scope-{index}", owner_id=user.id)
            for index, user in enumerate(users)
        ]
        session.add_all(bases)
        session.flush()
        documents = [
            KnowledgeDocument(
                knowledge_base_id=base.id,
                original_filename=f"policy-{index}.txt",
                storage_key=f"fixture/{index}",
                status="ready",
            )
            for index, base in enumerate(bases)
        ]
        session.add_all(documents)
        session.flush()
        chunks = []
        for index, document in enumerate(documents):
            built = build_chunks(
                document.id,
                document.knowledge_base_id,
                [
                    Document(
                        page_content="RX-700 电池型号为CR2032，保修24个月。"
                        if index == 0
                        else "Other tenant secret RX-700"
                    )
                ],
            )
            publish_chunks(session, document.id, built)
            chunks.extend(built)
        session.commit()
        output = SimpleNamespace(
            engine=engine,
            users=[user.id for user in users],
            bases=[base.id for base in bases],
            docs=[document.id for document in documents],
            chunks=chunks,
        )

    def no_provider(*_args, **_kwargs):
        raise AssertionError("No external provider expected in this test")

    monkeypatch.setattr(knowledge_retrieval, "create_retriever", no_provider)
    yield output
    engine.dispose()


def test_lexical_is_authorized_bounded_and_has_provenance(corpus):
    result = knowledge_retrieval.retrieve_evidence(
        corpus.users[0], [corpus.bases[0]], "RX-700", k=1, method="lexical"
    )
    assert len(result) == 1
    assert result[0].document_id == str(corpus.docs[0])
    assert result[0].retrieval.method == "lexical"
    assert result[0].retrieval.lexical_score > 0
    assert result[0].retrieval.index_version == corpus.chunks[0].index_version
    assert "secret" not in result[0].content
    with pytest.raises(HTTPException) as error:
        knowledge_retrieval.retrieve_evidence(
            corpus.users[0], [corpus.bases[1]], "RX-700", method="lexical"
        )
    assert error.value.status_code == 404


def test_hybrid_dense_failure_is_explicit_and_budget_never_downgraded(
    corpus, monkeypatch
):
    monkeypatch.setattr(
        knowledge_retrieval,
        "create_retriever",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    result = knowledge_retrieval.retrieve_evidence(
        corpus.users[0], [corpus.bases[0]], "RX-700"
    )
    assert "dense_unavailable" in result[0].retrieval.warnings[0]
    with pytest.raises(knowledge_retrieval.RetrievalUnavailableError):
        knowledge_retrieval.retrieve_evidence(
            corpus.users[0], [corpus.bases[0]], "quasar"
        )
    for exception in [BudgetExceeded("budget"), ResearchCancelled("cancel")]:
        monkeypatch.setattr(
            knowledge_retrieval,
            "create_retriever",
            lambda *_args, error=exception, **_kwargs: (_ for _ in ()).throw(error),
        )
        with pytest.raises(type(exception)):
            knowledge_retrieval.retrieve_evidence(
                corpus.users[0], [corpus.bases[0]], "RX-700"
            )


def test_dense_uses_published_version_and_ignores_deleted_or_foreign(
    corpus, monkeypatch
):
    published = corpus.chunks[0]
    valid = Document(page_content=published.content, metadata=published.source_metadata)
    stale = Document(
        page_content="obsolete RX-700 secret",
        metadata={**published.source_metadata, "index_version": "old"},
    )
    foreign = Document(
        page_content="other tenant",
        metadata={"document_id": str(corpus.docs[1]), "chunk_index": 0},
    )
    monkeypatch.setattr(
        knowledge_retrieval,
        "create_retriever",
        lambda *_args, **_kwargs: SimpleNamespace(
            invoke=lambda _query: [stale, foreign, valid]
        ),
    )
    result = knowledge_retrieval.retrieve_evidence(
        corpus.users[0], [corpus.bases[0]], "battery", method="dense"
    )
    assert len(result) == 1 and result[0].content == published.content
    with Session(corpus.engine) as session:
        session.delete(session.get(KnowledgeDocument, corpus.docs[0]))
        session.commit()
        assert not session.exec(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == corpus.docs[0])
        ).all()
    assert (
        knowledge_retrieval.retrieve_evidence(
            corpus.users[0], [corpus.bases[0]], "battery", method="dense"
        )
        == []
    )


def test_capacity_limit_is_explicit_and_top_k_is_global(corpus, monkeypatch):
    monkeypatch.setattr(knowledge_retrieval, "MAX_AUTHORIZED_CHUNKS", 0)
    with pytest.raises(knowledge_retrieval.RetrievalCapacityError):
        knowledge_retrieval.retrieve_evidence(
            corpus.users[0], [corpus.bases[0]], "RX-700", method="lexical"
        )


def test_chunk_generation_is_stable_and_replaces_atomically(corpus):
    first = build_chunks(
        corpus.docs[0],
        corpus.bases[0],
        [Document(page_content="original"), Document(page_content="second")],
    )
    replay = build_chunks(
        corpus.docs[0],
        corpus.bases[0],
        [Document(page_content="original"), Document(page_content="second")],
    )
    changed = build_chunks(
        corpus.docs[0], corpus.bases[0], [Document(page_content="updated")]
    )
    assert [item.id for item in first] == [item.id for item in replay]
    assert first[0].index_version != changed[0].index_version
    with Session(corpus.engine) as session:
        publish_chunks(session, corpus.docs[0], first)
        session.commit()
        publish_chunks(session, corpus.docs[0], changed)
        session.rollback()
        assert (
            len(
                session.exec(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == corpus.docs[0]
                    )
                ).all()
            )
            == 2
        )
        publish_chunks(session, corpus.docs[0], changed)
        session.commit()
        assert (
            len(
                session.exec(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == corpus.docs[0]
                    )
                ).all()
            )
            == 1
        )


def test_ingestion_can_publish_lexical_without_credentials_and_is_replay_safe(
    corpus, monkeypatch
):
    monkeypatch.setattr(
        document_processor,
        "split_document",
        lambda **_kwargs: [Document(page_content="new RX-700 retention 30 days")],
    )
    monkeypatch.setattr(document_processor, "delete_document_vectors", lambda *_args: 0)
    monkeypatch.setattr(
        document_processor,
        "create_vectorstore",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("embedding is not configured")
        ),
    )
    document_processor.ingest_document(corpus.docs[0])
    document_processor.ingest_document(corpus.docs[0], lexical_only=True)
    with Session(corpus.engine) as session:
        document = session.get(KnowledgeDocument, corpus.docs[0])
        chunks = session.exec(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == corpus.docs[0])
        ).all()
        assert document.status == "ready" and document.chunk_count == 1
        assert len(chunks) == 1 and not chunks[0].dense_indexed
        assert "Lexical-only" in document.error_message
    assert knowledge_retrieval.retrieve_evidence(
        corpus.users[0], [corpus.bases[0]], "retention", method="lexical"
    )
