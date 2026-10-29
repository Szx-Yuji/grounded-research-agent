"""Search result cache, backed by Redis when configured.

Falls back to a bounded in-process dict so a single worker still benefits.
Cache misses and backend errors are never fatal: a failure here degrades to
"not cached", never to a failed search.
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict

from app.research import config
from app.research.shared import get_redis

_local: OrderedDict[str, tuple[float, list]] = OrderedDict()
_lock = threading.Lock()


def _key(query: str, max_results: int) -> str:
    query_hash = hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()
    return f"grounded-research:public-search:v1:{max_results}:{query_hash}"


def get(query: str, max_results: int) -> list | None:
    if config.SEARCH_CACHE_TTL_SECONDS <= 0:
        return None
    key = _key(query, max_results)

    redis = get_redis()
    if redis is not None:
        try:
            raw = redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    with _lock:
        entry = _local.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            _local.pop(key, None)
            return None
        _local.move_to_end(key)
        return value


def set(query: str, max_results: int, results: list) -> None:
    if config.SEARCH_CACHE_TTL_SECONDS <= 0:
        return
    key = _key(query, max_results)

    redis = get_redis()
    if redis is not None:
        try:
            redis.setex(key, config.SEARCH_CACHE_TTL_SECONDS, json.dumps(results))
        except Exception:
            pass
        return

    with _lock:
        _local[key] = (time.time() + config.SEARCH_CACHE_TTL_SECONDS, results)
        _local.move_to_end(key)
        while len(_local) > config.SEARCH_CACHE_MAX_ENTRIES:
            _local.popitem(last=False)


def clear() -> None:
    with _lock:
        _local.clear()
