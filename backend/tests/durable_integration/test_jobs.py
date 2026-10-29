import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import assistant, research
from app.core.db import engine
from app.main import app
from app.models import (
    AssistantEvent,
    AssistantMessage,
    AssistantTurn,
    ExecutionJob,
    ResearchRun,
)
from app.services import assistant_conversations as chats
from app.services import durable_jobs as jobs

BASE = "/api/v1/assistant/conversations"


def conversation(client, actor):
    response = client.post(BASE, headers=actor.headers, json={})
    assert response.status_code == 201
    return response.json()["id"]


def submit(client, actor, conversation_id, key="intent-1", content="Hello"):
    return client.post(
        f"{BASE}/{conversation_id}/messages",
        headers={**actor.headers, "Idempotency-Key": key},
        json={"content": content},
    )


def job_for(target):
    with Session(engine) as session:
        return session.exec(
            select(ExecutionJob).where(ExecutionJob.target_id == uuid.UUID(str(target)))
        ).one()


def own_claim(target, worker="fixture-worker"):
    # Restrict tests to their own intent even if another suite has queued fixtures.
    with Session(engine) as session:
        target_job = session.exec(
            select(ExecutionJob).where(ExecutionJob.target_id == uuid.UUID(str(target)))
        ).one()
        other_ids = session.exec(
            select(ExecutionJob.id).where(
                ExecutionJob.status == "queued", ExecutionJob.id != target_job.id
            )
        ).all()
        for identity in other_ids:
            session.exec(
                select(ExecutionJob)
                .where(ExecutionJob.id == identity)
                .with_for_update()
            )
        result = jobs.claim_next(worker)
    assert result is not None and result.target_id == uuid.UUID(str(target))
    return result


def test_api_enqueues_atomically_without_execution_and_survives_api_restart(
    client, actor
):
    cid = conversation(client, actor)
    result = submit(client, actor, cid)
    assert result.status_code == 202
    target = uuid.UUID(result.json()["id"])
    assert result.json()["status"] == "queued"
    assert job_for(target).status == "queued"
    with TestClient(app) as second_api:
        read = second_api.get(f"{BASE}/{cid}/turns/{target}", headers=actor.headers)
        assert read.json()["status"] == "queued"
    with Session(engine) as session:
        assert (
            len(
                session.exec(
                    select(AssistantMessage).where(AssistantMessage.turn_id == target)
                ).all()
            )
            == 1
        )
        assert (
            session.exec(
                select(AssistantEvent).where(AssistantEvent.turn_id == target)
            ).all()
            == []
        )


def test_idempotent_intent_replay_and_payload_mismatch(client, actor, monkeypatch):
    cid = conversation(client, actor)
    first = submit(client, actor, cid)
    again = submit(client, actor, cid)
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert submit(client, actor, cid, content="different").status_code == 409
    calls = []
    monkeypatch.setattr(
        assistant,
        "execute_chat",
        lambda **_kwargs: calls.append("model") or {"answer": "Hello", "sources": []},
    )
    claim = own_claim(first.json()["id"])
    jobs.execute_claim(claim)
    final = submit(client, actor, cid)
    assert final.status_code == 202 and final.json()["status"] == "succeeded"
    assert final.json()["id"] == first.json()["id"] and calls == ["model"]


def test_concurrent_idempotency_creates_one_intent_and_message(client, actor):
    cid = conversation(client, actor)
    barrier = threading.Barrier(2)

    def post():
        barrier.wait(timeout=5)
        return submit(client, actor, cid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = [
            future.result(timeout=10)
            for future in [pool.submit(post), pool.submit(post)]
        ]
    assert [value.status_code for value in values] == [202, 202]
    assert len({value.json()["id"] for value in values}) == 1
    with Session(engine) as session:
        assert (
            len(
                session.exec(
                    select(AssistantMessage).where(
                        AssistantMessage.conversation_id == uuid.UUID(cid)
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(ExecutionJob).where(ExecutionJob.owner_id == actor.id)
                ).all()
            )
            == 1
        )


def test_skip_locked_claim_does_not_wait_for_another_worker(client, actor):
    first = submit(client, actor, conversation(client, actor)).json()["id"]
    second = submit(client, actor, conversation(client, actor), key="intent-2").json()[
        "id"
    ]
    with Session(engine) as held:
        held.exec(
            select(ExecutionJob)
            .where(ExecutionJob.target_id == uuid.UUID(first))
            .with_for_update()
        ).one()
        claim = jobs.claim_next("worker-two", ["assistant"])
        assert claim is not None and claim.target_id == uuid.UUID(second)
    assert job_for(first).status == "queued"
    assert job_for(second).attempt_count == 1


def test_fenced_execution_persists_answer_and_final_event_once(
    client, actor, monkeypatch
):
    cid = conversation(client, actor)
    target = submit(client, actor, cid).json()["id"]
    observed = []

    def execute(**kwargs):
        from app.runtime.context import current_context

        observed.append(current_context().job_id)
        kwargs["emit"]("token", {"content": "Final answer"})
        return {"answer": "Final answer", "sources": [], "delegate_notes": []}

    monkeypatch.setattr(assistant, "execute_chat", execute)
    claim = own_claim(target)
    jobs.execute_claim(claim)
    assert observed == [claim.job_id]
    assert job_for(target).status == "succeeded"
    with Session(engine) as session:
        assert session.get(AssistantTurn, uuid.UUID(target)).status == "succeeded"
        assert (
            len(
                session.exec(
                    select(AssistantMessage).where(
                        AssistantMessage.turn_id == uuid.UUID(target),
                        AssistantMessage.role == "assistant",
                    )
                ).all()
            )
            == 1
        )
        assert [
            event.event_type
            for event in session.exec(
                select(AssistantEvent)
                .where(AssistantEvent.turn_id == uuid.UUID(target))
                .order_by(AssistantEvent.id)
            )
        ] == ["token", "complete"]
    with jobs.execution_context(claim):
        chats._finish(uuid.UUID(target), "succeeded", result={"answer": "duplicate"})
    assert (
        client.get(f"{BASE}/{cid}/turns/{target}", headers=actor.headers).json()[
            "answer"
        ]
        == "Final answer"
    )


def test_expired_running_attempt_is_unknown_and_never_requeued(client, actor):
    target = submit(client, actor, conversation(client, actor)).json()["id"]
    claim = own_claim(target)
    with Session(engine) as session:
        job = session.get(ExecutionJob, claim.job_id)
        job.lease_expires_at = jobs.database_now(session) - timedelta(seconds=1)
        session.add(job)
        session.commit()
    jobs.expire_abandoned()
    jobs.expire_abandoned()
    job = job_for(target)
    assert job.status == "interrupted" and "automatically replayed" in job.error_message
    with Session(engine) as session:
        assert session.get(AssistantTurn, uuid.UUID(target)).status == "failed"
        assert (
            len(
                session.exec(
                    select(AssistantEvent).where(
                        AssistantEvent.turn_id == uuid.UUID(target)
                    )
                ).all()
            )
            == 1
        )
    with jobs.execution_context(claim), Session(engine) as session:
        with pytest.raises(jobs.LeaseLost):
            jobs.assert_lease(session)
    assert job.attempt_count == 1


def test_cancelled_queue_is_not_claimed_and_new_message_is_allowed(client, actor):
    cid = conversation(client, actor)
    first = submit(client, actor, cid).json()["id"]
    cancelled = client.post(f"{BASE}/{cid}/turns/{first}/cancel", headers=actor.headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert job_for(first).status == "cancelled"
    second = submit(client, actor, cid, key="new-intent")
    assert second.status_code == 202 and second.json()["id"] != first


def test_cross_process_cancel_fences_late_results(client, actor):
    cid = conversation(client, actor)
    target = submit(client, actor, cid).json()["id"]
    claim = own_claim(target)
    cancelled = client.post(
        f"{BASE}/{cid}/turns/{target}/cancel", headers=actor.headers
    )
    assert cancelled.json()["status"] == "cancelled"
    assert jobs.should_stop(claim)
    with jobs.execution_context(claim):
        chats._finish(uuid.UUID(target), "succeeded", result={"answer": "Late answer"})
    assert job_for(target).status == "cancelled"
    with Session(engine) as session:
        assert (
            session.exec(
                select(AssistantMessage).where(
                    AssistantMessage.turn_id == uuid.UUID(target),
                    AssistantMessage.role == "assistant",
                )
            ).all()
            == []
        )


def test_heartbeat_renews_same_attempt_and_lease_token(client, actor):
    target = submit(client, actor, conversation(client, actor)).json()["id"]
    claim = own_claim(target)
    before = job_for(target).lease_expires_at
    assert jobs.heartbeat(claim)
    after = job_for(target)
    assert after.lease_token == claim.token and after.lease_expires_at >= before
    assert after.attempt_count == 1


def test_research_and_assistant_share_durable_queue(client, actor, monkeypatch):
    response = client.post(
        "/api/v1/research/runs",
        headers={**actor.headers, "Idempotency-Key": "research-intent"},
        json={"query": "Storage", "use_web": True},
    )
    assert response.status_code == 202
    target = response.json()["id"]
    monkeypatch.setattr(
        research,
        "execute_research",
        lambda *_args, **_kwargs: {
            "final_report": "Report",
            "sources": [],
            "sub_tasks": [],
        },
    )
    claim = own_claim(target)
    assert claim.kind == "research"
    jobs.execute_claim(claim)
    with Session(engine) as session:
        assert session.get(ResearchRun, uuid.UUID(target)).status == "succeeded"
    assert job_for(target).status == "succeeded"


def test_real_worker_subprocess_executes_queued_intent(client, actor):
    import os
    import subprocess
    import sys
    from pathlib import Path

    cid = conversation(client, actor)
    target = submit(client, actor, cid).json()["id"]
    program = """
from app import assistant
from app.services import durable_jobs
assistant.execute_chat = lambda **kwargs: {'answer': 'Independent process result', 'sources': []}
claim = durable_jobs.claim_next('subprocess-worker', ['assistant'])
assert claim is not None
assert str(claim.target_id) == EXPECTED_TARGET
durable_jobs.execute_claim(claim)
""".replace("EXPECTED_TARGET", repr(target))
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        "Independent test worker failed (provider calls are mocked)"
    )
    turn = client.get(f"{BASE}/{cid}/turns/{target}", headers=actor.headers).json()
    assert (
        turn["status"] == "succeeded" and turn["answer"] == "Independent process result"
    )


def test_process_crash_after_claim_is_interrupted_without_reexecution(client, actor):
    import os
    import subprocess
    import sys
    from pathlib import Path

    target = submit(client, actor, conversation(client, actor)).json()["id"]
    program = """
import os
from app.services import durable_jobs
claim = durable_jobs.claim_next('crashing-test-worker', ['assistant'])
assert claim is not None
assert str(claim.target_id) == EXPECTED_TARGET
os._exit(17)
""".replace("EXPECTED_TARGET", repr(target))
    child = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 17
    assert job_for(target).status == "running"
    with Session(engine) as session:
        row = session.get(ExecutionJob, job_for(target).id)
        row.lease_expires_at = jobs.database_now(session) - timedelta(seconds=1)
        session.add(row)
        session.commit()
    jobs.expire_abandoned()
    assert job_for(target).status == "interrupted"
    assert jobs.claim_next("replacement-worker", ["assistant"]) is None


def test_deleted_conversation_removes_job_payload_and_call_rows(
    client, actor, monkeypatch
):
    from app.runtime.models import RuntimeCall

    cid = conversation(client, actor)
    target = submit(client, actor, cid).json()["id"]
    claim = own_claim(target)
    monkeypatch.setattr(
        assistant,
        "execute_chat",
        lambda **_kwargs: {"answer": "Completed", "sources": []},
    )
    jobs.execute_claim(claim)
    with Session(engine) as session:
        session.add(
            RuntimeCall(
                job_id=claim.job_id,
                owner_id=actor.id,
                lease_token=claim.token,
                kind="model",
                operation="fixture",
                status="succeeded",
            )
        )
        session.commit()
    assert client.delete(f"{BASE}/{cid}", headers=actor.headers).status_code == 204
    with Session(engine) as session:
        assert session.get(ExecutionJob, claim.job_id) is None
        assert (
            session.exec(
                select(RuntimeCall).where(RuntimeCall.job_id == claim.job_id)
            ).all()
            == []
        )


def test_replaced_chunk_generation_invalidates_notes_and_summary(actor):
    import hashlib

    from app.models import AssistantConversation, KnowledgeBase, KnowledgeDocument
    from app.retrieval_models import KnowledgeChunk
    from app.services.assistant_conversations import load_context

    with Session(engine) as session:
        kb = KnowledgeBase(name="Version test", owner_id=actor.id)
        session.add(kb)
        session.flush()
        document = KnowledgeDocument(
            knowledge_base_id=kb.id,
            original_filename="version.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="ready",
        )
        session.add(document)
        session.flush()
        digest = hashlib.sha256(b"old version").hexdigest()
        source = {
            "source_id": f"kb:{document.id}:0:{digest[:12]}",
            "kind": "knowledge",
            "content": "old version",
            "title": "version.txt",
            "document_id": str(document.id),
            "chunk_index": 0,
            "retrieval": {"index_version": "v1"},
        }
        convo = AssistantConversation(
            owner_id=actor.id,
            summary="Sensitive old version",
            summarized_count=2,
            summary_sources=[source],
        )
        session.add(convo)
        session.flush()
        turn = AssistantTurn(
            conversation_id=convo.id,
            status="succeeded",
            answer="Old result",
            sources=[source],
            delegate_notes=[{"answer": "old delegate secret"}],
        )
        session.add(turn)
        session.flush()
        session.add(
            AssistantMessage(
                conversation_id=convo.id,
                turn_id=turn.id,
                role="user",
                content="Read document",
            )
        )
        session.add(
            AssistantMessage(
                conversation_id=convo.id,
                turn_id=turn.id,
                role="assistant",
                content="old answer secret",
                sources=[source],
            )
        )
        session.add(
            KnowledgeChunk(
                id=uuid.uuid4().hex,
                document_id=document.id,
                knowledge_base_id=kb.id,
                chunk_index=0,
                content="new version",
                content_hash=hashlib.sha256(b"new version").hexdigest(),
                index_version="v2",
                tokenizer_version="test",
                token_count=2,
                term_frequencies={"new": 1},
                source_metadata={},
            )
        )
        session.commit()
        history, summary, covered = load_context(session, convo)
        assert summary == "" and covered == 0
        assert "old answer secret" not in str(
            history
        ) and "old delegate secret" not in str(history)
        assert any("unavailable" in item["content"] for item in history)


def test_document_intent_retry_reuses_row_and_changes_lease_token(actor):
    from app.models import KnowledgeBase, KnowledgeDocument

    with Session(engine) as session:
        kb = KnowledgeBase(name="Document job", owner_id=actor.id)
        session.add(kb)
        session.flush()
        document = KnowledgeDocument(
            knowledge_base_id=kb.id,
            original_filename="test.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="uploaded",
        )
        session.add(document)
        session.flush()
        job = jobs.enqueue_document(session, document)
        identity, doc_id = job.id, document.id
        session.commit()
    first = own_claim(doc_id)
    assert first.kind == "document"
    with jobs.execution_context(first), Session(engine) as session:
        jobs.assert_lease(session)
        document = session.get(KnowledgeDocument, doc_id)
        document.status = "failed"
        session.add(document)
        jobs.finish_in_transaction(session, "failed", "Explicit retry needed")
        session.commit()
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, doc_id)
        retried = jobs.enqueue_document(session, document)
        assert retried.id == identity
        document.status = "uploaded"
        session.add(document)
        session.commit()
    second = own_claim(doc_id)
    assert second.token != first.token
    assert job_for(doc_id).attempt_count == 2
    with jobs.execution_context(first), Session(engine) as session:
        with pytest.raises(jobs.LeaseLost):
            jobs.assert_lease(session)


def test_cancelled_sse_keeps_compatible_terminal_payload(client, actor):
    cid = conversation(client, actor)
    target = submit(client, actor, cid).json()["id"]
    client.post(f"{BASE}/{cid}/turns/{target}/cancel", headers=actor.headers)
    response = client.get(f"{BASE}/{cid}/turns/{target}/events", headers=actor.headers)
    assert response.status_code == 200
    assert "event: cancelled" in response.text and '"turn"' in response.text


def test_lease_naturally_expires_inside_an_existing_transaction(client, actor):
    from sqlalchemy import text

    target = submit(client, actor, conversation(client, actor)).json()["id"]
    claim = own_claim(target)
    with Session(engine) as session:
        row = session.get(ExecutionJob, claim.job_id)
        row.lease_expires_at = jobs.database_now(session) + timedelta(seconds=0.12)
        session.add(row)
        session.commit()
    with jobs.execution_context(claim), Session(engine) as session:
        jobs.assert_lease(session)
        # Keep the same transaction and row lock while the wall clock advances.
        session.execute(text("SELECT pg_sleep(0.2)"))
        with pytest.raises(jobs.LeaseLost):
            jobs.assert_lease(session)
    # The rejection must not depend on a reaper having changed the row status.
    assert job_for(target).status == "running"


def test_embedded_worker_process_guard_rejects_second_and_releases_after_exit():
    from app.worker import embedded_worker_guard, run_worker

    stop = threading.Event()
    stop.set()
    with embedded_worker_guard():
        with pytest.raises(RuntimeError, match="one worker process"):
            run_worker(stop, worker_id="second-process")
    # Guard must release its session lock on normal shutdown.
    run_worker(stop, worker_id="next-process")


def test_embedded_guard_detects_unlock_on_its_original_connection(monkeypatch):
    from contextlib import nullcontext

    from sqlalchemy import text

    from app import worker

    with engine.connect() as connection:
        monkeypatch.setattr(worker.engine, "connect", lambda: nullcontext(connection))
        with worker.embedded_worker_guard() as assert_owned:
            assert_owned()
            connection.execute(
                text("SELECT pg_advisory_unlock(:gate)"),
                {"gate": worker.EMBEDDED_WORKER_LOCK},
            )
            connection.commit()
            with pytest.raises(worker.WorkerGuardLost, match="restart"):
                assert_owned()
            # The guard never transparently reacquires after observing loss.
            with pytest.raises(worker.WorkerGuardLost):
                assert_owned()


def test_worker_exits_on_guard_loss_without_claiming_more_work(monkeypatch):
    from app import worker

    stop = threading.Event()
    checks = []
    claimed = []

    def lost_before_claim():
        checks.append("checked")
        if len(checks) == 2:
            raise worker.WorkerGuardLost("Lost fixture guard")

    monkeypatch.setattr(worker.durable_jobs, "expire_abandoned", lambda: None)
    monkeypatch.setattr(
        worker.durable_jobs,
        "claim_next",
        lambda *_args: claimed.append("unexpected claim"),
    )
    with pytest.raises(worker.WorkerGuardLost):
        worker._run_worker_loop(
            stop, worker_id="guard-loss-fixture", verify_guard=lost_before_claim
        )
    assert stop.is_set() and claimed == []
