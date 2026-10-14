"""Report assembly and rendering (JSON for systems, Markdown for advisors)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agents.base import AgentContext

SCHEMA_VERSION = "1.0"


def risk_level(score: int | None) -> str | None:
    if score is None:
        return None
    return "low" if score < 25 else "moderate" if score < 50 else "high" if score < 75 else "critical"


def build_report(
    ctx: "AgentContext",
    *,
    status: str,
    trace: list[dict[str, Any]],
    review: dict[str, Any] | None,
    memory: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    s = ctx.session
    profile = s.get("profile")
    metrics = s.get("metrics") or {}
    score = s.get("risk_score")
    as_of = s.get("as_of")
    issues = s.get("data_issues") or []

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": ctx.run_id,
        "client_id": ctx.client_id,
        "status": status,
        "error": error,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat() if as_of else None,
        "client": (
            {
                "name": profile.name,
                "risk_profile": profile.risk_profile,
                "currency": profile.currency,
                "monthly_income": profile.monthly_income,
            }
            if profile
            else None
        ),
        "risk": {"score": score, "level": risk_level(score), "history": s.get("history")},
        "briefing": s.get("briefing"),
        "recommendations": [r.to_dict() for r in s.get("recommendations") or []],
        "findings": [f.to_dict() for f in s.get("findings") or []],
        "review": review,
        "metrics": {k: v for k, v in metrics.items() if k != "positions"},
        "positions": metrics.get("positions"),
        "data_quality": {"sources": s.get("sources"), "issues": [i.to_dict() for i in issues]},
        "memory": memory,
        "execution": trace,
    }


def render_markdown(r: dict[str, Any]) -> str:
    out = [f"# Advisory report: {r['client']['name'] if r['client'] else r['client_id']}", ""]
    out.append(f"- Run: `{r['run_id']}`  |  Status: **{r['status']}**  |  As of: {r['as_of']}")
    if r["error"]:
        out += ["", f"**Run failed:** {r['error']}"]
        return "\n".join(out) + "\n"
    risk = r["risk"]
    out.append(f"- Risk score: **{risk['score']}/100 ({risk['level']})**")
    if risk.get("history") and risk["history"].get("trend"):
        out.append(f"- Trend: {risk['history']['trend']}")
    if r["briefing"]:
        out += ["", "## Briefing", "", r["briefing"]["text"], "", f"_source: {r['briefing']['source']}_"]

    out += ["", "## Recommendations", "", "| P | Action | Status | Approval |", "|---|---|---|---|"]
    for rec in r["recommendations"]:
        out.append(f"| {rec['priority']} | {rec['action']} | {rec['status']} | {'yes' if rec['requires_approval'] else 'no'} |")

    out += ["", "## Findings", "", "| Severity | Code | Finding | Detail |", "|---|---|---|---|"]
    for f in r["findings"]:
        tag = " (recurring)" if f["recurring"] else ""
        out.append(f"| {f['severity']} | {f['code']} | {f['title']}{tag} | {f['detail']} |")

    m = r["metrics"]
    if m:
        out += ["", "## Key metrics", ""]
        out.append(f"- Avg monthly spending: {_num(m.get('avg_monthly_spending'))}")
        out.append(f"- Avg monthly inflow: {_num(m.get('avg_monthly_inflow'))}")
        out.append(f"- Savings rate: {_pct(m.get('savings_rate'))}")
        out.append(f"- Emergency fund: {m.get('emergency_fund_months')} months")
        pf = m.get("portfolio")
        if pf:
            alloc = ", ".join(f"{k} {v:.0%}" for k, v in sorted(pf["allocation"].items(), key=lambda kv: -kv[1]))
            out.append(f"- Portfolio: {_num(pf['total_value'])} ({alloc})")

    dq = r["data_quality"]
    if dq["issues"]:
        out += ["", "## Data quality", ""]
        out += [f"- [{i['source']}] {i['message']}" + (f" (`{i['record_id']}`)" if i.get("record_id") else "")
                for i in dq["issues"]]
    if dq.get("sources"):
        out.append("- Sources: " + ", ".join(f"{k}={v}" for k, v in dq["sources"].items()))

    if r["review"]:
        out += ["", "## Review", "", f"- Outcome: {r['review']['outcome']} (reviewer: {r['review'].get('reviewer')})"]
        for d in r["review"].get("decisions", []):
            out.append(f"- {d['id']}: {d['decision']}" + (f" ({d['note']})" if d.get("note") else ""))

    out += ["", "## Execution", ""]
    for t in r["execution"]:
        mark = "ok" if t["ok"] else "FAILED"
        extra = " (degraded)" if t.get("degraded") else ""
        out.append(f"- {t['step']} -> {t['agent']}: {mark}{extra}" + (f" ({t['error']})" if t.get("error") else ""))
    return "\n".join(out) + "\n"


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    folder = output_dir / report["client_id"]
    folder.mkdir(parents=True, exist_ok=True)
    jp = folder / f"{report['run_id']}.json"
    mp = folder / f"{report['run_id']}.md"
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    mp.write_text(render_markdown(report), encoding="utf-8")
    return jp, mp


def _num(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"
