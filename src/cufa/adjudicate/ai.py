"""Tier 2: ask Gemini whether an answer indicates the person heard the word.

Only ``mismatch``-in-window cases arrive here. Everything tier 1 can decide,
tier 1 decides — a model call is slower, costs money, is rate limited, and is
not reproducible, so it earns its place only where edit distance genuinely
cannot help:

    "the word was justice"   — correct, and edit distance 15 away
    "justice i think?"       — correct, hedged
    "jushtis"                — heard it, spelled it phonetically
    "sorry I missed it"      — honest, and not a passphrase at all

A human reads all four instantly. Levenshtein scores all four as failures.

**Only two strings are ever sent**: the expected passphrase and the submitted
answer. No names, no emails, no attendance history, no cohort. That is better
privacy and better accuracy at the same time — the model's only job is judging
whether the answer indicates the person heard the word, and everything else is
context it could be wrong about.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from ..config import Settings, get_settings
from ..db import execute, fetch_one
from ..errors import AiUnavailable
from ..logging_setup import get_logger
from ..text import normalize_answer

log = get_logger(__name__)

# Bump this whenever the prompt text or the schema changes. It is part of the
# cache key, so a changed prompt invalidates cleanly instead of silently reusing
# verdicts that were produced by different instructions.
PROMPT_VERSION = "v1"

# Passed through ``types.Schema`` at call time rather than handed to the API as
# a raw dict: the SDK validates and normalizes it there (``"object"`` becomes
# ``Type.OBJECT``), so a malformed schema fails locally with a readable error
# instead of coming back as an opaque 400 mid-run.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "heard_the_passphrase": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["heard_the_passphrase", "confidence", "reasoning"],
}

PROMPT_TEMPLATE = """\
A teacher said one passphrase aloud during a live online lesson and displayed it \
on screen. Afterwards each student typed what they heard into a form.

Expected passphrase: {expected}
Student's answer: {submitted}

Decide whether the student's answer indicates they heard the passphrase.

Count as heard:
- the word inside a sentence ("the word was justice", "i think it was justice")
- a phonetic or misspelled attempt at the word ("jushtis", "justise")
- the word with hedging, extra punctuation, or commentary

Count as NOT heard:
- a different word with no relation to the expected one
- an admission of not knowing it ("sorry I missed it", "i don't know", "?")
- an empty or meaningless answer

Reply with JSON only: heard_the_passphrase (boolean), confidence (0.0-1.0), \
reasoning (one sentence).\
"""


@dataclass(frozen=True)
class AiVerdict:
    """A tier 2 judgment, whether it came from the model or from the cache."""

    heard_the_passphrase: bool
    confidence: float
    reasoning: str
    cached: bool = False


class Adjudicator(Protocol):
    """What the engine needs from tier 2. Injectable so tests never hit a network."""

    model_name: str

    def judge(self, expected: str, submitted: str) -> AiVerdict:
        ...


def cache_get(
    conn: psycopg.Connection, expected: str, submitted: str, model: str
) -> AiVerdict | None:
    row = fetch_one(
        conn,
        """
        select verdict, confidence, reasoning
          from ai_adjudication_cache
         where expected_normalized = %s and submitted_normalized = %s
           and prompt_version = %s and model = %s
        """,
        (expected, submitted, PROMPT_VERSION, model),
    )
    if not row:
        return None
    return AiVerdict(
        heard_the_passphrase=row["verdict"],
        confidence=float(row["confidence"]),
        reasoning=row["reasoning"],
        cached=True,
    )


def cache_put(
    conn: psycopg.Connection, expected: str, submitted: str, model: str, verdict: AiVerdict
) -> None:
    execute(
        conn,
        """
        insert into ai_adjudication_cache
            (expected_normalized, submitted_normalized, prompt_version, model,
             verdict, confidence, reasoning)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (expected_normalized, submitted_normalized, prompt_version, model)
        do nothing
        """,
        (
            expected,
            submitted,
            PROMPT_VERSION,
            model,
            verdict.heard_the_passphrase,
            round(float(verdict.confidence), 3),
            verdict.reasoning,
        ),
    )


class GeminiAdjudicator:
    """The live tier 2 client.

    Uses ``google-genai`` (``from google import genai``). The older
    ``google-generativeai`` package was deprecated in August 2025 and is not
    used here.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        if not settings.gemini_api_key:
            raise AiUnavailable(
                "GEMINI_API_KEY is not set, so tier 2 cannot run. This is not "
                "fatal: mismatch cases will be written as needs_review."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise AiUnavailable(f"google-genai is not importable: {exc}") from exc

        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.ai_model

    def judge(self, expected: str, submitted: str) -> AiVerdict:
        from google.genai import types

        prompt = PROMPT_TEMPLATE.format(
            expected=expected or "(none)", submitted=submitted or "(blank)"
        )
        config = types.GenerateContentConfig(
            # Reproducibility: the same two strings must produce the same verdict,
            # or the cache would be lying about what a fresh call would return.
            temperature=0,
            response_mime_type="application/json",
            response_schema=types.Schema(**RESPONSE_SCHEMA),
        )

        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.models.generate_content(
                    model=self.model_name, contents=prompt, config=config
                )
                return _parse_verdict(response.text)
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if _is_rate_limit(exc) and attempt < 3:
                    delay = min(2**attempt, 16) * (0.5 + random.random() / 2)
                    log.warning("gemini rate limited; retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                break

        raise AiUnavailable(f"Gemini call failed: {last_error}") from last_error


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text


def _parse_verdict(text: str | None) -> AiVerdict:
    if not text:
        raise AiUnavailable("Gemini returned an empty response")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise AiUnavailable(f"Gemini returned non-JSON: {text[:200]!r}") from exc

    try:
        confidence = float(payload["confidence"])
    except (KeyError, TypeError, ValueError):
        confidence = 0.5
    return AiVerdict(
        heard_the_passphrase=bool(payload.get("heard_the_passphrase", False)),
        confidence=max(0.0, min(1.0, confidence)),
        reasoning=str(payload.get("reasoning", "")).strip() or "(no reasoning returned)",
    )


def build_adjudicator(settings: Settings | None = None) -> Adjudicator | None:
    """Return a live adjudicator, or None when tier 2 cannot run.

    None rather than an exception: the caller degrades to needs_review, and a
    missing API key is a configuration state, not a failure of this run.
    """
    settings = settings or get_settings()
    try:
        return GeminiAdjudicator(settings)
    except AiUnavailable as exc:
        log.info("tier 2 unavailable: %s", str(exc).splitlines()[0])
        return None


def judge_with_cache(
    conn: psycopg.Connection, adjudicator: Adjudicator, expected: str, submitted: str
) -> AiVerdict:
    """Cache-first tier 2. The same pair of strings is never sent twice."""
    expected_norm = normalize_answer(expected)
    submitted_norm = normalize_answer(submitted)

    hit = cache_get(conn, expected_norm, submitted_norm, adjudicator.model_name)
    if hit is not None:
        return hit

    verdict = adjudicator.judge(expected_norm, submitted_norm)
    cache_put(conn, expected_norm, submitted_norm, adjudicator.model_name, verdict)
    return verdict
