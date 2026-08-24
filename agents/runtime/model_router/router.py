"""Deterministic routing, and the three budgets, enforced in one place.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 7.2 (one repair attempt, and what a
  repair request looks like), section 8 (the route table), section 9.4
  (effort per node), section 9.7 (refusals), section 9.9 (why routing is a
  static table and not a model call).
- ``docs/CANONICAL_DECISIONS.md`` -> *Models and prompts*: "Tier E fallback —
  one schema-repair attempt; on invocation failure, one fallback at low effort.
  Exhaustion becomes pending review. Tier R fallback — no downgrade to a weaker
  model. Failure persists a pending-human-review result."
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.6: "Count calls per node but enforce
  the budget **in the router, not in each node**. A per-node budget is a budget
  that three places can disagree about."

Three budgets, and why they are three
-------------------------------------
============================ ======= ================================
Failure                      Budget  Response
============================ ======= ================================
schema / semantic invalid    1       one repair on the same model
invocation failed (Tier E)   1       one call on the Tier R model, low effort
invocation failed (Tier R)   0       ``PENDING_REVIEW`` immediately
transport (throttle, 5xx)    N       bounded backoff; spends no other budget
refusal                      0       ``PENDING_REVIEW`` immediately
============================ ======= ================================

A router with **one** counter for the first two spends four calls on the
"invocation failed, then the fallback's output was malformed" path. A router
with two counters and no cap spends three. ``T7.6``'s acceptance says two, so
:data:`MAX_MODEL_CALLS_PER_NODE` is a hard cap over the logical attempts and
dominates both. Transport retries are counted separately and recorded
separately, because a throttle costs money and latency but is not evidence
about the model's output.

Why ``PENDING_REVIEW`` is returned rather than raised
------------------------------------------------------
An exception can be swallowed by a caller in a hurry. A returned
:class:`~agents.runtime.model_router.models.PendingReview` has to be handled,
carries a closed-set reason code, and carries the call records the
``agent_runs`` row needs — so the failure is *persisted* rather than merely
logged. Failing is always cheaper than committing a guess.

What this module never does
---------------------------
No canonical write, no database handle, no network call of its own, and no log
line containing artifact text, model output, or a credential. It logs node,
tier, model id, prompt version, token counts, durations and reason **codes**.
``ValidationFailure.detail`` is written for the model and is deliberately never
logged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import ValidationError

from agents.runtime.model_router.gemini import ModelClient, ModelRequest, ModelResponse
from agents.runtime.model_router.models import (
    Effort,
    GeminiRouterConfig,
    ModelCallOutcome,
    ModelCallRecord,
    ModelInvocationError,
    ModelRefusalError,
    NodeSpec,
    OutputContract,
    OutputT,
    PendingReview,
    PendingReviewReason,
    RouterResult,
    RouterSuccess,
    UnknownNodeError,
    ValidationFailure,
)
from provenance_domain.enums import ModelTier

__all__ = [
    "FALLBACK_EFFORT",
    "MAX_MODEL_CALLS_PER_NODE",
    "MAX_SCHEMA_REPAIR_ATTEMPTS",
    "MAX_TIER_E_FALLBACK_ATTEMPTS",
    "REPAIR_INSTRUCTION",
    "REPAIR_PROMPT_VERSION",
    "ROUTES",
    "ModelRouter",
    "route",
]

LOGGER: Final[logging.Logger] = logging.getLogger("provenance.agents.model_router")

# ---------------------------------------------------------------------------
# The static route table (section 8)
# ---------------------------------------------------------------------------

ROUTES: Final[dict[str, NodeSpec]] = {
    "extract_structured_evidence": NodeSpec(
        name="extract_structured_evidence",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.1.0",
        schema_version="extraction/1.0.0",
        max_output_tokens=8192,
        effort=None,
        thinking=False,
        tools_enabled=False,
    ),
    "strong_resolution": NodeSpec(
        name="strong_resolution",
        tier=ModelTier.R,
        prompt_version="pv-resolve-1.1.0",
        schema_version="resolution/1.0.0",
        max_output_tokens=16000,
        effort="HIGH",
        thinking=True,
        tools_enabled=True,
    ),
    "classify_attention_need": NodeSpec(
        name="classify_attention_need",
        tier=ModelTier.R,
        prompt_version="pv-attention-1.1.0",
        schema_version="attention/1.0.0",
        max_output_tokens=8192,
        effort="MEDIUM",
        thinking=True,
        tools_enabled=False,
    ),
    "draft_action": NodeSpec(
        name="draft_action",
        tier=ModelTier.R,
        prompt_version="pv-draft-1.0.0",
        schema_version="draft/1.0.0",
        max_output_tokens=16000,
        effort="HIGH",
        thinking=True,
        tools_enabled=False,
    ),
}


def route(node_name: str) -> NodeSpec:
    """The routing decision, in full. It reads a table and nothing else.

    Deliberately not a model call and not a function of artifact text: routing
    that reads untrusted prose is an injection target, and routing that is a
    model output makes every eval threshold an average over a distribution of
    configurations (section 9.9).
    """
    try:
        return ROUTES[node_name]
    except KeyError:
        known = ", ".join(sorted(ROUTES))
        raise UnknownNodeError(f"{node_name!r} is not a model node; known nodes: {known}") from None


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

#: One repair, per node invocation. There is no repair of a repair.
MAX_SCHEMA_REPAIR_ATTEMPTS: Final[int] = 1

#: Tier E only. Tier R never downgrades.
MAX_TIER_E_FALLBACK_ATTEMPTS: Final[int] = 1

#: The hard cap over logical attempts. Dominates both budgets above, which is
#: what makes ``T7.6``'s "``model_calls=2`` ... and **never** 3" true on every
#: path rather than only on the schema path.
MAX_MODEL_CALLS_PER_NODE: Final[int] = 2

#: Off until a live endpoint exists to calibrate backoff against. Section 7.2
#: allows two; the value is a constructor argument so turning it on is a
#: deployment decision with a test behind it, not a code change.
DEFAULT_MAX_TRANSPORT_RETRIES: Final[int] = 0

#: The Tier E invocation-failure fallback runs the *reasoning* model shallow.
#: Deep thinking on a rescue call buys latency, not accuracy, on a path that
#: already failed once.
FALLBACK_EFFORT: Final[Effort] = "LOW"

REPAIR_PROMPT_VERSION: Final[str] = "pv-repair-1.0.0"

#: Section 7.2, transcribed. ``{failures_json}`` is substituted by literal
#: replacement rather than ``str.format`` so that a future prompt revision
#: containing a JSON example does not turn into a ``KeyError`` at run time.
REPAIR_INSTRUCTION: Final[str] = """\
# REPAIR INSTRUCTION
# prompt_version: pv-repair-1.0.0

Your previous response did not satisfy the required contract. The failures are
listed below, each naming the JSON path and what is wrong with the value there.

Return a corrected object conforming to the same schema.

Rules for this correction:
  - Fix only what is listed. Do not re-analyse the artifact, do not add new
    candidates, do not remove candidates that were not flagged, and do not
    change values that were not flagged.
  - If a flagged value cannot be corrected because the underlying information is
    not available to you, replace it with the schema's null-equivalent and add a
    corresponding entry to the object's uncertainty, unresolved-question, or
    unresolved-risk array explaining the gap.
  - Do not add commentary about the correction. Return only the object.

FAILURES
{failures_json}
"""

#: Non-repairable failure codes, mapped to the pending reason they produce.
_TERMINAL_FAILURE_REASONS: Final[dict[str, PendingReviewReason]] = {
    "NONCE_LEAKED_IN_OUTPUT": "NONCE_LEAKED_IN_OUTPUT",
}

_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.5, 2.0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Attempt:
    response: ModelResponse
    started_at: datetime
    duration_ms: int


class ModelRouter:
    """The one place a model id is chosen and a call budget is spent."""

    def __init__(
        self,
        *,
        config: GeminiRouterConfig,
        client: ModelClient,
        clock: Callable[[], datetime] = _utc_now,
        max_transport_retries: int = DEFAULT_MAX_TRANSPORT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._client = client
        self._clock = clock
        self._max_transport_retries = max_transport_retries
        self._sleep = sleep

    @property
    def config(self) -> GeminiRouterConfig:
        return self._config

    # -- the public surface ------------------------------------------------

    def invoke(
        self,
        node_name: str,
        *,
        system: str,
        user_text: str,
        contract: OutputContract[OutputT],
    ) -> RouterResult[OutputT]:
        """Route, call, validate, and spend at most two logical attempts.

        There is no budget parameter. A node cannot ask for a third call, which
        is the whole point of putting the budget here.
        """
        spec = route(node_name)
        records: list[ModelCallRecord] = []
        logical_attempts = 0
        repairs_used = 0
        fallbacks_used = 0

        tier = spec.tier
        model_id = self._config.model_id_for(tier)
        effort = spec.effort
        thinking = spec.thinking
        prompt_version = spec.prompt_version
        prior_assistant: str | None = None
        extra_user: str | None = None
        failures: tuple[ValidationFailure, ...] = ()

        while True:
            if logical_attempts >= MAX_MODEL_CALLS_PER_NODE:
                return self._pending(
                    spec, "MODEL_CALL_BUDGET_EXHAUSTED", failures, records, logical_attempts
                )
            logical_attempts += 1

            request = ModelRequest(
                model_id=model_id,
                system_instruction=system,
                user_text=user_text,
                response_schema=contract.model,
                max_output_tokens=spec.max_output_tokens,
                effort=effort,
                thinking=thinking,
                prior_assistant=prior_assistant,
                extra_user=extra_user,
            )

            try:
                attempt = self._attempt(
                    request,
                    spec=spec,
                    tier=tier,
                    prompt_version=prompt_version,
                    repairs_used=repairs_used,
                    records=records,
                )
            except ModelRefusalError as exc:
                # A refusal is a decision, not an outage: no repair, no retry,
                # no downgrade, on either tier.
                return self._pending(
                    spec,
                    "MODEL_REFUSAL",
                    (),
                    records,
                    logical_attempts,
                    detail=exc.category or "",
                )
            except ModelInvocationError:
                if (
                    tier is ModelTier.E
                    and fallbacks_used < MAX_TIER_E_FALLBACK_ATTEMPTS
                    and logical_attempts < MAX_MODEL_CALLS_PER_NODE
                ):
                    fallbacks_used += 1
                    tier = ModelTier.R
                    model_id = self._config.model_id_for(ModelTier.R)
                    effort = FALLBACK_EFFORT
                    thinking = True
                    continue
                return self._pending(spec, "MODEL_INVOCATION_FAILED", (), records, logical_attempts)

            failures, value = self._validate(attempt.response, contract)
            records.append(
                self._record(
                    spec=spec,
                    tier=tier,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    attempt=attempt,
                    repairs_used=repairs_used,
                    outcome="OK" if not failures else "SCHEMA_FAILURE",
                    seq=len(records) + 1,
                )
            )

            if not failures and value is not None:
                LOGGER.info(
                    "node=%s tier=%s model=%s prompt=%s calls=%d repaired=%s status=SUCCEEDED",
                    spec.name,
                    tier.value,
                    model_id,
                    prompt_version,
                    len(records),
                    repairs_used > 0,
                )
                return RouterSuccess(
                    node=spec.name,
                    value=value,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    repaired=repairs_used > 0,
                    calls=tuple(records),
                    logical_attempts=logical_attempts,
                )

            terminal = self._terminal_reason(failures)
            if terminal is not None:
                return self._pending(spec, terminal, failures, records, logical_attempts)

            if (
                repairs_used < MAX_SCHEMA_REPAIR_ATTEMPTS
                and logical_attempts < MAX_MODEL_CALLS_PER_NODE
            ):
                repairs_used += 1
                prior_assistant = attempt.response.text
                extra_user = render_repair_instruction(failures)
                prompt_version = f"{spec.prompt_version}+{REPAIR_PROMPT_VERSION}"
                continue

            return self._pending(
                spec, "SCHEMA_REPAIR_EXHAUSTED", failures, records, logical_attempts
            )

    # -- one logical attempt, plus its own transport-retry budget ----------

    def _attempt(
        self,
        request: ModelRequest,
        *,
        spec: NodeSpec,
        tier: ModelTier,
        prompt_version: str,
        repairs_used: int,
        records: list[ModelCallRecord],
    ) -> _Attempt:
        """Invoke once, retrying only transport failures.

        A transport retry appends its own record — the call really happened and
        really cost something — but does not advance the logical attempt count,
        so a throttle never eats the repair the node is entitled to.
        """
        transport_attempt = 0
        while True:
            started_at = self._clock()
            try:
                response = self._client.generate(request)
            except ModelRefusalError:
                records.append(
                    self._failure_record(
                        spec=spec,
                        tier=tier,
                        model_id=request.model_id,
                        prompt_version=prompt_version,
                        started_at=started_at,
                        repairs_used=repairs_used,
                        outcome="REFUSAL",
                        seq=len(records) + 1,
                    )
                )
                LOGGER.warning(
                    "node=%s tier=%s model=%s outcome=REFUSAL",
                    spec.name,
                    tier.value,
                    request.model_id,
                )
                raise
            except ModelInvocationError as exc:
                will_retry = exc.retryable and transport_attempt < self._max_transport_retries
                records.append(
                    self._failure_record(
                        spec=spec,
                        tier=tier,
                        model_id=request.model_id,
                        prompt_version=prompt_version,
                        started_at=started_at,
                        repairs_used=repairs_used,
                        outcome="INVOCATION_FAILURE",
                        seq=len(records) + 1,
                        transport_retry=will_retry,
                    )
                )
                LOGGER.warning(
                    "node=%s tier=%s model=%s outcome=INVOCATION_FAILURE retrying=%s",
                    spec.name,
                    tier.value,
                    request.model_id,
                    will_retry,
                )
                if not will_retry:
                    raise
                self._sleep(_BACKOFF_SECONDS[min(transport_attempt, len(_BACKOFF_SECONDS) - 1)])
                transport_attempt += 1
                continue

            finished_at = self._clock()
            return _Attempt(
                response=response,
                started_at=started_at,
                duration_ms=(finished_at - started_at) // timedelta(milliseconds=1),
            )

    # -- validation --------------------------------------------------------

    def _validate(
        self, response: ModelResponse, contract: OutputContract[OutputT]
    ) -> tuple[tuple[ValidationFailure, ...], OutputT | None]:
        """Layers 1 and 2 of section 7.1. Layer 3 is the Kernel's, not ours.

        A truncated body is a **schema** failure, not an invocation failure:
        section 9.3 records that thinking tokens and response text draw on one
        ``max_tokens``, so a long think produces a half-written object rather
        than an error.
        """
        if response.truncated:
            return (
                (
                    ValidationFailure(
                        path="$",
                        code="OUTPUT_TRUNCATED",
                        detail=(
                            "the response stopped at max_output_tokens before the object "
                            "closed; thinking tokens and response text share that budget"
                        ),
                    ),
                ),
                None,
            )
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            return (
                (
                    ValidationFailure(
                        path="$",
                        code="OUTPUT_NOT_JSON",
                        detail=f"the response is not JSON: {exc.msg} at position {exc.pos}",
                    ),
                ),
                None,
            )
        try:
            value = contract.model.model_validate(payload)
        except ValidationError as exc:
            return (tuple(_schema_failures(exc)), None)
        return (tuple(contract.validate(value)), value)

    @staticmethod
    def _terminal_reason(
        failures: tuple[ValidationFailure, ...],
    ) -> PendingReviewReason | None:
        for failure in failures:
            reason = _TERMINAL_FAILURE_REASONS.get(failure.code)
            if reason is not None:
                return reason
        return None

    # -- records and outcomes ---------------------------------------------

    @staticmethod
    def _record(
        *,
        spec: NodeSpec,
        tier: ModelTier,
        model_id: str,
        prompt_version: str,
        attempt: _Attempt,
        repairs_used: int,
        outcome: ModelCallOutcome,
        seq: int,
    ) -> ModelCallRecord:
        return ModelCallRecord(
            seq=seq,
            node=spec.name,
            model_id=model_id,
            prompt_version=prompt_version,
            input_tokens=attempt.response.input_tokens,
            output_tokens=attempt.response.output_tokens,
            repair_attempts=repairs_used,
            duration_ms=attempt.duration_ms,
            started_at=attempt.started_at,
            tier=tier,
            outcome=outcome,
            thought_tokens=attempt.response.thought_tokens,
            cached_input_tokens=attempt.response.cached_input_tokens,
        )

    def _failure_record(
        self,
        *,
        spec: NodeSpec,
        tier: ModelTier,
        model_id: str,
        prompt_version: str,
        started_at: datetime,
        repairs_used: int,
        outcome: ModelCallOutcome,
        seq: int,
        transport_retry: bool = False,
    ) -> ModelCallRecord:
        """A call that produced no body still produced a bill and a latency."""
        finished_at = self._clock()
        return ModelCallRecord(
            seq=seq,
            node=spec.name,
            model_id=model_id,
            prompt_version=prompt_version,
            input_tokens=0,
            output_tokens=0,
            repair_attempts=repairs_used,
            duration_ms=(finished_at - started_at) // timedelta(milliseconds=1),
            started_at=started_at,
            tier=tier,
            outcome=outcome,
            transport_retry=transport_retry,
        )

    @staticmethod
    def _pending(
        spec: NodeSpec,
        reason_code: PendingReviewReason,
        failures: tuple[ValidationFailure, ...],
        records: list[ModelCallRecord],
        logical_attempts: int,
        detail: str = "",
    ) -> PendingReview:
        LOGGER.warning(
            "node=%s calls=%d attempts=%d status=PENDING_REVIEW reason=%s codes=%s",
            spec.name,
            len(records),
            logical_attempts,
            reason_code,
            ",".join(failure.code for failure in failures) or "-",
        )
        return PendingReview(
            node=spec.name,
            reason_code=reason_code,
            failures=failures,
            calls=tuple(records),
            logical_attempts=logical_attempts,
            detail=detail,
        )


def render_repair_instruction(failures: tuple[ValidationFailure, ...]) -> str:
    """Section 7.2's envelope, carrying the validator's own failure list."""
    payload = json.dumps([failure.as_json_obj() for failure in failures], indent=2)
    return REPAIR_INSTRUCTION.replace("{failures_json}", payload)


def _schema_failures(error: ValidationError) -> list[ValidationFailure]:
    """Pydantic errors, with ``input`` dropped.

    ``ValidationError.errors()`` attaches the raw input to every entry. That
    input is model output derived from artifact text, and it would otherwise
    ride into any handler that renders the exception. Only ``loc``, ``type``
    and ``msg`` survive — the same posture ``Settings`` takes for credentials.
    """
    failures: list[ValidationFailure] = []
    for entry in error.errors(include_url=False):
        location = ".".join(str(part) for part in entry["loc"]) or "$"
        failures.append(
            ValidationFailure(
                path=location,
                code="SCHEMA_VALIDATION_FAILED",
                detail=f"{entry['msg']} (pydantic: {entry['type']})",
            )
        )
    return failures
