"""Data Fetcher Agent: gathers client data, CRM context and market prices.

Client data is mandatory. CRM and market data are enrichment: if they fail the
agent continues, marks the result degraded and records why.
"""

from __future__ import annotations

from datetime import date

from ..models import ClientProfile, DataQualityIssue
from .base import Agent, AgentContext, AgentResult, Task


class DataFetcherAgent(Agent):
    name = "data_fetcher"
    handles = frozenset({"fetch_client_data"})

    def execute(self, task: Task, ctx: AgentContext) -> AgentResult:
        issues: list[DataQualityIssue] = []
        sources: dict[str, str] = {}

        raw = ctx.tools.call("client_data.load", client_id=ctx.client_id)
        if not raw.ok:
            raise self.fail(f"client data unavailable: {raw.error}")
        sources["client_data"] = raw.source

        norm = ctx.tools.call("client_data.normalize", raw=raw.data, client_id=ctx.client_id)
        if not norm.ok:
            raise self.fail(f"client data invalid: {norm.error}")
        profile: ClientProfile = norm.data["profile"]
        issues.extend(norm.data["issues"])

        if not profile.transactions and not profile.holdings:
            raise self.fail("client has neither transactions nor holdings; nothing to analyze")

        crm_res = ctx.tools.call("crm.get_client", client_id=ctx.client_id)
        crm = crm_res.data if crm_res.ok else None
        sources["crm"] = crm_res.source if crm_res.ok else ("not_found" if crm_res.error_type == "NotFoundError" else "unavailable")
        if not crm_res.ok:
            issues.append(DataQualityIssue("crm", f"CRM data missing ({crm_res.error}); relationship checks skipped"))

        quotes: dict = {}
        priced = [h for h in profile.holdings if h.asset_class != "cash"]
        if priced:
            q = ctx.tools.call(
                "market.get_quotes",
                symbols=[h.symbol for h in priced],
                fallback_prices={h.symbol: h.last_price for h in priced},
            )
            if q.ok:
                quotes = q.data
                sources["market"] = q.source
                if q.degraded:
                    issues.append(DataQualityIssue("market", f"live prices unavailable ({q.error}); using client-file prices"))
                missing = sorted({h.symbol for h in priced} - set(quotes))
                if missing:
                    issues.append(DataQualityIssue("market", f"no quote for {', '.join(missing)}; valued at last known price or cost"))
            else:
                sources["market"] = "unavailable"
                issues.append(DataQualityIssue("market", f"market data unavailable ({q.error}); valued at cost basis"))

        as_of = self._as_of(ctx, profile, raw.data.get("statement_end"), issues)

        s = ctx.session
        s.put("profile", profile, author=self.name)
        s.put("crm", crm, author=self.name)
        s.put("quotes", quotes, author=self.name)
        s.put("as_of", as_of, author=self.name)
        s.put("sources", sources, author=self.name)

        degraded = any(v in {"unavailable", "not_found", "fallback"} for v in sources.values())
        return AgentResult(
            self.name,
            task.id,
            ok=True,
            degraded=degraded,
            issues=issues,
            output={
                "sources": sources,
                "transactions": len(profile.transactions),
                "holdings": len(profile.holdings),
                "as_of": as_of.isoformat(),
            },
        )

    @staticmethod
    def _as_of(ctx: AgentContext, profile: ClientProfile, statement_end: str | None,
               issues: list[DataQualityIssue]) -> date:
        """Analysis date: CLI override, else the statement period end, else the
        latest transaction. Never wall-clock time, so runs are reproducible."""
        if ctx.settings.as_of:
            return date.fromisoformat(ctx.settings.as_of)
        if statement_end:
            try:
                return date.fromisoformat(statement_end)
            except ValueError:
                issues.append(DataQualityIssue("client_data", f"statement_end {statement_end!r} unparseable"))
        if profile.transactions:
            return max(t.date for t in profile.transactions)
        return date.today()
