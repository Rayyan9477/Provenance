"""Request models for the `/internal/v1` surface.

Authority: ``specs/15_API_SPEC.md`` sections 9.1-9.13, and section 3.6 for the
one exception noted below.

**No model here declares `tenant_id`, and only one declares `user_id`.**
``tests/auth/test_capability_binding.py`` asserts that against this file's AST
with a single-entry allowlist, so a second one cannot appear quietly.
:class:`MemoryProposalRequest.user_id` is that exception and section 3.6
defines exactly what it is: *an assertion by the caller about what it believes
it is doing*, compared against the server-resolved capability and then
discarded. It never selects a row, scopes a query, or widens authority.

Two more shapes are deliberate.

**`EvidenceCandidate` has no `source_authority`.** Section 9.4 step 3:
"a caller-supplied authority is not accepted; the field does not exist in the
request schema". With ``extra="forbid"`` that sentence is a ``422``.

**`AgentRunCompleteRequest.tool_calls` is `tool_calls`, and each entry is a
closed model.** The column is ``agent_runs.tool_calls``; ``mcp_tool_calls`` is
the *HTTP field name used by section 8.29's read side*, not a request field and
not a column. Each entry forbids extras so returned rows or SQL text cannot be
smuggled into the trace through a key nobody thought to reject.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from services.control_plane.app.api.schemas.common import ApiRequest, Ratio

__all__ = [
    "EVENT_ENVELOPE_FIELDS",
    "KNOWN_CONSUMERS",
    "ActionExecuteRequest",
    "AdvocacyActionIntentRequest",
    "AgentRunCompleteRequest",
    "DraftClaim",
    "DraftPayload",
    "EventDeliveryRequest",
    "EvidenceCandidate",
    "IngestArtifactRequest",
    "MemoryProposalRequest",
    "ModelCallRecord",
    "OutboxSweepRequest",
    "ProposalModel",
    "RegisterEvidenceRequest",
    "RetrievalRequest",
    "SesVerdicts",
    "ToolCallRecord",
    "TriggerEvaluateRequest",
]

#: Section 10.4. The closed set of consumer names ``/internal/v1/events/deliveries``
#: will dedupe for. An unknown name is a ``422``, not a silently accepted row in
#: ``processed_events`` that nothing will ever read.
KNOWN_CONSUMERS: Final[tuple[str, ...]] = (
    "advocate_dispatch",
    "notification_dispatch",
    "action_execute",
    "telemetry",
)

#: Section 10.1's required envelope fields. ``causation_id`` and
#: ``correlation_id`` are optional and are not listed.
EVENT_ENVELOPE_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "aggregate_version",
    "tenant_id",
    "user_id",
    "trace_id",
    "occurred_at",
    "payload",
)

SchemaVersion = Literal["1.0"]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, StringConstraints(max_length=1000)]


class SesVerdicts(ApiRequest):
    """Section 9.1. A failed SPF is evidence; a failed virus scan is a refusal."""

    spf: Literal["PASS", "FAIL", "GRAY", "PROCESSING_FAILED"]
    dkim: Literal["PASS", "FAIL", "GRAY", "PROCESSING_FAILED"]
    dmarc: Literal["PASS", "FAIL", "GRAY", "PROCESSING_FAILED"]
    spam: Literal["PASS", "FAIL", "GRAY", "PROCESSING_FAILED"]
    virus: Literal["PASS", "FAIL", "GRAY", "PROCESSING_FAILED"]

    def blocking_failure(self) -> str | None:
        """The verdict that must refuse the message, or ``None``."""
        for name in ("virus", "spam"):
            if getattr(self, name) == "FAIL":
                return name
        return None


class IngestArtifactRequest(ApiRequest):
    """Section 9.1. The SES worker presents an alias, never a user."""

    alias_hash: Annotated[str, StringConstraints(min_length=8, max_length=200)]
    s3_bucket: Annotated[str, StringConstraints(min_length=3, max_length=255)]
    s3_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    source_message_id: Annotated[str, StringConstraints(max_length=998)] | None = None
    sender: Annotated[str, StringConstraints(max_length=320)] | None = None
    recipient: Annotated[str, StringConstraints(max_length=320)] | None = None
    subject: Annotated[str, StringConstraints(max_length=998)] | None = None
    received_at: datetime
    size_bytes: int = Field(ge=0)
    content_sha256: Sha256Hex
    ses_verdicts: SesVerdicts


class EvidenceCandidate(ApiRequest):
    """Section 9.4. ``source_authority`` is assigned server-side and is absent."""

    client_ref: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    evidence_type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    block_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    exact_text: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    normalized_text: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    source_locator: dict[str, Any] | None = None
    actor_ref: Annotated[str, StringConstraints(max_length=320)] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime
    extraction_confidence: Ratio


class RegisterEvidenceRequest(ApiRequest):
    """Section 9.4."""

    schema_version: SchemaVersion = "1.0"
    candidates: list[EvidenceCandidate] = Field(default_factory=list, max_length=200)


class RetrievalRequest(ApiRequest):
    """Section 9.5. Read-only, so no idempotency key."""

    schema_version: SchemaVersion = "1.0"
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    identity_hints: dict[str, Any] | None = None
    temporal_window: dict[str, Any] | None = None
    top_k_vector: int = Field(default=20, ge=1, le=100)
    max_cases: int = Field(default=3, ge=1, le=20)
    max_evidence_snippets: int = Field(default=10, ge=1, le=50)


class ProposalModel(ApiRequest):
    """Section 9.7's ``model`` block, plus the tier that says which route entry.

    Section 9.8 deliberately carries **no** model block and this one does, and
    the asymmetry is in the two specs rather than in this file. It is safe here
    for one reason only: every field except ``prompt_version`` is *checked*
    against the ``agent_runs`` row before it reaches a proposal.
    ``services/control_plane/app/proposals/submission.py::resolve_attribution``
    is where that happens, and its docstring says which field is verified and
    which is asserted.

    ``tier`` is not in section 9.7's printed example and is required here.
    Without it the tier has to be recovered by searching ``model_route`` for
    the claimed id, which is ambiguous the moment both tiers point at one model
    -- the documented response to a Tier R capacity failure. A search that
    silently picked one would record a tier the call may not have used.
    ``EMBEDDING`` is absent from the literal because an embedding model
    produces no claim and therefore attributes none.
    """

    provider: Literal["bedrock", "gemini"]
    model_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    tier: Literal["E", "R"]
    prompt_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]


class MemoryProposalRequest(ApiRequest):
    """Section 9.7, the only path into the Memory Kernel.

    ``user_id`` is section 3.6's tripwire. It is compared against the
    server-resolved capability and then discarded; a mismatch is a
    ``403 CAPABILITY_SCOPE_MISMATCH`` and a high-severity alarm, because a
    correct system can never produce one.

    Three fields carry section 9.7's body into shapes
    ``provenance_contracts.proposal.MemoryProposal`` can accept, and each was
    absent while this endpoint was unbound:

    * ``model`` -- see :class:`ProposalModel`.
    * ``unresolved_questions`` is a list of **sentences**. The contract types it
      ``tuple[Text, ...]``; a list of objects advertised a shape that could
      never validate.
    * ``requested_case_transition`` and ``requested_transition_reason_code``
      travel together because ``MemoryProposal`` refuses one without the other,
      and the hero reveal -- ``RESOLVED -> REOPENED`` at revision 13 -- is a
      requested transition. Without them an agent could not ask for one.
    """

    schema_version: SchemaVersion = "1.0"
    agent_run_id: uuid.UUID
    proposal_id: uuid.UUID
    trace_id: uuid.UUID
    user_id: uuid.UUID
    proposal_type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    source_artifact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=64)
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    identity: dict[str, Any] = Field(default_factory=dict)
    claims: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    commitments: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    belief_mutations: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    conflict_hints: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    trigger_mutations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    unresolved_questions: list[Annotated[str, StringConstraints(min_length=1, max_length=2000)]] = (
        Field(default_factory=list, max_length=100)
    )
    requested_case_transition: Annotated[str, StringConstraints(max_length=64)] | None = None
    requested_transition_reason_code: Annotated[str, StringConstraints(max_length=64)] | None = None
    blocks_state_change: bool = False
    model: ProposalModel

    def declared_case_id(self) -> uuid.UUID | None:
        """The case this proposal aims at, if it named one.

        Returned so the handler can run section 3.6's ``case_id`` predicate.
        A malformed value is treated as "no case named" rather than raising:
        the capability check that follows is what decides, and it fails closed.
        """
        raw = self.identity.get("case_id") if isinstance(self.identity, dict) else None
        if raw is None:
            return None
        try:
            return uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            return None


class DraftClaim(ApiRequest):
    sentence_or_span: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    support_ids: list[uuid.UUID] = Field(default_factory=list, max_length=32)


class DraftPayload(ApiRequest):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    claims: list[DraftClaim] = Field(default_factory=list, max_length=100)
    requested_outcome: Annotated[str, StringConstraints(max_length=200)] | None = None
    tone: Annotated[str, StringConstraints(max_length=64)] | None = None
    unresolved_risks: list[ShortText] = Field(default_factory=list, max_length=20)


class AdvocacyActionIntentRequest(ApiRequest):
    """Section 9.8. Creating an intent is not an action."""

    schema_version: SchemaVersion = "1.0"
    agent_run_id: uuid.UUID
    case_id: uuid.UUID
    basis_case_revision: int = Field(ge=0)
    action_type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    recipient: Annotated[str, StringConstraints(min_length=3, max_length=320)]
    draft: DraftPayload
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    supporting_belief_versions: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class ToolCallRecord(ApiRequest):
    """Section 9.9's allowlist, closed.

    Every key an entry may carry is named here. ``returned_rows``,
    ``sql``, ``result`` and anything else are rejected, which is what stops a
    run from smuggling retrieved rows into the Memory Trace.
    """

    sequence: int = Field(ge=1)
    mcp_server: Annotated[str, StringConstraints(max_length=64)]
    tool_name: Annotated[str, StringConstraints(max_length=128)]
    view_name: Annotated[str, StringConstraints(max_length=128)] | None = None
    sql_role: Annotated[str, StringConstraints(max_length=64)] | None = None
    access_mode: Literal["READ_ONLY"] | None = None
    filter_summary: Annotated[str, StringConstraints(max_length=500)] | None = None
    rows_returned: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    denied: bool = False


class ModelCallRecord(ApiRequest):
    node: Annotated[str, StringConstraints(max_length=128)]
    tier: Literal["E", "R"]
    model_id: Annotated[str, StringConstraints(max_length=200)]
    prompt_version: Annotated[str, StringConstraints(max_length=64)] | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    repair_attempts: int | None = Field(default=None, ge=0)


class AgentRunCompleteRequest(ApiRequest):
    """Section 9.9. Closes the run and burns the capability."""

    status: Literal["SUCCEEDED", "FAILED", "ABANDONED"]
    error_code: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, max_length=50)
    model_calls: list[ModelCallRecord] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> AgentRunCompleteRequest:
        """``ck_agent_runs_error``, moved to the boundary that can name a field.

        The column constraint is ``status NOT IN ('FAILED','ABANDONED') OR
        error_code IS NOT NULL``. Reaching it means a ``CheckViolation`` and a
        ``500`` naming nothing; refusing here is a ``422`` naming
        ``error_code``. It is the same rule in both places on purpose -- the
        database keeps it true for every writer, and this keeps the message
        useful for the one writer that is an HTTP request.
        """
        if self.status in ("FAILED", "ABANDONED") and not self.error_code:
            raise ValueError(
                f"status {self.status} requires error_code; a run that failed "
                "without saying why is a trace nobody can act on"
            )
        return self


class TriggerEvaluateRequest(ApiRequest):
    """Section 9.10. The wakeup is never proof that the condition still holds."""

    scheduled_for: datetime
    schedule_name: Annotated[str, StringConstraints(max_length=128)] | None = None
    evaluation_version: int = Field(ge=1)


class ActionExecuteRequest(ApiRequest):
    """Section 9.11, the only endpoint with an external side effect."""

    expected_draft_sha256: Sha256Hex
    expected_case_revision: int = Field(ge=0)


class OutboxSweepRequest(ApiRequest):
    """Section 9.12. Service-level; it returns counts, never payloads."""

    batch_size: int = Field(default=100, ge=1, le=500)
    max_batches: int = Field(default=1, ge=1, le=20)
    worker_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class EventDeliveryRequest(ApiRequest):
    """Section 9.13. ``event_id`` is the idempotency key, so there is no header.

    ``event`` is carried as a mapping rather than a typed envelope on purpose:
    section 10.2 rule 3 makes additive payload fields legal within a version,
    and a strict model here would turn a legal producer-side addition into a
    ``422`` at the consumer. The *envelope* is still checked, field by field,
    because a missing ``event_id`` would silently defeat the dedupe ledger.
    """

    consumer_name: Literal[
        "advocate_dispatch",
        "notification_dispatch",
        "action_execute",
        "telemetry",
    ]
    event: dict[str, Any]

    @field_validator("event")
    @classmethod
    def _envelope_is_complete(cls, value: dict[str, Any]) -> dict[str, Any]:
        missing = [name for name in EVENT_ENVELOPE_FIELDS if name not in value]
        if missing:
            raise ValueError(f"the DomainEvent envelope is missing: {', '.join(missing)}")
        return value
