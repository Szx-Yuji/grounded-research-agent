"""The compatibility endpoint must use the same RAG delegate as /chat."""

from uuid import uuid4

import pytest

from app.runtime.context import BudgetExceeded
from app.services import legacy_knowledge


def test_legacy_adapter_delegates_with_bounded_context(monkeypatch):
    owner, knowledge_base = uuid4(), uuid4()
    observed = {}

    def fake_delegate(question, user_id, knowledge_base_ids):
        observed.update(
            question=question,
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
        )
        context = legacy_knowledge.execution_context
        assert context is not None
        return {
            "answer": "Grounded answer [kb:fixture:0]",
            "sources": [
                {
                    "source_id": "kb:fixture:0",
                    "kind": "knowledge",
                    "content": "Fixture evidence",
                    "title": "fixture.txt",
                    "document_id": str(uuid4()),
                    "chunk_index": 0,
                }
            ],
        }

    monkeypatch.setattr(legacy_knowledge, "ask_knowledge", fake_delegate)
    result = legacy_knowledge.execute_legacy_knowledge(
        "What is in the fixture?", owner, [knowledge_base]
    )
    assert result["answer"].startswith("Grounded answer")
    assert observed == {
        "question": "What is in the fixture?",
        "user_id": owner,
        "knowledge_base_ids": [knowledge_base],
    }


def test_legacy_adapter_propagates_budget_failures_without_fallback(monkeypatch):
    monkeypatch.setattr(
        legacy_knowledge,
        "ask_knowledge",
        lambda *_args: (_ for _ in ()).throw(BudgetExceeded("budget")),
    )
    with pytest.raises(BudgetExceeded, match="budget"):
        legacy_knowledge.execute_legacy_knowledge("question", uuid4(), [uuid4()])
