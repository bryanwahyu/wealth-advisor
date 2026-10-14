"""Analysis tools wrapping the pure analytics functions."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..analytics.detectors import DETECTORS, DetectionInput
from ..analytics.metrics import compute_metrics
from ..config import AnomalyThresholds, RiskPolicy
from ..models import ClientProfile, Finding
from .base import Tool

# When the same transaction is flagged by several detectors, keep the most
# specific explanation. A generic statistical outlier adds nothing once the
# transaction is already flagged as structuring or a large new counterparty.
_SUPERSEDED_BY = {
    "AMOUNT_OUTLIER": {"POSSIBLE_STRUCTURING", "LARGE_OUTFLOW"},
    "LARGE_OUTFLOW": {"POSSIBLE_STRUCTURING"},
}


class MetricsTool(Tool):
    name = "analysis.metrics"
    description = "Compute cash-flow, savings and portfolio metrics for a client."

    def run(self, profile: ClientProfile, quotes: dict[str, Any], as_of: date) -> dict[str, Any]:
        return compute_metrics(profile, quotes, as_of)


class AnomalyDetectionTool(Tool):
    name = "analysis.detect_anomalies"
    description = "Run all anomaly, trend and risk-signal detectors; returns findings and per-detector errors."

    def __init__(self, thresholds: AnomalyThresholds, policy: RiskPolicy):
        self.thresholds = thresholds
        self.policy = policy

    def run(
        self, profile: ClientProfile, metrics: dict[str, Any], crm: dict[str, Any] | None, as_of: date
    ) -> dict[str, Any]:
        inp = DetectionInput(profile, metrics, crm, as_of, self.thresholds, self.policy)
        findings: list[Finding] = []
        errors: dict[str, str] = {}
        for name, detector in DETECTORS.items():
            try:
                findings.extend(detector(inp))
            except Exception as exc:  # isolate detector bugs from each other
                errors[name] = f"{type(exc).__name__}: {exc}"
        findings = _dedupe(findings)
        findings.sort(key=lambda f: (-f.severity.weight, f.code))
        return {"findings": findings, "detector_errors": errors, "detectors_run": list(DETECTORS)}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    flagged: dict[str, set[str]] = {}
    for f in findings:
        for tid in f.transaction_ids:
            flagged.setdefault(tid, set()).add(f.code)
    out = []
    for f in findings:
        stronger = _SUPERSEDED_BY.get(f.code)
        if stronger and f.transaction_ids and all(flagged[t] & stronger for t in f.transaction_ids):
            continue
        out.append(f)
    return out
