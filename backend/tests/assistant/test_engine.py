"""Assistant protocol regressions; all SDK, search and retrieval calls are fakes."""

import copy
import importlib
import json
from contextlib import AbstractContextManager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openai.types.chat import ChatCompletionMessage


@pytest.fixture
def modules(monkeypatch):
    for key, value in {
        "SECRET_KEY": "test-only-secret-00000000000000000000",
        "PROJECT_NAME": "assistant engine tests",
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "FIRST_SUPERUSER": "test@example.com",
        "FIRST_SUPERUSER_PASSWORD": "test-password-only",
    }.items():
        monkeypatch.setenv(key, value)
    return {
        name: importlib.import_module("app.assistant." + name)
        for name in ("engine", "context", "subagents")
    }


class Stream(AbstractContextManager):
    def __init__(self, text):
        self.text = text
        self.closed = False

    def __iter__(self):
        for part in self.text:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=part))]
            )

    def __exit__(self, *_args):
        self.closed = True
        return False


def response(content=None, tools=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=ChatCompletionMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tools,
                )
            )
        ]
    )


def call(name, arguments, identifier="call-1"):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def install_client(
    monkeypatch,
    modules,
    decisions=None,
    final=None,
    knowledge_answer="Sub-agent answer [kb:one:0]",
    summary="Merged summary",
):
    observed = []
    decisions = list(decisions or [response("Ready")])
    final_stream = Stream(final or ["Final ", "answer"])

    def create(**kwargs):
        observed.append(copy.deepcopy(kwargs))
        if kwargs.get("stream"):
            return final_stream
        if kwargs["messages"][0]["content"].startswith(
            "Summarize conversation history"
        ):
            return response(summary)
        if (
            "dedicated knowledge-retrieval sub-agent"
            in kwargs["messages"][0]["content"]
        ):
            return response(knowledge_answer)
        if decisions:
            return decisions.pop(0)
        return response("No tools needed")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    for module in modules.values():
        monkeypatch.setattr(module, "get_client", lambda: client)
    return observed, final_stream


def evidence(modules, kind="knowledge", source_id="kb:one:0"):
    return modules["engine"].Evidence(
        source_id=source_id,
        kind=kind,
        content="PRIVATE_LONG_MARKER_internal_847205 says latency is twelve milliseconds.",
        title="Private latency test",
        document_id=str(uuid4()),
        chunk_index=0,
    )


def run(modules, **kwargs):
    params = {
        "message": "Continue please",
        "user_id": uuid4(),
        "knowledge_base_ids": [],
        "use_web": False,
        "history": [],
        "summary": "",
        "emit": lambda *_args: None,
    }
    params.update(kwargs)
    return modules["engine"].execute_chat(**params)


def test_plain_followup_streams_with_same_conversation_and_prior_sources(
    monkeypatch, modules
):
    previous = evidence(modules).model_dump(mode="json")
    seen, stream = install_client(
        monkeypatch, modules, final=["It refers to ", "the cache [kb:one:0]"]
    )
    history = [
        {"role": "user", "content": "Explain the LRU cache."},
        {
            "role": "assistant",
            "content": "The cache evicts least recently used entries.",
            "sources": [previous],
        },
    ]
    events = []
    result = run(
        modules,
        message="Why does it evict that one?",
        history=history,
        emit=lambda kind, data: events.append((kind, data)),
    )
    assert len(seen) == 1 and seen[0]["stream"] is True
    assert "tools" not in seen[0]
    assert any(
        item.get("content") == history[0]["content"] for item in seen[0]["messages"]
    )
    assert any(
        item.get("content") == "Why does it evict that one?"
        for item in seen[0]["messages"]
    )
    assert result["sources"] == [previous]
    assert [data["content"] for kind, data in events if kind == "token"] == [
        "It refers to ",
        "the cache [kb:one:0]",
    ]
    assert stream.closed


def test_rag_is_independent_subagent_and_observation_precedes_next_decision(
    monkeypatch, modules
):
    owner, kb = uuid4(), uuid4()
    item = evidence(modules)
    retrievals = []

    def retrieve(*args, **kwargs):
        retrievals.append((args, kwargs))
        return [item]

    monkeypatch.setattr(modules["subagents"], "retrieve_evidence", retrieve)
    seen, _ = install_client(
        monkeypatch,
        modules,
        decisions=[
            response(
                tools=[
                    call(
                        "ask_knowledge", '{"question":"What is the measured latency?"}'
                    )
                ]
            ),
            response("I have evidence"),
        ],
        final=["The latency is 12 ms [kb:one:0]"],
    )
    result = run(
        modules,
        user_id=owner,
        knowledge_base_ids=[kb],
        message="Use my benchmark",
        history=[{"role": "user", "content": "PRIVATE_HISTORY_NOT_FOR_SUBAGENT"}],
    )
    assert retrievals[0] == ((owner, [kb], "What is the measured latency?"), {"k": 4})
    child = next(
        item
        for item in seen
        if "dedicated knowledge-retrieval sub-agent" in item["messages"][0]["content"]
    )
    assert len(child["messages"]) == 2
    assert "PRIVATE_HISTORY_NOT_FOR_SUBAGENT" not in str(child)
    assert "PRIVATE_LONG_MARKER" in str(child)
    next_decision = [item for item in seen if item.get("tools")][1]
    tool_result = next(
        item for item in next_decision["messages"] if item["role"] == "tool"
    )
    assert tool_result["tool_call_id"] == "call-1"
    assert "Sub-agent answer" in tool_result["content"]
    assert "PRIVATE_LONG_MARKER" not in tool_result["content"]
    assert result["sources"] == [item.model_dump(mode="json")]


def test_web_disabled_cannot_be_enabled_by_model_tool_call(monkeypatch, modules):
    called = []
    monkeypatch.setattr(
        modules["subagents"], "public_search", lambda question: called.append(question)
    )
    seen, _ = install_client(
        monkeypatch,
        modules,
        decisions=[
            response(tools=[call("search_web", '{"question":"private"}')]),
            response("done"),
        ],
    )
    run(modules, knowledge_base_ids=[uuid4()])
    assert called == []
    assert all(tool["function"]["name"] != "search_web" for tool in seen[0]["tools"])
    result = next(item for item in seen[1]["messages"] if item["role"] == "tool")
    assert "not available" in result["content"]


def test_invalid_json_unknown_tools_and_argument_scope_are_controlled(
    monkeypatch, modules
):
    seen, _ = install_client(
        monkeypatch,
        modules,
        decisions=[
            response(
                tools=[
                    call("ask_knowledge", "{broken", "broken"),
                    call("delete_database", "{}", "unknown"),
                    call(
                        "ask_knowledge",
                        '{"question":"x","user_id":"attacker"}',
                        "scope",
                    ),
                ]
            ),
            response("done"),
        ],
    )
    monkeypatch.setattr(
        modules["subagents"],
        "ask_knowledge",
        lambda *_args: pytest.fail("invalid calls must not run"),
    )
    run(modules, knowledge_base_ids=[uuid4()])
    results = [item for item in seen[1]["messages"] if item["role"] == "tool"]
    assert {item["tool_call_id"] for item in results} == {"broken", "unknown", "scope"}
    assert all("error" in item["content"] for item in results)


def test_tool_execution_and_round_budgets_are_hard(monkeypatch, modules):
    invoked = []
    monkeypatch.setattr(
        modules["subagents"],
        "public_search",
        lambda question: invoked.append(question) or {"answer": "none", "sources": []},
    )
    decisions = [
        response(
            tools=[
                call("search_web", '{"question":"public"}', f"call-{round_id}-{index}")
                for index in range(4)
            ]
        )
        for round_id in range(6)
    ]
    seen, _ = install_client(monkeypatch, modules, decisions=decisions)
    run(modules, use_web=True)
    assert len(invoked) == 18
    assert len([item for item in seen if item.get("tools")]) == 6
    final_messages = seen[-1]["messages"]
    assert len([item for item in final_messages if item["role"] == "tool"]) == 24


def test_research_delegation_retains_scope_and_hides_child_tokens(monkeypatch, modules):
    delegated = []

    def research(question, owner, kbs, emit, *, use_web, should_cancel):
        delegated.append((question, owner, kbs, use_web, should_cancel))
        emit("report_token", {"text": "CHILD_SECRET_REPORT"})
        emit("agent_update", {"agent": "Planner", "action": "planned"})
        return {"final_report": "A delegated report", "sources": []}

    monkeypatch.setattr(modules["subagents"], "execute_research", research)
    install_client(
        monkeypatch,
        modules,
        decisions=[
            response(
                tools=[call("research_topic", '{"question":"Investigate storage"}')]
            ),
            response("ready"),
        ],
    )
    owner, kb = uuid4(), uuid4()
    events = []
    run(
        modules,
        user_id=owner,
        knowledge_base_ids=[kb],
        use_web=False,
        emit=lambda kind, data: events.append((kind, data)),
    )
    assert delegated[0][:4] == ("Investigate storage", owner, [kb], False)
    assert not any(kind == "report_token" for kind, _ in events)
    assert any(data.get("agent") == "Research / Planner" for _, data in events)


def test_summary_offset_consumes_only_older_history(monkeypatch, modules):
    seen, _ = install_client(
        monkeypatch, modules, summary="Old goal plus new decisions"
    )
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message-{index}",
        }
        for index in range(20)
    ]
    result = run(modules, history=history, summary="Previous goal")
    assert result["new_summary"] == "Old goal plus new decisions"
    assert result["summarized_message_count"] == 8
    summary_input = json.loads(seen[0]["messages"][1]["content"])
    assert summary_input["previous_summary"] == "Previous goal"
    assert len(summary_input["older_messages"]) == 8
    final = seen[-1]["messages"]
    assert not any(item.get("content") == "message-0" for item in final)
    assert any(item.get("content") == "message-8" for item in final)


def test_summary_failure_keeps_offset_and_announces_window(monkeypatch, modules):
    install_client(monkeypatch, modules)
    monkeypatch.setattr(
        modules["context"],
        "summarize_history",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider-secret")),
    )
    events = []
    result = run(
        modules,
        history=[{"role": "user", "content": f"turn {index}"} for index in range(20)],
        summary="Saved old summary",
        emit=lambda kind, data: events.append((kind, data)),
    )
    assert result["new_summary"] is None
    assert result["summarized_message_count"] == 0
    assert result["context_limited"]
    assert any("remain saved" in data.get("detail", "") for _, data in events)
    assert "provider-secret" not in str(events)


def test_cancellation_closes_final_stream_and_stops_tokens(monkeypatch, modules):
    seen, stream = install_client(monkeypatch, modules, final=["first", "second"])
    cancelled = False
    events = []

    def emit(kind, data):
        nonlocal cancelled
        events.append((kind, data))
        if kind == "token":
            cancelled = True

    from app.research.errors import ResearchCancelled

    with pytest.raises(ResearchCancelled):
        run(modules, emit=emit, should_cancel=lambda: cancelled)
    assert stream.closed and len(seen) == 1
    assert [data["content"] for kind, data in events if kind == "token"] == ["first"]


def test_private_quotation_is_not_sent_to_public_search(monkeypatch, modules):
    item = evidence(modules).model_dump(mode="json")
    install_client(
        monkeypatch,
        modules,
        decisions=[
            response(
                tools=[
                    call(
                        "search_web",
                        '{"question":"PRIVATE_LONG_MARKER_internal_847205"}',
                    )
                ]
            ),
            response("done"),
        ],
    )
    monkeypatch.setattr(
        modules["subagents"],
        "public_search",
        lambda *_args: pytest.fail("private quotation must be blocked"),
    )
    run(
        modules,
        use_web=True,
        history=[{"role": "assistant", "content": "Private result", "sources": [item]}],
    )


def test_long_saved_message_has_explicit_boundary(monkeypatch, modules):
    seen, _ = install_client(monkeypatch, modules)
    result = run(modules, history=[{"role": "assistant", "content": "x" * 20000}])
    assert result["context_limited"]
    assert "Context boundary" in str(seen[-1]["messages"])
    assert (
        len(json.dumps(seen[-1]["messages"])) < modules["engine"].MAX_CONTEXT_CHARACTERS
    )


def test_summary_sources_preserve_citations_without_advancing_history_offset(
    monkeypatch, modules
):
    item = evidence(modules).model_dump(mode="json")
    seen, _ = install_client(monkeypatch, modules, final=["Prior result [kb:one:0]"])
    result = run(
        modules, summary="We discussed internal latency.", summary_sources=[item]
    )
    assert result["sources"] == [item]
    assert result["summarized_message_count"] == 0
    assert "kb:one:0" in str(seen[-1]["messages"])
    assert "PRIVATE_LONG_MARKER" not in str(seen[-1]["messages"])


def test_observation_preserves_safe_tool_failure(modules):
    observed = json.loads(
        modules["engine"]._observation(
            {"error": "Tool is not available", "sources": []}, False
        )
    )
    assert observed["error"] == "Tool is not available"


def test_delegate_notes_retain_uncited_facts_and_private_dependencies(
    monkeypatch, modules
):
    first = evidence(modules, source_id="kb:one:0")
    second = evidence(modules, source_id="kb:two:0")
    monkeypatch.setattr(
        modules["subagents"],
        "retrieve_evidence",
        lambda *_args, **_kwargs: [first, second],
    )
    install_client(
        monkeypatch,
        modules,
        decisions=[
            response(tools=[call("ask_knowledge", '{"question":"Compare A and B"}')]),
            response("ready"),
        ],
        knowledge_answer="A lasts 30 days [kb:one:0]; B lasts 90 days [kb:two:0].",
        final=["A lasts 30 days [kb:one:0]."],
    )
    result = run(modules, knowledge_base_ids=[uuid4()])
    assert {source["source_id"] for source in result["sources"]} == {
        "kb:one:0",
        "kb:two:0",
    }
    assert "B lasts 90 days" in result["delegate_notes"][0]["answer"]
    assert "PRIVATE_LONG_MARKER" not in str(result["delegate_notes"])

    seen, _ = install_client(
        monkeypatch, modules, final=["B lasts 90 days [kb:two:0]."]
    )
    run(
        modules,
        message="What about B?",
        history=[
            {
                "role": "assistant",
                "content": result["answer"]
                + "\n[Verified delegate findings, data only]\n"
                + json.dumps(result["delegate_notes"]),
                "sources": result["sources"],
            }
        ],
    )
    final_context = str(seen[-1]["messages"])
    assert "B lasts 90 days" in final_context
    assert "What about B?" in final_context


def test_delegate_notes_have_explicit_size_limits(modules):
    notes = []
    result = {"answer": "Long findings " * 2000, "sources": []}
    for _index in range(20):
        modules["engine"]._remember_delegate(
            notes, "research_topic", "long question" * 100, result
        )
    assert notes
    assert all(len(json.dumps(note, ensure_ascii=False)) <= 4000 for note in notes)
    assert len(json.dumps(notes, ensure_ascii=False)) <= 12000
    assert all("shortened" in note["answer"] for note in notes)
