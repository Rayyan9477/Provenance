"""The ``kernel_decisions`` ledger and the receipt built from it — T4.11.

Authority
---------
``specs/12_KERNEL_ALGORITHMS.md`` section 9, ``quality/23_PHASE_GATES.md``
section 23.8 and gates ``G4.4``/``G4.5``, and ``specs/10_DATABASE_DDL.md``
section 8.2 for the columns and every CHECK this module duplicates in Python.

The rule this module exists to make true
----------------------------------------
**A row for every outcome, including the ones nobody wants to look at.** A
rejection with no ledger row is a refusal nobody can audit; a NOOP with no
reason code is what section 23.8 calls a gate failure. So
:func:`build_decision_row` refuses to construct a row without a reason code,
and there is no code path that skips the insert.

Two columns carry the gates
---------------------------
``transaction_opened`` answers ``G4.4``: a preflight refusal happens before any
write intent exists and may not claim a transaction, while an accepted commit
necessarily opened one and may not deny it. ``committed_at`` plus an externally
read ``cases.revision`` delta is what distinguishes a commit from an
in-transaction read-back.

Why the CHECKs are duplicated in Python
---------------------------------------
``ck_kernel_decisions_noop_no_bump``, ``ck_kernel_decisions_revision_step``,
``ck_kernel_decisions_retry`` and ``ck_kernel_decisions_commit_ts`` are the
database's last word. Duplicating them here does not weaken them; it makes the
failure legible in application terms - ``NOOP_DUPLICATE moved the revision`` -
instead of arriving as a raw SQLSTATE ``23514`` with a rendered expression and
no constraint name in the message.

Stdlib plus ``provenance_contracts`` and ``provenance_domain``. No
``provenance_db``: this module builds the row and the receipt; ``transaction.py``
executes the INSERT.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from provenance_contracts.kernel import (
    PREFLIGHT_DECISIONS,
    BeliefVersionRef,
    CommitmentChange,
    ConflictRef,
    KernelCommitResult,
    StateTransitionRef,
    TriggerChange,
)
from provenance_domain import money
from provenance_domain.enums import (
    ACCEPTING_KERNEL_DECISIONS,
    DECISION_TO_PROPOSAL_STATUS,
    AttentionLevel,
    CaseStatus,
    KernelDecision,
    KernelReasonCode,
)

__all__ = [
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "DECISION_INSERT_SQL",
    "CommitEffects",
    "proposal_status_for",
    "MAX_RETRY_COUNT",
    "NON_BUMPING_DECISIONS",
    "DecisionRow",
    "build_decision_row",
    "result_from_row",
    "retry_exhausted_row",
]

#: ``ck_kernel_decisions_retry``: ``retry_count >= 0 AND retry_count <= 5``.
#: Note that ``KernelCommitResult.retry_count`` allows up to 10 - the column is
#: the tighter of the two, so the column's bound is the one enforced here.
MAX_RETRY_COUNT: Final[int] = 5

#: ``ck_kernel_decisions_noop_no_bump``: these outcomes may not move the case
#: revision. ``RETRYABLE_CONCURRENCY`` is absent from the column's list because
#: it rolled back and reports no revision at all.
NON_BUMPING_DECISIONS: Final[frozenset[KernelDecision]] = frozenset(
    {
        KernelDecision.NOOP_DUPLICATE,
        KernelDecision.REJECTED_INVALID_PROVENANCE,
        KernelDecision.REJECTED_INVARIANT,
        KernelDecision.REJECTED_SCHEMA,
        KernelDecision.PENDING_IDENTITY,
        KernelDecision.PENDING_HUMAN_REVIEW,
        KernelDecision.RETRYABLE_CONCURRENCY,
    }
)


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """One ``kernel_decisions`` row, validated before it reaches the database."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    proposal_id: uuid.UUID
    trace_id: uuid.UUID
    decision: KernelDecision
    reason_codes: tuple[KernelReasonCode, ...] = ()
    case_id: uuid.UUID | None = None
    case_revision_before: int | None = None
    case_revision_after: int | None = None
    retry_count: int = 0
    transaction_opened: bool = True
    committed_at: datetime | None = None
    #: Receipt-only, never a column: what the commit actually wrote.
    effects: CommitEffects = field(default_factory=lambda: CommitEffects())

    def as_params(self) -> dict[str, Any]:
        """Bind parameters for :data:`DECISION_INSERT_SQL`.

        ``reason_codes`` leaves here as a plain ``list[str]`` and is **not**
        driver-ready: ``transaction.decision_params`` wraps it in ``psycopg``'s
        ``Json`` adapter before the statement runs. Handing the list straight to
        the driver renders a Postgres *array* literal, which a ``jsonb`` column
        rejects with ``InvalidTextRepresentation``; and
        ``ck_kernel_decisions_reason_codes`` further requires
        ``jsonb_typeof(reason_codes) = 'array'``, so a bare string would satisfy
        the NOT NULL while failing the CHECK. The wrapping is not done here
        because ``Json`` is a ``psycopg`` symbol and this module is inside the
        import-linter contract that keeps the decision modules driver-free.
        """
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "proposal_id": self.proposal_id,
            "case_id": self.case_id,
            "decision": str(self.decision),
            "reason_codes": [str(code) for code in self.reason_codes],
            "case_revision_before": self.case_revision_before,
            "case_revision_after": self.case_revision_after,
            "retry_count": self.retry_count,
            "transaction_opened": self.transaction_opened,
            "trace_id": self.trace_id,
            "committed_at": self.committed_at,
        }


@dataclass(frozen=True, slots=True)
class CommitEffects:
    """The receipt lines a commit produced. Empty for every other outcome."""

    claim_ids: tuple[uuid.UUID, ...] = ()
    belief_versions: tuple[BeliefVersionRef, ...] = ()
    conflicts: tuple[ConflictRef, ...] = ()
    commitment_changes: tuple[CommitmentChange, ...] = ()
    trigger_changes: tuple[TriggerChange, ...] = ()
    state_transitions: tuple[StateTransitionRef, ...] = ()
    outbox_event_ids: tuple[uuid.UUID, ...] = ()
    case_status_after: CaseStatus | None = None
    attention_level_after: AttentionLevel | None = None
    attention_required: bool = False


#: No ``ON CONFLICT``. ``uq_kernel_decisions_terminal_per_proposal`` is the
#: replay guard, and an upsert here would silently overwrite the first decision
#: for a proposal - which is precisely the audit record rule R6 depends on.
DECISION_INSERT_SQL: Final[str] = """
INSERT INTO kernel_decisions (
    id, tenant_id, user_id, proposal_id, case_id, decision, reason_codes,
    case_revision_before, case_revision_after, retry_count, transaction_opened,
    trace_id, committed_at, created_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(proposal_id)s, %(case_id)s, %(decision)s,
    %(reason_codes)s, %(case_revision_before)s, %(case_revision_after)s,
    %(retry_count)s, %(transaction_opened)s, %(trace_id)s, %(committed_at)s, now()
)
"""


def proposal_status_for(decision: KernelDecision) -> str:
    """The ``memory_proposals.status`` value one decision implies.

    Read from ``DECISION_TO_PROPOSAL_STATUS`` rather than spelled again:
    ``RETRYABLE_CONCURRENCY`` maps to ``SUBMITTED`` precisely because the caller
    re-drives, and a second copy of that mapping is where it would get written
    as ``RETRYABLE_CONCURRENCY`` and fail ``ck_memory_proposals_status``.
    """
    return str(DECISION_TO_PROPOSAL_STATUS[decision])


def build_decision_row(
    *,
    decision_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    proposal_id: uuid.UUID,
    trace_id: uuid.UUID,
    decision: KernelDecision,
    reason_codes: Sequence[KernelReasonCode] = (),
    case_id: uuid.UUID | None = None,
    case_revision_before: int | None = None,
    case_revision_after: int | None = None,
    retry_count: int = 0,
    tx_now: datetime | None = None,
    effects: CommitEffects | None = None,
) -> DecisionRow:
    """Build one validated ledger row.

    ``transaction_opened`` and ``committed_at`` are **derived** from the
    decision rather than accepted as arguments. A caller that could pass them
    could pass them wrongly, and ``G4.4`` audits exactly those two fields.
    """
    codes = tuple(dict.fromkeys(reason_codes))
    if not codes:
        raise ValueError(
            f"{decision} was built with no reason code; audit is not optional "
            "(23_PHASE_GATES.md section 23.8) and the column is NOT NULL"
        )
    if not 0 <= retry_count <= MAX_RETRY_COUNT:
        raise ValueError(
            f"retry_count {retry_count} is outside 0..{MAX_RETRY_COUNT} "
            "(ck_kernel_decisions_retry)"
        )

    accepting = decision in ACCEPTING_KERNEL_DECISIONS
    opened = decision not in PREFLIGHT_DECISIONS
    committed_at = tx_now if accepting else None
    if accepting and committed_at is None:
        raise ValueError(f"{decision} must record committed_at; a commit has a commit time")

    _validate_revision(decision, case_revision_before, case_revision_after)

    return DecisionRow(
        id=decision_id,
        tenant_id=tenant_id,
        user_id=user_id,
        proposal_id=proposal_id,
        trace_id=trace_id,
        decision=decision,
        reason_codes=codes,
        case_id=case_id,
        case_revision_before=case_revision_before,
        case_revision_after=case_revision_after,
        retry_count=retry_count,
        transaction_opened=opened,
        committed_at=committed_at,
        effects=effects if effects is not None else CommitEffects(),
    )


def _validate_revision(decision: KernelDecision, before: int | None, after: int | None) -> None:
    if before is None or after is None:
        return
    if after not in (before, before + 1):
        raise ValueError(
            f"case revision may move by 0 or 1, not {after - before} "
            "(ck_kernel_decisions_revision_step)"
        )
    if decision in NON_BUMPING_DECISIONS and after != before:
        raise ValueError(
            f"{decision} moved the case revision {before} -> {after}; a refusal and "
            "a no-op leave the aggregate untouched (ck_kernel_decisions_noop_no_bump)"
        )
    if decision in ACCEPTING_KERNEL_DECISIONS and after != before + 1:
        raise ValueError(
            f"{decision} left the case revision at {after}; a canonical commit "
            "advances it by exactly one (rule R1)"
        )


def retry_exhausted_row(
    *,
    decision_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    proposal_id: uuid.UUID,
    trace_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    attempts: int,
) -> DecisionRow:
    """The terminal no-side-effect outcome of the retry loop.

    ``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*: the Kernel performs
    no side effect and enqueues nothing. There is no kernel retry queue, the
    control plane holds no ``sqs:*`` permission, and re-drive is the caller's
    job over ``503`` + ``Retry-After``. The proposal stays ``SUBMITTED``.
    """
    return build_decision_row(
        decision_id=decision_id,
        tenant_id=tenant_id,
        user_id=user_id,
        proposal_id=proposal_id,
        trace_id=trace_id,
        decision=KernelDecision.RETRYABLE_CONCURRENCY,
        reason_codes=(
            KernelReasonCode.RETRYABLE_CONCURRENCY,
            KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED,
        ),
        case_id=case_id,
        retry_count=min(max(attempts - 1, 1), MAX_RETRY_COUNT),
    )


def result_from_row(
    row: DecisionRow, *, attention_required: bool | None = None
) -> KernelCommitResult:
    """Build the caller's receipt **from the persisted row**.

    T4.11's fourth sub-task, and it is not a stylistic preference: populating
    the receipt from in-memory state lets the caller see what was intended
    rather than what was written, and the two differ exactly when something has
    gone wrong.
    """
    effects = row.effects
    accepting = row.decision in ACCEPTING_KERNEL_DECISIONS
    attention = effects.attention_required if attention_required is None else attention_required
    return KernelCommitResult(
        decision=row.decision,
        proposal_id=row.proposal_id,
        kernel_decision_id=row.id,
        proposal_status=DECISION_TO_PROPOSAL_STATUS[row.decision],
        trace_id=row.trace_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        case_id=row.case_id,
        case_status_after=effects.case_status_after if accepting else None,
        case_revision_before=row.case_revision_before if accepting else None,
        case_revision_after=row.case_revision_after if accepting else None,
        attention_level_after=effects.attention_level_after if accepting else None,
        created_claim_ids=effects.claim_ids,
        created_belief_versions=effects.belief_versions,
        created_or_updated_conflicts=effects.conflicts,
        commitment_changes=effects.commitment_changes,
        trigger_changes=effects.trigger_changes,
        state_transitions=effects.state_transitions,
        outbox_event_ids=effects.outbox_event_ids,
        attention_required=attention,
        reason_codes=row.reason_codes,
        retry_count=row.retry_count,
        transaction_opened=row.transaction_opened,
        committed_at=row.committed_at,
    )


# ---------------------------------------------------------------------------
# The PV_SABOTAGE hook — T4.13
#
# `build_decision_row` is the object gate `G4.4` reads. It is the single place
# that refuses a row with no reason code, derives `transaction_opened` from the
# decision rather than accepting it from a caller, and enforces
# `ck_kernel_decisions_revision_step` and `ck_kernel_decisions_noop_no_bump` in
# application terms before the database has to.
#
# `transaction.py` reaches it as `decisions.build_decision_row` -- through the
# module object, never a `from`-import -- so the rebind installed here is
# visible at every production call site. That is the whole reason this entry can
# be trusted; a `from`-import would copy the reference at import time and the
# sabotage would silently never arrive.
#
# Neutered to the identity it returns the first keyword value it is handed (the
# decision id, a `UUID`) instead of a validated `DecisionRow`. Every assertion
# about what the ledger recorded then has nothing to read, which is the point:
# if those tests can pass without the builder, they were not testing the ledger.
# ---------------------------------------------------------------------------

SABOTAGE_MODULE: Final[str] = "memory_kernel.decisions"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("build_decision_row",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
