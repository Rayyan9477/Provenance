"""Types for the Gemini model router: configuration, routes, records, outcomes.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Models and prompts* (the tier split and
  the fallback policy) and -> *Bedrock model id canon* (the router obligation:
  ids are configuration, and the id that served a call is recorded).
- ``docs/specs/14_PROMPTS.md`` section 7 (repair policy), section 8 (the route
  table), section 9 (per-model parameters).
- ``docs/specs/10_DATABASE_DDL.md`` section 17 -> ``agent_runs.model_calls``,
  whose element shape :meth:`ModelCallRecord.as_agent_runs_element` emits.

THE LESSON THIS MODULE IS SHAPED BY
-----------------------------------
On the previous provider, ``list-foundation-models`` returned model ids that
were **not invocable**. Every id frozen from documentation turned out wrong,
and the failure surfaced at invocation time, per node, in production. Three
consequences are structural here rather than advisory:

1. **The allow-set is a ``Literal`` and a ``frozenset``.** An id outside it
   fails :meth:`GeminiRouterConfig.from_env` — that is, at container start —
   rather than on the first artifact that happens to route through it.
2. **No call site names an id.** :meth:`GeminiRouterConfig.model_id_for` is the
   only mapping from tier to string, so swapping a model is one environment
   variable and never a code change.
3. **The id actually used is recorded per call**, in
   :class:`ModelCallRecord`. A fallback that quietly served a run on a
   different model is visible in ``agent_runs.model_calls``.

The August 2026 model floor
---------------------------
The hackathon requires "Gemini 3.5 or newer" and **no Pro model satisfies it**:
``gemini-3.1-pro-preview`` is version 3.1, below the floor. Both tiers are
therefore Flash-class. That is a capability constraint recorded here, not a
preference — see ``ALLOWED_MODEL_IDS``.

Probed
------
``ops/gemini-probe.txt`` exists and carries a PASS line for every id below, at
``PASS 11 | FAIL 0 | CANNOT RUN 0``. Each constant therefore carries a
``# PROBED`` marker naming that transcript rather than the ``# PROBE REQUIRED``
marker it carried while the ids were hypotheses.

The markers are not maintained by hand.
``test_the_probe_markers_agree_with_the_transcript`` reads the transcript,
takes each id's verdict through ``smoke.probe_verdict`` -- the same function
the smoke tool uses, so there is one definition of "probed" -- and requires the
source marker to match. This section previously said the transcript "does not
exist" and every id was "documented but unprobed", seven days after the probe
ran and was committed, because the marker and the evidence were two facts kept
in step by intention. A test that derives one from the other cannot drift in
either direction: removing a marker early fails, and leaving one behind fails
too.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final, Generic, Literal, TypeAlias, TypeVar, get_args

from pydantic import BaseModel, SecretStr

from agents.runtime.schemas.validation import ValidationFailure
from provenance_domain.enums import ModelTier, ProposalStatus

#: The pydantic model a node's output validates as. Carried through
#: :class:`OutputContract` and :class:`RouterSuccess` so a caller gets the type
#: it asked for rather than a bare ``BaseModel`` it has to re-narrow.
OutputT = TypeVar("OutputT", bound=BaseModel)

__all__ = [
    "ALLOWED_MODEL_IDS",
    "DEFAULT_EXTRACTION_MODEL_ID",
    "DEFAULT_REASONING_FALLBACK_MODEL_ID",
    "DEFAULT_REASONING_MODEL_ID",
    "ENV_API_KEY",
    "ENV_EXTRACTION_MODEL_ID",
    "ENV_REASONING_FALLBACK_MODEL_ID",
    "ENV_REASONING_MODEL_ID",
    "NON_REPAIRABLE_CODES",
    "PENDING_REVIEW_REASONS",
    "PROBE_EVIDENCE_PATH",
    "AllowedModelId",
    "Effort",
    "GeminiRouterConfig",
    "ModelCallOutcome",
    "ModelCallRecord",
    "ModelConfigError",
    "ModelInvocationError",
    "ModelRefusalError",
    "NodeSpec",
    "OutputContract",
    "OutputT",
    "PendingReview",
    "PendingReviewReason",
    "RouterResult",
    "RouterSuccess",
    "UnknownNodeError",
    "ValidationFailure",
]

# ---------------------------------------------------------------------------
# Model ids — documented, unprobed, and read from configuration
# ---------------------------------------------------------------------------

#: Where the probe transcript will live. Named in every configuration error so
#: the reader is pointed at the evidence rather than at the documentation.
PROBE_EVIDENCE_PATH: Final[str] = "ops/gemini-probe.txt"

# PROBED — ops/gemini-probe.txt, PB-G2. Tier R: semantic resolution,
# contradiction characterisation, attention assessment, advocacy drafting.
# Flash-class because no Pro model clears the "3.5 or newer" floor.
#
# 3.6 rather than 3.7, swapped 2026-08-31 on measurement rather than taste.
# `gemini-3.7-flash` answered 0 of 3 two-word prompts that day, returning
# `503 UNAVAILABLE  This model is currently experiencing high demand` and one
# `504 DEADLINE_EXCEEDED` after 119s; `gemini-3.6-flash` answered 3 of 3,
# averaging 26s. That is capacity on the provider's side, not a defect here --
# which is exactly the condition the declared fallback exists for, and this is
# the "one-line environment change under time pressure" it was declared to
# make possible. Both ids are ≥ 3.5 and both are PROBED in the same transcript,
# so the mandatory floor is unaffected by which one is Tier R.
#
# The two constants are therefore swapped rather than one edited: 3.7 stays
# declared, probed and reachable by setting GEMINI_REASONING_MODEL_ID, so if
# its capacity returns the change back is the same one line.
#
# PROBED — ops/gemini-probe.txt, PB-G2.
DEFAULT_REASONING_MODEL_ID: Final[str] = "gemini-3.6-flash"

# PROBED — ops/gemini-probe.txt, PB-G2. Tier E: extraction, classification,
# bulk structured output.
DEFAULT_EXTRACTION_MODEL_ID: Final[str] = "gemini-3.5-flash-lite"

# PROBED — ops/gemini-probe.txt, PB-G2. Now 3.7, which is the id whose
# capacity actually failed. Declared so a *capacity* failure
# on Tier R is a one-line environment change under time pressure. It is
# deliberately not an automatic downgrade: canon forbids one on Tier R, and
# ``test_the_declared_reasoning_fallback_id_is_never_selected_automatically``
# is what stops it becoming one by accident.
DEFAULT_REASONING_FALLBACK_MODEL_ID: Final[str] = "gemini-3.7-flash"

#: The closed set. A stale id is a startup failure, not a nightly surprise.
AllowedModelId = Literal[
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

ALLOWED_MODEL_IDS: Final[frozenset[str]] = frozenset(get_args(AllowedModelId))

ENV_API_KEY: Final[str] = "GOOGLE_API_KEY"
ENV_REASONING_MODEL_ID: Final[str] = "GEMINI_REASONING_MODEL_ID"
ENV_EXTRACTION_MODEL_ID: Final[str] = "GEMINI_EXTRACTION_MODEL_ID"
ENV_REASONING_FALLBACK_MODEL_ID: Final[str] = "GEMINI_REASONING_FALLBACK_MODEL_ID"

#: ``output_config.effort`` has no Gemini equivalent; ``ThinkingConfig`` does.
#: These are the four levels the SDK exposes, spelled as the router's own
#: vocabulary so the adapter owns the mapping.
Effort = Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"]


class ModelConfigError(ValueError):
    """A model id, or a credential, that the router refuses to start with."""


class UnknownNodeError(LookupError):
    """A node name absent from the route table.

    Routing is a static table keyed on node name (section 8). An unknown name
    is a programming error, and answering it with a default model would be the
    dynamic routing section 9.9 rejects.
    """


class ModelInvocationError(RuntimeError):
    """The call did not produce a response: transport, quota, or a 4xx/5xx.

    Distinct from :class:`ModelRefusalError` and from a schema failure. The
    three carry different budgets, and conflating any two of them is how a
    two-call policy becomes a four-call bill.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelRefusalError(RuntimeError):
    """The model declined. Never repaired, never retried, never downgraded.

    A refusal is a decision, not an outage. Re-asking a larger model is
    shopping for a different answer to the same question.
    """

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class GeminiRouterConfig:
    """The four environment values, validated once, at startup.

    Constructed from a :class:`~collections.abc.Mapping` rather than from
    ``os.environ`` directly: ``provenance_contracts.settings`` owns the rule
    that one module reads the environment, and this package is not it. The CLI
    in ``agents/runtime/tools/smoke.py`` is the single place that passes
    ``os.environ`` in, and when ``Settings`` grows ``GEMINI_*`` fields the
    mapping comes from there instead — with no change to any caller here.
    """

    reasoning_model_id: str = DEFAULT_REASONING_MODEL_ID
    extraction_model_id: str = DEFAULT_EXTRACTION_MODEL_ID
    reasoning_fallback_model_id: str = DEFAULT_REASONING_FALLBACK_MODEL_ID
    #: ``None`` until a key exists. Absence is refused at the point of use
    #: (:meth:`require_api_key`), not at construction, so the router stays
    #: constructible, printable and testable before the probe.
    api_key: SecretStr | None = None

    def __post_init__(self) -> None:
        for variable, value in (
            (ENV_REASONING_MODEL_ID, self.reasoning_model_id),
            (ENV_EXTRACTION_MODEL_ID, self.extraction_model_id),
            (ENV_REASONING_FALLBACK_MODEL_ID, self.reasoning_fallback_model_id),
        ):
            if value not in ALLOWED_MODEL_IDS:
                allowed = ", ".join(sorted(ALLOWED_MODEL_IDS))
                raise ModelConfigError(
                    f"{variable}={value!r} is not in the allow-set ({allowed}). "
                    "A model id read from documentation is a hypothesis until it is "
                    f"invoked: record the transcript at {PROBE_EVIDENCE_PATH} and add "
                    "the id to AllowedModelId in the same change. Note that no Pro "
                    "model clears the 'Gemini 3.5 or newer' floor."
                )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GeminiRouterConfig:
        """Read the four variables, defaulting to the documented ids."""
        raw_key = env.get(ENV_API_KEY, "").strip()
        return cls(
            reasoning_model_id=env.get(ENV_REASONING_MODEL_ID, DEFAULT_REASONING_MODEL_ID),
            extraction_model_id=env.get(ENV_EXTRACTION_MODEL_ID, DEFAULT_EXTRACTION_MODEL_ID),
            reasoning_fallback_model_id=env.get(
                ENV_REASONING_FALLBACK_MODEL_ID, DEFAULT_REASONING_FALLBACK_MODEL_ID
            ),
            api_key=SecretStr(raw_key) if raw_key else None,
        )

    def model_id_for(self, tier: ModelTier) -> str:
        """The only mapping from tier to model id anywhere in the package."""
        if tier is ModelTier.E:
            return self.extraction_model_id
        if tier is ModelTier.R:
            return self.reasoning_model_id
        raise ModelConfigError(
            f"tier {tier} is not routed by this router; embeddings are served by "
            "the retrieval package's TextEmbedder implementation."
        )

    def require_api_key(self) -> SecretStr:
        raise_if_missing = self.api_key
        if raise_if_missing is None:
            raise ModelConfigError(
                f"{ENV_API_KEY} is not set. The Gemini Developer API needs an AI "
                "Studio key; until one exists the router runs only against fakes, "
                f"and {PROBE_EVIDENCE_PATH} stays absent."
            )
        return raise_if_missing


# ---------------------------------------------------------------------------
# The route table's row type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """One row of the static route table (section 8).

    ``(graph_version, node, prompt_version)`` determines ``model_id``, which is
    what makes an August run and an October eval replay the same path. No field
    here is a model output.
    """

    name: str
    tier: ModelTier
    prompt_version: str
    schema_version: str
    max_output_tokens: int
    #: ``None`` on Tier E: a thinking level on a Flash-Lite extraction call buys
    #: nothing and the parameter is not part of that node's contract.
    effort: Effort | None
    thinking: bool
    tools_enabled: bool


# ---------------------------------------------------------------------------
# Validation failures — the repair envelope's payload
# ---------------------------------------------------------------------------
#
# `ValidationFailure` is defined ONCE, in `agents/runtime/schemas/validation.py`,
# and re-exported here so existing importers keep working.
# `03_AGENTS_LANGGRAPH_CONTRACTS.md` section 5.4 puts semantic validation in the
# node, not in the transport: the checks that produce these failures need the
# artifact's content blocks and the current State Proof, neither of which the
# router has or should have. Two definitions of one concept is the drift this
# repository spends most of its effort preventing, so the transport imports the
# contract's type rather than declaring its own.


#: Codes that skip the repair attempt entirely (section 2.2, section 7.2).
NON_REPAIRABLE_CODES: Final[frozenset[str]] = frozenset({"NONCE_LEAKED_IN_OUTPUT"})


@dataclass(frozen=True, slots=True)
class OutputContract(Generic[OutputT]):
    """Layers 1 and 2 of section 7.1, bound together.

    ``model`` is handed to the SDK as the response schema *and* used to decode
    the reply, so the API-level constraint and the terminal validation cannot
    drift apart. ``validate`` carries the checks a JSON Schema cannot express —
    ranges, cross-field dependencies, span-substring equality.
    """

    model: type[OutputT]
    validate: Callable[[OutputT], Sequence[ValidationFailure]]


# ---------------------------------------------------------------------------
# Per-call attribution
# ---------------------------------------------------------------------------

ModelCallOutcome = Literal["OK", "SCHEMA_FAILURE", "INVOCATION_FAILURE", "REFUSAL"]


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    """One element of ``agent_runs.model_calls``, plus router-local context.

    The nine keys ``specs/10_DATABASE_DDL.md`` prints are emitted by
    :meth:`as_agent_runs_element`; the remaining fields stay in process for
    telemetry and for the tests that prove which model served which call.
    """

    seq: int
    node: str
    model_id: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    repair_attempts: int
    duration_ms: int
    started_at: datetime
    tier: ModelTier
    outcome: ModelCallOutcome
    thought_tokens: int = 0
    cached_input_tokens: int = 0
    transport_retry: bool = False

    def as_agent_runs_element(self) -> dict[str, object]:
        """Exactly the documented nine keys, JSON-serialisable."""
        return {
            "seq": self.seq,
            "node": self.node,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "repair_attempts": self.repair_attempts,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
        }


# ---------------------------------------------------------------------------
# Outcomes — PENDING_REVIEW is a value, never an exception
# ---------------------------------------------------------------------------

PendingReviewReason = Literal[
    "SCHEMA_REPAIR_EXHAUSTED",
    "MODEL_REFUSAL",
    "MODEL_INVOCATION_FAILED",
    "NONCE_LEAKED_IN_OUTPUT",
    "MODEL_CALL_BUDGET_EXHAUSTED",
]

PENDING_REVIEW_REASONS: Final[frozenset[str]] = frozenset(get_args(PendingReviewReason))


@dataclass(frozen=True, slots=True)
class RouterSuccess(Generic[OutputT]):
    """A validated terminal result, with the attribution that produced it."""

    node: str
    value: OutputT
    model_id: str
    prompt_version: str
    repaired: bool
    calls: tuple[ModelCallRecord, ...]
    logical_attempts: int

    status: ClassVar[Literal["SUCCEEDED"]] = "SUCCEEDED"

    def as_agent_runs_model_calls(self) -> str:
        return json.dumps([record.as_agent_runs_element() for record in self.calls])


@dataclass(frozen=True, slots=True)
class PendingReview:
    """A persisted state with a reason code, not an exception that unwinds.

    ``T7.6``: "Make ``PENDING_REVIEW`` a persisted state with a reason code,
    not an exception that unwinds." An exception would let a caller decide to
    swallow it; a returned value has to be handled or it fails type checking.
    """

    node: str
    reason_code: PendingReviewReason
    failures: tuple[ValidationFailure, ...]
    calls: tuple[ModelCallRecord, ...]
    logical_attempts: int
    detail: str = ""

    status: ClassVar[Literal["PENDING_REVIEW"]] = "PENDING_REVIEW"
    #: How the Memory Kernel records the same fact. One vocabulary, two layers.
    proposal_status: ClassVar[ProposalStatus] = ProposalStatus.PENDING_HUMAN_REVIEW

    def as_agent_runs_model_calls(self) -> str:
        return json.dumps([record.as_agent_runs_element() for record in self.calls])

    @property
    def failure_codes(self) -> tuple[str, ...]:
        """Codes only. Safe to log; ``ValidationFailure.detail`` is not."""
        return tuple(failure.code for failure in self.failures)


#: A node either produced a validated object or produced a persisted
#: pending-review state. There is no third outcome and no exception path.
#:
#: ``TypeAlias`` rather than PEP 695's ``type`` keyword, and the ``noqa`` is
#: deliberate: ruff 0.6.9 prefers ``type`` (UP040) while the pinned
#: ``mypy>=1.11,<2`` rejects it outright ("PEP 695 type aliases are not yet
#: supported"). Both pins are in ``pyproject.toml`` and neither is ours to move,
#: so the older spelling is the only one that satisfies both gates. Delete the
#: ``noqa`` in the same change that raises the mypy pin.
RouterResult: TypeAlias = RouterSuccess[OutputT] | PendingReview  # noqa: UP040
