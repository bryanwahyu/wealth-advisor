"""Domain models shared by agents and tools.

Plain dataclasses keep the package dependency-free. Every model exposes
`to_dict()` so reports and memory records serialize the same way.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> int:
        return {"low": 1, "medium": 3, "high": 6, "critical": 10}[self.value]

    def at_least(self, other: "Severity") -> bool:
        return self.weight >= other.weight


@dataclass
class Transaction:
    id: str
    date: date
    amount: float  # positive = inflow, negative = outflow
    category: str
    counterparty: str
    channel: str = "unknown"
    country: str = "ID"

    @property
    def is_debit(self) -> bool:
        return self.amount < 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


@dataclass
class Holding:
    symbol: str
    asset_class: str  # equity, bond, mutual_fund, crypto, cash, gold
    quantity: float
    cost_basis: float  # per unit
    last_price: float | None = None  # from client file, may be stale

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClientProfile:
    client_id: str
    name: str
    risk_profile: str  # conservative, moderate, aggressive
    monthly_income: float | None
    currency: str
    transactions: list[Transaction]
    holdings: list[Holding]
    goals: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DataQualityIssue:
    source: str
    message: str
    record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """One anomaly, trend or risk signal produced by the analyzer."""

    code: str  # stable identifier, e.g. AMOUNT_OUTLIER
    category: str  # transaction | trend | portfolio | relationship
    severity: Severity
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    transaction_ids: list[str] = field(default_factory=list)
    recurring: bool = False  # seen in a previous run (long-term memory)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class Recommendation:
    id: str
    priority: int  # 1 = most urgent
    action: str
    rationale: str
    related_findings: list[str]
    requires_approval: bool
    status: str = "proposed"  # proposed | approved | rejected | modified | pending_review
    reviewer_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
