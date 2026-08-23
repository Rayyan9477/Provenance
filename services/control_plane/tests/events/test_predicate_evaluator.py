"""Three-valued evaluation: the safety default, stated as truth tables.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §4.3 (Kleene logic and the firing rule),
  §8 (the evaluator), §10.3 (the stored trace), §15 items 7-9.

Why three-valued logic and not "NULL compares false"
----------------------------------------------------
``16_TRIGGER_DSL.md`` §17 R3 names this as the single most likely regression in
the subsystem: someone notices ``UNKNOWN`` complicates the evaluator, replaces
it with "null compares false", and the change passes casual review. It is not
cosmetic. Under the naive rule ``NOT(EQ(x, "FULFILLED"))`` becomes **true**
whenever ``x`` is null, so a trigger fires on missing data — a demand for money
that may already have been paid, sent because a column was empty.

:func:`test_kleene_truth_tables` and
:func:`test_a_null_operand_never_produces_a_fire` are what fail loudly on that
refactor, which is the only reason this module can be trusted six months from
now.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from services.control_plane.app.triggers import evaluator as evaluator_mod
from services.control_plane.app.triggers.ast import parse_spec
from services.control_plane.app.triggers.config import EVALUATOR_CODE_VERSION
from services.control_plane.app.triggers.evaluator import (
    Tri,
    evaluate_predicate,
    tri_and,
    tri_not,
    tri_or,
)
from services.control_plane.app.triggers.registry import resolve_field
from services.control_plane.tests.events._support import canon

pytestmark = pytest.mark.unit

ALL_TRI = (Tri.TRUE, Tri.FALSE, Tri.UNKNOWN)


def _spec(predicate: dict[str, Any], bindings: dict[str, Any] | None = None) -> Any:
    doc: dict[str, Any] = {"ast_version": "1.0", "predicate": predicate}
    if bindings is not None:
        doc["bindings"] = bindings
    return parse_spec(doc, resolve_field)


def _deposit_bindings() -> dict[str, Any]:
    return {"deposit": {"kind": "COMMITMENT", "id": str(canon.HERO_COMMITMENT_ID)}}


def _hero_values(**overrides: Any) -> dict[str, Any]:
    """The five paths §12.3 prints, plus whatever a test wants to move."""
    values: dict[str, Any] = {
        "clock.now": canon.DEMO_CLOCK_UTC,
        "case.status": "WAITING",
        "case.revision": 11,
        "commitments.deposit.due_at": canon.DEPOSIT_DUE_AT,
        "commitments.deposit.outstanding_amount": canon.DEPOSIT_OUTSTANDING,
        "commitments.deposit.status": "ACTIVE",
        "commitments.deposit.has_admitted_fulfillment": False,
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# §4.3 — the truth tables themselves. Matrix item 7.
# ---------------------------------------------------------------------------


def test_kleene_negation_table() -> None:
    assert tri_not(Tri.TRUE) is Tri.FALSE
    assert tri_not(Tri.FALSE) is Tri.TRUE
    assert tri_not(Tri.UNKNOWN) is Tri.UNKNOWN


@pytest.mark.parametrize(("left", "right"), list(itertools.product(ALL_TRI, ALL_TRI)))
def test_kleene_truth_tables(left: Tri, right: Tri) -> None:
    """All nine ``AND`` and nine ``OR`` cases, against §4.3 stated as prose.

    ``AND`` is FALSE if any argument is FALSE — *before* the UNKNOWN check, so
    ``FALSE AND UNKNOWN`` is FALSE, not UNKNOWN. ``OR`` mirrors it on TRUE.
    Getting that precedence backwards is the subtle version of the R3 bug.
    """
    expected_and = (
        Tri.FALSE
        if Tri.FALSE in (left, right)
        else Tri.UNKNOWN
        if Tri.UNKNOWN in (left, right)
        else Tri.TRUE
    )
    expected_or = (
        Tri.TRUE
        if Tri.TRUE in (left, right)
        else Tri.UNKNOWN
        if Tri.UNKNOWN in (left, right)
        else Tri.FALSE
    )
    assert tri_and([left, right]) is expected_and
    assert tri_or([left, right]) is expected_or


def test_false_beats_unknown_in_a_conjunction() -> None:
    assert tri_and([Tri.FALSE, Tri.UNKNOWN]) is Tri.FALSE
    assert tri_or([Tri.TRUE, Tri.UNKNOWN]) is Tri.TRUE


# ---------------------------------------------------------------------------
# §4.3 — comparison against absence. Matrix item 8.
# ---------------------------------------------------------------------------


def test_null_comparison_is_unknown() -> None:
    """Matrix item 8. ``GT(NULL, 0)`` is UNKNOWN, and so is its negation."""
    spec = _spec(
        {
            "op": "GT",
            "left": {"op": "FIELD", "path": "commitments.deposit.outstanding_amount"},
            "right": {"op": "CONST", "type": "DECIMAL", "value": "0"},
        },
        _deposit_bindings(),
    )
    evaluation = evaluate_predicate(
        spec, _hero_values(**{"commitments.deposit.outstanding_amount": None})
    )
    assert evaluation.result is Tri.UNKNOWN


def test_not_of_unknown_stays_unknown() -> None:
    spec = _spec(
        {
            "op": "NOT",
            "arg": {
                "op": "NE",
                "left": {"op": "FIELD", "path": "commitments.deposit.status"},
                "right": {"op": "CONST", "type": "STRING", "value": "FULFILLED"},
            },
        },
        _deposit_bindings(),
    )
    evaluation = evaluate_predicate(spec, _hero_values(**{"commitments.deposit.status": None}))
    assert evaluation.result is Tri.UNKNOWN


def test_a_null_operand_never_produces_a_fire() -> None:
    """The firing rule: a trigger fires only when the root is exactly TRUE.

    Under "null compares false" the ``NOT`` below would be TRUE and this trigger
    would demand money on the strength of an empty column.
    """
    spec = _spec(
        {
            "op": "NOT",
            "arg": {
                "op": "EQ",
                "left": {"op": "FIELD", "path": "commitments.deposit.status"},
                "right": {"op": "CONST", "type": "STRING", "value": "FULFILLED"},
            },
        },
        _deposit_bindings(),
    )
    evaluation = evaluate_predicate(spec, _hero_values(**{"commitments.deposit.status": None}))
    assert evaluation.result is not Tri.TRUE
    assert evaluation.result is Tri.UNKNOWN


def test_null_tests_are_never_unknown() -> None:
    """``IS_NULL``/``NOT_NULL`` are the only way to interrogate absence."""
    is_null = _spec(
        {"op": "IS_NULL", "arg": {"op": "FIELD", "path": "commitments.deposit.due_at"}},
        _deposit_bindings(),
    )
    not_null = _spec(
        {"op": "NOT_NULL", "arg": {"op": "FIELD", "path": "commitments.deposit.due_at"}},
        _deposit_bindings(),
    )
    absent = _hero_values(**{"commitments.deposit.due_at": None})
    assert evaluate_predicate(is_null, absent).result is Tri.TRUE
    assert evaluate_predicate(not_null, absent).result is Tri.FALSE
    present = _hero_values()
    assert evaluate_predicate(is_null, present).result is Tri.FALSE
    assert evaluate_predicate(not_null, present).result is Tri.TRUE


# ---------------------------------------------------------------------------
# Value semantics: Decimal money, UTC timestamps, no float anywhere.
# ---------------------------------------------------------------------------


def test_money_is_compared_as_decimal_not_float() -> None:
    """A tenth of a cent decides nothing by accident here.

    ``0.1 + 0.2`` is not ``0.3`` in binary floating point. If the evaluator
    coerced through ``float`` this comparison would be true.
    """
    spec = _spec(
        {
            "op": "GT",
            "left": {"op": "FIELD", "path": "commitments.deposit.outstanding_amount"},
            "right": {"op": "CONST", "type": "DECIMAL", "value": "0.3"},
        },
        _deposit_bindings(),
    )
    values = _hero_values(
        **{"commitments.deposit.outstanding_amount": Decimal("0.1") + Decimal("0.2")}
    )
    assert evaluate_predicate(spec, values).result is Tri.FALSE
    assert evaluation_detail(evaluate_predicate(spec, values)) == "0.3 GT 0.3"


def evaluation_detail(evaluation: Any) -> str:
    return evaluation.node_trace[0].detail


def test_decimal_and_int_compare_without_precision_loss() -> None:
    spec = _spec(
        {
            "op": "GT",
            "left": {"op": "FIELD", "path": "commitments.deposit.outstanding_amount"},
            "right": {"op": "CONST", "type": "INT", "value": 0},
        },
        _deposit_bindings(),
    )
    assert evaluate_predicate(spec, _hero_values()).result is Tri.TRUE


def test_timestamps_are_normalised_to_utc_before_comparison() -> None:
    """A wake time in another offset must not change the answer."""
    spec = _spec(
        {
            "op": "GTE",
            "left": {"op": "FIELD", "path": "clock.now"},
            "right": {"op": "FIELD", "path": "commitments.deposit.due_at"},
        },
        _deposit_bindings(),
    )
    from datetime import timedelta, timezone

    eastern = timezone(timedelta(hours=-4))
    values = _hero_values(**{"clock.now": canon.DEMO_CLOCK_UTC.astimezone(eastern)})
    assert evaluate_predicate(spec, values).result is Tri.TRUE


# ---------------------------------------------------------------------------
# The audit record — §8 and §10.3. Eager, total, reproducible.
# ---------------------------------------------------------------------------


def test_evaluation_is_eager_so_the_trace_is_complete() -> None:
    """No short-circuit: every subexpression appears in the trace.

    This is what lets the Memory Trace panel show a judge exactly which conjunct
    was false. A short-circuiting evaluator would produce a trace that stops at
    the first FALSE and explains nothing about the rest.
    """
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    values = _hero_values(**{"commitments.deposit.outstanding_amount": Decimal("0.0000")})
    evaluation = evaluate_predicate(spec, values)
    assert evaluation.result is Tri.FALSE
    # 7 conjuncts + the AND itself.
    assert len(evaluation.node_trace) == 8
    assert [step.result for step in evaluation.node_trace].count("FALSE") == 2


def test_node_trace_is_ordered_by_node_id() -> None:
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    evaluation = evaluate_predicate(spec, _hero_values())
    ids = [step.nid for step in evaluation.node_trace]
    assert ids == sorted(ids)
    assert ids[0] == 0  # the root AND is pre-order index 0


def test_evaluation_is_reproducible() -> None:
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    first = evaluate_predicate(spec, _hero_values())
    second = evaluate_predicate(spec, _hero_values())
    assert first == second


def test_evaluation_records_the_code_version_and_predicate_hash() -> None:
    """An old evaluation must never be silently reinterpreted by new code."""
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    evaluation = evaluate_predicate(spec, _hero_values())
    assert evaluation.evaluator_code_version == EVALUATOR_CODE_VERSION
    assert evaluation.predicate_sha256 == spec.sha256


def test_observed_covers_exactly_the_referenced_paths() -> None:
    """``observed`` is the durable answer to "what did it actually see?".

    Not more than the referenced paths — dumping the whole projection would put
    amounts the predicate never read into a record a judge reads as evidence of
    what the decision rested on.
    """
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    evaluation = evaluate_predicate(spec, _hero_values())
    assert set(evaluation.observed) == set(spec.referenced_paths)
    assert evaluation.observed["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert evaluation.observed["commitments.deposit.due_at"] == "2026-06-15T00:00:00Z"
    assert evaluation.observed["case.status"] == "WAITING"


def test_rendered_values_are_round_trippable_strings() -> None:
    spec = _spec(
        {
            "op": "EQ",
            "left": {"op": "FIELD", "path": "commitments.deposit.has_admitted_fulfillment"},
            "right": {"op": "CONST", "type": "BOOL", "value": False},
        },
        _deposit_bindings(),
    )
    evaluation = evaluate_predicate(spec, _hero_values())
    assert evaluation.observed["commitments.deposit.has_admitted_fulfillment"] == "false"
    assert evaluation.result is Tri.TRUE


def test_a_missing_projection_path_is_a_loud_failure_not_a_silent_unknown() -> None:
    """A projection that forgot a path is a bug, and it must not read as UNKNOWN.

    UNKNOWN means "the database does not know". A KeyError swallowed into
    UNKNOWN would mean "we forgot to load it", and the two are recorded
    identically in the audit trail while meaning opposite things.
    """
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    incomplete = _hero_values()
    del incomplete["commitments.deposit.status"]
    with pytest.raises(KeyError):
        evaluate_predicate(spec, incomplete)


# ---------------------------------------------------------------------------
# The hero predicate — matrix item 9.
# ---------------------------------------------------------------------------


def test_hero_predicate_fires_on_the_seeded_facts() -> None:
    """Matrix item 9. §12.3's values produce TRUE, conjunct by conjunct."""
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    evaluation = evaluate_predicate(spec, _hero_values())
    assert evaluation.result is Tri.TRUE
    assert all(step.result == "TRUE" for step in evaluation.node_trace)


def test_hero_predicate_is_false_after_payment() -> None:
    """Matrix item 10's predicate half. The landlord paid; nothing fires.

    A timer-based system would have emailed the landlord demanding money that
    had already been paid. This assertion is the difference.
    """
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    values = _hero_values(
        **{
            "commitments.deposit.outstanding_amount": Decimal("0.0000"),
            "commitments.deposit.status": "FULFILLED",
        }
    )
    evaluation = evaluate_predicate(spec, values)
    assert evaluation.result is Tri.FALSE


def test_hero_predicate_is_false_before_the_deadline() -> None:
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    values = _hero_values(**{"clock.now": datetime(2026, 6, 1, 0, 0, tzinfo=UTC)})
    assert evaluate_predicate(spec, values).result is Tri.FALSE


def test_hero_predicate_is_false_once_the_case_resolves() -> None:
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    values = _hero_values(**{"case.status": "RESOLVED"})
    assert evaluate_predicate(spec, values).result is Tri.FALSE


def test_the_evaluator_module_reads_no_wall_clock() -> None:
    """Matrix item 21. ``clock.now`` is the database's clock or it is nothing.

    Enforcement is structural: the evaluator receives a value map and has no
    other way to obtain a time. This scans the source so a future edit that
    reaches for ``datetime.now()`` fails here rather than in production, where
    it would produce ``days_overdue = -1`` on a row the database considers
    overdue.
    """
    import inspect

    source = inspect.getsource(evaluator_mod)
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("datetime.now(", "time.time(", "date.today(", "utcnow("):
        assert banned not in body, f"{banned} must not appear in the evaluator"
