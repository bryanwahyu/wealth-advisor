"""Agent contract.

An agent declares which task kinds it handles, receives a Task plus a shared
AgentContext, and returns an AgentResult. `handle()` wraps `execute()` so no
agent can crash the orchestrator: every exception becomes a failed result
with the error logged under the agent's name.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..errors import AgentError
from ..memory.short_term import SessionMemory
from ..models import DataQualityIssue
from ..observability import agent_scope, get_logger, log_event, timed
from ..tools.base import ToolExecutor


@dataclass
class Task:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class AgentResult:
    agent: str
    task_id: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    issues: list[DataQualityIssue] = field(default_factory=list)
    degraded: bool = False
    error: str | None = None


@dataclass
class AgentContext:
    run_id: str
    client_id: str
    settings: Settings
    tools: ToolExecutor
    session: SessionMemory


class Agent(ABC):
    name: str = "agent"
    handles: frozenset[str] = frozenset()

    def handle(self, task: Task, ctx: AgentContext) -> AgentResult:
        with agent_scope(self.name):
            ctx.session.record("task.received", author=self.name, task_id=task.id, task_kind=task.kind)
            try:
                with timed("agent.task", f"{task.kind}", task_id=task.id) as span:
                    result = self.execute(task, ctx)
                    span.update(ok=result.ok, degraded=result.degraded, issues=len(result.issues))
            except AgentError as exc:
                log_event("agent.failed", str(exc), level=logging.ERROR, task=task.kind)
                result = AgentResult(self.name, task.id, ok=False, error=str(exc))
            except Exception as exc:
                get_logger().exception("unhandled error in agent", extra={"event": "agent.crash", "data": {"task": task.kind}})
                result = AgentResult(self.name, task.id, ok=False, error=f"internal error: {type(exc).__name__}: {exc}")
            ctx.session.record("task.completed", author=self.name, task_id=task.id, ok=result.ok, error=result.error)
            return result

    @abstractmethod
    def execute(self, task: Task, ctx: AgentContext) -> AgentResult: ...

    def fail(self, message: str) -> AgentError:
        return AgentError(message, agent=self.name)
