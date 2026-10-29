"""Real gateway boundaries with deterministic providers; no credentials/network."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from app.runtime.context import (
    BudgetExceeded,
    ExecutionLimits,
    execution_context,
    provider_call,
)
from app.runtime.gateway import EmbeddingGateway, ModelGateway


def client(create):
    return ModelGateway(
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )


def test_nested_calls_share_atomic_budget_and_output_reservations():
    called = []
    gateway = client(
        lambda **kwargs: called.append(kwargs) or SimpleNamespace(usage=None)
    )
    with execution_context(
        limits=ExecutionLimits(model=2, output_tokens=100)
    ) as context:
        with provider_call("tool", operation="research_topic") as delegate:
            gateway.chat.completions.create(model="fixture", max_tokens=60)
            with execution_context(limits=ExecutionLimits(model=999)) as inner:
                assert inner is context
                with pytest.raises(BudgetExceeded, match="output-token"):
                    gateway.chat.completions.create(model="fixture", max_tokens=60)
            gateway.chat.completions.create(model="fixture", max_tokens=40)
            with pytest.raises(BudgetExceeded, match="model call"):
                gateway.chat.completions.create(model="fixture", max_tokens=1)
            delegate.succeed()
        assert len(called) == 2
        assert context.reserved_tokens == 100
        assert all(
            row["parent_id"] == delegate.record["id"]
            for row in context.calls
            if row["kind"] == "model"
        )


def test_parallel_searches_cannot_overrun_shared_budget():
    def search():
        try:
            with provider_call("search", operation="search") as call:
                call.succeed()
            return True
        except BudgetExceeded:
            return False

    with execution_context(limits=ExecutionLimits(search=3)) as context:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [pool.submit(copy_context().run, search) for _ in range(20)]
        assert sum(future.result() for future in results) == 3
        assert context.counts["search"] == 3


class Stream:
    def __init__(self):
        self.closed = False

    def __iter__(self):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="private answer"))],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=19, completion_tokens=7)
        )

    def close(self):
        self.closed = True


def test_stream_captures_usage_ttft_and_no_prompt_text():
    raw = Stream()
    observed = []
    recorded = []
    gateway = client(lambda **kwargs: observed.append(kwargs) or raw)
    with execution_context(
        limits=ExecutionLimits(),
        sink=lambda row, starting: recorded.append((row, starting)),
    ) as context:
        with gateway.chat.completions.create(
            model="fixture",
            messages=[{"role": "user", "content": "private prompt"}],
            max_tokens=20,
            stream=True,
        ) as stream:
            list(stream)
        row = context.calls[0]
        assert row["status"] == "succeeded"
        assert row["input_tokens"] == 19 and row["output_tokens"] == 7
        assert row["first_token_ms"] is not None and raw.closed
    assert [starting for _, starting in recorded] == [True, False]
    assert observed[0]["stream_options"] == {"include_usage": True}
    assert "private prompt" not in str(recorded) and "private answer" not in str(
        recorded
    )


def test_partial_stream_and_timeout_outcomes_stay_unknown_without_retry():
    raw = Stream()
    gateway = client(lambda **kwargs: raw)
    with execution_context(limits=ExecutionLimits()) as context:
        with gateway.chat.completions.create(stream=True, max_tokens=20) as stream:
            next(iter(stream))
        assert context.calls[0]["status"] == "unknown"
        assert context.calls[0]["output_tokens"] is None
    dispatches = []

    def timeout(**kwargs):
        dispatches.append(kwargs)
        raise TimeoutError("secret-vendor-request")

    with execution_context(limits=ExecutionLimits()) as context:
        with pytest.raises(TimeoutError):
            client(timeout).chat.completions.create(max_tokens=20)
        assert len(dispatches) == 1
        assert context.calls[0]["status"] == "unknown"
        assert "secret-vendor-request" not in str(context.calls)


def test_embedding_actual_batch_requests_are_counted_and_bounded():
    dispatches = []
    gateway = EmbeddingGateway(
        SimpleNamespace(
            create=lambda **kwargs: (
                dispatches.append(kwargs) or {"usage": {"prompt_tokens": 8}}
            )
        ),
        "embedding-fixture",
    )
    with execution_context(limits=ExecutionLimits(embedding=1)) as context:
        gateway.create(input=["a", "b"])
        with pytest.raises(BudgetExceeded):
            gateway.create(input=["c"])
        assert len(dispatches) == 1
        assert context.calls[0]["input_tokens"] == 8
        assert 0 < dispatches[0]["timeout"] <= 30


def test_deadline_and_unpersisted_intents_block_dispatch():
    with execution_context(limits=ExecutionLimits(seconds=-1)):
        with pytest.raises(BudgetExceeded, match="deadline"):
            client(
                lambda **kwargs: pytest.fail("deadline must prevent dispatch")
            ).chat.completions.create(max_tokens=1)

    def failed_sink(_row, _starting):
        raise OSError("database down")

    with execution_context(limits=ExecutionLimits(), sink=failed_sink):
        with pytest.raises(OSError):
            client(
                lambda **kwargs: pytest.fail("unrecorded request must not dispatch")
            ).chat.completions.create(max_tokens=1)


def test_missing_usage_is_explicitly_unknown_and_context_restored():
    from app.runtime.context import current_context

    with execution_context(limits=ExecutionLimits()) as context:
        client(lambda **kwargs: SimpleNamespace(usage=None)).chat.completions.create(
            max_tokens=1
        )
        assert context.calls[0]["input_tokens"] is None
        assert context.calls[0]["output_tokens"] is None
    assert current_context() is None


def test_unknown_call_cannot_be_replayed_inside_same_agent_turn():
    dispatched = []

    def timeout(**kwargs):
        dispatched.append(kwargs)
        raise TimeoutError("private-vendor-error")

    gateway = client(timeout)
    with execution_context(limits=ExecutionLimits()) as context:
        with pytest.raises(TimeoutError):
            gateway.chat.completions.create(
                model="fixture",
                max_tokens=15,
                messages=[{"role": "user", "content": "private"}],
            )
        with pytest.raises(BudgetExceeded, match="unresolved outcome"):
            gateway.chat.completions.create(
                model="fixture",
                max_tokens=15,
                messages=[{"role": "user", "content": "private"}],
            )
        assert len(dispatched) == 1 and len(context.calls) == 1
        assert "private" not in str(context.calls)
