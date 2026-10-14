"""Human-in-the-loop checkpoint.

Runs after recommendations are drafted and before anything is persisted as
final. A reviewer returns a decision per recommendation, or None to defer the
whole run (the report is then saved as `pending_review`).

Reviewers:
  auto         approve everything (demos, CI). Logged as auto-approval.
  defer        never decide; park the run for a human.
  interactive  ask on the terminal.
  file         read decisions from a JSON file (for async review / overrides).
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from .models import Finding, Recommendation

DECISIONS = {"approve", "reject", "modify"}


@dataclass
class ReviewDecision:
    recommendation_id: str
    decision: str  # approve | reject | modify
    note: str | None = None
    new_action: str | None = None
    reviewer: str = "unknown"


@dataclass
class ReviewPacket:
    client_id: str
    client_name: str
    risk_score: int
    findings: list[Finding]
    recommendations: list[Recommendation]
    briefing: str


class Reviewer(ABC):
    name: str = "reviewer"

    @abstractmethod
    def review(self, packet: ReviewPacket) -> list[ReviewDecision] | None: ...


class AutoApproveReviewer(Reviewer):
    name = "auto"

    def review(self, packet: ReviewPacket) -> list[ReviewDecision]:
        return [
            ReviewDecision(r.id, "approve", note="auto-approved (no human reviewer configured)", reviewer=self.name)
            for r in packet.recommendations
        ]


class DeferReviewer(Reviewer):
    name = "defer"

    def review(self, packet: ReviewPacket) -> None:
        return None


class FileReviewer(Reviewer):
    """Decisions file format:
    {"reviewer": "jane.advisor", "decisions": [
        {"id": "REC-LARGE_OUTFLOW", "decision": "modify", "action": "...", "note": "..."}]}
    Recommendations not mentioned stay pending."""

    name = "file"

    def __init__(self, path: Path):
        self.path = path

    def review(self, packet: ReviewPacket) -> list[ReviewDecision] | None:
        if not self.path.exists():
            return None
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        who = doc.get("reviewer", "file")
        out = []
        for d in doc.get("decisions", []):
            decision = str(d.get("decision", "")).lower()
            if decision not in DECISIONS:
                continue
            out.append(ReviewDecision(d.get("id", ""), decision, d.get("note"), d.get("action"), reviewer=who))
        return out


class InteractiveReviewer(Reviewer):
    name = "interactive"

    def __init__(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stderr,
                 ask: Callable[[str], str] | None = None):
        self.stdin, self.stdout = stdin, stdout
        self._ask = ask

    def ask(self, prompt: str) -> str:
        if self._ask:
            return self._ask(prompt)
        self.stdout.write(prompt)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            raise EOFError
        return line.strip()

    def review(self, packet: ReviewPacket) -> list[ReviewDecision] | None:
        w = self.stdout.write
        w(f"\n=== Review required: {packet.client_name} ({packet.client_id}) risk {packet.risk_score}/100 ===\n")
        w(packet.briefing + "\n\n")
        decisions: list[ReviewDecision] = []
        try:
            for r in packet.recommendations:
                if not r.requires_approval:
                    decisions.append(ReviewDecision(r.id, "approve", note="low-risk, approved with batch", reviewer="interactive"))
                    continue
                w(f"[P{r.priority}] {r.id}\n  action: {r.action}\n  why:    {r.rationale}\n")
                while True:
                    ans = self.ask("  (a)pprove / (r)eject / (m)odify / (d)efer run? ").lower()[:1]
                    if ans in {"a", "r", "m", "d"}:
                        break
                if ans == "d":
                    return None
                if ans == "m":
                    new_action = self.ask("  new action: ") or r.action
                    note = self.ask("  note: ") or None
                    decisions.append(ReviewDecision(r.id, "modify", note, new_action, reviewer="interactive"))
                else:
                    note = self.ask("  note (optional): ") or None
                    decisions.append(ReviewDecision(r.id, "approve" if ans == "a" else "reject", note, reviewer="interactive"))
        except EOFError:
            w("\n(no input available; deferring review)\n")
            return None
        return decisions


def apply_decisions(recs: list[Recommendation], decisions: list[ReviewDecision] | None) -> dict[str, Any]:
    """Mutates recommendation statuses; returns a review summary for the report."""
    if decisions is None:
        for r in recs:
            r.status = "pending_review"
        return {"outcome": "deferred", "decisions": [], "unknown_ids": []}

    by_id = {r.id: r for r in recs}
    unknown = []
    applied = []
    for d in decisions:
        rec = by_id.get(d.recommendation_id)
        if rec is None:
            unknown.append(d.recommendation_id)
            continue
        if d.decision == "approve":
            rec.status = "approved"
        elif d.decision == "reject":
            rec.status = "rejected"
        elif d.decision == "modify":
            rec.status = "modified"
            if d.new_action:
                rec.action = d.new_action
        rec.reviewer_note = d.note
        applied.append({"id": rec.id, "decision": d.decision, "reviewer": d.reviewer, "note": d.note})

    for r in recs:
        if r.status == "proposed":
            r.status = "pending_review"
    pending = [r.id for r in recs if r.status == "pending_review"]
    return {
        "outcome": "partial" if pending else "reviewed",
        "decisions": applied,
        "pending": pending,
        "unknown_ids": unknown,
    }
