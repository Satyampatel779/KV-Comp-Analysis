"""Groq-backed LLM helper for grounded property Q&A and comp summaries.

Uses Groq's OpenAI-compatible Chat Completions endpoint over ``httpx`` (no extra
SDK dependency). Every answer is grounded ONLY in the structured subject +
ranked-comparable-sales data we pass in: the model is instructed not to invent
facts and to frame any value opinion as a range with caveats. Prompt-building is
kept in module-level pure functions so it can be unit-tested without a network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

SYSTEM_PROMPT = (
    "You are KV Capital's Calgary residential comp-analysis assistant. "
    "Answer ONLY using the structured data provided below (the subject property "
    "and its ranked comparable sales). Do not invent facts, sales, or attributes "
    "that are not present in the data, and do not use outside market knowledge. "
    "Cite comparables by their list number in square brackets, e.g. [#1], [#2], so "
    "each claim is traceable to a specific comp row. "
    "When asked for a value or offer estimate, give a RANGE grounded in the "
    "comparable sale prices, briefly explain the basis, and note it is an "
    "automated estimate — not a formal appraisal or financial advice. "
    "If the provided data is insufficient to answer, say so plainly rather than guessing."
)

SUMMARY_INSTRUCTION = (
    "Write a brief comp-analysis summary for an underwriter: (1) the implied "
    "value range with a one-line basis, (2) the 2-3 strongest comparable sales "
    "and why they are strong, (3) any caveats (thin data, wide spread, stale "
    "sales, distance). Keep it under 180 words."
)


class LLMUnavailable(RuntimeError):
    """Raised when the LLM is not configured or the upstream call fails."""


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def build_context(subject: dict[str, Any], comparables: list[dict[str, Any]], max_comps: int = 12) -> str:
    """Render a compact, model-friendly view of the subject + top comps."""
    s = subject or {}
    lines = [
        "SUBJECT PROPERTY:",
        f"- address: {s.get('address') or 'n/a'}",
        f"- community: {s.get('community') or 'n/a'} | city: {s.get('city') or 'n/a'}",
        f"- type: {s.get('property_type_normalized') or 'n/a'}",
        f"- assessed_value: {_money(s.get('assessed_value'))}",
        f"- year_built: {s.get('year_built') or 'n/a'} | land_size_sqm: {s.get('land_size_sqm') or 'n/a'}",
        "",
        f"RANKED COMPARABLE SALES (top {min(len(comparables), max_comps)} of {len(comparables)}):",
    ]
    if not comparables:
        lines.append("- (none matched the filters)")
    for i, c in enumerate(comparables[:max_comps], start=1):
        reasons = c.get("reasons") or []
        lines.append(
            f"{i}. {c.get('address') or 'n/a'} | sold {_money(c.get('sale_price'))} "
            f"on {(c.get('sale_date') or 'n/a')[:10]} | score {c.get('score')} | "
            f"{c.get('distance_km')} km | {c.get('recency_days')} days ago | "
            f"value_gap {c.get('assessed_value_gap_ratio')} | "
            f"community {c.get('community') or 'n/a'}"
            + (f" | {', '.join(reasons)}" if reasons else "")
        )
    return "\n".join(lines)


def build_messages(
    subject: dict[str, Any],
    comparables: list[dict[str, Any]],
    question: str | None,
    mode: str = "qa",
) -> list[dict[str, str]]:
    """Build the chat messages for a grounded Q&A or summary request."""
    context = build_context(subject, comparables)
    if mode == "summary":
        user = f"{context}\n\n{SUMMARY_INSTRUCTION}"
    else:
        q = (question or "").strip() or "Summarize the comparable sales for this subject."
        user = f"{context}\n\nQUESTION: {q}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


class LLMService:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 45.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def ask(
        self,
        *,
        subject: dict[str, Any],
        comparables: list[dict[str, Any]],
        question: str | None = None,
        mode: str = "qa",
        temperature: float = 0.2,
        max_tokens: int = 700,
    ) -> dict[str, Any]:
        if not self.configured:
            raise LLMUnavailable(
                "LLM is not configured. Set GROQ_API_KEY (and optionally GROQ_MODEL)."
            )

        messages = build_messages(subject, comparables, question, mode)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:  # network/timeout
            raise LLMUnavailable(f"LLM request failed: {exc}") from exc

        if resp.status_code != 200:
            detail = resp.text[:300]
            raise LLMUnavailable(f"LLM upstream error {resp.status_code}: {detail}")

        body = resp.json()
        try:
            answer = body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise LLMUnavailable("LLM returned an unexpected response shape.") from exc

        return {
            "answer": answer,
            "model": self.model,
            "mode": mode,
            "used_comps": min(len(comparables), 12),
        }

    def stream(
        self,
        *,
        subject: dict[str, Any],
        comparables: list[dict[str, Any]],
        question: str | None = None,
        mode: str = "qa",
        temperature: float = 0.2,
        max_tokens: int = 700,
    ) -> Iterator[str]:
        """Yield answer text deltas as they arrive (Groq SSE streaming)."""
        if not self.configured:
            raise LLMUnavailable("LLM is not configured. Set GROQ_API_KEY.")

        messages = build_messages(subject, comparables, question, mode)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        body = resp.read().decode("utf-8", "replace")[:300]
                        raise LLMUnavailable(f"LLM upstream error {resp.status_code}: {body}")
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[len("data: "):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, json.JSONDecodeError):
                            continue
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM streaming request failed: {exc}") from exc
