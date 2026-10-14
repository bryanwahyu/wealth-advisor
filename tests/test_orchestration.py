from __future__ import annotations

import json

import pytest

from wealth_advisor.agents.base import Agent, AgentResult
from wealth_advisor.agents.orchestrator import Orchestrator, Step
from wealth_advisor.hitl import DeferReviewer, FileReviewer, InteractiveReviewer, Reviewer
from wealth_advisor.memory.scylla import load_migrations


def codes(report):
    return {f["code"] for f in report["findings"]}


def test_c001_end_to_end(make_system):
    report = make_system().orchestrator.run("C001")
    assert report["status"] == "completed"
    assert {"POSSIBLE_STRUCTURING", "LARGE_OUTFLOW", "CRYPTO_OVEREXPOSURE", "KYC_EXPIRING"} <= codes(report)
    assert report["risk"]["score"] >= 75
    assert report["recommendations"][0]["priority"] == 1
    assert [t["step"] for t in report["execution"]] == [
        "fetch_client_data", "analyze", "advise", "human_review", "persist"]
    json.dumps(report, default=str)  # serializable


def test_clean_client_needs_no_review(make_system):
    report = make_system().orchestrator.run("C004")
    assert report["findings"] == [] and report["review"]["outcome"] == "not_required"


def test_crm_outage_degrades_but_completes(make_system):
    report = make_system(crm_failure_mode="down").orchestrator.run("C001")
    assert report["status"] == "completed_with_warnings"
    assert report["data_quality"]["sources"]["crm"] == "unavailable"
    assert not codes(report) & {"KYC_EXPIRING", "STALE_RELATIONSHIP"}
    assert "POSSIBLE_STRUCTURING" in codes(report)


def test_market_outage_uses_stale_prices(make_system):
    report = make_system(market_failure_mode="down").orchestrator.run("C001")
    assert report["data_quality"]["sources"]["market"] == "fallback"
    assert "STALE_PRICES" in codes(report)


@pytest.mark.parametrize("client_id", ["C404", "C005", "../etc/passwd"])
def test_unusable_client_data_fails_cleanly(make_system, client_id):
    report = make_system().orchestrator.run(client_id)
    assert report["status"] == "failed" and report["error"]


def test_messy_file_is_analyzed_with_issues(make_system):
    report = make_system().orchestrator.run("C003")
    assert report["status"] == "completed_with_warnings"
    assert len(report["data_quality"]["issues"]) >= 6
    assert report["data_quality"]["sources"]["crm"] == "not_found"


def test_long_term_memory_marks_recurring_findings(make_system):
    system = make_system()
    first = system.orchestrator.run("C001")
    second = system.orchestrator.run("C001")
    assert not any(f["recurring"] for f in first["findings"])
    assert all(f["recurring"] for f in second["findings"])
    assert second["risk"]["history"]["previous_score"] == first["risk"]["score"]


def test_memory_outage_is_reported_not_treated_as_first_run(make_system):
    report = make_system(memory_backend="none").orchestrator.run("C001")
    assert report["risk"]["history"]["status"] == "unavailable"
    assert report["memory"]["persisted"] is False
    assert report["status"] == "completed_with_warnings"


def test_deferred_review_parks_run(make_system):
    report = make_system(reviewer=DeferReviewer()).orchestrator.run("C001")
    assert report["status"] == "pending_review"
    assert {r["status"] for r in report["recommendations"]} == {"pending_review"}


def test_file_review_overrides_recommendation(make_system, tmp_path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"reviewer": "advisor.rw", "decisions": [
        {"id": "REC-POSSIBLE_STRUCTURING", "decision": "approve", "note": "sent to compliance"},
        {"id": "REC-CONCENTRATION", "decision": "reject", "note": "client holds BBCA for dividend plan"},
        {"id": "REC-LARGE_OUTFLOW", "decision": "modify", "action": "Visit client in person to verify HK transfer"},
        {"id": "REC-DOES-NOT-EXIST", "decision": "approve"},
    ]}))
    report = make_system(reviewer=FileReviewer(decisions)).orchestrator.run("C001")
    recs = {r["id"]: r for r in report["recommendations"]}
    assert recs["REC-POSSIBLE_STRUCTURING"]["status"] == "approved"
    assert recs["REC-CONCENTRATION"]["status"] == "rejected"
    assert recs["REC-LARGE_OUTFLOW"]["action"].startswith("Visit client")
    assert report["review"]["unknown_ids"] == ["REC-DOES-NOT-EXIST"]
    assert report["status"] == "pending_review"  # remaining recs still need a decision


def test_interactive_review_with_scripted_answers(make_system):
    answers = iter(["a", ""] * 50)
    reviewer = InteractiveReviewer(ask=lambda prompt: next(answers))
    report = make_system(reviewer=reviewer).orchestrator.run("C001")
    assert report["status"] == "completed"
    assert {r["status"] for r in report["recommendations"]} == {"approved"}


def test_broken_reviewer_never_auto_approves(make_system):
    class Broken(Reviewer):
        def review(self, packet):
            raise RuntimeError("UI crashed")

    report = make_system(reviewer=Broken()).orchestrator.run("C001")
    assert report["status"] == "pending_review"


def test_agent_crash_is_contained(make_system, settings):
    class Exploding(Agent):
        name = "exploding"
        handles = frozenset({"explode"})

        def execute(self, task, ctx):
            raise ZeroDivisionError

    system = make_system()
    orch = Orchestrator([Exploding()], system.orchestrator.tools, DeferReviewer(), settings, plan=(Step("explode"),))
    report = orch.run("C001")
    assert report["status"] == "failed" and "ZeroDivisionError" in report["error"]


def test_routing_rejects_duplicate_and_missing_handlers(make_system, settings):
    class A(Agent):
        name = "a"
        handles = frozenset({"x"})

        def execute(self, task, ctx):
            return AgentResult(self.name, task.id, ok=True)

    tools = make_system().orchestrator.tools
    with pytest.raises(ValueError, match="handled by both"):
        Orchestrator([A(), A()], tools, DeferReviewer(), settings, plan=(Step("x"),))
    with pytest.raises(ValueError, match="no agent registered"):
        Orchestrator([A()], tools, DeferReviewer(), settings, plan=(Step("y"),))


def test_scylla_migrations_render_and_validate_identifiers():
    (version, stmts), = load_migrations("wa_test", "dc1", 3)
    assert version == "001_init"
    assert "'dc1': 3" in stmts[0] and all("{keyspace}" not in s for s in stmts)
    assert any("default_time_to_live = 86400" in s for s in stmts)
    with pytest.raises(ValueError):
        load_migrations("bad; DROP", "dc1", 1)
