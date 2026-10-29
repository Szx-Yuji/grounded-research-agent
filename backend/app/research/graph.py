from typing import Literal

from langgraph.graph import END, StateGraph

from app.research import config
from app.research.agents.analyst import analyst_node
from app.research.agents.planner import planner_node
from app.research.agents.refiner import refiner_node
from app.research.agents.researcher import researcher_node
from app.research.agents.writer import writer_node
from app.research.models import ResearchState


def error_handler_node(state: ResearchState) -> dict:
    return {"current_agent": "Error", "error": state.get("error", "Unknown error")}


def route_after_node(state: ResearchState) -> Literal["error_handler", "continue"]:
    if state.get("error"):
        return "error_handler"
    return "continue"


def coverage_ratio(state: ResearchState) -> float:
    """Fraction of sub-questions that produced at least one source."""
    sub_tasks = state.get("sub_tasks", [])
    if not sub_tasks:
        return 0.0
    return len(state.get("search_results", {})) / len(sub_tasks)


def route_after_research(
    state: ResearchState,
) -> Literal["error_handler", "refine", "continue"]:
    """Send a thin pass back through the refiner instead of on to the Analyst."""
    if state.get("error"):
        return "error_handler"

    unanswered = state.get("unanswered_tasks", [])
    retries_left = state.get("retry_count", 0) < config.MAX_RESEARCH_RETRIES
    thin = coverage_ratio(state) < config.MIN_COVERAGE_RATIO

    if unanswered and retries_left and thin:
        return "refine"
    return "continue"


def build_graph():
    graph = StateGraph(ResearchState)

    graph.add_node("planner", planner_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("refiner", refiner_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("writer", writer_node)
    graph.add_node("error_handler", error_handler_node)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        route_after_node,
        {"error_handler": "error_handler", "continue": "researcher"},
    )
    graph.add_conditional_edges(
        "researcher",
        route_after_research,
        {
            "error_handler": "error_handler",
            "refine": "refiner",
            "continue": "analyst",
        },
    )
    # retry_count / MAX_RESEARCH_RETRIES bound this cycle.
    graph.add_edge("refiner", "researcher")
    graph.add_conditional_edges(
        "analyst",
        route_after_node,
        {"error_handler": "error_handler", "continue": "writer"},
    )
    graph.add_conditional_edges(
        "writer",
        route_after_node,
        {"error_handler": "error_handler", "continue": END},
    )
    graph.add_edge("error_handler", END)

    return graph.compile()


compiled_graph = build_graph()
