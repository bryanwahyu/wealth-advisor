"""Anomaly, trend and risk-signal detectors.

Each detector is a pure function `(DetectionInput) -> list[Finding]`. They are
registered in DETECTORS so the analyzer runs all of them and one failing
detector does not hide the others.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from ..config import AnomalyThresholds, RiskPolicy
from ..models import ClientProfile, Finding, Severity, Transaction
from .metrics import NON_SPENDING_CATEGORIES, RISKY_ASSET_CLASSES


@dataclass
class DetectionInput:
    profile: ClientProfile
    metrics: dict[str, Any]
    crm: dict[str, Any] | None
    as_of: date
    thresholds: AnomalyThresholds
    policy: RiskPolicy


def _fmt(amount: float, currency: str) -> str:
    return f"{currency} {abs(amount):,.0f}"


def _spending(transactions: list[Transaction]) -> list[Transaction]:
    return [t for t in transactions if t.is_debit and t.category not in NON_SPENDING_CATEGORIES]


def recurring_payees(transactions: list[Transaction], min_months: int, tolerance: float = 0.1) -> set[str]:
    """Payees paid a similar amount in at least `min_months` distinct months
    (rent, school fees, insurance). Big but expected."""
    by_payee: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.is_debit:
            by_payee[t.counterparty].append(t)
    out = set()
    for payee, txs in by_payee.items():
        med = statistics.median(-t.amount for t in txs)
        similar_months = {t.date.strftime("%Y-%m") for t in txs if abs(-t.amount - med) <= tolerance * med}
        if len(similar_months) >= min_months:
            out.add(payee)
    return out


# --- transaction level -------------------------------------------------------


def amount_outliers(inp: DetectionInput) -> list[Finding]:
    """Modified z-score (median/MAD) per category. Robust to the outliers it is
    looking for, unlike mean/stddev. Categories with little history fall back to
    the client's whole spending distribution."""
    th = inp.thresholds
    debits = _spending(inp.profile.transactions)
    if len(debits) < th.min_history:
        return []
    by_cat: dict[str, list[float]] = defaultdict(list)
    for t in debits:
        by_cat[t.category].append(-t.amount)
    all_amounts = [-t.amount for t in debits]

    def stats(values: list[float]) -> tuple[float, float] | None:
        med = statistics.median(values)
        mad = statistics.median([abs(v - med) for v in values])
        return (med, mad) if mad > 0 else None

    global_stats = stats(all_amounts)
    recurring = recurring_payees(inp.profile.transactions, th.recurring_min_months)
    findings = []
    for t in debits:
        if t.counterparty in recurring:
            continue
        values = by_cat[t.category]
        s = stats(values) if len(values) >= th.min_history else global_stats
        if s is None:
            continue
        med, mad = s
        z = 0.6745 * (-t.amount - med) / mad
        if z < th.robust_z or -t.amount < med * th.min_outlier_multiple:
            continue
        severity = Severity.HIGH if z >= th.robust_z * 3 else Severity.MEDIUM
        findings.append(
            Finding(
                code="AMOUNT_OUTLIER",
                category="transaction",
                severity=severity,
                title=f"Unusually large {t.category} payment",
                detail=(
                    f"{_fmt(t.amount, inp.profile.currency)} to {t.counterparty} on {t.date} "
                    f"is {z:.1f} robust z-scores above the typical {_fmt(med, inp.profile.currency)}."
                ),
                evidence={"robust_z": round(z, 2), "median": med, "mad": mad},
                transaction_ids=[t.id],
            )
        )
    return findings


def large_new_counterparty(inp: DetectionInput) -> list[Finding]:
    """Outflows that are large relative to income, escalated when the
    counterparty is new or abroad."""
    income = inp.profile.monthly_income
    if not income:
        return []
    th = inp.thresholds
    seen_before: dict[str, date] = {}
    findings = []
    for t in inp.profile.transactions:
        first_seen = seen_before.setdefault(t.counterparty, t.date)
        if not t.is_debit or t.category in NON_SPENDING_CATEGORIES:
            continue
        ratio = -t.amount / income
        if ratio < th.large_vs_income_ratio:
            continue
        is_new = first_seen == t.date
        foreign = t.country.upper() != "ID"
        severity = Severity.MEDIUM
        if is_new and foreign:
            severity = Severity.CRITICAL
        elif is_new or foreign:
            severity = Severity.HIGH
        flags = [f for f, on in (("new counterparty", is_new), (f"cross-border ({t.country})", foreign)) if on]
        findings.append(
            Finding(
                code="LARGE_OUTFLOW",
                category="transaction",
                severity=severity,
                title="Large outflow" + (f": {', '.join(flags)}" if flags else ""),
                detail=(
                    f"{_fmt(t.amount, inp.profile.currency)} to {t.counterparty} on {t.date} equals "
                    f"{ratio:.1f}x monthly income."
                ),
                evidence={"income_ratio": round(ratio, 2), "new_counterparty": is_new, "country": t.country},
                transaction_ids=[t.id],
            )
        )
    return findings


def structuring(inp: DetectionInput) -> list[Finding]:
    """Several transfers just under the reporting threshold within a short window."""
    th = inp.thresholds
    low = th.reporting_threshold * (1 - th.structuring_band)
    near = [t for t in inp.profile.transactions if t.is_debit and low <= -t.amount < th.reporting_threshold]
    findings: list[Finding] = []
    used: set[str] = set()
    for i, anchor in enumerate(near):
        if anchor.id in used:
            continue
        window = [
            t for t in near[i:] if t.date - anchor.date <= timedelta(days=th.structuring_window_days)
        ]
        if len(window) < th.structuring_min_count:
            continue
        used.update(t.id for t in window)
        total = -sum(t.amount for t in window)
        findings.append(
            Finding(
                code="POSSIBLE_STRUCTURING",
                category="transaction",
                severity=Severity.CRITICAL,
                title="Transfers clustered just below reporting threshold",
                detail=(
                    f"{len(window)} transfers between {window[0].date} and {window[-1].date} each sit within "
                    f"{th.structuring_band:.0%} below {_fmt(th.reporting_threshold, inp.profile.currency)} "
                    f"(total {_fmt(total, inp.profile.currency)}). Escalate to compliance."
                ),
                evidence={"count": len(window), "total": total, "counterparties": sorted({t.counterparty for t in window})},
                transaction_ids=[t.id for t in window],
            )
        )
    return findings


def velocity(inp: DetectionInput) -> list[Finding]:
    per_day = Counter(t.date for t in inp.profile.transactions if t.is_debit)
    findings = []
    for day, count in sorted(per_day.items()):
        if count <= inp.thresholds.velocity_per_day:
            continue
        txs = [t for t in inp.profile.transactions if t.is_debit and t.date == day]
        findings.append(
            Finding(
                code="HIGH_VELOCITY",
                category="transaction",
                severity=Severity.MEDIUM,
                title="Burst of outgoing transactions",
                detail=f"{count} outflows on {day} (limit {inp.thresholds.velocity_per_day}), "
                f"total {_fmt(sum(t.amount for t in txs), inp.profile.currency)}.",
                evidence={"count": count, "channels": sorted({t.channel for t in txs})},
                transaction_ids=[t.id for t in txs],
            )
        )
    return findings


# --- trends ------------------------------------------------------------------


def spending_spike(inp: DetectionInput) -> list[Finding]:
    months = inp.metrics["complete_months"]
    if len(months) < 3:
        return []
    cf = inp.metrics["monthly_cashflow"]
    last, prior = months[-1], months[:-1]
    baseline = statistics.mean(cf[m]["spending"] for m in prior)
    if baseline <= 0:
        return []
    ratio = cf[last]["spending"] / baseline
    if ratio < inp.thresholds.spending_spike_ratio:
        return []
    return [
        Finding(
            code="SPENDING_SPIKE",
            category="trend",
            severity=Severity.HIGH if ratio >= 2 else Severity.MEDIUM,
            title=f"Spending in {last} up {ratio - 1:.0%} vs trailing average",
            detail=(
                f"{_fmt(cf[last]['spending'], inp.profile.currency)} spent in {last} against an average of "
                f"{_fmt(baseline, inp.profile.currency)} over {len(prior)} prior months."
            ),
            evidence={"month": last, "ratio": round(ratio, 2), "baseline": round(baseline, 2)},
        )
    ]


def negative_cashflow(inp: DetectionInput) -> list[Finding]:
    n = inp.thresholds.negative_cashflow_months
    months = inp.metrics["complete_months"][-n:]
    if len(months) < n:
        return []
    cf = inp.metrics["monthly_cashflow"]
    # Investing is not burn: use inflow - spending.
    nets = [cf[m]["inflow"] - cf[m]["spending"] for m in months]
    if any(v >= 0 for v in nets):
        return []
    return [
        Finding(
            code="NEGATIVE_CASHFLOW",
            category="trend",
            severity=Severity.HIGH,
            title=f"Spending exceeded income for {n} consecutive months",
            detail=f"Net cash flow {', '.join(_fmt(v, inp.profile.currency) for v in nets)} deficit in {', '.join(months)}.",
            evidence={"months": months, "net": nets},
        )
    ]


# --- portfolio ---------------------------------------------------------------


def concentration(inp: DetectionInput) -> list[Finding]:
    """Single-issuer risk. Diversified funds, bonds and cash are excluded."""
    limit = inp.thresholds.concentration_limit
    findings = []
    for p in inp.metrics["positions"]:
        if p["asset_class"] not in RISKY_ASSET_CLASSES or p["weight"] <= limit:
            continue
        findings.append(
            Finding(
                code="CONCENTRATION",
                category="portfolio",
                severity=Severity.HIGH if p["weight"] > limit * 1.5 else Severity.MEDIUM,
                title=f"{p['symbol']} is {p['weight']:.0%} of the portfolio",
                detail=f"Single-position limit is {limit:.0%}.",
                evidence={"symbol": p["symbol"], "weight": p["weight"]},
            )
        )
    return findings


def risk_profile_mismatch(inp: DetectionInput) -> list[Finding]:
    pf = inp.metrics["portfolio"]
    if not pf:
        return []
    rp = inp.profile.risk_profile
    max_risky = inp.policy.max_risky_share.get(rp)
    max_crypto = inp.policy.max_crypto_share.get(rp)
    findings = []
    if max_risky is not None and pf["risky_share"] > max_risky:
        findings.append(
            Finding(
                code="RISK_PROFILE_MISMATCH",
                category="portfolio",
                severity=Severity.HIGH,
                title=f"Risky assets {pf['risky_share']:.0%} exceed {rp} limit {max_risky:.0%}",
                detail="Equity and crypto exposure is above what the client's risk profile allows.",
                evidence={"risky_share": pf["risky_share"], "limit": max_risky},
            )
        )
    if max_crypto is not None and pf["crypto_share"] > max_crypto:
        findings.append(
            Finding(
                code="CRYPTO_OVEREXPOSURE",
                category="portfolio",
                severity=Severity.HIGH,
                title=f"Crypto {pf['crypto_share']:.0%} exceeds {rp} limit {max_crypto:.0%}",
                detail="Crypto allocation is outside policy for this risk profile.",
                evidence={"crypto_share": pf["crypto_share"], "limit": max_crypto},
            )
        )
    return findings


def drawdown(inp: DetectionInput) -> list[Finding]:
    findings = []
    for p in inp.metrics["positions"]:
        chg = p.get("change_30d")
        if chg is None or chg > inp.thresholds.drawdown_alert or p["weight"] < 0.05:
            continue
        findings.append(
            Finding(
                code="DRAWDOWN",
                category="portfolio",
                severity=Severity.MEDIUM,
                title=f"{p['symbol']} down {abs(chg):.0%} in 30 days",
                detail=f"Position weight {p['weight']:.0%}; unrealized P/L {p['unrealized_pl']:,.0f}.",
                evidence={"symbol": p["symbol"], "change_30d": chg, "weight": p["weight"]},
            )
        )
    return findings


def liquidity(inp: DetectionInput) -> list[Finding]:
    months = inp.metrics["emergency_fund_months"]
    target = inp.thresholds.emergency_fund_months
    if months is None or months >= target:
        return []
    return [
        Finding(
            code="LOW_LIQUIDITY",
            category="portfolio",
            severity=Severity.HIGH if months < 1 else Severity.MEDIUM,
            title=f"Cash covers {months:.1f} months of spending",
            detail=f"Target emergency fund is {target:.0f} months of average spending.",
            evidence={"months": months, "target": target},
        )
    ]


def stale_prices(inp: DetectionInput) -> list[Finding]:
    stale = [p["symbol"] for p in inp.metrics["positions"] if p["price_source"] in {"stale", "client_file", "cost_basis"}]
    if not stale:
        return []
    return [
        Finding(
            code="STALE_PRICES",
            category="portfolio",
            severity=Severity.LOW,
            title="Portfolio valued with non-live prices",
            detail=f"{', '.join(stale)} priced from fallback sources; allocation figures are approximate.",
            evidence={"symbols": stale},
        )
    ]


# --- relationship (CRM) ------------------------------------------------------


def relationship_signals(inp: DetectionInput) -> list[Finding]:
    crm = inp.crm
    if not crm:
        return []
    findings = []
    kyc = crm.get("kyc_expiry")
    if kyc:
        try:
            days_left = (date.fromisoformat(kyc) - inp.as_of).days
        except ValueError:
            days_left = None
        if days_left is not None and days_left < 0:
            findings.append(
                Finding("KYC_EXPIRED", "relationship", Severity.CRITICAL, "KYC expired",
                        f"KYC expired on {kyc}; transactions should not be advised on until refreshed.",
                        {"kyc_expiry": kyc})
            )
        elif days_left is not None and days_left <= 30:
            findings.append(
                Finding("KYC_EXPIRING", "relationship", Severity.MEDIUM, f"KYC expires in {days_left} days",
                        f"KYC expiry {kyc}.", {"kyc_expiry": kyc, "days_left": days_left})
            )
    last = crm.get("last_contact")
    if last:
        try:
            gap = (inp.as_of - date.fromisoformat(last)).days
        except ValueError:
            gap = None
        if gap is not None and gap > inp.thresholds.crm_stale_contact_days:
            findings.append(
                Finding("STALE_RELATIONSHIP", "relationship", Severity.LOW, f"No advisor contact for {gap} days",
                        f"Last contact {last}.", {"days": gap})
            )
    complaints = [t for t in crm.get("open_tickets", []) if t.get("type") == "complaint"]
    if complaints:
        findings.append(
            Finding("OPEN_COMPLAINT", "relationship", Severity.MEDIUM, f"{len(complaints)} open complaint(s)",
                    "; ".join(t.get("subject", "") for t in complaints), {"tickets": [t.get("id") for t in complaints]})
        )
    return findings


DETECTORS: dict[str, Callable[[DetectionInput], list[Finding]]] = {
    "amount_outliers": amount_outliers,
    "large_new_counterparty": large_new_counterparty,
    "structuring": structuring,
    "velocity": velocity,
    "spending_spike": spending_spike,
    "negative_cashflow": negative_cashflow,
    "concentration": concentration,
    "risk_profile_mismatch": risk_profile_mismatch,
    "drawdown": drawdown,
    "liquidity": liquidity,
    "stale_prices": stale_prices,
    "relationship_signals": relationship_signals,
}
