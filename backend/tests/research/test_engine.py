"""Isolated research regressions: fake model, search, and KB service only.

Run with ``pytest --confcutdir=tests/research tests/research`` to avoid the
template's database-owning root conftest.
"""

import importlib
from contextlib import AbstractContextManager
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def engine_modules(monkeypatch):
    for key, value in {
        "SECRET_KEY": "test-only-secret-00000000000000000000",
        "PROJECT_NAME": "research engine tests",
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "FIRST_SUPERUSER": "test@example.com",
        "FIRST_SUPERUSER_PASSWORD": "test-password-only",
    }.items():
        monkeypatch.setenv(key, value)
    modules = {
        name: importlib.import_module("app.research." + name)
        for name in (
            "engine",
            "evidence",
            "events",
            "errors",
            "config",
            "cache",
            "agents.planner",
            "agents.researcher",
            "agents.refiner",
            "agents.analyst",
            "agents.writer",
        )
    }
    return modules


class FakeStream(AbstractContextManager):
    def __init__(self, fragments):
        self.fragments = fragments

    def __iter__(self):
        for text in self.fragments:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
            )

    def __exit__(self, *_args):
        return False


def fake_llm(monkeypatch, modules):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            if "research analyst" in kwargs["messages"][0]["content"]:
                return FakeStream(["Supported ", "analysis [kb-secret]"])
            return FakeStream(["Report ", "[kb-secret]"])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='["public storage comparison"]')
                )
            ]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    for name in ("planner", "refiner", "analyst", "writer"):
        monkeypatch.setattr(modules["agents." + name], "get_client", lambda: client)
    return calls


def knowledge_source(modules):
    return modules["evidence"].Evidence(
        source_id="kb-secret",
        kind="knowledge",
        content="PRIVATE_BODY_MARKER: internal latency is 12 ms",
        title="Internal benchmark",
        url=None,
        document_id=str(uuid4()),
        chunk_index=0,
    )


def test_knowledge_only_skips_public_graph_and_streams_both_outputs(
    monkeypatch, engine_modules
):
    modules = engine_modules
    owner, kb = uuid4(), uuid4()
    source = knowledge_source(modules)
    retrievals = []

    def retrieve(**kwargs):
        retrievals.append(kwargs)
        return [source]

    monkeypatch.setattr(modules["engine"], "retrieve_evidence", retrieve)
    monkeypatch.setattr(
        modules["engine"].compiled_graph,
        "stream",
        lambda *_args, **_kwargs: pytest.fail(
            "knowledge-only must not run public graph"
        ),
    )
    calls = fake_llm(monkeypatch, modules)
    events = []
    result = modules["engine"].execute_research(
        "Compare storage",
        owner,
        [kb, kb],
        lambda kind, data: events.append((kind, data)),
        use_web=False,
    )

    assert retrievals == [
        {
            "user_id": owner,
            "knowledge_base_ids": [kb],
            "query": "Compare storage",
            "k": 4,
        }
    ]
    assert len(calls) == 2
    assert all(
        "PRIVATE_BODY_MARKER" in call["messages"][1]["content"] for call in calls
    )
    assert result["sources"] == [source.model_dump(mode="json")]
    assert result["final_report"] == "Report [kb-secret]"
    assert {kind for kind, _ in events} == {
        "agent_update",
        "analysis_token",
        "report_token",
    }
    assert result["sub_tasks"] == []


def test_private_evidence_is_not_sent_to_planner_or_search(monkeypatch, engine_modules):
    modules = engine_modules
    monkeypatch.setattr(
        modules["engine"],
        "retrieve_evidence",
        lambda **_kwargs: [knowledge_source(modules)],
    )
    searches = []

    def search(query):
        searches.append(query)
        return [
            {
                "title": "Public benchmark",
                "url": "https://example.com/benchmark",
                "content": "Public result",
            }
        ]

    monkeypatch.setattr(modules["agents.researcher"], "search_web", search)
    calls = fake_llm(monkeypatch, modules)
    result = modules["engine"].execute_research(
        "Compare storage", uuid4(), [uuid4()], lambda *_args: None
    )
    assert searches == ["public storage comparison"]
    assert "PRIVATE_BODY_MARKER" not in str(calls[0])
    assert [source["kind"] for source in result["sources"]] == ["knowledge", "web"]
    assert "PRIVATE_BODY_MARKER" in str(calls[1])
    assert "PRIVATE_BODY_MARKER" in str(calls[2])


def test_permission_failure_precedes_model_and_search(monkeypatch, engine_modules):
    modules = engine_modules

    def denied(**_kwargs):
        raise PermissionError("not allowed")

    monkeypatch.setattr(modules["engine"], "retrieve_evidence", denied)
    calls = fake_llm(monkeypatch, modules)
    with pytest.raises(PermissionError):
        modules["engine"].execute_research(
            "query", uuid4(), [uuid4()], lambda *_args: None
        )
    assert calls == []


def test_empty_evidence_fails_without_model_call(monkeypatch, engine_modules):
    modules = engine_modules
    calls = fake_llm(monkeypatch, modules)
    with pytest.raises(
        modules["errors"].ResearchExecutionError, match="No usable evidence"
    ):
        modules["engine"].execute_research(
            "query", uuid4(), [], lambda *_args: None, use_web=False
        )
    assert calls == []


def test_cancellation_during_analysis_closes_stream_and_restores_emitter(
    monkeypatch, engine_modules
):
    modules = engine_modules
    monkeypatch.setattr(
        modules["engine"],
        "retrieve_evidence",
        lambda **_kwargs: [knowledge_source(modules)],
    )
    fake_llm(monkeypatch, modules)
    events = []
    cancelled = False

    def emit(kind, data):
        nonlocal cancelled
        events.append((kind, data))
        if kind == "analysis_token":
            cancelled = True

    with pytest.raises(modules["errors"].ResearchCancelled):
        modules["engine"].execute_research(
            "query",
            uuid4(),
            [uuid4()],
            emit,
            use_web=False,
            should_cancel=lambda: cancelled,
        )
    assert len([event for event in events if event[0] == "analysis_token"]) == 1
    assert not any(event[0] == "report_token" for event in events)
    before = len(events)
    modules["events"].emit("analysis_token", {"text": "outside run"})
    assert len(events) == before


def test_untrusted_source_deduplication_and_cache_keys(engine_modules):
    modules = engine_modules
    sources = modules["evidence"].collect_sources(
        {
            "q": [
                {"title": "page", "url": "https://example.com", "content": "one"},
                {"title": "page again", "url": "https://example.com", "content": "two"},
                {"title": "bad", "url": "javascript:alert(1)", "content": "bad"},
            ]
        }
    )
    assert len(sources) == 1
    assert sources[0]["source_id"].startswith("web-")
    assert "private query text" not in modules["cache"]._key("private query text", 3)


def test_model_failure_does_not_expose_provider_exception(monkeypatch, engine_modules):
    modules = engine_modules
    monkeypatch.setattr(
        modules["engine"],
        "retrieve_evidence",
        lambda **_kwargs: [knowledge_source(modules)],
    )

    def broken_client():
        raise RuntimeError("secret-provider-token-and-sensitive-body")

    monkeypatch.setattr(modules["agents.analyst"], "get_client", broken_client)
    with pytest.raises(modules["errors"].ResearchExecutionError) as failure:
        modules["engine"].execute_research(
            "query", uuid4(), [uuid4()], lambda *_args: None, use_web=False
        )
    assert "secret-provider" not in str(failure.value)


def test_planner_rejects_unbounded_search_fanout(engine_modules):
    parse = engine_modules["agents.planner"].parse_task_list
    with pytest.raises(ValueError):
        parse('["a", "b", "c", "d", "e", "f"]')
    assert parse('```json\n[" a ", "a"]\n```') == ["a"]


def test_web_only_keeps_public_research_without_kb_calls(monkeypatch, engine_modules):
    modules = engine_modules
    monkeypatch.setattr(
        modules["engine"],
        "retrieve_evidence",
        lambda **_kwargs: pytest.fail("no KB selected; retrieval must not be called"),
    )
    monkeypatch.setattr(
        modules["agents.researcher"],
        "search_web",
        lambda _query: [
            {
                "title": "Public source",
                "url": "https://example.com",
                "content": "evidence",
            }
        ],
    )
    calls = fake_llm(monkeypatch, modules)
    result = modules["engine"].execute_research(
        "question", uuid4(), [], lambda *_args: None
    )
    assert len(calls) == 3
    assert all(item["kind"] == "web" for item in result["sources"])


def test_search_outage_can_use_private_evidence_with_bounded_refinement(
    monkeypatch, engine_modules
):
    modules = engine_modules
    monkeypatch.setattr(
        modules["engine"],
        "retrieve_evidence",
        lambda **_kwargs: [knowledge_source(modules)],
    )
    searches = []

    def outage(query):
        searches.append(query)
        raise RuntimeError("private provider details")

    monkeypatch.setattr(modules["agents.researcher"], "search_web", outage)
    calls = fake_llm(monkeypatch, modules)
    result = modules["engine"].execute_research(
        "question", uuid4(), [uuid4()], lambda *_args: None
    )
    assert len(searches) == 2
    assert len(calls) == 4  # Planner, Refiner, Analyst, Writer.
    assert "PRIVATE_BODY_MARKER" not in str(calls[1])
    assert "private provider details" not in str(result["logs"])
    assert all(item["kind"] == "knowledge" for item in result["sources"])


def test_total_web_failure_without_private_evidence_is_terminal(
    monkeypatch, engine_modules
):
    modules = engine_modules

    def outage(_query):
        raise RuntimeError("secret provider key")

    monkeypatch.setattr(modules["agents.researcher"], "search_web", outage)
    calls = fake_llm(monkeypatch, modules)
    with pytest.raises(
        modules["errors"].ResearchExecutionError, match="Web search is unavailable"
    ):
        modules["engine"].execute_research("question", uuid4(), [], lambda *_args: None)
    assert len(calls) == 1
