"""CRM API tool.

`MockCRMService` stands in for a remote HTTP API: it serves records from a JSON
file and can be told to misbehave (flaky, down, slow) so failure paths can be
exercised from the CLI. The tool only knows the `CRMClient` protocol, so a real
HTTP client can replace the mock without touching agents.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from ..errors import NotFoundError, TransientToolError
from .base import Tool


class CRMClient(Protocol):
    def get_client(self, client_id: str) -> dict[str, Any]: ...


class MockCRMService:
    def __init__(self, data_file: Path, failure_mode: str = "none", timeout_s: float = 0.2):
        self.data_file = data_file
        self.failure_mode = failure_mode
        self.timeout_s = timeout_s
        self.calls = 0

    def get_client(self, client_id: str) -> dict[str, Any]:
        self.calls += 1
        if self.failure_mode == "down":
            raise TransientToolError("CRM returned HTTP 503 Service Unavailable")
        if self.failure_mode == "flaky" and self.calls == 1:
            raise TransientToolError("CRM returned HTTP 502 Bad Gateway")
        if self.failure_mode == "slow":
            time.sleep(self.timeout_s)
            raise TransientToolError(f"CRM request timed out after {self.timeout_s}s")

        records = json.loads(self.data_file.read_text(encoding="utf-8"))
        record = records.get(client_id)
        if record is None:
            raise NotFoundError(f"CRM has no record for {client_id} (HTTP 404)")
        return record


class CRMTool(Tool):
    name = "crm.get_client"
    description = "Fetch relationship data (KYC, advisor, contact history, open tickets) from the CRM."
    cacheable = True

    def __init__(self, client: CRMClient):
        self.client = client

    def run(self, client_id: str) -> dict[str, Any]:
        record = self.client.get_client(client_id)
        record.setdefault("client_id", client_id)
        return record
