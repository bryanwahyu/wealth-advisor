"""Short-term (session) memory.

Lives for one orchestrator run. Agents share facts through it instead of
passing large objects around, it doubles as the tool-result cache, and its
event list is the run's decision trail. At the end of a run the events are
handed to long-term storage with a TTL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SessionMemory:
    def __init__(self, session_id: str, client_id: str):
        self.session_id = session_id
        self.client_id = client_id
        self._facts: dict[str, Any] = {}
        self._cache: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    # shared facts
    def put(self, key: str, value: Any, *, author: str) -> None:
        self._facts[key] = value
        self.record("fact", author=author, key=key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._facts.get(key, default)

    def has(self, key: str) -> bool:
        return key in self._facts

    # decision trail
    def record(self, kind: str, *, author: str, **payload: Any) -> None:
        self.events.append(
            {
                "seq": len(self.events),
                "at": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "author": author,
                **payload,
            }
        )

    # tool-result cache
    def get_cached(self, key: str) -> Any | None:
        return self._cache.get(key)

    def set_cached(self, key: str, value: Any) -> None:
        self._cache[key] = value


class SessionToolCache:
    """Adapter so the ToolExecutor can use SessionMemory as its cache."""

    def __init__(self, session: SessionMemory):
        self.session = session

    def get(self, key: str) -> Any | None:
        return self.session.get_cached(key)

    def set(self, key: str, value: Any) -> None:
        self.session.set_cached(key, value)
