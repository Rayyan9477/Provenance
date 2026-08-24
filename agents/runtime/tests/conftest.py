"""Fakes for the model-router suite — no API key, no socket, no google.genai client.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` sections 7, 8, 9.
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*, whose lesson —
  a documented model id is not an invocable one — is why every id in this
  suite is injected rather than assumed.
- ``docs/quality/20_TDD_STRATEGY.md`` section 4.2 rule 3: no wall-clock read in
  a test. :class:`SteppingClock` is the substitute, seeded from the repository
  ``frozen_clock`` fixture.

Why the fakes are scripts rather than mocks
-------------------------------------------
:class:`ScriptedClient` is handed an ordered list of *outcomes* and records
every request it was given. A test therefore asserts on **what was sent** and
on **how many times**, which is the only way to prove a budget. A mock that
answers any call the same way cannot fail the "never three calls" assertion,
which is the single assertion this phase exists to make.

This file will grow into the cassette player named by ``T7.7``; the scripted
client is the same seam a cassette plays through.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from google.genai import types
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# A clock that advances, without reading the wall clock
# ---------------------------------------------------------------------------


@dataclass
class SteppingClock:
    """Returns ``instant``, then ``instant + step``, then ``instant + 2*step``.

    Duration arithmetic in the router is integer milliseconds, so the step is
    chosen to make every recorded duration a round, asserted number rather than
    an incidental one.
    """

    instant: datetime
    step: timedelta = timedelta(milliseconds=250)
    reads: int = 0

    def __call__(self) -> datetime:
        current = self.instant + self.step * self.reads
        self.reads += 1
        return current


@pytest.fixture
def clock(frozen_clock: Any) -> SteppingClock:
    return SteppingClock(instant=frozen_clock.now_utc())


# ---------------------------------------------------------------------------
# A toy output contract
# ---------------------------------------------------------------------------


class ToyOutput(BaseModel):
    """Stands in for ``ExtractionResult`` / ``ResolutionAssessment``.

    It is deliberately tiny: this suite tests the router's budget and
    attribution, not the extraction schema, which ``T7.2`` owns.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_summary: str
    amount_decimal: Decimal | None = None


SUMMARY_SENTENCE_CAP = 2


def toy_semantic_validator(value: ToyOutput) -> list[Any]:
    """One semantic check the JSON Schema layer cannot express (section 7.1)."""
    from agents.runtime.model_router import ValidationFailure

    if value.artifact_summary.count(".") > SUMMARY_SENTENCE_CAP:
        return [
            ValidationFailure(
                path="artifact_summary",
                code="SUMMARY_TOO_LONG",
                detail="artifact_summary is at most 2 sentences",
            )
        ]
    return []


VALID_JSON = '{"artifact_summary": "One sentence.", "amount_decimal": "186.00"}'
TOO_LONG_JSON = '{"artifact_summary": "A. B. C. D.", "amount_decimal": null}'
UNPARSEABLE_JSON = '{"artifact_summary": '
WRONG_SHAPE_JSON = '{"artifact_summary": 4, "surprise": true}'


@pytest.fixture
def contract() -> Any:
    from agents.runtime.model_router import OutputContract

    return OutputContract(model=ToyOutput, validate=toy_semantic_validator)


# ---------------------------------------------------------------------------
# Scripted transport for gemini.GeminiClient
# ---------------------------------------------------------------------------


def gemini_response(
    *,
    text: str | None = VALID_JSON,
    finish_reason: types.FinishReason = types.FinishReason.STOP,
    model_version: str = "gemini-3.7-flash",
    prompt_tokens: int = 1200,
    output_tokens: int = 340,
    thought_tokens: int = 90,
    cached_tokens: int = 0,
) -> types.GenerateContentResponse:
    """A real ``GenerateContentResponse``, built by hand rather than mocked."""
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thought_tokens,
            cached_content_token_count=cached_tokens,
            total_token_count=prompt_tokens + output_tokens + thought_tokens,
        ),
        model_version=model_version,
    )


@dataclass
class RecordedGenerate:
    """Captures the exact kwargs ``google.genai`` would have received."""

    model: str
    contents: Any
    config: types.GenerateContentConfig


@dataclass
class ScriptedGenerate:
    """A stand-in for ``client.models.generate_content``.

    Each element of *outcomes* is either a response to return or an exception
    to raise. Running off the end is an error rather than a repeat, because a
    test that silently gets an extra call is a test that cannot prove a budget.
    """

    outcomes: Sequence[Any]
    calls: list[RecordedGenerate] = field(default_factory=list)

    def __call__(
        self, *, model: str, contents: Any, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append(RecordedGenerate(model=model, contents=contents, config=config))
        if len(self.calls) > len(self.outcomes):
            raise AssertionError(
                f"the router made call {len(self.calls)}; the script has "
                f"{len(self.outcomes)} outcome(s). An extra call is a budget violation."
            )
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


# ---------------------------------------------------------------------------
# Scripted ModelClient for the router suite
# ---------------------------------------------------------------------------


@dataclass
class ScriptedClient:
    """Implements the ``ModelClient`` protocol from an ordered script."""

    outcomes: Sequence[Any]
    requests: list[Any] = field(default_factory=list)

    def generate(self, request: Any) -> Any:
        self.requests.append(request)
        if len(self.requests) > len(self.outcomes):
            raise AssertionError(
                f"the router made call {len(self.requests)}; the script has "
                f"{len(self.outcomes)} outcome(s). An extra call is a budget violation."
            )
        outcome = self.outcomes[len(self.requests) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def model_ids(self) -> list[str]:
        return [request.model_id for request in self.requests]


def ok(text: str = VALID_JSON, *, model_version: str = "gemini-3.7-flash") -> Any:
    """A successful :class:`ModelResponse`."""
    from agents.runtime.model_router import ModelResponse

    return ModelResponse(
        text=text,
        model_version=model_version,
        input_tokens=1200,
        output_tokens=340,
        thought_tokens=90,
        cached_input_tokens=0,
        truncated=False,
    )


def truncated() -> Any:
    """A ``MAX_TOKENS`` stop: section 9.3's joint-budget failure mode."""
    from agents.runtime.model_router import ModelResponse

    return ModelResponse(
        text='{"artifact_summary": "unfin',
        model_version="gemini-3.7-flash",
        input_tokens=1200,
        output_tokens=8192,
        thought_tokens=7000,
        cached_input_tokens=0,
        truncated=True,
    )


ENV: dict[str, str] = {
    "GOOGLE_API_KEY": "not-a-real-key",
    "GEMINI_REASONING_MODEL_ID": "gemini-3.7-flash",
    "GEMINI_EXTRACTION_MODEL_ID": "gemini-3.5-flash-lite",
    "GEMINI_REASONING_FALLBACK_MODEL_ID": "gemini-3.6-flash",
}


@pytest.fixture
def env() -> dict[str, str]:
    return dict(ENV)


@pytest.fixture
def config(env: dict[str, str]) -> Any:
    from agents.runtime.model_router import GeminiRouterConfig

    return GeminiRouterConfig.from_env(env)


def iter_text_parts(contents: Any) -> Iterator[tuple[str, str]]:
    """``(role, text)`` for every part of a rendered ``contents`` list."""
    for content in contents:
        for part in content.parts or []:
            if part.text is not None:
                yield (content.role or "", part.text)
