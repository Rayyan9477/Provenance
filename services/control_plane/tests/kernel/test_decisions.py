"""T4.11 - the ``kernel_decisions`` ledger and ``KernelCommitResult``.

Authority: ``specs/12_KERNEL_ALGORITHMS.md`` section 9, ``23_PHASE_GATES.md``
section 23.8 and ``G4.4``/``G4.5``, ``specs/10_DATABASE_DDL.md`` section 8.2 for
the columns and their CHECK constraints.

The rule this module exists to make true: **a row for every outcome**. A
rejection that leaves no ledger row is a refusal nobody can audit, and a NOOP
without a reason code is the shape section 23.8 calls a gate failure. The
receipt is then built *from the persisted row*, so the caller sees what was
written rather than what was intended.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from provenance_contracts.kernel import ConflictRef, KernelCommitResult
from provenance_domain.enums import (
    ACCEPTING_KERNEL_DECISIONS,
    DECISION_TO_PROPOSAL_STATUS,
    ConflictStatus,
    ConflictType,
    KernelDecision,
    KernelReasonCode,
    ProposalStatus,
)
from services.control_plane.app.memory_kernel import decisions

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
PROPOSAL = uuid.UUID(int=0x9001)
TRACE = uuid.UUID(int=0x9002)
CASE = uuid.UUID(int=0x2001)
DECISION_ID = uuid.UUID(int=0x9003)
TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


def _effects(decision: KernelDecision) -> decisions.CommitEffects:
    """The receipt lines an accepting decision must carry.

    ``KernelCommitResult`` refuses to describe a commit that moved the revision
    while changing nothing, and refuses ``ACCEPTED_WITH_CONFLICT`` with no
    conflict row - "a lie about what the user will see". So an accepting row
    under test carries real effects rather than an empty tuple.
    """
    if decision not in ACCEPTING_KERNEL_DECISIONS:
        return decisions.CommitEffects()
    conflicts: tuple[ConflictRef, ...] = ()
    if decision is KernelDecision.ACCEPTED_WITH_CONFLICT:
        conflicts = (
            ConflictRef(
                conflict_id=uuid.UUID(int=0x2201),
                conflict_type=ConflictType.VALUE_CONFLICT,
                status=ConflictStatus.NEEDS_HUMAN,
                predicate="balance_owed",
                requires_human=True,
                created=True,
            ),
        )
    return decisions.CommitEffects(
        claim_ids=(uuid.UUID(int=0x4101),),
        conflicts=conflicts,
        attention_required=decision is KernelDecision.ACCEPTED_WITH_CONFLICT,
    )


def _row(
    decision: KernelDecision = KernelDecision.ACCEPTED_WITH_CONFLICT,
    **kwargs: object,
) -> decisions.DecisionRow:
    params: dict[str, object] = {
        "decision_id": DECISION_ID,
        "tenant_id": TENANT,
        "user_id": USER,
        "proposal_id": PROPOSAL,
        "trace_id": TRACE,
        "decision": decision,
        "reason_codes": (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
        "case_id": CASE,
        "case_revision_before": 12,
        "case_revision_after": 13,
        "tx_now": TX_NOW,
        "effects": _effects(decision),
    }
    params.update(kwargs)
    return decisions.build_decision_row(**params)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A row for every outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", list(KernelDecision))
def test_every_decision_value_produces_a_ledger_row(decision: KernelDecision) -> None:
    """Section 23.8: audit is not optional, including for the outcomes nobody
    wants to look at."""
    row = decisions.build_decision_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        decision=decision,
        reason_codes=(KernelReasonCode.NO_CANONICAL_CHANGE,),
        case_id=CASE,
        case_revision_before=12,
        case_revision_after=13 if decision in ACCEPTING_KERNEL_DECISIONS else 12,
        tx_now=TX_NOW,
        effects=_effects(decision),
    )
    assert row.decision is decision
    assert row.reason_codes


@pytest.mark.parametrize("decision", list(KernelDecision))
def test_a_decision_row_is_never_written_without_a_reason_code(
    decision: KernelDecision,
) -> None:
    with pytest.raises(ValueError, match="reason"):
        decisions.build_decision_row(
            decision_id=DECISION_ID,
            tenant_id=TENANT,
            user_id=USER,
            proposal_id=PROPOSAL,
            trace_id=TRACE,
            decision=decision,
            reason_codes=(),
            tx_now=TX_NOW,
        )


def test_a_preflight_rejection_did_not_open_a_transaction() -> None:
    """``G4.4``, as the column the gate reads."""
    row = _row(
        KernelDecision.REJECTED_INVALID_PROVENANCE,
        reason_codes=(KernelReasonCode.EVIDENCE_FOREIGN_USER,),
        case_revision_after=12,
    )
    assert row.transaction_opened is False
    assert row.committed_at is None


def test_a_schema_rejection_did_not_open_a_transaction() -> None:
    row = _row(
        KernelDecision.REJECTED_SCHEMA,
        reason_codes=(KernelReasonCode.SCHEMA_VERSION_UNSUPPORTED,),
        case_id=None,
        case_revision_before=None,
        case_revision_after=None,
    )
    assert row.transaction_opened is False


def test_an_invariant_rejection_did_open_one_and_says_so() -> None:
    """Section 1.2: the invariant sweep is re-executed inside PHASE B, so an
    invariant rejection may legitimately have opened and rolled back."""
    row = _row(
        KernelDecision.REJECTED_INVARIANT,
        reason_codes=(KernelReasonCode.INVARIANT_BELIEF_UNGROUNDED,),
        case_revision_after=12,
    )
    assert row.transaction_opened is True
    assert row.committed_at is None


def test_an_accepted_decision_records_committed_at() -> None:
    row = _row()
    assert row.transaction_opened is True
    assert row.committed_at == TX_NOW


def test_a_noop_does_not_move_the_revision() -> None:
    """``ck_kernel_decisions_noop_no_bump``, refused in Python first so the
    failure is legible in application terms."""
    with pytest.raises(ValueError, match="NOOP_DUPLICATE"):
        _row(
            KernelDecision.NOOP_DUPLICATE,
            reason_codes=(KernelReasonCode.PROPOSAL_ALREADY_DECIDED,),
            case_revision_after=13,
        )


def test_an_accepted_decision_must_move_the_revision() -> None:
    with pytest.raises(ValueError, match="revision"):
        _row(KernelDecision.ACCEPTED, case_revision_after=12)


def test_the_revision_may_only_move_by_one() -> None:
    with pytest.raises(ValueError, match="revision"):
        _row(KernelDecision.ACCEPTED, case_revision_after=14)


def test_retry_count_is_bounded_by_the_column_check() -> None:
    """``ck_kernel_decisions_retry`` allows 0..5; a Python refusal duplicates it
    so the failure is not a raw SQLSTATE."""
    with pytest.raises(ValueError, match="retry"):
        _row(retry_count=6)


# ---------------------------------------------------------------------------
# The INSERT itself
# ---------------------------------------------------------------------------


def test_the_insert_names_the_columns_the_schema_declares() -> None:
    sql = " ".join(decisions.DECISION_INSERT_SQL.split())
    assert sql.startswith("INSERT INTO kernel_decisions")
    for column in (
        "id",
        "tenant_id",
        "user_id",
        "proposal_id",
        "case_id",
        "decision",
        "reason_codes",
        "case_revision_before",
        "case_revision_after",
        "retry_count",
        "transaction_opened",
        "trace_id",
        "committed_at",
    ):
        assert column in sql, f"{column} is missing from the ledger INSERT"


def test_the_insert_is_never_an_upsert() -> None:
    """``uq_kernel_decisions_terminal_per_proposal`` is the replay guard. An
    ON CONFLICT here would silently overwrite the first decision."""
    assert "ON CONFLICT" not in decisions.DECISION_INSERT_SQL.upper()


def test_the_row_renders_reason_codes_as_a_json_array() -> None:
    """``ck_kernel_decisions_reason_codes`` requires ``jsonb_typeof = 'array'``."""
    params = _row().as_params()
    assert params["reason_codes"] == ["CONFLICT_VALUE_MUTUAL_EXCLUSION"]
    assert params["decision"] == "ACCEPTED_WITH_CONFLICT"
    assert params["transaction_opened"] is True


# ---------------------------------------------------------------------------
# The receipt, built from the persisted row
# ---------------------------------------------------------------------------


def test_the_receipt_is_built_from_the_row() -> None:
    row = _row()
    result = decisions.result_from_row(row, attention_required=True)
    assert isinstance(result, KernelCommitResult)
    assert result.kernel_decision_id == DECISION_ID
    assert result.case_revision_before == 12
    assert result.case_revision_after == 13
    assert result.transaction_opened is True
    assert result.committed_at == TX_NOW


@pytest.mark.parametrize("decision", list(KernelDecision))
def test_the_receipt_status_follows_the_frozen_map(decision: KernelDecision) -> None:
    reason = (
        KernelReasonCode.RETRYABLE_CONCURRENCY
        if decision is KernelDecision.RETRYABLE_CONCURRENCY
        else KernelReasonCode.NO_CANONICAL_CHANGE
    )
    row = decisions.build_decision_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        decision=decision,
        reason_codes=(reason, KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED)
        if decision is KernelDecision.RETRYABLE_CONCURRENCY
        else (reason,),
        case_id=CASE,
        case_revision_before=12,
        case_revision_after=13 if decision in ACCEPTING_KERNEL_DECISIONS else 12,
        retry_count=1 if decision is KernelDecision.RETRYABLE_CONCURRENCY else 0,
        tx_now=TX_NOW,
        effects=_effects(decision),
    )
    result = decisions.result_from_row(row)
    assert result.proposal_status is DECISION_TO_PROPOSAL_STATUS[decision]


def test_retry_exhaustion_enqueues_nothing_and_commits_nothing() -> None:
    """``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*. No kernel retry
    queue exists and the control plane holds no queue-publish permission."""
    row = decisions.retry_exhausted_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        case_id=CASE,
        attempts=5,
    )
    assert row.decision is KernelDecision.RETRYABLE_CONCURRENCY
    assert row.committed_at is None
    assert KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED in row.reason_codes
    result = decisions.result_from_row(row)
    assert result.retry_exhausted
    assert result.outbox_event_ids == ()
    assert result.proposal_status is ProposalStatus.SUBMITTED


def test_a_rejection_receipt_describes_no_canonical_rows() -> None:
    row = _row(
        KernelDecision.REJECTED_INVALID_PROVENANCE,
        reason_codes=(KernelReasonCode.EVIDENCE_FOREIGN_USER,),
        case_revision_after=12,
    )
    result = decisions.result_from_row(row)
    assert result.created_claim_ids == ()
    assert result.state_transitions == ()
    assert result.committed_at is None
    assert result.transaction_opened is False
