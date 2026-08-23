"""The closed outcome taxonomy: one result, exactly one reason, and a state.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts*: "Trigger results
  ``FIRED``, ``NO_OP``, ``DISARMED``, ``EXPIRED``, ``ERROR`` plus one
  closed-set reason code."
- ``docs/specs/16_TRIGGER_DSL.md`` §9.10 (the taxonomy), §9.11 (re-arm),
  §11.2 (``classify_false``).
- ``db/migrations/versions/0006_prospective_memory.py`` —
  ``ck_prospective_triggers_last_reason``, which is the same partition written
  as a database CHECK.

Why the partition is asserted against the migration
---------------------------------------------------
The result-to-reason pairing exists in two places: this module and a CHECK
constraint the Kernel's ``UPDATE`` has to satisfy. If they disagreed, a
perfectly reasonable-looking outcome — ``DISARMED`` with ``PREDICATE_FALSE``,
say — would pass every unit test and then fail at 3 a.m. inside a serializable
transaction, at the moment the row was needed. So
:func:`test_the_partition_matches_the_migration_check` parses the migration and
compares. That is the only way this can be a fact rather than an intention.

"An unexplained NOOP is a gate failure even though nothing broke"
-----------------------------------------------------------------
``23_PHASE_GATES.md`` §23.8. Every no-op below carries a specific reason, and
the tests assert the reason rather than the absence of a fire.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from provenance_domain.enums import TriggerReasonCode, TriggerResult, TriggerState
from services.control_plane.app.triggers.ast import parse_spec
from services.control_plane.app.triggers.config import REARM_POLICY
from services.control_plane.app.triggers.outcomes import (
    RESULT_REASONS,
    STATE_AFTER,
    Outcome,
    classify_false,
    disarm_reason,
    rearm_delay,
    reason_is_legal,
)
from services.control_plane.app.triggers.projection import build_projection
from services.control_plane.app.triggers.registry import resolve_field
from services.control_plane.tests.events._support import canon

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "db"
    / "migrations"
    / "versions"
    / "0006_prospective_memory.py"
)


def _projection(
    *,
    case_status: str = "WAITING",
    commitment_status: str = "ACTIVE",
    outstanding: str | None = "1800.0000",
):
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    projection = build_projection(
        case_row=canon.case_row(status=case_status),
        commitment_rows={
            canon.HERO_COMMITMENT_ID: canon.commitment_row(
                status=commitment_status, outstanding=outstanding
            )
        },
        trigger_row=canon.trigger_row(),
        spec=spec,
    )
    return projection, spec


# ---------------------------------------------------------------------------
# The partition itself.
# ---------------------------------------------------------------------------


def test_every_result_has_at_least_one_reason_and_no_reason_is_shared() -> None:
    """One reason belongs to exactly one result, or "closed set" means nothing."""
    assert set(RESULT_REASONS) == set(TriggerResult)
    seen: set[TriggerReasonCode] = set()
    for reasons in RESULT_REASONS.values():
        assert reasons
        assert not (seen & reasons), "a reason code may accompany exactly one result"
        seen |= reasons
    assert seen == set(TriggerReasonCode)


def test_the_partition_matches_the_migration_check() -> None:
    """``ck_prospective_triggers_last_reason``, parsed rather than paraphrased.

    A code that this module thinks legal and the database refuses would fail
    inside the fire transaction, far from here.
    """
    source = _MIGRATION.read_text(encoding="utf-8")
    check = source.split("ck_prospective_triggers_last_reason", 1)[1]
    check = check.split("CONSTRAINT ck_prospective_triggers_versions", 1)[0]
    clauses = re.findall(
        r"last_result = '(\w+)' AND last_reason_code IN \(([^)]*)\)", check, re.DOTALL
    )
    assert len(clauses) == len(RESULT_REASONS), "the CHECK lost a result branch"
    in_migration = {result: set(re.findall(r"'([A-Z_]+)'", body)) for result, body in clauses}
    assert in_migration == {
        result.value: {reason.value for reason in reasons}
        for result, reasons in RESULT_REASONS.items()
    }


@pytest.mark.parametrize(
    ("result", "reason", "legal"),
    [
        (TriggerResult.FIRED, TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID, True),
        (TriggerResult.NO_OP, TriggerReasonCode.PREDICATE_FALSE, True),
        (TriggerResult.DISARMED, TriggerReasonCode.CASE_RESOLVED, True),
        (TriggerResult.EXPIRED, TriggerReasonCode.TRIGGER_EXPIRED, True),
        (TriggerResult.ERROR, TriggerReasonCode.BINDING_UNRESOLVED, True),
        # The combination that reads plausibly and means nothing.
        (TriggerResult.DISARMED, TriggerReasonCode.PREDICATE_FALSE, False),
        (TriggerResult.FIRED, TriggerReasonCode.CASE_RESOLVED, False),
        (TriggerResult.NO_OP, TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID, False),
    ],
)
def test_reason_legality(result: TriggerResult, reason: TriggerReasonCode, legal: bool) -> None:
    assert reason_is_legal(result, reason) is legal


def test_an_illegal_pairing_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="is not a legal reason"):
        Outcome(result=TriggerResult.FIRED, reason_code=TriggerReasonCode.PREDICATE_FALSE)


def test_state_after_each_result() -> None:
    """§9.10's terminal-state column. ``NO_OP`` leaves the trigger armed."""
    assert STATE_AFTER[TriggerResult.FIRED] is TriggerState.FIRED
    assert STATE_AFTER[TriggerResult.NO_OP] is TriggerState.ARMED
    assert STATE_AFTER[TriggerResult.DISARMED] is TriggerState.DISARMED
    assert STATE_AFTER[TriggerResult.EXPIRED] is TriggerState.EXPIRED
    assert STATE_AFTER[TriggerResult.ERROR] is TriggerState.ARMED


def test_only_a_fire_touches_the_case_aggregate() -> None:
    """ "If one proposal produces no canonical change, do not increment revision."."""
    assert Outcome(
        result=TriggerResult.FIRED,
        reason_code=TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
    ).increments_case_revision
    for result, reason in (
        (TriggerResult.NO_OP, TriggerReasonCode.PREDICATE_FALSE),
        (TriggerResult.DISARMED, TriggerReasonCode.CASE_RESOLVED),
        (TriggerResult.EXPIRED, TriggerReasonCode.TRIGGER_EXPIRED),
        (TriggerResult.ERROR, TriggerReasonCode.PROJECTION_FAILED),
    ):
        assert not Outcome(result=result, reason_code=reason).increments_case_revision


# ---------------------------------------------------------------------------
# classify_false — §11.2. FALSE-and-done versus FALSE-and-keep-watching.
# ---------------------------------------------------------------------------


def test_a_paid_commitment_disarms_rather_than_re_arming() -> None:
    """§11.2. The obligation is discharged; there is no future in which it
    could become true again, so re-arming would be a promise to keep asking."""
    projection, spec = _projection(commitment_status="FULFILLED", outstanding="0.0000")
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_SATISFIED


@pytest.mark.parametrize("status", ["SUPERSEDED", "EXPIRED"])
def test_a_superseded_commitment_disarms(status: str) -> None:
    projection, spec = _projection(commitment_status=status)
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_SUPERSEDED


def test_a_resolved_case_disarms_with_case_resolved() -> None:
    """D8's assertion, at the level where the decision is actually made."""
    projection, spec = _projection(case_status="RESOLVED")
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.CASE_RESOLVED


def test_a_superseded_case_disarms_with_case_superseded() -> None:
    projection, spec = _projection(case_status="SUPERSEDED")
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.reason_code is TriggerReasonCode.CASE_SUPERSEDED


def test_the_case_outranks_the_commitment() -> None:
    """A resolved case disarms for CASE_RESOLVED even if the money is unpaid.

    Order matters: reporting COMMITMENT_SATISFIED on a case that was resolved
    while still owing money would be a false statement in the audit record.
    """
    projection, spec = _projection(case_status="RESOLVED", commitment_status="FULFILLED")
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.reason_code is TriggerReasonCode.CASE_RESOLVED


def test_a_still_open_obligation_is_a_no_op_that_keeps_watching() -> None:
    """The deadline has not passed yet: false now, possibly true later."""
    projection, spec = _projection()
    outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.PREDICATE_FALSE
    assert outcome.rearm is True


def test_a_disarming_outcome_does_not_rearm() -> None:
    projection, spec = _projection(case_status="RESOLVED")
    assert classify_false(projection, spec, "COMMITMENT_DEADLINE").rearm is False


# ---------------------------------------------------------------------------
# The re-arm backoff — §9.11.
# ---------------------------------------------------------------------------


def test_rearm_backoff_follows_the_published_sequence() -> None:
    expected = REARM_POLICY["COMMITMENT_DEADLINE"]
    assert expected == (1, 3, 7, 14, 30)
    for generation, days in enumerate(expected, start=1):
        assert rearm_delay("COMMITMENT_DEADLINE", generation) == timedelta(days=days)


def test_rearm_backoff_saturates_at_the_last_step() -> None:
    """Past the end of the sequence the interval holds rather than resetting.

    Resetting would turn a long-lived obligation into a daily re-arm storm, and
    §17 R5 is explicit that quota, not correctness, is what that costs.
    """
    assert rearm_delay("COMMITMENT_DEADLINE", 99) == timedelta(days=30)


def test_every_trigger_type_has_a_backoff() -> None:
    for trigger_type in (
        "COMMITMENT_DEADLINE",
        "RESPONSE_DEADLINE",
        "CONFLICT_TIMEOUT",
        "WARRANTY_WINDOW",
    ):
        assert rearm_delay(trigger_type, 1) > timedelta(0)


def test_money_in_the_projection_is_still_decimal_when_classified() -> None:
    projection, _ = _projection()
    assert projection.values["commitments.deposit.outstanding_amount"] == Decimal("1800.0000")


# ---------------------------------------------------------------------------
# The disarm precedence, shared with the Kernel.
# ---------------------------------------------------------------------------
#
# Two components decide "why did this trigger stop watching?": the evaluator,
# when a predicate comes out FALSE, and the Memory Kernel, when a committed
# proposal disarms a trigger outright. They must not answer differently about
# the same event, and "we agreed in a message thread" is not a mechanism.
# `disarm_reason` is the one implementation; `classify_false` calls it, and the
# Kernel calls it, so agreement is structural rather than remembered.


def test_a_resolved_case_outranks_a_fulfilled_commitment() -> None:
    """The precedence that settles it, in one assertion.

    Recording ``COMMITMENT_SATISFIED`` for a case that resolved while money was
    still owed puts a false statement into an audit record, and the audit record
    is the product. The case is therefore checked first, always.
    """
    assert (
        disarm_reason(case_status="RESOLVED", commitment_statuses=("FULFILLED",))
        is TriggerReasonCode.CASE_RESOLVED
    )
    assert (
        disarm_reason(case_status="RESOLVED", commitment_statuses=("ACTIVE",))
        is TriggerReasonCode.CASE_RESOLVED
    )


def test_a_superseded_case_reports_case_superseded() -> None:
    assert (
        disarm_reason(case_status="SUPERSEDED", commitment_statuses=("ACTIVE",))
        is TriggerReasonCode.CASE_SUPERSEDED
    )


@pytest.mark.parametrize("status", ["OPEN", "WAITING", "ACTIONABLE", "DISPUTED", "REOPENED"])
def test_a_live_case_is_not_terminal(status: str) -> None:
    assert disarm_reason(case_status=status, commitment_statuses=("ACTIVE",)) is None


def test_a_case_status_of_none_means_the_commit_did_not_move_the_case() -> None:
    """The Kernel's shape: a disarm may accompany a commit that moved no case."""
    assert (
        disarm_reason(case_status=None, commitment_statuses=("FULFILLED",))
        is TriggerReasonCode.COMMITMENT_SATISFIED
    )


def test_every_bound_commitment_must_be_fulfilled_to_report_satisfied() -> None:
    """One of two paid is not "satisfied" — it is a reason to keep watching.

    A trigger bound to two obligations is discharged when both are, not when
    either is. Reporting satisfaction on the first would stop watching the
    second, which is the obligation being forgotten.
    """
    assert (
        disarm_reason(case_status=None, commitment_statuses=("FULFILLED", "FULFILLED"))
        is TriggerReasonCode.COMMITMENT_SATISFIED
    )
    assert disarm_reason(case_status=None, commitment_statuses=("FULFILLED", "ACTIVE")) is None


@pytest.mark.parametrize("status", ["SUPERSEDED", "EXPIRED"])
def test_any_replaced_commitment_reports_superseded(status: str) -> None:
    """The subject was replaced; the successor carries its own trigger."""
    assert (
        disarm_reason(case_status=None, commitment_statuses=(status,))
        is TriggerReasonCode.COMMITMENT_SUPERSEDED
    )
    assert (
        disarm_reason(case_status=None, commitment_statuses=("FULFILLED", status))
        is TriggerReasonCode.COMMITMENT_SUPERSEDED
    )


def test_the_answer_does_not_depend_on_binding_order() -> None:
    """Order-dependence here would make the same world produce two audit records.

    ``bindings`` is a mapping and its iteration order is an implementation
    detail of whoever serialized the predicate. A precedence that returned on
    the first match it happened to see would be reading that detail as meaning.
    """
    forward = disarm_reason(case_status=None, commitment_statuses=("FULFILLED", "SUPERSEDED"))
    reverse = disarm_reason(case_status=None, commitment_statuses=("SUPERSEDED", "FULFILLED"))
    assert forward is reverse is TriggerReasonCode.COMMITMENT_SUPERSEDED


def test_no_bound_commitments_is_not_vacuously_satisfied() -> None:
    """``all([])`` is ``True``, and that would disarm a trigger that watches
    only the case. Nothing is discharged, so nothing is reported."""
    assert disarm_reason(case_status=None, commitment_statuses=()) is None


@pytest.mark.parametrize("status", ["ACTIVE", "PARTIAL", "DISPUTED", "PROPOSED"])
def test_a_live_commitment_is_not_terminal(status: str) -> None:
    assert disarm_reason(case_status=None, commitment_statuses=(status,)) is None


def test_a_missing_commitment_status_is_not_terminal() -> None:
    """A projection that could not read a status must not be read as discharge."""
    assert disarm_reason(case_status=None, commitment_statuses=(None,)) is None


def test_every_reason_it_returns_is_legal_for_disarmed() -> None:
    """Whatever it answers must satisfy ``ck_prospective_triggers_last_reason``."""
    answers = {
        disarm_reason(case_status="RESOLVED", commitment_statuses=()),
        disarm_reason(case_status="SUPERSEDED", commitment_statuses=()),
        disarm_reason(case_status=None, commitment_statuses=("FULFILLED",)),
        disarm_reason(case_status=None, commitment_statuses=("SUPERSEDED",)),
    }
    assert None not in answers
    for reason in answers:
        assert reason is not None
        assert reason_is_legal(TriggerResult.DISARMED, reason)


def test_classify_false_is_the_same_precedence_and_not_a_second_copy() -> None:
    """``classify_false`` delegates, so the two cannot drift apart.

    Asserted through behaviour rather than by reading the source: every
    combination below must produce the reason ``disarm_reason`` gives for the
    same world.
    """
    for case_status, commitment_status in (
        ("RESOLVED", "FULFILLED"),
        ("SUPERSEDED", "ACTIVE"),
        ("WAITING", "FULFILLED"),
        ("WAITING", "SUPERSEDED"),
        ("WAITING", "ACTIVE"),
    ):
        projection, spec = _projection(case_status=case_status, commitment_status=commitment_status)
        outcome = classify_false(projection, spec, "COMMITMENT_DEADLINE")
        expected = disarm_reason(case_status=case_status, commitment_statuses=(commitment_status,))
        if expected is None:
            assert outcome.result is TriggerResult.NO_OP
            assert outcome.reason_code is TriggerReasonCode.PREDICATE_FALSE
        else:
            assert outcome.result is TriggerResult.DISARMED
            assert outcome.reason_code is expected
