"""Advisor-facing summary.

The LLM only writes prose. It never decides findings, severities or
recommendations; those come from deterministic code and are passed in as
facts. If the LLM is disabled, unavailable or refuses, the template summary is
used, so the pipeline output never depends on model availability.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..errors import ToolError, TransientToolError, ValidationError
from .base import Tool

SYSTEM_PROMPT = (
    "You are writing an internal briefing for a private-banking wealth advisor. "
    "Use only the facts in the JSON you are given; do not invent numbers, products or events. "
    "Write 4-6 sentences of plain prose: overall risk picture, the most urgent issues, "
    "and what the advisor should do first. Mention when data was incomplete. No headings, no bullet points."
)


def template_summary(facts: dict[str, Any]) -> str:
    name = facts["client"]["name"]
    counts = facts["severity_counts"]
    urgent = [f["title"] for f in facts["findings"] if f["severity"] in ("critical", "high")][:3]
    parts = [
        f"{name} ({facts['client']['risk_profile']} profile) has a risk score of {facts['risk_score']}/100 "
        f"with {counts.get('critical', 0)} critical and {counts.get('high', 0)} high-severity findings."
    ]
    if facts.get("risk_trend"):
        parts.append(facts["risk_trend"])
    if urgent:
        parts.append("Most urgent: " + "; ".join(urgent) + ".")
    if facts["recommendations"]:
        parts.append(f"First action: {facts['recommendations'][0]['action']}.")
    if facts.get("data_gaps"):
        parts.append("Data gaps: " + "; ".join(facts["data_gaps"][:3]) + ".")
    return " ".join(parts)


class NarrativeTool(Tool):
    name = "llm.summarize"
    description = "Write a short advisor briefing from structured facts (LLM with template fallback)."

    def __init__(self, enabled: bool, model: str):
        self.enabled = enabled
        self.model = model

    def run(self, facts: dict[str, Any]) -> dict[str, str]:
        if not self.enabled:
            return {"text": template_summary(facts), "source": "template"}
        return {"text": self._call_claude(facts), "source": self.model}

    def fallback(self, error: ToolError, facts: dict[str, Any]) -> dict[str, str]:
        return {"text": template_summary(facts), "source": "template", "fallback_reason": str(error)}

    def _call_claude(self, facts: dict[str, Any]) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ValidationError("anthropic package not installed (pip install anthropic)", tool=self.name) from exc
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise ValidationError("no Anthropic credentials in environment", tool=self.name)

        client = anthropic.Anthropic(max_retries=0, timeout=60)  # retries are the executor's job
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise TransientToolError(f"Claude API transient error: {exc}", tool=self.name) from exc
        except anthropic.APIStatusError as exc:
            raise ToolError(f"Claude API error {exc.status_code}: {exc.message}", tool=self.name) from exc

        if response.stop_reason == "refusal":
            raise ToolError("model declined to write the summary", tool=self.name)
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise ToolError("model returned no text", tool=self.name)
        return text
