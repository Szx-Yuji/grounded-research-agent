"""Trace actual SDK request boundaries, including streaming and embedding batches."""

from types import SimpleNamespace
from typing import Any

from app.research.events import check_cancelled
from app.runtime.context import (
    current_context,
    provider_call,
    request_fingerprint,
    request_timeout,
)


class TracedStream:
    def __init__(self, stream, call):
        self.stream, self.call = stream, call

    def __enter__(self):
        return self

    def __iter__(self):
        for chunk in self.stream:
            check_cancelled()
            context = current_context()
            if context:
                context.remaining()
            self.call.usage(getattr(chunk, "usage", None))
            if getattr(chunk, "choices", None) and getattr(
                chunk.choices[0].delta, "content", None
            ):
                self.call.first_token()
            yield chunk
        self.call.succeed()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.stream.close()
        except BaseException as close_error:
            self.call.__exit__(
                type(close_error), close_error, close_error.__traceback__
            )
            raise
        return self.call.__exit__(exc_type, exc, tb)


class CompletionGateway:
    def __init__(self, client):
        self.client = client

    def create(self, **kwargs):
        tokens = kwargs.get("max_tokens", kwargs.get("max_completion_tokens", 4096))
        if "max_tokens" not in kwargs and "max_completion_tokens" not in kwargs:
            kwargs["max_tokens"] = tokens
        call = provider_call(
            "model",
            operation="chat.completions",
            model=kwargs.get("model"),
            max_output_tokens=tokens,
            request_key=request_fingerprint("model", kwargs),
        )
        call.__enter__()
        try:
            kwargs["timeout"] = request_timeout(60)
            if kwargs.get("stream"):
                kwargs["stream_options"] = {"include_usage": True}
            response = self.client.chat.completions.create(**kwargs)
        except BaseException as exc:
            call.__exit__(type(exc), exc, exc.__traceback__)
            raise
        if kwargs.get("stream"):
            return TracedStream(response, call)
        try:
            call.succeed(usage=getattr(response, "usage", None))
            return response
        finally:
            call.__exit__(None, None, None)


class ModelGateway:
    def __init__(self, client):
        self.client = client
        self.chat = SimpleNamespace(completions=CompletionGateway(client))

    def close(self):
        self.client.close()


class EmbeddingGateway:
    """Decorate OpenAIEmbeddings.client, not a higher-level batch method."""

    def __init__(self, client, model: str):
        self.client, self.model = client, model

    def create(self, **kwargs) -> Any:
        with provider_call(
            "embedding",
            operation="embeddings.create",
            model=self.model,
            request_key=request_fingerprint("embedding", kwargs),
        ) as call:
            kwargs["timeout"] = request_timeout(30)
            response = self.client.create(**kwargs)
            usage = (
                response.get("usage")
                if isinstance(response, dict)
                else getattr(response, "usage", None)
            )
            call.succeed(usage=usage)
            return response
