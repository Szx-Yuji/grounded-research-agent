"""Provider-free Agent Harness evaluation.

This evaluates deterministic orchestration contracts around the main Agent:
tool availability/selection, source IDs, abstention and public-query safety.
The fixture model is intentionally deterministic and must never be presented
as a measurement of a real provider's answer quality.
"""

import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.assistant import engine, subagents  # noqa: E402
from app.runtime.context import ExecutionLimits, execution_context  # noqa: E402

DATASET = Path(__file__).with_name("dataset.json")
OUTPUT = ROOT / "evals/results/agent-fixture.json"


class FakeStream:
    def __init__(self, answer: str):
        self.answer = answer

    def __enter__(self):
        return self

    def __iter__(self):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=self.answer))]
        )

    def __exit__(self, *_args):
        return False


def _response(content="", tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _tool_call(name: str, question: str):
    arguments = json.dumps(
        {"name": "source-comparison"}
        if name == "load_skill"
        else {"question": question},
        ensure_ascii=False,
    )
    return SimpleNamespace(
        id=f"fixture-{uuid4().hex}",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _source(source_id: str, kind: str):
    return {
        "source_id": source_id,
        "kind": kind,
        "content": "Fixture evidence",
        "title": "fixture",
        "url": "https://fixture.invalid/source" if kind == "web" else None,
        "document_id": "fixture" if kind == "knowledge" else None,
        "chunk_index": 0 if kind == "knowledge" else None,
    }


def run_case(case: dict) -> dict:
    decisions = list(case.get("expected_tools", []))
    calls = []
    public_queries = []

    class Completions:
        def create(self, **kwargs):
            calls.append(copy.deepcopy(kwargs))
            if kwargs.get("stream"):
                return FakeStream(case["answer"])
            if decisions:
                name = decisions.pop(0)
                return _response(tool_calls=[_tool_call(name, case["message"])])
            return _response("ready")

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    original_main = engine.get_client
    original_child = subagents.get_client
    original_rag = subagents.ask_knowledge
    original_research = subagents.research_topic
    original_search = subagents.public_search

    def fake_rag(_question, _user, _scope):
        source_ids = case.get("expected_source_ids", [])
        return {
            "answer": case["answer"],
            "sources": [_source(value, "knowledge") for value in source_ids],
        }

    def fake_research(*_args, **_kwargs):
        source_ids = case.get("expected_source_ids", [])
        return {
            "answer": case["answer"],
            "sources": [_source(value, "web") for value in source_ids],
        }

    def fake_search(question):
        public_queries.append(question)
        source_ids = case.get("expected_source_ids", [])
        return {
            "answer": "Public search evidence",
            "sources": [_source(value, "web") for value in source_ids],
        }

    setattr(engine, "get_client", lambda: client)
    setattr(subagents, "get_client", lambda: client)
    setattr(subagents, "ask_knowledge", fake_rag)
    setattr(subagents, "research_topic", fake_research)
    setattr(subagents, "public_search", fake_search)
    events = []
    try:
        with execution_context(limits=ExecutionLimits(seconds=30)):
            result = engine.execute_chat(
                message=case["message"],
                user_id=uuid4(),
                knowledge_base_ids=[uuid4()] if case.get("knowledge") else [],
                use_web=case.get("web", False),
                history=case.get("history", []),
                summary="",
                emit=lambda kind, data: events.append((kind, data)),
            )
    except Exception as exc:  # The harness records failures without leaking prompts.
        return {"id": case["id"], "status": "error", "error_type": type(exc).__name__}
    finally:
        setattr(engine, "get_client", original_main)
        setattr(subagents, "get_client", original_child)
        setattr(subagents, "ask_knowledge", original_rag)
        setattr(subagents, "research_topic", original_research)
        setattr(subagents, "public_search", original_search)

    actual_tools = [
        data.get("tool")
        for kind, data in events
        if kind == "agent_update" and data.get("action") == "Delegating tool"
    ]
    answer = result.get("answer", "")
    cited = re.findall(r"\[([^\]]+)\]", answer)
    expected_tools = case.get("expected_tools", [])
    expected_sources = set(case.get("expected_source_ids", []))
    cited_set = set(cited)
    checks = {
        "tool_selection": actual_tools == expected_tools,
        "citation_precision": bool(cited_set) is False
        if not expected_sources
        else cited_set <= expected_sources,
        "citation_recall": expected_sources <= cited_set,
        "forbidden_content_absent": all(
            value not in answer for value in case.get("forbidden", [])
        ),
        "private_query_not_public": all(
            "PRIVATE_" not in query for query in public_queries
        ),
    }
    return {
        "id": case["id"],
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "tool_calls": actual_tools,
        "public_query_count": len(public_queries),
        "citation_ids": sorted(cited_set),
    }


def main() -> int:
    cases = json.loads(DATASET.read_text())
    results = [run_case(case) for case in cases]
    checks = [result for result in results if result["status"] != "error"]
    report = {
        "schema_version": 1,
        "mode": "offline_agent_harness_fixture",
        "provider_calls": 0,
        "limitations": [
            "Deterministic fake model; this is an orchestration contract report, not real model quality.",
            "Use a separately approved provider mode and a redacted holdout set for production quality claims.",
        ],
        "counts": {
            "cases": len(results),
            "passed": sum(item["status"] == "passed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "errors": sum(item["status"] == "error" for item in results),
        },
        "aggregate": {
            "tool_selection_accuracy": sum(
                item.get("checks", {}).get("tool_selection", False) for item in checks
            )
            / max(1, len(checks)),
            "citation_precision_contract": sum(
                item.get("checks", {}).get("citation_precision", False)
                for item in checks
            )
            / max(1, len(checks)),
            "citation_recall_contract": sum(
                item.get("checks", {}).get("citation_recall", False) for item in checks
            )
            / max(1, len(checks)),
            "private_query_safety": sum(
                item.get("checks", {}).get("private_query_not_public", False)
                for item in checks
            )
            / max(1, len(checks)),
        },
        "cases_detail": results,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "passed"
                if report["counts"]["failed"] == 0 and report["counts"]["errors"] == 0
                else "failed",
                "report": str(OUTPUT.relative_to(ROOT)),
                "counts": report["counts"],
            },
            ensure_ascii=False,
        )
    )
    return (
        0 if report["counts"]["failed"] == 0 and report["counts"]["errors"] == 0 else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
