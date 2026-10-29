import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import assistant
from app.core.db import engine
from app.core.security import create_access_token
from app.models import ExecutionJob, User
from app.runtime.context import execution_context, provider_call
from app.runtime.gateway import ModelGateway
from app.runtime.models import RuntimeCall
from app.services import durable_jobs as jobs

BASE = "/api/v1/assistant/conversations"


def submit(client, actor):
    conversation = client.post(BASE, headers=actor.headers, json={}).json()["id"]
    response = client.post(
        f"{BASE}/{conversation}/messages",
        headers={**actor.headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"content": "private prompt"},
    )
    assert response.status_code == 202
    target = uuid.UUID(response.json()["id"])
    claim = jobs.claim_next("runtime-fixture")
    assert claim is not None and claim.target_id == target
    return target, claim


def test_real_gateway_ledger_api_ownership_and_request_correlation(
    client, actor, monkeypatch
):
    dispatched = []

    def raw_create(**kwargs):
        dispatched.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=6)
        )

    gateway = ModelGateway(
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=raw_create))
        )
    )

    def execute(**kwargs):
        with provider_call("tool", operation="fixture_delegate") as tool:
            gateway.chat.completions.create(
                model="fixture",
                messages=[{"role": "user", "content": kwargs["message"]}],
                max_tokens=15,
            )
            tool.succeed()
        return {"answer": "private model answer", "sources": []}

    monkeypatch.setattr(assistant, "execute_chat", execute)
    target, claim = submit(client, actor)
    jobs.execute_claim(claim)
    url = f"/api/v1/integrations/turns/{target}/execution"
    response = client.get(
        url, headers={**actor.headers, "X-Request-ID": "ledger-check"}
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "ledger-check"
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["usage"]["call_counts"] == {"model": 1, "tool": 1}
    assert result["usage"]["known_input_tokens"] == 12
    assert result["usage"]["known_output_tokens"] == 6
    assert result["usage"]["usage_complete"] is True
    assert result["usage"]["estimated_cost"] is None
    assert len(dispatched) == 1 and len(result["calls"]) == 2
    child = next(call for call in result["calls"] if call["kind"] == "model")
    parent = next(call for call in result["calls"] if call["kind"] == "tool")
    assert child["parent_id"] == parent["id"]
    assert (
        "private prompt" not in response.text
        and "private model answer" not in response.text
    )
    with Session(engine) as session:
        stranger = User(
            email=f"other-runtime-{uuid.uuid4().hex}@example.com",
            hashed_password="fixture",
        )
        session.add(stranger)
        session.commit()
        session.refresh(stranger)
        identity = stranger.id
    try:
        token = create_access_token(identity, timedelta(minutes=2))
        assert (
            client.get(url, headers={"Authorization": f"Bearer {token}"}).status_code
            == 404
        )
        assert client.get(url).status_code in {401, 403}
    finally:
        with Session(engine) as session:
            session.delete(session.get(User, identity))
            session.commit()


def test_stale_lease_blocks_provider_before_dispatch_and_started_becomes_unknown(
    client, actor
):
    target, claim = submit(client, actor)
    with Session(engine) as session:
        job = session.get(ExecutionJob, claim.job_id)
        deadline = job.deadline_at
    with (
        jobs.execution_context(claim),
        execution_context(
            claim.job_id, claim.owner_id, lease_token=claim.token, deadline_at=deadline
        ),
    ):
        pending = provider_call(
            "model", operation="fixture-inflight", max_output_tokens=12
        )
        pending.__enter__()
        # Simulate SIGKILL after the persisted request intent: no completion call.
        with Session(engine) as session:
            job = session.get(ExecutionJob, claim.job_id)
            job.lease_expires_at = jobs.database_now(session) - timedelta(seconds=2)
            session.add(job)
            session.commit()
        assert jobs.expire_abandoned() >= 1
        gateway = ModelGateway(
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kwargs: pytest.fail("stale lease dispatched")
                    )
                )
            )
        )
        with pytest.raises(jobs.LeaseLost):
            gateway.chat.completions.create(max_tokens=1)
        # Reset the local span without modifying the original call's persisted intent.
        try:
            pending.__exit__(None, None, None)
        except jobs.LeaseLost:
            pass
    response = client.get(
        f"/api/v1/integrations/turns/{target}/execution", headers=actor.headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert response.json()["calls"][0]["status"] == "unknown"
    assert response.json()["usage"]["usage_complete"] is False
    with Session(engine) as session:
        stored = session.exec(
            select(RuntimeCall).where(RuntimeCall.job_id == claim.job_id)
        ).one()
        assert stored.status == "started"


def test_readonly_integration_rejects_cross_tenant_before_embedding(
    client, actor, monkeypatch
):
    from app.models import KnowledgeBase
    from app.services import knowledge_retrieval

    with Session(engine) as session:
        stranger = User(
            email=f"kb-other-{uuid.uuid4().hex}@example.com", hashed_password="fixture"
        )
        session.add(stranger)
        session.flush()
        base = KnowledgeBase(name="private", owner_id=stranger.id)
        session.add(base)
        session.commit()
        session.refresh(base)
        base_id, identity = base.id, stranger.id
    monkeypatch.setattr(
        knowledge_retrieval,
        "create_retriever",
        lambda *args, **kwargs: pytest.fail("unauthorized provider"),
    )
    try:
        response = client.post(
            "/api/v1/integrations/search-evidence",
            headers=actor.headers,
            json={"knowledge_base_ids": [str(base_id)], "query": "secret"},
        )
        assert response.status_code == 404
        assert (
            client.get("/api/v1/integrations/skills", headers=actor.headers).status_code
            == 200
        )
    finally:
        with Session(engine) as session:
            session.delete(session.get(User, identity))
            session.commit()
