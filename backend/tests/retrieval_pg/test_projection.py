from types import SimpleNamespace

from langchain_core.documents import Document
from sqlmodel import Session, select

from app.core.db import engine
from app.models import KnowledgeBase, KnowledgeDocument
from app.retrieval_models import KnowledgeChunk
from app.services import document_processor, knowledge_retrieval
from app.services.knowledge_index import build_chunks, publish_chunks


def test_postgres_jsonb_generation_rollback_and_document_cascade(owner):
    first = build_chunks(
        owner.document_id,
        owner.kb_id,
        [Document(page_content="RX-700 型号保修24个月，电池CR2032。")],
    )
    replacement = build_chunks(
        owner.document_id,
        owner.kb_id,
        [Document(page_content="RX-700 warranty revision: 36 months")],
    )
    with Session(engine) as session:
        publish_chunks(session, owner.document_id, first)
        session.commit()
        saved = session.exec(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == owner.document_id
            )
        ).one()
        assert saved.term_frequencies.get("rx-700") == 1
        assert saved.source_metadata["index_version"] == saved.index_version
        original_version = saved.index_version
        publish_chunks(session, owner.document_id, replacement)
        session.rollback()
    result = knowledge_retrieval.retrieve_evidence(
        owner.user_id, [owner.kb_id], "RX-700", method="lexical"
    )
    assert len(result) == 1 and result[0].retrieval.index_version == original_version
    with Session(engine) as session:
        publish_chunks(session, owner.document_id, replacement)
        session.commit()
    updated = knowledge_retrieval.retrieve_evidence(
        owner.user_id, [owner.kb_id], "warranty", method="lexical"
    )
    assert updated and updated[0].source_id != result[0].source_id
    with Session(engine) as session:
        session.delete(session.get(KnowledgeDocument, owner.document_id))
        session.commit()
        assert not session.exec(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == owner.document_id
            )
        ).all()
    assert (
        knowledge_retrieval.retrieve_evidence(
            owner.user_id, [owner.kb_id], "RX-700", method="lexical"
        )
        == []
    )


def test_real_pg_ingestion_publication_hybrid_and_knowledge_delete(owner, monkeypatch):
    documents = [
        Document(page_content="型号RX-700使用CR2032。"),
        Document(page_content="Live customer data retention is thirty days."),
    ]
    monkeypatch.setattr(
        document_processor, "split_document", lambda **_kwargs: documents
    )
    monkeypatch.setattr(document_processor, "delete_document_vectors", lambda *_args: 0)
    writes = []

    class FakeVectors:
        def add_documents(self, *, documents, ids):
            writes.extend(documents)
            assert len(ids) == len(set(ids)) == 2

    monkeypatch.setattr(
        document_processor, "create_vectorstore", lambda *_args: FakeVectors()
    )
    document_processor.ingest_document(owner.document_id)
    monkeypatch.setattr(
        knowledge_retrieval,
        "create_retriever",
        lambda *_args, **_kwargs: SimpleNamespace(
            invoke=lambda _query: list(reversed(writes))
        ),
    )
    result = knowledge_retrieval.retrieve_evidence(
        owner.user_id, [owner.kb_id], "RX-700 CR2032", k=1
    )
    assert len(result) == 1
    assert result[0].chunk_index == 0
    assert result[0].retrieval.method == "hybrid"
    assert result[0].retrieval.lexical_rank == 1
    with Session(engine) as session:
        chunks = session.exec(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == owner.document_id
            )
        ).all()
        assert len(chunks) == 2 and all(chunk.dense_indexed for chunk in chunks)
        session.delete(session.get(KnowledgeBase, owner.kb_id))
        session.commit()
        assert not session.exec(
            select(KnowledgeChunk).where(
                KnowledgeChunk.knowledge_base_id == owner.kb_id
            )
        ).all()
