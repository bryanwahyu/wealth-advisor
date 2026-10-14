"""Client data tools: loading raw JSON and normalizing it into domain models.

Normalization is lenient on purpose. Bad records are dropped and reported as
data-quality issues instead of failing the whole run; only a file that cannot
be parsed at all is a hard error.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..errors import NotFoundError, ValidationError
from ..models import ClientProfile, DataQualityIssue, Holding, Transaction
from .base import Tool

VALID_RISK_PROFILES = {"conservative", "moderate", "aggressive"}
INCOME_CATEGORIES = {"salary", "income", "dividend_income", "business_income"}


class ClientDataLoaderTool(Tool):
    name = "client_data.load"
    description = "Load a client's raw financial data (JSON) by client id."
    cacheable = True

    def __init__(self, clients_dir: Path):
        self.clients_dir = clients_dir

    def run(self, client_id: str) -> dict[str, Any]:
        if not client_id or "/" in client_id or ".." in client_id:
            raise ValidationError(f"invalid client id {client_id!r}", tool=self.name)
        path = self.clients_dir / f"{client_id}.json"
        if not path.exists():
            raise NotFoundError(f"no data file for client {client_id}", tool=self.name)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path.name} is not valid JSON: {exc}", tool=self.name) from exc
        if not isinstance(raw, dict):
            raise ValidationError(f"{path.name} must contain a JSON object", tool=self.name)
        return raw


class ClientDataNormalizerTool(Tool):
    name = "client_data.normalize"
    description = "Validate and normalize raw client JSON into a ClientProfile plus data-quality issues."

    def run(self, raw: dict[str, Any], client_id: str) -> dict[str, Any]:
        issues: list[DataQualityIssue] = []

        def issue(msg: str, record_id: str | None = None) -> None:
            issues.append(DataQualityIssue(source="client_data", message=msg, record_id=record_id))

        if raw.get("client_id") not in (None, client_id):
            raise ValidationError(
                f"file client_id {raw.get('client_id')!r} does not match requested {client_id!r}", tool=self.name
            )

        risk = str(raw.get("risk_profile", "")).lower()
        if risk not in VALID_RISK_PROFILES:
            issue(f"risk_profile {raw.get('risk_profile')!r} invalid or missing; assuming 'conservative'")
            risk = "conservative"

        transactions = self._parse_transactions(raw.get("transactions"), issue)
        holdings = self._parse_holdings(raw.get("holdings"), issue)

        income = raw.get("monthly_income")
        if not isinstance(income, (int, float)) or income <= 0:
            income = self._infer_income(transactions)
            if income:
                issue(f"monthly_income missing; inferred {income:,.0f} from income transactions")
            else:
                issue("monthly_income missing and could not be inferred")
                income = None

        profile = ClientProfile(
            client_id=client_id,
            name=str(raw.get("name") or client_id),
            risk_profile=risk,
            monthly_income=float(income) if income else None,
            currency=str(raw.get("currency") or "IDR"),
            transactions=transactions,
            holdings=holdings,
            goals=raw.get("goals") if isinstance(raw.get("goals"), list) else [],
        )
        return {"profile": profile, "issues": issues}

    @staticmethod
    def _parse_transactions(items: Any, issue) -> list[Transaction]:
        if items is None:
            issue("transactions missing")
            return []
        if not isinstance(items, list):
            issue("transactions is not a list; ignored")
            return []
        seen: set[str] = set()
        out: list[Transaction] = []
        for i, item in enumerate(items):
            tid = str(item.get("id") or f"row-{i}") if isinstance(item, dict) else f"row-{i}"
            if not isinstance(item, dict):
                issue("transaction is not an object; dropped", tid)
                continue
            if tid in seen:
                issue("duplicate transaction id; dropped", tid)
                continue
            amount = item.get("amount")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                issue("transaction amount missing or non-numeric; dropped", tid)
                continue
            try:
                when = date.fromisoformat(str(item.get("date")))
            except ValueError:
                issue(f"transaction date {item.get('date')!r} unparseable; dropped", tid)
                continue
            seen.add(tid)
            out.append(
                Transaction(
                    id=tid,
                    date=when,
                    amount=float(amount),
                    category=str(item.get("category") or "uncategorized").lower(),
                    counterparty=str(item.get("counterparty") or "unknown"),
                    channel=str(item.get("channel") or "unknown"),
                    country=str(item.get("country") or "ID"),
                )
            )
        out.sort(key=lambda t: (t.date, t.id))
        return out

    @staticmethod
    def _parse_holdings(items: Any, issue) -> list[Holding]:
        if items is None:
            issue("holdings missing; portfolio checks will be skipped")
            return []
        if not isinstance(items, list):
            issue("holdings is not a list; ignored")
            return []
        out: list[Holding] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("symbol"):
                issue("holding without symbol; dropped")
                continue
            qty = item.get("quantity")
            if not isinstance(qty, (int, float)) or qty < 0:
                issue("holding quantity invalid; dropped", item.get("symbol"))
                continue
            out.append(
                Holding(
                    symbol=str(item["symbol"]),
                    asset_class=str(item.get("asset_class") or "unknown").lower(),
                    quantity=float(qty),
                    cost_basis=float(item.get("cost_basis") or 0),
                    last_price=float(item["last_price"]) if isinstance(item.get("last_price"), (int, float)) else None,
                )
            )
        return out

    @staticmethod
    def _infer_income(transactions: list[Transaction]) -> float | None:
        by_month: dict[str, float] = {}
        for t in transactions:
            if t.amount > 0 and t.category in INCOME_CATEGORIES:
                key = t.date.strftime("%Y-%m")
                by_month[key] = by_month.get(key, 0.0) + t.amount
        if not by_month:
            return None
        values = sorted(by_month.values())
        return round(values[len(values) // 2], 2)
