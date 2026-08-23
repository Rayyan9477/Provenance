"""T1.2 — state-machine legality for `provenance_domain.transitions`.

Every expectation in this file is **hand-transcribed** from the specification:

* `docs/specs/12_KERNEL_ALGORITHMS.md` §5.1 — the 10x10 case-status grid, cell
  by cell, including the `G1` and `G2` guard markers.
* `docs/specs/11_CONTRACTS.md` §4.1 to §4.3 — the case, commitment, conflict,
  action, trigger, outbox, proposal and epistemic tables.
* `docs/specs/16_TRIGGER_DSL.md` §9.10 — the trigger outcome taxonomy: which
  `TriggerResult` leaves the row in which `TriggerState`.
* `docs/CANONICAL_DECISIONS.md`, *Hero commit canon* — `CONTRADICTORY_EVIDENCE`
  is the hero's reopen code, and `CONTRADICTORY_EVIDENCE_ADMITTED` /
  `RC_CONTRADICTORY_EVIDENCE` raise rather than merely reading oddly.

No table, frozenset or mapping of *expectations* is imported from
`provenance_domain.transitions`. `quality/20_TDD_STRATEGY.md` §5.1 forbids it
for this transition table by name: "Importing the production table would make
the test tautological." The production symbols that *are* imported below are
the functions under test, the machine handles they take, and — for the
immutability test alone — the table objects themselves, which are asserted
against for mutability, not read for expectations.

Every negative case here is a real bug someone would otherwise ship: a resolved
case quietly going back to work without a reason, an approved action skipping
revalidation, a settled obligation un-settling itself, an obligation marked
FULFILLED while money is still owed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from provenance_domain.transitions import (
    ACTION_MACHINE,
    CASE_MACHINE,
    CASE_TRANSITIONS,
    COMMITMENT_MACHINE,
    CONFLICT_MACHINE,
    EPISTEMIC_MACHINE,
    MACHINES,
    OUTBOX_MACHINE,
    PROPOSAL_MACHINE,
    TRIGGER_MACHINE,
    IllegalTransition,
    IllegalTransitionError,
    TransitionVerdict,
    assert_transition,
    commitment_transition,
    legal_transition,
    reachable_states,
    trigger_wake_transition,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# The 10x10 case grid, transcribed by hand from 12_KERNEL_ALGORITHMS.md §5.1.
#
#   "Y"       legal unconditionally
#   "G1"      legal only if the qualifying-evidence guard passes
#   "G2"      legal only with a case-merge reason code
#   "-"       illegal
#
# All 100 cells are written out, including the illegal ones and the diagonal,
# so that this file is a second independent copy of the grid rather than a
# sparse restatement of the interesting half of it.
# ---------------------------------------------------------------------------

CASE_STATES: tuple[str, ...] = (
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
)

CASE_GRID: dict[str, tuple[str, ...]] = {
    #                 OPEN WAIT ACTN INPR DISP BLKD AWTU RSLV ROPN SUPD
    "OPEN": ("-", "Y", "Y", "-", "Y", "Y", "-", "Y", "-", "G2"),
    "WAITING": ("-", "-", "Y", "-", "Y", "Y", "-", "Y", "-", "G2"),
    "ACTIONABLE": ("-", "-", "-", "Y", "Y", "-", "Y", "Y", "-", "G2"),
    "IN_PROGRESS": ("-", "Y", "Y", "-", "Y", "-", "-", "Y", "-", "G2"),
    "DISPUTED": ("-", "Y", "Y", "-", "-", "-", "Y", "Y", "-", "G2"),
    "BLOCKED": ("-", "Y", "Y", "-", "-", "-", "-", "Y", "-", "G2"),
    "AWAITING_USER": ("-", "-", "Y", "Y", "-", "-", "-", "Y", "-", "G2"),
    "RESOLVED": ("-", "-", "-", "-", "-", "-", "-", "-", "G1", "G2"),
    "REOPENED": ("-", "Y", "Y", "-", "Y", "-", "-", "Y", "-", "G2"),
    "SUPERSEDED": ("-", "-", "-", "-", "-", "-", "-", "-", "-", "-"),
}

#: `"-"` in the grid above, spelled the way the implementation reports it.
ILLEGAL = "ILLEGAL"


def expected_case_code(frm: str, to: str) -> str:
    """The grid cell for `frm -> to`, as a verdict code."""
    cell = CASE_GRID[frm][CASE_STATES.index(to)]
    return ILLEGAL if cell == "-" else cell


#: `CASE_REOPEN_REASON_CODES`, hand-typed from 11_CONTRACTS.md §4.1. Imported
#: from nowhere: this is the second copy the guard is checked against.
REOPEN_REASON_CODES: frozenset[str] = frozenset(
    {
        "CONTRADICTORY_EVIDENCE",
        "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "TRIGGER_FIRED_UNFULFILLED",
        "USER_DISPUTE",
        "FULFILLMENT_REVERSED",
    }
)

#: `CASE_SUPERSEDE_REASON_CODES`, hand-typed from 11_CONTRACTS.md §4.1.
SUPERSEDE_REASON_CODES: frozenset[str] = frozenset(
    {"MERGED_INTO_CASE", "SPLIT_INTO_CASES", "DUPLICATE_CASE"}
)

#: The two spellings the canon register names as wrong. Neither has ever been a
#: member of the allowlist, and the register requires that either one *raises*.
MISSPELLED_REOPEN_CODES: tuple[str, ...] = (
    "CONTRADICTORY_EVIDENCE_ADMITTED",
    "RC_CONTRADICTORY_EVIDENCE",
)


# ---------------------------------------------------------------------------
# The case machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("frm", CASE_STATES)
def test_case_transition_matrix_is_exactly_the_specified_matrix(frm: str) -> None:
    """Each row of the grid, all ten columns, against the implementation.

    Ten parametrised tests cover all one hundred cells. A cell that the
    specification marks `-` must report `ILLEGAL`; a cell marked `G1` or `G2`
    must report that guard code even before a reason code is supplied, because
    "guarded" is a property of the cell and "permitted right now" is a property
    of the call.
    """
    for to in CASE_STATES:
        expected = expected_case_code(frm, to)
        verdict = legal_transition("CASE", frm, to)
        assert (
            verdict.code == expected
        ), f"{frm} -> {to}: spec says {expected}, implementation says {verdict.code}"
        # An unguarded cell is legal without a reason code; a guarded one is not.
        assert bool(verdict) is (
            expected == "Y"
        ), f"{frm} -> {to}: legality disagrees with {expected}"


def test_case_grid_is_a_complete_10x10_transcription() -> None:
    """The hand-written grid really is 10x10, so the row test really is 100 cells."""
    assert len(CASE_STATES) == 10
    assert set(CASE_GRID) == set(CASE_STATES)
    assert all(len(row) == 10 for row in CASE_GRID.values())
    assert sum(len(row) for row in CASE_GRID.values()) == 100
    assert {cell for row in CASE_GRID.values() for cell in row} == {"-", "Y", "G1", "G2"}


def test_self_transition_is_never_legal() -> None:
    """A status that does not change is not a transition (12 §5.1).

    It must not consume a case revision, so the diagonal of the grid is `-`
    in every one of the ten cells.
    """
    for state in CASE_STATES:
        verdict = legal_transition(CASE_MACHINE, state, state)
        assert verdict.code == ILLEGAL
        assert not verdict


def test_superseded_case_is_terminal() -> None:
    """SUPERSEDED goes nowhere, not even with a merge reason code in hand."""
    for target in CASE_STATES:
        assert not legal_transition(
            CASE_MACHINE, "SUPERSEDED", target, reason_code="MERGED_INTO_CASE"
        )
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(CASE_MACHINE, "SUPERSEDED", "REOPENED")
    assert "terminal" in str(excinfo.value)


def test_resolved_case_cannot_jump_straight_to_actionable() -> None:
    """A resolved case reopens or it does nothing.

    It never silently becomes actionable again — that would erase the fact that
    it had been closed, which is the whole point of the hero scenario.
    """
    assert not legal_transition(CASE_MACHINE, "RESOLVED", "ACTIONABLE")

    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(CASE_MACHINE, "RESOLVED", "ACTIONABLE")

    error = excinfo.value
    assert error.machine == "case"
    assert error.from_state == "RESOLVED"
    assert error.to_state == "ACTIONABLE"
    assert "not a legal transition" in str(error)


def test_reopen_requires_a_qualifying_reason_code() -> None:
    """RESOLVED -> REOPENED is guarded.

    Without a reason from the allowlist it is illegal, so no code path can
    reopen a case "just because".
    """
    assert not legal_transition(CASE_MACHINE, "RESOLVED", "REOPENED")
    assert not legal_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code="BECAUSE")
    for code in sorted(REOPEN_REASON_CODES):
        assert legal_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code=code), code


def test_hero_reopen_is_legal_with_contradictory_evidence() -> None:
    """The hero commit: `CONTRADICTORY_EVIDENCE` and no other spelling."""
    verdict = legal_transition(
        CASE_MACHINE, "RESOLVED", "REOPENED", reason_code="CONTRADICTORY_EVIDENCE"
    )
    assert verdict.legal is True
    assert verdict.code == "G1"
    assert verdict.guard is None
    assert verdict.reason_code == "CONTRADICTORY_EVIDENCE"
    assert_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code="CONTRADICTORY_EVIDENCE")


@pytest.mark.parametrize("wrong_code", MISSPELLED_REOPEN_CODES)
def test_misspelled_reopen_reason_code_raises(wrong_code: str) -> None:
    """`CANONICAL_DECISIONS.md`, *Hero commit canon*.

    `CONTRADICTORY_EVIDENCE_ADMITTED` and `RC_CONTRADICTORY_EVIDENCE` are not
    members of the allowlist and never were, so a transition carrying either
    raises rather than merely reading oddly. The guard is the mechanism; this
    test is the proof that it is a guard and not a label.
    """
    assert wrong_code not in REOPEN_REASON_CODES

    with pytest.raises(IllegalTransition) as excinfo:
        assert_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code=wrong_code)
    assert excinfo.value.reason_code == wrong_code

    verdict = legal_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code=wrong_code)
    assert not verdict
    with pytest.raises(IllegalTransitionError):
        verdict.raise_if_illegal()


def test_reopen_error_names_the_allowed_reason_codes() -> None:
    """The error carries the closed set, so no caller has to invent a message."""
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code="NOPE")
    message = str(excinfo.value)
    assert "guarded transition requires reason_code" in message
    for code in sorted(REOPEN_REASON_CODES):
        assert code in message


def test_verdict_is_not_a_bare_boolean() -> None:
    """A boolean forces the caller to invent an error message.

    Invented error messages are how closed reason-code sets leak, so the
    verdict carries legality, the guard that rejected, that guard's allowlist
    and the reason code that was offered.
    """
    verdict = legal_transition(CASE_MACHINE, "RESOLVED", "REOPENED", reason_code="BECAUSE")
    assert isinstance(verdict, TransitionVerdict)
    assert not isinstance(verdict, bool)
    assert bool(verdict) is False
    assert verdict.legal is False
    assert verdict.machine == "case"
    assert verdict.from_state == "RESOLVED"
    assert verdict.to_state == "REOPENED"
    assert verdict.code == "G1"
    assert verdict.guard == "G1"
    assert verdict.reason_code == "BECAUSE"
    assert verdict.allowed_reason_codes == REOPEN_REASON_CODES
    with pytest.raises(AttributeError):
        verdict.legal = True  # type: ignore[misc]


def test_supersede_requires_a_merge_reason_code() -> None:
    """G2: `* -> SUPERSEDED` names the surviving case or it does not happen."""
    for frm in ("OPEN", "WAITING", "ACTIONABLE", "RESOLVED", "REOPENED"):
        assert not legal_transition(CASE_MACHINE, frm, "SUPERSEDED")
        assert not legal_transition(CASE_MACHINE, frm, "SUPERSEDED", reason_code="TIDYING_UP")
        for code in sorted(SUPERSEDE_REASON_CODES):
            assert legal_transition(CASE_MACHINE, frm, "SUPERSEDED", reason_code=code), (frm, code)
    # A reopen code is not a merge code: the two allowlists are not interchangeable.
    assert not legal_transition(
        CASE_MACHINE, "OPEN", "SUPERSEDED", reason_code="CONTRADICTORY_EVIDENCE"
    )


def test_unknown_states_are_rejected_not_ignored() -> None:
    """A stale status from a superseded document is illegal, never a default."""
    assert not legal_transition(CASE_MACHINE, "ALMOST_DONE", "RESOLVED")
    assert not legal_transition(CASE_MACHINE, "OPEN", "MOSTLY_FINE")
    assert legal_transition(CASE_MACHINE, "OPEN", "MOSTLY_FINE").code == ILLEGAL
    with pytest.raises(IllegalTransitionError):
        assert_transition(CASE_MACHINE, "OPEN", "MOSTLY_FINE")
    # An unknown *machine* is a programming error, not a data error.
    with pytest.raises(ValueError, match="NOT_A_MACHINE"):
        legal_transition("NOT_A_MACHINE", "OPEN", "RESOLVED")


def test_every_case_state_is_reachable_from_open() -> None:
    """No orphaned state: every status is reachable from OPEN."""
    reachable = reachable_states(CASE_MACHINE, "OPEN")
    for status in CASE_STATES:
        if status == "OPEN":
            continue
        assert status in reachable, f"{status} is orphaned in the case machine"
    # OPEN itself has no inbound edge: a case is created there, never moved there.
    assert "OPEN" not in reachable


def test_transition_tables_are_immutable() -> None:
    """Frozen at import: a caller cannot widen the table at runtime."""
    with pytest.raises(TypeError):
        CASE_TRANSITIONS["OPEN"] = frozenset()  # type: ignore[index]
    with pytest.raises(TypeError):
        MACHINES["case"] = CASE_MACHINE  # type: ignore[index]
    with pytest.raises(AttributeError):
        CASE_MACHINE.name = "not_the_case_machine"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The commitment machine — 11_CONTRACTS.md §4.2
# ---------------------------------------------------------------------------

COMMITMENT_STATES: tuple[str, ...] = (
    "PROPOSED",
    "ACTIVE",
    "PARTIAL",
    "DISPUTED",
    "FULFILLED",
    "EXPIRED",
    "SUPERSEDED",
)

#: `"G"` is an unlabelled guard: the cell is legal only with a reason code from
#: that transition's allowlist. Hand-transcribed from 11_CONTRACTS.md §4.2.
COMMITMENT_GRID: dict[str, tuple[str, ...]] = {
    #               PROP ACTV PART DISP FULF EXPD SUPD
    "PROPOSED": ("-", "Y", "-", "-", "-", "Y", "Y"),
    "ACTIVE": ("-", "-", "Y", "Y", "Y", "Y", "Y"),
    "PARTIAL": ("-", "-", "-", "Y", "Y", "Y", "Y"),
    "DISPUTED": ("-", "Y", "Y", "-", "Y", "Y", "Y"),
    "FULFILLED": ("-", "-", "-", "G", "-", "-", "Y"),
    "EXPIRED": ("-", "Y", "-", "Y", "-", "-", "Y"),
    "SUPERSEDED": ("-", "-", "-", "-", "-", "-", "-"),
}

#: `COMMITMENT_UNFULFIL_REASON_CODES`, hand-typed from 11_CONTRACTS.md §4.2.
UNFULFIL_REASON_CODES: frozenset[str] = frozenset(
    {
        "FULFILLMENT_REVERSED",
        "PAYMENT_CLAWED_BACK",
        "USER_DISPUTE",
        "COUNTERPARTY_RETRACTION",
    }
)


def test_commitment_matrix_is_exactly_the_specified_matrix() -> None:
    """All 49 cells of the commitment table, hand-transcribed."""
    for frm, row in COMMITMENT_GRID.items():
        for index, cell in enumerate(row):
            to = COMMITMENT_STATES[index]
            expected = ILLEGAL if cell == "-" else cell
            verdict = legal_transition(COMMITMENT_MACHINE, frm, to)
            assert (
                verdict.code == expected
            ), f"{frm} -> {to}: spec says {expected}, implementation says {verdict.code}"


def test_fulfilled_commitment_cannot_silently_become_active() -> None:
    """A settled obligation does not un-settle itself by the back door."""
    assert not legal_transition(COMMITMENT_MACHINE, "FULFILLED", "ACTIVE")
    assert not legal_transition(COMMITMENT_MACHINE, "FULFILLED", "PARTIAL")
    assert not legal_transition(
        COMMITMENT_MACHINE, "FULFILLED", "ACTIVE", reason_code="PAYMENT_CLAWED_BACK"
    )


def test_unfulfilling_requires_a_named_cause() -> None:
    """FULFILLED -> DISPUTED exists for clawbacks and retractions, and is guarded."""
    assert not legal_transition(COMMITMENT_MACHINE, "FULFILLED", "DISPUTED")
    assert not legal_transition(
        COMMITMENT_MACHINE, "FULFILLED", "DISPUTED", reason_code="CHANGED_MY_MIND"
    )
    for code in sorted(UNFULFIL_REASON_CODES):
        assert legal_transition(COMMITMENT_MACHINE, "FULFILLED", "DISPUTED", reason_code=code), code


@pytest.mark.parametrize("frm", ["ACTIVE", "PARTIAL", "DISPUTED"])
def test_fulfilled_is_unreachable_while_outstanding_is_positive(frm: str) -> None:
    """`outstanding > 0` and `status = FULFILLED` cannot both be true.

    The shape of the transition is legal (the grid says `Y`); the amount is
    what refuses it, and the refusal names `INVARIANT_FULFILLED_STATUS_MISMATCH`
    rather than a sentence someone made up at the call site.
    """
    verdict = commitment_transition(frm, "FULFILLED", outstanding_amount=Decimal("186.0000"))
    assert verdict.legal is False
    assert verdict.guard == "INVARIANT_FULFILLED_STATUS_MISMATCH"
    assert "outstanding" in verdict.detail
    with pytest.raises(IllegalTransitionError):
        verdict.raise_if_illegal()
    # The table alone says yes; only the amount-aware helper says no.
    assert legal_transition(COMMITMENT_MACHINE, frm, "FULFILLED")


def test_fulfilled_is_reachable_when_nothing_is_outstanding() -> None:
    """Zero outstanding is the only door to FULFILLED, and it is open."""
    verdict = commitment_transition("PARTIAL", "FULFILLED", outstanding_amount=Decimal("0.0000"))
    assert verdict.legal is True
    assert verdict.guard is None
    # Not knowing the amount is not the same as knowing it is zero, but the
    # table-level verdict is still returned so the kernel can order its checks.
    assert commitment_transition("ACTIVE", "FULFILLED").legal is True
    # Every other target is unaffected by the amount rule.
    assert (
        commitment_transition("ACTIVE", "PARTIAL", outstanding_amount=Decimal("186.0000")).legal
        is True
    )


# ---------------------------------------------------------------------------
# The trigger machine — 11_CONTRACTS.md §4.3 and 16_TRIGGER_DSL.md §9.10
# ---------------------------------------------------------------------------

TRIGGER_STATES: tuple[str, ...] = ("ARMED", "FIRED", "DISARMED", "EXPIRED")

TRIGGER_GRID: dict[str, tuple[str, ...]] = {
    #             ARMD FIRD DISA EXPD
    "ARMED": ("-", "Y", "Y", "Y"),
    "FIRED": ("Y", "-", "Y", "Y"),
    "DISARMED": ("-", "-", "-", "-"),
    "EXPIRED": ("-", "-", "-", "-"),
}

#: 16_TRIGGER_DSL.md §9.10, the outcome taxonomy: `TriggerResult` -> the state
#: the row is left in. NO_OP and ERROR leave the state unchanged.
WAKE_OUTCOME: dict[str, str] = {
    "FIRED": "FIRED",
    "NO_OP": "ARMED",
    "DISARMED": "DISARMED",
    "EXPIRED": "EXPIRED",
    "ERROR": "ARMED",
}


def test_trigger_matrix_is_exactly_the_specified_matrix() -> None:
    """All 16 cells of the trigger table, hand-transcribed."""
    for frm, row in TRIGGER_GRID.items():
        for index, cell in enumerate(row):
            to = TRIGGER_STATES[index]
            expected = ILLEGAL if cell == "-" else cell
            verdict = legal_transition(TRIGGER_MACHINE, frm, to)
            assert (
                verdict.code == expected
            ), f"{frm} -> {to}: spec says {expected}, implementation says {verdict.code}"


def test_disarmed_trigger_cannot_fire() -> None:
    """Prospective memory that was switched off stays off."""
    assert not legal_transition(TRIGGER_MACHINE, "DISARMED", "FIRED")
    assert not legal_transition(TRIGGER_MACHINE, "EXPIRED", "FIRED")
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_transition(TRIGGER_MACHINE, "DISARMED", "FIRED")
    assert "terminal" in str(excinfo.value)


def test_trigger_wake_outcomes_map_to_the_specified_states() -> None:
    """One wake, one outcome, one resulting state (16 §9.10).

    Only FIRED touches the case aggregate; NO_OP and ERROR leave the trigger
    ARMED, which is why they must not be modelled as state changes that could
    consume a revision.
    """
    for result, state in sorted(WAKE_OUTCOME.items()):
        verdict = trigger_wake_transition("ARMED", result)
        assert verdict.legal is True, result
        assert verdict.to_state == state, result
        assert verdict.code == ("NO_OP" if state == "ARMED" else "Y"), result
    with pytest.raises(ValueError, match="EXPLODED"):
        trigger_wake_transition("ARMED", "EXPLODED")


def test_wake_of_a_trigger_that_is_not_armed_is_refused() -> None:
    """§9.6 guard: a wake for a row that is no longer ARMED does nothing.

    The refusal carries `TRIGGER_NOT_ARMED`, which is a member of the closed
    trigger reason-code registry, so the evaluator reports it rather than
    inventing wording.
    """
    for state in ("FIRED", "DISARMED", "EXPIRED"):
        verdict = trigger_wake_transition(state, "FIRED")
        assert verdict.legal is False, state
        assert verdict.guard == "TRIGGER_NOT_ARMED", state
    with pytest.raises(IllegalTransitionError):
        trigger_wake_transition("DISARMED", "FIRED").raise_if_illegal()


# ---------------------------------------------------------------------------
# The remaining machines — 11_CONTRACTS.md §4.2, §4.3
# ---------------------------------------------------------------------------


def test_no_route_to_executing_that_skips_approval() -> None:
    """Invariant 4 expressed as a table.

    The only initial route to EXECUTING is APPROVED -> EXECUTING guarded by
    REVALIDATION_PASSED; a retry may use FAILED_RETRYABLE -> EXECUTING under
    the same guard. PROPOSED and NEEDS_REVIEW have no edge at all.
    """
    assert not legal_transition(ACTION_MACHINE, "PROPOSED", "EXECUTING")
    assert not legal_transition(
        ACTION_MACHINE, "PROPOSED", "EXECUTING", reason_code="REVALIDATION_PASSED"
    )
    assert not legal_transition(ACTION_MACHINE, "NEEDS_REVIEW", "EXECUTING")
    assert not legal_transition(ACTION_MACHINE, "APPROVED", "EXECUTING")
    assert legal_transition(
        ACTION_MACHINE, "APPROVED", "EXECUTING", reason_code="REVALIDATION_PASSED"
    )
    assert legal_transition(
        ACTION_MACHINE, "FAILED_RETRYABLE", "EXECUTING", reason_code="REVALIDATION_PASSED"
    )
    # Executed is terminal: a sent email cannot be sent again by a state change.
    assert not legal_transition(ACTION_MACHINE, "EXECUTED", "EXECUTING")
    assert not legal_transition(ACTION_MACHINE, "EXECUTED", "NEEDS_REVIEW")
    with pytest.raises(IllegalTransitionError):
        assert_transition(ACTION_MACHINE, "PROPOSED", "EXECUTING")


def test_outbox_cannot_skip_dispatching_and_dead_letters_stay_dead() -> None:
    """The outbox has one path out of PENDING and one end state per outcome."""
    assert not legal_transition(OUTBOX_MACHINE, "PENDING", "DISPATCHED")
    assert legal_transition(OUTBOX_MACHINE, "PENDING", "DISPATCHING")
    assert legal_transition(OUTBOX_MACHINE, "DISPATCHING", "FAILED_RETRYABLE")
    assert legal_transition(OUTBOX_MACHINE, "FAILED_RETRYABLE", "DISPATCHING")
    assert not legal_transition(OUTBOX_MACHINE, "DEAD", "DISPATCHING")
    assert not legal_transition(OUTBOX_MACHINE, "DISPATCHED", "DISPATCHING")


def test_machine_registry_covers_every_declared_machine() -> None:
    """Eight machines, addressable by name, each with the §4 behaviour."""
    assert set(MACHINES) == {
        "case",
        "commitment",
        "conflict",
        "action_intent",
        "prospective_trigger",
        "outbox_event",
        "memory_proposal",
        "epistemic_status",
    }
    assert MACHINES["case"] is CASE_MACHINE
    # Conflict: reopening a resolved conflict is guarded (§4.2).
    assert not legal_transition(CONFLICT_MACHINE, "RESOLVED", "OPEN")
    assert legal_transition(
        CONFLICT_MACHINE, "RESOLVED", "OPEN", reason_code="NEW_CONTRADICTORY_EVIDENCE"
    )
    assert legal_transition(
        CONFLICT_MACHINE, "AUTO_RESOLVED", "OPEN", reason_code="AUTHORITY_REASSESSED"
    )
    # Proposal: every outcome of SUBMITTED is terminal.
    assert legal_transition(PROPOSAL_MACHINE, "SUBMITTED", "ACCEPTED")
    assert not legal_transition(PROPOSAL_MACHINE, "ACCEPTED", "REJECTED_INVARIANT")
    assert not legal_transition(PROPOSAL_MACHINE, "SUBMITTED", "SUBMITTED")
    # Epistemic status is a property of a belief version, so v2 may restate v1.
    assert legal_transition(EPISTEMIC_MACHINE, "CONFIRMED", "CONFIRMED")
    assert legal_transition(EPISTEMIC_MACHINE, "CONFIRMED", "DISPUTED")
    assert not legal_transition(EPISTEMIC_MACHINE, "RETRACTED", "CONFIRMED")
