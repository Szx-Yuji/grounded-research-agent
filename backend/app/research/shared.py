"""Optional Redis cache; connection failures leave search functional."""

import threading
from typing import Any

from app.research import config

_client: Any = None
_checked = False
_lock = threading.Lock()


def get_redis() -> Any:
    global _client, _checked
    with _lock:
        if _checked:
            return _client
        _checked = True
        if not config.REDIS_URL:
            return None
        try:
            import redis

            candidate = redis.Redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
            candidate.ping()
            _client = candidate
        except Exception:
            _client = None
    return _client
