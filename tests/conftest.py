from __future__ import annotations

import pytest

from wealth_advisor.app import build_system
from wealth_advisor.config import PROJECT_ROOT, Settings
from wealth_advisor.hitl import AutoApproveReviewer
from wealth_advisor.observability import configure_logging


@pytest.fixture(autouse=True)
def _quiet_logs():
    configure_logging(None, "test", verbose=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = PROJECT_ROOT / "data"
    s.output_dir = tmp_path / "out"
    s.memory_db = tmp_path / "memory.sqlite3"
    s.tool_backoff_seconds = 0
    return s


@pytest.fixture
def make_system(settings):
    systems = []

    def _make(reviewer=None, memory_backend="sqlite", **overrides):
        for k, v in overrides.items():
            setattr(settings, k, v)
        system = build_system(settings, reviewer or AutoApproveReviewer(), memory_backend=memory_backend)
        systems.append(system)
        return system

    yield _make
    for s in systems:
        s.close()
