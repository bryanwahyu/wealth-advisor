"""Runs only when a Scylla node is reachable (docker compose up -d scylla)
and SCYLLA_HOSTS is set."""

from __future__ import annotations

import os
import uuid

import pytest

from wealth_advisor.memory.long_term import MemoryUnavailableError, RunRecord, now_ms

pytestmark = pytest.mark.skipif(not os.getenv("SCYLLA_HOSTS"), reason="SCYLLA_HOSTS not set")


@pytest.fixture(scope="module")
def scylla():
    from wealth_advisor.memory.scylla import ScyllaMemory, ScyllaSettings

    settings = ScyllaSettings.from_env()
    settings.keyspace = "wealth_advisor_test"
    try:
        mem = ScyllaMemory(settings)
    except MemoryUnavailableError as exc:
        pytest.skip(f"scylla not reachable: {exc}")
    yield mem
    mem.session.execute("DROP KEYSPACE IF EXISTS wealth_advisor_test")
    mem.close()


def test_roundtrip_latest_first(scylla):
    client = f"T-{uuid.uuid4().hex[:6]}"
    for i, score in enumerate((40, 70)):
        scylla.save_run(RunRecord(
            run_id=f"r{i}", client_id=client, created_at=now_ms() + i, status="completed", risk_score=score,
            finding_codes=["DRAWDOWN"], summary="s",
            findings=[{"code": "DRAWDOWN", "severity": "medium", "title": "t"}],
        ))
    runs = scylla.latest_runs(client)
    assert [r.risk_score for r in runs] == [70, 40]
    assert len(scylla.finding_history(client, "DRAWDOWN")) == 2
    assert scylla.finding_history(client, "OTHER") == []


def test_session_events(scylla):
    scylla.save_session("s1", "T", [{"seq": 0, "kind": "fact"}, {"seq": 1, "kind": "decision"}])
    rows = list(scylla.session.execute("SELECT ttl(payload) FROM session_events WHERE session_id = 's1'"))
    assert len(rows) == 2 and all(0 < r[0] <= 86400 for r in rows)
