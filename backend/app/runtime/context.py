"""One atomic budget shared by the parent, delegates and search pool threads.

Limits reserve the maximum requested output, not guessed provider charges.
Provider requests are recorded before dispatch. Unknown outcomes are never
automatically replayed by this module.
"""

import contextvars
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.research.errors import ResearchExecutionError
from app.research.events import check_cancelled

CallKind = Literal["model", "search", "embedding", "tool"]


class BudgetExceeded(ResearchExecutionError):
    """A whole execution exhausted an explicit resource limit."""


@dataclass(frozen=True)
class ExecutionLimits:
    model: int = 24
    search: int = 16
    embedding: int = 12
    tool: int = 30
    output_tokens: int = 50000
    seconds: float = 300

    @classmethod
    def from_settings(cls) -> ExecutionLimits:
        from app.core.config import settings

        return cls(
            model=settings.EXECUTION_MAX_MODEL_CALLS,
            search=settings.EXECUTION_MAX_SEARCH_CALLS,
            embedding=settings.EXECUTION_MAX_EMBEDDING_CALLS,
            tool=settings.EXECUTION_MAX_TOOL_CALLS,
            output_tokens=settings.EXECUTION_MAX_OUTPUT_TOKENS,
            seconds=settings.RESEARCH_TIMEOUT_SECONDS,
        )


_context: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "execution_runtime", default=None
)
_parent: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "execution_span", default=None
)


def _persist(call: dict[str, Any], starting: bool) -> None:
    """Fail closed before dispatch if the call intent cannot be persisted."""
    from sqlmodel import Session

    from app.core.db import engine
    from app.runtime.models import RuntimeCall
    from app.services.durable_jobs import assert_lease

    with Session(engine) as session:
        assert_lease(session, call["job_id"], call["lease_token"])
        if starting:
            session.add(RuntimeCall.model_validate(call))
        else:
            row = session.get(RuntimeCall, call["id"])
            if row is None:
                raise RuntimeError("Provider call reservation was not persisted")
            for key in (
                "status",
                "input_tokens",
                "output_tokens",
                "duration_ms",
                "first_token_ms",
                "error_type",
                "finished_at",
                "attributes",
            ):
                setattr(row, key, call.get(key))
            session.add(row)
        session.commit()


@dataclass
class RunContext:
    limits: ExecutionLimits
    deadline: float
    job_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    lease_token: uuid.UUID | None = None
    sink: Callable[[dict[str, Any], bool], None] | None = None
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("model", "search", "embedding", "tool"), 0
        )
    )
    reserved_tokens: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    request_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remaining(self) -> float:
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise BudgetExceeded(
                "Execution deadline exhausted. Narrow the question or start another turn."
            )
        return seconds

    def reserve(
        self,
        kind: CallKind,
        tokens: int,
        request_key: str | None = None,
        record: dict[str, Any] | None = None,
    ) -> None:
        check_cancelled()
        with self.lock:
            self.remaining()
            previous = self.request_states.get(request_key) if request_key else None
            if previous and previous["status"] in {"started", "unknown"}:
                raise BudgetExceeded(
                    "An identical provider request has an unresolved outcome. It was not automatically replayed; submit a new turn to retry explicitly."
                )
            if self.counts[kind] >= getattr(self.limits, kind):
                raise BudgetExceeded(f"Execution {kind} call budget exhausted.")
            if self.reserved_tokens + tokens > self.limits.output_tokens:
                raise BudgetExceeded(
                    "Execution output-token reservation budget exhausted."
                )
            self.counts[kind] += 1
            self.reserved_tokens += tokens
            if request_key and record is not None:
                self.request_states[request_key] = record


def current_context() -> RunContext | None:
    return _context.get()


def request_timeout(default: float) -> float:
    current = current_context()
    return min(default, current.remaining()) if current else default


@contextmanager
def execution_context(
    job_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    *,
    lease_token: uuid.UUID | None = None,
    deadline_at: datetime | None = None,
    limits: ExecutionLimits | None = None,
    sink: Callable[[dict[str, Any], bool], None] | None = None,
) -> Iterator[RunContext]:
    existing = current_context()
    if existing is not None:
        yield existing
        return
    limits = limits or ExecutionLimits.from_settings()
    seconds = limits.seconds
    if deadline_at is not None:
        seconds = min(seconds, (deadline_at - datetime.now(UTC)).total_seconds())
    if job_id is not None and (owner_id is None or lease_token is None):
        raise ValueError("Persistent execution requires an owner and a lease token")
    current = RunContext(
        limits,
        time.monotonic() + seconds,
        job_id,
        owner_id,
        lease_token,
        sink or (_persist if job_id else None),
    )
    token = _context.set(current)
    try:
        yield current
    finally:
        _context.reset(token)


class ProviderCall:
    def __init__(
        self,
        kind: CallKind,
        *,
        operation: str,
        model: str | None = None,
        max_output_tokens: int = 0,
        request_key: str | None = None,
    ):
        if (
            kind not in ("model", "search", "embedding", "tool")
            or max_output_tokens < 0
        ):
            raise ValueError("Invalid provider call reservation")
        self.context = current_context()
        self.request_key = request_key
        self.started = time.monotonic()
        self.done = False
        self.record: dict[str, Any] = {
            "id": uuid.uuid4(),
            "parent_id": _parent.get(),
            "kind": kind,
            "operation": operation[:80],
            "model": str(model)[:160] if model else None,
            "reserved_output_tokens": max_output_tokens,
            "status": "started",
            "started_at": datetime.now(UTC),
            "attributes": {},
            "input_tokens": None,
            "output_tokens": None,
            "first_token_ms": None,
            "duration_ms": None,
            "error_type": None,
            "finished_at": None,
        }

    def __enter__(self) -> ProviderCall:
        check_cancelled()
        if self.context:
            self.context.reserve(
                self.record["kind"],
                self.record["reserved_output_tokens"],
                self.request_key,
                self.record,
            )
            self.record.update(
                job_id=self.context.job_id,
                owner_id=self.context.owner_id,
                lease_token=self.context.lease_token,
            )
            if self.context.sink:
                self.context.sink(self.record.copy(), True)
            with self.context.lock:
                self.context.calls.append(self.record)
        self.parent_token = _parent.set(self.record["id"])
        return self

    def first_token(self) -> None:
        if self.record["first_token_ms"] is None:
            self.record["first_token_ms"] = round(
                (time.monotonic() - self.started) * 1000, 3
            )

    def usage(self, usage: Any) -> None:
        if usage is None:
            return

        def value(key):
            result = (
                usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            )
            return (
                result
                if isinstance(result, int)
                and not isinstance(result, bool)
                and result >= 0
                else None
            )

        input_tokens = value("prompt_tokens")
        if input_tokens is None and self.record["kind"] == "embedding":
            input_tokens = value("total_tokens")
        if input_tokens is not None:
            self.record["input_tokens"] = input_tokens
        output_tokens = value("completion_tokens")
        if output_tokens is not None:
            self.record["output_tokens"] = output_tokens

    def succeed(
        self, *, usage: Any = None, attributes: dict[str, Any] | None = None
    ) -> None:
        self.usage(usage)
        self._finish("succeeded", attributes=attributes)

    def _finish(
        self,
        status: str,
        *,
        error: BaseException | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        if self.done:
            return
        self.done = True
        self.record.update(
            status=status,
            duration_ms=round((time.monotonic() - self.started) * 1000, 3),
            finished_at=datetime.now(UTC),
            error_type=type(error).__name__[:80] if error else None,
        )
        if attributes:
            # Only code-supplied scalar counters; never arbitrary provider data.
            self.record["attributes"] = {
                key: val
                for key, val in attributes.items()
                if key in {"result_count", "item_count", "input_characters"}
                and isinstance(val, int)
            }
        if self.context and self.context.sink:
            self.context.sink(self.record.copy(), False)

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.done:
                status = (
                    "failed"
                    if exc is not None
                    and getattr(exc, "status_code", 500)
                    in {400, 401, 403, 404, 422, 429}
                    else "unknown"
                )
                self._finish(status, error=exc)
        finally:
            _parent.reset(self.parent_token)
        return False


def provider_call(
    kind: CallKind,
    *,
    operation: str,
    model: str | None = None,
    max_output_tokens: int = 0,
    request_key: str | None = None,
) -> ProviderCall:
    return ProviderCall(
        kind,
        operation=operation,
        model=model,
        max_output_tokens=max_output_tokens,
        request_key=request_key,
    )


def request_fingerprint(kind: str, payload: Any) -> str:
    """Keep only an in-memory hash; never add request text to telemetry."""
    return (
        kind
        + ":"
        + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
