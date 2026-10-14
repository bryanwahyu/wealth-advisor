"""ScyllaDB long-term memory.

Design follows the usual Scylla rules:

* Tables are shaped by query (see migrations/*.cql), partitioned by client so
  "latest runs" and "finding history" are single-partition reads.
* Writes for one run go in a LOGGED batch so the run row and its finding rows
  land together.
* Prepared statements, LOCAL_QUORUM, token-aware + DC-aware routing.
* Schema migrations are embedded .cql files, idempotent (IF NOT EXISTS), and
  tracked in `schema_migrations`; they run on startup.
* Driver errors surface as MemoryUnavailableError. No silent fallback.

Requires `pip install scylla-driver` (imported as `cassandra`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .long_term import LongTermMemory, MemoryUnavailableError, RunRecord, now_ms

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")


@dataclass
class ScyllaSettings:
    hosts: list[str]
    port: int = 9042
    keyspace: str = "wealth_advisor"
    local_dc: str = "datacenter1"
    replication_factor: int = 1
    username: str | None = None
    password: str | None = None
    connect_timeout: float = 5.0
    request_timeout: float = 5.0

    @classmethod
    def from_env(cls) -> "ScyllaSettings":
        return cls(
            hosts=[h.strip() for h in os.getenv("SCYLLA_HOSTS", "127.0.0.1").split(",") if h.strip()],
            port=int(os.getenv("SCYLLA_PORT", "9042")),
            keyspace=os.getenv("SCYLLA_KEYSPACE", "wealth_advisor"),
            local_dc=os.getenv("SCYLLA_LOCAL_DC", "datacenter1"),
            replication_factor=int(os.getenv("SCYLLA_RF", "1")),
            username=os.getenv("SCYLLA_USERNAME") or None,
            password=os.getenv("SCYLLA_PASSWORD") or None,
        )


def load_migrations(keyspace: str, local_dc: str, rf: int) -> list[tuple[str, list[str]]]:
    """Return [(version, [statement, ...])] in file order."""
    for name, value in (("keyspace", keyspace), ("local_dc", local_dc)):
        if not _IDENT.match(value):
            raise ValueError(f"invalid {name} {value!r}")
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.cql")):
        text = "\n".join(line for line in path.read_text().splitlines() if not line.strip().startswith("--"))
        text = text.replace("{keyspace}", keyspace).replace("{local_dc}", local_dc).replace("{rf}", str(rf))
        statements = [s.strip() for s in text.split(";") if s.strip()]
        out.append((path.stem, statements))
    return out


class ScyllaMemory(LongTermMemory):
    backend = "scylla"

    def __init__(self, settings: ScyllaSettings):
        try:
            from cassandra import ConsistencyLevel
            from cassandra.auth import PlainTextAuthProvider
            from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
            from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy
        except ImportError as exc:
            raise MemoryUnavailableError("scylla-driver not installed (pip install scylla-driver)") from exc

        self.settings = settings
        profile = ExecutionProfile(
            load_balancing_policy=TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc=settings.local_dc)),
            consistency_level=ConsistencyLevel.LOCAL_QUORUM,
            request_timeout=settings.request_timeout,
        )
        auth = (
            PlainTextAuthProvider(settings.username, settings.password)
            if settings.username and settings.password
            else None
        )
        self.cluster = Cluster(
            settings.hosts,
            port=settings.port,
            auth_provider=auth,
            execution_profiles={EXEC_PROFILE_DEFAULT: profile},
            connect_timeout=settings.connect_timeout,
            protocol_version=4,
        )
        try:
            self.session = self.cluster.connect()
            self._migrate()
            self.session.set_keyspace(settings.keyspace)
            self._prepare()
        except MemoryUnavailableError:
            self.cluster.shutdown()
            raise
        except Exception as exc:
            self.cluster.shutdown()
            raise MemoryUnavailableError(f"cannot connect to Scylla at {settings.hosts}: {exc}") from exc

    def _migrate(self) -> None:
        ks = self.settings.keyspace
        migrations = load_migrations(ks, self.settings.local_dc, self.settings.replication_factor)
        # First statement of 001 creates the keyspace; the tracking table needs it.
        self.session.execute(migrations[0][1][0])
        self.session.execute(
            f"CREATE TABLE IF NOT EXISTS {ks}.schema_migrations (version text PRIMARY KEY, applied_at bigint)"
        )
        applied = {r.version for r in self.session.execute(f"SELECT version FROM {ks}.schema_migrations")}
        for version, statements in migrations:
            if version in applied:
                continue
            for stmt in statements:
                self.session.execute(stmt)
            self.session.execute(
                f"INSERT INTO {ks}.schema_migrations (version, applied_at) VALUES (%s, %s)", (version, now_ms())
            )

    def _prepare(self) -> None:
        p = self.session.prepare
        self.q_insert_run = p(
            "INSERT INTO advisor_runs_by_client (client_id, created_at, run_id, status, risk_score, "
            "finding_codes, summary, report_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        self.q_insert_run_id = p("INSERT INTO advisor_runs_by_id (run_id, client_id, created_at) VALUES (?, ?, ?)")
        self.q_insert_finding = p(
            "INSERT INTO findings_by_client_code (client_id, code, created_at, run_id, severity, title) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        self.q_latest_runs = p(
            "SELECT run_id, client_id, created_at, status, risk_score, finding_codes, summary "
            "FROM advisor_runs_by_client WHERE client_id = ? LIMIT ?"
        )
        self.q_finding_history = p(
            "SELECT run_id, created_at, severity, title FROM findings_by_client_code "
            "WHERE client_id = ? AND code = ? LIMIT ?"
        )
        self.q_insert_event = p(
            "INSERT INTO session_events (session_id, seq, client_id, created_at, kind, payload) VALUES (?, ?, ?, ?, ?, ?)"
        )

    def _batch(self):
        from cassandra.query import BatchStatement, BatchType

        return BatchStatement(batch_type=BatchType.LOGGED)

    def save_run(self, record: RunRecord) -> None:
        batch = self._batch()
        batch.add(
            self.q_insert_run,
            (
                record.client_id,
                record.created_at,
                record.run_id,
                record.status,
                record.risk_score,
                set(record.finding_codes),
                record.summary,
                json.dumps(record.report, default=str) if record.report else None,
            ),
        )
        batch.add(self.q_insert_run_id, (record.run_id, record.client_id, record.created_at))
        for f in record.findings:
            batch.add(
                self.q_insert_finding,
                (record.client_id, f["code"], record.created_at, record.run_id, f["severity"], f["title"]),
            )
        self._execute(batch)

    def latest_runs(self, client_id: str, limit: int = 5) -> list[RunRecord]:
        rows = self._execute(self.q_latest_runs, (client_id, limit))
        return [
            RunRecord(
                run_id=r.run_id,
                client_id=r.client_id,
                created_at=r.created_at,
                status=r.status,
                risk_score=r.risk_score,
                finding_codes=sorted(r.finding_codes or []),
                summary=r.summary,
            )
            for r in rows
        ]

    def finding_history(self, client_id: str, code: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._execute(self.q_finding_history, (client_id, code, limit))
        return [{"run_id": r.run_id, "created_at": r.created_at, "severity": r.severity, "title": r.title} for r in rows]

    def save_session(self, session_id: str, client_id: str, events: list[dict[str, Any]]) -> None:
        # Single partition, so an unlogged batch is cheap and still atomic per partition.
        from cassandra.query import BatchStatement, BatchType

        now = now_ms()
        batch = BatchStatement(batch_type=BatchType.UNLOGGED)
        for e in events:
            batch.add(self.q_insert_event, (session_id, e["seq"], client_id, now, e["kind"], json.dumps(e, default=str)))
        self._execute(batch)

    def _execute(self, statement, params=None):
        try:
            return list(self.session.execute(statement, params)) if params is not None else self.session.execute(statement)
        except Exception as exc:
            raise MemoryUnavailableError(f"scylla query failed: {exc}") from exc

    def close(self) -> None:
        self.cluster.shutdown()
