"""Synchronous graph execution for the application's background worker."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.research.errors import ResearchExecutionError
from app.research.events import check_cancelled, emit_log, emitter_context
from app.research.evidence import collect_sources
from app.research.graph import compiled_graph
from app.research.models import ResearchState
from app.services.knowledge_retrieval import retrieve_evidence


def execute_research(
    query: str,
    user_id: UUID,
    knowledge_base_ids: list[UUID],
    emit: Callable[[str, dict[str, Any]], None],
    *,
    use_web: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one research graph, with permission-scoped KB evidence.

    Events are ``agent_update``, ``analysis_token`` and ``report_token``.
    The worker emits the final ``complete`` or ``error`` event. No retrieval
    results enter Planner, Refiner, Tavily queries, or the public search cache.
    Exceptions from retrieval/authorization are allowed to reach the caller;
    graph failures raise ``ResearchExecutionError`` with a safe stage message.
    """
    state: ResearchState = {
        "query": query,
        "user_id": user_id,
        "knowledge_base_ids": list(dict.fromkeys(knowledge_base_ids)),
        "knowledge_evidence": [],
        "sub_tasks": [],
        "search_results": {},
        "analysis": "",
        "final_report": "",
        "agent_logs": [],
        "current_agent": "",
        "error": None,
        "unanswered_tasks": [],
        "retry_count": 0,
    }
    with emitter_context(emit, should_cancel):
        check_cancelled()
        # Fail closed before starting any model/search calls if KB access fails.
        if state["knowledge_base_ids"]:
            evidence = retrieve_evidence(
                user_id=user_id,
                knowledge_base_ids=state["knowledge_base_ids"],
                query=query,
                k=4,
            )
            state["knowledge_evidence"] = [
                item.model_dump(mode="json") for item in evidence
            ]
            check_cancelled()
            emit_log(
                state["agent_logs"],
                {
                    "agent": "Knowledge",
                    "action": "Retrieved selected knowledge bases",
                    "detail": f"Retrieved {len(evidence)} evidence chunks",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

        if use_web:
            updates = compiled_graph.stream(state, stream_mode="updates")
        else:
            from app.research.agents.analyst import analyst_node
            from app.research.agents.writer import writer_node

            def knowledge_updates():
                for node in (analyst_node, writer_node):
                    check_cancelled()
                    yield {node.__name__: node(state)}

            updates = knowledge_updates()

        for update in updates:
            check_cancelled()
            for output in update.values():
                state.update(output)
                if state.get("error"):
                    raise ResearchExecutionError(state["error"])

        if not state["final_report"]:
            raise ResearchExecutionError("Research did not produce a report")
        return {
            "final_report": state["final_report"],
            "sub_tasks": state["sub_tasks"],
            "sources": collect_sources(
                state["search_results"], state["knowledge_evidence"]
            ),
            "logs": state["agent_logs"],
        }
