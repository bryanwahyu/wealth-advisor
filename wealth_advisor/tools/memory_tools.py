"""Long-term memory exposed as tools, so agents use the same executor path
(retries, logging, circuit breaker) for storage as for any external API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..errors import ToolError, TransientToolError
from ..memory.long_term import DisabledMemory, LongTermMemory, MemoryUnavailableError, RunRecord
from .base import Tool


def _translate(memory: LongTermMemory, exc: MemoryUnavailableError, tool: str) -> ToolError:
    # A disabled store will not come back within this run; a transport error might.
    if isinstance(memory, DisabledMemory):
        return ToolError(str(exc), tool=tool)
    return TransientToolError(str(exc), tool=tool)


class RecallClientHistoryTool(Tool):
    name = "memory.recall"
    description = "Recall previous runs and prior occurrences of finding codes for a client."

    def __init__(self, memory: LongTermMemory):
        self.memory = memory

    def run(self, client_id: str, codes: list[str], limit: int = 5) -> dict[str, Any]:
        try:
            runs = self.memory.latest_runs(client_id, limit=limit)
            history = {code: self.memory.finding_history(client_id, code, limit=limit) for code in codes}
        except MemoryUnavailableError as exc:
            raise _translate(self.memory, exc, self.name) from exc
        return {"runs": [asdict(r) for r in runs], "finding_history": history, "backend": self.memory.backend}


class StoreRunTool(Tool):
    name = "memory.store_run"
    description = "Persist a run summary and its findings to long-term memory."

    def __init__(self, memory: LongTermMemory):
        self.memory = memory

    def run(self, record: RunRecord) -> dict[str, str]:
        try:
            self.memory.save_run(record)
        except MemoryUnavailableError as exc:
            raise _translate(self.memory, exc, self.name) from exc
        return {"backend": self.memory.backend, "run_id": record.run_id}


class StoreSessionTool(Tool):
    name = "memory.store_session"
    description = "Persist the session event trail (expires after 24h)."

    def __init__(self, memory: LongTermMemory):
        self.memory = memory

    def run(self, session_id: str, client_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            self.memory.save_session(session_id, client_id, events)
        except MemoryUnavailableError as exc:
            raise _translate(self.memory, exc, self.name) from exc
        return {"backend": self.memory.backend, "events": len(events)}
