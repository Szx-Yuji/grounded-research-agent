"""Shared DeepSeek client, using its OpenAI-compatible API."""

from openai import OpenAI

from app.research import config
from app.research.events import check_cancelled
from app.runtime.gateway import ModelGateway

_client: ModelGateway | None = None


def get_client() -> ModelGateway:
    check_cancelled()
    global _client
    if _client is None:
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
        _client = ModelGateway(
            OpenAI(
                api_key=api_key,
                base_url=config.DEEPSEEK_BASE_URL,
                timeout=config.DEEPSEEK_TIMEOUT_SECONDS,
                max_retries=0,
            )
        )
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None
