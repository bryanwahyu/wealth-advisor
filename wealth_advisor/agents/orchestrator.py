"""Orchestrator Agent.

Owns the execution plan, routes each step to the agent that declares the
task kind, decides whether the run can continue after a failure, runs the
human review checkpoint, and persists the outcome.

Plan:
  fetch_client_data (required) -> analyze (required) -> advise (required)
  -> human review (when risk or actions call for it) -> persist (best effort)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..hitl import ReviewPacket, Reviewer, apply_decisions
from ..memory.long_term import RunRecord, now_ms
from ..memory.short_term import SessionMemory, SessionToolCache
from ..models import DataQualityIssue
from ..observability import agent_scope, log_event, run_id_var
from ..report import build_report
from ..tools.base import ToolExecutor
from .base import Agent, AgentContext, AgentResult, Task


@dataclass(frozen=True)
class Step:
    kind: str
    required: bool = True


DEFAULT_PLAN = (Step("fetch_client_data"), Step("analyze"), Step("advise"))


class Orchestrator:
    name = "orchestrator"

    def __init__(
        self,
        agents: list[Agent],
        tools: ToolExecutor,
        reviewer: Reviewer,
        settings: Settings,
        plan: tuple[Step, ...] = DEFAULT_PLAN,
    ):
        self.routes: dict[str, Agent] = {}
        for agent in agents:
            for kind in agent.handles:
                if kind in self.routes:
                    raise ValueError(f"task kind {kind!r} handled by both {self.routes[kind].name} and {agent.name}")
                self.routes[kind] = agent
        missing = [s.kind for s in plan if s.kind not in self.routes]
        if missing:
            raise ValueError(f"no agent registered for: {', '.join(missing)}")
        self.tools = tools
        self.reviewer = reviewer
        self.settings = settings
        self.plan = plan

    def route(self, kind: str) -> Agent:
        return self.routes[kind]

    def run(self, client_id: str) -> dict[str, Any]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        token = run_id_var.set(run_id)
        try:
            with agent_scope(self.name):
                return self._run(run_id, client_id)
        finally:
            run_id_var.reset(token)

    def _run(self, run_id: str, client_id: str) -> dict[str, Any]:
        session = SessionMemory(run_id, client_id)
        self.tools.cache = SessionToolCache(session)
        ctx = AgentContext(run_id, client_id, self.settings, self.tools, session)
        trace: list[dict[str, Any]] = []
        issues: list[DataQualityIssue] = []
        session.put("data_issues", issues, author=self.name)
        log_event("run.start", f"run started for client {client_id}", client_id=client_id)

        for step in self.plan:
            agent = self.route(step.kind)
            log_event("route", f"{step.kind} -> {agent.name}", task=step.kind, agent=agent.name)
            result = agent.handle(Task(step.kind), ctx)
            trace.append(self._trace_entry(step, result))
            issues.extend(result.issues)
            if not result.ok and step.required:
                log_event("run.aborted", f"required step {step.kind} failed: {result.error}", level=logging.ERROR)
                report = build_report(ctx, status="failed", trace=trace, review=None, memory=None,
                                      error=f"{step.kind}: {result.error}")
                self._persist_session(ctx)
                return report

        review = self._checkpoint(ctx)
        trace.append({"step": "human_review", "agent": self.reviewer.name, "ok": True, "outcome": review["outcome"]})

        status = self._status(review, trace, issues)
        memory = self._persist(ctx, status)
        trace.append({"step": "persist", "agent": self.name, "ok": memory["persisted"], "error": memory.get("error")})
        self._persist_session(ctx)

        report = build_report(ctx, status=status, trace=trace, review=review, memory=memory)
        log_event("run.done", f"run finished: {status}", status=status, risk_score=session.get("risk_score"))
        return report

    def _checkpoint(self, ctx: AgentContext) -> dict[str, Any]:
        s = ctx.session
        recs = s.get("recommendations")
        if not s.get("needs_review"):
            for r in recs:
                r.status = "approved"
                r.reviewer_note = "auto-approved: low risk, no actions need sign-off"
            s.record("decision", author=self.name, what="review_skipped")
            log_event("hitl.skipped", "no recommendation requires approval")
            return {"outcome": "not_required", "decisions": [], "reviewer": None}

        packet = ReviewPacket(
            client_id=ctx.client_id,
            client_name=s.get("profile").name,
            risk_score=s.get("risk_score"),
            findings=s.get("findings"),
            recommendations=recs,
            briefing=(s.get("briefing") or {}).get("text", ""),
        )
        log_event("hitl.requested", f"review requested from {self.reviewer.name}", reviewer=self.reviewer.name)
        try:
            decisions = self.reviewer.review(packet)
        except Exception as exc:  # a broken reviewer must not approve by accident
            log_event("hitl.error", f"reviewer failed, deferring: {exc}", level=logging.ERROR)
            decisions = None
        summary = apply_decisions(recs, decisions)
        summary["reviewer"] = self.reviewer.name
        s.record("decision", author=self.name, what="review", outcome=summary["outcome"],
                 decisions=summary.get("decisions"))
        log_event("hitl.result", f"review outcome: {summary['outcome']}", outcome=summary["outcome"],
                  unknown_ids=summary.get("unknown_ids"))
        return summary

    @staticmethod
    def _status(review: dict[str, Any], trace: list[dict[str, Any]], issues: list[DataQualityIssue]) -> str:
        if review["outcome"] in {"deferred", "partial"}:
            return "pending_review"
        if any(t.get("degraded") for t in trace) or issues:
            return "completed_with_warnings"
        return "completed"

    def _persist(self, ctx: AgentContext, status: str) -> dict[str, Any]:
        s = ctx.session
        findings = s.get("findings") or []
        briefing = s.get("briefing") or {}
        record = RunRecord(
            run_id=ctx.run_id,
            client_id=ctx.client_id,
            created_at=now_ms(),
            status=status,
            risk_score=s.get("risk_score", 0),
            finding_codes=[f.code for f in findings],
            summary=briefing.get("text", ""),
            findings=[{"code": f.code, "severity": f.severity.value, "title": f.title} for f in findings],
        )
        res = self.tools.call("memory.store_run", record=record)
        if not res.ok:
            log_event("memory.persist_failed", f"run not saved to long-term memory: {res.error}", level=logging.ERROR)
            return {"persisted": False, "error": res.error}
        return {"persisted": True, "backend": res.data["backend"]}

    def _persist_session(self, ctx: AgentContext) -> None:
        res = self.tools.call(
            "memory.store_session", session_id=ctx.run_id, client_id=ctx.client_id, events=ctx.session.events
        )
        if not res.ok:
            log_event("memory.session_not_saved", f"session trail not saved: {res.error}", level=logging.WARNING)

    @staticmethod
    def _trace_entry(step: Step, result: AgentResult) -> dict[str, Any]:
        return {
            "step": step.kind,
            "agent": result.agent,
            "ok": result.ok,
            "degraded": result.degraded,
            "error": result.error,
            "output": result.output,
        }
