"""Section 8.33: invoke the configured model ids and record what answered.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` section 8.0, rows 8.33 and 8.34 -- the route
  index owns their existence, auth, scope and error set. There is no section
  body for either; this module implements what the register entry named:
  "the model router. The probe invokes the configured Gemini ids and records
  what answered".
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon*.
- ``ops/gemini-probe.txt`` -- the transcript whose absence was the blocker the
  register recorded. It exists, so the blocker is discharged.

Why the probe lives beside the counterfactual
----------------------------------------------
Both are judge-mode endpoints whose whole content is *a model call actually
made*, and both hang off the same router seam. Sharing the seam is the point:
a probe that reported on a client the counterfactual does not use would report
on a different system.

What a probe is allowed to conclude
------------------------------------
Only what it observed. ``PASS`` means an id was invoked and answered;
``CANNOT_RUN`` means no call was attempted and says why -- no key, or an id
outside the allow-set. Those are three outcomes and not two (``D-00-005``), and
the counts are reported separately, because ``ops/gemini-probe.txt``'s own
history is that two of its first verdicts described the probe rather than the
model.

No caching, no shortcuts
-------------------------
A probe that returned a previous result would be answering a question about the
past while looking like a question about now, which is the only question this
endpoint exists to answer.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from agents.runtime.model_router.gemini import ModelRequest
from agents.runtime.model_router.models import (
    GeminiRouterConfig,
    ModelConfigError,
    ModelInvocationError,
    ModelRefusalError,
)

__all__ = ["PROBE_MAX_OUTPUT_TOKENS", "ModelProbeService", "ProbeAnswer"]

#: Small, but not as small as it looks like it could be. The probe asks whether
#: an id answers under this build's transport, not whether it can write an
#: essay -- and the first live run set this to 64 and reported
#: ``gemini-3.7-flash`` FAIL: "answered 4 characters that do not satisfy
#: ProbeAnswer". Measured at 256 on 2026-08-25 the same id returns
#: ``{"ok":true,"echo":"PROVENANCE"}`` in **11 output tokens** and **67 thought
#: tokens** -- with ``thinking=False``. The reasoning model thinks anyway, the
#: thoughts are billed against ``max_output_tokens``, and a budget under them
#: truncates the answer.
#:
#: So the FAIL was a fact about the probe, not about the id. That is ``D-00-047``
#: exactly -- "a probe measures the probe until proven otherwise" -- and the
#: number is recorded here rather than rounded up quietly, because the next
#: person to shrink it needs the measurement, not the value.
PROBE_MAX_OUTPUT_TOKENS: Final[int] = 256


class ProbeAnswer(BaseModel):
    """The smallest structured reply that still exercises the schema path.

    Free text would prove the endpoint answers; it would not prove that
    ``response_schema`` round-trips, which is the half of the transport that
    ``agents/runtime/model_router/wire_schema.py`` exists because of.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    echo: str


class ModelProbeService:
    """Invokes each configured id once and reports what came back."""

    __slots__ = ("_client_factory", "_clock", "_config_factory")

    def __init__(
        self,
        *,
        config_factory: Callable[[], GeminiRouterConfig],
        client_factory: Callable[[GeminiRouterConfig], Any],
        clock: Callable[[], datetime],
    ) -> None:
        self._config_factory = config_factory
        self._client_factory = client_factory
        self._clock = clock

    async def run(self, payload: Any) -> dict[str, Any]:
        """Section 8.33. Never raises; a failure is a recorded verdict."""
        probe_id = uuid.uuid4()
        started = self._clock()
        try:
            config = self._config_factory()
        except ModelConfigError as exc:
            return _body(
                probe_id,
                payload,
                started,
                self._clock(),
                [
                    {
                        "model_id": None,
                        "verdict": "CANNOT_RUN",
                        "detail": str(exc),
                    }
                ],
            )

        if config.api_key is None:
            return _body(
                probe_id,
                payload,
                started,
                self._clock(),
                [
                    {
                        "model_id": model_id,
                        "verdict": "CANNOT_RUN",
                        "detail": (
                            "GOOGLE_API_KEY is not set in this process, so no call was "
                            "attempted and none can be. This is not a failure of the id"
                        ),
                    }
                    for model_id in _targets(config, payload)
                ],
            )

        client = self._client_factory(config)
        results = [
            await asyncio.to_thread(_probe_one, client, model_id)
            for model_id in _targets(config, payload)
        ]
        return _body(probe_id, payload, started, self._clock(), results)


def _targets(config: GeminiRouterConfig, payload: Any) -> tuple[str, ...]:
    """Which ids to invoke. The configured tiers, or the one asked for.

    The requested id is checked against the configured set rather than passed
    through: an endpoint that invoked any string a caller supplied would be a
    way to spend this project's model budget on somebody else's prompt.
    """
    configured = (config.extraction_model_id, config.reasoning_model_id)
    requested = getattr(payload, "model_id", None)
    if requested and requested in configured:
        return (str(requested),)
    return configured


def _probe_one(client: Any, model_id: str) -> dict[str, Any]:
    """One invocation, and the verdict it earned."""
    request = ModelRequest(
        model_id=model_id,
        system_instruction=(
            "You are a availability probe. Reply with ok=true and echo the word PROVENANCE."
        ),
        user_text="Reply with ok true and echo PROVENANCE.",
        response_schema=ProbeAnswer,
        max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
        effort=None,
        thinking=False,
    )
    try:
        response = client.generate(request)
    except ModelRefusalError as exc:
        return {"model_id": model_id, "verdict": "FAIL", "detail": f"refused: {exc}"}
    except ModelInvocationError as exc:
        return {"model_id": model_id, "verdict": "FAIL", "detail": str(exc)}
    except ModelConfigError as exc:
        return {"model_id": model_id, "verdict": "CANNOT_RUN", "detail": str(exc)}
    text = (response.text or "").strip()
    if not text:
        return {
            "model_id": model_id,
            "verdict": "FAIL",
            "detail": "answered with no content; a 200 carrying nothing is not an answer",
        }
    # Decoded, not merely counted. "It returned some characters" is what
    # `ops/gemini-probe.txt`'s first run reported PASS for on three ids that
    # answered nothing useful (`D-00-046`), and the half of the transport this
    # build actually depends on is `response_schema` round-tripping -- which a
    # length check does not touch.
    try:
        answer = ProbeAnswer.model_validate_json(text)
    except ValueError as exc:
        return {
            "model_id": model_id,
            "verdict": "FAIL",
            "detail": (
                f"answered {len(text)} characters that do not satisfy ProbeAnswer: "
                f"{type(exc).__name__}"
            ),
        }
    return {
        "model_id": model_id,
        "verdict": "PASS",
        "detail": f"answered ok={answer.ok} echo={answer.echo!r}",
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }


def _body(
    probe_id: uuid.UUID,
    payload: Any,
    started: datetime,
    finished: datetime,
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = {verdict: 0 for verdict in ("PASS", "FAIL", "CANNOT_RUN")}
    for result in results:
        counts[str(result["verdict"])] += 1
    return {
        "probe_id": str(probe_id),
        "probe_type": getattr(payload, "probe_type", "MODEL_AVAILABILITY"),
        "status": "COMPLETED" if counts["PASS"] else "FAILED",
        "started_at": started,
        "completed_at": finished,
        "duration_ms": max(int((finished - started).total_seconds() * 1000), 0),
        "results": [dict(result) for result in results],
        "counts": counts,
    }
