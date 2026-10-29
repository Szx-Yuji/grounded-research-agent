"""Independent PostgreSQL worker: ``python -m app.worker``.

The API never starts this loop implicitly. The PG queue supports atomic claims,
but the embedded local vector index currently restricts deployment to one worker
process with bounded threads. Lost attempts are never automatically paid-replayed.
"""

import logging
import os
import signal
import socket
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager

from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.services import durable_jobs

logger = logging.getLogger(__name__)
EMBEDDED_WORKER_LOCK = 748382953


class WorkerGuardLost(RuntimeError):
    """The worker must exit instead of silently using a new database session."""


@contextmanager
def embedded_worker_guard() -> Iterator[Callable[[], None]]:
    """Deployment guard; it does not make API-side embedded access process-safe."""
    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:gate)"), {"gate": EMBEDDED_WORKER_LOCK}
        )
        connection.commit()
        if not acquired:
            raise RuntimeError(
                "The embedded vector index permits one worker process. Stop the existing worker before starting another."
            )
        lost = False

        def assert_owned() -> None:
            nonlocal lost
            if lost:
                raise WorkerGuardLost("Worker guard was lost; restart the worker.")
            try:
                owned = connection.scalar(
                    text("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_locks
                        WHERE locktype = 'advisory' AND pid = pg_backend_pid()
                          AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
                          AND classid::bigint = :high AND objid::bigint = :low
                          AND objsubid = 1 AND granted
                    )
                """),
                    {
                        "high": EMBEDDED_WORKER_LOCK >> 32,
                        "low": EMBEDDED_WORKER_LOCK & 0xFFFFFFFF,
                    },
                )
                connection.commit()
                if not owned:
                    raise WorkerGuardLost("Worker guard was lost; restart the worker.")
            except Exception:
                lost = True
                raise WorkerGuardLost(
                    "Worker guard connection was lost; restart the worker."
                ) from None

        try:
            yield assert_owned
        finally:
            try:
                if lost:
                    # Do not return an uncertain session-level advisory lock to
                    # the connection pool after a non-disconnect SQL failure.
                    connection.invalidate()
                else:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:gate)"),
                        {"gate": EMBEDDED_WORKER_LOCK},
                    )
                    connection.commit()
            except Exception as exc:
                # A disconnected session has already lost its advisory lock.
                logger.error("Worker guard connection ended (%s)", type(exc).__name__)


def run_worker(
    stop: threading.Event,
    *,
    worker_id: str | None = None,
    kinds: list[str] | None = None,
) -> None:
    with embedded_worker_guard() as assert_owned:
        _run_worker_loop(
            stop, worker_id=worker_id, kinds=kinds, verify_guard=assert_owned
        )


def _run_worker_loop(
    stop: threading.Event,
    *,
    worker_id: str | None = None,
    kinds: list[str] | None = None,
    verify_guard: Callable[[], None],
) -> None:
    identity = (
        worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    )
    active: dict[Future, threading.Event] = {}
    with ThreadPoolExecutor(
        max_workers=settings.RESEARCH_MAX_WORKERS, thread_name_prefix="durable-worker"
    ) as pool:
        try:
            while not stop.is_set():
                verify_guard()
                for future in list(active):
                    if future.done():
                        active.pop(future)
                        try:
                            future.result()
                        except Exception as exc:
                            logger.error(
                                "Durable job execution exited unexpectedly (%s)",
                                type(exc).__name__,
                            )
                try:
                    durable_jobs.expire_abandoned()
                    while (
                        len(active) < settings.RESEARCH_MAX_WORKERS
                        and not stop.is_set()
                    ):
                        verify_guard()
                        claim = durable_jobs.claim_next(identity, kinds)
                        if claim is None:
                            break
                        cancel = threading.Event()
                        future = pool.submit(durable_jobs.execute_claim, claim, cancel)
                        active[future] = cancel
                except WorkerGuardLost:
                    raise
                except Exception as exc:
                    logger.error(
                        "Worker could not claim or maintain jobs (%s)",
                        type(exc).__name__,
                    )
                stop.wait(settings.WORKER_POLL_SECONDS)
        except WorkerGuardLost:
            stop.set()
            raise
        finally:
            for cancel in active.values():
                cancel.set()
            # Cancellation is sent before waiting for active HTTP requests to
            # wind down. A guard loss never silently reacquires the session lock.


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stop.set())
    run_worker(stop)


if __name__ == "__main__":
    main()
