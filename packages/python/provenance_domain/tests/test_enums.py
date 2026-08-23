"""T1.1 — the closed vocabularies of `provenance_domain.enums`.

Every membership set in this file is **hand-typed** from the specification:

* `docs/specs/11_CONTRACTS.md` §3 — the authoritative enum list.
* `docs/CANONICAL_DECISIONS.md` — *Names and counts*, *Evidence and retrieval*,
  *Hero commit canon*.
* `docs/specs/12_KERNEL_ALGORITHMS.md` §9.3 — the kernel reason-code catalogue.
* `docs/specs/16_TRIGGER_DSL.md` §9.10 — the trigger outcome reason codes.
* `docs/specs/14_PROMPTS.md` §4 (`pv-attention`) — the Advocate attention classes.

Nothing here may import a membership set, a frozenset, or a mapping from
`provenance_domain.enums`. Importing the production vocabulary to test the
production vocabulary is tautological and `quality/20_TDD_STRATEGY.md` §5.1
forbids it. Only the module object itself is imported, so the assertions below
compare the shipped code against a second, independent transcription.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Mapping

import pytest

from provenance_domain import enums

# ---------------------------------------------------------------------------
# The specification, transcribed by hand.
#
# Shape: {enum class name: {member name: wire value}}. Wire value is asserted
# as well as membership because every value here is part of the persisted wire
# format (11_CONTRACTS.md §3 preamble), and three of these enums deliberately
# do NOT use the member name as the value.
# ---------------------------------------------------------------------------

_IDENTITY_VALUED: dict[str, tuple[str, ...]] = {
    # -- Aggregate lifecycle states (11_CONTRACTS.md §3) --------------------
    "CaseStatus": (
        "OPEN",
        "WAITING",
        "ACTIONABLE",
        "IN_PROGRESS",
        "DISPUTED",
        "BLOCKED",
        "AWAITING_USER",
        "RESOLVED",
        "REOPENED",
        "SUPERSEDED",
    ),
    "CommitmentStatus": (
        "PROPOSED",
        "ACTIVE",
        "PARTIAL",
        "DISPUTED",
        "FULFILLED",
        "EXPIRED",
        "SUPERSEDED",
    ),
    "ConflictStatus": (
        "OPEN",
        "AUTO_RESOLVED",
        "NEEDS_HUMAN",
        "RESOLVED",
        "SUPERSEDED",
    ),
    "ConflictType": (
        "VALUE_CONFLICT",
        "TEMPORAL_CONFLICT",
        "AUTHORITY_CONFLICT",
        "IDENTITY_CONFLICT",
        "COMMITMENT_WITHDRAWAL_CONFLICT",
        "FULFILLMENT_CONFLICT",
        "POLICY_VERSION_CONFLICT",
    ),
    "ConflictSeverity": ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
    "EpistemicStatus": (
        "CONFIRMED",
        "PROBABLE",
        "UNCERTAIN",
        "DISPUTED",
        "SUPERSEDED",
        "RETRACTED",
    ),
    "ClaimKind": (
        "OBSERVATION",
        "COUNTERPARTY_CLAIM",
        "USER_CLAIM",
        "COMMITMENT_CLAIM",
        "POLICY_TERM",
        "FULFILLMENT_CLAIM",
        "CORRECTION",
        "INFERENCE",
    ),
    "EvidenceType": (
        "STATEMENT",
        "CONFIRMATION",
        "CANCELLATION_NOTICE",
        "SERVICE_STATUS_ASSERTION",
        "INVOICE_LINE",
        "PAYMENT_RECORD",
        "RECEIPT",
        "COMMITMENT_STATEMENT",
        "POLICY_TERM_TEXT",
        "DATE_ASSERTION",
        "AMOUNT_ASSERTION",
        "IDENTIFIER_ASSERTION",
        "ADDRESS_ASSERTION",
        "CORRECTION_NOTICE",
        "ATTACHMENT_REFERENCE",
        "QUOTED_HISTORY_EXCERPT",
    ),
    "RetractionStatus": ("ACTIVE", "SUPERSEDED", "RETRACTED", "QUARANTINED"),
    "ActionState": (
        "PROPOSED",
        "NEEDS_REVIEW",
        "APPROVED",
        "REJECTED",
        "EXECUTING",
        "EXECUTED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "CANCELLED",
        "CANCELLED_STALE",
    ),
    "ExecutionStatus": (
        "STARTED",
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "ABORTED_STALE",
    ),
    "TriggerState": ("ARMED", "FIRED", "DISARMED", "EXPIRED"),
    "OutboxStatus": (
        "PENDING",
        "DISPATCHING",
        "DISPATCHED",
        "FAILED_RETRYABLE",
        "DEAD",
    ),
    "KernelDecision": (
        "ACCEPTED",
        "ACCEPTED_WITH_CONFLICT",
        "NOOP_DUPLICATE",
        "PENDING_IDENTITY",
        "PENDING_HUMAN_REVIEW",
        "REJECTED_INVALID_PROVENANCE",
        "REJECTED_INVARIANT",
        "REJECTED_SCHEMA",
        "RETRYABLE_CONCURRENCY",
    ),
    "ProposalStatus": (
        "SUBMITTED",
        "ACCEPTED",
        "ACCEPTED_WITH_CONFLICT",
        "NOOP_DUPLICATE",
        "PENDING_IDENTITY",
        "PENDING_HUMAN_REVIEW",
        "REJECTED_INVALID_PROVENANCE",
        "REJECTED_INVARIANT",
        "REJECTED_SCHEMA",
    ),
    # -- Structural / referential enums ------------------------------------
    "SubjectType": (
        "RELATIONSHIP",
        "CASE",
        "COMMITMENT",
        "COUNTERPARTY",
        "USER",
        "ARTIFACT",
        "SERVICE",
    ),
    "ActorType": ("USER", "COUNTERPARTY", "THIRD_PARTY", "SYSTEM", "UNKNOWN"),
    "SupportRelation": ("SUPPORTS", "CONTRADICTS", "QUALIFIES"),
    "SemanticRelation": ("SUPPORTS", "CONTRADICTS", "QUALIFIES", "UNRELATED"),
    "SupportSourceKind": ("EVIDENCE", "CLAIM", "BELIEF_VERSION", "DERIVATION"),
    "ValueType": (
        "BOOLEAN",
        "STRING",
        "MONEY",
        "QUANTITY",
        "TIMESTAMP",
        "DATE",
        "INTERVAL",
        "ENUM",
        "IDENTIFIER",
        "ADDRESS",
        "STRUCT",
    ),
    "AggregateType": ("CASE", "RELATIONSHIP", "ACTION", "TRIGGER", "ARTIFACT"),
    "TransitionType": (
        "CASE_STATUS",
        "CASE_ATTENTION",
        "COMMITMENT_STATUS",
        "COMMITMENT_AMOUNT",
        "CONFLICT_STATUS",
        "BELIEF_VERSIONED",
        "TRIGGER_STATE",
        "ACTION_STATE",
        "RELATIONSHIP_STATUS",
        "EVIDENCE_RETRACTION",
    ),
    "AttentionLevel": ("NONE", "INFO", "ATTENTION", "URGENT"),
    "AdvocateAttentionClass": (
        "NONE",
        "FYI",
        "ACTION_SUGGESTED",
        "ACTION_REQUIRED",
        "HUMAN_DECISION",
    ),
    "RelationshipStatus": ("ACTIVE", "INACTIVE", "CLOSED"),
    "CaseType": (
        "SERVICE_CANCELLATION",
        "BILLING_DISPUTE",
        "DEPOSIT_RETURN",
        "DAMAGE_REIMBURSEMENT",
        "EXPENSE_REIMBURSEMENT",
        "WARRANTY_CLAIM",
        "REFUND",
        "ACCOUNT_CLOSURE",
        "SERVICE_INSTALLATION",
        "GENERAL",
    ),
    "CommitmentType": (
        "MONETARY_PAYMENT",
        "MONETARY_REFUND",
        "MONETARY_REIMBURSEMENT",
        "MONETARY_CREDIT",
        "DEPOSIT_RETURN",
        "SERVICE_TERMINATION",
        "SERVICE_DELIVERY",
        "REPAIR",
        "RESPONSE",
        "DOCUMENT_DELIVERY",
        "CORRECTION",
        "OTHER",
    ),
    "FulfillmentAdmissionStatus": (
        "ADMITTED",
        "CLAIMED_ONLY",
        "DISPUTED",
        "REJECTED",
        "REJECTED_CURRENCY",
    ),
    "ArtifactSourceType": (
        "EMAIL_INBOUND",
        "UPLOAD_EML",
        "UPLOAD_PDF",
        "UPLOAD_IMAGE",
        "UPLOAD_TEXT",
        "USER_CORRECTION",
        "SEED_FIXTURE",
    ),
    "ParserStatus": (
        "PENDING",
        "PARSING",
        "PARSED",
        "PARTIAL",
        "FAILED",
        "UNSUPPORTED_MIME",
    ),
    "ContentBlockKind": (
        "SUBJECT",
        "HEADER",
        "BODY",
        "QUOTED_HISTORY",
        "ATTACHMENT_TEXT",
        "TABLE",
        "FORM",
        "SIGNATURE",
    ),
    "TrustClass": ("UNTRUSTED", "TRUSTED_CANONICAL"),
    "Modality": (
        "ASSERTED_PAST",
        "ASSERTED_PRESENT",
        "PROMISED_FUTURE",
        "CONDITIONAL",
        "HYPOTHETICAL",
        "QUOTED_HISTORICAL",
    ),
    "DateGranularity": ("INSTANT", "DAY", "MONTH", "QUARTER", "YEAR", "UNKNOWN"),
    "DateRole": (
        "ISSUED_AT",
        "DUE_AT",
        "EFFECTIVE_FROM",
        "EFFECTIVE_TO",
        "SERVICE_PERIOD_START",
        "SERVICE_PERIOD_END",
        "EVENT_OCCURRED_AT",
        "UNKNOWN",
    ),
    "AmountRole": (
        "TOTAL_DUE",
        "LINE_ITEM",
        "PAID",
        "OUTSTANDING",
        "DEPOSIT",
        "CREDIT",
        "TAX",
        "UNKNOWN",
    ),
    "ExternalIdentifierKind": (
        "ACCOUNT_NUMBER",
        "INVOICE_NUMBER",
        "ORDER_NUMBER",
        "TICKET_NUMBER",
        "CONFIRMATION_CODE",
        "POLICY_NUMBER",
        "LEASE_NUMBER",
        "EMAIL_MESSAGE_ID",
        "EMAIL_THREAD_ID",
        "SERVICE_ADDRESS",
    ),
    "IdentityCandidateKind": ("RELATIONSHIP", "CASE"),
    "SourceClass": (
        "BANK_OR_CARD_STATEMENT",
        "PAYMENT_PROCESSOR_RECORD",
        "SIGNED_AGREEMENT",
        "PROVIDER_SYSTEM_NOTICE",
        "PROVIDER_AGENT_WRITTEN",
        "PROVIDER_AGENT_CHAT",
        "OFFICIAL_POLICY_DOC",
        "MARKETING_PAGE",
        "USER_UPLOADED_RECEIPT",
        "USER_STATEMENT",
        "USER_CORRECTION",
        "MODEL_INFERENCE",
    ),
    "BeliefMutationKind": ("CREATE", "REVISE", "SUPERSEDE", "RETRACT"),
    "TriggerMutationKind": ("ARM", "REARM", "DISARM", "EXTEND"),
    "TriggerType": (
        "COMMITMENT_DEADLINE",
        "RESPONSE_DEADLINE",
        "CONFLICT_TIMEOUT",
        "WARRANTY_WINDOW",
    ),
    "TriggerResult": ("FIRED", "NO_OP", "DISARMED", "EXPIRED", "ERROR"),
    "TriggerReasonCode": (
        "COMMITMENT_OVERDUE_UNPAID",
        "RESPONSE_DEADLINE_MISSED",
        "CONFLICT_UNRESOLVED_TIMEOUT",
        "WARRANTY_WINDOW_CLOSING",
        "PREDICATE_FALSE",
        "PREDICATE_UNKNOWN",
        "WOKE_TOO_EARLY",
        "STALE_SCHEDULE_GENERATION",
        "TRIGGER_NOT_ARMED",
        "CONCURRENT_CASE_MUTATION",
        "IDEMPOTENT_REPLAY",
        "COMMITMENT_SATISFIED",
        "COMMITMENT_SUPERSEDED",
        "BINDING_SUPERSEDED",
        "CASE_RESOLVED",
        "CASE_SUPERSEDED",
        "USER_DISMISSED",
        "TRIGGER_EXPIRED",
        "REARM_BUDGET_EXHAUSTED",
        "BINDING_UNRESOLVED",
        "PROJECTION_FAILED",
        "KERNEL_UNAVAILABLE",
    ),
    "WakeupSource": (
        "EVENTBRIDGE_SCHEDULER",
        "EVENTBRIDGE_RULE",
        "KERNEL_INLINE",
        "MANUAL_DEMO",
    ),
    "ActionType": (
        "OUTBOUND_EMAIL_DISPUTE",
        "OUTBOUND_EMAIL_FOLLOW_UP",
        "OUTBOUND_EMAIL_CANCELLATION_PROOF",
        "OUTBOUND_EMAIL_DEPOSIT_DEMAND",
        "INTERNAL_REMINDER",
    ),
    "PredicateOp": (
        "AND",
        "OR",
        "NOT",
        "EQ",
        "NE",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "IS_NULL",
        "NOT_NULL",
        "FIELD",
        "CONST",
    ),
    "ProposalType": (
        "INGESTION_INTERPRETATION",
        "TRIGGER_EVALUATION",
        "USER_CORRECTION",
        "FULFILLMENT_ADMISSION",
        "SYSTEM_DERIVATION",
        "SEED_FIXTURE",
    ),
    "PrincipalType": ("HUMAN", "WORKLOAD"),
    "WorkloadKind": (
        "AGENT_RUNTIME",
        "WORKER_INGEST",
        "WORKER_TRIGGER",
        "WORKER_OUTBOX",
        "WORKER_ACTION_EXECUTOR",
    ),
    "AgentRunStatus": ("RUNNING", "SUCCEEDED", "FAILED", "ABANDONED"),
    "MemoryMode": ("ON", "OFF"),
    "KernelReasonCode": (
        "SCHEMA_VERSION_UNSUPPORTED",
        "SCHEMA_FIELD_MISSING",
        "SCHEMA_TYPE_INVALID",
        "PROPOSAL_TOO_LARGE",
        "PRINCIPAL_USER_MISMATCH",
        "TENANT_MISMATCH",
        "EVIDENCE_NOT_FOUND",
        "ARTIFACT_NOT_FOUND",
        "EVIDENCE_FOREIGN_USER",
        "ARTIFACT_FOREIGN_USER",
        "EVIDENCE_ARTIFACT_MISMATCH",
        "CLAIM_EVIDENCE_UNLINKED",
        "SOURCE_RETRACTED_EXCLUDED",
        "PROPOSAL_ALREADY_DECIDED",
        "ARTIFACT_CONTENT_DUPLICATE",
        "CLAIM_SEMANTIC_DUPLICATE",
        "FULFILLMENT_EVIDENCE_DUPLICATE",
        "NO_CANONICAL_CHANGE",
        "IDENTITY_UNRESOLVED",
        "IDENTITY_AMBIGUOUS_MULTI_CASE",
        "IDENTITY_CONFIDENCE_BELOW_FLOOR",
        "RELATIONSHIP_NOT_FOUND",
        "CASE_NOT_IN_RELATIONSHIP",
        "CASE_TERMINAL_SUPERSEDED",
        "VALIDITY_UNKNOWN_NOT_COMPARABLE",
        "VALIDITY_INVERTED",
        "VALIDITY_FUTURE_BEYOND_HORIZON",
        "LATE_ARRIVING_HISTORICAL_VERSION",
        "SUPERSESSION_AUTHORITY_INSUFFICIENT",
        "CONFLICT_VALUE_MUTUAL_EXCLUSION",
        "CONFLICT_TEMPORAL_OVERLAP",
        "CONFLICT_AUTHORITY_TIE",
        "CONFLICT_CURRENCY_MISMATCH",
        "CONFLICT_OVER_FULFILMENT",
        "CONFLICT_COMMITMENT_WITHDRAWAL",
        "CONFLICT_PAYMENT_DENIAL",
        "CONFLICT_HINT_UNMAPPED_FAMILY",
        "AUTHORITY_UNMAPPED_SOURCE_CLASS",
        "AUTO_RESOLVED_AUTHORITY_MARGIN",
        "AUTO_RESOLVED_ENTAILMENT_PENALTY",
        "AUTO_RESOLVED_TEMPORAL_PRECEDENCE",
        "HUMAN_REQUIRED_AUTHORITY_TIE",
        "HUMAN_REQUIRED_WITHDRAWAL",
        "HUMAN_REQUIRED_USER_DISPUTE",
        "HUMAN_REQUIRED_MONETARY_THRESHOLD",
        "HUMAN_REQUIRED_ACTION_BLOCKING",
        "HUMAN_REQUIRED_UNRESOLVABLE_TYPE",
        "BELIEF_RETAINED_UNDER_CONTRADICTION",
        "BELIEF_SUPERSEDED_BY_CHALLENGER",
        "BELIEF_MARKED_DISPUTED",
        "BELIEF_CREATED",
        "COMMITMENT_PARTIAL_RECOMPUTED",
        "COMMITMENT_FULFILLED",
        "COMMITMENT_EXPIRED",
        "COMMITMENT_DISPUTED_EXCESS",
        "FULFILLMENT_ADMITTED",
        "FULFILLMENT_CURRENCY_REJECTED",
        "CASE_REOPENED_QUALIFYING_EVIDENCE",
        "CASE_REOPEN_REFUSED_NON_QUALIFYING",
        "CASE_REOPEN_LIMIT_REACHED",
        "CASE_TRANSITION_ILLEGAL",
        "CASE_TRANSITION_MULTIPLE_IN_COMMIT",
        "TRIGGER_ARMED",
        "TRIGGER_DISARMED_RESOLVED",
        "TRIGGER_FIRED_PREDICATE_TRUE",
        "TRIGGER_NOOP_PREDICATE_FALSE",
        "TRIGGER_EXPIRED",
        "INVARIANT_BELIEF_UNGROUNDED",
        "INVARIANT_OUTSTANDING_NEGATIVE",
        "INVARIANT_FULFILLED_STATUS_MISMATCH",
        "INVARIANT_REVISION_NOT_MONOTONIC",
        "INVARIANT_OVERLAPPING_LIVE_VERSIONS",
        "INVARIANT_DUPLICATE_SUPPORT_EDGE",
        "INVARIANT_BELIEF_IDENTITY",
        "INVARIANT_MULTI_CASE_PROPOSAL",
        "INVARIANT_TENANT_LEAK",
        "INVARIANT_UNIQUE_VIOLATION",
        "OPTIMISTIC_REVISION_MISMATCH",
        "RETRYABLE_CONCURRENCY",
        "RETRY_EXHAUSTED_NOT_ENQUEUED",
        "ACTION_IDEMPOTENCY_REPLAY",
    ),
    "CaseReopenReasonCode": (
        "CONTRADICTORY_EVIDENCE",
        "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "TRIGGER_FIRED_UNFULFILLED",
        "USER_DISPUTE",
        "FULFILLMENT_REVERSED",
    ),
}

# The three enums whose wire value is deliberately NOT the member name.
_EXPLICIT_VALUED: dict[str, dict[str, str]] = {
    "ModelTier": {"E": "E", "R": "R", "EMBEDDING": "EMBEDDING"},
    "OAuthScope": {
        "MEMORY_READ": "provenance.memory/read",
        "MEMORY_PROPOSE": "provenance.memory/propose",
        "ACTION_PROPOSE": "provenance.action/propose",
        "INGEST_WRITE": "provenance.ingest/write",
        "TRIGGER_EVALUATE": "provenance.trigger/evaluate",
        "ACTION_EXECUTE": "provenance.action/execute",
        "OUTBOX_DISPATCH": "provenance.outbox/dispatch",
    },
    "AgentSafeView": {
        "CASE_CONTEXT": "agent_case_context_v1",
        "ACTIVE_BELIEFS": "agent_active_beliefs_v1",
        "BELIEF_LINEAGE": "agent_belief_lineage_v1",
        "EVIDENCE_RETRIEVAL": "agent_evidence_retrieval_v1",
        "OPEN_OBLIGATIONS": "agent_open_obligations_v1",
    },
    "EventType": {
        "ARTIFACT_RECEIVED": "artifact.received.v1",
        "ARTIFACT_PARSED": "artifact.parsed.v1",
        "ARTIFACT_REJECTED": "artifact.rejected.v1",
        "EVIDENCE_ADMITTED": "evidence.admitted.v1",
        "EVIDENCE_RETRACTED": "evidence.retracted.v1",
        "MEMORY_PROPOSAL_ACCEPTED": "memory.proposal.accepted.v1",
        "MEMORY_PROPOSAL_REJECTED": "memory.proposal.rejected.v1",
        "BELIEF_CHANGED": "belief.changed.v1",
        "CONFLICT_DETECTED": "conflict.detected.v1",
        "CONFLICT_RESOLVED": "conflict.resolved.v1",
        "CASE_REOPENED": "case.reopened.v1",
        "CASE_STATE_CHANGED": "case.state_changed.v1",
        "COMMITMENT_CREATED": "commitment.created.v1",
        "COMMITMENT_PARTIALLY_FULFILLED": "commitment.partially_fulfilled.v1",
        "COMMITMENT_FULFILLED": "commitment.fulfilled.v1",
        "COMMITMENT_OVERDUE": "commitment.overdue.v1",
        "TRIGGER_ARMED": "trigger.armed.v1",
        "TRIGGER_FIRED": "trigger.fired.v1",
        "TRIGGER_NOOP": "trigger.noop.v1",
        "ACTION_PROPOSED": "action.proposed.v1",
        "ACTION_APPROVED": "action.approved.v1",
        "ACTION_REJECTED": "action.rejected.v1",
        "ACTION_EXECUTED": "action.executed.v1",
        "ACTION_FAILED": "action.failed.v1",
        "RELATIONSHIP_STATE_CHANGED": "relationship.state_changed.v1",
    },
}

SPEC: dict[str, dict[str, str]] = {
    **{name: {m: m for m in members} for name, members in _IDENTITY_VALUED.items()},
    **_EXPLICIT_VALUED,
}


def _exported_enums() -> dict[str, type[enum.StrEnum]]:
    """Every public `StrEnum` the module exposes, by attribute name."""
    found: dict[str, type[enum.StrEnum]] = {}
    for name, obj in vars(enums).items():
        if name.startswith("_"):
            continue
        if (
            isinstance(obj, type)
            and issubclass(obj, enum.StrEnum)
            and obj is not enum.StrEnum
            and obj.__members__
        ):
            found[name] = obj
    return found


# ---------------------------------------------------------------------------
# 1 — the whole closed vocabulary, membership and wire value
# ---------------------------------------------------------------------------


def test_every_closed_vocabulary_matches_the_specification_exactly() -> None:
    exported = _exported_enums()

    assert set(exported) == set(SPEC), (
        "the set of enums exported by provenance_domain.enums does not match "
        "11_CONTRACTS.md §3 plus the reason-code registries; "
        f"missing={sorted(set(SPEC) - set(exported))} "
        f"unexpected={sorted(set(exported) - set(SPEC))}"
    )

    for name, cls in sorted(exported.items()):
        actual = {member.name: member.value for member in cls}
        assert actual == SPEC[name], f"{name} membership/wire values drifted from the specification"
        assert len(cls) == len(SPEC[name]), f"{name} declares an alias; aliases are forbidden"
        assert list(cls.__members__) == list(actual), f"{name} declares an alias"

    # Every member is a real `str` on the wire, with no `use_enum_values` trick.
    for name, cls in sorted(exported.items()):
        for member in cls:
            assert isinstance(member, str), f"{name}.{member.name} is not a str"
            assert f"{member}" == member.value

    # `__all__` must actually name everything, or a consumer's star-import
    # silently loses a vocabulary.
    assert set(SPEC).issubset(set(enums.__all__))


# ---------------------------------------------------------------------------
# 2 — case attention: exactly four, no aliases
# ---------------------------------------------------------------------------


def test_case_attention_levels_are_exactly_four_with_no_aliases() -> None:
    expected = ("NONE", "INFO", "ATTENTION", "URGENT")

    assert [m.name for m in enums.AttentionLevel] == list(expected)
    assert [m.value for m in enums.AttentionLevel] == list(expected)
    assert len(enums.AttentionLevel) == 4

    # No aliases: an aliased StrEnum has more entries in __members__ than in
    # its canonical iteration order. CANONICAL_DECISIONS.md: "No aliases are
    # accepted."
    assert list(enums.AttentionLevel.__members__) == list(expected)

    for rejected in (
        "FYI",
        "ACTION_SUGGESTED",
        "ACTION_REQUIRED",
        "HUMAN_DECISION",
        "NEEDS_ATTENTION",
        "none",
        "Urgent",
        "",
    ):
        with pytest.raises(ValueError):
            enums.AttentionLevel.parse(rejected)


# ---------------------------------------------------------------------------
# 3 — advocate attention: a separate set of five
# ---------------------------------------------------------------------------


def test_advocate_attention_classes_are_the_separate_set_of_five() -> None:
    expected = ("NONE", "FYI", "ACTION_SUGGESTED", "ACTION_REQUIRED", "HUMAN_DECISION")

    assert [m.name for m in enums.AdvocateAttentionClass] == list(expected)
    assert [m.value for m in enums.AdvocateAttentionClass] == list(expected)
    assert len(enums.AdvocateAttentionClass) == 5
    assert list(enums.AdvocateAttentionClass.__members__) == list(expected)

    for rejected in ("INFO", "ATTENTION", "URGENT", "ACTION", "fyi"):
        with pytest.raises(ValueError):
            enums.AdvocateAttentionClass.parse(rejected)


# ---------------------------------------------------------------------------
# 4 — the two attention vocabularies never cross
# ---------------------------------------------------------------------------


def test_case_attention_is_not_advocate_attention_and_nothing_maps_them() -> None:
    case_levels = {"NONE", "INFO", "ATTENTION", "URGENT"}
    advocate_classes = {
        "NONE",
        "FYI",
        "ACTION_SUGGESTED",
        "ACTION_REQUIRED",
        "HUMAN_DECISION",
    }

    assert case_levels != advocate_classes
    assert case_levels & advocate_classes == {"NONE"}
    assert {m.value for m in enums.AttentionLevel} == case_levels
    assert {m.value for m in enums.AdvocateAttentionClass} == advocate_classes

    # Even the one shared spelling is two distinct members of two distinct
    # types. `cases.attention_level` must never receive an advocate class.
    assert enums.AttentionLevel.NONE is not enums.AdvocateAttentionClass.NONE
    assert type(enums.AttentionLevel.NONE) is not type(enums.AdvocateAttentionClass.NONE)
    assert not isinstance(enums.AdvocateAttentionClass.NONE, enums.AttentionLevel)
    assert not isinstance(enums.AttentionLevel.NONE, enums.AdvocateAttentionClass)

    for advocate_only in advocate_classes - case_levels:
        with pytest.raises(ValueError):
            enums.AttentionLevel.parse(advocate_only)
    for case_only in case_levels - advocate_classes:
        with pytest.raises(ValueError):
            enums.AdvocateAttentionClass.parse(case_only)

    # No implicit mapping lives here. The module declares vocabularies and
    # frozen cross-enum maps only; the advocate -> case mapping is
    # deterministic and belongs to the control plane (14_PROMPTS.md §4).
    functions = [
        name
        for name, obj in vars(enums).items()
        if not name.startswith("_")
        and (inspect.isfunction(obj) or inspect.isbuiltin(obj))
        and getattr(obj, "__module__", None) == enums.__name__
    ]
    assert functions == [], f"provenance_domain.enums must declare no functions, found {functions}"

    for name, obj in vars(enums).items():
        if name.startswith("_") or not isinstance(obj, Mapping):
            continue
        keys = set(obj)
        values = set(obj.values())
        crosses = (
            keys <= set(enums.AdvocateAttentionClass) and values <= set(enums.AttentionLevel)
        ) or (keys <= set(enums.AttentionLevel) and values <= set(enums.AdvocateAttentionClass))
        assert not crosses, f"{name} implicitly maps advocate attention onto case attention"


# ---------------------------------------------------------------------------
# 5 — trigger vocabulary
# ---------------------------------------------------------------------------


def test_trigger_types_and_results_are_exact() -> None:
    assert [m.value for m in enums.TriggerType] == [
        "COMMITMENT_DEADLINE",
        "RESPONSE_DEADLINE",
        "CONFLICT_TIMEOUT",
        "WARRANTY_WINDOW",
    ]
    assert [m.value for m in enums.TriggerResult] == [
        "FIRED",
        "NO_OP",
        "DISARMED",
        "EXPIRED",
        "ERROR",
    ]
    assert len(enums.TriggerType) == 4
    assert len(enums.TriggerResult) == 5

    # NO_OP, not NOOP: the trigger outcome and the kernel decision
    # `NOOP_DUPLICATE` are different vocabularies and must not be conflated.
    with pytest.raises(ValueError):
        enums.TriggerResult.parse("NOOP")
    with pytest.raises(ValueError):
        enums.TriggerResult.parse("NOOP_DUPLICATE")
    for rejected in ("SCHEDULED", "WOKEN", "ARMED", "PENDING"):
        with pytest.raises(ValueError):
            enums.TriggerResult.parse(rejected)

    # Trigger lifecycle state is its own enum and does not contain NO_OP.
    assert [m.value for m in enums.TriggerState] == ["ARMED", "FIRED", "DISARMED", "EXPIRED"]
    with pytest.raises(ValueError):
        enums.TriggerState.parse("NO_OP")


# ---------------------------------------------------------------------------
# 6 — retraction status
# ---------------------------------------------------------------------------


def test_retraction_status_is_exactly_the_four_canonical_values() -> None:
    # CANONICAL_DECISIONS.md, "Evidence and retrieval": ACTIVE, RETRACTED,
    # SUPERSEDED, QUARANTINED. Membership is the contract; declaration order
    # follows 11_CONTRACTS.md §3.
    assert {m.value for m in enums.RetractionStatus} == {
        "ACTIVE",
        "RETRACTED",
        "SUPERSEDED",
        "QUARANTINED",
    }
    assert len(enums.RetractionStatus) == 4

    # Only ACTIVE is retrieval-eligible, so no synonym may creep in.
    for rejected in ("DELETED", "PURGED", "TOMBSTONED", "INACTIVE", "active"):
        with pytest.raises(ValueError):
            enums.RetractionStatus.parse(rejected)


# ---------------------------------------------------------------------------
# 7 — the case-reopen reason code, spelled exactly one way
# ---------------------------------------------------------------------------


def test_case_reopen_reason_codes_admit_only_the_canonical_spelling() -> None:
    expected = {
        "CONTRADICTORY_EVIDENCE",
        "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "TRIGGER_FIRED_UNFULFILLED",
        "USER_DISPUTE",
        "FULFILLMENT_REVERSED",
    }

    assert set(enums.CASE_REOPEN_REASON_CODES) == expected
    assert isinstance(enums.CASE_REOPEN_REASON_CODES, frozenset)
    assert all(isinstance(code, str) for code in enums.CASE_REOPEN_REASON_CODES)

    # The hero commit canon, verbatim.
    assert "CONTRADICTORY_EVIDENCE" in enums.CASE_REOPEN_REASON_CODES
    assert "CONTRADICTORY_EVIDENCE_ADMITTED" not in enums.CASE_REOPEN_REASON_CODES
    assert "RC_CONTRADICTORY_EVIDENCE" not in enums.CASE_REOPEN_REASON_CODES

    assert {m.value for m in enums.CaseReopenReasonCode} == expected
    assert enums.CaseReopenReasonCode.CONTRADICTORY_EVIDENCE.value == "CONTRADICTORY_EVIDENCE"
    for rejected in ("CONTRADICTORY_EVIDENCE_ADMITTED", "RC_CONTRADICTORY_EVIDENCE"):
        with pytest.raises(ValueError):
            enums.CaseReopenReasonCode.parse(rejected)


# ---------------------------------------------------------------------------
# 8 — the two reason-code registries are closed
# ---------------------------------------------------------------------------


def test_kernel_and_trigger_reason_code_registries_are_closed() -> None:
    # 12_KERNEL_ALGORITHMS.md §9.3 — 81 codes.
    assert len(enums.KernelReasonCode) == 81
    assert {m.value for m in enums.KernelReasonCode} == set(SPEC["KernelReasonCode"])
    for expected in (
        "CASE_REOPENED_QUALIFYING_EVIDENCE",
        "CONFLICT_VALUE_MUTUAL_EXCLUSION",
        "AUTO_RESOLVED_ENTAILMENT_PENALTY",
        "BELIEF_RETAINED_UNDER_CONTRADICTION",
        "BELIEF_MARKED_DISPUTED",
        "TRIGGER_ARMED",
        "RETRY_EXHAUSTED_NOT_ENQUEUED",
    ):
        assert expected in {m.value for m in enums.KernelReasonCode}
    for rejected in ("SOMETHING_WENT_WRONG", "CASE_REOPENED", "RC_SCHEMA_INVALID"):
        with pytest.raises(ValueError):
            enums.KernelReasonCode.parse(rejected)

    # 16_TRIGGER_DSL.md §9.10 — 22 codes, one per outcome bucket.
    fired = {
        "COMMITMENT_OVERDUE_UNPAID",
        "RESPONSE_DEADLINE_MISSED",
        "CONFLICT_UNRESOLVED_TIMEOUT",
        "WARRANTY_WINDOW_CLOSING",
    }
    no_op = {
        "PREDICATE_FALSE",
        "PREDICATE_UNKNOWN",
        "WOKE_TOO_EARLY",
        "STALE_SCHEDULE_GENERATION",
        "TRIGGER_NOT_ARMED",
        "CONCURRENT_CASE_MUTATION",
        "IDEMPOTENT_REPLAY",
    }
    disarmed = {
        "COMMITMENT_SATISFIED",
        "COMMITMENT_SUPERSEDED",
        "BINDING_SUPERSEDED",
        "CASE_RESOLVED",
        "CASE_SUPERSEDED",
        "USER_DISMISSED",
    }
    expired = {"TRIGGER_EXPIRED", "REARM_BUDGET_EXHAUSTED"}
    error = {"BINDING_UNRESOLVED", "PROJECTION_FAILED", "KERNEL_UNAVAILABLE"}

    all_trigger_codes = fired | no_op | disarmed | expired | error
    assert len(all_trigger_codes) == 22
    assert {m.value for m in enums.TriggerReasonCode} == all_trigger_codes
    assert len(enums.TriggerReasonCode) == 22

    # The no-op reason codes named by the task plan must all be present.
    for code in no_op:
        assert enums.TriggerReasonCode.parse(code).value == code
    for rejected in ("PREDICATE_TRUE", "NO_OP", "TRIGGER_NOOP_PREDICATE_FALSE"):
        with pytest.raises(ValueError):
            enums.TriggerReasonCode.parse(rejected)

    # The two registries are distinct vocabularies that happen to share two
    # spellings; neither may be substituted for the other.
    assert {m.value for m in enums.KernelReasonCode} != all_trigger_codes
    with pytest.raises(ValueError):
        enums.KernelReasonCode.parse("PREDICATE_FALSE")


# ---------------------------------------------------------------------------
# 9 — parse raises rather than coercing, for every vocabulary
# ---------------------------------------------------------------------------


def test_parse_raises_on_unknown_members_and_never_coerces() -> None:
    exported = _exported_enums()
    assert exported, "no enums were exported"

    for name, cls in sorted(exported.items()):
        for member in cls:
            assert (
                cls.parse(member.value) is member
            ), f"{name}.parse did not round-trip a wire value"
            assert cls.parse(member) is member, f"{name}.parse did not accept its own member"

        for rejected in ("__NOT_A_MEMBER__", "", " ", "NONE_OF_THE_ABOVE"):
            if rejected in {m.value for m in cls}:
                continue
            with pytest.raises(ValueError):
                cls.parse(rejected)

        # A stale value from a superseded document must raise, never coerce to
        # a default or to the first member.
        for wrong_type in (None, 0, 1, [], {}, object()):
            with pytest.raises(ValueError):
                cls.parse(wrong_type)

    # Wire form, not member name: the three explicitly-valued enums must reject
    # their own attribute names, or a consumer that persists `ARTIFACT_RECEIVED`
    # instead of `artifact.received.v1` passes silently.
    for cls_name, members in _EXPLICIT_VALUED.items():
        cls = exported[cls_name]
        for member_name, wire in members.items():
            if member_name == wire:
                continue
            assert cls.parse(wire).name == member_name
            with pytest.raises(ValueError):
                cls.parse(member_name)
