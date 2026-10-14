"""Market data tool with a stale-price fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import ToolError, TransientToolError
from .base import Tool


class MockMarketDataService:
    def __init__(self, data_file: Path, failure_mode: str = "none"):
        self.data_file = data_file
        self.failure_mode = failure_mode

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        if self.failure_mode == "down":
            raise TransientToolError("market data provider unreachable")
        snapshot = json.loads(self.data_file.read_text(encoding="utf-8"))
        prices = snapshot.get("quotes", {})
        return {s: {**prices[s], "as_of": snapshot.get("as_of"), "stale": False} for s in symbols if s in prices}


class MarketDataTool(Tool):
    name = "market.get_quotes"
    description = "Get latest price and 30-day change for a list of symbols."
    cacheable = True

    def __init__(self, service: MockMarketDataService):
        self.service = service

    def run(self, symbols: list[str], fallback_prices: dict[str, float] | None = None) -> dict[str, Any]:
        return self.service.quotes(symbols)

    def fallback(
        self, error: ToolError, symbols: list[str], fallback_prices: dict[str, float] | None = None
    ) -> dict[str, Any] | None:
        """Use the last price stored in the client file. 30-day change is unknown."""
        if not fallback_prices:
            return None
        return {
            s: {"price": p, "change_30d": None, "as_of": None, "stale": True}
            for s, p in fallback_prices.items()
            if s in symbols and p is not None
        }
