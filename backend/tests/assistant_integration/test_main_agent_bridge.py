"""API/DB/worker -> real main agent -> RAG delegate -> continuous second turn."""

import copy
import importlib
import json
import time
import uuid
from contextlib import AbstractContextManager
from types import SimpleNamespace

from sqlmodel import Session

from app import assistant
from app.core.db import engine
from app.models import AssistantConversation, AssistantTurn, KnowledgeDocument
from app.services import assistant_conversations
from app.services.knowledge_retrieval import Evidence

BASE = "/api/v1/assistant/conversations"


class FakeModelStream(AbstractContextManager):
    def __init__(self, text):
        self.text = text

    def __iter__(self):
        midpoint = len(self.text) // 2
        for fragment in [self.text[:midpoint], self.text[midpoint:]]:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=fragment))]
            )

    def __exit__(self, *_args):
        return False


def test_real_main_agent_delegates_rag_then_continues_same_conversation(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    with Session(engine) as session:
        document = KnowledgeDocument(
            knowledge_base_id=actor_a.knowledge_base_id,
            original_filename="options.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="ready",
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        evidence = Evidence(
            source_id=f"kb:{document.id}:0",
            kind="knowledge",
            content="PRIVATE_RAW_OPTIONS: First plan: thirty days.",
            title="Retention options",
            document_id=str(document.id),
            chunk_index=0,
        )
        second_document = KnowledgeDocument(
            knowledge_base_id=actor_a.knowledge_base_id,
            original_filename="second-options.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="ready",
        )
        session.add(second_document)
        session.commit()
        session.refresh(second_document)
        second_evidence = Evidence(
            source_id=f"kb:{second_document.id}:0",
            kind="knowledge",
            content="PRIVATE_RAW_SECOND_OPTIONS: Second plan: ninety days.",
            title="Second retention plan",
            document_id=str(second_document.id),
            chunk_index=0,
        )

    main_engine = importlib.import_module("app.assistant.engine")
    subagents = importlib.import_module("app.assistant.subagents")
    monkeypatch.setattr(assistant, "execute_chat", main_engine.execute_chat)
    calls, retrievals = [], []
    decision_count = 0
    answer_count = 0
    first_answer = f"The first plan keeps data for thirty days [{evidence.source_id}]."
    second_answer = f"The second plan keeps it for ninety days [{evidence.source_id}]."

    def response(content="", tool_calls=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, tool_calls=tool_calls)
                )
            ]
        )

    def create(**kwargs):
        nonlocal decision_count, answer_count
        calls.append(copy.deepcopy(kwargs))
        if kwargs.get("stream"):
            answer_count += 1
            return FakeModelStream(first_answer if answer_count == 1 else second_answer)
        if (
            "dedicated knowledge-retrieval sub-agent"
            in kwargs["messages"][0]["content"]
        ):
            return response(
                f"First plan: thirty days; second plan: ninety days [{evidence.source_id}]."
            )
        decision_count += 1
        if decision_count == 1:
            return response(
                tool_calls=[
                    SimpleNamespace(
                        id="knowledge-call-1",
                        function=SimpleNamespace(
                            name="ask_knowledge",
                            arguments=json.dumps(
                                {"question": "What are the two retention plans?"}
                            ),
                        ),
                    )
                ]
            )
        return response("The conversation already contains the needed answer.")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(main_engine, "get_client", lambda: fake_client)
    monkeypatch.setattr(subagents, "get_client", lambda: fake_client)

    def retrieval(user_id, knowledge_base_ids, question, *, k):
        assert user_id == actor_a.id
        assert knowledge_base_ids == [actor_a.knowledge_base_id]
        assert k == 4
        retrievals.append(question)
        return [evidence, second_evidence]

    monkeypatch.setattr(subagents, "retrieve_evidence", retrieval)
    conversation = client.post(
        BASE,
        headers=actor_a.headers,
        json={"knowledge_base_ids": [str(actor_a.knowledge_base_id)], "use_web": False},
    )
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    def chat(message):
        created = client.post(
            f"{BASE}/{conversation_id}/messages",
            headers=actor_a.headers,
            json={"content": message},
        )
        assert created.status_code == 202
        turn_id = created.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with assistant_conversations._lock:
                if uuid.UUID(turn_id) not in assistant_conversations._active:
                    break
            time.sleep(0.01)
        turn = client.get(
            f"{BASE}/{conversation_id}/turns/{turn_id}", headers=actor_a.headers
        )
        assert turn.status_code == 200
        assert turn.json()["status"] == "succeeded"
        return turn.json()

    first = chat("Explain the retention plans.")
    second = chat("What about the second plan?")
    assert first["answer"] == first_answer
    assert second["answer"] == second_answer
    assert retrievals == ["What are the two retention plans?"]
    assert {source["document_id"] for source in first["sources"]} == {
        evidence.document_id,
        second_evidence.document_id,
    }
    assert second["sources"][0]["source_id"] == evidence.source_id
    assert (
        len(calls) == 6
    )  # Main decision, RAG, observation decision, answer; follow-up decision, answer.
    followup_decision = calls[4]
    history = followup_decision["messages"]
    assert any(
        item["role"] == "user" and item["content"] == "Explain the retention plans."
        for item in history
    )
    assert any(
        item["role"] == "assistant" and item["content"].startswith(first_answer)
        for item in history
    )
    assert any(
        item["role"] == "user" and item["content"] == "What about the second plan?"
        for item in history
    )
    assert "First plan: thirty days; second plan: ninety days" in str(history)
    assert "PRIVATE_RAW_OPTIONS" not in str(history)
    assert "PRIVATE_RAW_SECOND_OPTIONS" not in str(history)
    with Session(engine) as session:
        stored_turn = session.get(AssistantTurn, uuid.UUID(first["id"]))
        assert stored_turn is not None
        assert any(
            note["tool"] == "ask_knowledge"
            and "second plan: ninety days" in note["answer"]
            for note in stored_turn.delegate_notes
        )
    assert "PRIVATE_RAW_OPTIONS" in str(
        calls[1]
    )  # Raw evidence only entered the RAG child request.
    persisted = client.get(
        f"{BASE}/{conversation_id}/messages", headers=actor_a.headers
    ).json()
    assert persisted["count"] == 4
    assert persisted["data"][-1]["content"] == second_answer
    stream = client.get(
        f"{BASE}/{conversation_id}/turns/{first['id']}/events", headers=actor_a.headers
    )
    assert stream.status_code == 200
    assert "ask_knowledge" in stream.text
    assert "event: token" in stream.text and "event: complete" in stream.text

    # The final first answer cites A only. B still supports the delegated note,
    # so deleting B must invalidate reuse of that whole dependent answer/note.
    assert f"[{second_evidence.source_id}]" not in first["answer"]
    with Session(engine) as session:
        deleted = session.get(KnowledgeDocument, uuid.UUID(second_evidence.document_id))
        assert deleted is not None
        session.delete(deleted)
        session.commit()
        stored_conversation = session.get(
            AssistantConversation, uuid.UUID(conversation_id)
        )
        assert stored_conversation is not None
        history_after_delete, _summary, _covered = assistant_conversations.load_context(
            session, stored_conversation
        )
        earlier_answers = [
            item for item in history_after_delete if item["role"] == "assistant"
        ]
        assert earlier_answers
        assert all("unavailable" in item["content"] for item in earlier_answers)
        assert "second plan: ninety days" not in str(history_after_delete)
        assert "Earlier delegated findings" not in str(history_after_delete)
