from __future__ import annotations

from wealth_advisor.errors import NotFoundError, ToolError, TransientToolError
from wealth_advisor.tools.base import Tool, ToolExecutor, ToolRegistry


class FlakyTool(Tool):
    name = "flaky"
    description = "fails N times then succeeds"

    def __init__(self, failures: int, error=TransientToolError):
        self.failures = failures
        self.error = error
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error("boom")
        return {"ok": True}


class FallbackTool(FlakyTool):
    name = "with_fallback"

    def fallback(self, error, **kwargs):
        return {"from": "fallback"}


class CrashingTool(Tool):
    name = "crash"
    description = "has a bug"

    def run(self, **kwargs):
        raise KeyError("bug")


class DictCache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


def executor(*tools, **kw) -> ToolExecutor:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return ToolExecutor(reg, backoff_seconds=0, **kw)


def test_transient_error_is_retried_until_success():
    tool = FlakyTool(failures=2)
    res = executor(tool, max_retries=2).call("flaky")
    assert res.ok and res.attempts == 3 and res.source == "live"


def test_retries_exhausted_returns_failure_not_exception():
    tool = FlakyTool(failures=10)
    res = executor(tool, max_retries=2).call("flaky")
    assert not res.ok and res.attempts == 3 and res.error_type == "TransientToolError"


def test_non_retryable_error_is_not_retried():
    tool = FlakyTool(failures=10, error=NotFoundError)
    res = executor(tool, max_retries=5).call("flaky")
    assert not res.ok and tool.calls == 1 and res.error_type == "NotFoundError"


def test_fallback_used_after_failures():
    res = executor(FallbackTool(failures=10)).call("with_fallback")
    assert res.ok and res.degraded and res.data == {"from": "fallback"}


def test_circuit_opens_and_short_circuits():
    tool = FlakyTool(failures=100)
    ex = executor(tool, max_retries=0, failure_threshold=2)
    ex.call("flaky")
    ex.call("flaky")
    calls_before = tool.calls
    res = ex.call("flaky")
    assert not res.ok and res.error_type == "CircuitOpenError" and tool.calls == calls_before


def test_unexpected_exception_in_tool_is_contained():
    res = executor(CrashingTool()).call("crash")
    assert not res.ok and "KeyError" in res.error


def test_unknown_tool():
    res = executor().call("nope")
    assert not res.ok and res.error_type == "ToolNotRegisteredError"


def test_cacheable_tool_served_from_cache():
    tool = FlakyTool(failures=0)
    tool.cacheable = True
    ex = executor(tool, cache=DictCache())
    ex.call("flaky", x=1)
    res = ex.call("flaky", x=1)
    assert res.source == "cache" and tool.calls == 1


def test_tool_error_base_is_not_retryable():
    assert not ToolError("x").retryable
    assert TransientToolError("x").retryable
