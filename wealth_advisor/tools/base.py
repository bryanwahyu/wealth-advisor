"""Tool abstraction.

Agents never import concrete tools. They receive a `ToolExecutor` and call
tools by name. The executor owns the cross-cutting concerns every external
call needs: retries with backoff, a per-tool circuit breaker, optional fallback,
session-level caching and logging. A call never raises; it returns a
`ToolResult` the agent can inspect.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import CircuitOpenError, ToolError, ToolNotRegisteredError
from ..observability import log_event


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    error_type: str | None = None
    attempts: int = 0
    source: str = "live"  # live | cache | fallback

    @property
    def degraded(self) -> bool:
        return self.source == "fallback"


class Tool(ABC):
    name: str
    description: str
    cacheable: bool = False  # safe to reuse within one session

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Do the work. Raise a ToolError subclass on failure."""

    def fallback(self, error: ToolError, **kwargs: Any) -> Any:
        """Optional degraded answer when `run` keeps failing. Return None for 'no fallback'."""
        return None


class ToolCache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotRegisteredError(f"no tool named {name!r}", tool=name) from None

    def describe(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


@dataclass
class ToolExecutor:
    registry: ToolRegistry
    max_retries: int = 2
    backoff_seconds: float = 0.05
    failure_threshold: int = 3
    reset_after_seconds: float = 30.0
    cache: ToolCache | None = None
    _circuits: dict[str, _Circuit] = field(default_factory=dict)

    def call(self, name: str, **kwargs: Any) -> ToolResult:
        try:
            tool = self.registry.get(name)
        except ToolNotRegisteredError as exc:
            log_event("tool.unknown", str(exc), level=logging.ERROR, tool=name)
            return ToolResult(tool=name, ok=False, error=str(exc), error_type=type(exc).__name__)

        cache_key = self._cache_key(name, kwargs) if tool.cacheable and self.cache else None
        if cache_key and (hit := self.cache.get(cache_key)) is not None:  # type: ignore[union-attr]
            log_event("tool.cache_hit", f"{name} served from session cache", level=logging.DEBUG, tool=name)
            return ToolResult(tool=name, ok=True, data=hit, source="cache")

        circuit = self._circuits.setdefault(name, _Circuit())
        last_error: ToolError | None = None
        attempts = 0

        if self._is_open(circuit):
            last_error = CircuitOpenError(f"circuit open for {name}", tool=name)
            log_event("tool.circuit_open", str(last_error), level=logging.WARNING, tool=name)
        else:
            while attempts <= self.max_retries:
                attempts += 1
                start = time.perf_counter()
                try:
                    data = tool.run(**kwargs)
                except ToolError as exc:
                    last_error = exc
                    log_event(
                        "tool.error",
                        f"{name} attempt {attempts} failed: {exc}",
                        level=logging.WARNING,
                        tool=name,
                        attempt=attempts,
                        error_type=type(exc).__name__,
                        retryable=exc.retryable,
                    )
                    if not exc.retryable:
                        break
                    if attempts <= self.max_retries:
                        time.sleep(self.backoff_seconds * (2 ** (attempts - 1)))
                    continue
                except Exception as exc:  # bug inside a tool: never retry, never crash the run
                    last_error = ToolError(f"unexpected {type(exc).__name__}: {exc}", tool=name)
                    log_event("tool.crash", f"{name} raised unexpectedly", level=logging.ERROR, tool=name, error=repr(exc))
                    break
                circuit.failures = 0
                circuit.opened_at = None
                log_event(
                    "tool.call",
                    f"{name} ok",
                    level=logging.DEBUG,
                    tool=name,
                    attempt=attempts,
                    duration_ms=round((time.perf_counter() - start) * 1000, 1),
                )
                if cache_key:
                    self.cache.set(cache_key, data)  # type: ignore[union-attr]
                return ToolResult(tool=name, ok=True, data=data, attempts=attempts)

            circuit.failures += 1
            if circuit.failures >= self.failure_threshold:
                circuit.opened_at = time.monotonic()
                log_event("tool.circuit_tripped", f"{name} circuit opened", level=logging.WARNING, tool=name)

        assert last_error is not None
        fallback = self._try_fallback(tool, last_error, kwargs)
        if fallback is not None:
            return ToolResult(
                tool=name,
                ok=True,
                data=fallback,
                error=str(last_error),
                error_type=type(last_error).__name__,
                attempts=attempts,
                source="fallback",
            )
        return ToolResult(
            tool=name, ok=False, error=str(last_error), error_type=type(last_error).__name__, attempts=attempts
        )

    def _is_open(self, circuit: _Circuit) -> bool:
        if circuit.opened_at is None:
            return False
        if time.monotonic() - circuit.opened_at >= self.reset_after_seconds:
            circuit.opened_at = None  # half-open: let the next call through
            return False
        return True

    def _try_fallback(self, tool: Tool, error: ToolError, kwargs: dict[str, Any]) -> Any:
        try:
            data = tool.fallback(error, **kwargs)
        except Exception as exc:
            log_event("tool.fallback_failed", f"{tool.name} fallback raised", level=logging.ERROR, error=repr(exc))
            return None
        if data is not None:
            log_event("tool.fallback", f"{tool.name} using fallback after: {error}", level=logging.WARNING, tool=tool.name)
        return data

    @staticmethod
    def _cache_key(name: str, kwargs: dict[str, Any]) -> str:
        raw = json.dumps(kwargs, sort_keys=True, default=str)
        return f"tool:{name}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"
