"""Exceptions whose messages may be exposed to the conversation owner."""


class AssistantExecutionError(RuntimeError):
    """The assistant could not complete this message."""
