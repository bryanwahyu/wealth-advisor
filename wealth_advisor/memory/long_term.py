"""Long-term memory: past runs and finding history per client.

Storage is behind `LongTermMemory`. Two implementations:

* `SQLiteMemory`  - zero-setup default, a single file.
* `ScyllaMemory`  - ScyllaDB, see scylla.py.

Contract shared by all backends: a storage/transport failure raises
`MemoryUnavailableError`. It is never turned into an empty result, because
"no history" and "could not read history" lead to different advice (a
recurring finding would be reported as new).
"""

from __future__ import annotations

import json
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import WealthAdvisorError

SESSION_TTL_SECONDS = 24 * 3600


class MemoryUnavailableError(WealthAdvisorError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RunRecord:
    run_id: str
    client_id: str
    created_at: int  # unix ms
    status: str
    risk_score: int
    finding_codes: list[str]
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)  # code, severity, title
    report: dict[str, Any] | None = None


class LongTermMemory(ABC):
    backend: str = "abstract"
    enabled: bool = True

    @abstractmethod
    def save_run(self, record: RunRecord) -> None: ...

    @abstractmethod
    def latest_runs(self, client_id: str, limit: int = 5) -> list[RunRecord]:
        """Newest first."""

    @abstractmethod
    def finding_history(self, client_id: str, code: str, limit: int = 10) -> list[dict[str, Any]]:
        """Previous occurrences of a finding code for a client, newest first."""

    @abstractmethod
    def save_session(self, session_id: str, client_id: str, events: list[dict[str, Any]]) -> None:
        """Persist the session trail; expires after SESSION_TTL_SECONDS."""

    def close(self) -> None:
        pass


class DisabledMemory(LongTermMemory):
    """Used when the configured backend could not be reached at startup. The
    run continues, and every report states that long-term memory was off."""

    backend = "disabled"
    enabled = False

    def __init__(self, reason: str):
        self.reason = reason

    def save_run(self, record: RunRecord) -> None:
        raise MemoryUnavailableError(f"long-term memory disabled: {self.reason}")

    def latest_runs(self, client_id: str, limit: int = 5) -> list[RunRecord]:
        raise MemoryUnavailableError(f"long-term memory disabled: {self.reason}")

    def finding_history(self, client_id: str, code: str, limit: int = 10) -> list[dict[str, Any]]:
        raise MemoryUnavailableError(f"long-term memory disabled: {self.reason}")

    def save_session(self, session_id: str, client_id: str, events: list[dict[str, Any]]) -> None:
        raise MemoryUnavailableError(f"long-term memory disabled: {self.reason}")


class SQLiteMemory(LongTermMemory):
    """Same query shapes as the Scylla tables: runs by client newest-first, and
    findings keyed by (client_id, code)."""

    backend = "sqlite"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS advisor_runs (
        run_id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        status TEXT NOT NULL,
        risk_score INTEGER NOT NULL,
        finding_codes TEXT NOT NULL,
        summary TEXT NOT NULL,
        report_json TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_runs_client ON advisor_runs (client_id, created_at DESC);
    CREATE TABLE IF NOT EXISTS findings_by_client_code (
        client_id TEXT NOT NULL,
        code TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        run_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        PRIMARY KEY (client_id, code, created_at, run_id)
    );
    CREATE TABLE IF NOT EXISTS session_events (
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        client_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (session_id, seq)
    );
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(str(path))
            self.conn.executescript(self.SCHEMA)
        except sqlite3.Error as exc:
            raise MemoryUnavailableError(f"sqlite open failed: {exc}") from exc

    def save_run(self, record: RunRecord) -> None:
        try:
            with self.conn:  # one transaction for run + findings
                self.conn.execute(
                    "INSERT OR REPLACE INTO advisor_runs VALUES (?,?,?,?,?,?,?,?)",
                    (
                        record.run_id,
                        record.client_id,
                        record.created_at,
                        record.status,
                        record.risk_score,
                        json.dumps(sorted(set(record.finding_codes))),
                        record.summary,
                        json.dumps(record.report, default=str) if record.report else None,
                    ),
                )
                self.conn.executemany(
                    "INSERT OR REPLACE INTO findings_by_client_code VALUES (?,?,?,?,?,?)",
                    [
                        (record.client_id, f["code"], record.created_at, record.run_id, f["severity"], f["title"])
                        for f in record.findings
                    ],
                )
        except sqlite3.Error as exc:
            raise MemoryUnavailableError(f"sqlite write failed: {exc}") from exc

    def latest_runs(self, client_id: str, limit: int = 5) -> list[RunRecord]:
        try:
            rows = self.conn.execute(
                "SELECT run_id, client_id, created_at, status, risk_score, finding_codes, summary "
                "FROM advisor_runs WHERE client_id = ? ORDER BY created_at DESC LIMIT ?",
                (client_id, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryUnavailableError(f"sqlite read failed: {exc}") from exc
        return [
            RunRecord(run_id=r[0], client_id=r[1], created_at=r[2], status=r[3], risk_score=r[4],
                      finding_codes=json.loads(r[5]), summary=r[6])
            for r in rows
        ]

    def finding_history(self, client_id: str, code: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            rows = self.conn.execute(
                "SELECT run_id, created_at, severity, title FROM findings_by_client_code "
                "WHERE client_id = ? AND code = ? ORDER BY created_at DESC LIMIT ?",
                (client_id, code, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryUnavailableError(f"sqlite read failed: {exc}") from exc
        return [{"run_id": r[0], "created_at": r[1], "severity": r[2], "title": r[3]} for r in rows]

    def save_session(self, session_id: str, client_id: str, events: list[dict[str, Any]]) -> None:
        now = now_ms()
        try:
            with self.conn:
                self.conn.execute("DELETE FROM session_events WHERE expires_at < ?", (now,))
                self.conn.executemany(
                    "INSERT OR REPLACE INTO session_events VALUES (?,?,?,?,?,?,?)",
                    [
                        (session_id, e["seq"], client_id, now, now + SESSION_TTL_SECONDS * 1000, e["kind"],
                         json.dumps(e, default=str))
                        for e in events
                    ],
                )
        except sqlite3.Error as exc:
            raise MemoryUnavailableError(f"sqlite write failed: {exc}") from exc

    def close(self) -> None:
        self.conn.close()


def build_long_term_memory(backend: str, sqlite_path: Path) -> LongTermMemory:
    """Construct the configured backend. If it cannot be reached at startup the
    run proceeds with memory disabled (and says so) rather than silently
    switching to a different store, which would split a client's history."""
    from ..observability import log_event
    import logging

    try:
        if backend == "sqlite":
            return SQLiteMemory(sqlite_path)
        if backend == "scylla":
            from .scylla import ScyllaMemory, ScyllaSettings

            return ScyllaMemory(ScyllaSettings.from_env())
        if backend == "none":
            return DisabledMemory("disabled by configuration")
        raise ValueError(f"unknown memory backend {backend!r}")
    except MemoryUnavailableError as exc:
        log_event("memory.unavailable", f"{backend} memory unavailable, continuing without it: {exc}",
                  level=logging.ERROR, backend=backend)
        return DisabledMemory(f"{backend}: {exc}")
