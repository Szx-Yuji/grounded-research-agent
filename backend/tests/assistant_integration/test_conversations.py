"""Real PostgreSQL/JWT integration for persistent delegated-agent conversations."""

import copy
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, select

from app import assistant
from app.core.config import settings
from app.core.db import engine
from app.models import (
    AssistantConversation,
    AssistantEvent,
    AssistantTurn,
    KnowledgeDocument,
)
from app.services import assistant_conversations as service

BASE = "/api/v1/assistant/conversations"


def new_conversation(client, actor, **config):
    response = client.post(BASE, headers=actor.headers, json=config)
    assert response.status_code == 201
    return response.json()["id"]


def wait_turn(turn_id):
    deadline = time.monotonic() + 5
    turn_id = uuid.UUID(str(turn_id))
    while time.monotonic() < deadline:
        with service._lock:
            if turn_id not in service._active:
                return
        time.sleep(0.01)
    raise AssertionError("Assistant worker did not finish")


def send(client, actor, conversation_id, message):
    response = client.post(
        f"{BASE}/{conversation_id}/messages",
        headers=actor.headers,
        json={"content": message},
    )
    assert response.status_code == 202
    turn = response.json()
    wait_turn(turn["id"])
    return client.get(
        f"{BASE}/{conversation_id}/turns/{turn['id']}", headers=actor.headers
    ).json()


def messages(client, actor, conversation_id):
    response = client.get(f"{BASE}/{conversation_id}/messages", headers=actor.headers)
    assert response.status_code == 200
    return response.json()


def source_for(actor):
    with Session(engine) as session:
        document = KnowledgeDocument(
            knowledge_base_id=actor.knowledge_base_id,
            original_filename="private-policy.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="ready",
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return {
            "source_id": f"kb:{document.id}:0",
            "kind": "knowledge",
            "title": "Private policy",
            "document_id": str(document.id),
            "chunk_index": 0,
            "content": "PRIVATE_FIXTURE_EVIDENCE: first and second retention options",
        }


def test_conversation_requires_authentication(client):
    for method in ("GET", "POST"):
        response = client.request(method, BASE, json={} if method == "POST" else None)
        assert response.status_code in {401, 403}


def test_two_turn_history_persists_after_reopening_and_sse_resumes(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    captured = []
    source = source_for(actor_a)

    def execute_chat(**kwargs):
        captured.append(
            copy.deepcopy(
                {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"emit", "should_cancel"}
                }
            )
        )
        answer = (
            "The project is Atlas." if len(captured) == 1 else "Atlas, as discussed."
        )
        kwargs["emit"]("agent_update", {"agent": "Main", "action": "Answered"})
        kwargs["emit"]("token", {"content": answer})
        return {
            "answer": answer,
            "sources": [source],
            "new_summary": None,
            "summarized_message_count": 0,
        }

    monkeypatch.setattr(assistant, "execute_chat", execute_chat)
    conversation_id = new_conversation(
        client,
        actor_a,
        knowledge_base_ids=[str(actor_a.knowledge_base_id)],
        use_web=False,
    )
    first = send(client, actor_a, conversation_id, "My project is named Atlas.")
    second = send(client, actor_a, conversation_id, "What is its name?")
    assert first["status"] == second["status"] == "succeeded"
    assert captured[0]["history"] == []
    assert captured[0]["summary"] == ""
    assert captured[1]["message"] == "What is its name?"
    assert [(item["role"], item["content"]) for item in captured[1]["history"]] == [
        ("user", "My project is named Atlas."),
        ("assistant", "The project is Atlas."),
    ]
    assert captured[1]["history"][1]["sources"] == [source]
    assert captured[1]["user_id"] == actor_a.id
    assert captured[1]["knowledge_base_ids"] == [actor_a.knowledge_base_id]
    history = messages(client, actor_a, conversation_id)
    assert history["count"] == 4
    assert [item["content"] for item in history["data"]] == [
        "My project is named Atlas.",
        "The project is Atlas.",
        "What is its name?",
        "Atlas, as discussed.",
    ]
    reopened = client.get(f"{BASE}/{conversation_id}", headers=actor_a.headers)
    assert reopened.status_code == 200
    assert (
        client.get(
            f"{BASE}/{conversation_id}/turns/active", headers=actor_a.headers
        ).json()
        is None
    )
    listed = client.get(BASE, headers=actor_a.headers).json()
    assert [item["id"] for item in listed["data"]] == [conversation_id]

    stream_url = f"{BASE}/{conversation_id}/turns/{second['id']}/events"
    stream = client.get(stream_url, headers=actor_a.headers)
    assert stream.status_code == 200
    blocks = [block for block in stream.text.split("\n\n") if block.startswith("id:")]
    events = []
    for block in blocks:
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append(
            {
                "id": int(fields["id"]),
                "event": fields["event"],
                "data": json.loads(fields["data"]),
            }
        )
    assert [item["event"] for item in events] == ["agent_update", "token", "complete"]
    assert events[1]["data"] == {"content": "Atlas, as discussed."}
    assert events[-1]["data"]["turn"]["status"] == "succeeded"
    assert events[-1]["data"]["message"]["content"] == "Atlas, as discussed."
    resumed = client.get(
        stream_url, params={"after": events[1]["id"]}, headers=actor_a.headers
    )
    assert "event: complete" in resumed.text
    assert "event: token" not in resumed.text
    assert (
        client.get(
            stream_url, params={"after": events[-1]["id"]}, headers=actor_a.headers
        ).text
        == ""
    )
    with Session(engine) as session:
        conversation = session.get(AssistantConversation, uuid.UUID(conversation_id))
        assert conversation is not None and conversation.owner_id == actor_a.id
    assert client.delete(
        f"{BASE}/{conversation_id}", headers=actor_a.headers
    ).status_code in {200, 204}
    with Session(engine) as session:
        assert session.get(AssistantConversation, uuid.UUID(conversation_id)) is None
        assert (
            session.exec(
                select(AssistantTurn).where(
                    AssistantTurn.conversation_id == uuid.UUID(conversation_id)
                )
            ).first()
            is None
        )


def test_cross_user_conversation_turn_messages_and_events_are_isolated(
    client, actors, monkeypatch
):
    actor_a, actor_b = actors
    monkeypatch.setattr(
        assistant,
        "execute_chat",
        lambda **_kwargs: {
            "answer": "Owner B only",
            "sources": [],
            "summarized_message_count": 0,
        },
    )
    conversation_b = new_conversation(client, actor_b)
    turn_b = send(client, actor_b, conversation_b, "Private B")
    assert client.get(BASE, headers=actor_a.headers).json()["count"] == 0
    denied = client.post(
        BASE,
        headers=actor_a.headers,
        json={"knowledge_base_ids": [str(actor_b.knowledge_base_id)]},
    )
    assert denied.status_code == 404
    for method, suffix, body in [
        ("GET", "", None),
        ("PATCH", "", {"title": "Stolen"}),
        ("DELETE", "", None),
        ("GET", "/messages", None),
        ("POST", "/messages", {"content": "Stolen"}),
        ("GET", "/turns/active", None),
        ("GET", f"/turns/{turn_b['id']}", None),
        ("POST", f"/turns/{turn_b['id']}/cancel", None),
        ("GET", f"/turns/{turn_b['id']}/events", None),
    ]:
        response = client.request(
            method,
            f"{BASE}/{conversation_b}{suffix}",
            json=body,
            headers=actor_a.headers,
        )
        assert response.status_code == 404, (method, suffix, response.text)
    own_conversation = new_conversation(client, actor_a)
    patch = client.patch(
        f"{BASE}/{own_conversation}",
        headers=actor_a.headers,
        json={"knowledge_base_ids": [str(actor_b.knowledge_base_id)]},
    )
    assert patch.status_code == 404


def test_one_active_turn_blocks_second_submit_and_config_changes_cancel_has_no_answer(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    started, release = threading.Event(), threading.Event()

    def delayed(**_kwargs):
        started.set()
        assert release.wait(5)
        return {
            "answer": "Late answer",
            "sources": [],
            "new_summary": "Must not save",
            "summarized_message_count": 0,
        }

    monkeypatch.setattr(assistant, "execute_chat", delayed)
    conversation_id = new_conversation(client, actor_a)
    response = client.post(
        f"{BASE}/{conversation_id}/messages",
        headers=actor_a.headers,
        json={"content": "Wait"},
    )
    assert response.status_code == 202
    turn_id = response.json()["id"]
    try:
        assert started.wait(5)
        active = client.get(
            f"{BASE}/{conversation_id}/turns/active", headers=actor_a.headers
        ).json()
        assert active["id"] == turn_id
        assert (
            client.post(
                f"{BASE}/{conversation_id}/messages",
                headers=actor_a.headers,
                json={"content": "Overtake"},
            ).status_code
            == 409
        )
        assert (
            client.patch(
                f"{BASE}/{conversation_id}",
                headers=actor_a.headers,
                json={"use_web": True},
            ).status_code
            == 409
        )
        assert (
            client.delete(
                f"{BASE}/{conversation_id}", headers=actor_a.headers
            ).status_code
            == 409
        )
        cancelled = client.post(
            f"{BASE}/{conversation_id}/turns/{turn_id}/cancel", headers=actor_a.headers
        )
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
    finally:
        release.set()
        wait_turn(turn_id)
    assert [
        item["role"] for item in messages(client, actor_a, conversation_id)["data"]
    ] == ["user"]
    current = client.get(
        f"{BASE}/{conversation_id}/turns/{turn_id}", headers=actor_a.headers
    ).json()
    assert current["status"] == "cancelled" and current["answer"] == ""
    assert (
        client.get(f"{BASE}/{conversation_id}", headers=actor_a.headers).json()[
            "summary"
        ]
        == ""
    )

    following_history = []

    def next_success(**kwargs):
        following_history.append(copy.deepcopy(kwargs["history"]))
        return {"answer": "Fresh reply", "sources": [], "summarized_message_count": 0}

    monkeypatch.setattr(assistant, "execute_chat", next_success)
    assert (
        send(client, actor_a, conversation_id, "New attempt")["status"] == "succeeded"
    )
    assert following_history == [[]]


def test_failed_and_cancelled_user_inputs_do_not_enter_later_model_history(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    calls = []

    def execute_chat(**kwargs):
        calls.append(copy.deepcopy(kwargs["history"]))
        if kwargs["message"] == "Fail this":
            raise RuntimeError("Fixture-only failure")
        return {"answer": "Success", "sources": [], "summarized_message_count": 0}

    monkeypatch.setattr(assistant, "execute_chat", execute_chat)
    conversation_id = new_conversation(client, actor_a)
    assert send(client, actor_a, conversation_id, "Fail this")["status"] == "failed"
    assert send(client, actor_a, conversation_id, "Try again")["status"] == "succeeded"
    assert calls == [[], []]
    assert [
        item["content"] for item in messages(client, actor_a, conversation_id)["data"]
    ] == ["Fail this", "Try again", "Success"]


def test_summary_offset_and_sources_persist_without_repeating_covered_history(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    source = source_for(actor_a)
    captured = []

    def execute_chat(**kwargs):
        captured.append(
            copy.deepcopy(
                {
                    "history": kwargs["history"],
                    "summary": kwargs["summary"],
                    "summary_sources": kwargs.get("summary_sources", []),
                }
            )
        )
        return {
            "answer": f"Answer {len(captured)}",
            "sources": [source],
            "new_summary": "Remembered first exchange" if len(captured) == 2 else None,
            "summarized_message_count": 2 if len(captured) == 2 else 0,
        }

    monkeypatch.setattr(assistant, "execute_chat", execute_chat)
    conversation_id = new_conversation(client, actor_a)
    for question in ["First", "Second", "Third"]:
        assert send(client, actor_a, conversation_id, question)["status"] == "succeeded"
    assert len(captured[1]["history"]) == 2
    assert captured[2]["summary"] == "Remembered first exchange"
    assert captured[2]["summary_sources"] == [source]
    assert [item["content"] for item in captured[2]["history"]] == [
        "Second",
        "Answer 2",
    ]
    with Session(engine) as session:
        conversation = session.get(AssistantConversation, uuid.UUID(conversation_id))
        assert conversation is not None
        assert conversation.summarized_count == 2
        assert conversation.summary == "Remembered first exchange"
        assert conversation.summary_sources == [source]
    assert messages(client, actor_a, conversation_id)["count"] == 6


def test_deleted_private_source_invalidates_summary_and_redacts_model_history(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    source = source_for(actor_a)
    captured = []

    def execute_chat(**kwargs):
        captured.append(
            copy.deepcopy(
                {
                    "history": kwargs["history"],
                    "summary": kwargs["summary"],
                    "summary_sources": kwargs.get("summary_sources", []),
                }
            )
        )
        return {
            "answer": "Sensitive answer",
            "sources": [source] if len(captured) < 3 else [],
            "new_summary": "Sensitive summary" if len(captured) == 2 else None,
            "summarized_message_count": 2 if len(captured) == 2 else 0,
        }

    monkeypatch.setattr(assistant, "execute_chat", execute_chat)
    conversation_id = new_conversation(client, actor_a)
    send(client, actor_a, conversation_id, "One")
    send(client, actor_a, conversation_id, "Two")
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, uuid.UUID(source["document_id"]))
        assert document is not None
        session.delete(document)
        session.commit()
    assert send(client, actor_a, conversation_id, "Three")["status"] == "succeeded"
    assert captured[2]["summary"] == ""
    assert captured[2]["summary_sources"] == []
    old_answers = [
        item for item in captured[2]["history"] if item["role"] == "assistant"
    ]
    assert len(old_answers) == 2
    assert all(
        "unavailable" in item["content"] and not item["sources"] for item in old_answers
    )
    assert "Sensitive answer" not in str(captured[2])


def test_restart_marks_only_active_turns_failed_and_keeps_history(actors):
    actor_a, _ = actors
    turn_ids = {}
    with Session(engine) as session:
        for status in ["queued", "running", "succeeded", "failed", "cancelled"]:
            conversation = AssistantConversation(owner_id=actor_a.id)
            session.add(conversation)
            session.flush()
            turn = AssistantTurn(
                conversation_id=conversation.id,
                status=status,
                answer="Saved" if status == "succeeded" else "",
            )
            session.add(turn)
            session.flush()
            turn_ids[status] = turn.id
        session.commit()
    service.recover_interrupted_turns()
    service.recover_interrupted_turns()
    with Session(engine) as session:
        for before, turn_id in turn_ids.items():
            turn = session.get(AssistantTurn, turn_id)
            assert turn is not None
            events = session.exec(
                select(AssistantEvent).where(AssistantEvent.turn_id == turn_id)
            ).all()
            if before in {"queued", "running"}:
                assert turn.status == "failed" and "restart" in turn.error_message
                assert len(events) == 1 and events[0].event_type == "error"
            else:
                assert turn.status == before and events == []


def test_concurrent_posts_create_exactly_one_active_turn(client, actors, monkeypatch):
    actor_a, _ = actors
    release = threading.Event()
    barrier = threading.Barrier(2)
    monkeypatch.setattr(settings, "RESEARCH_MAX_WORKERS", 3)

    def blocked(**_kwargs):
        assert release.wait(5)
        return {"answer": "Done", "sources": [], "summarized_message_count": 0}

    monkeypatch.setattr(assistant, "execute_chat", blocked)
    conversation_id = new_conversation(client, actor_a)

    def submit(content):
        barrier.wait(timeout=5)
        return client.post(
            f"{BASE}/{conversation_id}/messages",
            headers=actor_a.headers,
            json={"content": content},
        )

    responses = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, content) for content in ("First", "Second")]
            responses = [future.result(timeout=5) for future in futures]
        assert sorted(response.status_code for response in responses) == [202, 409]
        with Session(engine) as session:
            turns = session.exec(
                select(AssistantTurn).where(
                    AssistantTurn.conversation_id == uuid.UUID(conversation_id)
                )
            ).all()
            assert len(turns) == 1 and turns[0].status in {"queued", "running"}
        assert messages(client, actor_a, conversation_id)["count"] == 1
    finally:
        release.set()
        for response in responses:
            if response.status_code == 202:
                wait_turn(response.json()["id"])


@pytest.mark.parametrize("bad_count", [-1, 1])
def test_invalid_summary_boundary_fails_without_message_or_summary_commit(
    client, actors, monkeypatch, bad_count
):
    actor_a, _ = actors
    monkeypatch.setattr(
        assistant,
        "execute_chat",
        lambda **_kwargs: {
            "answer": "Must not save",
            "sources": [],
            "new_summary": "Must not save",
            "summarized_message_count": bad_count,
        },
    )
    conversation_id = new_conversation(client, actor_a)
    turn = send(client, actor_a, conversation_id, "First has no old history")
    assert turn["status"] == "failed"
    assert turn["answer"] == ""
    assert [
        item["role"] for item in messages(client, actor_a, conversation_id)["data"]
    ] == ["user"]
    with Session(engine) as session:
        conversation = session.get(AssistantConversation, uuid.UUID(conversation_id))
        assert conversation is not None
        assert conversation.summary == "" and conversation.summarized_count == 0


def test_deleting_selected_knowledge_base_removes_scope_and_allows_plain_chat(
    client, actors, monkeypatch
):
    from app.api.routes import knowledge_bases

    actor_a, actor_b = actors
    conversation_a = new_conversation(
        client,
        actor_a,
        knowledge_base_ids=[str(actor_a.knowledge_base_id)],
        use_web=False,
    )
    conversation_b = new_conversation(
        client,
        actor_b,
        knowledge_base_ids=[str(actor_b.knowledge_base_id)],
        use_web=False,
    )
    deleted_vector_scopes = []
    monkeypatch.setattr(
        knowledge_bases,
        "delete_knowledge_base_vectors",
        lambda knowledge_base_id: deleted_vector_scopes.append(knowledge_base_id),
    )
    unauthorized = client.delete(
        f"/api/v1/knowledge-bases/{actor_a.knowledge_base_id}",
        headers=actor_b.headers,
    )
    assert unauthorized.status_code == 404
    assert deleted_vector_scopes == []
    deleted = client.delete(
        f"/api/v1/knowledge-bases/{actor_a.knowledge_base_id}",
        headers=actor_a.headers,
    )
    assert deleted.status_code == 200
    assert deleted_vector_scopes == [actor_a.knowledge_base_id]
    assert (
        client.get(f"{BASE}/{conversation_a}", headers=actor_a.headers).json()[
            "knowledge_base_ids"
        ]
        == []
    )
    assert client.get(f"{BASE}/{conversation_b}", headers=actor_b.headers).json()[
        "knowledge_base_ids"
    ] == [str(actor_b.knowledge_base_id)]
    seen_scope = []

    def plain_chat(**kwargs):
        seen_scope.append(kwargs["knowledge_base_ids"])
        return {
            "answer": "Hello after deleting the knowledge base",
            "sources": [],
            "summarized_message_count": 0,
        }

    monkeypatch.setattr(assistant, "execute_chat", plain_chat)
    turn = send(client, actor_a, conversation_a, "Hello")
    assert turn["status"] == "succeeded"
    assert seen_scope == [[]]
