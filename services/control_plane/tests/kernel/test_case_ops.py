"""T4.7 - case transition legality, guard G1, and the aggregate revision rule.

Authority: ``specs/12_KERNEL_ALGORITHMS.md`` section 5 (the matrix, G1's five
conjuncts, rule C1) and section 6 (rules R1-R7).

The matrix is asserted cell by cell against the printed grid rather than
against the implementation's own table, because a state machine that agrees
with itself proves nothing. ``provenance_domain.transitions.CASE_MACHINE`` is
wrapped, never re-implemented: a second copy of the legality table is a second
source of truth about what a case may do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from provenance_domain.enums import (
    AttentionLevel,
    CaseReopenReasonCode,
    CaseStatus,
    ConflictSeverity,
    KernelReasonCode,
)
from provenance_domain.invariants import InvariantViolation, assert_revision_increment
from services.control_plane.app.memory_kernel import case_ops
from services.control_plane.app.memory_kernel.config import KernelConfig

pytestmark = pytest.mark.unit

RESOLVED_AT = datetime(2026, 6, 2, 13, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)
EARLIER = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)

CASE_ID = uuid.UUID(int=0x2001)
TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
EV_NEW = uuid.UUID(int=0x6101)
EV_OLD = uuid.UUID(int=0x6102)

#: ``specs/12_KERNEL_ALGORITHMS.md`` section 5.1, transcribed from the printed
#: grid. "Y" legal, "G1"/"G2" guarded, absent means illegal. Every state of the
#: 10x10 table is present, including the diagonal.
GRID: dict[str, dict[str, str]] = {
    "OPEN": {
        "WAITING": "Y",
        "ACTIONABLE": "Y",
        "DISPUTED": "Y",
        "BLOCKED": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "WAITING": {
        "ACTIONABLE": "Y",
        "DISPUTED": "Y",
        "BLOCKED": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "ACTIONABLE": {
        "IN_PROGRESS": "Y",
        "AWAITING_USER": "Y",
        "DISPUTED": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "IN_PROGRESS": {
        "WAITING": "Y",
        "ACTIONABLE": "Y",
        "DISPUTED": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "DISPUTED": {
        "WAITING": "Y",
        "ACTIONABLE": "Y",
        "AWAITING_USER": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "BLOCKED": {"WAITING": "Y", "ACTIONABLE": "Y", "RESOLVED": "Y", "SUPERSEDED": "G2"},
    "AWAITING_USER": {
        "ACTIONABLE": "Y",
        "IN_PROGRESS": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "RESOLVED": {"REOPENED": "G1", "SUPERSEDED": "G2"},
    "REOPENED": {
        "WAITING": "Y",
        "ACTIONABLE": "Y",
        "DISPUTED": "Y",
        "RESOLVED": "Y",
        "SUPERSEDED": "G2",
    },
    "SUPERSEDED": {},
}

ALL_STATES = tuple(GRID)


def _case(
    *,
    status: CaseStatus = CaseStatus.RESOLVED,
    revision: int = 12,
    reopened_count: int = 0,
    resolved_at: datetime | None = RESOLVED_AT,
    attention: AttentionLevel = AttentionLevel.NONE,
) -> case_ops.CaseRow:
    return case_ops.CaseRow(
        case_id=CASE_ID,
        tenant_id=TENANT,
        user_id=USER,
        status=status,
        revision=revision,
        reopened_count=reopened_count,
        resolved_at=resolved_at,
        attention_level=attention,
    )


def _snapshot(
    *,
    linked: frozenset[uuid.UUID] = frozenset({EV_OLD}),
    hashes: frozenset[str] = frozenset({"aa" * 32}),
    created_at: datetime = LATER,
) -> case_ops.CaseSnapshot:
    return case_ops.CaseSnapshot(
        evidence_ids_linked_to_case=linked,
        artifact_hashes_linked_to_case=hashes,
        evidence={
            EV_NEW: case_ops.EvidenceRecord(evidence_id=EV_NEW, created_at=created_at),
            EV_OLD: case_ops.EvidenceRecord(evidence_id=EV_OLD, created_at=EARLIER),
        },
    )


def _hero_basis() -> case_ops.ReopenBasis:
    """The hero: new evidence and one HIGH conflict on this case."""
    return case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        artifact_hashes=("9f" * 32,),
        conflicts=(case_ops.ConflictSignal(case_id=CASE_ID, severity=ConflictSeverity.HIGH),),
    )


# ---------------------------------------------------------------------------
# 5.1 - the matrix, cell by cell
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("frm", ALL_STATES)
@pytest.mark.parametrize("to", ALL_STATES)
def test_every_cell_of_the_printed_matrix(frm: str, to: str) -> None:
    """The wrapper agrees with section 5.1's grid in all 100 cells."""
    expected = GRID[frm].get(to)
    verdict = case_ops.case_transition_verdict(frm, to)
    if expected is None:
        assert not verdict, f"{frm} -> {to} is illegal in section 5.1 but was reported legal"
        return
    if expected == "Y":
        assert verdict, f"{frm} -> {to} is 'Y' in section 5.1 but was refused"
        assert case_ops.guard_code(frm, to) is None
    else:
        assert not verdict, f"{frm} -> {to} is {expected} and needs its guard's reason code"
        assert case_ops.guard_code(frm, to) == expected


def test_a_self_transition_is_never_a_transition() -> None:
    """Section 5.1: a status that does not change is not a transition and must
    not consume a revision."""
    for state in ALL_STATES:
        assert not case_ops.case_transition_verdict(state, state)


def test_superseded_is_terminal() -> None:
    for state in ALL_STATES:
        assert not case_ops.case_transition_verdict(CaseStatus.SUPERSEDED, state)


def test_resolved_to_reopened_needs_a_reopen_reason_code() -> None:
    refused = case_ops.case_transition_verdict(CaseStatus.RESOLVED, CaseStatus.REOPENED)
    assert not refused
    assert refused.guard == "G1"
    allowed = case_ops.case_transition_verdict(
        CaseStatus.RESOLVED,
        CaseStatus.REOPENED,
        reason_code=str(CaseReopenReasonCode.CONTRADICTORY_EVIDENCE),
    )
    assert allowed


def test_an_invented_reopen_reason_code_is_refused() -> None:
    """``CONTRADICTORY_EVIDENCE_ADMITTED`` is not a member and never was."""
    assert not case_ops.case_transition_verdict(
        CaseStatus.RESOLVED, CaseStatus.REOPENED, reason_code="CONTRADICTORY_EVIDENCE_ADMITTED"
    )


# ---------------------------------------------------------------------------
# 5.3 - the five conjuncts of G1
# ---------------------------------------------------------------------------


def test_g1_passes_for_the_hero() -> None:
    verdict = case_ops.qualifies_for_reopen(_case(), _hero_basis(), _snapshot())
    assert verdict.qualifies
    assert verdict.reason_code is KernelReasonCode.CASE_REOPENED_QUALIFYING_EVIDENCE
    assert verdict.attention_level is AttentionLevel.URGENT


def test_q1_refuses_when_no_evidence_is_new() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_OLD,),
        conflicts=(case_ops.ConflictSignal(case_id=CASE_ID, severity=ConflictSeverity.HIGH),),
    )
    verdict = case_ops.qualifies_for_reopen(_case(), basis, _snapshot())
    assert not verdict.qualifies
    assert verdict.reason_code is KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING
    assert verdict.test_failed == "Q1"


def test_q2_refuses_evidence_recorded_before_the_case_was_resolved() -> None:
    """Re-importing an artifact we already had must never reopen anything."""
    verdict = case_ops.qualifies_for_reopen(_case(), _hero_basis(), _snapshot(created_at=EARLIER))
    assert not verdict.qualifies
    assert verdict.test_failed == "Q2"


def test_q2_admits_late_arriving_valid_time() -> None:
    """Rule T4: valid time may be old; only record time must be fresh."""
    verdict = case_ops.qualifies_for_reopen(
        _case(), _hero_basis(), _snapshot(created_at=RESOLVED_AT + timedelta(seconds=1))
    )
    assert verdict.qualifies


def test_q3_refuses_the_marketing_email() -> None:
    """New evidence, recorded after resolution, that did nothing canonical.

    Section 5.3's named negative control. Without Q3 this scenario reopens the
    case and the product looks broken in the first thirty seconds.
    """
    basis = case_ops.ReopenBasis(evidence_ids=(EV_NEW,))
    verdict = case_ops.qualifies_for_reopen(_case(), basis, _snapshot())
    assert not verdict.qualifies
    assert verdict.reason_code is KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING
    assert verdict.test_failed == "Q3"


def test_q3_a_low_severity_conflict_is_not_material() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        conflicts=(case_ops.ConflictSignal(case_id=CASE_ID, severity=ConflictSeverity.LOW),),
    )
    assert not case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


def test_q3_a_conflict_on_another_case_is_not_material() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        conflicts=(
            case_ops.ConflictSignal(case_id=uuid.UUID(int=0x2999), severity=ConflictSeverity.HIGH),
        ),
    )
    assert not case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


@pytest.mark.parametrize(
    ("before", "after"),
    [("FULFILLED", "ACTIVE"), ("FULFILLED", "PARTIAL"), ("EXPIRED", "DISPUTED")],
)
def test_q3_branch_b_a_reversed_commitment_is_material(before: str, after: str) -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        commitment_deltas=(case_ops.CommitmentSignal(status_before=before, status_after=after),),
    )
    assert case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


def test_q3_branch_c_a_fired_trigger_is_material() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        trigger_deltas=(case_ops.TriggerSignal(new_state="FIRED", predicate_result=True),),
    )
    assert case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


def test_q3_branch_c_a_trigger_whose_predicate_is_false_is_not() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        trigger_deltas=(case_ops.TriggerSignal(new_state="FIRED", predicate_result=False),),
    )
    assert not case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


def test_q3_branch_d_a_user_dispute_is_material() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        claims=(case_ops.ClaimSignal(claim_kind="USER_CLAIM", disputes_case_belief=True),),
    )
    assert case_ops.qualifies_for_reopen(_case(), basis, _snapshot()).qualifies


def test_q4_refuses_an_artifact_already_linked_to_this_case() -> None:
    basis = case_ops.ReopenBasis(
        evidence_ids=(EV_NEW,),
        artifact_hashes=("aa" * 32,),
        conflicts=(case_ops.ConflictSignal(case_id=CASE_ID, severity=ConflictSeverity.HIGH),),
    )
    verdict = case_ops.qualifies_for_reopen(_case(), basis, _snapshot())
    assert not verdict.qualifies
    assert verdict.reason_code is KernelReasonCode.ARTIFACT_CONTENT_DUPLICATE
    assert verdict.test_failed == "Q4"


def test_q5_the_flapping_guard_asks_for_a_person() -> None:
    """A case that has reopened five times needs a person, not a sixth reopen."""
    cfg = KernelConfig()
    verdict = case_ops.qualifies_for_reopen(
        _case(reopened_count=cfg.max_reopens), _hero_basis(), _snapshot(), cfg
    )
    assert not verdict.qualifies
    assert verdict.reason_code is KernelReasonCode.CASE_REOPEN_LIMIT_REACHED
    assert verdict.attention_level is AttentionLevel.ATTENTION


def test_q5_does_not_fire_one_reopen_early() -> None:
    cfg = KernelConfig()
    assert case_ops.qualifies_for_reopen(
        _case(reopened_count=cfg.max_reopens - 1), _hero_basis(), _snapshot(), cfg
    ).qualifies


def test_a_case_that_was_never_resolved_cannot_pass_q2() -> None:
    verdict = case_ops.qualifies_for_reopen(
        _case(status=CaseStatus.OPEN, resolved_at=None), _hero_basis(), _snapshot()
    )
    assert not verdict.qualifies


# ---------------------------------------------------------------------------
# Rule C1 and section 6 - one transition, one increment
# ---------------------------------------------------------------------------


def test_c1_two_requested_transitions_in_one_commit_are_refused() -> None:
    with pytest.raises(case_ops.MultipleTransitionsError) as excinfo:
        case_ops.plan_case_update(
            _case(status=CaseStatus.OPEN, resolved_at=None),
            requested=[CaseStatus.ACTIONABLE, CaseStatus.DISPUTED],
            changed=True,
        )
    assert excinfo.value.code is KernelReasonCode.CASE_TRANSITION_MULTIPLE_IN_COMMIT


def test_r1_a_canonical_commit_increments_exactly_once() -> None:
    assert case_ops.revision_after(12, changed=True) == 13


def test_r2_a_noop_does_not_increment() -> None:
    assert case_ops.revision_after(12, changed=False) == 12


def test_the_revision_rule_is_the_domain_invariant_not_a_second_copy() -> None:
    """A no-op result that claimed a canonical change would be caught here."""
    with pytest.raises(InvariantViolation):
        assert_revision_increment(12, case_ops.revision_after(12, changed=False), changed=True)


def test_the_hero_case_update_is_the_canon_shape() -> None:
    """``CANONICAL_DECISIONS.md`` -> hero commit: 12 -> 13, REOPENED, count 1."""
    update = case_ops.plan_case_update(
        _case(),
        requested=[CaseStatus.REOPENED],
        reason_code=str(CaseReopenReasonCode.CONTRADICTORY_EVIDENCE),
        basis=_hero_basis(),
        snapshot=_snapshot(),
        changed=True,
    )
    assert update.status_before is CaseStatus.RESOLVED
    assert update.status_after is CaseStatus.REOPENED
    assert (update.revision_before, update.revision_after) == (12, 13)
    assert update.reopen_delta == 1
    assert update.attention_after is AttentionLevel.URGENT
    assert update.reason_code == "CONTRADICTORY_EVIDENCE"


def test_reopen_does_not_clear_resolved_at() -> None:
    """When the case was previously resolved is a historical fact, and Q2
    depends on it."""
    update = case_ops.plan_case_update(
        _case(),
        requested=[CaseStatus.REOPENED],
        reason_code=str(CaseReopenReasonCode.CONTRADICTORY_EVIDENCE),
        basis=_hero_basis(),
        snapshot=_snapshot(),
        changed=True,
    )
    assert update.resolved_at == RESOLVED_AT


def test_a_refused_reopen_keeps_the_status_and_the_revision_still_moves() -> None:
    """Q5 withholds the transition only. The claim, the conflict and the belief
    version are still written, so the commit is still canonical."""
    cfg = KernelConfig()
    update = case_ops.plan_case_update(
        _case(reopened_count=cfg.max_reopens),
        requested=[CaseStatus.REOPENED],
        reason_code=str(CaseReopenReasonCode.CONTRADICTORY_EVIDENCE),
        basis=_hero_basis(),
        snapshot=_snapshot(),
        changed=True,
        cfg=cfg,
    )
    assert update.status_after is CaseStatus.RESOLVED
    assert update.reopen_delta == 0
    assert update.attention_after is AttentionLevel.ATTENTION
    assert update.revision_after == 13


def test_an_illegal_transition_is_refused_by_reason_code() -> None:
    with pytest.raises(case_ops.IllegalCaseTransitionError) as excinfo:
        case_ops.plan_case_update(
            _case(status=CaseStatus.SUPERSEDED, resolved_at=None),
            requested=[CaseStatus.ACTIONABLE],
            changed=True,
        )
    assert excinfo.value.code is KernelReasonCode.CASE_TRANSITION_ILLEGAL


# ---------------------------------------------------------------------------
# R4 - the optimistic predicate
# ---------------------------------------------------------------------------


def test_the_update_carries_the_optimistic_revision_predicate() -> None:
    """Rule R4: redundant under SERIALIZABLE, required anyway. It converts a
    subtle isolation regression into a loud OPTIMISTIC_REVISION_MISMATCH."""
    sql = " ".join(case_ops.CASE_UPDATE_SQL.split())
    assert "UPDATE cases" in sql
    assert "revision = revision + 1" in sql
    assert "AND revision = %(revision_before)s" in sql
    assert "tenant_id = %(tenant_id)s" in sql
    assert "user_id = %(user_id)s" in sql


def test_the_update_never_clears_resolved_at_unconditionally() -> None:
    assert "resolved_at = NULL" not in case_ops.CASE_UPDATE_SQL
