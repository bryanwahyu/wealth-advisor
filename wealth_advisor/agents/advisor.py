"""Advisor Agent: turns findings into prioritized, structured recommendations
and a short briefing.

Recommendations are rule-based so they are reproducible and auditable. The
LLM (if enabled) only phrases the briefing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from ..models import Finding, Recommendation, Severity
from .base import Agent, AgentContext, AgentResult, Task

REVIEW_RISK_SCORE = 60


@dataclass(frozen=True)
class Playbook:
    priority: int
    requires_approval: bool
    action: Callable[[list[Finding], dict[str, Any]], str]


def _txn_ids(fs: list[Finding]) -> str:
    ids = [t for f in fs for t in f.transaction_ids]
    return ", ".join(ids[:6]) + (" …" if len(ids) > 6 else "")


PLAYBOOKS: dict[str, Playbook] = {
    "POSSIBLE_STRUCTURING": Playbook(
        1, True, lambda fs, _: f"Escalate transactions {_txn_ids(fs)} to compliance for suspicious-transaction review; "
        "hold further large transfers until cleared"
    ),
    "KYC_EXPIRED": Playbook(1, True, lambda fs, _: "Suspend new advisory transactions and complete KYC refresh"),
    "LARGE_OUTFLOW": Playbook(
        2, True, lambda fs, _: f"Call client to verify purpose and beneficiary of large outflow(s) {_txn_ids(fs)}"
    ),
    "CRYPTO_OVEREXPOSURE": Playbook(
        2, True, lambda fs, _: f"Propose reducing crypto to within the {fs[0].evidence['limit']:.0%} policy limit, "
        "or re-assess the client's risk profile"
    ),
    "RISK_PROFILE_MISMATCH": Playbook(
        2, True, lambda fs, _: f"Propose rebalancing equity/crypto from {fs[0].evidence['risky_share']:.0%} "
        f"to at most {fs[0].evidence['limit']:.0%}, or re-assess the risk profile"
    ),
    "CONCENTRATION": Playbook(
        2, True, lambda fs, _: "Propose trimming " + ", ".join(f.evidence["symbol"] for f in fs)
        + " toward the single-position limit"
    ),
    "HIGH_VELOCITY": Playbook(
        2, False, lambda fs, _: f"Verify transaction burst {_txn_ids(fs)} with client (possible account compromise)"
    ),
    "OPEN_COMPLAINT": Playbook(2, False, lambda fs, _: "Resolve open complaint before any product conversation"),
    "LOW_LIQUIDITY": Playbook(
        2, False, lambda fs, m: f"Build emergency fund to {fs[0].evidence['target']:.0f} months of spending "
        f"(about {m.get('avg_monthly_spending', 0) * fs[0].evidence['target']:,.0f})"
    ),
    "NEGATIVE_CASHFLOW": Playbook(2, False, lambda fs, _: "Review budget with client; spending has exceeded income"),
    "SPENDING_SPIKE": Playbook(3, False, lambda fs, _: f"Discuss {fs[0].evidence['month']} spending increase with client"),
    "AMOUNT_OUTLIER": Playbook(3, False, lambda fs, _: f"Confirm {len(fs)} unusual payment(s) with client: {_txn_ids(fs)}"),
    "DRAWDOWN": Playbook(
        3, False, lambda fs, _: "Review investment thesis for " + ", ".join(f.evidence["symbol"] for f in fs)
    ),
    "KYC_EXPIRING": Playbook(3, False, lambda fs, _: f"Schedule KYC renewal before {fs[0].evidence['kyc_expiry']}"),
    "STALE_RELATIONSHIP": Playbook(4, False, lambda fs, _: "Schedule a periodic portfolio review meeting"),
}


class AdvisorAgent(Agent):
    name = "advisor"
    handles = frozenset({"advise"})

    def execute(self, task: Task, ctx: AgentContext) -> AgentResult:
        s = ctx.session
        if not s.has("findings"):
            raise self.fail("no findings in session; analyze step must run first")
        findings: list[Finding] = s.get("findings")
        metrics: dict[str, Any] = s.get("metrics")
        score: int = s.get("risk_score")

        recs = self.build_recommendations(findings, metrics)
        needs_review = score >= REVIEW_RISK_SCORE or any(r.requires_approval for r in recs)

        facts = self._facts(ctx, findings, recs, score)
        narrative = ctx.tools.call("llm.summarize", facts=facts)
        briefing = narrative.data if narrative.ok else {"text": "", "source": "none"}

        s.put("recommendations", recs, author=self.name)
        s.put("briefing", briefing, author=self.name)
        s.put("needs_review", needs_review, author=self.name)
        s.record("decision", author=self.name, what="review_required" if needs_review else "auto_eligible",
                 risk_score=score, approvals=[r.id for r in recs if r.requires_approval])

        return AgentResult(
            self.name,
            task.id,
            ok=True,
            degraded=not narrative.ok or narrative.degraded,
            output={"recommendations": len(recs), "needs_review": needs_review, "briefing_source": briefing["source"]},
        )

    @staticmethod
    def build_recommendations(findings: list[Finding], metrics: dict[str, Any]) -> list[Recommendation]:
        grouped: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            if f.code in PLAYBOOKS:
                grouped[f.code].append(f)

        recs = []
        for code, fs in grouped.items():
            pb = PLAYBOOKS[code]
            priority = pb.priority
            worst = max(fs, key=lambda f: f.severity.weight)
            if worst.severity == Severity.CRITICAL:
                priority = 1
            recurring = any(f.recurring for f in fs)
            if recurring:
                priority = max(1, priority - 1)  # unresolved since last review: more urgent
            rationale = "; ".join(f.title for f in fs[:3])
            if recurring:
                rationale += " (recurring: also flagged in a previous run)"
            recs.append(
                Recommendation(
                    id=f"REC-{code}",
                    priority=priority,
                    action=pb.action(fs, metrics),
                    rationale=rationale,
                    related_findings=[code],
                    requires_approval=pb.requires_approval or worst.severity == Severity.CRITICAL,
                )
            )
        recs.sort(key=lambda r: (r.priority, not r.requires_approval, r.id))
        return recs

    @staticmethod
    def _facts(ctx: AgentContext, findings: list[Finding], recs: list[Recommendation], score: int) -> dict[str, Any]:
        profile = ctx.session.get("profile")
        counts: dict[str, int] = defaultdict(int)
        for f in findings:
            counts[f.severity.value] += 1
        history = ctx.session.get("history") or {}
        return {
            "client": {"name": profile.name, "risk_profile": profile.risk_profile, "currency": profile.currency},
            "risk_score": score,
            "risk_trend": history.get("trend"),
            "severity_counts": dict(counts),
            "findings": [{"code": f.code, "severity": f.severity.value, "title": f.title, "detail": f.detail}
                         for f in findings[:12]],
            "recommendations": [{"priority": r.priority, "action": r.action} for r in recs[:6]],
            "data_gaps": [i.message for i in ctx.session.get("data_issues", [])],
        }
