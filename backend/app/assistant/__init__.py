"""Conversation-first assistant; provider imports stay inside worker execution."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.assistant.errors import AssistantExecutionError

__all__ = ["AssistantExecutionError", "execute_chat"]


def execute_chat(
    message: str,
    user_id: UUID,
    knowledge_base_ids: list[UUID],
    use_web: bool,
    history: list[dict[str, Any]],
    summary: str,
    emit: Callable[[str, dict[str, Any]], None],
    should_cancel: Callable[[], bool] | None = None,
    *,
    summary_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.assistant.engine import execute_chat as execute

    return execute(
        message,
        user_id,
        knowledge_base_ids,
        use_web,
        history,
        summary,
        emit,
        should_cancel=should_cancel,
        summary_sources=summary_sources,
    )
