"""Generate deterministic mock data for the demo clients.

    python scripts/generate_sample_data.py

C001  moderate, high-net-worth. Clean history, then in September: a large
      transfer to a new offshore counterparty, three transfers just under the
      IDR 500M reporting line, a burst of e-wallet top-ups, crypto above policy.
C002  conservative. Spending creeps above income for three months; holdings
      field missing entirely; KYC expiring soon.
C003  aggressive. Messy file: no monthly_income, duplicate ids, missing
      amounts, bad dates, a holding the market feed does not know. Not in CRM.
C004  moderate, healthy baseline with nothing unusual (control case).
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data"
START, END = date(2026, 4, 1), date(2026, 9, 30)


def months():
    d = START
    while d <= END:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


class Ledger:
    def __init__(self, prefix: str, seed: int):
        self.rng = random.Random(seed)
        self.prefix = prefix
        self.rows: list[dict] = []

    def add(self, when: date, amount: float, category: str, counterparty: str, channel: str = "transfer",
            country: str = "ID") -> None:
        self.rows.append({
            "id": f"{self.prefix}-{len(self.rows) + 1:04d}",
            "date": when.isoformat(),
            "amount": round(amount, -3),
            "category": category,
            "counterparty": counterparty,
            "channel": channel,
            "country": country,
        })

    def routine(self, income: float, rent: float, scale: float, spend_growth: float = 0.0) -> None:
        """Monthly salary, rent, bills, plus weekly groceries and dining."""
        for i, m in enumerate(months()):
            g = 1 + spend_growth * i
            self.add(m.replace(day=25), income, "salary", "PT Employer Payroll")
            self.add(m.replace(day=2), -rent, "housing", "Apartment Management")
            self.add(m.replace(day=5), -self.rng.uniform(1.8, 2.4) * scale * 1e6, "utilities", "PLN")
            self.add(m.replace(day=6), -0.35e6 * scale, "subscriptions", "Telkomsel")
            for week in range(4):
                d = m + timedelta(days=7 * week + self.rng.randint(0, 3))
                self.add(d, -self.rng.uniform(1.5, 2.5) * scale * 1e6 * g, "groceries", "Supermarket Ranch")
                self.add(d + timedelta(days=1), -self.rng.uniform(0.6, 1.4) * scale * 1e6 * g, "dining",
                         self.rng.choice(["Sate Khas Senayan", "Kopi Kenangan", "Bebek Bengil"]), "card")
                self.add(d + timedelta(days=2), -self.rng.uniform(0.2, 0.5) * scale * 1e6 * g, "transport", "Gojek",
                         "ewallet")


def c001() -> dict:
    lg = Ledger("C001", 1)
    lg.routine(income=250e6, rent=35e6, scale=3.0)
    for m in months():
        lg.add(m.replace(day=26), -50e6, "investment", "Mandiri Sekuritas")
    # September anomalies
    lg.add(date(2026, 9, 12), -420e6, "transfer", "Lucky Star Trading Ltd", "wire", "HK")
    for d, amt in ((date(2026, 9, 20), 480e6), (date(2026, 9, 21), 485e6), (date(2026, 9, 22), 472e6)):
        lg.add(d, -amt, "transfer", "PT Sinar Abadi Jaya")
    lg.add(date(2026, 9, 19), 1_500e6, "asset_sale", "Mandiri Sekuritas")
    for _ in range(8):
        lg.add(date(2026, 9, 27), -2e6, "ewallet_topup", "OVO", "ewallet")
    lg.add(date(2026, 9, 15), -38e6, "shopping", "Plaza Indonesia Boutique", "card")
    return {
        "client_id": "C001",
        "name": "Andi Pratama",
        "risk_profile": "moderate",
        "monthly_income": 250_000_000,
        "currency": "IDR",
        "statement_end": END.isoformat(),
        "goals": [{"name": "Children's education", "target": 3_000_000_000, "year": 2034}],
        "holdings": [
            {"symbol": "BBCA", "asset_class": "equity", "quantity": 400_000, "cost_basis": 8_900, "last_price": 9_850},
            {"symbol": "TLKM", "asset_class": "equity", "quantity": 250_000, "cost_basis": 3_400, "last_price": 3_050},
            {"symbol": "BTC", "asset_class": "crypto", "quantity": 0.9, "cost_basis": 1_450_000_000,
             "last_price": 1_520_000_000},
            {"symbol": "RD-SUCORINVEST-BOND", "asset_class": "bond", "quantity": 900_000, "cost_basis": 1_950,
             "last_price": 2_010},
            {"symbol": "CASH-IDR", "asset_class": "cash", "quantity": 900_000_000, "cost_basis": 1},
        ],
        "transactions": lg.rows,
    }


def c002() -> dict:
    lg = Ledger("C002", 2)
    lg.routine(income=30e6, rent=7.5e6, scale=1.4, spend_growth=0.12)
    for m in list(months())[3:]:
        lg.add(m.replace(day=18), -4.5e6, "education", "Universitas Indonesia")
        lg.add(m.replace(day=21), -3.2e6, "healthcare", "RS Pondok Indah", "card")
    return {
        "client_id": "C002",
        "name": "Siti Rahayu",
        "risk_profile": "conservative",
        "monthly_income": 30_000_000,
        "currency": "IDR",
        "statement_end": END.isoformat(),
        "transactions": lg.rows,
        # holdings deliberately missing
    }


def c003() -> dict:
    lg = Ledger("C003", 3)
    lg.routine(income=60e6, rent=12e6, scale=1.8)
    rows = lg.rows
    rows[10]["amount"] = None
    rows[20]["date"] = "2026-13-45"
    rows.append(dict(rows[30]))  # duplicate id
    del rows[40]["amount"]
    return {
        "client_id": "C003",
        "name": "Budi Santoso",
        "risk_profile": "Aggressive",
        "currency": "IDR",
        "statement_end": END.isoformat(),
        "holdings": [
            {"symbol": "GOTO", "asset_class": "equity", "quantity": 2_000_000, "cost_basis": 95, "last_price": 70},
            {"symbol": "ANTM", "asset_class": "equity", "quantity": 150_000, "cost_basis": 1_600, "last_price": 1_710},
            {"symbol": "PRIVATE-FUND-X", "asset_class": "equity", "quantity": 1, "cost_basis": 250_000_000},
            {"symbol": "CASH-IDR", "asset_class": "cash", "quantity": 210_000_000, "cost_basis": 1},
            {"asset_class": "equity", "quantity": 10},
        ],
        "transactions": rows,
    }


def c004() -> dict:
    lg = Ledger("C004", 4)
    lg.routine(income=45e6, rent=9e6, scale=1.2)
    for m in months():
        lg.add(m.replace(day=26), -8e6, "investment", "Bibit")
    return {
        "client_id": "C004",
        "name": "Dewi Lestari",
        "risk_profile": "moderate",
        "monthly_income": 45_000_000,
        "currency": "IDR",
        "statement_end": END.isoformat(),
        "holdings": [
            {"symbol": "BBRI", "asset_class": "equity", "quantity": 30_000, "cost_basis": 4_300, "last_price": 4_550},
            {"symbol": "RD-SUCORINVEST-BOND", "asset_class": "bond", "quantity": 120_000, "cost_basis": 1_900,
             "last_price": 2_010},
            {"symbol": "ORI-025", "asset_class": "bond", "quantity": 100, "cost_basis": 1_000_000,
             "last_price": 1_004_000},
            {"symbol": "CASH-IDR", "asset_class": "cash", "quantity": 180_000_000, "cost_basis": 1},
        ],
        "transactions": lg.rows,
    }


CRM = {
    "C001": {
        "advisor": "R. Wijaya",
        "segment": "Priority",
        "kyc_expiry": "2026-10-20",
        "last_contact": "2026-02-03",
        "open_tickets": [],
        "notes": "Prefers WhatsApp. Interested in offshore diversification.",
    },
    "C002": {
        "advisor": "M. Hasan",
        "segment": "Emerald",
        "kyc_expiry": "2026-10-15",
        "last_contact": "2026-08-10",
        "open_tickets": [{"id": "T-8812", "type": "complaint", "subject": "Unexpected admin fee on savings account"}],
    },
    "C004": {
        "advisor": "M. Hasan",
        "segment": "Emerald",
        "kyc_expiry": "2027-05-01",
        "last_contact": "2026-09-02",
        "open_tickets": [],
    },
}

QUOTES = {
    "as_of": "2026-09-30",
    "quotes": {
        "BBCA": {"price": 9_700, "change_30d": -0.02},
        "TLKM": {"price": 2_900, "change_30d": -0.06},
        "BTC": {"price": 1_180_000_000, "change_30d": -0.22},
        "RD-SUCORINVEST-BOND": {"price": 2_015, "change_30d": 0.004},
        "BBRI": {"price": 4_600, "change_30d": 0.01},
        "ORI-025": {"price": 1_003_500, "change_30d": 0.0},
        "GOTO": {"price": 64, "change_30d": -0.18},
        "ANTM": {"price": 1_690, "change_30d": 0.03},
    },
}


def main() -> None:
    (ROOT / "clients").mkdir(parents=True, exist_ok=True)
    (ROOT / "crm").mkdir(parents=True, exist_ok=True)
    (ROOT / "market").mkdir(parents=True, exist_ok=True)
    for build in (c001, c002, c003, c004):
        doc = build()
        (ROOT / "clients" / f"{doc['client_id']}.json").write_text(json.dumps(doc, indent=2))
    (ROOT / "clients" / "C005.json").write_text('{"client_id": "C005", "name": "Broken File", ')
    (ROOT / "crm" / "crm.json").write_text(json.dumps(CRM, indent=2))
    (ROOT / "market" / "quotes.json").write_text(json.dumps(QUOTES, indent=2))
    print(f"wrote mock data to {ROOT}")


if __name__ == "__main__":
    main()
