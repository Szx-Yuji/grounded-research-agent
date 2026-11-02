"""Small process-local metrics registry for operator smoke checks.

Values are deliberately low-cardinality and contain no prompts, sources or
tokens. Durable queue and provider usage remain in PostgreSQL; this registry
is a fast health signal, not a replacement for a metrics backend.
"""

import threading
from collections import defaultdict
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._durations: dict[str, list[float]] = defaultdict(list)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_ms(self, name: str, value: float) -> None:
        with self._lock:
            values = self._durations[name]
            values.append(round(value, 3))
            if len(values) > 1000:
                del values[: len(values) - 1000]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            durations = {
                name: {
                    "count": len(values),
                    "p50_ms": _percentile(values, 0.50),
                    "p95_ms": _percentile(values, 0.95),
                }
                for name, values in self._durations.items()
            }
            return {"counters": dict(self._counters), "durations": durations}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


registry = MetricsRegistry()
