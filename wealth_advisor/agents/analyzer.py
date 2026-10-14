"""Analyzer Agent: metrics, anomaly detection, risk scoring, and comparison
against long-term memory."""

from __future__ import annotations

import math
from typing import Any

from ..models import DataQualityIssue, Finding
from .base import Agent, AgentContext, AgentResult, Task


def risk_score(findings: list[Finding]) -> int:
    """Saturating score in [0, 100]. One critical finding is ~33, two criticals
    plus a few highs approach 80. Order-independent and bounded."""
    total = sum(f.severity.weight for f in findings)
    return round(100 * (1 - math.exp(-total / 25)))


class AnalyzerAgent(Agent):
    name = "analyzer"
    handles = frozenset({"analyze"})

    def execute(self, task: Task, ctx: AgentContext) -> AgentResult:
        s = ctx.session
        if not s.has("profile"):
            raise self.fail("no client profile in session; fetch step must run first")
        profile, as_of = s.get("profile"), s.get("as_of")
        issues: list[DataQualityIssue] = []

        m = ctx.tools.call("analysis.metrics", profile=profile, quotes=s.get("quotes") or {}, as_of=as_of)
        if not m.ok:
            raise self.fail(f"metrics failed: {m.error}")
        metrics = m.data

        d = ctx.tools.call("analysis.detect_anomalies", profile=profile, metrics=metrics, crm=s.get("crm"), as_of=as_of)
        if not d.ok:
            raise self.fail(f"anomaly detection failed: {d.error}")
        findings: list[Finding] = d.data["findings"]
        for name, err in d.data["detector_errors"].items():
            issues.append(DataQualityIssue("analyzer", f"detector {name} failed: {err}"))

        score = risk_score(findings)
        history = self._compare_with_history(ctx, findings, score, issues)

        s.put("metrics", metrics, author=self.name)
        s.put("findings", findings, author=self.name)
        s.put("risk_score", score, author=self.name)
        s.put("history", history, author=self.name)
        s.record(
            "decision",
            author=self.name,
            what="risk_scored",
            risk_score=score,
            findings=[f.code for f in findings],
        )

        return AgentResult(
            self.name,
            task.id,
            ok=True,
            degraded=bool(d.data["detector_errors"]) or history["status"] != "ok",
            issues=issues,
            output={"risk_score": score, "finding_count": len(findings), "history": history},
        )

    def _compare_with_history(
        self, ctx: AgentContext, findings: list[Finding], score: int, issues: list[DataQualityIssue]
    ) -> dict[str, Any]:
        codes = sorted({f.code for f in findings})
        recall = ctx.tools.call("memory.recall", client_id=ctx.client_id, codes=codes)
        if not recall.ok:
            # Unknown is not the same as "first time": leave recurring=False but say so.
            issues.append(DataQualityIssue("memory", f"history unavailable ({recall.error}); recurrence unknown"))
            return {"status": "unavailable", "previous_runs": None, "trend": None}

        runs = recall.data["runs"]
        for f in findings:
            prior = recall.data["finding_history"].get(f.code) or []
            if prior:
                f.recurring = True
                f.evidence["previous_occurrences"] = len(prior)

        if not runs:
            return {"status": "ok", "previous_runs": 0, "trend": "First analysis for this client."}

        prev = runs[0]
        delta = score - prev["risk_score"]
        direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
        trend = f"Risk score {direction} from {prev['risk_score']} (run {prev['run_id']})" + (
            f" by {abs(delta)} points." if delta else "."
        )
        resolved = sorted(set(prev["finding_codes"]) - set(codes))
        return {
            "status": "ok",
            "previous_runs": len(runs),
            "previous_score": prev["risk_score"],
            "delta": delta,
            "trend": trend,
            "resolved_since_last_run": resolved,
            "backend": recall.data["backend"],
        }
