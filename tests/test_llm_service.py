"""DB-free, network-free unit tests for the Groq LLM helper.

Only the pure prompt-building functions and the configured/guard behaviour are
tested here — no live call to Groq is made.
"""

from __future__ import annotations

import pytest

from llm_service import (
    SUMMARY_INSTRUCTION,
    LLMService,
    LLMUnavailable,
    build_context,
    build_messages,
)

SUBJECT = {
    "address": "10 MAIN ST NW",
    "community": "ALPHA",
    "city": "Calgary",
    "property_type_normalized": "detached",
    "assessed_value": 600000.0,
    "year_built": 2000,
    "land_size_sqm": 500.0,
}
COMPS = [
    {
        "address": "12 MAIN ST NW",
        "sale_price": 615000,
        "sale_date": "2026-04-01T00:00:00Z",
        "score": 95.0,
        "distance_km": 0.2,
        "recency_days": 60,
        "assessed_value_gap_ratio": 0.02,
        "community": "ALPHA",
        "reasons": ["0.20 km away", "same community"],
    }
]


def test_build_context_includes_subject_and_comps():
    ctx = build_context(SUBJECT, COMPS)
    assert "10 MAIN ST NW" in ctx
    assert "RANKED COMPARABLE SALES" in ctx
    assert "12 MAIN ST NW" in ctx
    assert "$615,000" in ctx


def test_build_context_handles_no_comps():
    ctx = build_context(SUBJECT, [])
    assert "(none matched the filters)" in ctx


def test_build_messages_qa_mode():
    msgs = build_messages(SUBJECT, COMPS, "What is a fair offer?", mode="qa")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "QUESTION: What is a fair offer?" in msgs[1]["content"]


def test_build_messages_summary_mode_ignores_question():
    msgs = build_messages(SUBJECT, COMPS, None, mode="summary")
    assert SUMMARY_INSTRUCTION in msgs[1]["content"]


def test_messages_enforce_data_grounding():
    msgs = build_messages(SUBJECT, COMPS, "What is a fair offer?", mode="qa")
    system = msgs[0]["content"]
    user = msgs[1]["content"]
    # system prompt forbids invention / outside knowledge
    assert "Never invent" in system or "never invent" in system.lower()
    assert "outside" in system.lower()
    # user message fences the data and restates the rule
    assert "=== DATA" in user and "=== END DATA ===" in user
    assert "only the DATA" in user


def test_service_not_configured_without_key():
    svc = LLMService(api_key=None, model="x")
    assert svc.configured is False
    with pytest.raises(LLMUnavailable):
        svc.ask(subject=SUBJECT, comparables=COMPS, question="hi")
