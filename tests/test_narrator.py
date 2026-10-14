"""LLM briefing path, exercised with a stand-in `anthropic` module (no network)."""

from __future__ import annotations

import sys
import types

import pytest

from wealth_advisor.errors import ToolError
from wealth_advisor.tools.base import ToolExecutor, ToolRegistry
from wealth_advisor.tools.narrator import NarrativeTool

FACTS = {
    "client": {"name": "A", "risk_profile": "moderate", "currency": "IDR"},
    "risk_score": 80,
    "risk_trend": None,
    "severity_counts": {"critical": 1},
    "findings": [{"code": "X", "severity": "critical", "title": "Bad thing", "detail": "d"}],
    "recommendations": [{"priority": 1, "action": "Call client"}],
    "data_gaps": [],
}


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    def __init__(self, stop_reason, text):
        self.stop_reason = stop_reason
        self.content = [_Block(text)]


@pytest.fixture
def fake_anthropic(monkeypatch):
    calls = []
    mod = types.ModuleType("anthropic")

    class APIStatusError(Exception):
        status_code, message = 400, "bad"

    for name in ("RateLimitError", "APIConnectionError", "InternalServerError"):
        setattr(mod, name, type(name, (Exception,), {}))
    mod.APIStatusError = APIStatusError
    mod.response = _Response("end_turn", "Briefing from model.")

    class Anthropic:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

        def _create(self, **kw):
            calls.append(kw)
            return mod.response

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    mod.calls = calls
    return mod


def test_llm_writes_briefing_from_facts_only(fake_anthropic):
    out = NarrativeTool(enabled=True, model="claude-opus-5-5").run(facts=FACTS)
    assert out == {"text": "Briefing from model.", "source": "claude-opus-5-5"}
    (call,) = fake_anthropic.calls
    assert call["model"] == "claude-opus-5-5"
    assert '"risk_score": 80' in call["messages"][0]["content"]


def test_refusal_falls_back_to_template(fake_anthropic):
    fake_anthropic.response = _Response("refusal", "")
    reg = ToolRegistry()
    reg.register(NarrativeTool(enabled=True, model="claude-opus-5-5"))
    res = ToolExecutor(reg, backoff_seconds=0).call("llm.summarize", facts=FACTS)
    assert res.ok and res.degraded and res.data["source"] == "template"
    assert "risk score of 80/100" in res.data["text"]


def test_missing_credentials_is_an_error(fake_anthropic, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ToolError):
        NarrativeTool(enabled=True, model="claude-opus-5-5").run(facts=FACTS)
