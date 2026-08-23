"""Canonical domain enumerations for Provenance.

Stdlib only. Importable from Lambda cold-start paths and from the Memory
Kernel's transaction callback. No Pydantic, no I/O, no configuration reads.

Every value in this module is part of the persisted wire format. Adding a
member is a minor version change; removing or renaming one is a major
version change and requires a migration of the CHECK constraints in
db/migrations plus a bump of SCHEMA_VERSION in provenance_contracts.base.

Authority: `specs/11_CONTRACTS.md` section 3 for the vocabularies and the
frozen cross-enum maps; `specs/12_KERNEL_ALGORITHMS.md` section 9.3 for
``KernelReasonCode``; `specs/16_TRIGGER_DSL.md` section 9.10 for
``TriggerReasonCode``; `specs/11_CONTRACTS.md` section 4.1 for
``CASE_REOPEN_REASON_CODES``; `specs/14_PROMPTS.md` section 4 for
``AdvocateAttentionClass``.

Two attention vocabularies exist and they never cross. ``AttentionLevel`` is
the four-value column vocabulary of ``cases.attention_level``.
``AdvocateAttentionClass`` is a five-value *model output*. The mapping between
them is deterministic and belongs to the control plane; this module declares
no function that performs it, so no caller can reach for one by accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

__all__ = [
    "ActionState",
    "ActionType",
    "ActorType",
    "AdvocateAttentionClass",
    "AgentRunStatus",
    "AgentSafeView",
    "AggregateType",
    "AmountRole",
    "ArtifactSourceType",
    "AttentionLevel",
    "BeliefMutationKind",
    "CaseReopenReasonCode",
    "CaseStatus",
    "CaseType",
    "ClaimKind",
    "CommitmentStatus",
    "CommitmentType",
    "ConflictSeverity",
    "ConflictStatus",
    "ConflictType",
    "ContentBlockKind",
    "DateGranularity",
    "DateRole",
    "EpistemicStatus",
    "EventType",
    "EvidenceType",
    "ExecutionStatus",
    "ExternalIdentifierKind",
    "FulfillmentAdmissionStatus",
    "IdentityCandidateKind",
    "KernelDecision",
    "KernelReasonCode",
    "MemoryMode",
    "Modality",
    "ModelTier",
    "OAuthScope",
    "OutboxStatus",
    "ParserStatus",
    "PredicateOp",
    "PrincipalType",
    "ProposalStatus",
    "ProposalType",
    "RelationshipStatus",
    "RetractionStatus",
    "SemanticRelation",
    "SourceClass",
    "SubjectType",
    "SupportRelation",
    "SupportSourceKind",
    "TransitionType",
    "TriggerMutationKind",
    "TriggerReasonCode",
    "TriggerResult",
    "TriggerState",
    "TriggerType",
    "TrustClass",
    "ValueType",
    "WakeupSource",
    "WorkloadKind",
    "CASE_REOPEN_REASON_CODES",
    "DECISION_TO_PROPOSAL_STATUS",
    "EVENT_AGGREGATE_TYPE",
    "TERMINAL_KERNEL_DECISIONS",
    "ACCEPTING_KERNEL_DECISIONS",
]


class _ClosedVocabulary(StrEnum):
    """Base for every closed vocabulary in this module.

    Carries exactly one addition over ``StrEnum``: ``parse``, which raises on
    an unknown member instead of coercing. A stale value read back from a
    superseded document, a hand-edited fixture or an older deployment must
    surface as an exception at the boundary rather than silently becoming a
    default. This class declares no members, so subclassing it is legal.
    """

    @classmethod
    def parse(cls, value: object) -> Self:
        """Return the member whose **wire value** is ``value``.

        Accepts an existing member of ``cls`` unchanged. Everything else must
        be the exact persisted string: member *names* are not accepted where
        they differ from the value, because the name is not what is stored.

        Raises:
            ValueError: if ``value`` is not a member of this vocabulary.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        permitted = ", ".join(member.value for member in cls)
        raise ValueError(f"{value!r} is not a member of {cls.__name__}; permitted: {permitted}")


# ---------------------------------------------------------------------------
# Aggregate lifecycle states
# ---------------------------------------------------------------------------


class CaseStatus(_ClosedVocabulary):
    """Lifecycle of the primary consistency aggregate (`cases`)."""

    OPEN = "OPEN"
    WAITING = "WAITING"
    ACTIONABLE = "ACTIONABLE"
    IN_PROGRESS = "IN_PROGRESS"
    DISPUTED = "DISPUTED"
    BLOCKED = "BLOCKED"
    AWAITING_USER = "AWAITING_USER"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    SUPERSEDED = "SUPERSEDED"


class CommitmentStatus(_ClosedVocabulary):
    """Lifecycle of an obligation owed by or to the user."""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    DISPUTED = "DISPUTED"
    FULFILLED = "FULFILLED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ConflictStatus(_ClosedVocabulary):
    """Lifecycle of a durable contradiction object."""

    OPEN = "OPEN"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class ConflictType(_ClosedVocabulary):
    """Why two propositions cannot both be canonical."""

    VALUE_CONFLICT = "VALUE_CONFLICT"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    AUTHORITY_CONFLICT = "AUTHORITY_CONFLICT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    COMMITMENT_WITHDRAWAL_CONFLICT = "COMMITMENT_WITHDRAWAL_CONFLICT"
    FULFILLMENT_CONFLICT = "FULFILLMENT_CONFLICT"
    POLICY_VERSION_CONFLICT = "POLICY_VERSION_CONFLICT"


class ConflictSeverity(_ClosedVocabulary):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EpistemicStatus(_ClosedVocabulary):
    """How strongly Provenance holds a belief version."""

    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNCERTAIN = "UNCERTAIN"
    DISPUTED = "DISPUTED"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class ClaimKind(_ClosedVocabulary):
    """What sort of assertion a source actor made. Never truth."""

    OBSERVATION = "OBSERVATION"
    COUNTERPARTY_CLAIM = "COUNTERPARTY_CLAIM"
    USER_CLAIM = "USER_CLAIM"
    COMMITMENT_CLAIM = "COMMITMENT_CLAIM"
    POLICY_TERM = "POLICY_TERM"
    FULFILLMENT_CLAIM = "FULFILLMENT_CLAIM"
    CORRECTION = "CORRECTION"
    INFERENCE = "INFERENCE"


class EvidenceType(_ClosedVocabulary):
    """Kind of atomic immutable observation lifted out of an artifact.

    Admitting evidence means "this text was present in this artifact",
    never "this statement is true".
    """

    STATEMENT = "STATEMENT"
    CONFIRMATION = "CONFIRMATION"
    CANCELLATION_NOTICE = "CANCELLATION_NOTICE"
    SERVICE_STATUS_ASSERTION = "SERVICE_STATUS_ASSERTION"
    INVOICE_LINE = "INVOICE_LINE"
    PAYMENT_RECORD = "PAYMENT_RECORD"
    RECEIPT = "RECEIPT"
    COMMITMENT_STATEMENT = "COMMITMENT_STATEMENT"
    POLICY_TERM_TEXT = "POLICY_TERM_TEXT"
    DATE_ASSERTION = "DATE_ASSERTION"
    AMOUNT_ASSERTION = "AMOUNT_ASSERTION"
    IDENTIFIER_ASSERTION = "IDENTIFIER_ASSERTION"
    ADDRESS_ASSERTION = "ADDRESS_ASSERTION"
    CORRECTION_NOTICE = "CORRECTION_NOTICE"
    ATTACHMENT_REFERENCE = "ATTACHMENT_REFERENCE"
    QUOTED_HISTORY_EXCERPT = "QUOTED_HISTORY_EXCERPT"


class RetractionStatus(_ClosedVocabulary):
    """Addition C. Retracted evidence keeps its row and its embedding, so
    every retrieval path MUST filter on this flag or corrected evidence
    resurfaces through the vector index.
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"
    QUARANTINED = "QUARANTINED"


class ActionState(_ClosedVocabulary):
    """Lifecycle of an `action_intents` row (an outbound side effect)."""

    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"
    CANCELLED_STALE = "CANCELLED_STALE"


class ExecutionStatus(_ClosedVocabulary):
    """Per-attempt outcome recorded in `action_executions`."""

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    ABORTED_STALE = "ABORTED_STALE"


class TriggerState(_ClosedVocabulary):
    """Lifecycle of prospective memory."""

    ARMED = "ARMED"
    FIRED = "FIRED"
    DISARMED = "DISARMED"
    EXPIRED = "EXPIRED"


class OutboxStatus(_ClosedVocabulary):
    """Lifecycle of a transactional outbox row."""

    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    DEAD = "DEAD"


class KernelDecision(_ClosedVocabulary):
    """The deterministic Memory Kernel's verdict on one MemoryProposal."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_CONFLICT = "ACCEPTED_WITH_CONFLICT"
    NOOP_DUPLICATE = "NOOP_DUPLICATE"
    PENDING_IDENTITY = "PENDING_IDENTITY"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    REJECTED_INVALID_PROVENANCE = "REJECTED_INVALID_PROVENANCE"
    REJECTED_INVARIANT = "REJECTED_INVARIANT"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"
    RETRYABLE_CONCURRENCY = "RETRYABLE_CONCURRENCY"


class ProposalStatus(_ClosedVocabulary):
    """Lifecycle of a `memory_proposals` row."""

    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_CONFLICT = "ACCEPTED_WITH_CONFLICT"
    NOOP_DUPLICATE = "NOOP_DUPLICATE"
    PENDING_IDENTITY = "PENDING_IDENTITY"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    REJECTED_INVALID_PROVENANCE = "REJECTED_INVALID_PROVENANCE"
    REJECTED_INVARIANT = "REJECTED_INVARIANT"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"


# ---------------------------------------------------------------------------
# Structural / referential enums
# ---------------------------------------------------------------------------


class SubjectType(_ClosedVocabulary):
    RELATIONSHIP = "RELATIONSHIP"
    CASE = "CASE"
    COMMITMENT = "COMMITMENT"
    COUNTERPARTY = "COUNTERPARTY"
    USER = "USER"
    ARTIFACT = "ARTIFACT"
    SERVICE = "SERVICE"


class ActorType(_ClosedVocabulary):
    USER = "USER"
    COUNTERPARTY = "COUNTERPARTY"
    THIRD_PARTY = "THIRD_PARTY"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class SupportRelation(_ClosedVocabulary):
    """The relation stored on a `belief_support` (grounding) edge."""

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    QUALIFIES = "QUALIFIES"


class SemanticRelation(_ClosedVocabulary):
    """Advisory relation emitted by the Tier R resolver. Superset of
    SupportRelation: UNRELATED never becomes a persisted edge.
    """

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    QUALIFIES = "QUALIFIES"
    UNRELATED = "UNRELATED"


class SupportSourceKind(_ClosedVocabulary):
    EVIDENCE = "EVIDENCE"
    CLAIM = "CLAIM"
    BELIEF_VERSION = "BELIEF_VERSION"
    DERIVATION = "DERIVATION"


class ValueType(_ClosedVocabulary):
    """Discriminator for `belief_versions.value_json` / `claims.object_json`."""

    BOOLEAN = "BOOLEAN"
    STRING = "STRING"
    MONEY = "MONEY"
    QUANTITY = "QUANTITY"
    TIMESTAMP = "TIMESTAMP"
    DATE = "DATE"
    INTERVAL = "INTERVAL"
    ENUM = "ENUM"
    IDENTIFIER = "IDENTIFIER"
    ADDRESS = "ADDRESS"
    STRUCT = "STRUCT"


class AggregateType(_ClosedVocabulary):
    CASE = "CASE"
    RELATIONSHIP = "RELATIONSHIP"
    ACTION = "ACTION"
    TRIGGER = "TRIGGER"
    ARTIFACT = "ARTIFACT"


class TransitionType(_ClosedVocabulary):
    """`state_transitions.transition_type`."""

    CASE_STATUS = "CASE_STATUS"
    CASE_ATTENTION = "CASE_ATTENTION"
    COMMITMENT_STATUS = "COMMITMENT_STATUS"
    COMMITMENT_AMOUNT = "COMMITMENT_AMOUNT"
    CONFLICT_STATUS = "CONFLICT_STATUS"
    BELIEF_VERSIONED = "BELIEF_VERSIONED"
    TRIGGER_STATE = "TRIGGER_STATE"
    ACTION_STATE = "ACTION_STATE"
    RELATIONSHIP_STATUS = "RELATIONSHIP_STATUS"
    EVIDENCE_RETRACTION = "EVIDENCE_RETRACTION"


class AttentionLevel(_ClosedVocabulary):
    """`cases.attention_level`. Exactly four values, no aliases.

    This is the *persisted* vocabulary. `AdvocateAttentionClass` is a separate
    model output and must never be written here.
    """

    NONE = "NONE"
    INFO = "INFO"
    ATTENTION = "ATTENTION"
    URGENT = "URGENT"


class AdvocateAttentionClass(_ClosedVocabulary):
    """The Advocate's five attention classes (`specs/14_PROMPTS.md` section 4).

    A model output, not a column. `CANONICAL_DECISIONS.md` (*Names and
    counts*) makes the separation binding: these are "mapped deterministically
    to case attention and action policy, never stored directly in
    `cases.attention_level`". The mapping is the deterministic control plane's,
    and this module deliberately does not provide it — a helper here would be
    the shortest path to writing `ACTION_REQUIRED` into a four-value column.
    """

    NONE = "NONE"
    FYI = "FYI"
    ACTION_SUGGESTED = "ACTION_SUGGESTED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    HUMAN_DECISION = "HUMAN_DECISION"


class RelationshipStatus(_ClosedVocabulary):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CLOSED = "CLOSED"


class CaseType(_ClosedVocabulary):
    SERVICE_CANCELLATION = "SERVICE_CANCELLATION"
    BILLING_DISPUTE = "BILLING_DISPUTE"
    DEPOSIT_RETURN = "DEPOSIT_RETURN"
    DAMAGE_REIMBURSEMENT = "DAMAGE_REIMBURSEMENT"
    EXPENSE_REIMBURSEMENT = "EXPENSE_REIMBURSEMENT"
    WARRANTY_CLAIM = "WARRANTY_CLAIM"
    REFUND = "REFUND"
    ACCOUNT_CLOSURE = "ACCOUNT_CLOSURE"
    SERVICE_INSTALLATION = "SERVICE_INSTALLATION"
    GENERAL = "GENERAL"


class CommitmentType(_ClosedVocabulary):
    MONETARY_PAYMENT = "MONETARY_PAYMENT"
    MONETARY_REFUND = "MONETARY_REFUND"
    MONETARY_REIMBURSEMENT = "MONETARY_REIMBURSEMENT"
    MONETARY_CREDIT = "MONETARY_CREDIT"
    DEPOSIT_RETURN = "DEPOSIT_RETURN"
    SERVICE_TERMINATION = "SERVICE_TERMINATION"
    SERVICE_DELIVERY = "SERVICE_DELIVERY"
    REPAIR = "REPAIR"
    RESPONSE = "RESPONSE"
    DOCUMENT_DELIVERY = "DOCUMENT_DELIVERY"
    CORRECTION = "CORRECTION"
    OTHER = "OTHER"


class FulfillmentAdmissionStatus(_ClosedVocabulary):
    ADMITTED = "ADMITTED"
    CLAIMED_ONLY = "CLAIMED_ONLY"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"
    REJECTED_CURRENCY = "REJECTED_CURRENCY"


class ArtifactSourceType(_ClosedVocabulary):
    EMAIL_INBOUND = "EMAIL_INBOUND"
    UPLOAD_EML = "UPLOAD_EML"
    UPLOAD_PDF = "UPLOAD_PDF"
    UPLOAD_IMAGE = "UPLOAD_IMAGE"
    UPLOAD_TEXT = "UPLOAD_TEXT"
    USER_CORRECTION = "USER_CORRECTION"
    SEED_FIXTURE = "SEED_FIXTURE"


class ParserStatus(_ClosedVocabulary):
    PENDING = "PENDING"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED_MIME = "UNSUPPORTED_MIME"


class ContentBlockKind(_ClosedVocabulary):
    SUBJECT = "SUBJECT"
    HEADER = "HEADER"
    BODY = "BODY"
    QUOTED_HISTORY = "QUOTED_HISTORY"
    ATTACHMENT_TEXT = "ATTACHMENT_TEXT"
    TABLE = "TABLE"
    FORM = "FORM"
    SIGNATURE = "SIGNATURE"


class TrustClass(_ClosedVocabulary):
    """Prompt-boundary tag. Artifact-derived text is always UNTRUSTED and
    must be rendered inside the UNTRUSTED EVIDENCE section of a prompt.
    """

    UNTRUSTED = "UNTRUSTED"
    TRUSTED_CANONICAL = "TRUSTED_CANONICAL"


class Modality(_ClosedVocabulary):
    """Encodes the 'will / may / might / has / did' rule from the
    Interpreter prompt contract. The Kernel refuses to create a commitment
    from HYPOTHETICAL or QUOTED_HISTORICAL modality alone.
    """

    ASSERTED_PAST = "ASSERTED_PAST"
    ASSERTED_PRESENT = "ASSERTED_PRESENT"
    PROMISED_FUTURE = "PROMISED_FUTURE"
    CONDITIONAL = "CONDITIONAL"
    HYPOTHETICAL = "HYPOTHETICAL"
    QUOTED_HISTORICAL = "QUOTED_HISTORICAL"


class DateGranularity(_ClosedVocabulary):
    INSTANT = "INSTANT"
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"
    UNKNOWN = "UNKNOWN"


class DateRole(_ClosedVocabulary):
    ISSUED_AT = "ISSUED_AT"
    DUE_AT = "DUE_AT"
    EFFECTIVE_FROM = "EFFECTIVE_FROM"
    EFFECTIVE_TO = "EFFECTIVE_TO"
    SERVICE_PERIOD_START = "SERVICE_PERIOD_START"
    SERVICE_PERIOD_END = "SERVICE_PERIOD_END"
    EVENT_OCCURRED_AT = "EVENT_OCCURRED_AT"
    UNKNOWN = "UNKNOWN"


class AmountRole(_ClosedVocabulary):
    TOTAL_DUE = "TOTAL_DUE"
    LINE_ITEM = "LINE_ITEM"
    PAID = "PAID"
    OUTSTANDING = "OUTSTANDING"
    DEPOSIT = "DEPOSIT"
    CREDIT = "CREDIT"
    TAX = "TAX"
    UNKNOWN = "UNKNOWN"


class ExternalIdentifierKind(_ClosedVocabulary):
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    INVOICE_NUMBER = "INVOICE_NUMBER"
    ORDER_NUMBER = "ORDER_NUMBER"
    TICKET_NUMBER = "TICKET_NUMBER"
    CONFIRMATION_CODE = "CONFIRMATION_CODE"
    POLICY_NUMBER = "POLICY_NUMBER"
    LEASE_NUMBER = "LEASE_NUMBER"
    EMAIL_MESSAGE_ID = "EMAIL_MESSAGE_ID"
    EMAIL_THREAD_ID = "EMAIL_THREAD_ID"
    SERVICE_ADDRESS = "SERVICE_ADDRESS"


class IdentityCandidateKind(_ClosedVocabulary):
    RELATIONSHIP = "RELATIONSHIP"
    CASE = "CASE"


class SourceClass(_ClosedVocabulary):
    """The model may recommend a source CLASS. It never emits an authority
    SCORE. `provenance_domain.authority` maps (class, predicate family) to
    a configured band. See 02_DATA_MEMORY_TRANSACTIONS.md section 6.
    """

    BANK_OR_CARD_STATEMENT = "BANK_OR_CARD_STATEMENT"
    PAYMENT_PROCESSOR_RECORD = "PAYMENT_PROCESSOR_RECORD"
    SIGNED_AGREEMENT = "SIGNED_AGREEMENT"
    PROVIDER_SYSTEM_NOTICE = "PROVIDER_SYSTEM_NOTICE"
    PROVIDER_AGENT_WRITTEN = "PROVIDER_AGENT_WRITTEN"
    PROVIDER_AGENT_CHAT = "PROVIDER_AGENT_CHAT"
    OFFICIAL_POLICY_DOC = "OFFICIAL_POLICY_DOC"
    MARKETING_PAGE = "MARKETING_PAGE"
    USER_UPLOADED_RECEIPT = "USER_UPLOADED_RECEIPT"
    USER_STATEMENT = "USER_STATEMENT"
    USER_CORRECTION = "USER_CORRECTION"
    MODEL_INFERENCE = "MODEL_INFERENCE"


class BeliefMutationKind(_ClosedVocabulary):
    CREATE = "CREATE"
    REVISE = "REVISE"
    SUPERSEDE = "SUPERSEDE"
    RETRACT = "RETRACT"


class TriggerMutationKind(_ClosedVocabulary):
    ARM = "ARM"
    REARM = "REARM"
    DISARM = "DISARM"
    EXTEND = "EXTEND"


class TriggerType(_ClosedVocabulary):
    COMMITMENT_DEADLINE = "COMMITMENT_DEADLINE"
    RESPONSE_DEADLINE = "RESPONSE_DEADLINE"
    CONFLICT_TIMEOUT = "CONFLICT_TIMEOUT"
    WARRANTY_WINDOW = "WARRANTY_WINDOW"


class TriggerResult(_ClosedVocabulary):
    FIRED = "FIRED"
    NO_OP = "NO_OP"
    DISARMED = "DISARMED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class TriggerReasonCode(_ClosedVocabulary):
    """Closed reason registry for exactly one trigger-evaluation outcome.

    Grouped by the `TriggerResult` each code may accompany, per
    `specs/16_TRIGGER_DSL.md` section 9.10: FIRED, then NO_OP, then DISARMED,
    then EXPIRED, then ERROR. The result-to-code partition itself is the
    trigger evaluator's contract (T6.x) and is not declared here.
    """

    COMMITMENT_OVERDUE_UNPAID = "COMMITMENT_OVERDUE_UNPAID"
    RESPONSE_DEADLINE_MISSED = "RESPONSE_DEADLINE_MISSED"
    CONFLICT_UNRESOLVED_TIMEOUT = "CONFLICT_UNRESOLVED_TIMEOUT"
    WARRANTY_WINDOW_CLOSING = "WARRANTY_WINDOW_CLOSING"
    PREDICATE_FALSE = "PREDICATE_FALSE"
    PREDICATE_UNKNOWN = "PREDICATE_UNKNOWN"
    WOKE_TOO_EARLY = "WOKE_TOO_EARLY"
    STALE_SCHEDULE_GENERATION = "STALE_SCHEDULE_GENERATION"
    TRIGGER_NOT_ARMED = "TRIGGER_NOT_ARMED"
    CONCURRENT_CASE_MUTATION = "CONCURRENT_CASE_MUTATION"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    COMMITMENT_SATISFIED = "COMMITMENT_SATISFIED"
    COMMITMENT_SUPERSEDED = "COMMITMENT_SUPERSEDED"
    BINDING_SUPERSEDED = "BINDING_SUPERSEDED"
    CASE_RESOLVED = "CASE_RESOLVED"
    CASE_SUPERSEDED = "CASE_SUPERSEDED"
    USER_DISMISSED = "USER_DISMISSED"
    TRIGGER_EXPIRED = "TRIGGER_EXPIRED"
    REARM_BUDGET_EXHAUSTED = "REARM_BUDGET_EXHAUSTED"
    BINDING_UNRESOLVED = "BINDING_UNRESOLVED"
    PROJECTION_FAILED = "PROJECTION_FAILED"
    KERNEL_UNAVAILABLE = "KERNEL_UNAVAILABLE"


class WakeupSource(_ClosedVocabulary):
    EVENTBRIDGE_SCHEDULER = "EVENTBRIDGE_SCHEDULER"
    EVENTBRIDGE_RULE = "EVENTBRIDGE_RULE"
    KERNEL_INLINE = "KERNEL_INLINE"
    MANUAL_DEMO = "MANUAL_DEMO"


class ActionType(_ClosedVocabulary):
    OUTBOUND_EMAIL_DISPUTE = "OUTBOUND_EMAIL_DISPUTE"
    OUTBOUND_EMAIL_FOLLOW_UP = "OUTBOUND_EMAIL_FOLLOW_UP"
    OUTBOUND_EMAIL_CANCELLATION_PROOF = "OUTBOUND_EMAIL_CANCELLATION_PROOF"
    OUTBOUND_EMAIL_DEPOSIT_DEMAND = "OUTBOUND_EMAIL_DEPOSIT_DEMAND"
    INTERNAL_REMINDER = "INTERNAL_REMINDER"


class PredicateOp(_ClosedVocabulary):
    """The complete safe trigger-predicate grammar. No other operator may be
    stored. There is no user-supplied code path.
    """

    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    IS_NULL = "IS_NULL"
    NOT_NULL = "NOT_NULL"
    FIELD = "FIELD"
    CONST = "CONST"


class ProposalType(_ClosedVocabulary):
    INGESTION_INTERPRETATION = "INGESTION_INTERPRETATION"
    TRIGGER_EVALUATION = "TRIGGER_EVALUATION"
    USER_CORRECTION = "USER_CORRECTION"
    FULFILLMENT_ADMISSION = "FULFILLMENT_ADMISSION"
    SYSTEM_DERIVATION = "SYSTEM_DERIVATION"
    SEED_FIXTURE = "SEED_FIXTURE"


class ModelTier(_ClosedVocabulary):
    """Frozen model routing tiers. The concrete Bedrock inference-profile ids
    are read from configuration (`BEDROCK_REASONING_MODEL_ID`,
    `BEDROCK_EXTRACTION_MODEL_ID`) per the Bedrock model id canon, so no model
    id appears in this module.
    """

    E = "E"  # bulk structured extraction
    R = "R"  # resolution / advocacy
    EMBEDDING = "EMBEDDING"  # amazon.titan-embed-text-v2:0


class PrincipalType(_ClosedVocabulary):
    HUMAN = "HUMAN"
    WORKLOAD = "WORKLOAD"


class WorkloadKind(_ClosedVocabulary):
    AGENT_RUNTIME = "AGENT_RUNTIME"
    WORKER_INGEST = "WORKER_INGEST"
    WORKER_TRIGGER = "WORKER_TRIGGER"
    WORKER_OUTBOX = "WORKER_OUTBOX"
    WORKER_ACTION_EXECUTOR = "WORKER_ACTION_EXECUTOR"


class OAuthScope(_ClosedVocabulary):
    """Cognito resource-server scopes. Exactly the canon list."""

    MEMORY_READ = "provenance.memory/read"
    MEMORY_PROPOSE = "provenance.memory/propose"
    ACTION_PROPOSE = "provenance.action/propose"
    INGEST_WRITE = "provenance.ingest/write"
    TRIGGER_EVALUATE = "provenance.trigger/evaluate"
    ACTION_EXECUTE = "provenance.action/execute"
    OUTBOX_DISPATCH = "provenance.outbox/dispatch"


class AgentSafeView(_ClosedVocabulary):
    """Addition B. The only read surfaces the CockroachDB MCP server exposes
    to `pv_agent_reader`. Recorded in the Memory Trace so MCP use is visible
    rather than hidden plumbing.

    These are read-model view names, not canonical table names, and they are
    telemetry values rather than instructions. See section 12 for the
    no-SQL lint carve-out.
    """

    CASE_CONTEXT = "agent_case_context_v1"
    ACTIVE_BELIEFS = "agent_active_beliefs_v1"
    BELIEF_LINEAGE = "agent_belief_lineage_v1"
    EVIDENCE_RETRIEVAL = "agent_evidence_retrieval_v1"
    OPEN_OBLIGATIONS = "agent_open_obligations_v1"


class AgentRunStatus(_ClosedVocabulary):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class MemoryMode(_ClosedVocabulary):
    """Addition A. Judge Mode counterfactual toggle. ON is production
    behaviour; OFF disables retrieval and canonical memory so the same
    artifact can be processed side by side.
    """

    ON = "ON"
    OFF = "OFF"


class EventType(_ClosedVocabulary):
    """Closed registry. Consumers must not invent event names ad hoc."""

    ARTIFACT_RECEIVED = "artifact.received.v1"
    ARTIFACT_PARSED = "artifact.parsed.v1"
    ARTIFACT_REJECTED = "artifact.rejected.v1"
    EVIDENCE_ADMITTED = "evidence.admitted.v1"
    EVIDENCE_RETRACTED = "evidence.retracted.v1"

    MEMORY_PROPOSAL_ACCEPTED = "memory.proposal.accepted.v1"
    MEMORY_PROPOSAL_REJECTED = "memory.proposal.rejected.v1"
    BELIEF_CHANGED = "belief.changed.v1"
    CONFLICT_DETECTED = "conflict.detected.v1"
    CONFLICT_RESOLVED = "conflict.resolved.v1"

    CASE_REOPENED = "case.reopened.v1"
    CASE_STATE_CHANGED = "case.state_changed.v1"
    COMMITMENT_CREATED = "commitment.created.v1"
    COMMITMENT_PARTIALLY_FULFILLED = "commitment.partially_fulfilled.v1"
    COMMITMENT_FULFILLED = "commitment.fulfilled.v1"
    COMMITMENT_OVERDUE = "commitment.overdue.v1"

    TRIGGER_ARMED = "trigger.armed.v1"
    TRIGGER_FIRED = "trigger.fired.v1"
    TRIGGER_NOOP = "trigger.noop.v1"

    ACTION_PROPOSED = "action.proposed.v1"
    ACTION_APPROVED = "action.approved.v1"
    ACTION_REJECTED = "action.rejected.v1"
    ACTION_EXECUTED = "action.executed.v1"
    ACTION_FAILED = "action.failed.v1"
    RELATIONSHIP_STATE_CHANGED = "relationship.state_changed.v1"


# ---------------------------------------------------------------------------
# Closed reason-code registries
# ---------------------------------------------------------------------------


class KernelReasonCode(_ClosedVocabulary):
    """The Memory Kernel's reason-code catalogue.

    Authority: `specs/12_KERNEL_ALGORITHMS.md` section 9.3, in table order.
    Eighty-one codes, closed. `KernelCommitResult.reason_codes` is a list of
    these and nothing else; a stringly-typed message here is how a closed set
    leaks. `specs/12_KERNEL_ALGORITHMS.md` names this enum `ReasonCode` and
    places it in `provenance_domain/kernel/reasons.py`; that module re-exports
    this one rather than declaring a second copy, so the vocabulary has one
    definition.

    Two spellings are shared with other vocabularies and mean different things:
    `RETRYABLE_CONCURRENCY` is also a `KernelDecision`, and `TRIGGER_EXPIRED`
    is also a `TriggerReasonCode`. They are not interchangeable.
    """

    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    SCHEMA_FIELD_MISSING = "SCHEMA_FIELD_MISSING"
    SCHEMA_TYPE_INVALID = "SCHEMA_TYPE_INVALID"
    PROPOSAL_TOO_LARGE = "PROPOSAL_TOO_LARGE"
    PRINCIPAL_USER_MISMATCH = "PRINCIPAL_USER_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    EVIDENCE_FOREIGN_USER = "EVIDENCE_FOREIGN_USER"
    ARTIFACT_FOREIGN_USER = "ARTIFACT_FOREIGN_USER"
    EVIDENCE_ARTIFACT_MISMATCH = "EVIDENCE_ARTIFACT_MISMATCH"
    CLAIM_EVIDENCE_UNLINKED = "CLAIM_EVIDENCE_UNLINKED"
    SOURCE_RETRACTED_EXCLUDED = "SOURCE_RETRACTED_EXCLUDED"
    PROPOSAL_ALREADY_DECIDED = "PROPOSAL_ALREADY_DECIDED"
    ARTIFACT_CONTENT_DUPLICATE = "ARTIFACT_CONTENT_DUPLICATE"
    CLAIM_SEMANTIC_DUPLICATE = "CLAIM_SEMANTIC_DUPLICATE"
    FULFILLMENT_EVIDENCE_DUPLICATE = "FULFILLMENT_EVIDENCE_DUPLICATE"
    NO_CANONICAL_CHANGE = "NO_CANONICAL_CHANGE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    IDENTITY_AMBIGUOUS_MULTI_CASE = "IDENTITY_AMBIGUOUS_MULTI_CASE"
    IDENTITY_CONFIDENCE_BELOW_FLOOR = "IDENTITY_CONFIDENCE_BELOW_FLOOR"
    RELATIONSHIP_NOT_FOUND = "RELATIONSHIP_NOT_FOUND"
    CASE_NOT_IN_RELATIONSHIP = "CASE_NOT_IN_RELATIONSHIP"
    CASE_TERMINAL_SUPERSEDED = "CASE_TERMINAL_SUPERSEDED"
    VALIDITY_UNKNOWN_NOT_COMPARABLE = "VALIDITY_UNKNOWN_NOT_COMPARABLE"
    VALIDITY_INVERTED = "VALIDITY_INVERTED"
    VALIDITY_FUTURE_BEYOND_HORIZON = "VALIDITY_FUTURE_BEYOND_HORIZON"
    LATE_ARRIVING_HISTORICAL_VERSION = "LATE_ARRIVING_HISTORICAL_VERSION"
    SUPERSESSION_AUTHORITY_INSUFFICIENT = "SUPERSESSION_AUTHORITY_INSUFFICIENT"
    CONFLICT_VALUE_MUTUAL_EXCLUSION = "CONFLICT_VALUE_MUTUAL_EXCLUSION"
    CONFLICT_TEMPORAL_OVERLAP = "CONFLICT_TEMPORAL_OVERLAP"
    CONFLICT_AUTHORITY_TIE = "CONFLICT_AUTHORITY_TIE"
    CONFLICT_CURRENCY_MISMATCH = "CONFLICT_CURRENCY_MISMATCH"
    CONFLICT_OVER_FULFILMENT = "CONFLICT_OVER_FULFILMENT"
    CONFLICT_COMMITMENT_WITHDRAWAL = "CONFLICT_COMMITMENT_WITHDRAWAL"
    CONFLICT_PAYMENT_DENIAL = "CONFLICT_PAYMENT_DENIAL"
    CONFLICT_HINT_UNMAPPED_FAMILY = "CONFLICT_HINT_UNMAPPED_FAMILY"
    AUTHORITY_UNMAPPED_SOURCE_CLASS = "AUTHORITY_UNMAPPED_SOURCE_CLASS"
    AUTO_RESOLVED_AUTHORITY_MARGIN = "AUTO_RESOLVED_AUTHORITY_MARGIN"
    AUTO_RESOLVED_ENTAILMENT_PENALTY = "AUTO_RESOLVED_ENTAILMENT_PENALTY"
    AUTO_RESOLVED_TEMPORAL_PRECEDENCE = "AUTO_RESOLVED_TEMPORAL_PRECEDENCE"
    HUMAN_REQUIRED_AUTHORITY_TIE = "HUMAN_REQUIRED_AUTHORITY_TIE"
    HUMAN_REQUIRED_WITHDRAWAL = "HUMAN_REQUIRED_WITHDRAWAL"
    HUMAN_REQUIRED_USER_DISPUTE = "HUMAN_REQUIRED_USER_DISPUTE"
    HUMAN_REQUIRED_MONETARY_THRESHOLD = "HUMAN_REQUIRED_MONETARY_THRESHOLD"
    HUMAN_REQUIRED_ACTION_BLOCKING = "HUMAN_REQUIRED_ACTION_BLOCKING"
    HUMAN_REQUIRED_UNRESOLVABLE_TYPE = "HUMAN_REQUIRED_UNRESOLVABLE_TYPE"
    BELIEF_RETAINED_UNDER_CONTRADICTION = "BELIEF_RETAINED_UNDER_CONTRADICTION"
    BELIEF_SUPERSEDED_BY_CHALLENGER = "BELIEF_SUPERSEDED_BY_CHALLENGER"
    BELIEF_MARKED_DISPUTED = "BELIEF_MARKED_DISPUTED"
    BELIEF_CREATED = "BELIEF_CREATED"
    COMMITMENT_PARTIAL_RECOMPUTED = "COMMITMENT_PARTIAL_RECOMPUTED"
    COMMITMENT_FULFILLED = "COMMITMENT_FULFILLED"
    COMMITMENT_EXPIRED = "COMMITMENT_EXPIRED"
    COMMITMENT_DISPUTED_EXCESS = "COMMITMENT_DISPUTED_EXCESS"
    FULFILLMENT_ADMITTED = "FULFILLMENT_ADMITTED"
    FULFILLMENT_CURRENCY_REJECTED = "FULFILLMENT_CURRENCY_REJECTED"
    CASE_REOPENED_QUALIFYING_EVIDENCE = "CASE_REOPENED_QUALIFYING_EVIDENCE"
    CASE_REOPEN_REFUSED_NON_QUALIFYING = "CASE_REOPEN_REFUSED_NON_QUALIFYING"
    CASE_REOPEN_LIMIT_REACHED = "CASE_REOPEN_LIMIT_REACHED"
    CASE_TRANSITION_ILLEGAL = "CASE_TRANSITION_ILLEGAL"
    CASE_TRANSITION_MULTIPLE_IN_COMMIT = "CASE_TRANSITION_MULTIPLE_IN_COMMIT"
    TRIGGER_ARMED = "TRIGGER_ARMED"
    TRIGGER_DISARMED_RESOLVED = "TRIGGER_DISARMED_RESOLVED"
    TRIGGER_FIRED_PREDICATE_TRUE = "TRIGGER_FIRED_PREDICATE_TRUE"
    TRIGGER_NOOP_PREDICATE_FALSE = "TRIGGER_NOOP_PREDICATE_FALSE"
    TRIGGER_EXPIRED = "TRIGGER_EXPIRED"
    INVARIANT_BELIEF_UNGROUNDED = "INVARIANT_BELIEF_UNGROUNDED"
    INVARIANT_OUTSTANDING_NEGATIVE = "INVARIANT_OUTSTANDING_NEGATIVE"
    INVARIANT_FULFILLED_STATUS_MISMATCH = "INVARIANT_FULFILLED_STATUS_MISMATCH"
    INVARIANT_REVISION_NOT_MONOTONIC = "INVARIANT_REVISION_NOT_MONOTONIC"
    INVARIANT_OVERLAPPING_LIVE_VERSIONS = "INVARIANT_OVERLAPPING_LIVE_VERSIONS"
    INVARIANT_DUPLICATE_SUPPORT_EDGE = "INVARIANT_DUPLICATE_SUPPORT_EDGE"
    INVARIANT_BELIEF_IDENTITY = "INVARIANT_BELIEF_IDENTITY"
    INVARIANT_MULTI_CASE_PROPOSAL = "INVARIANT_MULTI_CASE_PROPOSAL"
    INVARIANT_TENANT_LEAK = "INVARIANT_TENANT_LEAK"
    INVARIANT_UNIQUE_VIOLATION = "INVARIANT_UNIQUE_VIOLATION"
    OPTIMISTIC_REVISION_MISMATCH = "OPTIMISTIC_REVISION_MISMATCH"
    RETRYABLE_CONCURRENCY = "RETRYABLE_CONCURRENCY"
    RETRY_EXHAUSTED_NOT_ENQUEUED = "RETRY_EXHAUSTED_NOT_ENQUEUED"
    ACTION_IDEMPOTENCY_REPLAY = "ACTION_IDEMPOTENCY_REPLAY"


class CaseReopenReasonCode(_ClosedVocabulary):
    """The only reason codes that may accompany `RESOLVED -> REOPENED`.

    Authority: `specs/11_CONTRACTS.md` section 4.1. The hero's code is
    `CONTRADICTORY_EVIDENCE`; `CONTRADICTORY_EVIDENCE_ADMITTED` and
    `RC_CONTRADICTORY_EVIDENCE` are not members and never were, so a
    transition carrying either raises rather than merely reading oddly
    (`CANONICAL_DECISIONS.md`, *Hero commit canon*).
    """

    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    COUNTERPARTY_CLAIM_AFTER_CLOSE = "COUNTERPARTY_CLAIM_AFTER_CLOSE"
    TRIGGER_FIRED_UNFULFILLED = "TRIGGER_FIRED_UNFULFILLED"
    USER_DISPUTE = "USER_DISPUTE"
    FULFILLMENT_REVERSED = "FULFILLMENT_REVERSED"


#: `specs/11_CONTRACTS.md` section 4.1 declares this as a
#: `Final[frozenset[str]]` and uses it directly as the guard allowlist on
#: `RESOLVED -> REOPENED`. It is derived from `CaseReopenReasonCode` rather
#: than typed a second time, so the enum and the guard cannot drift apart.
#: `provenance_domain.transitions` imports this name instead of redeclaring it.
CASE_REOPEN_REASON_CODES: Final[frozenset[str]] = frozenset(
    member.value for member in CaseReopenReasonCode
)


# ---------------------------------------------------------------------------
# Frozen cross-enum maps
# ---------------------------------------------------------------------------

EVENT_AGGREGATE_TYPE: Final[Mapping[EventType, AggregateType]] = MappingProxyType(
    {
        EventType.ARTIFACT_RECEIVED: AggregateType.ARTIFACT,
        EventType.ARTIFACT_PARSED: AggregateType.ARTIFACT,
        EventType.ARTIFACT_REJECTED: AggregateType.ARTIFACT,
        EventType.EVIDENCE_ADMITTED: AggregateType.ARTIFACT,
        EventType.EVIDENCE_RETRACTED: AggregateType.CASE,
        EventType.MEMORY_PROPOSAL_ACCEPTED: AggregateType.CASE,
        EventType.MEMORY_PROPOSAL_REJECTED: AggregateType.CASE,
        EventType.BELIEF_CHANGED: AggregateType.CASE,
        EventType.CONFLICT_DETECTED: AggregateType.CASE,
        EventType.CONFLICT_RESOLVED: AggregateType.CASE,
        EventType.CASE_REOPENED: AggregateType.CASE,
        EventType.CASE_STATE_CHANGED: AggregateType.CASE,
        EventType.COMMITMENT_CREATED: AggregateType.CASE,
        EventType.COMMITMENT_PARTIALLY_FULFILLED: AggregateType.CASE,
        EventType.COMMITMENT_FULFILLED: AggregateType.CASE,
        EventType.COMMITMENT_OVERDUE: AggregateType.CASE,
        EventType.TRIGGER_ARMED: AggregateType.TRIGGER,
        EventType.TRIGGER_FIRED: AggregateType.TRIGGER,
        EventType.TRIGGER_NOOP: AggregateType.TRIGGER,
        EventType.ACTION_PROPOSED: AggregateType.ACTION,
        EventType.ACTION_APPROVED: AggregateType.ACTION,
        EventType.ACTION_REJECTED: AggregateType.ACTION,
        EventType.ACTION_EXECUTED: AggregateType.ACTION,
        EventType.ACTION_FAILED: AggregateType.ACTION,
        EventType.RELATIONSHIP_STATE_CHANGED: AggregateType.RELATIONSHIP,
    }
)

DECISION_TO_PROPOSAL_STATUS: Final[Mapping[KernelDecision, ProposalStatus]] = MappingProxyType(
    {
        KernelDecision.ACCEPTED: ProposalStatus.ACCEPTED,
        KernelDecision.ACCEPTED_WITH_CONFLICT: ProposalStatus.ACCEPTED_WITH_CONFLICT,
        KernelDecision.NOOP_DUPLICATE: ProposalStatus.NOOP_DUPLICATE,
        KernelDecision.PENDING_IDENTITY: ProposalStatus.PENDING_IDENTITY,
        KernelDecision.PENDING_HUMAN_REVIEW: ProposalStatus.PENDING_HUMAN_REVIEW,
        KernelDecision.REJECTED_INVALID_PROVENANCE: ProposalStatus.REJECTED_INVALID_PROVENANCE,
        KernelDecision.REJECTED_INVARIANT: ProposalStatus.REJECTED_INVARIANT,
        KernelDecision.REJECTED_SCHEMA: ProposalStatus.REJECTED_SCHEMA,
        # RETRYABLE_CONCURRENCY leaves the proposal SUBMITTED for re-drive.
        KernelDecision.RETRYABLE_CONCURRENCY: ProposalStatus.SUBMITTED,
    }
)

ACCEPTING_KERNEL_DECISIONS: Final[frozenset[KernelDecision]] = frozenset(
    {KernelDecision.ACCEPTED, KernelDecision.ACCEPTED_WITH_CONFLICT}
)

TERMINAL_KERNEL_DECISIONS: Final[frozenset[KernelDecision]] = frozenset(
    {
        KernelDecision.ACCEPTED,
        KernelDecision.ACCEPTED_WITH_CONFLICT,
        KernelDecision.NOOP_DUPLICATE,
        KernelDecision.REJECTED_INVALID_PROVENANCE,
        KernelDecision.REJECTED_INVARIANT,
        KernelDecision.REJECTED_SCHEMA,
    }
)
