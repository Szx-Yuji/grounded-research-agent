"""Lets graph nodes push incremental output back to the run's event queue.

The nodes run on a worker thread owned by main.py. A ContextVar avoids adding a
callback argument to every node signature. With no emitter installed, emit() is
a no-op.
"""

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from app.research.errors import ResearchCancelled

_emitter: contextvars.ContextVar[Callable[[str, dict[str, Any]], None] | None] = (
    contextvars.ContextVar("research_emitter", default=None)
)
_should_cancel: contextvars.ContextVar[Callable[[], bool] | None] = (
    contextvars.ContextVar("research_should_cancel", default=None)
)


def set_emitter(fn: Callable[[str, dict[str, Any]], None] | None) -> None:
    _emitter.set(fn)


@contextmanager
def emitter_context(
    fn: Callable[[str, dict[str, Any]], None],
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Restore the caller's context even when retrieval or a graph node fails."""
    token = _emitter.set(fn)
    cancel_token = _should_cancel.set(should_cancel)
    try:
        yield
    finally:
        _emitter.reset(token)
        _should_cancel.reset(cancel_token)


def check_cancelled() -> None:
    callback = _should_cancel.get()
    if callback is not None and callback():
        raise ResearchCancelled("Research was cancelled")


def emit(event_type: str, data: dict) -> None:
    check_cancelled()
    fn = _emitter.get()
    if fn is not None:
        fn(event_type, data)


def emit_log(logs: list[dict], entry: dict) -> list[dict]:
    """Record a log entry and stream it now.

    The graph only yields to main.py when a node returns, so a node that does
    several seconds of work per item (the Researcher) would otherwise report
    all of it in one burst at the end.
    """
    logs.append(entry)
    emit("agent_update", entry)
    return logs
