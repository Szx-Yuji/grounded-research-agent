"""User-safe execution outcomes shared by nodes and the worker entrypoint."""


class ResearchExecutionError(RuntimeError):
    """The research graph could not produce a grounded report."""


class ResearchCancelled(ResearchExecutionError):
    """The caller requested cancellation of the research run."""
