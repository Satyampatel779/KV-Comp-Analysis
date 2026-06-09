"""Groq-backed LLM helper for grounded property Q&A and comp summaries.

Uses Groq's OpenAI-compatible Chat Completions endpoint over ``httpx`` (no extra
SDK dependency). Every answer is grounded ONLY in the structured subject +
ranked-comparable-sales data we pass in: the model is instructed not to invent
facts and to frame any value opinion as a range with caveats. Prompt-building is
kept in module-level pure functions so it can be unit-tested without a network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx

SYSTEM_PROMPT = (
    "You are KV Capital's Calgary residential comp-analysis assistant. You answer "
    "STRICTLY from the DATA block in the user message and from nothing else.\n"
    "HARD RULES — follow all of them:\n"
    "1. Use ONLY the subject and comparable-sales facts in the DATA. Never invent, "
    "estimate, or recall any address, price, date, distance, size, year, or other "
    "number that is not explicitly present in the DATA.\n"
    "2. Do NOT use outside or prior market knowledge, neighbourhood reputation, or "
    "assumptions. If a fact is not in the DATA, state that it is not available.\n"
    "3. The ONLY new numbers you may produce are simple aggregations of the comparable "
    "sale prices that are already listed (their minimum, maximum, median, or average). "
    "Do not apply growth rates, adjustments, or invented multipliers of your own.\n"
    "4. Cite every comparable you rely on by its list number in square brackets, e.g. "
    "[#1], [#3], so each statement is traceable to a specific row.\n"
    "5. If the DATA is insufficient to answer, say exactly what is missing and stop — "
    "do not guess or fill gaps.\n"
    "6. Any value or offer figure must be a RANGE taken from the listed comparable "
    "sale prices, and you must append: 'Automated estimate from the listed comps — "
    "not a formal appraisal or financial advice.'\n"
    "Be concise, factual, and do not speculate."
)

SUMMARY_INSTRUCTION = (
    "Write a brief comp-analysis summary for an underwriter: (1) the implied "
    "value range with a one-line basis, (2) the 2-3 strongest comparable sales "
    "(cite [#n]) and why they are strong, (3) data caveats (thin data, wide "
    "spread, stale sales, distance). (4) End with a 'Verify manually' line "
    "listing factors NOT captured in the data that a human must confirm before "
    "relying on these comps: what each property backs onto, walkout basement, "
    "legal/secondary suite, renovation quality, and whether each sale was truly "
    "arms-length. Do not assert values for those factors — only flag them as "
    "unknowns to check. Keep it under 200 words."
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
    """Build the chat messages for a grounded Q&A or summary request.

    The subject + comps are wrapped in an explicit ``DATA`` fence and the answer
    instruction is restated after it, so the model is anchored to those facts and
    cannot draw on outside knowledge.
    """
    context = build_context(subject, comparables)
    if mode == "summary":
        task = SUMMARY_INSTRUCTION
    else:
        q = (question or "").strip() or "Summarize the comparable sales for this subject."
        task = f"QUESTION: {q}"
    user = (
        "=== DATA (the ONLY facts you may use) ===\n"
        f"{context}\n"
        "=== END DATA ===\n\n"
        f"{task}\n\n"
        "Answer using only the DATA above. Do not introduce any number, address, or "
        "fact that is not in it; the only new numbers allowed are min/max/median/average "
        "of the listed comp sale prices. Cite comps as [#n]. If the DATA cannot answer "
        "the question, say what is missing instead of guessing."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


_CITE_RE = re.compile(r"\[#(\d+)\]")
_DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


def verify_grounding(
    answer: str, subject: dict[str, Any], comparables: list[dict[str, Any]]
) -> dict[str, Any]:
    """Programmatically check the answer obeys the grounding contract.

    Enforces what the prompt only asks for: every ``[#n]`` citation must point at
    a real comp, and every dollar figure must fall within the listed comps'
    price range (sale + time-adjusted) — or the subject's assessed value — so the
    model can't smuggle in an invented number. Returns the parsed citations and
    any violations (a flag, not a hard block).
    """
    n = len(comparables)
    warnings: list[str] = []

    cited = sorted({int(m) for m in _CITE_RE.findall(answer or "")})
    bad = [c for c in cited if c < 1 or c > n]
    if bad:
        warnings.append(f"cites [#{', #'.join(map(str, bad))}] but only [#1]–[#{n}] exist")

    allowed: list[float] = []
    for c in comparables:
        for key in ("sale_price", "time_adjusted_price"):
            v = c.get(key)
            if isinstance(v, (int, float)):
                allowed.append(float(v))
    sv = (subject or {}).get("assessed_value")
    if isinstance(sv, (int, float)):
        allowed.append(float(sv))

    figures = [float(x.replace(",", "")) for x in _DOLLAR_RE.findall(answer or "")]
    if allowed and figures:
        lo, hi = min(allowed) * 0.97, max(allowed) * 1.03
        outside = [f for f in figures if f >= 1000 and not (lo <= f <= hi)]
        if outside:
            shown = ", ".join(f"${f:,.0f}" for f in sorted(set(outside)))
            warnings.append(
                f"dollar figure(s) {shown} fall outside the comp range "
                f"(${min(allowed):,.0f}–${max(allowed):,.0f})"
            )

    return {"ok": not warnings, "cited": cited, "warnings": warnings}


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
        temperature: float = 0.0,
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

        verification = verify_grounding(answer, subject, comparables)
        if not verification["ok"]:
            answer += "\n\n⚠️ Automated grounding check: " + "; ".join(verification["warnings"]) + "."

        return {
            "answer": answer,
            "model": self.model,
            "mode": mode,
            "used_comps": min(len(comparables), 12),
            "verification": verification,
        }

    def stream(
        self,
        *,
        subject: dict[str, Any],
        comparables: list[dict[str, Any]],
        question: str | None = None,
        mode: str = "qa",
        temperature: float = 0.0,
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
                    chunks: list[str] = []
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
                            chunks.append(delta)
                            yield delta
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM streaming request failed: {exc}") from exc

        # Same grounding guard as ask(), appended once the full answer is known.
        verification = verify_grounding("".join(chunks), subject, comparables)
        if not verification["ok"]:
            yield "\n\n⚠️ Automated grounding check: " + "; ".join(verification["warnings"]) + "."
