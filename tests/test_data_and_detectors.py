from __future__ import annotations

from datetime import date, timedelta

import pytest

from wealth_advisor.analytics.detectors import (
    DetectionInput,
    amount_outliers,
    large_new_counterparty,
    negative_cashflow,
    relationship_signals,
    risk_profile_mismatch,
    structuring,
)
from wealth_advisor.analytics.metrics import compute_metrics
from wealth_advisor.config import AnomalyThresholds, RiskPolicy
from wealth_advisor.errors import ValidationError
from wealth_advisor.models import ClientProfile, Holding, Severity, Transaction
from wealth_advisor.tools.client_data import ClientDataNormalizerTool

AS_OF = date(2026, 9, 30)


def tx(i, d, amount, category="groceries", counterparty="Shop", country="ID"):
    return Transaction(f"T{i}", d, amount, category, counterparty, "card", country)


def baseline(months=6, income=50e6):
    rows, i = [], 0
    for m in range(4, 4 + months):
        rows.append(tx(i := i + 1, date(2026, m, 25), income, "salary", "Payroll"))
        rows.append(tx(i := i + 1, date(2026, m, 2), -12e6, "housing", "Landlord"))
        for w in range(4):
            rows.append(tx(i := i + 1, date(2026, m, 3 + 7 * w), -1.5e6 - w * 1e5, "groceries", "Shop"))
    return rows, i


def detect(fn, transactions, holdings=(), crm=None, risk="moderate", income=50e6):
    profile = ClientProfile("X", "X", risk, income, "IDR", sorted(transactions, key=lambda t: t.date), list(holdings))
    metrics = compute_metrics(profile, {}, AS_OF)
    return fn(DetectionInput(profile, metrics, crm, AS_OF, AnomalyThresholds(), RiskPolicy()))


def test_normalizer_drops_bad_rows_and_infers_income():
    raw = {
        "client_id": "C9",
        "risk_profile": "Aggressive",
        "transactions": [
            {"id": "a", "date": "2026-09-25", "amount": 40e6, "category": "salary"},
            {"id": "a", "date": "2026-09-25", "amount": 40e6, "category": "salary"},
            {"id": "b", "date": "2026-13-01", "amount": -1},
            {"id": "c", "date": "2026-09-02", "amount": None},
            "garbage",
        ],
        "holdings": [{"symbol": "BBCA", "asset_class": "equity", "quantity": 10}, {"quantity": 5}],
    }
    out = ClientDataNormalizerTool().run(raw=raw, client_id="C9")
    p = out["profile"]
    msgs = " | ".join(i.message for i in out["issues"])
    assert [t.id for t in p.transactions] == ["a"]
    assert p.risk_profile == "aggressive"
    assert p.monthly_income == 40e6
    assert len(p.holdings) == 1
    for expected in ("duplicate", "unparseable", "non-numeric", "not an object", "without symbol", "inferred"):
        assert expected in msgs


def test_normalizer_rejects_mismatched_client_id():
    with pytest.raises(ValidationError):
        ClientDataNormalizerTool().run(raw={"client_id": "OTHER"}, client_id="C1")


def test_recurring_rent_is_not_an_outlier_but_one_off_is():
    rows, i = baseline()
    assert detect(amount_outliers, rows) == []
    rows.append(tx(i + 1, date(2026, 9, 15), -20e6, "shopping", "Boutique"))
    found = detect(amount_outliers, rows)
    assert [f.transaction_ids for f in found] == [[f"T{i + 1}"]]


def test_large_new_foreign_counterparty_is_critical():
    rows, i = baseline()
    rows.append(tx(i + 1, date(2026, 9, 12), -40e6, "transfer", "Offshore Ltd", "HK"))
    (f,) = detect(large_new_counterparty, rows)
    assert f.severity == Severity.CRITICAL and f.evidence["new_counterparty"]


def test_structuring_cluster_detected_and_spread_out_transfers_are_not():
    rows, i = baseline()
    clustered = rows + [tx(i + k, date(2026, 9, 20) + timedelta(days=k), -470e6, "transfer", "PT A") for k in (1, 2)]
    assert detect(structuring, clustered)[0].severity == Severity.CRITICAL
    spread = rows + [tx(i + k, date(2026, 6, 1) + timedelta(days=30 * k), -470e6, "transfer", "PT A") for k in (1, 2)]
    assert detect(structuring, spread) == []


def test_negative_cashflow_three_months():
    rows, _ = baseline(income=10e6)
    (f,) = detect(negative_cashflow, rows, income=10e6)
    assert f.code == "NEGATIVE_CASHFLOW"


def test_risk_profile_mismatch_for_conservative_client():
    holdings = [Holding("BTC", "crypto", 1, 100, 100), Holding("CASH", "cash", 100, 1)]
    codes = {f.code for f in detect(risk_profile_mismatch, [], holdings, risk="conservative")}
    assert codes == {"RISK_PROFILE_MISMATCH", "CRYPTO_OVEREXPOSURE"}


def test_kyc_expired_is_critical_and_bad_dates_are_ignored():
    found = detect(relationship_signals, [], crm={"kyc_expiry": "2026-01-01", "last_contact": "not-a-date"})
    assert [(f.code, f.severity) for f in found] == [("KYC_EXPIRED", Severity.CRITICAL)]
