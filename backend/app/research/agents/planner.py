import json
from datetime import UTC, datetime

from app.research import config
from app.research.errors import ResearchCancelled
from app.research.events import emit_log
from app.research.llm import get_client
from app.research.models import ResearchState

SYSTEM_PROMPT = (
    "You are a research planner. Given a user's question, break it down into "
    "3-5 specific, searchable sub-questions that together would fully answer "
    "the original query. Return ONLY a JSON array of strings."
)


def parse_task_list(raw: str) -> list[str]:
    """Parse a JSON array of strings out of an LLM response.

    Tolerates the model wrapping its answer in a Markdown code fence.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) > 1:
            raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    tasks = json.loads(raw)
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise ValueError("LLM did not return a JSON array of strings")
    tasks = list(dict.fromkeys(t.strip() for t in tasks if t.strip()))
    if not tasks or len(tasks) > config.MAX_SUB_TASKS:
        raise ValueError("Expected between one and five research sub-tasks")
    if any(len(task) > config.MAX_SEARCH_QUERY_LENGTH for task in tasks):
        raise ValueError("Research search query is too long")
    return tasks


def planner_node(state: ResearchState) -> dict:
    try:
        client = get_client()
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": state["query"]},
            ],
            extra_body={"thinking": {"type": config.DEEPSEEK_THINKING}},
        )
        sub_tasks = parse_task_list(response.choices[0].message.content or "")

        logs = emit_log(
            list(state.get("agent_logs", [])),
            {
                "agent": "Planner",
                "action": "Generated sub-tasks",
                "detail": sub_tasks,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        return {
            "sub_tasks": sub_tasks,
            "unanswered_tasks": [],
            "retry_count": 0,
            "agent_logs": logs,
            "current_agent": "Planner",
            "error": None,
        }
    except ResearchCancelled:
        raise
    except Exception:
        return {
            "error": "Research planning failed. Please try again.",
            "current_agent": "Planner",
            "agent_logs": state.get("agent_logs", []),
        }
