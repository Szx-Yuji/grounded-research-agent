"""Compatibility adapter for the pre-unified knowledge-base endpoints.

The old HTTP response shape remains stable, but retrieval, authorization,
model retries and execution limits now use the same RAG delegate as /chat.
This adapter deliberately does not create a durable job: callers that need
durable execution should use the unified conversation API.
"""

from typing import Any
from uuid import UUID

from app.assistant.subagents import ask_knowledge
from app.runtime.context import ExecutionLimits, execution_context


def execute_legacy_knowledge(
    question: str,
    user_id: UUID,
    knowledge_base_ids: list[UUID],
) -> dict[str, Any]:
    """Run one bounded compatibility request with the unified RAG delegate."""
    with execution_context(
        limits=ExecutionLimits(
            model=2,
            embedding=2,
            tool=1,
            search=0,
            output_tokens=6000,
            seconds=60,
        )
    ):
        return ask_knowledge(question, user_id, knowledge_base_ids)
