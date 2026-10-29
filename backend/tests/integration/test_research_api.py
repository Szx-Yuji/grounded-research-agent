"""Real PostgreSQL, JWT routes, worker lifecycle and SSE; external engine faked."""

import json
import threading
import time
import uuid
from collections.abc import Callable

import pytest
from sqlmodel import Session, select

import app.research
from app.core.db import engine
from app.models import ResearchEvent, ResearchRun
from app.services import research_runs

BASE = "/api/v1/research/runs"


def wait_until(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Research worker did not reach the expected state")


def worker_done(run_id: uuid.UUID) -> bool:
    with research_runs._lock:
        return run_id not in research_runs._active


def stored_run(run_id: uuid.UUID) -> ResearchRun:
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None
        return run


def seed_run(owner_id: uuid.UUID, status: str = "succeeded") -> uuid.UUID:
    with Session(engine) as session:
        run = ResearchRun(owner_id=owner_id, query="Fixture question", status=status)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def parse_sse(text: str) -> list[dict]:
    result = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if ":" in line and not line.startswith(":"):
                key, value = line.split(":", 1)
                fields[key] = value.lstrip()
        if "data" in fields:
            result.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return result


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("POST", "", {"query": "Question", "use_web": True}),
        ("GET", "/{id}", None),
        ("GET", "/{id}/events", None),
        ("POST", "/{id}/cancel", None),
        ("DELETE", "/{id}", None),
    ],
)
def test_research_routes_require_authentication(client, method, suffix, body):
    path = BASE + suffix.format(id=uuid.uuid4())
    response = client.request(method, path, json=body)
    assert response.status_code in {401, 403}


def test_invalid_token_is_rejected(client):
    response = client.get(BASE, headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code in {401, 403}


def test_other_users_knowledge_and_run_are_inaccessible(client, actors):
    actor_a, actor_b = actors
    run_b = seed_run(actor_b.id)
    response = client.post(
        BASE,
        headers=actor_a.headers,
        json={
            "query": "Read another user's private document",
            "knowledge_base_ids": [str(actor_b.knowledge_base_id)],
            "use_web": False,
        },
    )
    assert response.status_code == 404
    listed = client.get(BASE, headers=actor_a.headers)
    assert listed.status_code == 200
    assert str(run_b) not in [run["id"] for run in listed.json()["data"]]
    assert listed.json()["count"] == 0
    for method, suffix in [
        ("GET", ""),
        ("GET", "/events"),
        ("POST", "/cancel"),
        ("DELETE", ""),
    ]:
        denied = client.request(
            method, f"{BASE}/{run_b}{suffix}", headers=actor_a.headers
        )
        assert denied.status_code == 404
    assert stored_run(run_b).status == "succeeded"


def test_mixed_run_persists_owner_sources_and_resumable_events(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    captured = {}
    sources = [
        {
            "source_id": "web-1",
            "kind": "web",
            "content": "Public evidence",
            "title": "A page",
            "url": "https://example.com/evidence",
        },
        {
            "source_id": "kb-1",
            "kind": "knowledge",
            "content": "Private evidence",
            "title": "Policy",
            "document_id": str(uuid.uuid4()),
            "chunk_index": 0,
        },
    ]

    def fake_execution(
        query, user_id, knowledge_base_ids, emit, *, use_web, should_cancel
    ):
        captured.update(
            query=query,
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            use_web=use_web,
        )
        assert not should_cancel()
        emit("agent_update", {"agent": "Planner", "action": "Planned"})
        emit("analysis_token", {"text": "Analysis "})
        emit("report_token", {"text": "Report [1][2]"})
        return {
            "final_report": "Report [1][2]",
            "sub_tasks": ["Question"],
            "sources": sources,
        }

    monkeypatch.setattr(app.research, "execute_research", fake_execution)
    created = client.post(
        BASE,
        headers=actor_a.headers,
        json={
            "query": "Mixed research",
            "knowledge_base_ids": [str(actor_a.knowledge_base_id)],
            "use_web": True,
        },
    )
    assert created.status_code == 202
    run_id = uuid.UUID(created.json()["id"])
    wait_until(lambda: worker_done(run_id))
    run = stored_run(run_id)
    assert run.owner_id == actor_a.id
    assert run.status == "succeeded"
    assert run.sources == sources
    assert run.final_report == "Report [1][2]"
    assert captured == {
        "query": "Mixed research",
        "user_id": actor_a.id,
        "knowledge_base_ids": [actor_a.knowledge_base_id],
        "use_web": True,
    }

    streamed = client.get(f"{BASE}/{run_id}/events", headers=actor_a.headers)
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(streamed.text)
    assert [event["event"] for event in events] == [
        "agent_update",
        "analysis_token",
        "report_token",
        "complete",
    ]
    assert [event["id"] for event in events] == sorted(
        {event["id"] for event in events}
    )
    assert events[-1]["data"]["sources"] == sources
    cursor = events[1]["id"]
    resumed = parse_sse(
        client.get(
            f"{BASE}/{run_id}/events?after={cursor}", headers=actor_a.headers
        ).text
    )
    assert resumed == events[2:]
    header_resume = parse_sse(
        client.get(
            f"{BASE}/{run_id}/events?after=0",
            headers={**actor_a.headers, "Last-Event-ID": str(cursor)},
        ).text
    )
    assert header_resume == events[2:]

    with Session(engine) as session:
        stored_events = session.exec(
            select(ResearchEvent)
            .where(ResearchEvent.run_id == run_id)
            .order_by(ResearchEvent.id)
        ).all()
        assert [event.id for event in stored_events] == [
            event["id"] for event in events
        ]
    listed = client.get(BASE, headers=actor_a.headers).json()
    assert [item["id"] for item in listed["data"]] == [str(run_id)]
    assert listed["count"] == 1
    assert client.delete(f"{BASE}/{run_id}", headers=actor_a.headers).status_code == 204
    with Session(engine) as session:
        assert session.get(ResearchRun, run_id) is None
        assert (
            session.exec(
                select(ResearchEvent).where(ResearchEvent.run_id == run_id)
            ).first()
            is None
        )


def test_cancelled_run_survives_late_worker_completion_and_running_delete_is_rejected(
    client, actors, monkeypatch
):
    actor_a, _ = actors
    entered, release = threading.Event(), threading.Event()

    def delayed_execution(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return {"final_report": "Too late", "sources": [], "sub_tasks": []}

    monkeypatch.setattr(app.research, "execute_research", delayed_execution)
    created = client.post(
        BASE, headers=actor_a.headers, json={"query": "Long question", "use_web": True}
    )
    assert created.status_code == 202
    run_id = uuid.UUID(created.json()["id"])
    try:
        assert entered.wait(5)
        assert stored_run(run_id).status == "running"
        assert (
            client.delete(f"{BASE}/{run_id}", headers=actor_a.headers).status_code
            == 409
        )
        cancelled = client.post(f"{BASE}/{run_id}/cancel", headers=actor_a.headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
    finally:
        release.set()
        wait_until(lambda: worker_done(run_id))
    run = stored_run(run_id)
    assert run.status == "cancelled"
    assert run.final_report == ""
    events = parse_sse(
        client.get(f"{BASE}/{run_id}/events", headers=actor_a.headers).text
    )
    assert [event["event"] for event in events] == ["cancelled"]


def test_restart_recovery_only_marks_unfinished_runs_failed(actors):
    actor_a, _ = actors
    runs = {
        status: seed_run(actor_a.id, status)
        for status in ["queued", "running", "succeeded", "cancelled", "failed"]
    }
    research_runs.recover_interrupted_runs()
    research_runs.recover_interrupted_runs()  # Repeated startup must not duplicate errors.
    for previous, run_id in runs.items():
        run = stored_run(run_id)
        with Session(engine) as session:
            events = session.exec(
                select(ResearchEvent).where(ResearchEvent.run_id == run_id)
            ).all()
        if previous in {"queued", "running"}:
            assert run.status == "failed"
            assert run.error_message == research_runs.RESTART_MESSAGE
            assert len(events) == 1
            assert events[0].event_type == "error"
        else:
            assert run.status == previous
            assert events == []


def test_missing_model_configuration_does_not_create_run(client, actors, monkeypatch):
    from app.core.config import settings

    actor_a, _ = actors
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", None)
    response = client.post(
        BASE, headers=actor_a.headers, json={"query": "No model key", "use_web": True}
    )
    assert response.status_code == 503
    assert client.get(BASE, headers=actor_a.headers).json()["count"] == 0


def test_no_source_selected_is_rejected(client, actors):
    actor_a, _ = actors
    response = client.post(
        BASE,
        headers=actor_a.headers,
        json={"query": "No sources", "use_web": False, "knowledge_base_ids": []},
    )
    assert response.status_code == 422
