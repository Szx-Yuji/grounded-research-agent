from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.research import config
from app.research.events import check_cancelled, emit_log
from app.research.models import ResearchState
from app.research.tools.search import SearchError, search_web


def _log(agent: str, action: str, detail) -> dict:
    return {
        "agent": agent,
        "action": action,
        "detail": detail,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def researcher_node(state: ResearchState) -> dict:
    """Search each outstanding sub-task: every one on the first pass, only the
    refiner's rewrites afterwards.

    Searches run concurrently, but results are logged from this thread in the
    order tasks were planned. Emitting from the pool threads would be a no-op,
    since the emitter is a ContextVar and pool threads start with a fresh
    context.

    Tracks "found nothing" separately from "could not run", and errors only if
    every request failed.
    """
    check_cancelled()
    pending = state.get("unanswered_tasks") or state.get("sub_tasks", [])
    search_results = dict(state.get("search_results", {}))
    logs = list(state.get("agent_logs", []))

    unanswered: list[str] = []
    failures: list[str] = []

    if pending:
        workers = max(1, min(config.SEARCH_CONCURRENCY, len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {task: pool.submit(search_web, task) for task in pending}

            for task in pending:
                check_cancelled()
                try:
                    results = futures[task].result()
                except SearchError:
                    failures.append(task)
                    unanswered.append(task)
                    emit_log(logs, _log("Researcher", "Search failed", task))
                except Exception:  # noqa: BLE001 - a pool failure is still a failure
                    failures.append(task)
                    unanswered.append(task)
                    emit_log(logs, _log("Researcher", "Search failed", task))
                else:
                    if results:
                        search_results[task] = results
                        emit_log(logs, _log("Researcher", "Searched", task))
                    else:
                        unanswered.append(task)
                        emit_log(logs, _log("Researcher", "No results found", task))

    if (
        pending
        and len(failures) == len(pending)
        and not state.get("knowledge_evidence")
        and not search_results
    ):
        return {
            "search_results": search_results,
            "unanswered_tasks": unanswered,
            "agent_logs": logs,
            "current_agent": "Researcher",
            "error": (
                "Web search is unavailable and no knowledge evidence was retrieved."
            ),
        }

    return {
        "search_results": search_results,
        "unanswered_tasks": unanswered,
        "agent_logs": logs,
        "current_agent": "Researcher",
        "error": None,
    }
