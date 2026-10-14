"""Exception hierarchy.

Tools raise ToolError subclasses; the tool executor decides whether to retry
based on `retryable`. Agents raise AgentError when they cannot produce a result.
"""


class WealthAdvisorError(Exception):
    """Base class for all errors raised by this package."""


class ToolError(WealthAdvisorError):
    retryable = False

    def __init__(self, message: str, *, tool: str | None = None):
        super().__init__(message)
        self.tool = tool


class TransientToolError(ToolError):
    """Timeouts, 5xx, rate limits. Worth retrying."""

    retryable = True


class NotFoundError(ToolError):
    """The requested record does not exist. Never retried."""


class ValidationError(ToolError):
    """Input data is malformed beyond repair."""


class ToolNotRegisteredError(ToolError):
    pass


class CircuitOpenError(ToolError):
    """The tool failed too often recently; calls are short-circuited."""


class AgentError(WealthAdvisorError):
    def __init__(self, message: str, *, agent: str):
        super().__init__(message)
        self.agent = agent
