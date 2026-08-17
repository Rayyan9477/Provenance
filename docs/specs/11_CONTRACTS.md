# Provenance — Shared Contracts Specification

Purpose: the exact, copy-pasteable Pydantic v2 type system that every Provenance process — control plane, agent runtime, and Lambda workers — imports instead of re-declaring JSON shapes.

Status: planning-complete baseline v1.1
Implementation status: not started

Audience: coding agents generating `packages/python/provenance_contracts` and `packages/python/provenance_domain`; backend engineers writing the Memory Kernel and API layer; agent engineers writing LangGraph node signatures; reviewers checking that the four invariants are enforced by types and not by prose.

---

## 1. What this document is

`00_IMPLEMENTATION_MAP.md` §6 says shared contracts are mandatory and that coding agents must not re-declare JSON shapes independently in multiple services. `03_AGENTS_LANGGRAPH_CONTRACTS.md` sketches those shapes conceptually. This document converts both into real Python.

Everything below is the source of truth. If a service's local model disagrees with this file, the service is wrong.

Two packages are specified:

| Package | Contains | Depends on |
|---|---|---|
| `provenance_domain` | enums, state-machine transition tables, `legal_transition()`, invariant functions, authority bands, derivation registry | stdlib only |
| `provenance_contracts` | Pydantic v2 boundary models | `pydantic>=2.9`, `provenance_domain` |

`provenance_domain` deliberately has **no Pydantic dependency**. It is importable from the Kernel's hot path, from Lambda workers with tight cold-start budgets, and from test fixtures, without pulling a validation framework.

### 1.1 Directory layout

```text
packages/python/provenance_domain/
├── pyproject.toml
├── src/provenance_domain/
│   ├── __init__.py
│   ├── enums.py
│   ├── transitions.py
│   ├── invariants.py
│   ├── authority.py
│   └── derivations.py
└── tests/
    ├── test_transitions.py
    └── test_invariants.py

packages/python/provenance_contracts/
├── pyproject.toml
├── src/provenance_contracts/
│   ├── __init__.py
│   ├── base.py
│   ├── identity.py
│   ├── ingestion.py
│   ├── retrieval.py
│   ├── resolution.py
│   ├── predicates.py
│   ├── proposal.py
│   ├── kernel.py
│   ├── proof.py
│   ├── events.py
│   ├── actions.py
│   └── triggers.py
└── tests/
    ├── test_scalars.py
    ├── test_proposal_grounding.py
    ├── test_retrieval_retraction.py
    ├── test_draft_grounding.py
    ├── test_kernel_result.py
    ├── test_state_proof.py
    ├── test_roundtrip.py
    └── test_no_sql_in_contracts.py
```

---

## 2. Contract law

These eleven rules are enforced by code in this document, not by convention. Each rule names the mechanism that enforces it.

| # | Rule | Enforcement mechanism |
|---|---|---|
| L1 | Every boundary payload carries `schema_version` | `BoundaryContract` base class + `_validate_schema_version` |
| L2 | Money is `Decimal` + explicit `currency`; `float` is rejected | `Money` model, `_reject_float_amount` validator, `json_schema_extra={"type":"string"}` |
| L3 | Confidences and weights are `Decimal` in `[0, 1]`, quantised to 4 dp | `Confidence` annotated type |
| L4 | All timestamps are timezone-aware UTC | `UtcDatetime` annotated type; naive datetimes raise |
| L5 | No contract contains SQL text or a canonical table name | `SafeIdentifier` / `FieldPath` constrained types + `test_no_sql_in_contracts.py` AST lint |
| L6 | IDs are UUIDv7 where available, UUIDv4 otherwise | `new_id()` resolver in `base.py` |
| L7 | A canonical belief version is GROUNDED (≥1 `SUPPORTS` edge) unless it is a registered deterministic derivation | `ProposedBeliefMutation._require_grounding`, `BeliefProof._require_grounding` |
| L8 | Every state change is checked against a frozen transition table | `provenance_domain.transitions.legal_transition()` |
| L9 | Contracts are immutable; collections are tuples | `ConfigDict(frozen=True, extra="forbid")` |
| L10 | Machine principals never assert their own `user_id` | `InternalPrincipal` has no free `user_id`; only a server-issued `CapabilityBinding` |
| L11 | Outbound drafts carry only claims with support IDs | `DraftAction._require_support_and_spans` |

### 2.1 The three-term vocabulary in code

- **Provenance** appears only as the package prefix `provenance_*`. It is never a field name, never a variable, never a common noun.
- **grounding** is the set of `belief_support` edges linking a belief version to evidence or claims. In code: `ProposedSupportEdge`, `ProposedBeliefMutation.grounding`, `BeliefProof.grounding`, `GroundingEdgeProof`. Relations are `SUPPORTS | CONTRADICTS | QUALIFIES`.
- **lineage** is the `belief_versions` chain and the reason for each supersession. In code: `BeliefProof.lineage`, `LineageEntry.supersession_reason_code`.

`StateProof` renders both. A `BeliefProof` with grounding but no lineage is legal (a v1 belief). A `BeliefProof` with lineage but no grounding on its current version is a bug and is rejected by validation.

---

## 3. `provenance_domain/enums.py`

Every enum is a `StrEnum`, so the wire form is the member's string and Pydantic round-trips it without `use_enum_values` tricks.

```python
"""Canonical domain enumerations for Provenance.

Stdlib only. Importable from Lambda cold-start paths and from the Memory
Kernel's transaction callback. No Pydantic, no I/O, no configuration reads.

Every value in this module is part of the persisted wire format. Adding a
member is a minor version change; removing or renaming one is a major
version change and requires a migration of the CHECK constraints in
db/migrations plus a bump of SCHEMA_VERSION in provenance_contracts.base.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

__all__ = [
    "ActionState",
    "ActionType",
    "ActorType",
    "AgentRunStatus",
    "AgentSafeView",
    "AggregateType",
    "AmountRole",
    "ArtifactSourceType",
    "AttentionLevel",
    "BeliefMutationKind",
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
    "TriggerResult",
    "TriggerState",
    "TriggerType",
    "TrustClass",
    "ValueType",
    "WakeupSource",
    "WorkloadKind",
    "DECISION_TO_PROPOSAL_STATUS",
    "EVENT_AGGREGATE_TYPE",
    "TERMINAL_KERNEL_DECISIONS",
    "ACCEPTING_KERNEL_DECISIONS",
]


# ---------------------------------------------------------------------------
# Aggregate lifecycle states
# ---------------------------------------------------------------------------


class CaseStatus(StrEnum):
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


class CommitmentStatus(StrEnum):
    """Lifecycle of an obligation owed by or to the user."""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    DISPUTED = "DISPUTED"
    FULFILLED = "FULFILLED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ConflictStatus(StrEnum):
    """Lifecycle of a durable contradiction object."""

    OPEN = "OPEN"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class ConflictType(StrEnum):
    """Why two propositions cannot both be canonical."""

    VALUE_CONFLICT = "VALUE_CONFLICT"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    AUTHORITY_CONFLICT = "AUTHORITY_CONFLICT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    COMMITMENT_WITHDRAWAL_CONFLICT = "COMMITMENT_WITHDRAWAL_CONFLICT"
    FULFILLMENT_CONFLICT = "FULFILLMENT_CONFLICT"
    POLICY_VERSION_CONFLICT = "POLICY_VERSION_CONFLICT"


class ConflictSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EpistemicStatus(StrEnum):
    """How strongly Provenance holds a belief version."""

    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNCERTAIN = "UNCERTAIN"
    DISPUTED = "DISPUTED"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class ClaimKind(StrEnum):
    """What sort of assertion a source actor made. Never truth."""

    OBSERVATION = "OBSERVATION"
    COUNTERPARTY_CLAIM = "COUNTERPARTY_CLAIM"
    USER_CLAIM = "USER_CLAIM"
    COMMITMENT_CLAIM = "COMMITMENT_CLAIM"
    POLICY_TERM = "POLICY_TERM"
    FULFILLMENT_CLAIM = "FULFILLMENT_CLAIM"
    CORRECTION = "CORRECTION"
    INFERENCE = "INFERENCE"


class EvidenceType(StrEnum):
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


class RetractionStatus(StrEnum):
    """Addition C. Retracted evidence keeps its row and its embedding, so
    every retrieval path MUST filter on this flag or corrected evidence
    resurfaces through the vector index.
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"
    QUARANTINED = "QUARANTINED"


class ActionState(StrEnum):
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


class ExecutionStatus(StrEnum):
    """Per-attempt outcome recorded in `action_executions`."""

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    ABORTED_STALE = "ABORTED_STALE"


class TriggerState(StrEnum):
    """Lifecycle of prospective memory."""

    ARMED = "ARMED"
    FIRED = "FIRED"
    DISARMED = "DISARMED"
    EXPIRED = "EXPIRED"


class OutboxStatus(StrEnum):
    """Lifecycle of a transactional outbox row."""

    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    DEAD = "DEAD"


class KernelDecision(StrEnum):
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


class ProposalStatus(StrEnum):
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


class SubjectType(StrEnum):
    RELATIONSHIP = "RELATIONSHIP"
    CASE = "CASE"
    COMMITMENT = "COMMITMENT"
    COUNTERPARTY = "COUNTERPARTY"
    USER = "USER"
    ARTIFACT = "ARTIFACT"
    SERVICE = "SERVICE"


class ActorType(StrEnum):
    USER = "USER"
    COUNTERPARTY = "COUNTERPARTY"
    THIRD_PARTY = "THIRD_PARTY"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class SupportRelation(StrEnum):
    """The relation stored on a `belief_support` (grounding) edge."""

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    QUALIFIES = "QUALIFIES"


class SemanticRelation(StrEnum):
    """Advisory relation emitted by the Tier R resolver. Superset of
    SupportRelation: UNRELATED never becomes a persisted edge.
    """

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    QUALIFIES = "QUALIFIES"
    UNRELATED = "UNRELATED"


class SupportSourceKind(StrEnum):
    EVIDENCE = "EVIDENCE"
    CLAIM = "CLAIM"
    BELIEF_VERSION = "BELIEF_VERSION"
    DERIVATION = "DERIVATION"


class ValueType(StrEnum):
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


class AggregateType(StrEnum):
    CASE = "CASE"
    RELATIONSHIP = "RELATIONSHIP"
    ACTION = "ACTION"
    TRIGGER = "TRIGGER"
    ARTIFACT = "ARTIFACT"


class TransitionType(StrEnum):
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


class AttentionLevel(StrEnum):
    NONE = "NONE"
    INFO = "INFO"
    ATTENTION = "ATTENTION"
    URGENT = "URGENT"


class RelationshipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CLOSED = "CLOSED"


class CaseType(StrEnum):
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


class CommitmentType(StrEnum):
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


class FulfillmentAdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"
    CLAIMED_ONLY = "CLAIMED_ONLY"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"
    REJECTED_CURRENCY = "REJECTED_CURRENCY"


class ArtifactSourceType(StrEnum):
    EMAIL_INBOUND = "EMAIL_INBOUND"
    UPLOAD_EML = "UPLOAD_EML"
    UPLOAD_PDF = "UPLOAD_PDF"
    UPLOAD_IMAGE = "UPLOAD_IMAGE"
    UPLOAD_TEXT = "UPLOAD_TEXT"
    USER_CORRECTION = "USER_CORRECTION"
    SEED_FIXTURE = "SEED_FIXTURE"


class ParserStatus(StrEnum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED_MIME = "UNSUPPORTED_MIME"


class ContentBlockKind(StrEnum):
    SUBJECT = "SUBJECT"
    HEADER = "HEADER"
    BODY = "BODY"
    QUOTED_HISTORY = "QUOTED_HISTORY"
    ATTACHMENT_TEXT = "ATTACHMENT_TEXT"
    TABLE = "TABLE"
    FORM = "FORM"
    SIGNATURE = "SIGNATURE"


class TrustClass(StrEnum):
    """Prompt-boundary tag. Artifact-derived text is always UNTRUSTED and
    must be rendered inside the UNTRUSTED EVIDENCE section of a prompt.
    """

    UNTRUSTED = "UNTRUSTED"
    TRUSTED_CANONICAL = "TRUSTED_CANONICAL"


class Modality(StrEnum):
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


class DateGranularity(StrEnum):
    INSTANT = "INSTANT"
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"
    UNKNOWN = "UNKNOWN"


class DateRole(StrEnum):
    ISSUED_AT = "ISSUED_AT"
    DUE_AT = "DUE_AT"
    EFFECTIVE_FROM = "EFFECTIVE_FROM"
    EFFECTIVE_TO = "EFFECTIVE_TO"
    SERVICE_PERIOD_START = "SERVICE_PERIOD_START"
    SERVICE_PERIOD_END = "SERVICE_PERIOD_END"
    EVENT_OCCURRED_AT = "EVENT_OCCURRED_AT"
    UNKNOWN = "UNKNOWN"


class AmountRole(StrEnum):
    TOTAL_DUE = "TOTAL_DUE"
    LINE_ITEM = "LINE_ITEM"
    PAID = "PAID"
    OUTSTANDING = "OUTSTANDING"
    DEPOSIT = "DEPOSIT"
    CREDIT = "CREDIT"
    TAX = "TAX"
    UNKNOWN = "UNKNOWN"


class ExternalIdentifierKind(StrEnum):
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


class IdentityCandidateKind(StrEnum):
    RELATIONSHIP = "RELATIONSHIP"
    CASE = "CASE"


class SourceClass(StrEnum):
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


class BeliefMutationKind(StrEnum):
    CREATE = "CREATE"
    REVISE = "REVISE"
    SUPERSEDE = "SUPERSEDE"
    RETRACT = "RETRACT"


class TriggerMutationKind(StrEnum):
    ARM = "ARM"
    REARM = "REARM"
    DISARM = "DISARM"
    EXTEND = "EXTEND"


class TriggerType(StrEnum):
    COMMITMENT_DEADLINE = "COMMITMENT_DEADLINE"
    RESPONSE_DEADLINE = "RESPONSE_DEADLINE"
    CONFLICT_TIMEOUT = "CONFLICT_TIMEOUT"
    WARRANTY_WINDOW = "WARRANTY_WINDOW"


class TriggerResult(StrEnum):
    FIRED = "FIRED"
    NO_OP = "NO_OP"
    DISARMED = "DISARMED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class TriggerReasonCode(StrEnum):
    """Closed reason registry for exactly one trigger-evaluation outcome."""

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


class WakeupSource(StrEnum):
    EVENTBRIDGE_SCHEDULER = "EVENTBRIDGE_SCHEDULER"
    EVENTBRIDGE_RULE = "EVENTBRIDGE_RULE"
    KERNEL_INLINE = "KERNEL_INLINE"
    MANUAL_DEMO = "MANUAL_DEMO"


class ActionType(StrEnum):
    OUTBOUND_EMAIL_DISPUTE = "OUTBOUND_EMAIL_DISPUTE"
    OUTBOUND_EMAIL_FOLLOW_UP = "OUTBOUND_EMAIL_FOLLOW_UP"
    OUTBOUND_EMAIL_CANCELLATION_PROOF = "OUTBOUND_EMAIL_CANCELLATION_PROOF"
    OUTBOUND_EMAIL_DEPOSIT_DEMAND = "OUTBOUND_EMAIL_DEPOSIT_DEMAND"
    INTERNAL_REMINDER = "INTERNAL_REMINDER"


class PredicateOp(StrEnum):
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


class ProposalType(StrEnum):
    INGESTION_INTERPRETATION = "INGESTION_INTERPRETATION"
    TRIGGER_EVALUATION = "TRIGGER_EVALUATION"
    USER_CORRECTION = "USER_CORRECTION"
    FULFILLMENT_ADMISSION = "FULFILLMENT_ADMISSION"
    SYSTEM_DERIVATION = "SYSTEM_DERIVATION"
    SEED_FIXTURE = "SEED_FIXTURE"


class ModelTier(StrEnum):
    """Frozen model routing tiers. See the frozen canon."""

    E = "E"  # anthropic.claude-haiku-4-5      — bulk structured extraction
    R = "R"  # anthropic.claude-opus-5         — resolution / advocacy
    EMBEDDING = "EMBEDDING"  # amazon.titan-embed-text-v2:0


class PrincipalType(StrEnum):
    HUMAN = "HUMAN"
    WORKLOAD = "WORKLOAD"


class WorkloadKind(StrEnum):
    AGENT_RUNTIME = "AGENT_RUNTIME"
    WORKER_INGEST = "WORKER_INGEST"
    WORKER_TRIGGER = "WORKER_TRIGGER"
    WORKER_OUTBOX = "WORKER_OUTBOX"
    WORKER_ACTION_EXECUTOR = "WORKER_ACTION_EXECUTOR"


class OAuthScope(StrEnum):
    """Cognito resource-server scopes. Exactly the canon list."""

    MEMORY_READ = "provenance.memory/read"
    MEMORY_PROPOSE = "provenance.memory/propose"
    ACTION_PROPOSE = "provenance.action/propose"
    INGEST_WRITE = "provenance.ingest/write"
    TRIGGER_EVALUATE = "provenance.trigger/evaluate"
    ACTION_EXECUTE = "provenance.action/execute"
    OUTBOX_DISPATCH = "provenance.outbox/dispatch"


class AgentSafeView(StrEnum):
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


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class MemoryMode(StrEnum):
    """Addition A. Judge Mode counterfactual toggle. ON is production
    behaviour; OFF disables retrieval and canonical memory so the same
    artifact can be processed side by side.
    """

    ON = "ON"
    OFF = "OFF"


class EventType(StrEnum):
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

DECISION_TO_PROPOSAL_STATUS: Final[Mapping[KernelDecision, ProposalStatus]] = (
    MappingProxyType(
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
```

---

## 4. `provenance_domain/transitions.py`

Every legal state change in Provenance is declared once, as a frozen table, and checked through one helper. Nothing in the Kernel may hand-roll an `if status == ...` ladder.

```python
"""Frozen state-machine transition tables and the single legality helper.

Design rules:
  * Tables are `MappingProxyType` over `frozenset` values: immutable at
    import time, cheap to read, impossible to mutate from a caller.
  * A transition is legal only if it appears in the table AND satisfies any
    registered guard.
  * Guards are reason-code allowlists, not callables. A guard that must
    inspect amounts belongs in `provenance_domain.invariants`, because the
    Kernel evaluates it inside a serializable transaction with no I/O.
  * Self-transitions are illegal unless the machine sets
    `allow_self_loop=True`: a no-op must not increment a case revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Iterable, Mapping

from provenance_domain.enums import (
    ActionState,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    EpistemicStatus,
    OutboxStatus,
    ProposalStatus,
    TriggerState,
)

__all__ = [
    "IllegalTransitionError",
    "StateMachine",
    "legal_transition",
    "assert_transition",
    "reachable_states",
    "CASE_TRANSITIONS",
    "COMMITMENT_TRANSITIONS",
    "CONFLICT_TRANSITIONS",
    "ACTION_TRANSITIONS",
    "TRIGGER_TRANSITIONS",
    "OUTBOX_TRANSITIONS",
    "PROPOSAL_TRANSITIONS",
    "EPISTEMIC_TRANSITIONS",
    "CASE_MACHINE",
    "COMMITMENT_MACHINE",
    "CONFLICT_MACHINE",
    "ACTION_MACHINE",
    "TRIGGER_MACHINE",
    "OUTBOX_MACHINE",
    "PROPOSAL_MACHINE",
    "EPISTEMIC_MACHINE",
    "MACHINES",
    "CASE_REOPEN_REASON_CODES",
    "CASE_SUPERSEDE_REASON_CODES",
    "ACTION_EXECUTE_REASON_CODES",
    "ACTION_STALE_REASON_CODES",
    "COMMITMENT_UNFULFIL_REASON_CODES",
    "CONFLICT_REOPEN_REASON_CODES",
]


class IllegalTransitionError(ValueError):
    """Raised when a state change is not in the frozen transition table.

    Carries structured attributes so the Kernel can turn it into a
    `REJECTED_INVARIANT` decision with a machine-readable reason code
    instead of a stringly-typed error.
    """

    def __init__(
        self,
        machine: str,
        from_state: str,
        to_state: str,
        *,
        reason_code: str | None = None,
        detail: str = "",
    ) -> None:
        self.machine = machine
        self.from_state = from_state
        self.to_state = to_state
        self.reason_code = reason_code
        self.detail = detail
        message = f"{machine}: {from_state} -> {to_state} is not a legal transition"
        if reason_code is not None:
            message += f" with reason_code={reason_code!r}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class StateMachine:
    """An immutable declaration of one aggregate's legal state changes."""

    name: str
    transitions: Mapping[str, frozenset[str]]
    terminal: frozenset[str]
    guards: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType({})
    allow_self_loop: bool = False

    @property
    def states(self) -> frozenset[str]:
        out: set[str] = set(self.transitions)
        for targets in self.transitions.values():
            out.update(targets)
        return frozenset(out)

    def can(
        self, from_state: str, to_state: str, *, reason_code: str | None = None
    ) -> bool:
        return legal_transition(self, from_state, to_state, reason_code=reason_code)


def legal_transition(
    machine: StateMachine,
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
) -> bool:
    """Return True iff `from_state -> to_state` is permitted by `machine`.

    Accepts enum members or their string values interchangeably, because the
    Kernel reads states back from CockroachDB as plain strings.
    """
    frm = str(from_state)
    to = str(to_state)

    known = machine.states
    if frm not in known or to not in known:
        return False
    if frm == to:
        return machine.allow_self_loop
    if frm in machine.terminal:
        return False
    if to not in machine.transitions.get(frm, frozenset()):
        return False

    guard = machine.guards.get((frm, to))
    if guard is not None:
        if reason_code is None or reason_code not in guard:
            return False
    return True


def assert_transition(
    machine: StateMachine,
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
) -> None:
    """Raise `IllegalTransitionError` unless the transition is legal."""
    if legal_transition(machine, from_state, to_state, reason_code=reason_code):
        return

    frm, to = str(from_state), str(to_state)
    detail = ""
    if frm in machine.terminal:
        detail = f"{frm} is terminal"
    elif (frm, to) in machine.guards and reason_code not in machine.guards[(frm, to)]:
        allowed = ", ".join(sorted(machine.guards[(frm, to)]))
        detail = f"guarded transition requires reason_code in {{{allowed}}}"
    raise IllegalTransitionError(
        machine.name, frm, to, reason_code=reason_code, detail=detail
    )


def reachable_states(machine: StateMachine, from_state: str) -> frozenset[str]:
    """Breadth-first closure. Used by tests to prove no state is orphaned."""
    seen: set[str] = set()
    frontier: list[str] = [str(from_state)]
    while frontier:
        current = frontier.pop()
        for nxt in machine.transitions.get(current, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return frozenset(seen)


def _table(raw: dict[str, Iterable[str]]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType({str(k): frozenset(str(v) for v in vs) for k, vs in raw.items()})
```

### 4.1 Case machine

Stated assumption: `02_DATA_MEMORY_TRANSACTIONS.md` §13 calls `SUPERSEDED` "terminal for a case replaced by another case" but does not enumerate its inbound edges. We make `SUPERSEDED` reachable from every non-terminal state, guarded by a merge/split reason code, so case merges are expressible without a later schema change.

```python
CASE_REOPEN_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "CONTRADICTORY_EVIDENCE",
        "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "TRIGGER_FIRED_UNFULFILLED",
        "USER_DISPUTE",
        "FULFILLMENT_REVERSED",
    }
)

CASE_SUPERSEDE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"MERGED_INTO_CASE", "SPLIT_INTO_CASES", "DUPLICATE_CASE"}
)

_CASE_NON_TERMINAL: Final[tuple[CaseStatus, ...]] = (
    CaseStatus.OPEN,
    CaseStatus.WAITING,
    CaseStatus.ACTIONABLE,
    CaseStatus.IN_PROGRESS,
    CaseStatus.DISPUTED,
    CaseStatus.BLOCKED,
    CaseStatus.AWAITING_USER,
    CaseStatus.RESOLVED,
    CaseStatus.REOPENED,
)

CASE_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        CaseStatus.OPEN: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.WAITING: (
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.ACTIONABLE: (
            CaseStatus.IN_PROGRESS,
            CaseStatus.AWAITING_USER,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.IN_PROGRESS: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.DISPUTED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.AWAITING_USER,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.BLOCKED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.AWAITING_USER: (
            CaseStatus.ACTIONABLE,
            CaseStatus.IN_PROGRESS,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        # The hero scenario lives on this line.
        CaseStatus.RESOLVED: (CaseStatus.REOPENED, CaseStatus.SUPERSEDED),
        CaseStatus.REOPENED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.SUPERSEDED: (),
    }
)

CASE_MACHINE: Final[StateMachine] = StateMachine(
    name="case",
    transitions=CASE_TRANSITIONS,
    terminal=frozenset({str(CaseStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (str(CaseStatus.RESOLVED), str(CaseStatus.REOPENED)): CASE_REOPEN_REASON_CODES,
            **{
                (str(s), str(CaseStatus.SUPERSEDED)): CASE_SUPERSEDE_REASON_CODES
                for s in _CASE_NON_TERMINAL
            },
        }
    ),
    allow_self_loop=False,
)
```

### 4.2 Commitment, conflict, action machines

`FULFILLED -> DISPUTED` exists deliberately: a settled obligation can be reopened by a clawback or a counterparty retraction. It is guarded so a careless proposal cannot silently un-fulfil an obligation.

There is **no** edge from `PROPOSED` or `NEEDS_REVIEW` to `EXECUTING`. The only initial route to `EXECUTING` is `APPROVED -> EXECUTING`, guarded by `REVALIDATION_PASSED`; a retry may use `FAILED_RETRYABLE -> EXECUTING` under the same guard. The executor may emit that reason only after all five staleness checks in `02_DATA_MEMORY_TRANSACTIONS.md` §18 pass. That is invariant 4 expressed as a table.

```python
COMMITMENT_UNFULFIL_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "FULFILLMENT_REVERSED",
        "PAYMENT_CLAWED_BACK",
        "USER_DISPUTE",
        "COUNTERPARTY_RETRACTION",
    }
)

COMMITMENT_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        CommitmentStatus.PROPOSED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.ACTIVE: (
            CommitmentStatus.PARTIAL,
            CommitmentStatus.FULFILLED,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.PARTIAL: (
            CommitmentStatus.FULFILLED,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.DISPUTED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.PARTIAL,
            CommitmentStatus.FULFILLED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.FULFILLED: (
            CommitmentStatus.DISPUTED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.EXPIRED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.SUPERSEDED: (),
    }
)

COMMITMENT_MACHINE: Final[StateMachine] = StateMachine(
    name="commitment",
    transitions=COMMITMENT_TRANSITIONS,
    terminal=frozenset({str(CommitmentStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (
                str(CommitmentStatus.FULFILLED),
                str(CommitmentStatus.DISPUTED),
            ): COMMITMENT_UNFULFIL_REASON_CODES,
        }
    ),
)

CONFLICT_REOPEN_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"NEW_CONTRADICTORY_EVIDENCE", "USER_REJECTED_AUTO_RESOLUTION", "AUTHORITY_REASSESSED"}
)

CONFLICT_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ConflictStatus.OPEN: (
            ConflictStatus.AUTO_RESOLVED,
            ConflictStatus.NEEDS_HUMAN,
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.NEEDS_HUMAN: (
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.AUTO_RESOLVED: (
            ConflictStatus.OPEN,
            ConflictStatus.NEEDS_HUMAN,
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.RESOLVED: (ConflictStatus.OPEN, ConflictStatus.SUPERSEDED),
        ConflictStatus.SUPERSEDED: (),
    }
)

CONFLICT_MACHINE: Final[StateMachine] = StateMachine(
    name="conflict",
    transitions=CONFLICT_TRANSITIONS,
    terminal=frozenset({str(ConflictStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (
                str(ConflictStatus.RESOLVED),
                str(ConflictStatus.OPEN),
            ): CONFLICT_REOPEN_REASON_CODES,
            (
                str(ConflictStatus.AUTO_RESOLVED),
                str(ConflictStatus.OPEN),
            ): CONFLICT_REOPEN_REASON_CODES,
        }
    ),
)

ACTION_EXECUTE_REASON_CODES: Final[frozenset[str]] = frozenset({"REVALIDATION_PASSED"})

ACTION_STALE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "CASE_REVISION_CHANGED",
        "DRAFT_HASH_CHANGED",
        "SUPPORT_BELIEF_SUPERSEDED",
        "USER_CANCELLED",
    }
)

ACTION_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ActionState.PROPOSED: (
            ActionState.NEEDS_REVIEW,
            ActionState.APPROVED,
            ActionState.REJECTED,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.NEEDS_REVIEW: (
            ActionState.APPROVED,
            ActionState.REJECTED,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.APPROVED: (
            ActionState.EXECUTING,
            ActionState.NEEDS_REVIEW,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.EXECUTING: (
            ActionState.EXECUTED,
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_FINAL,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.FAILED_RETRYABLE: (
            ActionState.EXECUTING,
            ActionState.NEEDS_REVIEW,
            ActionState.FAILED_FINAL,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.EXECUTED: (),
        ActionState.REJECTED: (),
        ActionState.FAILED_FINAL: (),
        ActionState.CANCELLED: (),
        ActionState.CANCELLED_STALE: (),
    }
)

ACTION_MACHINE: Final[StateMachine] = StateMachine(
    name="action_intent",
    transitions=ACTION_TRANSITIONS,
    terminal=frozenset(
        {
            str(ActionState.EXECUTED),
            str(ActionState.REJECTED),
            str(ActionState.FAILED_FINAL),
            str(ActionState.CANCELLED),
            str(ActionState.CANCELLED_STALE),
        }
    ),
    guards=MappingProxyType(
        {
            (
                str(ActionState.APPROVED),
                str(ActionState.EXECUTING),
            ): ACTION_EXECUTE_REASON_CODES,
            (
                str(ActionState.FAILED_RETRYABLE),
                str(ActionState.EXECUTING),
            ): ACTION_EXECUTE_REASON_CODES,
            (
                str(ActionState.APPROVED),
                str(ActionState.CANCELLED_STALE),
            ): ACTION_STALE_REASON_CODES,
        }
    ),
)
```

### 4.3 Trigger, outbox, proposal, epistemic machines

Epistemic status is a property of a belief **version**, not of a mutable row. "Transition" therefore means: the status a new version may carry given the status of the version it supersedes. `allow_self_loop=True` because v2 may legitimately restate v1's status with stronger grounding.

```python
TRIGGER_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        TriggerState.ARMED: (
            TriggerState.FIRED,
            TriggerState.DISARMED,
            TriggerState.EXPIRED,
        ),
        TriggerState.FIRED: (
            TriggerState.ARMED,
            TriggerState.DISARMED,
            TriggerState.EXPIRED,
        ),
        TriggerState.DISARMED: (),
        TriggerState.EXPIRED: (),
    }
)

TRIGGER_MACHINE: Final[StateMachine] = StateMachine(
    name="prospective_trigger",
    transitions=TRIGGER_TRANSITIONS,
    terminal=frozenset({str(TriggerState.DISARMED), str(TriggerState.EXPIRED)}),
)

OUTBOX_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        OutboxStatus.PENDING: (OutboxStatus.DISPATCHING,),
        OutboxStatus.DISPATCHING: (
            OutboxStatus.DISPATCHED,
            OutboxStatus.FAILED_RETRYABLE,
        ),
        OutboxStatus.FAILED_RETRYABLE: (OutboxStatus.DISPATCHING, OutboxStatus.DEAD),
        OutboxStatus.DISPATCHED: (),
        OutboxStatus.DEAD: (),
    }
)

OUTBOX_MACHINE: Final[StateMachine] = StateMachine(
    name="outbox_event",
    transitions=OUTBOX_TRANSITIONS,
    terminal=frozenset({str(OutboxStatus.DISPATCHED), str(OutboxStatus.DEAD)}),
)

_PROPOSAL_OUTCOMES: Final[tuple[ProposalStatus, ...]] = (
    ProposalStatus.ACCEPTED,
    ProposalStatus.ACCEPTED_WITH_CONFLICT,
    ProposalStatus.NOOP_DUPLICATE,
    ProposalStatus.PENDING_IDENTITY,
    ProposalStatus.PENDING_HUMAN_REVIEW,
    ProposalStatus.REJECTED_INVALID_PROVENANCE,
    ProposalStatus.REJECTED_INVARIANT,
    ProposalStatus.REJECTED_SCHEMA,
)

PROPOSAL_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ProposalStatus.SUBMITTED: _PROPOSAL_OUTCOMES,
        ProposalStatus.PENDING_IDENTITY: (
            ProposalStatus.ACCEPTED,
            ProposalStatus.ACCEPTED_WITH_CONFLICT,
            ProposalStatus.PENDING_HUMAN_REVIEW,
            ProposalStatus.REJECTED_INVALID_PROVENANCE,
            ProposalStatus.REJECTED_INVARIANT,
        ),
        ProposalStatus.PENDING_HUMAN_REVIEW: (
            ProposalStatus.ACCEPTED,
            ProposalStatus.ACCEPTED_WITH_CONFLICT,
            ProposalStatus.REJECTED_INVARIANT,
        ),
        ProposalStatus.ACCEPTED: (),
        ProposalStatus.ACCEPTED_WITH_CONFLICT: (),
        ProposalStatus.NOOP_DUPLICATE: (),
        ProposalStatus.REJECTED_INVALID_PROVENANCE: (),
        ProposalStatus.REJECTED_INVARIANT: (),
        ProposalStatus.REJECTED_SCHEMA: (),
    }
)

PROPOSAL_MACHINE: Final[StateMachine] = StateMachine(
    name="memory_proposal",
    transitions=PROPOSAL_TRANSITIONS,
    terminal=frozenset(
        {
            str(ProposalStatus.ACCEPTED),
            str(ProposalStatus.ACCEPTED_WITH_CONFLICT),
            str(ProposalStatus.NOOP_DUPLICATE),
            str(ProposalStatus.REJECTED_INVALID_PROVENANCE),
            str(ProposalStatus.REJECTED_INVARIANT),
            str(ProposalStatus.REJECTED_SCHEMA),
        }
    ),
)

EPISTEMIC_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        EpistemicStatus.CONFIRMED: (
            EpistemicStatus.PROBABLE,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.PROBABLE: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.UNCERTAIN: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.PROBABLE,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.DISPUTED: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.PROBABLE,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.SUPERSEDED: (),
        EpistemicStatus.RETRACTED: (),
    }
)

EPISTEMIC_MACHINE: Final[StateMachine] = StateMachine(
    name="epistemic_status",
    transitions=EPISTEMIC_TRANSITIONS,
    terminal=frozenset(
        {str(EpistemicStatus.SUPERSEDED), str(EpistemicStatus.RETRACTED)}
    ),
    allow_self_loop=True,
)

MACHINES: Final[Mapping[str, StateMachine]] = MappingProxyType(
    {
        m.name: m
        for m in (
            CASE_MACHINE,
            COMMITMENT_MACHINE,
            CONFLICT_MACHINE,
            ACTION_MACHINE,
            TRIGGER_MACHINE,
            OUTBOX_MACHINE,
            PROPOSAL_MACHINE,
            EPISTEMIC_MACHINE,
        )
    }
)
```

### 4.4 Legality is necessary, not sufficient

`legal_transition()` answers "is this shape of change allowed". It does not answer "is this change consistent with the amounts". Those checks live in `invariants.py` below. The Kernel calls both, in this order, at steps 14 and 16 of the decision pipeline in `02_DATA_MEMORY_TRANSACTIONS.md` §8.

---

## 5. `provenance_domain` — invariants, authority, derivations

### 5.1 `invariants.py`

```python
"""Pure invariant functions. No I/O, no Pydantic. Safe to call inside a
serializable transaction callback and safe to call again on a 40001 retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from provenance_domain.enums import CommitmentStatus

__all__ = [
    "InvariantViolation",
    "CurrencyMismatchError",
    "CommitmentAmounts",
    "MONEY_EXPONENT",
    "quantise_money",
    "assert_money_scale",
    "derive_outstanding",
    "derive_commitment_status",
    "assert_commitment_consistent",
    "assert_revision_increment",
    "HUMAN_REVIEW_CONFIDENCE_FLOOR",
    "IDENTITY_STRONG_THRESHOLD",
    "IDENTITY_MARGIN_THRESHOLD",
]

MONEY_EXPONENT: Final[Decimal] = Decimal("0.0001")  # matches DECIMAL(20,4)

# Thresholds are configuration constants, never prompt text
# (03_AGENTS_LANGGRAPH_CONTRACTS.md section 5.7).
HUMAN_REVIEW_CONFIDENCE_FLOOR: Final[Decimal] = Decimal("0.70")
IDENTITY_STRONG_THRESHOLD: Final[Decimal] = Decimal("0.90")
IDENTITY_MARGIN_THRESHOLD: Final[Decimal] = Decimal("0.15")


class InvariantViolation(ValueError):
    """A hard domain rule was broken. Maps to KernelDecision.REJECTED_INVARIANT."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class CurrencyMismatchError(InvariantViolation):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(
            "CURRENCY_MISMATCH",
            f"refusing arithmetic across {left} and {right} "
            "without an explicit conversion event",
        )


def assert_money_scale(amount: Decimal) -> None:
    if not amount.is_finite():
        raise InvariantViolation("MONEY_NOT_FINITE", f"{amount!r} is not a finite decimal")
    if -amount.as_tuple().exponent > 4:
        raise InvariantViolation(
            "MONEY_SCALE",
            f"{amount} exceeds DECIMAL(20,4); round at the source, never here",
        )


def quantise_money(amount: Decimal) -> Decimal:
    """Normalise scale without changing value."""
    assert_money_scale(amount)
    return amount.quantize(MONEY_EXPONENT)


@dataclass(frozen=True, slots=True)
class CommitmentAmounts:
    currency: str
    committed: Decimal
    fulfilled: Decimal
    outstanding: Decimal


def derive_outstanding(
    *,
    currency: str,
    committed: Decimal,
    fulfilled: Decimal,
    fulfilment_currency: str,
) -> CommitmentAmounts:
    """`outstanding = committed - fulfilled`. Deterministic and currency-strict.

    Never clamps. Over-fulfilment yields a negative outstanding, which the
    Kernel turns into a FULFILLMENT_CONFLICT instead of silently absorbing.
    """
    if currency != fulfilment_currency:
        raise CurrencyMismatchError(currency, fulfilment_currency)
    assert_money_scale(committed)
    assert_money_scale(fulfilled)
    if committed < 0:
        raise InvariantViolation("NEGATIVE_COMMITMENT", f"committed={committed}")
    if fulfilled < 0:
        raise InvariantViolation("NEGATIVE_FULFILMENT", f"fulfilled={fulfilled}")
    return CommitmentAmounts(
        currency=currency,
        committed=quantise_money(committed),
        fulfilled=quantise_money(fulfilled),
        outstanding=quantise_money(committed - fulfilled),
    )


def derive_commitment_status(
    amounts: CommitmentAmounts,
    *,
    current: CommitmentStatus,
    has_blocking_conflict: bool,
) -> CommitmentStatus:
    """The only place commitment status is computed from money."""
    if amounts.outstanding < 0 or has_blocking_conflict:
        return CommitmentStatus.DISPUTED
    if amounts.committed > 0 and amounts.outstanding == 0:
        return CommitmentStatus.FULFILLED
    if amounts.fulfilled > 0:
        return CommitmentStatus.PARTIAL
    if current is CommitmentStatus.PROPOSED:
        return CommitmentStatus.PROPOSED
    return CommitmentStatus.ACTIVE


def assert_commitment_consistent(
    amounts: CommitmentAmounts, status: CommitmentStatus
) -> None:
    """Hero scenario: USD 420 owed, USD 200 paid, USD 220 outstanding, PARTIAL."""
    if amounts.outstanding != amounts.committed - amounts.fulfilled:
        raise InvariantViolation(
            "OUTSTANDING_NOT_DERIVED",
            f"{amounts.outstanding} != {amounts.committed} - {amounts.fulfilled}",
        )
    if status is CommitmentStatus.FULFILLED and amounts.outstanding > 0:
        raise InvariantViolation(
            "FULFILLED_WITH_OUTSTANDING", f"outstanding={amounts.outstanding}"
        )
    if status is CommitmentStatus.PARTIAL and amounts.fulfilled <= 0:
        raise InvariantViolation(
            "PARTIAL_WITHOUT_FULFILMENT", f"fulfilled={amounts.fulfilled}"
        )


def assert_revision_increment(before: int, after: int, *, changed: bool) -> None:
    """Invariant 3. One canonical commit moves a case revision by exactly one;
    a no-op does not move it at all.
    """
    expected = before + 1 if changed else before
    if after != expected:
        raise InvariantViolation(
            "REVISION_INCREMENT",
            f"expected case revision {expected}, got {after} (changed={changed})",
        )
```

### 5.2 `authority.py`

There is no single global trustworthiness score. The model recommends a `SourceClass`; this table maps `(predicate family, class)` to a band; the Kernel picks the value. Contracts never carry an authority score produced by a model.

```python
from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Final, Mapping

from provenance_domain.enums import SourceClass

__all__ = ["PREDICATE_FAMILIES", "AUTHORITY_BANDS", "authority_for", "predicate_family"]

PREDICATE_FAMILIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "service_terminated": "SERVICE_STATUS",
        "service_active": "SERVICE_STATUS",
        "billing_period_covered": "SERVICE_STATUS",
        "balance_owed": "BALANCE",
        "payment_received": "PAYMENT",
        "amount_outstanding": "OUTSTANDING",
        "commitment_status": "COMMITMENT_STATUS",
        "commitment_withdrawn": "COMMITMENT_STATUS",
    }
)

# Exact scores, not fitted probabilities. §12.3.2 is the semantic owner; the
# documentation lint compares this complete matrix with that table.
_FAMILIES = ("SERVICE_STATUS", "BALANCE", "PAYMENT", "OUTSTANDING", "COMMITMENT_STATUS")

def _row(*values: str) -> Mapping[str, Decimal]:
    return MappingProxyType(dict(zip(_FAMILIES, map(Decimal, values), strict=True)))

AUTHORITY_SCORES: Final[Mapping[SourceClass, Mapping[str, Decimal]]] = MappingProxyType({
    SourceClass.BANK_OR_CARD_STATEMENT: _row("0.10", "0.55", "0.97", "0.60", "0.10"),
    SourceClass.PAYMENT_PROCESSOR_RECORD: _row("0.10", "0.60", "0.96", "0.60", "0.10"),
    SourceClass.SIGNED_AGREEMENT: _row("0.92", "0.85", "0.30", "0.90", "0.95"),
    SourceClass.PROVIDER_SYSTEM_NOTICE: _row("0.88", "0.90", "0.70", "0.72", "0.55"),
    SourceClass.PROVIDER_AGENT_WRITTEN: _row("0.85", "0.72", "0.55", "0.70", "0.88"),
    SourceClass.PROVIDER_AGENT_CHAT: _row("0.68", "0.55", "0.45", "0.55", "0.70"),
    SourceClass.OFFICIAL_POLICY_DOC: _row("0.60", "0.50", "0.20", "0.45", "0.62"),
    SourceClass.MARKETING_PAGE: _row("0.35", "0.25", "0.05", "0.20", "0.30"),
    SourceClass.USER_UPLOADED_RECEIPT: _row("0.30", "0.45", "0.80", "0.50", "0.25"),
    SourceClass.USER_STATEMENT: _row("0.45", "0.40", "0.50", "0.48", "0.40"),
    SourceClass.USER_CORRECTION: _row("0.75", "0.70", "0.70", "0.72", "0.70"),
    SourceClass.MODEL_INFERENCE: _row("0.05", "0.05", "0.05", "0.05", "0.05"),
})

_UNKNOWN_SCORE: Final[Decimal] = Decimal("0.10")


def predicate_family(predicate: str) -> str:
    return PREDICATE_FAMILIES.get(predicate, "UNMAPPED")


def authority_for(predicate: str, source_class: SourceClass) -> Decimal:
    """Return the frozen predicate-family/source-class score from §12.3.2."""
    family = predicate_family(predicate)
    return AUTHORITY_SCORES.get(source_class, {}).get(
        family, _UNKNOWN_SCORE
    ).quantize(Decimal("0.0001"))
```

### 5.3 `derivations.py`

The grounding invariant says a canonical belief version must carry at least one `SUPPORTS` edge **unless it is an explicitly defined deterministic derivation**. This module is that definition. A `ProposedBeliefMutation` may skip grounding only by naming a derivation that appears here.

```python
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

__all__ = ["DerivationSpec", "DERIVATION_REGISTRY", "is_registered_derivation"]


@dataclass(frozen=True, slots=True)
class DerivationSpec:
    name: str
    function_version: str
    input_kinds: tuple[str, ...]
    description: str


DERIVATION_REGISTRY: Final[Mapping[str, DerivationSpec]] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            DerivationSpec(
                name="outstanding_from_committed_minus_fulfilled",
                function_version="1.0.0",
                input_kinds=("COMMITMENT", "FULFILLMENT"),
                description="outstanding = committed - admitted fulfilment total",
            ),
            DerivationSpec(
                name="commitment_overdue_from_due_at",
                function_version="1.0.0",
                input_kinds=("COMMITMENT", "CLOCK"),
                description="overdue = clock.now >= due_at AND outstanding > 0",
            ),
            DerivationSpec(
                name="case_attention_from_open_conflicts",
                function_version="1.0.0",
                input_kinds=("CASE", "CONFLICT"),
                description="attention rises to URGENT while an OPEN conflict exists",
            ),
            DerivationSpec(
                name="service_period_overlap",
                function_version="1.0.0",
                input_kinds=("BELIEF_VERSION", "CLAIM"),
                description="a billed period overlapping a terminated period is mutually exclusive",
            ),
        )
    }
)


def is_registered_derivation(name: str, function_version: str) -> bool:
    spec = DERIVATION_REGISTRY.get(name)
    return spec is not None and spec.function_version == function_version
```

---

## 6. `provenance_contracts/base.py`

This module encodes contract law L1–L6 and L9. Every other contracts module imports from here and adds nothing to the scalar vocabulary.

```python
"""Scalar types, immutability policy, ID generation, and canonical hashing.

Nothing in `provenance_contracts` may define its own datetime handling,
its own money type, or its own confidence bound. They live here once.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Callable, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_MAJORS",
    "Contract",
    "BoundaryContract",
    "Money",
    "Confidence",
    "Weight",
    "UtcDatetime",
    "CurrencyCode",
    "IanaTimezone",
    "Sha256Hex",
    "SafeIdentifier",
    "ReasonCode",
    "LocalId",
    "BlockId",
    "IdempotencyKey",
    "Revision",
    "new_id",
    "utc_now",
    "UUID_GENERATOR",
    "canonical_json",
    "content_hash",
]

SCHEMA_VERSION: Final[str] = "1.0"
SUPPORTED_SCHEMA_MAJORS: Final[frozenset[str]] = frozenset({"1"})


# ---------------------------------------------------------------------------
# L6 — identifiers: UUIDv7 where available, UUIDv4 fallback
# ---------------------------------------------------------------------------


def _resolve_uuid7() -> tuple[Callable[[], uuid.UUID], str]:
    """Pick the best available time-ordered UUID generator.

    Order: stdlib `uuid.uuid7` (Python 3.14+), then the `uuid6` backport
    (returns a stdlib `uuid.UUID`), then `uuid_utils` (Rust, coerced through
    `str`), then `uuid.uuid4`. Resolved once at import; the chosen name is
    exported so telemetry can record which one a deployment actually used.

    UUIDv7 matters here for a concrete reason: CockroachDB range splits are
    lexicographic on the primary key, and time-ordered UUIDs keep an
    append-heavy table such as evidence_items from scattering. It is a
    performance preference, never a correctness requirement -- no code may
    infer ordering from an ID.
    """
    stdlib_uuid7 = getattr(uuid, "uuid7", None)
    if callable(stdlib_uuid7):
        return stdlib_uuid7, "stdlib.uuid7"
    try:
        from uuid6 import uuid7 as _uuid6_uuid7  # type: ignore[import-not-found]

        return _uuid6_uuid7, "uuid6.uuid7"
    except ImportError:
        pass
    try:
        import uuid_utils  # type: ignore[import-not-found]

        return (lambda: uuid.UUID(str(uuid_utils.uuid7()))), "uuid_utils.uuid7"
    except ImportError:
        pass
    return uuid.uuid4, "stdlib.uuid4"


_UUID_IMPL, UUID_GENERATOR = _resolve_uuid7()


def new_id() -> uuid.UUID:
    """Allocate an opaque, externally visible identifier."""
    return _UUID_IMPL()


def utc_now() -> datetime:
    """The only clock read in the contracts package."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# L4 — timestamps are timezone-aware UTC
# ---------------------------------------------------------------------------


def _coerce_utc(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "naive datetime rejected: every Provenance timestamp is "
                "timezone-aware UTC; the UI localises, the wire never does"
            )
        return value.astimezone(timezone.utc)
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


def _check_iana_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


IanaTimezone = Annotated[
    str, StringConstraints(min_length=1, max_length=64), BeforeValidator(_check_iana_timezone)
]


# ---------------------------------------------------------------------------
# L3 — confidences and weights
# ---------------------------------------------------------------------------

_CONFIDENCE_QUANTUM: Final[Decimal] = Decimal("0.0001")


def _coerce_confidence(value: Any) -> Any:
    """Accept int/float/str/Decimal, quantise to 4 dp, reject bool and NaN.

    Floats are tolerated here (unlike money) because a model emitting
    `"confidence": 0.87` in JSON is normal and the value is advisory. It is
    immediately quantised so two runs producing 0.8700000001 and 0.87 hash
    identically.
    """
    if isinstance(value, bool):
        raise ValueError("bool is not a confidence")
    if isinstance(value, (int, float, str, Decimal)):
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"not a decimal confidence: {value!r}") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"confidence must be finite, got {value!r}")
        return decimal_value.quantize(_CONFIDENCE_QUANTUM)
    return value


Confidence = Annotated[
    Decimal,
    BeforeValidator(_coerce_confidence),
    Field(ge=Decimal("0"), le=Decimal("1"), description="Decimal in [0,1], 4 dp"),
]

#: Grounding-edge weight. Same domain and coercion as `Confidence`; a distinct
#: alias so a reader can tell "how sure the extractor was" from "how much this
#: edge counts".
Weight = Confidence


# ---------------------------------------------------------------------------
# L5 — constrained identifier types that structurally cannot carry SQL
# ---------------------------------------------------------------------------

#: Predicate names, derivation names, transition names. Lowercase snake with
#: dots. A string matching this pattern cannot contain whitespace, quotes,
#: semicolons or parentheses, so it cannot express a SQL statement.
SafeIdentifier = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", max_length=128)
]

#: Machine-readable reason codes are SCREAMING_SNAKE.
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]

#: Within-proposal reference. Two-letter kind prefix keeps cross-reference
#: errors readable: ev_ evidence, cl_ claim, cm_ commitment, bm_ belief
#: mutation, cf_ conflict hint, tg_ trigger, pc_ prospective cue, un_
#: uncertainty, ij_ injection observation.
LocalId = Annotated[str, StringConstraints(pattern=r"^(ev|cl|cm|bm|cf|tg|pc|un|ij)_[0-9a-z]{1,24}$")]

#: Content block reference inside one artifact.
BlockId = Annotated[str, StringConstraints(pattern=r"^blk_[0-9a-z]{1,32}$")]

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", to_lower=True)]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

IdempotencyKey = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
]

Revision = Annotated[int, Field(ge=0, le=2**62)]


# ---------------------------------------------------------------------------
# L9 — immutability policy
# ---------------------------------------------------------------------------


class Contract(BaseModel):
    """Base for every Provenance value object.

    `extra="forbid"` is load-bearing, not tidiness. It is how an agent is
    prevented from smuggling an `authority_score`, a `sql`, or a
    `_bypass_review` field into a proposal: an unknown key is a hard
    validation error, not a silently ignored one.

    `frozen=True` means a validated contract cannot be edited in place after
    a check has passed. Collections are declared as tuples for the same
    reason. Models carrying a JSON payload are frozen but not hashable;
    use `content_hash()` when a digest is needed.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        ser_json_bytes="base64",
        arbitrary_types_allowed=False,
    )


class BoundaryContract(Contract):
    """L1. Anything that crosses a process, queue, or service boundary."""

    schema_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        major = value.split(".", 1)[0]
        if major not in SUPPORTED_SCHEMA_MAJORS:
            raise ValueError(
                f"unsupported schema major {major!r} for {cls.__name__}; "
                f"this build understands {sorted(SUPPORTED_SCHEMA_MAJORS)}"
            )
        return value


# ---------------------------------------------------------------------------
# L2 — money
# ---------------------------------------------------------------------------


class Money(Contract):
    """An exact monetary amount with an explicit currency.

    Wire form is a JSON **string**: `{"amount": "186.00", "currency": "USD"}`.
    `json_schema_extra` forces `"type": "string"` into the generated JSON
    Schema, which is what the Tier E structured-output call is constrained
    by, so the model is told to emit a string rather than a number.

    Rejects: float, bool, non-finite, more than four decimal places. There is
    no rounding here on purpose -- silently rounding an obligation is exactly
    the failure mode this product exists to prevent.
    """

    amount: Decimal = Field(
        json_schema_extra={"type": "string", "pattern": r"^-?\d{1,16}(\.\d{1,4})?$"}
    )
    currency: CurrencyCode

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float_amount(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("bool is not a monetary amount")
        if isinstance(value, float):
            raise ValueError(
                "float is not an acceptable monetary amount; send a JSON string "
                'such as "186.00" so no binary rounding error can enter an obligation'
            )
        if isinstance(value, (int, str)):
            try:
                value = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"not a decimal amount: {value!r}") from exc
        return value

    @field_validator("amount")
    @classmethod
    def _validate_scale(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("monetary amount must be finite")
        if -value.as_tuple().exponent > 4:
            raise ValueError(
                f"{value} has more than 4 decimal places; DECIMAL(20,4) is the "
                "canonical storage scale and rounding must happen at the source"
            )
        if abs(value) >= Decimal("1e16"):
            raise ValueError(f"{value} exceeds DECIMAL(20,4) precision")
        return value

    def same_currency_as(self, other: "Money") -> bool:
        return self.currency == other.currency

    def require_same_currency(self, other: "Money") -> None:
        if not self.same_currency_as(other):
            raise ValueError(
                f"currency mismatch {self.currency} vs {other.currency}; "
                "the Kernel refuses cross-currency arithmetic without an "
                "explicit conversion event"
            )

    def __add__(self, other: "Money") -> "Money":
        self.require_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self.require_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount}"


# ---------------------------------------------------------------------------
# Canonical serialisation and hashing
# ---------------------------------------------------------------------------


def canonical_json(
    model: BaseModel, *, exclude: frozenset[str] | None = None
) -> bytes:
    """Deterministic bytes for hashing and signature comparison.

    Sorted keys, no whitespace, `None` omitted, Decimals as strings, datetimes
    as ISO-8601 with an explicit offset. Two structurally equal contracts
    produce identical bytes on any machine and any Python build.
    """
    payload = model.model_dump(mode="json", exclude_none=True, exclude=exclude)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def content_hash(model: BaseModel, *, exclude: frozenset[str] | None = None) -> str:
    """Lowercase hex SHA-256 over `canonical_json(model)`."""
    return hashlib.sha256(canonical_json(model, exclude=exclude)).hexdigest()
```

### 6.1 Why money is a string on the wire

`Decimal` is the storage type, but JSON has only one number type and every JSON parser in the path may reinterpret it as a binary float. `Money._reject_float_amount` catches the in-process case; `json_schema_extra={"type": "string"}` catches the model-output case by constraining Bedrock structured output; the API layer's generated OpenAPI schema catches the client case. All three layers are needed because none of them alone covers all three producers.

### 6.2 `JsonValue` payloads

`pydantic.JsonValue` is used for `object_value`, `value_json`, and `DomainEvent.payload`. It is a recursive union of JSON primitives, lists, and dicts. It cannot hold a Python object, a callable, or a `bytes`, so no contract can smuggle an executable payload through a JSON field.

---

## 7. `provenance_contracts/identity.py` — Principal and InternalPrincipal

Two principal types exist because two different things authenticate. A human authenticates as themselves through `provenance-web`. A workload authenticates as *itself* through client credentials and then borrows a *server-issued* capability. Contract law L10: a machine client never asserts its own `user_id`.

```python
"""Request-scoped authenticated identity.

Neither type ever carries a raw JWT, a client secret, or a database
credential. The control plane converts a validated token into one of these
once per request and passes the object -- never the token -- into business
modules.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IanaTimezone,
    UtcDatetime,
    utc_now,
)
from provenance_domain.enums import OAuthScope, PrincipalType, WorkloadKind

__all__ = [
    "Principal",
    "InternalPrincipal",
    "CapabilityBinding",
    "AuthorizationError",
    "COGNITO_APP_CLIENTS",
]

COGNITO_APP_CLIENTS: frozenset[str] = frozenset(
    {"provenance-web", "provenance-agent-runtime", "provenance-workers"}
)

CognitoSub = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class AuthorizationError(PermissionError):
    """Raised by `require_scope` / `assert_owns`. The API layer maps this to
    HTTP 403 with a reason code and never leaks the failing predicate to the
    client body.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class Principal(BoundaryContract):
    """An authenticated human, resolved from a Cognito token.

    `tenant_id` and `user_id` are resolved server-side from `cognito_sub`
    via the users table; they are never read from a token claim, because a
    custom claim is attacker-influencable in a way a database lookup is not.
    """

    principal_type: Literal[PrincipalType.HUMAN] = PrincipalType.HUMAN
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    cognito_sub: CognitoSub
    app_client: Literal["provenance-web"] = "provenance-web"
    email: Annotated[str, StringConstraints(max_length=320)] | None = None
    display_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    timezone: IanaTimezone = "UTC"
    scopes: frozenset[OAuthScope] = frozenset()
    token_issued_at: UtcDatetime
    token_expires_at: UtcDatetime
    request_id: uuid.UUID
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _validate_token_window(self) -> "Principal":
        if self.token_expires_at <= self.token_issued_at:
            raise ValueError("token_expires_at must be after token_issued_at")
        return self

    def has_scope(self, scope: OAuthScope) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: OAuthScope) -> None:
        if not self.has_scope(scope):
            raise AuthorizationError("MISSING_SCOPE", f"principal lacks {scope}")

    def assert_owns(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Row-level ownership check. Every repository read and write in the
        control plane calls this before touching a user-owned aggregate.
        """
        if tenant_id != self.tenant_id or user_id != self.user_id:
            raise AuthorizationError(
                "CROSS_TENANT_ACCESS", "principal does not own the requested aggregate"
            )

    def is_expired(self, *, now: UtcDatetime | None = None) -> bool:
        return (now or utc_now()) >= self.token_expires_at


class CapabilityBinding(Contract):
    """The server-side record that grants a workload access to exactly one
    user's data for exactly one unit of work.

    This is the object referenced in `04_API_EVENTS_SECURITY.md` section 2.2:
    the Agent Runtime presents an `agent_run_id`, the backend loads the run
    record, and the tenant/user come from that record. A stolen or buggy
    workload token cannot name another user's UUID and be believed, because
    the UUID it names is never consulted.
    """

    binding_id: uuid.UUID
    binding_kind: Literal["AGENT_RUN", "ACTION_INTENT", "TRIGGER_EVALUATION", "INGEST_JOB"]
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    allowed_case_ids: tuple[uuid.UUID, ...] = ()
    expires_at: UtcDatetime
    status: Literal["ACTIVE", "CONSUMED", "REVOKED", "EXPIRED"]

    @model_validator(mode="after")
    def _cap_allowed_cases(self) -> "CapabilityBinding":
        if len(self.allowed_case_ids) > 16:
            raise ValueError(
                "a single binding may not span more than 16 cases; "
                "narrow the unit of work instead of widening the capability"
            )
        return self

    def permits_case(self, case_id: uuid.UUID) -> bool:
        if self.case_id is not None and case_id == self.case_id:
            return True
        return case_id in self.allowed_case_ids


class InternalPrincipal(BoundaryContract):
    """An authenticated workload (agent runtime or Lambda worker).

    Deliberately has NO free `tenant_id` / `user_id`. Ownership is available
    only through `require_binding()`, which fails closed when the caller
    presented no server-resolved capability. This is contract law L10 made
    unenforceable-by-accident: there is no field to populate incorrectly.
    """

    principal_type: Literal[PrincipalType.WORKLOAD] = PrincipalType.WORKLOAD
    app_client: Literal["provenance-agent-runtime", "provenance-workers"]
    workload: WorkloadKind
    scopes: frozenset[OAuthScope]
    binding: CapabilityBinding | None = None
    agent_run_id: uuid.UUID | None = None
    token_issued_at: UtcDatetime
    token_expires_at: UtcDatetime
    request_id: uuid.UUID
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _validate_client_scopes(self) -> "InternalPrincipal":
        if self.token_expires_at <= self.token_issued_at:
            raise ValueError("token_expires_at must be after token_issued_at")
        allowed = _ALLOWED_SCOPES_BY_CLIENT[self.app_client]
        excess = self.scopes - allowed
        if excess:
            raise ValueError(
                f"{self.app_client} presented scopes it may never hold: "
                f"{sorted(str(s) for s in excess)}"
            )
        if not self.scopes:
            raise ValueError("a workload principal with no scope cannot do anything")
        return self

    def has_scope(self, scope: OAuthScope) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: OAuthScope) -> None:
        if not self.has_scope(scope):
            raise AuthorizationError("MISSING_SCOPE", f"workload lacks {scope}")

    def require_binding(self, *, now: UtcDatetime | None = None) -> CapabilityBinding:
        """Return the capability, or fail closed.

        Every internal endpoint that touches user-owned state calls this and
        uses the returned tenant/user. No endpoint reads a user id from the
        request body.
        """
        binding = self.binding
        if binding is None:
            raise AuthorizationError(
                "NO_CAPABILITY_BINDING",
                "workload presented no server-resolved capability; "
                "a machine client may not name a user itself",
            )
        if binding.status != "ACTIVE":
            raise AuthorizationError(
                "BINDING_NOT_ACTIVE", f"capability binding is {binding.status}"
            )
        if (now or utc_now()) >= binding.expires_at:
            raise AuthorizationError("BINDING_EXPIRED", "capability binding has expired")
        return binding


_ALLOWED_SCOPES_BY_CLIENT: dict[str, frozenset[OAuthScope]] = {
    "provenance-agent-runtime": frozenset(
        {
            OAuthScope.MEMORY_READ,
            OAuthScope.MEMORY_PROPOSE,
            OAuthScope.ACTION_PROPOSE,
        }
    ),
    "provenance-workers": frozenset(
        {
            OAuthScope.INGEST_WRITE,
            OAuthScope.TRIGGER_EVALUATE,
            OAuthScope.ACTION_EXECUTE,
            OAuthScope.OUTBOX_DISPATCH,
        }
    ),
}
```

Note what the agent runtime's scope set does **not** contain: `provenance.action/execute`. The graph that drafts a dispute cannot be the process that sends it, and that is enforced at the Cognito app-client boundary as well as in the state machine.

---

## 8. `provenance_contracts/ingestion.py` — artifacts, content, extraction

### 8.1 Artifact metadata and content blocks

```python
"""Inputs to the Ingestion/Interpretation graph.

Nothing in this module is trusted. `ContentBlock.trust_class` is a
`Literal[TrustClass.UNTRUSTED]` so that a prompt builder which forgets to
put a block in the UNTRUSTED EVIDENCE section is a type error rather than a
prompt-injection incident.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BlockId,
    BoundaryContract,
    Confidence,
    Contract,
    LocalId,
    Money,
    ReasonCode,
    SafeIdentifier,
    Sha256Hex,
    UtcDatetime,
)
from provenance_domain.enums import (
    ActorType,
    AmountRole,
    ArtifactSourceType,
    ClaimKind,
    CommitmentType,
    ContentBlockKind,
    DateGranularity,
    DateRole,
    EvidenceType,
    ExternalIdentifierKind,
    Modality,
    ModelTier,
    ParserStatus,
    SourceClass,
    SubjectType,
    TriggerType,
    TrustClass,
    ValueType,
)

__all__ = [
    "ContentLocator",
    "SourceLocator",
    "ArtifactMetadata",
    "ContentBlock",
    "NormalizedContent",
    "CounterpartyHint",
    "ExternalIdentifier",
    "DateMention",
    "AmountMention",
    "EvidenceCandidate",
    "ClaimCandidate",
    "CommitmentCandidate",
    "ProspectiveCue",
    "InjectionObservation",
    "Uncertainty",
    "ExtractionResult",
    "EXTRACTION_SCHEMA_VERSION",
]

EXTRACTION_SCHEMA_VERSION: str = "1.0"

FreeText = Annotated[str, StringConstraints(min_length=1, max_length=8000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ContentLocator(Contract):
    """Where the raw bytes live. Control-plane internal only."""

    scheme: Literal["s3"] = "s3"
    bucket: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{2,62}$")]
    key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    version_id: Annotated[str, StringConstraints(max_length=1024)] | None = None


class SourceLocator(Contract):
    """Exactly where inside an artifact an observation came from.

    Every admitted evidence item carries one. "No candidate without
    provenance may be admitted" is enforced by making this field required on
    `EvidenceCandidate`, not by asking the model nicely.
    """

    kind: Literal["EMAIL_PART", "PDF_PAGE", "IMAGE_REGION", "TABLE_CELL", "TEXT_SPAN"]
    block_id: BlockId
    mime_part: Annotated[str, StringConstraints(max_length=128)] | None = None
    page: Annotated[int, Field(ge=1, le=10_000)] | None = None
    bbox: tuple[float, float, float, float] | None = None
    char_start: Annotated[int, Field(ge=0)] | None = None
    char_end: Annotated[int, Field(ge=0)] | None = None
    row: Annotated[int, Field(ge=0)] | None = None
    column: Annotated[int, Field(ge=0)] | None = None
    attachment_name: Annotated[str, StringConstraints(max_length=255)] | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "SourceLocator":
        if self.char_start is not None and self.char_end is not None:
            if self.char_end <= self.char_start:
                raise ValueError("char_end must be greater than char_start")
        required: dict[str, tuple[str, ...]] = {
            "PDF_PAGE": ("page",),
            "IMAGE_REGION": ("bbox",),
            "TABLE_CELL": ("row", "column"),
            "TEXT_SPAN": ("char_start", "char_end"),
        }
        for field_name in required.get(self.kind, ()):
            if getattr(self, field_name) is None:
                raise ValueError(f"{self.kind} locator requires {field_name}")
        return self


class ArtifactMetadata(BoundaryContract):
    """Safe metadata for one immutable source artifact.

    Carries no document text. The Interpreter's `load_artifact_metadata`
    node receives this; `load_normalized_content` separately returns blocks.
    Splitting them keeps a metadata-only path (dedupe checks, timeline
    rendering) that never materialises document content.
    """

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    source_type: ArtifactSourceType
    mime_type: Annotated[str, StringConstraints(pattern=r"^[\w.+-]+/[\w.+-]+$", max_length=255)]
    content_sha256: Sha256Hex
    size_bytes: Annotated[int, Field(gt=0, le=20 * 1024 * 1024)]
    content_locator: ContentLocator | None = None
    source_message_id: Annotated[str, StringConstraints(max_length=998)] | None = None
    sender: Annotated[str, StringConstraints(max_length=320)] | None = None
    recipient: Annotated[str, StringConstraints(max_length=320)] | None = None
    subject: Annotated[str, StringConstraints(max_length=2000)] | None = None
    received_at: UtcDatetime
    event_time: UtcDatetime | None = None
    parser_status: ParserStatus
    parser_version: Annotated[str, StringConstraints(max_length=64)] | None = None
    block_count: Annotated[int, Field(ge=0, le=10_000)] = 0
    is_duplicate_of: uuid.UUID | None = None

    def redacted_for_agent(self) -> "ArtifactMetadata":
        """The agent-facing projection: no bucket, no key, no version id.

        The agent fetches content through `get_artifact_content(artifact_id)`,
        which re-authorises against the run binding. Handing it an S3
        locator would create a second, unaudited read path.
        """
        return self.model_copy(update={"content_locator": None})


class ContentBlock(Contract):
    """One parser-produced unit of artifact text.

    `kind=QUOTED_HISTORY` is the mechanism behind the Interpreter rule
    "distinguish quoted history from new message content". A promise found
    only inside a QUOTED_HISTORY block is not a new promise, and
    `ClaimCandidate.modality` must then be `QUOTED_HISTORICAL`.
    """

    block_id: BlockId
    artifact_id: uuid.UUID
    ordinal: Annotated[int, Field(ge=0, le=10_000)]
    kind: ContentBlockKind
    text: Annotated[str, StringConstraints(max_length=100_000)]
    content_sha256: Sha256Hex
    source_locator: SourceLocator
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None
    trust_class: Literal[TrustClass.UNTRUSTED] = TrustClass.UNTRUSTED

    @model_validator(mode="after")
    def _locator_matches_block(self) -> "ContentBlock":
        if self.source_locator.block_id != self.block_id:
            raise ValueError(
                f"source_locator.block_id {self.source_locator.block_id!r} "
                f"does not match block_id {self.block_id!r}"
            )
        return self

    @property
    def is_quoted_history(self) -> bool:
        return self.kind is ContentBlockKind.QUOTED_HISTORY


class NormalizedContent(BoundaryContract):
    """The output of `load_normalized_content`. Bounded on purpose."""

    artifact_id: uuid.UUID
    parser_version: Annotated[str, StringConstraints(max_length=64)]
    blocks: tuple[ContentBlock, ...] = Field(max_length=500)
    truncated: bool = False

    @model_validator(mode="after")
    def _unique_ordered_blocks(self) -> "NormalizedContent":
        ids = [b.block_id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate block_id in normalized content")
        ordinals = [b.ordinal for b in self.blocks]
        if ordinals != sorted(ordinals):
            raise ValueError("blocks must be supplied in ordinal order")
        for block in self.blocks:
            if block.artifact_id != self.artifact_id:
                raise ValueError(
                    f"block {block.block_id} belongs to a different artifact"
                )
        return self
```

### 8.2 `ExtractionResult` — the Tier E output contract

This is the JSON Schema that constrains the `anthropic.claude-haiku-4-5` structured-output call. Its cross-reference validators are `validate_extraction_schema` (`03_AGENTS_LANGGRAPH_CONTRACTS.md` §5.4) expressed as types: every candidate cites a block that was actually supplied, every claim cites an evidence candidate that exists, and every local id is unique.

```python
class CounterpartyHint(Contract):
    raw_name: ShortText
    normalized_name: ShortText
    domain: Annotated[str, StringConstraints(max_length=253)] | None = None
    block_id: BlockId
    confidence: Confidence


class ExternalIdentifier(Contract):
    kind: ExternalIdentifierKind
    value: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    block_id: BlockId
    confidence: Confidence


class DateMention(Contract):
    raw_text: ShortText
    normalized: UtcDatetime | None = None
    granularity: DateGranularity
    role: DateRole
    block_id: BlockId
    confidence: Confidence

    @model_validator(mode="after")
    def _unknown_granularity_has_no_value(self) -> "DateMention":
        # Bitemporal rule T2: when evidence has no trustworthy effective date,
        # say unknown rather than inventing one.
        if self.granularity is DateGranularity.UNKNOWN and self.normalized is not None:
            raise ValueError(
                "granularity=UNKNOWN must not carry a normalized timestamp; "
                "an invented date is worse than an absent one"
            )
        return self


class AmountMention(Contract):
    raw_text: ShortText
    money: Money
    role: AmountRole
    block_id: BlockId
    confidence: Confidence


class EvidenceCandidate(Contract):
    """A proposed immutable observation. Admission means "this text was
    present", never "this statement is true".
    """

    local_id: LocalId
    evidence_type: EvidenceType
    exact_text: FreeText
    normalized_text: FreeText
    block_id: BlockId
    source_locator: SourceLocator
    source_class: SourceClass
    quoted: bool = False
    modality: Modality
    observed_at: UtcDatetime
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "EvidenceCandidate":
        if not self.local_id.startswith("ev_"):
            raise ValueError("EvidenceCandidate.local_id must use the ev_ prefix")
        if self.source_locator.block_id != self.block_id:
            raise ValueError("source_locator must point at the cited block")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError(
                "validity interval is half-open [valid_from, valid_to); "
                "valid_to must be strictly after valid_from"
            )
        return self


class ClaimCandidate(Contract):
    """A source actor's assertion. Never canonical by itself."""

    local_id: LocalId
    claim_kind: ClaimKind
    subject_type: SubjectType
    subject_hint: ShortText
    predicate: SafeIdentifier
    object_type: ValueType
    object_value: JsonValue
    actor_type: ActorType
    actor_hint: ShortText | None = None
    evidence_local_id: LocalId
    quoted: bool = False
    modality: Modality
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "ClaimCandidate":
        if not self.local_id.startswith("cl_"):
            raise ValueError("ClaimCandidate.local_id must use the cl_ prefix")
        if not self.evidence_local_id.startswith("ev_"):
            raise ValueError("evidence_local_id must reference an ev_ candidate")
        return self


class CommitmentCandidate(Contract):
    """A proposed obligation. Modality is load-bearing: an obligation may not
    be inferred from user desire, from a hypothetical, or from quoted
    history alone.
    """

    local_id: LocalId
    commitment_type: CommitmentType
    description: FreeText
    obligor_type: ActorType
    obligor_hint: ShortText | None = None
    beneficiary_type: ActorType
    beneficiary_hint: ShortText | None = None
    money: Money | None = None
    due_at: UtcDatetime | None = None
    due_condition_text: ShortText | None = None
    source_claim_local_id: LocalId
    quoted: bool = False
    modality: Modality
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "CommitmentCandidate":
        if not self.local_id.startswith("cm_"):
            raise ValueError("CommitmentCandidate.local_id must use the cm_ prefix")
        if self.quoted or self.modality in (
            Modality.HYPOTHETICAL,
            Modality.QUOTED_HISTORICAL,
        ):
            raise ValueError(
                f"quoted={self.quoted}, modality={self.modality} cannot create a commitment; "
                "extract it as a claim and let the Kernel decide"
            )
        if self.money is not None and self.money.amount < 0:
            raise ValueError("a committed amount may not be negative")
        return self


class ProspectiveCue(Contract):
    """Text implying a future check: 'within 30 days of inspection'.

    In the hero scenario this is what eventually arms the landlord deposit
    trigger that fires on its own four months later.
    """

    local_id: LocalId
    cue_text: ShortText
    suggested_trigger_type: TriggerType
    block_id: BlockId
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    relative_to_claim_local_id: LocalId | None = None
    quoted: bool = False
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "ProspectiveCue":
        if not self.local_id.startswith("pc_"):
            raise ValueError("ProspectiveCue.local_id must use the pc_ prefix")
        return self


class InjectionObservation(Contract):
    """An instruction-like span observed in untrusted content, never obeyed."""

    local_id: LocalId
    block_id: BlockId
    classification: Literal[
        "INSTRUCTION_OVERRIDE",
        "TOOL_CALL_IMITATION",
        "SYSTEM_IMPERSONATION",
        "FENCE_BREAKOUT",
        "AUTHORITY_SPOOF",
        "IDENTIFIER_INJECTION",
        "OTHER",
    ]
    excerpt: ShortText
    action_taken: Literal["TREATED_AS_DATA"] = "TREATED_AS_DATA"

    @model_validator(mode="after")
    def _validate(self) -> "InjectionObservation":
        if not self.local_id.startswith("ij_"):
            raise ValueError("InjectionObservation.local_id must use the ij_ prefix")
        return self


class Uncertainty(Contract):
    """An explicit statement of ambiguity. The Interpreter is instructed to
    state ambiguity rather than force a value; this is where that goes.
    """

    local_id: LocalId
    code: ReasonCode
    description: FreeText
    affects_local_ids: tuple[LocalId, ...] = ()
    blocks_state_change: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "Uncertainty":
        if not self.local_id.startswith("un_"):
            raise ValueError("Uncertainty.local_id must use the un_ prefix")
        return self


class ExtractionResult(BoundaryContract):
    """Complete Tier E output for one artifact.

    Cross-reference validation happens here rather than in a node function,
    so a schema-valid-but-referentially-broken extraction cannot reach the
    proposal builder at all. A failure raises `ValidationError`, the graph
    takes its single repair attempt, and a second failure routes to
    FAIL_SAFE with the evidence left pending.
    """

    extraction_schema_version: str = EXTRACTION_SCHEMA_VERSION
    artifact_id: uuid.UUID
    agent_run_id: uuid.UUID
    trace_id: uuid.UUID
    source_block_ids: tuple[BlockId, ...] = Field(min_length=1, max_length=500)

    artifact_summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    counterparty_hints: tuple[CounterpartyHint, ...] = Field(default=(), max_length=10)
    external_identifiers: tuple[ExternalIdentifier, ...] = Field(default=(), max_length=40)
    dates: tuple[DateMention, ...] = Field(default=(), max_length=60)
    amounts: tuple[AmountMention, ...] = Field(default=(), max_length=60)
    evidence_candidates: tuple[EvidenceCandidate, ...] = Field(default=(), max_length=60)
    claim_candidates: tuple[ClaimCandidate, ...] = Field(default=(), max_length=60)
    commitment_candidates: tuple[CommitmentCandidate, ...] = Field(default=(), max_length=20)
    prospective_cues: tuple[ProspectiveCue, ...] = Field(default=(), max_length=20)
    injection_observations: tuple[InjectionObservation, ...] = Field(default=(), max_length=20)
    uncertainties: tuple[Uncertainty, ...] = Field(default=(), max_length=30)
    needs_visual_reasoning: bool = False

    model_id: Annotated[str, StringConstraints(max_length=128)]
    model_tier: Literal[ModelTier.E, ModelTier.R]
    prompt_version: Annotated[str, StringConstraints(max_length=32)]
    repaired: bool = False

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "ExtractionResult":
        known_blocks = set(self.source_block_ids)

        # 1. every cited block was actually supplied to the model
        cited: list[tuple[str, str]] = []
        for group in (
            self.counterparty_hints,
            self.external_identifiers,
            self.dates,
            self.amounts,
            self.evidence_candidates,
            self.prospective_cues,
            self.injection_observations,
        ):
            for item in group:
                cited.append((type(item).__name__, item.block_id))
        unknown = {(k, b) for k, b in cited if b not in known_blocks}
        if unknown:
            raise ValueError(
                "candidates cite block ids that were not supplied: "
                f"{sorted(unknown)}; a hallucinated locator is unadmittable provenance"
            )

        # 2. local ids are globally unique inside the result
        all_local_ids = [
            item.local_id
            for group in (
                self.evidence_candidates,
                self.claim_candidates,
                self.commitment_candidates,
                self.prospective_cues,
                self.injection_observations,
                self.uncertainties,
            )
            for item in group
        ]
        if len(set(all_local_ids)) != len(all_local_ids):
            raise ValueError("duplicate local_id in extraction result")

        # 3. claims resolve to a declared evidence candidate
        evidence_ids = {e.local_id for e in self.evidence_candidates}
        dangling_claims = [
            c.local_id for c in self.claim_candidates if c.evidence_local_id not in evidence_ids
        ]
        if dangling_claims:
            raise ValueError(
                f"claim candidates {dangling_claims} cite unknown evidence; "
                "no candidate without provenance may be admitted"
            )

        # 4. commitments resolve to a declared claim candidate
        claim_ids = {c.local_id for c in self.claim_candidates}
        dangling_commitments = [
            m.local_id
            for m in self.commitment_candidates
            if m.source_claim_local_id not in claim_ids
        ]
        if dangling_commitments:
            raise ValueError(
                f"commitment candidates {dangling_commitments} cite an unknown claim"
            )

        # 5. uncertainties and cues reference real local ids
        universe = set(all_local_ids)
        for unc in self.uncertainties:
            missing = set(unc.affects_local_ids) - universe
            if missing:
                raise ValueError(
                    f"uncertainty {unc.local_id} references unknown ids {sorted(missing)}"
                )
        for cue in self.prospective_cues:
            if (
                cue.relative_to_claim_local_id is not None
                and cue.relative_to_claim_local_id not in claim_ids
            ):
                raise ValueError(
                    f"cue {cue.local_id} anchors to an unknown claim candidate"
                )
        return self

    @property
    def blocks_state_change(self) -> bool:
        """True when at least one uncertainty is severe enough that the
        Kernel must not mutate canonical state from this extraction.
        """
        return any(u.blocks_state_change for u in self.uncertainties)
```

The `ClaimCandidate` model has **no** `authority_score` field, and `extra="forbid"` means one cannot be added by a model at runtime. The extractor may recommend a `source_class`; `provenance_domain.authority.authority_for()` turns that into a number. This is the difference between "the model told us how much to trust it" and "we decided how much to trust that kind of source".

---

## 9. `provenance_contracts/retrieval.py` — IdentityCandidate and RetrievalContext

Two of the three canonical additions land in this module. Addition B makes the CockroachDB MCP tool calls first-class contract data so the Memory Trace can render them. Addition C makes the retraction filter structurally unskippable.

```python
"""Deterministic retrieval output. Bounded by construction.

'Never send all history to the model' is enforced with `Field(max_length=)`
rather than a comment: three relationship candidates, three case candidates,
ten evidence snippets. If a caller wants more, it must change this file and
justify it in review.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    Money,
    ReasonCode,
    SafeIdentifier,
    UtcDatetime,
)
from provenance_contracts.ingestion import SourceLocator
from provenance_domain.enums import (
    AgentSafeView,
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EvidenceType,
    IdentityCandidateKind,
    RelationshipStatus,
    RetractionStatus,
    SubjectType,
)

__all__ = [
    "MatchSignal",
    "IdentityCandidate",
    "EvidenceSnippet",
    "CanonicalBeliefSummary",
    "ActiveConflictSummary",
    "ActiveCommitmentSummary",
    "TemporalFact",
    "McpToolCall",
    "VectorSearchParams",
    "RetrievalDebug",
    "RetrievalContext",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_VERSION",
]

#: Frozen. One embedding version is active for the primary vector index.
EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS: int = 1024
EMBEDDING_VERSION: str = "v1"

Snippet = Annotated[str, StringConstraints(min_length=1, max_length=1200)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class MatchSignal(Contract):
    """One deterministic reason a candidate scored the way it did.

    Signals are computed by relational validation (02 section 15.3), never by
    a model. They are what makes an identity score explainable in the Memory
    Trace instead of being an unexplained number.
    """

    signal: Literal[
        "EXACT_EXTERNAL_REFERENCE",
        "SENDER_DOMAIN_MATCH",
        "THREAD_ID_MATCH",
        "SERVICE_ADDRESS_MATCH",
        "AMOUNT_CONSISTENT",
        "TEMPORAL_OVERLAP",
        "RELATIONSHIP_ACTIVE",
        "CASE_RECENTLY_ACTIVE",
        "USER_CONFIRMED_MAPPING",
        "VECTOR_SIMILARITY",
        "COUNTERPARTY_NAME_SIMILARITY",
    ]
    matched: bool
    weight: Confidence
    detail: Annotated[str, StringConstraints(max_length=300)] | None = None


class IdentityCandidate(BoundaryContract):
    """A relationship or case this artifact might belong to.

    `score` is deterministic: it is a weighted sum of `signals`, not a model
    opinion. `route_resolution_need` compares it against the configured
    thresholds in `provenance_domain.invariants` (0.90 top-1, 0.15 margin)
    to decide whether the Tier R resolver runs at all.
    """

    candidate_kind: IdentityCandidateKind
    candidate_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    label: Label
    counterparty_name: Label | None = None
    relationship_status: RelationshipStatus | None = None
    case_status: CaseStatus | None = None
    last_activity_at: UtcDatetime | None = None
    score: Confidence
    signals: tuple[MatchSignal, ...] = Field(default=(), max_length=16)
    reasons: tuple[Annotated[str, StringConstraints(max_length=300)], ...] = Field(
        default=(), max_length=8
    )

    @model_validator(mode="after")
    def _kind_matches_status(self) -> "IdentityCandidate":
        if self.candidate_kind is IdentityCandidateKind.CASE and self.case_status is None:
            raise ValueError("a CASE candidate must carry case_status")
        if (
            self.candidate_kind is IdentityCandidateKind.RELATIONSHIP
            and self.relationship_status is None
        ):
            raise ValueError("a RELATIONSHIP candidate must carry relationship_status")
        return self


class EvidenceSnippet(Contract):
    """A retrieved evidence item, already filtered for retraction.

    Addition C. A retracted or superseded evidence item keeps its row and its
    embedding in the vector index -- that is what makes lineage auditable --
    so ANN search will happily return it. `retraction_status` is therefore
    pinned to ACTIVE by a validator: if a retrieval path forgets its
    `WHERE retraction_status = 'ACTIVE'` predicate, the resulting context
    fails validation before it ever reaches a prompt, rather than quietly
    resurfacing a correction the user already made.
    """

    evidence_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_type: EvidenceType
    normalized_text: Snippet
    source_locator: SourceLocator
    observed_at: UtcDatetime
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    source_authority: Confidence | None = None
    retraction_status: Literal[RetractionStatus.ACTIVE] = RetractionStatus.ACTIVE
    similarity: Confidence | None = None
    retrieved_by: tuple[Literal["VECTOR", "EXACT_MATCH", "GRAPH_EXPANSION"], ...] = ()


class CanonicalBeliefSummary(Contract):
    """What Provenance currently holds. Trusted context, not evidence."""

    belief_id: uuid.UUID
    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: SafeIdentifier
    value_summary: Snippet
    epistemic_status: EpistemicStatus
    belief_confidence: Confidence
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    support_edge_count: Annotated[int, Field(ge=0)]


class ActiveConflictSummary(Contract):
    conflict_id: uuid.UUID
    case_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    predicate: SafeIdentifier
    summary: Snippet
    requires_human: bool
    detected_at: UtcDatetime


class ActiveCommitmentSummary(Contract):
    commitment_id: uuid.UUID
    case_id: uuid.UUID
    description: Snippet
    status: CommitmentStatus
    committed: Money | None = None
    fulfilled: Money | None = None
    outstanding: Money | None = None
    due_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _currencies_agree(self) -> "ActiveCommitmentSummary":
        present = [m for m in (self.committed, self.fulfilled, self.outstanding) if m]
        if present and len({m.currency for m in present}) > 1:
            raise ValueError(
                "committed/fulfilled/outstanding must share one currency; "
                "cross-currency aggregation requires an explicit conversion event"
            )
        return self


class TemporalFact(Contract):
    """A dated anchor the resolver needs in order to reason about overlap."""

    label: Label
    predicate: SafeIdentifier
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    recorded_at: UtcDatetime
    source_evidence_id: uuid.UUID | None = None


class McpToolCall(Contract):
    """Addition B. One CockroachDB MCP read, surfaced rather than hidden.

    The MCP server is read-only and SQL grants are the real permission
    boundary: the connection authenticates as `pv_agent_reader`, which holds
    SELECT on the agent-safe views only. Recording the view name, the row
    count, and the role in the contract means the Memory Trace can show a
    judge exactly which governed surface the agent touched, and the
    `db_role` literal means a trace claiming a write role fails validation.

    `arguments_digest` rather than raw arguments: query parameters can echo
    document text, and raw document contents never enter logs or traces.
    """

    server: Literal["cockroachdb-mcp"] = "cockroachdb-mcp"
    tool_name: Annotated[str, StringConstraints(max_length=64)]
    view: AgentSafeView
    db_role: Literal["pv_agent_reader"] = "pv_agent_reader"
    arguments_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    row_count: Annotated[int, Field(ge=0, le=10_000)]
    latency_ms: Annotated[int, Field(ge=0, le=600_000)]
    started_at: UtcDatetime
    truncated: bool = False


class VectorSearchParams(Contract):
    """The exact ANN parameters used, frozen into the trace.

    `user_prefix_applied` is a `Literal[True]`: the vector index is defined
    as `(user_id, embedding vector_cosine_ops)` so ANN search cannot cross
    users, and a retrieval result that claims otherwise cannot be
    constructed.
    """

    model_id: Literal["amazon.titan-embed-text-v2:0"] = EMBEDDING_MODEL_ID
    dimensions: Literal[1024] = EMBEDDING_DIMENSIONS
    embedding_version: Annotated[str, StringConstraints(max_length=64)] = EMBEDDING_VERSION
    distance: Literal["cosine"] = "cosine"
    top_k: Annotated[int, Field(ge=1, le=200)] = 20
    rerank_to: Annotated[int, Field(ge=1, le=50)] = 10
    beam_size: Annotated[int, Field(ge=1, le=512)] | None = None
    user_prefix_applied: Literal[True] = True
    retraction_filter_applied: Literal[True] = True


class RetrievalDebug(Contract):
    """Everything a judge needs to believe the retrieval step."""

    deterministic_hints: tuple[Annotated[str, StringConstraints(max_length=200)], ...] = (
        Field(default=(), max_length=30)
    )
    vector_search: VectorSearchParams | None = None
    mcp_tool_calls: tuple[McpToolCall, ...] = Field(default=(), max_length=20)
    candidates_considered: Annotated[int, Field(ge=0)] = 0
    candidates_filtered_by_retraction: Annotated[int, Field(ge=0)] = 0
    elapsed_ms: Annotated[int, Field(ge=0)] = 0


class RetrievalContext(BoundaryContract):
    """The bounded memory package handed to the Interpreter and the resolver.

    Caps come straight from 02 section 15.5 and are enforced here so no node
    can widen them at call time.
    """

    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    artifact_id: uuid.UUID | None = None

    relationship_candidates: tuple[IdentityCandidate, ...] = Field(default=(), max_length=3)
    case_candidates: tuple[IdentityCandidate, ...] = Field(default=(), max_length=3)
    canonical_beliefs: tuple[CanonicalBeliefSummary, ...] = Field(default=(), max_length=25)
    evidence_snippets: tuple[EvidenceSnippet, ...] = Field(default=(), max_length=10)
    active_conflicts: tuple[ActiveConflictSummary, ...] = Field(default=(), max_length=10)
    active_commitments: tuple[ActiveCommitmentSummary, ...] = Field(default=(), max_length=15)
    temporal_facts: tuple[TemporalFact, ...] = Field(default=(), max_length=20)
    unresolved_identity_questions: tuple[
        Annotated[str, StringConstraints(max_length=300)], ...
    ] = Field(default=(), max_length=8)

    debug: RetrievalDebug = RetrievalDebug()
    retrieved_at: UtcDatetime

    @model_validator(mode="after")
    def _validate_scope_and_kinds(self) -> "RetrievalContext":
        for cand in self.relationship_candidates:
            if cand.candidate_kind is not IdentityCandidateKind.RELATIONSHIP:
                raise ValueError("relationship_candidates must hold RELATIONSHIP kinds")
        for cand in self.case_candidates:
            if cand.candidate_kind is not IdentityCandidateKind.CASE:
                raise ValueError("case_candidates must hold CASE kinds")
        foreign = [
            str(c.candidate_id)
            for c in (*self.relationship_candidates, *self.case_candidates)
            if c.user_id != self.user_id or c.tenant_id != self.tenant_id
        ]
        if foreign:
            raise ValueError(
                f"candidates {foreign} belong to another user; "
                "retrieval is scoped by user prefix and must never cross that line"
            )
        return self

    def top_case_candidate(self) -> IdentityCandidate | None:
        return self.case_candidates[0] if self.case_candidates else None

    def identity_margin(self) -> Confidence | None:
        """Difference between the top two case candidates.

        `route_resolution_need` invokes the Tier R resolver when this is
        below the configured 0.15 margin, or when the top score is below
        0.90. Both thresholds are configuration, never prompt text.
        """
        if len(self.case_candidates) < 2:
            return None
        return self.case_candidates[0].score - self.case_candidates[1].score
```

### 9.1 Why the retraction guard is a type and not a query comment

Addition C is a real failure mode, not a hypothetical: the vector index is intentionally append-only alongside the evidence table, so a superseded invoice line remains a perfectly good ANN neighbour forever. Three defences are stacked here — `EvidenceSnippet.retraction_status` is `Literal[ACTIVE]`, `VectorSearchParams.retraction_filter_applied` is `Literal[True]`, and `RetrievalDebug.candidates_filtered_by_retraction` records how many were dropped so the number can be asserted in an evaluation. The first two make the mistake unrepresentable; the third makes it observable when someone changes the query and the count silently goes to zero.

---

## 10. `provenance_contracts/resolution.py` — ResolutionAssessment

```python
"""Tier R (anthropic.claude-opus-5) advisory output.

Everything here is advisory. `advisory: Literal[True]` is a field, not a
docstring, so a downstream consumer that treats an assessment as canonical
has to visibly strip a flag that says it is not.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    LocalId,
    ReasonCode,
    SafeIdentifier,
    UtcDatetime,
)
from provenance_domain.enums import (
    DateGranularity,
    ModelTier,
    SemanticRelation,
    SourceClass,
    SupportSourceKind,
)
from provenance_domain.invariants import HUMAN_REVIEW_CONFIDENCE_FLOOR

__all__ = [
    "ResolvedIdentity",
    "SemanticRelationAssertion",
    "TemporalInterpretation",
    "ProposedSupersession",
    "ModelAttribution",
    "ResolutionAssessment",
]

Reason = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class ModelAttribution(BoundaryContract):
    """Which model produced an artifact of reasoning, under which prompt.

    Stored on every agent run so a demo or evaluation regression is
    explainable: graph name, graph version, prompt version, model id,
    extraction schema version.
    """

    provider: Literal["bedrock"] = "bedrock"
    model_id: Annotated[str, StringConstraints(max_length=128)]
    tier: ModelTier
    prompt_version: Annotated[str, StringConstraints(max_length=32)]
    graph_name: Annotated[str, StringConstraints(max_length=64)]
    graph_version: Annotated[str, StringConstraints(max_length=32)]
    extraction_schema_version: Annotated[str, StringConstraints(max_length=16)] | None = None

    @model_validator(mode="after")
    def _tier_matches_model(self) -> "ModelAttribution":
        expected = {
            ModelTier.E: "anthropic.claude-haiku-4-5",
            ModelTier.R: "anthropic.claude-opus-5",
            ModelTier.EMBEDDING: "amazon.titan-embed-text-v2:0",
        }[self.tier]
        if self.model_id != expected:
            raise ValueError(
                f"tier {self.tier} is frozen to {expected!r}, got {self.model_id!r}; "
                "model routing is deterministic from task class, and there is no "
                "meta-agent that chooses models"
            )
        return self


class ResolvedIdentity(Contract):
    relationship_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    confidence: Confidence
    reasons: tuple[Reason, ...] = Field(default=(), max_length=8)


class SemanticRelationAssertion(Contract):
    """"Does this evidence support, contradict, or qualify that belief?"

    `UNRELATED` is expressible so the resolver can say "no" explicitly; the
    proposal builder drops UNRELATED rows rather than persisting them, since
    `belief_support.relation` has no such value.
    """

    source_kind: SupportSourceKind
    source_id: uuid.UUID | None = None
    source_local_id: LocalId | None = None
    target_kind: Literal["BELIEF_VERSION", "CLAIM", "COMMITMENT"]
    target_id: uuid.UUID
    relation: SemanticRelation
    confidence: Confidence
    rationale: Reason

    @model_validator(mode="after")
    def _exactly_one_source_ref(self) -> "SemanticRelationAssertion":
        if (self.source_id is None) == (self.source_local_id is None):
            raise ValueError(
                "provide exactly one of source_id (already persisted) or "
                "source_local_id (proposed in this run)"
            )
        return self


class TemporalInterpretation(Contract):
    """A reading of when something was true, separate from when we learned it."""

    target_kind: Literal["CLAIM", "COMMITMENT", "BELIEF"]
    target_id: uuid.UUID | None = None
    target_local_id: LocalId | None = None
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    granularity: DateGranularity
    basis: Reason
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "TemporalInterpretation":
        if (self.target_id is None) == (self.target_local_id is None):
            raise ValueError("provide exactly one of target_id or target_local_id")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("intervals are half-open [valid_from, valid_to)")
        return self


class ProposedSupersession(Contract):
    """Bitemporal rule T3: a prior version's `valid_to` may close at T only
    if source authority supports supersession. The resolver names the
    authority basis; the Kernel decides whether it is sufficient.
    """

    target_kind: Literal["BELIEF", "COMMITMENT", "POLICY_TERM"]
    target_id: uuid.UUID
    superseding_source_kind: SupportSourceKind
    superseding_source_local_id: LocalId | None = None
    superseding_source_id: uuid.UUID | None = None
    effective_at: UtcDatetime
    authority_basis: SourceClass
    reason_code: ReasonCode
    confidence: Confidence


class ResolutionAssessment(BoundaryContract):
    """Advisory output of the `strong_resolution` node.

    Contains no database mutations, no SQL, and no entitlement conclusions.
    It says what relates to what and when things were true; the Kernel
    decides what that means for canonical state.
    """

    advisory: Literal[True] = True
    trace_id: uuid.UUID
    agent_run_id: uuid.UUID

    identity: ResolvedIdentity
    semantic_relations: tuple[SemanticRelationAssertion, ...] = Field(default=(), max_length=40)
    proposed_temporal_interpretations: tuple[TemporalInterpretation, ...] = Field(
        default=(), max_length=20
    )
    proposed_supersessions: tuple[ProposedSupersession, ...] = Field(default=(), max_length=10)
    unresolved_questions: tuple[Reason, ...] = Field(default=(), max_length=10)
    requires_human_review: bool
    rationale_summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    model: ModelAttribution

    @model_validator(mode="after")
    def _low_confidence_must_escalate(self) -> "ResolutionAssessment":
        """Abstain rather than commit wrongly on ambiguous cases.

        If the resolver bound an identity below the human-review floor, or
        left an unresolved question open, or proposed closing out a prior
        version, it must have set `requires_human_review`. This turns the
        identity evaluation gate ("abstain/pending rather than wrong commit")
        into a validation error instead of a metric someone reads later.
        """
        if self.model.tier is not ModelTier.R:
            raise ValueError("ResolutionAssessment must come from Tier R")
        must_escalate = (
            (self.identity.case_id is not None
             and self.identity.confidence < HUMAN_REVIEW_CONFIDENCE_FLOOR)
            or bool(self.unresolved_questions)
            or any(
                s.confidence < HUMAN_REVIEW_CONFIDENCE_FLOOR
                for s in self.proposed_supersessions
            )
        )
        if must_escalate and not self.requires_human_review:
            raise ValueError(
                "assessment binds an identity or supersession below the "
                f"{HUMAN_REVIEW_CONFIDENCE_FLOOR} human-review floor, or leaves a "
                "question open, but did not set requires_human_review"
            )
        return self

    def support_edges(self) -> tuple[SemanticRelationAssertion, ...]:
        """Only the relations that can become persisted grounding edges."""
        return tuple(
            r for r in self.semantic_relations if r.relation is not SemanticRelation.UNRELATED
        )
```

---

## 11. `provenance_contracts/predicates.py` — the safe trigger AST

Trigger predicates are data, never code. The grammar is closed, the field paths are allowlisted by prefix, and depth is bounded so a malicious or malformed proposal cannot build an evaluator bomb.

```python
"""The only executable-looking thing Provenance persists, and it is not
executable. `PredicateNode` is a closed algebraic term evaluated by
deterministic Python against a whitelisted projection.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import Contract
from provenance_domain.enums import PredicateOp

__all__ = ["PredicateNode", "FieldPath", "WHITELISTED_FIELD_ROOTS", "MAX_PREDICATE_DEPTH"]

MAX_PREDICATE_DEPTH: Final[int] = 8

#: Roots of the CaseProjection / CommitmentProjection exposed to predicates.
WHITELISTED_FIELD_ROOTS: Final[frozenset[str]] = frozenset(
    {"case", "commitments", "conflicts", "triggers", "clock"}
)

FieldPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^(case|commitments|conflicts|triggers|clock)(\.[a-z0-9_]+){1,5}$",
        max_length=160,
    ),
]

_ARITY: Final[dict[PredicateOp, tuple[int, int]]] = {
    PredicateOp.AND: (2, 8),
    PredicateOp.OR: (2, 8),
    PredicateOp.NOT: (1, 1),
    PredicateOp.EQ: (2, 2),
    PredicateOp.NE: (2, 2),
    PredicateOp.GT: (2, 2),
    PredicateOp.GTE: (2, 2),
    PredicateOp.LT: (2, 2),
    PredicateOp.LTE: (2, 2),
    PredicateOp.IS_NULL: (1, 1),
    PredicateOp.NOT_NULL: (1, 1),
    PredicateOp.FIELD: (0, 0),
    PredicateOp.CONST: (0, 0),
}

_LEAVES: Final[frozenset[PredicateOp]] = frozenset({PredicateOp.FIELD, PredicateOp.CONST})


class PredicateNode(Contract):
    """One node of a trigger predicate.

    FIELD carries `path`; CONST carries `value`; every other operator carries
    `args`. Mixing them is a validation error, so there is no node that is
    simultaneously a leaf and a branch.
    """

    op: PredicateOp
    path: FieldPath | None = None
    value: JsonValue | None = None
    args: tuple["PredicateNode", ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def _validate_shape(self) -> "PredicateNode":
        low, high = _ARITY[self.op]
        if not (low <= len(self.args) <= high):
            raise ValueError(
                f"{self.op} takes between {low} and {high} arguments, got {len(self.args)}"
            )
        if self.op is PredicateOp.FIELD:
            if self.path is None:
                raise ValueError("FIELD requires a path")
            if self.value is not None:
                raise ValueError("FIELD must not carry a value")
            root = self.path.split(".", 1)[0]
            if root not in WHITELISTED_FIELD_ROOTS:
                raise ValueError(f"field root {root!r} is not whitelisted")
        elif self.op is PredicateOp.CONST:
            if self.path is not None:
                raise ValueError("CONST must not carry a path")
        else:
            if self.path is not None or self.value is not None:
                raise ValueError(f"{self.op} is a branch node and takes only args")
        if self.op not in _LEAVES and self.depth() > MAX_PREDICATE_DEPTH:
            raise ValueError(
                f"predicate depth {self.depth()} exceeds {MAX_PREDICATE_DEPTH}; "
                "a trigger condition that deep is a modelling error"
            )
        return self

    def depth(self) -> int:
        if not self.args:
            return 1
        return 1 + max(child.depth() for child in self.args)

    def field_paths(self) -> frozenset[str]:
        if self.op is PredicateOp.FIELD and self.path is not None:
            return frozenset({self.path})
        return frozenset().union(*(c.field_paths() for c in self.args)) if self.args else frozenset()


PredicateNode.model_rebuild()
```

The landlord deposit trigger from the hero scenario, written in this grammar:

```python
DEPOSIT_OVERDUE = PredicateNode(
    op=PredicateOp.AND,
    args=(
        PredicateNode(
            op=PredicateOp.GT,
            args=(
                PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.outstanding_amount"),
                PredicateNode(op=PredicateOp.CONST, value="0"),
            ),
        ),
        PredicateNode(
            op=PredicateOp.GTE,
            args=(
                PredicateNode(op=PredicateOp.FIELD, path="clock.now"),
                PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.due_at"),
            ),
        ),
    ),
)
```

That is the entire mechanism behind the second reveal: no user set a reminder, and no model decided to remember. A predicate was armed at commit time and the scheduler evaluated it.

---

## 12. `provenance_contracts/proposal.py` — MemoryProposal

This is the only shape an LLM agent may submit toward canonical state, and the Kernel is the only writer that acts on it. Note what is absent: no table names, no SQL, no `tenant_id`, no authority score, no permission fields.

### 12.1 Proposal sub-models

```python
"""The typed MemoryProposal and its parts.

Kernel rule: LLM agents propose typed MemoryProposals; the deterministic
Memory Kernel is the only canonical writer. No agent gets SQL write access,
ever. This module is where that boundary is given a shape.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    IdempotencyKey,
    LocalId,
    Money,
    ReasonCode,
    SafeIdentifier,
    UtcDatetime,
    Weight,
)
from provenance_contracts.predicates import PredicateNode
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.derivations import is_registered_derivation
from provenance_domain.enums import (
    ActorType,
    BeliefMutationKind,
    CaseStatus,
    ClaimKind,
    CommitmentType,
    ConflictSeverity,
    ConflictType,
    EpistemicStatus,
    Modality,
    ProposalType,
    SourceClass,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    TriggerMutationKind,
    TriggerType,
    ValueType,
)

__all__ = [
    "ProposalIdentity",
    "ProposedClaim",
    "ProposedCommitment",
    "DeterministicDerivation",
    "ProposedSupportEdge",
    "ProposedBeliefMutation",
    "ConflictHint",
    "ProposedTrigger",
    "MemoryProposal",
]

Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ProposalIdentity(Contract):
    """Which relationship and case the agent believes this belongs to.

    A proposal may legitimately resolve to nothing: `PENDING_IDENTITY` is a
    better outcome than a confident write to the wrong case.
    """

    relationship_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    confidence: Confidence
    unresolved_candidates: tuple[uuid.UUID, ...] = Field(default=(), max_length=6)
    resolved_by: Literal["DETERMINISTIC", "TIER_R_RESOLVER", "USER_CONFIRMED"] = "DETERMINISTIC"


class ProposedClaim(Contract):
    """An assertion to be recorded as a claim. Never as a fact.

    In the hero scenario the June invoice becomes exactly this: a
    `COUNTERPARTY_CLAIM` with `predicate="billing_period_covered"`. It is
    admitted, it is preserved, and it does not overwrite anything.
    """

    local_id: LocalId
    claim_kind: ClaimKind
    subject_type: SubjectType
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier
    object_type: ValueType
    object_value: JsonValue
    actor_type: ActorType
    actor_ref: ShortText | None = None
    evidence_id: uuid.UUID
    source_class: SourceClass
    modality: Modality
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "ProposedClaim":
        if not self.local_id.startswith("cl_"):
            raise ValueError("ProposedClaim.local_id must use the cl_ prefix")
        if (self.subject_id is None) == (self.subject_local_ref is None):
            raise ValueError("provide exactly one of subject_id or subject_local_ref")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("intervals are half-open [valid_from, valid_to)")
        return self


class ProposedCommitment(Contract):
    """An obligation to record. Amounts are Money; there is no float path."""

    local_id: LocalId
    commitment_type: CommitmentType
    description: Text
    obligor_type: ActorType
    obligor_ref: ShortText | None = None
    beneficiary_type: ActorType
    beneficiary_ref: ShortText | None = None
    committed: Money | None = None
    due_at: UtcDatetime | None = None
    due_condition: PredicateNode | None = None
    source_claim_local_id: LocalId | None = None
    source_claim_id: uuid.UUID | None = None
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "ProposedCommitment":
        if not self.local_id.startswith("cm_"):
            raise ValueError("ProposedCommitment.local_id must use the cm_ prefix")
        if (self.source_claim_local_id is None) == (self.source_claim_id is None):
            raise ValueError(
                "a commitment must originate from exactly one claim "
                "(local to this proposal, or already persisted)"
            )
        if self.committed is not None and self.committed.amount < 0:
            raise ValueError("a committed amount may not be negative")
        if self.due_at is None and self.due_condition is None:
            # Legal: an open-ended obligation. Recorded explicitly so it is a
            # decision rather than an omission.
            pass
        return self


class DeterministicDerivation(Contract):
    """The only lawful excuse for a belief version with no grounding edge.

    The name and version must appear in `provenance_domain.derivations`.
    An agent cannot invent a derivation to bypass grounding, because the
    registry is closed and lives outside the contract.
    """

    name: SafeIdentifier
    function_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    input_refs: tuple[uuid.UUID, ...] = Field(default=(), max_length=20)
    input_local_refs: tuple[LocalId, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _must_be_registered(self) -> "DeterministicDerivation":
        if not is_registered_derivation(self.name, self.function_version):
            raise ValueError(
                f"{self.name}@{self.function_version} is not a registered "
                "deterministic derivation; ungrounded belief versions are only "
                "permitted for derivations declared in provenance_domain"
            )
        if not self.input_refs and not self.input_local_refs:
            raise ValueError("a derivation must name at least one input")
        return self


class ProposedSupportEdge(Contract):
    """One grounding edge: belief version <- evidence / claim / belief version.

    This becomes a `belief_support` row unchanged. The table name is stable
    per the frozen canon; the vocabulary word for the edge is "grounding".
    """

    source_kind: SupportSourceKind
    source_id: uuid.UUID | None = None
    source_local_id: LocalId | None = None
    relation: SupportRelation
    weight: Weight | None = None
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ProposedSupportEdge":
        if (self.source_id is None) == (self.source_local_id is None):
            raise ValueError("provide exactly one of source_id or source_local_id")
        if self.source_kind is SupportSourceKind.DERIVATION:
            raise ValueError(
                "a DERIVATION is expressed through ProposedBeliefMutation.derivation, "
                "not as a support edge"
            )
        return self


class ProposedBeliefMutation(Contract):
    """Create, revise, supersede, or retract a belief.

    The grounding invariant is enforced in `_require_grounding` below: a
    canonical belief version must have at least one SUPPORTS edge unless it
    names a registered deterministic derivation. There is no third option.
    """

    local_id: LocalId
    mutation_kind: BeliefMutationKind

    belief_id: uuid.UUID | None = None
    subject_type: SubjectType | None = None
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier | None = None

    value_type: ValueType | None = None
    value_json: JsonValue | None = None
    epistemic_status: EpistemicStatus
    belief_confidence: Confidence
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    supersedes_version_id: uuid.UUID | None = None
    reason_code: ReasonCode | None = None

    grounding: tuple[ProposedSupportEdge, ...] = Field(default=(), max_length=20)
    derivation: DeterministicDerivation | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProposedBeliefMutation":
        if not self.local_id.startswith("bm_"):
            raise ValueError("ProposedBeliefMutation.local_id must use the bm_ prefix")

        if self.mutation_kind is BeliefMutationKind.CREATE:
            missing = [
                name
                for name in ("subject_type", "predicate", "value_type")
                if getattr(self, name) is None
            ]
            if missing or (self.subject_id is None and self.subject_local_ref is None):
                raise ValueError(
                    "CREATE requires subject_type, a subject reference, predicate "
                    f"and value_type (missing: {missing})"
                )
            if self.belief_id is not None:
                raise ValueError("CREATE must not name an existing belief_id")
        else:
            if self.belief_id is None:
                raise ValueError(f"{self.mutation_kind} requires belief_id")

        if self.mutation_kind in (
            BeliefMutationKind.REVISE,
            BeliefMutationKind.SUPERSEDE,
        ):
            if self.supersedes_version_id is None:
                raise ValueError(
                    f"{self.mutation_kind} must name the version it replaces so the "
                    "lineage chain stays unbroken"
                )
            if self.value_type is None:
                raise ValueError(f"{self.mutation_kind} requires value_type")

        if self.mutation_kind is BeliefMutationKind.RETRACT:
            if self.epistemic_status is not EpistemicStatus.RETRACTED:
                raise ValueError("RETRACT must set epistemic_status=RETRACTED")
            if self.reason_code is None:
                raise ValueError("a retraction without a reason code is unauditable")

        if (
            self.epistemic_status
            in (EpistemicStatus.SUPERSEDED, EpistemicStatus.RETRACTED)
            and self.reason_code is None
        ):
            raise ValueError(
                f"epistemic_status={self.epistemic_status} requires a reason_code"
            )

        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("intervals are half-open [valid_from, valid_to)")
        return self

    @model_validator(mode="after")
    def _require_grounding(self) -> "ProposedBeliefMutation":
        """THE grounding invariant.

        A retraction is exempt: it removes a belief from canonical standing
        rather than asserting one, and it carries a reason code instead.
        Everything else must either cite evidence or be a registered
        deterministic derivation.
        """
        if self.mutation_kind is BeliefMutationKind.RETRACT:
            return self
        if self.derivation is not None:
            if self.grounding:
                # Belt and braces is fine, but a derivation plus contradicting
                # edges is incoherent: pick one story.
                if any(e.relation is SupportRelation.CONTRADICTS for e in self.grounding):
                    raise ValueError(
                        "a deterministic derivation cannot simultaneously be "
                        "contradicted by its own grounding"
                    )
            return self
        supports = [e for e in self.grounding if e.relation is SupportRelation.SUPPORTS]
        if not supports:
            raise ValueError(
                f"belief mutation {self.local_id} is UNGROUNDED: a canonical belief "
                "version needs at least one SUPPORTS edge, or a registered "
                "deterministic derivation. Beliefs are revisable, but they are "
                "never free-floating."
            )
        return self


class ConflictHint(Contract):
    """An advisory 'these two cannot both be true'.

    The Kernel runs its own deterministic contradiction detection and does
    not depend on this. The hint improves reason codes and ordering; it does
    not create a conflict by itself.
    """

    local_id: LocalId
    advisory: Literal[True] = True
    conflict_type: ConflictType
    subject_type: SubjectType
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier
    left_source_kind: SupportSourceKind
    left_source_id: uuid.UUID | None = None
    left_source_local_id: LocalId | None = None
    right_source_kind: SupportSourceKind
    right_source_id: uuid.UUID | None = None
    right_source_local_id: LocalId | None = None
    severity: ConflictSeverity
    requires_human_hint: bool = False
    rationale: Text
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> "ConflictHint":
        if not self.local_id.startswith("cf_"):
            raise ValueError("ConflictHint.local_id must use the cf_ prefix")
        for side in ("left", "right"):
            has_id = getattr(self, f"{side}_source_id") is not None
            has_local = getattr(self, f"{side}_source_local_id") is not None
            if has_id == has_local:
                raise ValueError(
                    f"{side} side needs exactly one of source_id or source_local_id"
                )
        if (
            self.left_source_id is not None
            and self.left_source_id == self.right_source_id
        ):
            raise ValueError("a source cannot conflict with itself")
        return self


class ProposedTrigger(Contract):
    """Arm, re-arm, disarm, or extend prospective memory."""

    local_id: LocalId
    mutation_kind: TriggerMutationKind
    trigger_id: uuid.UUID | None = None
    trigger_type: TriggerType
    predicate: PredicateNode | None = None
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    rationale: Text

    @model_validator(mode="after")
    def _validate(self) -> "ProposedTrigger":
        if not self.local_id.startswith("tg_"):
            raise ValueError("ProposedTrigger.local_id must use the tg_ prefix")
        if self.mutation_kind is TriggerMutationKind.ARM:
            if self.predicate is None:
                raise ValueError("ARM requires a predicate")
            if self.trigger_id is not None:
                raise ValueError("ARM creates a trigger and must not name one")
        else:
            if self.trigger_id is None:
                raise ValueError(f"{self.mutation_kind} requires trigger_id")
        if (
            self.not_before is not None
            and self.expires_at is not None
            and self.expires_at <= self.not_before
        ):
            raise ValueError("expires_at must be after not_before")
        return self
```

### 12.2 `MemoryProposal`

```python
class MemoryProposal(BoundaryContract):
    """The complete typed proposal an agent submits to the Memory Kernel.

    Absent by design:
      * `tenant_id` -- the Kernel derives tenancy from the authenticated
        internal principal's capability binding (pipeline step 2). A field
        the agent could fill in is a field an attacker could fill in.
      * any authority score -- see `provenance_domain.authority`.
      * any SQL, table name, or permission grant.

    `user_id` IS present, and is a cross-check rather than a grant: pipeline
    step 3 rejects the proposal when it disagrees with the binding. A machine
    client asserting a user id it was not issued is a security event, and we
    want it to be loud rather than absent.
    """

    proposal_id: uuid.UUID
    proposal_type: ProposalType
    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    user_id: uuid.UUID

    source_artifact_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10)
    evidence_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=60)

    identity: ProposalIdentity
    claims: tuple[ProposedClaim, ...] = Field(default=(), max_length=60)
    commitments: tuple[ProposedCommitment, ...] = Field(default=(), max_length=20)
    belief_mutations: tuple[ProposedBeliefMutation, ...] = Field(default=(), max_length=40)
    conflict_hints: tuple[ConflictHint, ...] = Field(default=(), max_length=20)
    trigger_mutations: tuple[ProposedTrigger, ...] = Field(default=(), max_length=10)

    requested_case_transition: CaseStatus | None = None
    requested_transition_reason_code: ReasonCode | None = None
    unresolved_questions: tuple[Text, ...] = Field(default=(), max_length=10)
    blocks_state_change: bool = False

    model: ModelAttribution
    idempotency_key: IdempotencyKey
    created_at: UtcDatetime

    # -- structural validation -------------------------------------------

    @model_validator(mode="after")
    def _must_propose_something(self) -> "MemoryProposal":
        if not any(
            (
                self.claims,
                self.commitments,
                self.belief_mutations,
                self.conflict_hints,
                self.trigger_mutations,
            )
        ):
            raise ValueError(
                "an empty proposal is not a proposal; if nothing was learned, "
                "do not submit and let the run end with a visible NOOP status"
            )
        return self

    @model_validator(mode="after")
    def _local_ids_unique(self) -> "MemoryProposal":
        ids = [
            item.local_id
            for group in (
                self.claims,
                self.commitments,
                self.belief_mutations,
                self.conflict_hints,
                self.trigger_mutations,
            )
            for item in group
        ]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate local_id in proposal: {duplicates}")
        return self

    @model_validator(mode="after")
    def _evidence_references_are_declared(self) -> "MemoryProposal":
        """Every persisted evidence id used anywhere must appear in
        `evidence_ids`, so the Kernel can load and ownership-check the whole
        set in one query at pipeline step 4 and reject foreign provenance at
        step 5 before any write is planned.
        """
        declared = set(self.evidence_ids)

        undeclared: set[uuid.UUID] = set()
        for claim in self.claims:
            if claim.evidence_id not in declared:
                undeclared.add(claim.evidence_id)
        for mutation in self.belief_mutations:
            for edge in mutation.grounding:
                if (
                    edge.source_kind is SupportSourceKind.EVIDENCE
                    and edge.source_id is not None
                    and edge.source_id not in declared
                ):
                    undeclared.add(edge.source_id)
        if undeclared:
            raise ValueError(
                "these evidence ids are referenced but not declared in "
                f"evidence_ids: {sorted(str(u) for u in undeclared)}"
            )
        return self

    @model_validator(mode="after")
    def _local_references_resolve(self) -> "MemoryProposal":
        claim_ids = {c.local_id for c in self.claims}
        known = (
            claim_ids
            | {c.local_id for c in self.commitments}
            | {b.local_id for b in self.belief_mutations}
        )

        for commitment in self.commitments:
            ref = commitment.source_claim_local_id
            if ref is not None and ref not in claim_ids:
                raise ValueError(
                    f"commitment {commitment.local_id} cites unknown claim {ref}"
                )
        for mutation in self.belief_mutations:
            for edge in mutation.grounding:
                if edge.source_local_id is not None and edge.source_local_id not in known:
                    raise ValueError(
                        f"belief mutation {mutation.local_id} grounds on unknown "
                        f"local reference {edge.source_local_id}"
                    )
            if mutation.derivation is not None:
                missing = set(mutation.derivation.input_local_refs) - known
                if missing:
                    raise ValueError(
                        f"derivation on {mutation.local_id} names unknown local "
                        f"inputs {sorted(missing)}"
                    )
        for hint in self.conflict_hints:
            for ref in (hint.left_source_local_id, hint.right_source_local_id):
                if ref is not None and ref not in known:
                    raise ValueError(
                        f"conflict hint {hint.local_id} cites unknown local ref {ref}"
                    )
        return self

    @model_validator(mode="after")
    def _transition_requires_reason(self) -> "MemoryProposal":
        if self.requested_case_transition is not None:
            if self.identity.case_id is None:
                raise ValueError(
                    "a case transition may only be requested when identity "
                    "resolves to a case"
                )
            if self.requested_transition_reason_code is None:
                raise ValueError(
                    "requested_case_transition requires a reason code; the Kernel "
                    "checks it against the frozen guard table"
                )
        return self

    @model_validator(mode="after")
    def _blocked_proposals_do_not_mutate(self) -> "MemoryProposal":
        """When extraction flagged a state-blocking uncertainty, the proposal
        may still record claims and evidence -- evidence is append-only and
        always admissible -- but it may not ask to change canonical belief,
        commitments, or case state.
        """
        if self.blocks_state_change and (
            self.belief_mutations or self.commitments or self.requested_case_transition
        ):
            raise ValueError(
                "proposal declares blocks_state_change but requests belief, "
                "commitment or case mutations; record the claims and escalate"
            )
        return self

    # -- convenience ------------------------------------------------------

    def is_state_changing(self) -> bool:
        return bool(
            self.belief_mutations
            or self.commitments
            or self.trigger_mutations
            or self.requested_case_transition
        )

    def ungrounded_mutations(self) -> tuple[ProposedBeliefMutation, ...]:
        """Always empty for a validated proposal. Kept as an explicit
        assertion point for the Kernel's step 16 invariant sweep, so the
        check exists in the Kernel's own code path and does not rely solely
        on validation having run upstream.
        """
        return tuple(
            m
            for m in self.belief_mutations
            if m.derivation is None
            and m.mutation_kind is not BeliefMutationKind.RETRACT
            and not any(e.relation is SupportRelation.SUPPORTS for e in m.grounding)
        )
```

### 12.3 The hero-scenario proposal, in full

This is the payload the Interpreter submits when the June invoice arrives. It admits the counterparty's assertion, grounds nothing new as canonical truth, hints at the contradiction, and asks for the reopen. Every canonical consequence is the Kernel's decision, not the agent's.

```python
june_invoice_proposal = MemoryProposal(
    proposal_id=new_id(),
    proposal_type=ProposalType.INGESTION_INTERPRETATION,
    trace_id=trace_id,
    agent_run_id=run_id,
    user_id=user_id,
    source_artifact_ids=(invoice_artifact_id,),
    evidence_ids=(invoice_line_evidence_id, billing_period_evidence_id),
    identity=ProposalIdentity(
        relationship_id=old_isp_relationship_id,
        case_id=cancellation_case_id,
        confidence=Decimal("0.96"),
        resolved_by="DETERMINISTIC",
    ),
    claims=(
        ProposedClaim(
            local_id="cl_billing",
            claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
            subject_type=SubjectType.RELATIONSHIP,
            subject_id=old_isp_relationship_id,
            predicate="billing_period_covered",
            object_type=ValueType.INTERVAL,
            object_value={"from": "2026-06-01T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
            actor_type=ActorType.COUNTERPARTY,
            actor_ref="billing@example-isp.test",
            evidence_id=billing_period_evidence_id,
            source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
            modality=Modality.ASSERTED_PRESENT,
            valid_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
            valid_to=datetime(2026, 7, 1, tzinfo=timezone.utc),
            extraction_confidence=Decimal("0.97"),
        ),
        ProposedClaim(
            local_id="cl_amount",
            claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
            subject_type=SubjectType.RELATIONSHIP,
            subject_id=old_isp_relationship_id,
            predicate="amount_outstanding",
            object_type=ValueType.MONEY,
            object_value={"amount": "186.00", "currency": "USD"},
            actor_type=ActorType.COUNTERPARTY,
            actor_ref="billing@example-isp.test",
            evidence_id=invoice_line_evidence_id,
            source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
            modality=Modality.ASSERTED_PRESENT,
            extraction_confidence=Decimal("0.99"),
        ),
    ),
    conflict_hints=(
        ConflictHint(
            local_id="cf_service",
            conflict_type=ConflictType.VALUE_CONFLICT,
            subject_type=SubjectType.RELATIONSHIP,
            subject_id=old_isp_relationship_id,
            predicate="service_terminated",
            left_source_kind=SupportSourceKind.BELIEF_VERSION,
            left_source_id=service_terminated_v1_id,
            right_source_kind=SupportSourceKind.CLAIM,
            right_source_local_id="cl_billing",
            severity=ConflictSeverity.HIGH,
            requires_human_hint=False,
            rationale=(
                "Canonical belief holds service terminated 31 May 2026, grounded in "
                "the 15 May cancellation confirmation. The invoice asserts service "
                "was delivered 1-30 June. The intervals overlap and the values are "
                "mutually exclusive."
            ),
            confidence=Decimal("0.94"),
        ),
    ),
    requested_case_transition=CaseStatus.REOPENED,
    requested_transition_reason_code="COUNTERPARTY_CLAIM_AFTER_CLOSE",
    model=ModelAttribution(
        model_id="anthropic.claude-haiku-4-5",
        tier=ModelTier.E,
        prompt_version="interpreter-1.3",
        graph_name="ingestion_graph",
        graph_version="1.2.0",
        extraction_schema_version="1.0",
    ),
    idempotency_key=f"proposal:{invoice_artifact_id}:interpreter-1.3",
    created_at=utc_now(),
)
```

Note that the proposal contains **no** `belief_mutations`. The agent does not decide that service was or was not terminated. It records what the counterparty said and flags the incompatibility; the Kernel decides whether the canonical belief changes, stays, or becomes `DISPUTED`.

---

## 13. `provenance_contracts/kernel.py` — KernelCommitResult

```python
"""What the deterministic Memory Kernel returns after one decision.

The result is a receipt, not a summary: every id it names is a row that now
exists, and the revision arithmetic is checked here so a caller cannot be
handed a receipt that describes an impossible commit.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    Money,
    ReasonCode,
    Revision,
    SafeIdentifier,
    UtcDatetime,
)
from provenance_domain.enums import (
    ACCEPTING_KERNEL_DECISIONS,
    DECISION_TO_PROPOSAL_STATUS,
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    KernelDecision,
    ProposalStatus,
    TransitionType,
    TriggerState,
)

__all__ = [
    "BeliefVersionRef",
    "ConflictRef",
    "CommitmentChange",
    "TriggerChange",
    "StateTransitionRef",
    "KernelCommitResult",
]


class BeliefVersionRef(Contract):
    belief_id: uuid.UUID
    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    predicate: SafeIdentifier
    epistemic_status: EpistemicStatus
    supersedes_version_id: uuid.UUID | None = None
    grounding_edge_count: Annotated[int, Field(ge=0)]
    is_derived: bool = False

    @model_validator(mode="after")
    def _committed_versions_are_grounded(self) -> "BeliefVersionRef":
        """The read-side half of the grounding invariant. A committed version
        that is neither derived nor retracted must have left at least one
        support edge behind, or the commit that produced it was wrong.
        """
        if (
            not self.is_derived
            and self.epistemic_status is not EpistemicStatus.RETRACTED
            and self.grounding_edge_count < 1
        ):
            raise ValueError(
                f"belief version {self.belief_version_id} was committed with no "
                "grounding edge and no derivation"
            )
        if self.version_no > 1 and self.supersedes_version_id is None:
            raise ValueError(
                f"version {self.version_no} must name the version it supersedes; "
                "lineage may not have a gap"
            )
        return self


class ConflictRef(Contract):
    conflict_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    predicate: SafeIdentifier
    requires_human: bool
    created: bool
    canonical_belief_version_id: uuid.UUID | None = None
    resolution_reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _resolved_conflicts_explain_themselves(self) -> "ConflictRef":
        if (
            self.status in (ConflictStatus.AUTO_RESOLVED, ConflictStatus.RESOLVED)
            and self.resolution_reason_code is None
        ):
            raise ValueError(
                f"conflict {self.conflict_id} is {self.status} without a reason code; "
                "two high-authority sources are never silently reconciled"
            )
        return self


class CommitmentChange(Contract):
    commitment_id: uuid.UUID
    status_before: CommitmentStatus | None = None
    status_after: CommitmentStatus
    committed: Money | None = None
    fulfilled_before: Money | None = None
    fulfilled_after: Money | None = None
    outstanding_after: Money | None = None
    fulfillment_ids: tuple[uuid.UUID, ...] = ()
    created: bool = False

    @model_validator(mode="after")
    def _arithmetic_holds(self) -> "CommitmentChange":
        """`outstanding = committed - fulfilled`, verified on the receipt.

        This is the $420 / $200 / $220 case from the hero scenario checked
        one more time on the way out of the Kernel.
        """
        if self.committed is None or self.outstanding_after is None:
            return self
        fulfilled = self.fulfilled_after
        if fulfilled is None:
            return self
        self.committed.require_same_currency(fulfilled)
        self.committed.require_same_currency(self.outstanding_after)
        expected = self.committed.amount - fulfilled.amount
        if self.outstanding_after.amount != expected:
            raise ValueError(
                f"outstanding {self.outstanding_after.amount} != "
                f"{self.committed.amount} - {fulfilled.amount}"
            )
        if (
            self.status_after is CommitmentStatus.FULFILLED
            and self.outstanding_after.amount > 0
        ):
            raise ValueError("FULFILLED with a positive outstanding amount is impossible")
        return self


class TriggerChange(Contract):
    trigger_id: uuid.UUID
    state_before: TriggerState | None = None
    state_after: TriggerState
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    schedule_name: Annotated[str, StringConstraints(max_length=64)] | None = None
    basis_case_revision: Revision
    created: bool = False


class StateTransitionRef(Contract):
    state_transition_id: uuid.UUID
    transition_type: TransitionType
    case_revision: Revision
    from_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    to_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    reason_code: ReasonCode
    recorded_at: UtcDatetime


class KernelCommitResult(BoundaryContract):
    """The outcome of one Memory Kernel decision.

    Invariant 3 in receipt form. `_validate_revision_arithmetic` refuses to
    describe a commit where canonical objects changed but the case revision
    did not move, or where nothing changed but it did.
    """

    decision: KernelDecision
    proposal_id: uuid.UUID
    kernel_decision_id: uuid.UUID
    proposal_status: ProposalStatus
    trace_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID

    case_id: uuid.UUID | None = None
    case_status_after: CaseStatus | None = None
    case_revision_before: Revision | None = None
    case_revision_after: Revision | None = None
    attention_level_after: AttentionLevel | None = None

    created_claim_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=60)
    created_belief_versions: tuple[BeliefVersionRef, ...] = Field(default=(), max_length=40)
    created_or_updated_conflicts: tuple[ConflictRef, ...] = Field(default=(), max_length=20)
    commitment_changes: tuple[CommitmentChange, ...] = Field(default=(), max_length=20)
    trigger_changes: tuple[TriggerChange, ...] = Field(default=(), max_length=10)
    state_transitions: tuple[StateTransitionRef, ...] = Field(default=(), max_length=30)
    outbox_event_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=30)

    attention_required: bool = False
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), max_length=20)
    retry_count: Annotated[int, Field(ge=0, le=10)] = 0
    committed_at: UtcDatetime | None = None

    # -- validation -------------------------------------------------------

    @model_validator(mode="after")
    def _status_matches_decision(self) -> "KernelCommitResult":
        expected = DECISION_TO_PROPOSAL_STATUS[self.decision]
        if self.proposal_status is not expected:
            raise ValueError(
                f"decision {self.decision} implies proposal status {expected}, "
                f"got {self.proposal_status}"
            )
        return self

    @model_validator(mode="after")
    def _rejections_are_empty_and_explained(self) -> "KernelCommitResult":
        rejected = self.decision.value.startswith("REJECTED")
        if rejected:
            if not self.reason_codes:
                raise ValueError(
                    f"{self.decision} must carry at least one reason code; "
                    "an unexplained rejection is not auditable"
                )
            wrote_anything = any(
                (
                    self.created_claim_ids,
                    self.created_belief_versions,
                    self.created_or_updated_conflicts,
                    self.commitment_changes,
                    self.trigger_changes,
                    self.state_transitions,
                    self.outbox_event_ids,
                )
            )
            if wrote_anything or self.committed_at is not None:
                raise ValueError(
                    f"{self.decision} claims to have written canonical rows; "
                    "a rejected proposal writes nothing but its own decision row"
                )
        return self

    @model_validator(mode="after")
    def _validate_revision_arithmetic(self) -> "KernelCommitResult":
        changed = bool(
            self.created_claim_ids
            or self.created_belief_versions
            or self.created_or_updated_conflicts
            or self.commitment_changes
            or self.trigger_changes
            or self.state_transitions
        )
        if self.decision not in ACCEPTING_KERNEL_DECISIONS:
            return self
        if self.case_id is None:
            raise ValueError("an accepted decision affecting state must name a case")
        if self.case_revision_before is None or self.case_revision_after is None:
            raise ValueError("an accepted decision must report both case revisions")
        expected = self.case_revision_before + 1 if changed else self.case_revision_before
        if self.case_revision_after != expected:
            raise ValueError(
                f"case revision must go {self.case_revision_before} -> {expected} "
                f"(changed={changed}), got {self.case_revision_after}"
            )
        for transition in self.state_transitions:
            if transition.case_revision != self.case_revision_after:
                raise ValueError(
                    "every state transition written by a commit carries that "
                    f"commit's new revision {self.case_revision_after}, got "
                    f"{transition.case_revision}"
                )
        if self.committed_at is None:
            raise ValueError("an accepted decision must report committed_at")
        return self

    @model_validator(mode="after")
    def _conflicting_accept_has_a_conflict(self) -> "KernelCommitResult":
        if self.decision is KernelDecision.ACCEPTED_WITH_CONFLICT:
            if not self.created_or_updated_conflicts:
                raise ValueError(
                    "ACCEPTED_WITH_CONFLICT without a conflict row is a lie about "
                    "what the user will see"
                )
            if not self.attention_required:
                raise ValueError(
                    "a committed conflict always raises attention; silent "
                    "contradictions are the failure this product exists to prevent"
                )
        return self

    @model_validator(mode="after")
    def _retryable_is_not_a_commit(self) -> "KernelCommitResult":
        if self.decision is KernelDecision.RETRYABLE_CONCURRENCY:
            if self.committed_at is not None or self.outbox_event_ids:
                raise ValueError(
                    "RETRYABLE_CONCURRENCY means the transaction rolled back; it "
                    "cannot report a commit time or outbox rows"
                )
            if self.retry_count < 1:
                raise ValueError("RETRYABLE_CONCURRENCY implies at least one retry")
        return self

    # -- convenience ------------------------------------------------------

    @property
    def is_accepted(self) -> bool:
        return self.decision in ACCEPTING_KERNEL_DECISIONS

    @property
    def should_wake_advocate(self) -> bool:
        """Route to the Advocate graph only for user-impacting commits."""
        return self.is_accepted and self.attention_required
```

The June-invoice commit returns `ACCEPTED_WITH_CONFLICT` with `case_revision_before=7`, `case_revision_after=8`, one `ConflictRef` (`VALUE_CONFLICT`, `OPEN`), one `StateTransitionRef` (`CASE_STATUS`, `RESOLVED -> REOPENED`, reason `COUNTERPARTY_CLAIM_AFTER_CLOSE`, revision 8), two `created_claim_ids`, and one outbox event id for `case.reopened.v1`. All of it in one serializable transaction; any 40001 rolls the whole thing back and re-reads.

---

## 14. `provenance_contracts/proof.py` — StateProof

State Proof is assembled deterministically from committed rows. It renders **both** grounding (why we believe each thing) and lineage (how each belief got here and why it changed). LLM prose is optional presentation layered on top and never replaces the structure.

Addition A lives here too: the same type carries `memory_mode`, and a `MemoryMode.OFF` proof is validated to be empty. That is what makes the Judge Mode counterfactual a single comparable object rather than two ad-hoc renderings.

```python
"""The deterministic explanation read model.

Assembled by the control plane from committed rows only (02 section 14).
Nothing here is generated by a model, and no field accepts model prose.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    Money,
    ReasonCode,
    Revision,
    SafeIdentifier,
    Sha256Hex,
    UtcDatetime,
    Weight,
    content_hash,
)
from provenance_contracts.ingestion import SourceLocator
from provenance_contracts.predicates import PredicateNode
from provenance_domain.enums import (
    AttentionLevel,
    CaseStatus,
    CaseType,
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EvidenceType,
    MemoryMode,
    RetractionStatus,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    TransitionType,
    TriggerState,
    TriggerType,
    ValueType,
)

__all__ = [
    "EvidenceProof",
    "GroundingEdgeProof",
    "LineageEntry",
    "BeliefVersionProof",
    "BeliefProof",
    "ConflictProof",
    "FulfillmentProof",
    "CommitmentProof",
    "TriggerProof",
    "DerivationTrace",
    "CaseSnapshot",
    "StateProof",
    "PROOF_HASH_EXCLUDE",
]

Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]

#: Volatile fields excluded from `proof_hash`, so the same committed state
#: hashes identically no matter when it was rendered.
PROOF_HASH_EXCLUDE: frozenset[str] = frozenset({"proof_id", "generated_at", "proof_hash"})


class EvidenceProof(Contract):
    """The immutable observation behind a grounding edge, with its locator.

    `retraction_status` is a full enum here, unlike `EvidenceSnippet`: a
    State Proof must be able to *show* that a piece of evidence was retracted
    and that the belief it once supported has moved on. Hiding retractions
    from the proof would defeat the purpose of keeping lineage.
    """

    evidence_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_type: EvidenceType
    exact_text: Text | None = None
    normalized_text: Text
    source_locator: SourceLocator
    observed_at: UtcDatetime
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    source_authority: Confidence | None = None
    retraction_status: RetractionStatus = RetractionStatus.ACTIVE
    artifact_received_at: UtcDatetime
    artifact_sender: Annotated[str, StringConstraints(max_length=320)] | None = None


class GroundingEdgeProof(Contract):
    """One `belief_support` edge, rendered."""

    support_id: uuid.UUID
    source_kind: SupportSourceKind
    source_id: uuid.UUID
    relation: SupportRelation
    weight: Weight | None = None
    reason_code: ReasonCode | None = None
    evidence: EvidenceProof | None = None
    claim_summary: Text | None = None

    @model_validator(mode="after")
    def _evidence_edges_carry_evidence(self) -> "GroundingEdgeProof":
        if self.source_kind is SupportSourceKind.EVIDENCE and self.evidence is None:
            raise ValueError(
                "an EVIDENCE grounding edge must render its evidence; a proof that "
                "says 'trust me' is not a proof"
            )
        if self.evidence is not None and self.evidence.evidence_id != self.source_id:
            raise ValueError("rendered evidence does not match the edge source id")
        return self


class BeliefVersionProof(Contract):
    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    value_type: ValueType
    value_json: JsonValue
    epistemic_status: EpistemicStatus
    belief_confidence: Confidence
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    recorded_at: UtcDatetime
    kernel_decision_id: uuid.UUID


class LineageEntry(Contract):
    """One link in the belief_versions chain, with the reason it was replaced.

    Lineage without reasons is a changelog. Lineage with reason codes is an
    argument, which is what the user needs when a counterparty disputes it.
    """

    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    value_json: JsonValue
    epistemic_status: EpistemicStatus
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    recorded_at: UtcDatetime
    superseded_at: UtcDatetime | None = None
    superseded_by_version_id: uuid.UUID | None = None
    supersession_reason_code: ReasonCode | None = None
    kernel_decision_id: uuid.UUID

    @model_validator(mode="after")
    def _supersession_is_explained(self) -> "LineageEntry":
        superseded = self.superseded_by_version_id is not None
        if superseded and self.supersession_reason_code is None:
            raise ValueError(
                f"version {self.version_no} was superseded without a reason code"
            )
        if superseded and self.superseded_at is None:
            raise ValueError("a superseded version must record superseded_at")
        return self


class DerivationTrace(Contract):
    """A deterministic computation shown with its inputs and its output."""

    derivation_name: SafeIdentifier
    function_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    inputs: tuple[tuple[str, JsonValue], ...] = Field(default=(), max_length=20)
    output: JsonValue
    explanation: Text


class BeliefProof(Contract):
    """One proposition, its current version, its grounding, and its lineage.

    This model is the reason the three-term vocabulary exists. `grounding`
    answers "why do you believe this"; `lineage` answers "what did you
    believe before, and what changed your mind".
    """

    belief_id: uuid.UUID
    subject_type: SubjectType
    subject_id: uuid.UUID
    subject_label: Annotated[str, StringConstraints(max_length=200)]
    predicate: SafeIdentifier
    current_version: BeliefVersionProof
    grounding: tuple[GroundingEdgeProof, ...] = Field(default=(), max_length=30)
    lineage: tuple[LineageEntry, ...] = Field(default=(), max_length=30)
    derivation: DerivationTrace | None = None

    @model_validator(mode="after")
    def _require_grounding(self) -> "BeliefProof":
        """The read-side grounding invariant, checked at render time.

        If this ever fires in production it means a canonical belief exists
        that the system cannot justify, which is a data-integrity incident,
        not a rendering bug. Failing loudly here is the point.
        """
        if self.current_version.epistemic_status is EpistemicStatus.RETRACTED:
            return self
        if self.derivation is not None:
            return self
        supports = [e for e in self.grounding if e.relation is SupportRelation.SUPPORTS]
        if not supports:
            raise ValueError(
                f"belief {self.belief_id} ({self.predicate}) is canonical but "
                "UNGROUNDED: no SUPPORTS edge and no derivation"
            )
        return self

    @model_validator(mode="after")
    def _lineage_is_ordered_and_consistent(self) -> "BeliefProof":
        versions = [entry.version_no for entry in self.lineage]
        if versions != sorted(versions):
            raise ValueError("lineage must be ordered oldest-first")
        if len(set(versions)) != len(versions):
            raise ValueError("duplicate version_no in lineage")
        if self.lineage:
            if self.current_version.version_no != max(versions):
                raise ValueError(
                    "current_version must be the newest entry in the lineage chain"
                )
            open_ends = [e for e in self.lineage if e.superseded_by_version_id is None]
            if len(open_ends) != 1:
                raise ValueError(
                    "exactly one lineage entry may be un-superseded (the current one)"
                )
        return self

    @property
    def is_disputed(self) -> bool:
        return self.current_version.epistemic_status is EpistemicStatus.DISPUTED

    @property
    def contradicting_edges(self) -> tuple[GroundingEdgeProof, ...]:
        return tuple(
            e for e in self.grounding if e.relation is SupportRelation.CONTRADICTS
        )


class ConflictProof(Contract):
    conflict_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    severity: ConflictSeverity
    predicate: SafeIdentifier
    requires_human: bool
    left_summary: Text
    left_evidence: EvidenceProof | None = None
    right_summary: Text
    right_evidence: EvidenceProof | None = None
    canonical_belief_version_id: uuid.UUID | None = None
    resolution_reason_code: ReasonCode | None = None
    detected_at: UtcDatetime
    resolved_at: UtcDatetime | None = None


class FulfillmentProof(Contract):
    fulfillment_id: uuid.UUID
    evidence_id: uuid.UUID
    amount: Money | None = None
    fulfilled_at: UtcDatetime
    admission_status: Annotated[str, StringConstraints(max_length=32)]
    confidence: Confidence


class CommitmentProof(Contract):
    commitment_id: uuid.UUID
    description: Text
    status: CommitmentStatus
    committed: Money | None = None
    fulfilled: Money | None = None
    outstanding: Money | None = None
    due_at: UtcDatetime | None = None
    fulfillments: tuple[FulfillmentProof, ...] = Field(default=(), max_length=30)
    outstanding_derivation: DerivationTrace | None = None

    @model_validator(mode="after")
    def _arithmetic_is_shown(self) -> "CommitmentProof":
        if self.committed is None or self.outstanding is None:
            return self
        fulfilled = self.fulfilled
        if fulfilled is None:
            raise ValueError("a monetary commitment proof must show fulfilled")
        self.committed.require_same_currency(fulfilled)
        self.committed.require_same_currency(self.outstanding)
        if self.outstanding.amount != self.committed.amount - fulfilled.amount:
            raise ValueError(
                "outstanding shown in the proof is not derivable from "
                "committed - fulfilled"
            )
        if self.status is CommitmentStatus.FULFILLED and self.outstanding.amount > 0:
            raise ValueError("FULFILLED with outstanding > 0 cannot be rendered")
        if self.outstanding_derivation is None:
            raise ValueError(
                "a monetary commitment must show its derivation trace; the number "
                "the user acts on has to be explainable"
            )
        return self


class TriggerProof(Contract):
    trigger_id: uuid.UUID
    trigger_type: TriggerType
    state: TriggerState
    predicate: PredicateNode
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    basis_case_revision: Revision
    last_evaluated_at: UtcDatetime | None = None
    last_result: Annotated[str, StringConstraints(max_length=32)] | None = None
    armed_because: Text


class CaseSnapshot(Contract):
    case_id: uuid.UUID
    case_type: CaseType
    title: Annotated[str, StringConstraints(max_length=300)]
    status: CaseStatus
    revision: Revision
    attention_level: AttentionLevel
    counterparty_name: Annotated[str, StringConstraints(max_length=200)]
    relationship_id: uuid.UUID
    opened_at: UtcDatetime
    resolved_at: UtcDatetime | None = None
    reopened_count: Annotated[int, Field(ge=0)]
    last_activity_at: UtcDatetime


class StateTransitionProof(Contract):
    state_transition_id: uuid.UUID
    transition_type: TransitionType
    case_revision: Revision
    from_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    to_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    reason_code: ReasonCode
    recorded_at: UtcDatetime


class StateProof(BoundaryContract):
    """Everything Provenance can justify about one case, at one revision.

    Bound to `case.revision`: an action approved against this proof is
    invalidated the moment the revision moves. `proof_hash` lets a client
    detect that without re-reading the whole structure.
    """

    proof_id: uuid.UUID
    generated_at: UtcDatetime
    tenant_id: uuid.UUID
    user_id: uuid.UUID

    memory_mode: MemoryMode = MemoryMode.ON
    case: CaseSnapshot | None = None
    beliefs: tuple[BeliefProof, ...] = Field(default=(), max_length=60)
    conflicts: tuple[ConflictProof, ...] = Field(default=(), max_length=30)
    commitments: tuple[CommitmentProof, ...] = Field(default=(), max_length=30)
    triggers: tuple[TriggerProof, ...] = Field(default=(), max_length=20)
    transitions: tuple[StateTransitionProof, ...] = Field(default=(), max_length=60)
    derivations: tuple[DerivationTrace, ...] = Field(default=(), max_length=20)

    memory_disabled_reason: Text | None = None
    proof_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def _memory_off_is_empty(self) -> "StateProof":
        """Addition A. A MemoryMode.OFF proof is a real, valid, empty proof.

        Judge Mode runs the identical artifact twice. With memory OFF there
        is no retrieval and no canonical state, so the honest rendering is an
        empty proof with a stated reason -- which is exactly why the OFF
        reply can only say "Invoice for $186 due 30 June" while the ON reply
        can say "contradicts your 15 May termination confirmation".

        Emptiness is enforced rather than assumed, so a counterfactual cannot
        be quietly contaminated by leaked canonical state and still be
        presented as the memory-off baseline.
        """
        if self.memory_mode is MemoryMode.OFF:
            populated = any(
                (self.beliefs, self.conflicts, self.commitments, self.triggers, self.transitions)
            )
            if populated:
                raise ValueError(
                    "a MemoryMode.OFF proof must be empty; leaking canonical state "
                    "into the counterfactual makes the comparison meaningless"
                )
            if self.memory_disabled_reason is None:
                raise ValueError("MemoryMode.OFF requires memory_disabled_reason")
        else:
            if self.case is None:
                raise ValueError("a MemoryMode.ON proof must name its case")
            if self.memory_disabled_reason is not None:
                raise ValueError("memory_disabled_reason is only valid when mode is OFF")
        return self

    @model_validator(mode="after")
    def _transitions_do_not_exceed_current_revision(self) -> "StateProof":
        if self.case is None:
            return self
        future = [t for t in self.transitions if t.case_revision > self.case.revision]
        if future:
            raise ValueError(
                "state proof contains transitions newer than the case revision it "
                "claims to describe; the read was not point-in-time consistent"
            )
        return self

    def compute_hash(self) -> str:
        """Deterministic digest of the proof's substance.

        Excludes `proof_id`, `generated_at` and `proof_hash` itself, so two
        renderings of the same committed revision agree. The Advocate binds
        a draft to this value; the executor re-computes it before sending.
        """
        return content_hash(self, exclude=PROOF_HASH_EXCLUDE)

    def with_hash(self) -> "StateProof":
        return self.model_copy(update={"proof_hash": self.compute_hash()})

    def support_ids(self) -> frozenset[uuid.UUID]:
        """Every id a draft claim is permitted to cite.

        `validate_draft_claims` checks membership against this set. A claim
        citing anything outside it is unsupported by definition.
        """
        ids: set[uuid.UUID] = set()
        for belief in self.beliefs:
            ids.add(belief.current_version.belief_version_id)
            for edge in belief.grounding:
                ids.add(edge.support_id)
                ids.add(edge.source_id)
                if edge.evidence is not None:
                    ids.add(edge.evidence.evidence_id)
        for conflict in self.conflicts:
            ids.add(conflict.conflict_id)
        for commitment in self.commitments:
            ids.add(commitment.commitment_id)
            for fulfillment in commitment.fulfillments:
                ids.add(fulfillment.fulfillment_id)
        return frozenset(ids)
```

---

## 15. `provenance_contracts/events.py` — DomainEvent

```python
"""The single envelope every domain event uses.

Written inside the Kernel transaction to `outbox_events`, swept by the
dispatcher, published to EventBridge, consumed with `event_id` as the dedupe
key against `processed_events`.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Final, Mapping

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Revision,
    UtcDatetime,
    new_id,
    utc_now,
)
from provenance_domain.enums import EVENT_AGGREGATE_TYPE, AggregateType, EventType

__all__ = ["DomainEvent", "MAX_EVENT_PAYLOAD_BYTES", "FORBIDDEN_PAYLOAD_KEYS"]

#: EventBridge caps a PutEvents entry at 256 KB. We cap far below that on
#: purpose: an event is a pointer to committed state, not a copy of it.
MAX_EVENT_PAYLOAD_BYTES: Final[int] = 16 * 1024

#: "Do not put raw document contents into logs" applied to the event bus,
#: which is a log with extra steps. A consumer that needs the text reads it
#: through an authorised API using the ids in the payload.
FORBIDDEN_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "raw_text",
        "exact_text",
        "body",
        "html_body",
        "mime_content",
        "attachment_bytes",
        "normalized_content",
        "draft_body",
        "email_body",
        "password",
        "token",
        "access_token",
        "secret",
    }
)


class DomainEvent(BoundaryContract):
    """A fact that already happened, addressed to nobody in particular.

    `aggregate_version` is the case revision (or the aggregate's own
    revision) at commit time. Consumers use it to detect out-of-order
    delivery: EventBridge is at-least-once and unordered, so a consumer that
    sees version 8 after version 9 must drop, not apply.
    """

    event_id: uuid.UUID = Field(default_factory=new_id)
    event_type: EventType
    aggregate_type: AggregateType
    aggregate_id: uuid.UUID
    aggregate_version: Revision
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    trace_id: uuid.UUID
    causation_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    occurred_at: UtcDatetime = Field(default_factory=utc_now)
    payload_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = "1.0"
    payload: Mapping[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aggregate_type_matches_event(self) -> "DomainEvent":
        expected = EVENT_AGGREGATE_TYPE[self.event_type]
        if self.aggregate_type is not expected:
            raise ValueError(
                f"{self.event_type} is an {expected} event, got {self.aggregate_type}"
            )
        return self

    @model_validator(mode="after")
    def _payload_is_small_and_clean(self) -> "DomainEvent":
        forbidden = _forbidden_keys_in(self.payload)
        if forbidden:
            raise ValueError(
                f"event payload contains keys that may carry document text or "
                f"credentials: {sorted(forbidden)}; publish ids, not contents"
            )
        encoded = json.dumps(
            dict(self.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError(
                f"event payload is {len(encoded)} bytes, over the "
                f"{MAX_EVENT_PAYLOAD_BYTES} byte cap; reference the aggregate "
                "instead of embedding it"
            )
        return self

    def dedupe_key(self, consumer_name: str) -> tuple[str, uuid.UUID]:
        """The primary key of `processed_events`. Idempotent consumption is
        the consumer's job, and this is the only key it may use.
        """
        return (consumer_name, self.event_id)


def _forbidden_keys_in(payload: object, *, depth: int = 0) -> set[str]:
    if depth > 6:
        return set()
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_PAYLOAD_KEYS:
                found.add(str(key))
            found |= _forbidden_keys_in(value, depth=depth + 1)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found |= _forbidden_keys_in(item, depth=depth + 1)
    return found
```

Example, written by the Kernel in the same transaction as the reopen:

```python
DomainEvent(
    event_type=EventType.CASE_REOPENED,
    aggregate_type=AggregateType.CASE,
    aggregate_id=cancellation_case_id,
    aggregate_version=8,
    tenant_id=tenant_id,
    user_id=user_id,
    trace_id=trace_id,
    causation_id=kernel_decision_id,
    payload={
        "reason_code": "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "conflict_id": str(conflict_id),
        "previous_status": "RESOLVED",
        "new_status": "REOPENED",
        "attention_level": "URGENT",
    },
)
```

The payload names ids and codes. It does not contain one character of the invoice.

---

## 16. `provenance_contracts/actions.py` — DraftAction and ActionIntentView

Invariant 4 in type form. A draft is a proposal; an intent is a permissioned object; an execution happens only after deterministic revalidation.

### 16.1 `DraftAction`

```python
"""Advocate output and the human-facing action record.

Every factual sentence in an outbound message must cite State Proof support
ids, and each cited span must literally appear in the body at the offsets it
claims. Both are checked here, deterministically, before a human ever sees
the draft.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IdempotencyKey,
    ReasonCode,
    Revision,
    Sha256Hex,
    UtcDatetime,
    content_hash,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActionState,
    ActionType,
    ExecutionStatus,
    ModelTier,
)

__all__ = [
    "DraftClaim",
    "DraftAction",
    "ActionExecutionView",
    "ExecutabilityVerdict",
    "ActionIntentView",
    "DRAFT_HASH_EXCLUDE",
    "FORBIDDEN_OUTBOUND_TERMS",
]

#: Excluded from the draft hash: generation metadata is not what the human
#: approved. Editing the body changes the hash; regenerating the identical
#: body with a newer prompt version does not.
DRAFT_HASH_EXCLUDE: frozenset[str] = frozenset({"draft_id", "generated_at", "generated_by"})

#: The Advocate must not mention internal scores or architecture in outbound
#: user communication. Kept deliberately narrow so it catches leaked
#: internals without censoring ordinary words: each entry is a term that has
#: no innocent reading in a letter to a landlord or an ISP.
FORBIDDEN_OUTBOUND_TERMS: Final[tuple[str, ...]] = (
    "belief_version",
    "belief version id",
    "memory kernel",
    "state proof",
    "confidence score",
    "epistemic",
    "embedding",
    "vector index",
    "cockroachdb",
    "langgraph",
    "bedrock",
    "prompt",
    "system prompt",
    "large language model",
)

Body = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class DraftClaim(Contract):
    """One factual assertion inside a draft, with its grounding.

    `support_ids` must be non-empty and must resolve inside
    `StateProof.support_ids()`. The span offsets make the check mechanical
    rather than a matter of interpretation.
    """

    claim_id: Annotated[str, StringConstraints(pattern=r"^dc_[0-9a-z]{1,16}$")]
    sentence_or_span: Sentence
    char_start: Annotated[int, Field(ge=0)]
    char_end: Annotated[int, Field(ge=1)]
    support_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10)
    support_kind: Literal["BELIEF_VERSION", "EVIDENCE", "COMMITMENT", "CONFLICT"]

    @model_validator(mode="after")
    def _span_is_sane(self) -> "DraftClaim":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.char_end - self.char_start != len(self.sentence_or_span):
            raise ValueError(
                "span length does not match the quoted sentence; the citation "
                "must be checkable against the body by offset"
            )
        return self


class DraftAction(BoundaryContract):
    """A proposed outbound communication. Never sent from this object.

    The Advocate's read tools are `get_state_proof` and `get_action_policy`;
    its only write tool is `create_action_intent`. There is no send tool
    anywhere in the agent's surface, so a prompt injection saying "call
    send_email now" has nothing to call.
    """

    draft_id: uuid.UUID
    case_id: uuid.UUID
    basis_case_revision: Revision
    basis_proof_hash: Sha256Hex
    action_type: ActionType
    channel: Literal["EMAIL"] = "EMAIL"
    recipient: Annotated[str, StringConstraints(max_length=320)]
    subject: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    body: Body
    claims: tuple[DraftClaim, ...] = Field(default=(), max_length=30)
    requested_outcome: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    tone: Literal["NEUTRAL", "FIRM", "CONCILIATORY"] = "NEUTRAL"
    unresolved_risks: tuple[Annotated[str, StringConstraints(max_length=400)], ...] = Field(
        default=(), max_length=8
    )
    generated_by: ModelAttribution
    generated_at: UtcDatetime

    @model_validator(mode="after")
    def _require_support_and_spans(self) -> "DraftAction":
        """L11. Every claim cites support, and every span is real.

        This is the draft-grounding gate: 100% of factual outbound claims
        must have at least one State Proof support id. `support_ids` is
        declared `min_length=1`, so the only remaining question is whether
        the span is honest, which is what the offset check answers.
        """
        for claim in self.claims:
            if claim.char_end > len(self.body):
                raise ValueError(
                    f"claim {claim.claim_id} cites offsets past the end of the body"
                )
            actual = self.body[claim.char_start : claim.char_end]
            if actual != claim.sentence_or_span:
                raise ValueError(
                    f"claim {claim.claim_id} quotes text that is not at the offsets "
                    "it names; a citation that cannot be located is not a citation"
                )
        ids = [c.claim_id for c in self.claims]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate claim_id in draft")
        return self

    @model_validator(mode="after")
    def _no_internal_vocabulary(self) -> "DraftAction":
        haystack = f"{self.subject}\n{self.body}".lower()
        leaked = sorted({t for t in FORBIDDEN_OUTBOUND_TERMS if t in haystack})
        if leaked:
            raise ValueError(
                f"outbound draft leaks internal vocabulary {leaked}; the recipient "
                "is a landlord or a billing department, not an engineer"
            )
        return self

    @model_validator(mode="after")
    def _drafted_by_tier_r(self) -> "DraftAction":
        if self.generated_by.tier is not ModelTier.R:
            raise ValueError("advocacy drafting is a Tier R task")
        return self

    def sha256(self) -> str:
        """The exact hash bound at approval and re-checked at execution."""
        return content_hash(self, exclude=DRAFT_HASH_EXCLUDE)

    def validate_against_proof(self, support_ids: frozenset[uuid.UUID]) -> tuple[str, ...]:
        """Return the ids of claims whose support is not in the current proof.

        Called by `validate_draft_claims`. An empty result means the draft is
        fully grounded. A non-empty result means one repair attempt, then
        `ActionState.NEEDS_REVIEW` with the warning attached -- never a
        silent send.
        """
        return tuple(
            claim.claim_id
            for claim in self.claims
            if not set(claim.support_ids).issubset(support_ids)
        )
```

### 16.2 `ActionIntentView` and the staleness verdict

```python
class ActionExecutionView(Contract):
    execution_id: uuid.UUID
    attempt_no: Annotated[int, Field(ge=1, le=5)]
    provider: Annotated[str, StringConstraints(max_length=64)]
    provider_correlation_id: Annotated[str, StringConstraints(max_length=255)] | None = None
    request_sha256: Sha256Hex
    status: ExecutionStatus
    error_code: ReasonCode | None = None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None


class ExecutabilityVerdict(Contract):
    """The answer to 'may this be sent right now'. Fails closed."""

    allowed: bool
    blocking_reasons: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _allowed_means_unblocked(self) -> "ExecutabilityVerdict":
        if self.allowed and self.blocking_reasons:
            raise ValueError("an allowed verdict cannot carry blocking reasons")
        if not self.allowed and not self.blocking_reasons:
            raise ValueError("a refusal must say why")
        return self


class ActionIntentView(BoundaryContract):
    """The permissioned action record, as the UI and the executor see it.

    Approval binds three things at once: the case revision, the exact draft
    hash, and the supporting belief versions. All three are re-checked at
    execution time by `executability()`.
    """

    action_intent_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    action_type: ActionType
    status: ActionState
    recipient: Annotated[str, StringConstraints(max_length=320)]

    draft: DraftAction
    draft_sha256: Sha256Hex
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    supporting_belief_versions: tuple[uuid.UUID, ...] = Field(default=(), max_length=40)
    basis_case_revision: Revision

    created_by_agent_run_id: uuid.UUID | None = None
    approved_by_user_id: uuid.UUID | None = None
    approved_at: UtcDatetime | None = None
    approval_draft_sha256: Sha256Hex | None = None

    idempotency_key: IdempotencyKey
    executions: tuple[ActionExecutionView, ...] = Field(default=(), max_length=5)
    warnings: tuple[Annotated[str, StringConstraints(max_length=400)], ...] = Field(
        default=(), max_length=8
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def _hash_matches_draft(self) -> "ActionIntentView":
        if self.draft.sha256() != self.draft_sha256:
            raise ValueError(
                "draft_sha256 does not match the rendered draft; the record and "
                "the content have diverged"
            )
        if self.draft.case_id != self.case_id:
            raise ValueError("draft belongs to a different case")
        if self.draft.basis_case_revision != self.basis_case_revision:
            raise ValueError("draft and intent disagree about the basis revision")
        return self

    @model_validator(mode="after")
    def _approval_is_complete_or_absent(self) -> "ActionIntentView":
        approval_fields = (
            self.approved_by_user_id,
            self.approved_at,
            self.approval_draft_sha256,
        )
        if any(f is not None for f in approval_fields) and not all(
            f is not None for f in approval_fields
        ):
            raise ValueError(
                "an approval must record who, when, and exactly what was approved"
            )
        post_approval = {
            ActionState.APPROVED,
            ActionState.EXECUTING,
            ActionState.EXECUTED,
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_FINAL,
            ActionState.CANCELLED_STALE,
        }
        if self.status in post_approval and self.approval_draft_sha256 is None:
            raise ValueError(
                f"status {self.status} without a recorded approval hash means an "
                "action could execute content no human ever saw"
            )
        return self

    def executability(
        self,
        *,
        current_case_revision: int,
        current_belief_version_ids: frozenset[uuid.UUID],
        has_successful_execution: bool,
    ) -> ExecutabilityVerdict:
        """The five checks from 02 section 18, evaluated together.

        The executor calls this immediately before dispatch, inside the same
        read as the case load. Any failure means NEEDS_REVIEW or
        CANCELLED_STALE, never an automatic send.
        """
        reasons: list[str] = []
        if self.status is not ActionState.APPROVED:
            reasons.append("NOT_APPROVED")
        if current_case_revision != self.basis_case_revision:
            reasons.append("CASE_REVISION_CHANGED")
        if (
            self.approval_draft_sha256 is None
            or self.approval_draft_sha256 != self.draft.sha256()
        ):
            reasons.append("DRAFT_HASH_CHANGED")
        if not set(self.supporting_belief_versions).issubset(current_belief_version_ids):
            reasons.append("SUPPORT_BELIEF_SUPERSEDED")
        if has_successful_execution:
            reasons.append("ALREADY_EXECUTED")
        return ExecutabilityVerdict(
            allowed=not reasons, blocking_reasons=tuple(reasons)
        )
```

`executability()` returns `SUPPORT_BELIEF_SUPERSEDED` rather than silently continuing when a cited belief has been revised. That is the difference between "we sent a letter citing a fact" and "we sent a letter citing a fact we no longer hold".

---

## 17. `provenance_contracts/triggers.py` — TriggerWakeup

```python
"""Prospective memory delivery and evaluation.

A wakeup is an invitation to re-evaluate, never an instruction to act. The
evaluator reloads the case, runs the predicate against a fresh projection,
and most of the time correctly does nothing.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IdempotencyKey,
    ReasonCode,
    Revision,
    UtcDatetime,
)
from provenance_contracts.predicates import FieldPath
from provenance_domain.enums import (
    CaseStatus,
    PredicateOp,
    TriggerResult,
    TriggerReasonCode,
    TriggerState,
    TriggerType,
    WakeupSource,
)

__all__ = ["TriggerWakeup", "PredicateEvalStep", "TriggerEvaluationResult"]


class TriggerWakeup(BoundaryContract):
    """One delivery from EventBridge Scheduler to the trigger evaluator.

    Carries `basis_case_revision` as it was when the trigger was armed. The
    evaluator compares it against the live revision: a large gap is not an
    error, it just means the world moved and the predicate must be judged
    against the world as it is now.
    """

    wakeup_id: uuid.UUID
    trigger_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    trigger_type: TriggerType
    source: WakeupSource
    schedule_name: Annotated[str, StringConstraints(max_length=64)] | None = None
    scheduled_for: UtcDatetime
    delivered_at: UtcDatetime
    basis_case_revision: Revision
    evaluation_version: Annotated[int, Field(ge=0)]
    idempotency_key: IdempotencyKey
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _scheduler_wakeups_name_their_schedule(self) -> "TriggerWakeup":
        if (
            self.source is WakeupSource.EVENTBRIDGE_SCHEDULER
            and self.schedule_name is None
        ):
            raise ValueError(
                "a scheduler-sourced wakeup must name its schedule so the "
                "one-time schedule can be deleted after it fires"
            )
        return self


class PredicateEvalStep(Contract):
    """One node of the predicate AST, with the value it saw.

    This is what makes the second reveal legible: the trace shows
    `commitments.deposit.outstanding_amount = "1800.00"` and
    `clock.now >= commitments.deposit.due_at` both resolving true, so the
    user can see the reminder was derived, not guessed.
    """

    op: PredicateOp
    path: FieldPath | None = None
    observed_value: JsonValue | None = None
    result: bool | None = None
    depth: Annotated[int, Field(ge=0, le=8)] = 0


class TriggerEvaluationResult(BoundaryContract):
    """What the evaluator decided, and why.

    A no-op is a first-class, expected outcome and is recorded as such: a
    trigger that fires after its case resolved is a bug, and `DISARMED / CASE_RESOLVED`
    is how the system proves it did not.
    """

    trigger_id: uuid.UUID
    wakeup_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    trace_id: uuid.UUID

    evaluated_at: UtcDatetime
    current_case_revision: Revision
    current_case_status: CaseStatus
    result: TriggerResult
    state_before: TriggerState
    state_after: TriggerState
    predicate_trace: tuple[PredicateEvalStep, ...] = Field(default=(), max_length=40)
    reason_code: TriggerReasonCode
    proposal_id: uuid.UUID | None = None
    outbox_event_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def _noop_means_no_side_effects(self) -> "TriggerEvaluationResult":
        is_noop = self.result is TriggerResult.NO_OP
        if is_noop:
            if self.proposal_id is not None:
                raise ValueError(
                    f"{self.result} must not submit a memory proposal; a no-op "
                    "wakeup changes nothing"
                )
            if self.state_after is TriggerState.FIRED:
                raise ValueError(f"{self.result} cannot leave the trigger FIRED")
        if self.result is TriggerResult.FIRED and self.state_after is not TriggerState.FIRED:
            raise ValueError("a FIRED result must leave the trigger in FIRED")
        if (
            self.result is TriggerResult.DISARMED
            and self.reason_code is TriggerReasonCode.CASE_RESOLVED
            and self.current_case_status is not CaseStatus.RESOLVED
        ):
            raise ValueError(
                "DISARMED/CASE_RESOLVED claims the case is resolved but the observed "
                f"status is {self.current_case_status}"
            )
        return self
```

The landlord trigger from the hero scenario fires like this:

```python
TriggerEvaluationResult(
    trigger_id=deposit_trigger_id,
    wakeup_id=wakeup_id,
    tenant_id=tenant_id,
    user_id=user_id,
    case_id=deposit_case_id,
    trace_id=trace_id,
    evaluated_at=utc_now(),
    current_case_revision=3,
    current_case_status=CaseStatus.WAITING,
    result=TriggerResult.FIRED,
    state_before=TriggerState.ARMED,
    state_after=TriggerState.FIRED,
    predicate_trace=(
        PredicateEvalStep(op=PredicateOp.AND, result=True, depth=0),
        PredicateEvalStep(
            op=PredicateOp.GT,
            path="commitments.deposit.outstanding_amount",
            observed_value="1800.00",
            result=True,
            depth=1,
        ),
        PredicateEvalStep(
            op=PredicateOp.GTE,
            path="clock.now",
            observed_value="2026-08-17T09:00:00Z",
            result=True,
            depth=1,
        ),
    ),
    reason_code=TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
    proposal_id=followup_proposal_id,
    outbox_event_ids=(commitment_overdue_event_id,),
)
```

Nobody set a reminder. A predicate was armed when the promise was recorded, and thirty days later the clock made it true.

---

## 18. `provenance_contracts/__init__.py`

```python
"""Shared Provenance contracts. Import from here, never from a submodule of
another service.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from pydantic import BaseModel

from provenance_contracts.actions import (
    ActionExecutionView,
    ActionIntentView,
    DraftAction,
    DraftClaim,
    ExecutabilityVerdict,
)
from provenance_contracts.base import (
    SCHEMA_VERSION,
    BoundaryContract,
    Confidence,
    Contract,
    Money,
    UtcDatetime,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)
from provenance_contracts.events import DomainEvent
from provenance_contracts.identity import (
    AuthorizationError,
    CapabilityBinding,
    InternalPrincipal,
    Principal,
)
from provenance_contracts.ingestion import (
    ArtifactMetadata,
    ContentBlock,
    ContentLocator,
    ExtractionResult,
    NormalizedContent,
    SourceLocator,
)
from provenance_contracts.kernel import (
    BeliefVersionRef,
    CommitmentChange,
    ConflictRef,
    KernelCommitResult,
    StateTransitionRef,
    TriggerChange,
)
from provenance_contracts.predicates import PredicateNode
from provenance_contracts.proof import (
    BeliefProof,
    CaseSnapshot,
    ConflictProof,
    CommitmentProof,
    GroundingEdgeProof,
    LineageEntry,
    StateProof,
)
from provenance_contracts.proposal import (
    ConflictHint,
    MemoryProposal,
    ProposedBeliefMutation,
    ProposedClaim,
    ProposedCommitment,
    ProposedSupportEdge,
    ProposedTrigger,
)
from provenance_contracts.resolution import ModelAttribution, ResolutionAssessment
from provenance_contracts.retrieval import (
    EvidenceSnippet,
    IdentityCandidate,
    McpToolCall,
    RetrievalContext,
    VectorSearchParams,
)
from provenance_contracts.triggers import (
    PredicateEvalStep,
    TriggerEvaluationResult,
    TriggerWakeup,
)

__version__ = "1.0.0"

#: Every model that crosses a boundary, by name. The API layer iterates this
#: to emit JSON Schemas, and `test_roundtrip.py` iterates it to prove every
#: contract serialises and re-validates.
CONTRACT_REGISTRY: Final[Mapping[str, type[BaseModel]]] = MappingProxyType(
    {
        "Principal": Principal,
        "InternalPrincipal": InternalPrincipal,
        "ArtifactMetadata": ArtifactMetadata,
        "NormalizedContent": NormalizedContent,
        "ExtractionResult": ExtractionResult,
        "IdentityCandidate": IdentityCandidate,
        "RetrievalContext": RetrievalContext,
        "ResolutionAssessment": ResolutionAssessment,
        "MemoryProposal": MemoryProposal,
        "KernelCommitResult": KernelCommitResult,
        "StateProof": StateProof,
        "DomainEvent": DomainEvent,
        "DraftAction": DraftAction,
        "ActionIntentView": ActionIntentView,
        "TriggerWakeup": TriggerWakeup,
        "TriggerEvaluationResult": TriggerEvaluationResult,
    }
)
```

`CONTRACT_REGISTRY` is the single place a new boundary contract must be registered. Two tests iterate it — round-trip serialisation and JSON Schema generation — so a contract that is added to a module but forgotten here is untested by construction, and a contract added here but broken fails CI immediately.

---

## 19. Packaging

```toml
# packages/python/provenance_domain/pyproject.toml
[project]
name = "provenance-domain"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/provenance_domain"]
```

```toml
# packages/python/provenance_contracts/pyproject.toml
[project]
name = "provenance-contracts"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.9,<3",
  "provenance-domain==1.0.0",
  "uuid6>=2024.7.10",   # UUIDv7 until the stdlib ships uuid.uuid7
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov>=5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/provenance_contracts"]
```

`uuid6` is a soft dependency in code (`ImportError` falls back to `uuid4`) and a hard dependency in the manifest. The fallback exists so a Lambda layer that omits it still runs correctly; the manifest exists so it normally does not have to.

---

## 20. Unit tests

These are not illustrative. They are the acceptance criteria for the two packages, and they must exist and pass before anything downstream is built.

### 20.1 `provenance_domain/tests/test_transitions.py` — illegal transitions are rejected

```python
"""State-machine legality. Every negative case here is a real bug someone
would otherwise ship: a resolved case quietly going back to work without a
reason, an approved action skipping revalidation, a settled obligation
un-settling itself.
"""

import pytest

from provenance_domain.enums import (
    ActionState,
    CaseStatus,
    CommitmentStatus,
    OutboxStatus,
    TriggerState,
)
from provenance_domain.transitions import (
    ACTION_MACHINE,
    CASE_MACHINE,
    CASE_TRANSITIONS,
    COMMITMENT_MACHINE,
    OUTBOX_MACHINE,
    TRIGGER_MACHINE,
    IllegalTransitionError,
    assert_transition,
    legal_transition,
    reachable_states,
)


# --- the headline case: RESOLVED does not go back to work -------------------

def test_resolved_case_cannot_jump_straight_to_actionable():
    """A resolved case reopens or it does nothing. It never silently becomes
    actionable again -- that would erase the fact that it had been closed,
    which is the whole point of the hero scenario.
    """
    assert not legal_transition(CASE_MACHINE, CaseStatus.RESOLVED, CaseStatus.ACTIONABLE)

    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(CASE_MACHINE, CaseStatus.RESOLVED, CaseStatus.ACTIONABLE)

    error = excinfo.value
    assert error.machine == "case"
    assert error.from_state == "RESOLVED"
    assert error.to_state == "ACTIONABLE"
    assert "not a legal transition" in str(error)


def test_reopen_requires_a_qualifying_reason_code():
    """RESOLVED -> REOPENED is guarded. Without a reason from the allowlist
    it is illegal, so no code path can reopen a case "just because".
    """
    assert not legal_transition(CASE_MACHINE, CaseStatus.RESOLVED, CaseStatus.REOPENED)
    assert not legal_transition(
        CASE_MACHINE, CaseStatus.RESOLVED, CaseStatus.REOPENED, reason_code="BECAUSE"
    )
    assert legal_transition(
        CASE_MACHINE,
        CaseStatus.RESOLVED,
        CaseStatus.REOPENED,
        reason_code="COUNTERPARTY_CLAIM_AFTER_CLOSE",
    )


def test_reopen_error_names_the_allowed_reason_codes():
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(
            CASE_MACHINE, CaseStatus.RESOLVED, CaseStatus.REOPENED, reason_code="NOPE"
        )
    assert "guarded transition requires reason_code" in str(excinfo.value)
    assert "CONTRADICTORY_EVIDENCE" in str(excinfo.value)


def test_superseded_case_is_terminal():
    for target in CaseStatus:
        if target is CaseStatus.SUPERSEDED:
            continue
        assert not legal_transition(
            CASE_MACHINE, CaseStatus.SUPERSEDED, target, reason_code="MERGED_INTO_CASE"
        )


def test_no_self_transition_on_a_case():
    """A no-op must not look like a transition, because a transition
    increments a case revision.
    """
    assert not legal_transition(CASE_MACHINE, CaseStatus.OPEN, CaseStatus.OPEN)


def test_unknown_states_are_rejected_not_ignored():
    assert not legal_transition(CASE_MACHINE, "ALMOST_DONE", CaseStatus.RESOLVED)
    assert not legal_transition(CASE_MACHINE, CaseStatus.OPEN, "MOSTLY_FINE")


def test_every_case_state_is_reachable_from_open():
    reachable = reachable_states(CASE_MACHINE, CaseStatus.OPEN)
    for status in CaseStatus:
        if status is CaseStatus.OPEN:
            continue
        assert str(status) in reachable, f"{status} is orphaned in the case machine"


def test_transition_table_is_immutable():
    with pytest.raises(TypeError):
        CASE_TRANSITIONS["OPEN"] = frozenset()  # type: ignore[index]


# --- actions: no route to EXECUTING that skips approval ---------------------

def test_proposed_cannot_execute():
    assert not legal_transition(ACTION_MACHINE, ActionState.PROPOSED, ActionState.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        assert_transition(ACTION_MACHINE, ActionState.PROPOSED, ActionState.EXECUTING)


def test_needs_review_cannot_execute():
    assert not legal_transition(
        ACTION_MACHINE, ActionState.NEEDS_REVIEW, ActionState.EXECUTING
    )


def test_approved_executes_only_after_revalidation():
    assert not legal_transition(ACTION_MACHINE, ActionState.APPROVED, ActionState.EXECUTING)
    assert legal_transition(
        ACTION_MACHINE,
        ActionState.APPROVED,
        ActionState.EXECUTING,
        reason_code="REVALIDATION_PASSED",
    )


def test_executed_is_terminal():
    assert not legal_transition(ACTION_MACHINE, ActionState.EXECUTED, ActionState.EXECUTING)
    assert not legal_transition(ACTION_MACHINE, ActionState.EXECUTED, ActionState.NEEDS_REVIEW)


# --- commitments, triggers, outbox ------------------------------------------

def test_fulfilled_commitment_cannot_silently_become_active():
    assert not legal_transition(
        COMMITMENT_MACHINE, CommitmentStatus.FULFILLED, CommitmentStatus.ACTIVE
    )
    assert not legal_transition(
        COMMITMENT_MACHINE, CommitmentStatus.FULFILLED, CommitmentStatus.PARTIAL
    )


def test_unfulfilling_requires_a_named_cause():
    assert not legal_transition(
        COMMITMENT_MACHINE, CommitmentStatus.FULFILLED, CommitmentStatus.DISPUTED
    )
    assert legal_transition(
        COMMITMENT_MACHINE,
        CommitmentStatus.FULFILLED,
        CommitmentStatus.DISPUTED,
        reason_code="PAYMENT_CLAWED_BACK",
    )


def test_disarmed_trigger_cannot_fire():
    assert not legal_transition(TRIGGER_MACHINE, TriggerState.DISARMED, TriggerState.FIRED)
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(TRIGGER_MACHINE, TriggerState.DISARMED, TriggerState.FIRED)
    assert "terminal" in str(excinfo.value)


def test_outbox_cannot_skip_dispatching():
    assert not legal_transition(OUTBOX_MACHINE, OutboxStatus.PENDING, OutboxStatus.DISPATCHED)
    assert legal_transition(OUTBOX_MACHINE, OutboxStatus.PENDING, OutboxStatus.DISPATCHING)


def test_dead_letters_stay_dead():
    assert not legal_transition(OUTBOX_MACHINE, OutboxStatus.DEAD, OutboxStatus.DISPATCHING)
```

### 20.2 `provenance_domain/tests/test_invariants.py`

```python
from decimal import Decimal

import pytest

from provenance_domain.enums import CommitmentStatus
from provenance_domain.invariants import (
    CurrencyMismatchError,
    InvariantViolation,
    assert_commitment_consistent,
    assert_revision_increment,
    derive_commitment_status,
    derive_outstanding,
)


def test_moving_company_partial_fulfilment():
    """USD 420 promised, USD 200 paid, USD 220 outstanding, status PARTIAL."""
    amounts = derive_outstanding(
        currency="USD",
        committed=Decimal("420.00"),
        fulfilled=Decimal("200.00"),
        fulfilment_currency="USD",
    )
    assert amounts.outstanding == Decimal("220.0000")
    status = derive_commitment_status(
        amounts, current=CommitmentStatus.ACTIVE, has_blocking_conflict=False
    )
    assert status is CommitmentStatus.PARTIAL
    assert_commitment_consistent(amounts, status)


def test_cross_currency_arithmetic_is_refused():
    with pytest.raises(CurrencyMismatchError):
        derive_outstanding(
            currency="USD",
            committed=Decimal("420.00"),
            fulfilled=Decimal("200.00"),
            fulfilment_currency="EUR",
        )


def test_overfulfilment_is_not_clamped():
    amounts = derive_outstanding(
        currency="USD",
        committed=Decimal("420.00"),
        fulfilled=Decimal("500.00"),
        fulfilment_currency="USD",
    )
    assert amounts.outstanding == Decimal("-80.0000")
    assert (
        derive_commitment_status(
            amounts, current=CommitmentStatus.PARTIAL, has_blocking_conflict=False
        )
        is CommitmentStatus.DISPUTED
    )


def test_fulfilled_with_outstanding_is_impossible():
    amounts = derive_outstanding(
        currency="USD",
        committed=Decimal("420.00"),
        fulfilled=Decimal("200.00"),
        fulfilment_currency="USD",
    )
    with pytest.raises(InvariantViolation) as excinfo:
        assert_commitment_consistent(amounts, CommitmentStatus.FULFILLED)
    assert excinfo.value.code == "FULFILLED_WITH_OUTSTANDING"


def test_revision_moves_by_exactly_one_or_not_at_all():
    assert_revision_increment(7, 8, changed=True)
    assert_revision_increment(7, 7, changed=False)
    with pytest.raises(InvariantViolation):
        assert_revision_increment(7, 9, changed=True)
    with pytest.raises(InvariantViolation):
        assert_revision_increment(7, 8, changed=False)
```

### 20.3 `provenance_contracts/tests/test_scalars.py` — money, time, confidence

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from provenance_contracts.base import Confidence, Money, UtcDatetime


class _Probe(BaseModel):
    at: UtcDatetime
    score: Confidence


def test_float_money_is_rejected_in_python():
    with pytest.raises(ValidationError) as excinfo:
        Money(amount=186.00, currency="USD")
    assert "float is not an acceptable monetary amount" in str(excinfo.value)


def test_money_accepts_string_and_decimal_and_int():
    assert Money(amount="186.00", currency="USD").amount == Decimal("186.00")
    assert Money(amount=Decimal("186.00"), currency="USD").amount == Decimal("186.00")
    assert Money(amount=186, currency="USD").amount == Decimal("186")


def test_money_json_wire_form_is_a_string():
    parsed = Money.model_validate_json('{"amount": "186.00", "currency": "USD"}')
    assert parsed.amount == Decimal("186.00")
    assert parsed.model_dump(mode="json") == {"amount": "186.00", "currency": "USD"}


def test_money_rejects_more_than_four_decimal_places():
    with pytest.raises(ValidationError) as excinfo:
        Money(amount="186.000001", currency="USD")
    assert "more than 4 decimal places" in str(excinfo.value)


def test_money_currency_must_be_three_upper_letters():
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="usd")
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="DOLLARS")


def test_money_arithmetic_refuses_cross_currency():
    usd = Money(amount="420.00", currency="USD")
    eur = Money(amount="200.00", currency="EUR")
    assert (usd - Money(amount="200.00", currency="USD")).amount == Decimal("220.00")
    with pytest.raises(ValueError, match="currency mismatch"):
        usd - eur


def test_money_is_frozen():
    money = Money(amount="1.00", currency="USD")
    with pytest.raises(ValidationError):
        money.amount = Decimal("2.00")


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _Probe(at=datetime(2026, 6, 5, 12, 0, 0), score="0.5")
    assert "naive datetime rejected" in str(excinfo.value)


def test_offset_datetimes_are_normalised_to_utc():
    probe = _Probe(at="2026-06-05T14:00:00+02:00", score="0.5")
    assert probe.at == datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert probe.at.tzinfo is timezone.utc


def test_z_suffix_is_accepted():
    assert _Probe(at="2026-06-05T12:00:00Z", score="0").at.tzinfo is timezone.utc


def test_confidence_is_bounded_and_quantised():
    assert _Probe(at="2026-06-05T12:00:00Z", score=0.876543).score == Decimal("0.8765")
    with pytest.raises(ValidationError):
        _Probe(at="2026-06-05T12:00:00Z", score="1.01")
    with pytest.raises(ValidationError):
        _Probe(at="2026-06-05T12:00:00Z", score="-0.01")
    with pytest.raises(ValidationError):
        _Probe(at="2026-06-05T12:00:00Z", score=True)


def test_extra_fields_are_forbidden():
    """An agent cannot smuggle an authority score into a contract."""
    with pytest.raises(ValidationError) as excinfo:
        Money.model_validate(
            {"amount": "1.00", "currency": "USD", "authority_score": "1.0"}
        )
    assert "authority_score" in str(excinfo.value)
```

### 20.4 `test_proposal_grounding.py` — the grounding invariant

```python
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from provenance_contracts.proposal import (
    DeterministicDerivation,
    ProposedBeliefMutation,
    ProposedSupportEdge,
)
from provenance_domain.enums import (
    BeliefMutationKind,
    EpistemicStatus,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    ValueType,
)


def _base(**overrides):
    payload = dict(
        local_id="bm_1",
        mutation_kind=BeliefMutationKind.CREATE,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=uuid.uuid4(),
        predicate="service_terminated",
        value_type=ValueType.BOOLEAN,
        value_json=True,
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.95"),
    )
    payload.update(overrides)
    return payload


def test_belief_without_grounding_or_derivation_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(**_base())
    assert "UNGROUNDED" in str(excinfo.value)


def test_contradicting_edge_alone_does_not_ground_a_belief():
    """A belief supported only by something that contradicts it is not
    grounded. This is the subtle version of the bug and the one most likely
    to slip through review.
    """
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            **_base(
                grounding=(
                    ProposedSupportEdge(
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=uuid.uuid4(),
                        relation=SupportRelation.CONTRADICTS,
                    ),
                )
            )
        )
    assert "UNGROUNDED" in str(excinfo.value)


def test_one_supports_edge_is_enough():
    mutation = ProposedBeliefMutation(
        **_base(
            grounding=(
                ProposedSupportEdge(
                    source_kind=SupportSourceKind.EVIDENCE,
                    source_id=uuid.uuid4(),
                    relation=SupportRelation.SUPPORTS,
                    weight=Decimal("0.9"),
                ),
            )
        )
    )
    assert len(mutation.grounding) == 1


def test_registered_derivation_is_the_only_ungrounded_path():
    mutation = ProposedBeliefMutation(
        **_base(
            derivation=DeterministicDerivation(
                name="outstanding_from_committed_minus_fulfilled",
                function_version="1.0.0",
                input_refs=(uuid.uuid4(),),
            )
        )
    )
    assert mutation.grounding == ()


def test_unregistered_derivation_cannot_bypass_grounding():
    with pytest.raises(ValidationError) as excinfo:
        DeterministicDerivation(
            name="trust_me_bro",
            function_version="1.0.0",
            input_refs=(uuid.uuid4(),),
        )
    assert "not a registered" in str(excinfo.value)


def test_revision_must_name_the_version_it_replaces():
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            **_base(
                local_id="bm_2",
                mutation_kind=BeliefMutationKind.REVISE,
                belief_id=uuid.uuid4(),
                subject_id=None,
                subject_type=None,
                predicate=None,
                grounding=(
                    ProposedSupportEdge(
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=uuid.uuid4(),
                        relation=SupportRelation.SUPPORTS,
                    ),
                ),
            )
        )
    assert "must name the version it replaces" in str(excinfo.value)


def test_retraction_is_exempt_but_must_be_explained():
    retraction = ProposedBeliefMutation(
        local_id="bm_3",
        mutation_kind=BeliefMutationKind.RETRACT,
        belief_id=uuid.uuid4(),
        epistemic_status=EpistemicStatus.RETRACTED,
        belief_confidence=Decimal("1.0"),
        reason_code="USER_CORRECTED_RECORD",
    )
    assert retraction.grounding == ()

    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            local_id="bm_4",
            mutation_kind=BeliefMutationKind.RETRACT,
            belief_id=uuid.uuid4(),
            epistemic_status=EpistemicStatus.RETRACTED,
            belief_confidence=Decimal("1.0"),
        )
    assert "unauditable" in str(excinfo.value)
```

### 20.5 `test_retrieval_retraction.py` — Addition C

```python
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from provenance_contracts.ingestion import SourceLocator
from provenance_contracts.retrieval import EvidenceSnippet, RetrievalContext
from provenance_domain.enums import EvidenceType, RetractionStatus


def _snippet(**overrides):
    payload = dict(
        evidence_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        evidence_type=EvidenceType.INVOICE_LINE,
        normalized_text="Invoice for service June 1 through June 30. Amount due USD 186.",
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id="blk_body1", char_start=0, char_end=64
        ),
        observed_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )
    payload.update(overrides)
    return payload


def test_active_evidence_is_accepted():
    assert _snippet_model().retraction_status is RetractionStatus.ACTIVE


def _snippet_model():
    return EvidenceSnippet(**_snippet())


def test_retracted_evidence_cannot_enter_a_retrieval_context():
    """Retracted evidence keeps its embedding, so ANN search will return it.
    The type refuses to carry it into a prompt.
    """
    with pytest.raises(ValidationError):
        EvidenceSnippet(**_snippet(retraction_status=RetractionStatus.RETRACTED))


def test_superseded_evidence_is_equally_refused():
    with pytest.raises(ValidationError):
        EvidenceSnippet(**_snippet(retraction_status=RetractionStatus.SUPERSEDED))


def test_quarantined_evidence_is_equally_refused():
    with pytest.raises(ValidationError):
        EvidenceSnippet(**_snippet(retraction_status=RetractionStatus.QUARANTINED))


def test_vector_params_cannot_claim_an_unfiltered_search():
    from provenance_contracts.retrieval import VectorSearchParams

    with pytest.raises(ValidationError):
        VectorSearchParams(retraction_filter_applied=False)
    with pytest.raises(ValidationError):
        VectorSearchParams(user_prefix_applied=False)


def test_context_refuses_candidates_belonging_to_another_user():
    from provenance_contracts.retrieval import IdentityCandidate
    from provenance_domain.enums import CaseStatus, IdentityCandidateKind

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    foreign = IdentityCandidate(
        candidate_kind=IdentityCandidateKind.CASE,
        candidate_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),  # someone else
        label="Old ISP cancellation",
        case_status=CaseStatus.RESOLVED,
        score="0.99",
    )
    with pytest.raises(ValidationError) as excinfo:
        RetrievalContext(
            trace_id=uuid.uuid4(),
            agent_run_id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            case_candidates=(foreign,),
            retrieved_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
    assert "belong to another user" in str(excinfo.value)


def test_evidence_snippet_cap_is_ten():
    with pytest.raises(ValidationError):
        RetrievalContext(
            trace_id=uuid.uuid4(),
            agent_run_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            evidence_snippets=tuple(_snippet_model() for _ in range(11)),
            retrieved_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
```

### 20.6 `test_draft_grounding.py` — L11

```python
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from provenance_contracts.actions import DraftAction, DraftClaim
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ActionType, ModelTier

BODY = (
    "Your invoice covers 1-30 June 2026. "
    "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026. "
    "Please withdraw the charge of USD 186.00."
)
SPAN = "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026."
START = BODY.index(SPAN)

TIER_R = ModelAttribution(
    model_id="anthropic.claude-opus-5",
    tier=ModelTier.R,
    prompt_version="advocate-1.1",
    graph_name="advocate_graph",
    graph_version="1.0.0",
)


def _draft(**overrides):
    payload = dict(
        draft_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        basis_case_revision=8,
        basis_proof_hash="a" * 64,
        action_type=ActionType.OUTBOUND_EMAIL_DISPUTE,
        recipient="billing@example-isp.test",
        subject="Invoice for June 2026 on a cancelled account",
        body=BODY,
        requested_outcome="Withdraw the June invoice and confirm the account is closed.",
        generated_by=TIER_R,
        generated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )
    payload.update(overrides)
    return payload


def test_a_claim_must_cite_support():
    with pytest.raises(ValidationError):
        DraftClaim(
            claim_id="dc_1",
            sentence_or_span=SPAN,
            char_start=START,
            char_end=START + len(SPAN),
            support_ids=(),
            support_kind="BELIEF_VERSION",
        )


def test_a_claim_span_must_actually_be_in_the_body():
    bad = DraftClaim(
        claim_id="dc_1",
        sentence_or_span=SPAN,
        char_start=0,  # wrong offset
        char_end=len(SPAN),
        support_ids=(uuid.uuid4(),),
        support_kind="BELIEF_VERSION",
    )
    with pytest.raises(ValidationError) as excinfo:
        DraftAction(**_draft(claims=(bad,)))
    assert "not at the offsets it names" in str(excinfo.value)


def test_a_grounded_draft_validates():
    support_id = uuid.uuid4()
    draft = DraftAction(
        **_draft(
            claims=(
                DraftClaim(
                    claim_id="dc_1",
                    sentence_or_span=SPAN,
                    char_start=START,
                    char_end=START + len(SPAN),
                    support_ids=(support_id,),
                    support_kind="BELIEF_VERSION",
                ),
            )
        )
    )
    assert draft.validate_against_proof(frozenset({support_id})) == ()
    assert draft.validate_against_proof(frozenset()) == ("dc_1",)


def test_draft_hash_ignores_generation_metadata_but_not_content():
    first = DraftAction(**_draft())
    same_content = DraftAction(
        **_draft(draft_id=uuid.uuid4(), generated_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    )
    assert first.sha256() == same_content.sha256()

    edited = DraftAction(**_draft(body=BODY + " Thank you."))
    assert first.sha256() != edited.sha256()


def test_internal_vocabulary_cannot_leak_into_an_outbound_message():
    with pytest.raises(ValidationError) as excinfo:
        DraftAction(
            **_draft(
                body=BODY + " Our belief_version 3 has a confidence score of 0.95."
            )
        )
    assert "leaks internal vocabulary" in str(excinfo.value)


def test_advocacy_drafting_is_tier_r_only():
    tier_e = ModelAttribution(
        model_id="anthropic.claude-haiku-4-5",
        tier=ModelTier.E,
        prompt_version="advocate-1.1",
        graph_name="advocate_graph",
        graph_version="1.0.0",
    )
    with pytest.raises(ValidationError):
        DraftAction(**_draft(generated_by=tier_e))


def test_model_tier_is_frozen_to_one_model_id():
    with pytest.raises(ValidationError) as excinfo:
        ModelAttribution(
            model_id="anthropic.claude-sonnet-4-6",
            tier=ModelTier.R,
            prompt_version="advocate-1.1",
            graph_name="advocate_graph",
            graph_version="1.0.0",
        )
    assert "frozen to" in str(excinfo.value)
```

### 20.7 `test_kernel_result.py` — revision arithmetic and honest receipts

```python
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from provenance_contracts.kernel import ConflictRef, KernelCommitResult, StateTransitionRef
from provenance_domain.enums import (
    ConflictStatus,
    ConflictType,
    KernelDecision,
    ProposalStatus,
    TransitionType,
)

NOW = datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)


def _conflict():
    return ConflictRef(
        conflict_id=uuid.uuid4(),
        conflict_type=ConflictType.VALUE_CONFLICT,
        status=ConflictStatus.OPEN,
        predicate="service_terminated",
        requires_human=False,
        created=True,
    )


def _transition(revision: int):
    return StateTransitionRef(
        state_transition_id=uuid.uuid4(),
        transition_type=TransitionType.CASE_STATUS,
        case_revision=revision,
        from_state="RESOLVED",
        to_state="REOPENED",
        reason_code="COUNTERPARTY_CLAIM_AFTER_CLOSE",
        recorded_at=NOW,
    )


def _result(**overrides):
    payload = dict(
        decision=KernelDecision.ACCEPTED_WITH_CONFLICT,
        proposal_id=uuid.uuid4(),
        kernel_decision_id=uuid.uuid4(),
        proposal_status=ProposalStatus.ACCEPTED_WITH_CONFLICT,
        trace_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_revision_before=7,
        case_revision_after=8,
        created_claim_ids=(uuid.uuid4(), uuid.uuid4()),
        created_or_updated_conflicts=(_conflict(),),
        state_transitions=(_transition(8),),
        outbox_event_ids=(uuid.uuid4(),),
        attention_required=True,
        committed_at=NOW,
    )
    payload.update(overrides)
    return payload


def test_the_hero_commit_validates():
    result = KernelCommitResult(**_result())
    assert result.is_accepted
    assert result.should_wake_advocate


def test_revision_must_advance_by_exactly_one():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(case_revision_after=9))
    assert "case revision must go 7 -> 8" in str(excinfo.value)


def test_a_noop_must_not_advance_the_revision():
    with pytest.raises(ValidationError):
        KernelCommitResult(
            **_result(
                created_claim_ids=(),
                created_or_updated_conflicts=(),
                state_transitions=(),
                decision=KernelDecision.ACCEPTED,
                proposal_status=ProposalStatus.ACCEPTED,
                attention_required=False,
            )
        )


def test_state_transitions_carry_the_new_revision():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(state_transitions=(_transition(7),)))
    assert "carries that commit's new revision 8" in str(excinfo.value)


def test_decision_and_proposal_status_cannot_disagree():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(proposal_status=ProposalStatus.ACCEPTED))
    assert "implies proposal status" in str(excinfo.value)


def test_a_rejection_writes_nothing():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_result(
                decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
                proposal_status=ProposalStatus.REJECTED_INVALID_PROVENANCE,
                reason_codes=("CROSS_USER_EVIDENCE",),
            )
        )
    assert "writes nothing but its own decision row" in str(excinfo.value)


def test_a_rejection_must_be_explained():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_result(
                decision=KernelDecision.REJECTED_INVARIANT,
                proposal_status=ProposalStatus.REJECTED_INVARIANT,
                created_claim_ids=(),
                created_or_updated_conflicts=(),
                state_transitions=(),
                outbox_event_ids=(),
                committed_at=None,
                attention_required=False,
                case_revision_before=None,
                case_revision_after=None,
            )
        )
    assert "at least one reason code" in str(excinfo.value)


def test_a_committed_conflict_always_raises_attention():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(attention_required=False))
    assert "silent contradictions" in str(excinfo.value)


def test_retryable_concurrency_is_not_a_commit():
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_result(
                decision=KernelDecision.RETRYABLE_CONCURRENCY,
                proposal_status=ProposalStatus.SUBMITTED,
                retry_count=5,
            )
        )
    assert "rolled back" in str(excinfo.value)
```

### 20.8 `test_state_proof.py` — grounding, lineage, and the Judge Mode counterfactual

```python
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from provenance_contracts.ingestion import SourceLocator
from provenance_contracts.proof import (
    BeliefProof,
    BeliefVersionProof,
    EvidenceProof,
    GroundingEdgeProof,
    LineageEntry,
    StateProof,
)
from provenance_domain.enums import (
    EpistemicStatus,
    EvidenceType,
    MemoryMode,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    ValueType,
)

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)
EVIDENCE_ID = uuid.uuid4()


def _evidence():
    return EvidenceProof(
        evidence_id=EVIDENCE_ID,
        artifact_id=uuid.uuid4(),
        evidence_type=EvidenceType.CANCELLATION_NOTICE,
        normalized_text="Your cancellation is confirmed. Service ends 31 May 2026.",
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id="blk_body1", char_start=0, char_end=56
        ),
        observed_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        artifact_received_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )


def _version(no: int = 1):
    return BeliefVersionProof(
        belief_version_id=uuid.uuid4(),
        version_no=no,
        value_type=ValueType.BOOLEAN,
        value_json=True,
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.95"),
        recorded_at=NOW,
        kernel_decision_id=uuid.uuid4(),
    )


def _belief(**overrides):
    payload = dict(
        belief_id=uuid.uuid4(),
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=uuid.uuid4(),
        subject_label="Old ISP",
        predicate="service_terminated",
        current_version=_version(),
        grounding=(
            GroundingEdgeProof(
                support_id=uuid.uuid4(),
                source_kind=SupportSourceKind.EVIDENCE,
                source_id=EVIDENCE_ID,
                relation=SupportRelation.SUPPORTS,
                evidence=_evidence(),
            ),
        ),
    )
    payload.update(overrides)
    return payload


def test_a_canonical_belief_must_be_grounded_at_render_time():
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(**_belief(grounding=()))
    assert "UNGROUNDED" in str(excinfo.value)


def test_an_evidence_edge_must_render_its_evidence():
    with pytest.raises(ValidationError) as excinfo:
        GroundingEdgeProof(
            support_id=uuid.uuid4(),
            source_kind=SupportSourceKind.EVIDENCE,
            source_id=EVIDENCE_ID,
            relation=SupportRelation.SUPPORTS,
        )
    assert "must render its evidence" in str(excinfo.value)


def test_lineage_supersession_requires_a_reason():
    with pytest.raises(ValidationError) as excinfo:
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=1,
            value_json=True,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=uuid.uuid4(),
            kernel_decision_id=uuid.uuid4(),
        )
    assert "without a reason code" in str(excinfo.value)


def test_lineage_must_end_at_the_current_version():
    v2 = _version(2)
    stale_lineage = (
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=1,
            value_json=True,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=v2.belief_version_id,
            supersession_reason_code="CONTRADICTORY_EVIDENCE",
            kernel_decision_id=uuid.uuid4(),
        ),
    )
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(**_belief(current_version=v2, lineage=stale_lineage))
    assert "exactly one lineage entry may be un-superseded" in str(excinfo.value)


def test_memory_off_proof_must_be_empty():
    """Addition A. The counterfactual cannot be contaminated."""
    with pytest.raises(ValidationError) as excinfo:
        StateProof(
            proof_id=uuid.uuid4(),
            generated_at=NOW,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            memory_mode=MemoryMode.OFF,
            memory_disabled_reason="Judge Mode: retrieval and canonical memory disabled.",
            beliefs=(BeliefProof(**_belief()),),
        )
    assert "must be empty" in str(excinfo.value)


def test_memory_off_proof_requires_a_stated_reason():
    with pytest.raises(ValidationError) as excinfo:
        StateProof(
            proof_id=uuid.uuid4(),
            generated_at=NOW,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            memory_mode=MemoryMode.OFF,
        )
    assert "memory_disabled_reason" in str(excinfo.value)


def test_memory_off_proof_is_valid_when_genuinely_empty():
    proof = StateProof(
        proof_id=uuid.uuid4(),
        generated_at=NOW,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_mode=MemoryMode.OFF,
        memory_disabled_reason="Judge Mode: retrieval and canonical memory disabled.",
    )
    assert proof.support_ids() == frozenset()


def test_proof_hash_is_stable_across_renderings():
    common = dict(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_mode=MemoryMode.OFF,
        memory_disabled_reason="Judge Mode: retrieval and canonical memory disabled.",
    )
    first = StateProof(proof_id=uuid.uuid4(), generated_at=NOW, **common)
    second = StateProof(
        proof_id=uuid.uuid4(),
        generated_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        **common,
    )
    assert first.compute_hash() == second.compute_hash()
```

### 20.9 `test_roundtrip.py`

```python
import json

from pydantic import BaseModel

from provenance_contracts import CONTRACT_REGISTRY
from provenance_contracts.base import SCHEMA_VERSION


def test_every_boundary_contract_declares_schema_version():
    for name, model in CONTRACT_REGISTRY.items():
        assert "schema_version" in model.model_fields, f"{name} is not a BoundaryContract"
        assert model.model_fields["schema_version"].default == SCHEMA_VERSION


def test_every_contract_emits_a_json_schema():
    for name, model in CONTRACT_REGISTRY.items():
        schema = model.model_json_schema()
        assert schema["type"] == "object", name
        assert json.dumps(schema)  # serialisable, no dangling refs


def roundtrip(instance: BaseModel) -> BaseModel:
    """Helper used by per-contract fixtures: dump to JSON, re-validate, and
    require structural equality. Every contract in CONTRACT_REGISTRY has a
    fixture in `tests/fixtures/` and is exercised through this function.
    """
    restored = type(instance).model_validate_json(instance.model_dump_json())
    assert restored == instance
    return restored
```

### 20.10 `test_no_sql_in_contracts.py` — contract law L5

```python
"""No contract may contain SQL text or a canonical table name.

Implemented as an AST scan over the shipped source rather than a runtime
check, because the rule is about what the contracts package *is*, not about
what a particular payload happens to hold. Docstrings are skipped: they are
allowed to discuss the data model in prose, and section 3 of the spec
depends on that.
"""

from __future__ import annotations

import ast
import pathlib
import re

import provenance_contracts

CANONICAL_TABLES = frozenset(
    {
        "tenants", "users", "ingest_aliases", "counterparties", "relationships",
        "contexts", "cases", "source_artifacts", "evidence_items", "claims",
        "beliefs", "belief_versions", "belief_support", "conflicts", "commitments",
        "fulfillments", "state_transitions", "memory_proposals", "kernel_decisions",
        "prospective_triggers", "action_intents", "action_executions",
        "outbox_events", "processed_events", "agent_runs", "idempotency_records",
    }
)

# Statement shapes rather than bare keywords. `from` and `where` are ordinary
# English and appear in error messages; `SELECT ... FROM` does not.
SQL_STATEMENT_PATTERNS = (
    r"\bselect\b[\s\S]{0,200}?\bfrom\b",
    r"\binsert\s+into\b",
    r"\bupdate\b[\s\S]{0,200}?\bset\b",
    r"\bdelete\s+from\b",
    r"\bupsert\s+into\b",
    r"\b(drop|alter|truncate)\s+(table|view|index|database)\b",
    r"\b(grant|revoke)\s+\w+\s+on\b",
    r"\bunion\s+(all\s+)?select\b",
    r"--\s*$",
    r";\s*(select|insert|update|delete|drop)\b",
)

PACKAGE_ROOT = pathlib.Path(provenance_contracts.__file__).parent


def _string_constants(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string literal except docstrings."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_canonical_table_name_appears_in_any_contract_literal():
    offences: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, text in _string_constants(tree):
            tokens = {t.strip(" .,;:()[]{}\"'").lower() for t in text.split()}
            hits = tokens & CANONICAL_TABLES
            if hits:
                offences.append(f"{path.name}:{lineno} -> {sorted(hits)} in {text!r}")
    assert not offences, "canonical table names found in contracts:\n" + "\n".join(offences)


def test_no_sql_statement_appears_in_any_contract_literal():
    offences: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, text in _string_constants(tree):
            lowered = text.lower()
            for pattern in SQL_STATEMENT_PATTERNS:
                if re.search(pattern, lowered, flags=re.MULTILINE):
                    offences.append(f"{path.name}:{lineno} -> {pattern} in {text!r}")
    assert not offences, "SQL statement shapes found in contracts:\n" + "\n".join(offences)


def test_agent_safe_view_names_live_in_the_domain_package_not_here():
    """Carve-out, stated deliberately.

    `AgentSafeView` names read-model views and is defined in
    `provenance_domain.enums`, where it functions as an allowlist rather
    than an instruction. The contracts package may reference the enum but
    must not hard-code a view name as a literal.
    """
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for literal in ('"agent_', "'agent_"):
            assert literal not in source, f"{path.name} hard-codes a view name"
```

---

## 21. Where each contract is produced and consumed

| Contract | Produced by | Consumed by | Persisted as |
|---|---|---|---|
| `Principal` | control plane auth module | every public API handler | not persisted |
| `InternalPrincipal` + `CapabilityBinding` | control plane auth module | internal API handlers, Kernel | binding derives from `agent_runs` / `action_intents` |
| `ArtifactMetadata` | ingestion coordinator | Interpreter `load_artifact_metadata` | `source_artifacts` |
| `ContentBlock` / `NormalizedContent` | parser | Interpreter `load_normalized_content` | S3 parser snapshot; not canonical |
| `ExtractionResult` | Tier E model | `validate_extraction_schema`, proposal builder | `agent_runs` metadata only |
| `IdentityCandidate` | retrieval engine | `route_resolution_need`, resolver | not persisted |
| `RetrievalContext` | retrieval engine | Interpreter, resolver, Memory Trace | not persisted |
| `ResolutionAssessment` | Tier R model | proposal builder | `agent_runs` metadata only |
| `MemoryProposal` | proposal builder | Memory Kernel | `memory_proposals.payload` |
| `KernelCommitResult` | Memory Kernel | agent runtime, API, EventBridge routing | `kernel_decisions` |
| `StateProof` | State Proof builder | Advocate, UI, Judge Mode | not persisted; hash bound to actions |
| `DomainEvent` | Memory Kernel (in transaction) | outbox dispatcher, EventBridge, workers | `outbox_events` |
| `DraftAction` | Tier R model via Advocate | `validate_draft_claims`, UI, executor | `action_intents.draft_payload` |
| `ActionIntentView` | control plane actions module | UI, executor | `action_intents` + `action_executions` |
| `TriggerWakeup` | EventBridge Scheduler → Lambda | trigger evaluator | not persisted |
| `TriggerEvaluationResult` | trigger evaluator | Kernel, Memory Trace | `prospective_triggers` + `state_transitions` |

The build dependency graph in `00_IMPLEMENTATION_MAP.md` §10 starts at "contracts/domain enums" for a reason: every row above depends on this file, and nothing in this file depends on anything else in the system.

---

## 22. Versioning and compatibility

`schema_version` is `MAJOR.MINOR` and is independent of the package version.

- **Minor bump** — adding an optional field, adding an enum member, relaxing a constraint. Old consumers keep working; `extra="forbid"` means a *new* producer sending a new field to an *old* consumer fails loudly rather than silently dropping data, which is the correct failure for a system of record.
- **Major bump** — removing or renaming a field, tightening a constraint, removing an enum member, changing a field's type. Requires adding the new major to `SUPPORTED_SCHEMA_MAJORS`, a migration for any persisted payload (`memory_proposals.payload`, `outbox_events.payload`, `action_intents.draft_payload`), and a deploy order of consumers before producers.

Three payload columns hold serialised contracts and therefore survive deploys. Any major bump must ship a reader that can still parse `1.x` rows, because `belief_versions` lineage and `outbox_events` history are append-only and are never rewritten.

`EXTRACTION_SCHEMA_VERSION` versions independently of `SCHEMA_VERSION`, because prompt and extraction-schema churn is expected to be far faster than boundary-contract churn. It is recorded on every `agent_runs` row so an evaluation regression can be attributed to a schema change rather than a model change.

---

## 23. Risks and decided posture

**1. The float-money guard is strongest in Python and weakest in JSON. (Highest-severity risk in this document.)**
`Money._reject_float_amount` reliably rejects a Python `float`. For `model_validate_json`, whether pydantic-core hands the validator a `float` or a pre-converted `Decimal` for a bare JSON number is a version-dependent implementation detail we have not pinned to a specific pydantic-core release. The mitigation is layered — `json_schema_extra={"type": "string"}` constrains Bedrock structured output and the generated OpenAPI schema, so the *producers* are told to emit strings — but a hand-written client posting `{"amount": 186.00}` might be silently accepted as `Decimal("186.0")` on some pydantic builds. **Required before the first commit of real money data:** a test that asserts the exact behaviour of `Money.model_validate_json('{"amount": 186.00, "currency": "USD"}')` on the pinned pydantic version, and, if it does not raise, an explicit `strict=True` on `Money` plus a re-run of the whole suite. Do not assume the guard holds; measure it.

**2. `extra="forbid"` makes rolling-deploy order load-bearing.**
A producer deployed with a new optional field will be rejected outright by a consumer still on the old build. That is the right trade for a system of record — silently discarded fields are far worse. **Decision:** deploy tolerant consumers before producers, retain parsers for the previous schema major, and require `G13.9` to run the immediately previous application image against the head schema and contract fixtures before deployment.

**3. `FORBIDDEN_OUTBOUND_TERMS` is a lexicon, and lexicons are brittle in both directions.**
It will not catch a model that paraphrases ("our records show a numeric certainty of 0.95"), and it can produce a false positive on a legitimate sentence — an outbound message about a genuine cloud-hosting dispute would trip on `bedrock`. It is a backstop behind the Advocate prompt, not a control. If false positives appear in evaluation, the correct fix is to narrow the list, not to delete the check.

**4. The no-SQL lint is a structural check, not a taint analysis.**
It proves the contracts *package source* names no canonical table and contains no SQL statement shape. It says nothing about runtime string content — a user could legitimately write "please check my claims" in a free-text field, and should be able to. Preventing SQL injection is the job of parameterised queries in `provenance_db`; this test only prevents a coding agent from quietly reintroducing SQL into the contract layer. The `AgentSafeView` carve-out is deliberate and documented; it is an allowlist of read-model views living in `provenance_domain`, and a reviewer should push back if that enum ever grows a name that is not a view.

**5. `SUPERSEDED` inbound edges on the case machine are deliberately broad.**
**Decision:** every non-superseded case status may transition to terminal `SUPERSEDED`, but only through a typed `CASE_MERGE` proposal naming a surviving case in the same tenant, user, and relationship. The surviving case must itself be non-superseded. This is the frozen v1 merge rule reflected in `CASE_TRANSITIONS` and `12_KERNEL_ALGORITHMS.md` §5; narrowing it later is a versioned contract change, not an implementation preference.

**6. `ResolutionAssessment._low_confidence_must_escalate` can make the resolver's output unconstructible.**
If the Tier R model returns `requires_human_review=False` alongside an unresolved question, validation fails and the node consumes its single repair attempt. That is the intended behaviour — abstain rather than commit wrongly — but it converts a soft quality problem into a hard graph failure. The `PENDING_HUMAN_REVIEW` path must therefore be genuinely reachable from a validation failure, not only from a well-formed low-confidence assessment. This is a graph-wiring obligation that this document specifies but cannot enforce.

**7. Frozen contracts holding `Mapping` fields are not hashable.**
`ConfigDict(frozen=True)` generates `__hash__`, but `DomainEvent.payload` and any `JsonValue` dict field will raise `TypeError` if someone actually calls `hash()` on the model or puts it in a set. Use `content_hash()` instead. This is a latent papercut rather than a correctness problem, and it is called out so nobody debugs it twice.

**8. `Weight` is an alias of `Confidence`, so mypy will not catch swapping them.**
They share a domain and a coercion path. If the two ever need to diverge — for example if edge weights become signed — this must become a distinct `Annotated` type rather than an alias, and every `belief_support.weight` value needs revisiting.

**9. `derive_commitment_status` has one behaviour we should watch in the demo.**
An `ACTIVE` commitment with `committed=0` never becomes `FULFILLED`, because the `committed > 0` guard exists to stop a zero-value placeholder from auto-settling. Non-monetary commitments (`SERVICE_TERMINATION`, `DOCUMENT_DELIVERY`) carry no amount at all and therefore need an explicit status transition rather than a derived one. The Kernel must special-case them; this document defines the arithmetic path only.

**10. `MAX_EVENT_PAYLOAD_BYTES` at 16 KiB is a guess, not a measurement.**
It is far below the EventBridge 256 KB entry limit and comfortably above any payload we currently emit, but no event has yet been measured against it. If a `belief.changed.v1` payload with a large `value_json` approaches the cap, the correct fix is to shrink the payload to ids, not to raise the constant.
