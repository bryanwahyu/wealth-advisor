"""Deterministic financial metrics. Pure functions, no I/O."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ..models import ClientProfile, Holding, Transaction

# Moving money between own accounts or into investments is not consumption.
NON_SPENDING_CATEGORIES = {"investment", "transfer_internal", "savings"}
# Payments to third parties. Tracked apart from consumption so one large
# transfer does not distort spending trends or the emergency-fund target.
TRANSFER_CATEGORIES = {"transfer"}
RISKY_ASSET_CLASSES = {"equity", "crypto"}


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def monthly_cashflow(transactions: list[Transaction]) -> dict[str, dict[str, float]]:
    months: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inflow": 0.0, "spending": 0.0, "transfers_out": 0.0, "invested": 0.0}
    )
    for t in transactions:
        m = months[month_key(t.date)]
        if t.amount > 0:
            m["inflow"] += t.amount
        elif t.category in NON_SPENDING_CATEGORIES:
            m["invested"] += -t.amount
        elif t.category in TRANSFER_CATEGORIES:
            m["transfers_out"] += -t.amount
        else:
            m["spending"] += -t.amount
    for m in months.values():
        m["net"] = m["inflow"] - m["spending"] - m["transfers_out"] - m["invested"]
    return {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(months.items())}


def complete_months(cashflow: dict[str, dict[str, float]], as_of: date) -> list[str]:
    """Months that ended on or before `as_of`, so a partial month never looks like a dip."""
    current = month_key(as_of)
    month_ended = (as_of + timedelta(days=1)).month != as_of.month
    return [k for k in cashflow if k < current or (k == current and month_ended)]


def value_holdings(
    holdings: list[Holding], quotes: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for h in holdings:
        q = quotes.get(h.symbol) or {}
        if h.asset_class == "cash":
            price, price_source = 1.0, "par"
        elif q.get("price") is not None:
            price, price_source = float(q["price"]), "stale" if q.get("stale") else "market"
        elif h.last_price is not None:
            price, price_source = h.last_price, "client_file"
        else:
            price, price_source = h.cost_basis, "cost_basis"
        value = h.quantity * price
        rows.append(
            {
                "symbol": h.symbol,
                "asset_class": h.asset_class,
                "value": round(value, 2),
                "price": price,
                "price_source": price_source,
                "change_30d": q.get("change_30d"),
                "unrealized_pl": round(value - h.quantity * h.cost_basis, 2) if h.asset_class != "cash" else 0.0,
            }
        )
    total = sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = round(r["value"] / total, 4) if total else 0.0
    return rows


def portfolio_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(p["value"] for p in positions)
    by_class: dict[str, float] = defaultdict(float)
    for p in positions:
        by_class[p["asset_class"]] += p["value"]
    share = {k: round(v / total, 4) if total else 0.0 for k, v in by_class.items()}
    return {
        "total_value": round(total, 2),
        "allocation": share,
        "risky_share": round(sum(share.get(c, 0.0) for c in RISKY_ASSET_CLASSES), 4),
        "crypto_share": share.get("crypto", 0.0),
        "cash": round(by_class.get("cash", 0.0), 2),
        "unrealized_pl": round(sum(p["unrealized_pl"] for p in positions), 2),
    }


def compute_metrics(profile: ClientProfile, quotes: dict[str, dict[str, Any]], as_of: date) -> dict[str, Any]:
    cashflow = monthly_cashflow(profile.transactions)
    full = complete_months(cashflow, as_of)
    spend = [cashflow[m]["spending"] for m in full]
    avg_spending = round(sum(spend) / len(spend), 2) if spend else None
    inflows = [cashflow[m]["inflow"] for m in full]
    avg_inflow = round(sum(inflows) / len(inflows), 2) if inflows else None

    positions = value_holdings(profile.holdings, quotes)
    portfolio = portfolio_summary(positions) if positions else None

    savings_rate = None
    if avg_inflow and avg_spending is not None:
        savings_rate = round(1 - avg_spending / avg_inflow, 4)

    emergency_months = None
    if portfolio and avg_spending:
        emergency_months = round(portfolio["cash"] / avg_spending, 2)

    return {
        "as_of": as_of.isoformat(),
        "transaction_count": len(profile.transactions),
        "complete_months": full,
        "monthly_cashflow": cashflow,
        "avg_monthly_spending": avg_spending,
        "avg_monthly_inflow": avg_inflow,
        "savings_rate": savings_rate,
        "emergency_fund_months": emergency_months,
        "positions": positions,
        "portfolio": portfolio,
    }
