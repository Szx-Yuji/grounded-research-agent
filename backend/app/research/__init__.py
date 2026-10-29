"""Lightweight research API; load provider SDKs only when a worker executes."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.research.errors import ResearchCancelled, ResearchExecutionError

__all__ = ["ResearchCancelled", "ResearchExecutionError", "execute_research"]


def execute_research(
    query: str,
    user_id: UUID,
    knowledge_base_ids: list[UUID],
    emit: Callable[[str, dict[str, Any]], None],
    *,
    use_web: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    from app.research.engine import execute_research as execute

    return execute(
        query,
        user_id,
        knowledge_base_ids,
        emit,
        use_web=use_web,
        should_cancel=should_cancel,
    )
