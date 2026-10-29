"""Shared DeepSeek client, using its OpenAI-compatible API."""

from openai import OpenAI

from app.research import config
from app.research.events import check_cancelled

_client: OpenAI | None = None


def get_client() -> OpenAI:
    check_cancelled()
    global _client
    if _client is None:
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
        _client = OpenAI(
            api_key=api_key,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=config.DEEPSEEK_TIMEOUT_SECONDS,
        )
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None
