from typing import Any, TypedDict
from uuid import UUID


class ResearchState(TypedDict):
    query: str
    user_id: UUID
    knowledge_base_ids: list[UUID]
    knowledge_evidence: list[dict[str, Any]]
    sub_tasks: list[str]
    search_results: dict[str, list]
    analysis: str
    final_report: str
    agent_logs: list[dict]
    current_agent: str
    error: str | None
    # Sub-questions that produced no usable results on the last pass. The
    # refiner rewrites these; the researcher retries only these.
    unanswered_tasks: list[str]
    # How many refine/re-search rounds have run. Bounds the retry loop.
    retry_count: int
