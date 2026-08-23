"""The projection: canonical rows flattened into exactly the paths the AST reads.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §7.2 (the two queries, one read-only
  snapshot) and §7.3 (``projection.py``).
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, "Trigger
  arithmetic" — ``days_overdue`` is a reviewed derived field, and this module
  is where the arithmetic that replaces an AST node actually lives.

Two things these tests defend
-----------------------------
1. **The clock is the database's.** ``build_projection`` takes ``db_now`` out
   of the case row and has no other way to obtain a time. Any other clock would
   compare a worker's wall time against a database-written deadline.
2. **A binding that does not resolve is an error, not a no-op.** A trigger
   referencing a commitment that is no longer on the case indicates a Kernel bug
   or a hand-edited row. Silently treating it as "nothing to do" is how an
   obligation gets forgotten, which is the worst failure this product has.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.control_plane.app.triggers.ast import parse_spec
from services.control_plane.app.triggers.projection import (
    BindingUnresolved,
    build_projection,
)
from services.control_plane.app.triggers.registry import (
    COMMITMENT_FIELDS,
    STATIC_FIELDS,
    resolve_field,
)
from services.control_plane.tests.events._support import canon

pytestmark = pytest.mark.unit


def _hero_projection(**case_overrides: object):
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    return build_projection(
        case_row=canon.case_row(**case_overrides),  # type: ignore[arg-type]
        commitment_rows={canon.HERO_COMMITMENT_ID: canon.commitment_row()},
        trigger_row=canon.trigger_row(),
        spec=spec,
    )


# ---------------------------------------------------------------------------
# Completeness: every whitelisted path the spec can read is materialised.
# ---------------------------------------------------------------------------


def test_every_registry_path_is_materialised_for_the_declared_bindings() -> None:
    """The evaluator's ``values[node.path]`` must never miss.

    §8 documents the lookup as infallible "because the projection materialises
    every whitelisted path for the declared bindings before evaluation begins".
    That is a claim about this function, so it is asserted here rather than
    trusted.
    """
    projection = _hero_projection()
    expected = set(STATIC_FIELDS)
    expected |= {f"commitments.deposit.{leaf}" for leaf in COMMITMENT_FIELDS}
    assert set(projection.values) == expected


def test_the_clock_comes_from_the_case_row_and_nowhere_else() -> None:
    other_instant = datetime(2027, 2, 1, 9, 0, 0, tzinfo=UTC)
    projection = _hero_projection(db_now=other_instant)
    assert projection.values["clock.now"] == other_instant
    assert projection.db_now == other_instant


def test_a_non_utc_db_now_is_normalised() -> None:
    from datetime import timezone

    eastern = timezone(timedelta(hours=-4))
    projection = _hero_projection(db_now=canon.DEMO_CLOCK_UTC.astimezone(eastern))
    assert projection.values["clock.now"] == canon.DEMO_CLOCK_UTC
    assert projection.values["clock.now"].tzinfo is UTC


# ---------------------------------------------------------------------------
# The derived fields — the arithmetic that is NOT in the AST.
# ---------------------------------------------------------------------------


def test_days_overdue_is_ninety_five_against_the_demo_clock() -> None:
    """``CANONICAL_DECISIONS.md``: 95 days, derived rather than transcribed.

    ``DAYS_OVERDUE_AT_DEMO`` in ``_support.canon`` is the canon's own figure.
    This test recomputes it from ``due_at`` and the demo clock, so the two
    agree only if the derivation is right.
    """
    projection = _hero_projection()
    assert projection.values["commitments.deposit.days_overdue"] == canon.DAYS_OVERDUE_AT_DEMO


def test_days_overdue_is_negative_before_the_deadline() -> None:
    """ "May be negative before the deadline" — §5.3, and it must not clamp.

    Clamping to zero would make "0 days overdue" mean both "due today" and "due
    next year", and a predicate comparing against it could not tell them apart.
    """
    projection = _hero_projection(db_now=canon.DEPOSIT_DUE_AT - timedelta(days=3))
    assert projection.values["commitments.deposit.days_overdue"] == -3


def test_days_overdue_is_null_when_there_is_no_deadline() -> None:
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    projection = build_projection(
        case_row=canon.case_row(),
        commitment_rows={canon.HERO_COMMITMENT_ID: canon.commitment_row(due_at=None)},
        trigger_row=canon.trigger_row(),
        spec=spec,
    )
    assert projection.values["commitments.deposit.days_overdue"] is None


def test_days_overdue_floors_rather_than_rounds() -> None:
    """23 hours past the deadline is 0 days overdue, not 1.

    Rounding would let a follow-up claim "1 day overdue" on the evening of the
    deadline, which is a small dishonesty in a letter the user signs.
    """
    projection = _hero_projection(db_now=canon.DEPOSIT_DUE_AT + timedelta(hours=23))
    assert projection.values["commitments.deposit.days_overdue"] == 0


def test_days_since_last_activity_uses_the_same_derivation() -> None:
    projection = _hero_projection()
    # last_activity_at is 2026-06-01T09:00Z; the demo clock is 2026-09-18T13:00Z.
    assert projection.values["case.days_since_last_activity"] == 109


def test_money_survives_the_projection_as_decimal() -> None:
    projection = _hero_projection()
    outstanding = projection.values["commitments.deposit.outstanding_amount"]
    total = projection.values["case.total_outstanding_amount"]
    assert isinstance(outstanding, Decimal)
    assert isinstance(total, Decimal)
    assert outstanding == canon.DEPOSIT_OUTSTANDING
    assert total == Decimal("2020.0000")


def test_a_mixed_currency_case_yields_a_null_currency_not_a_wrong_sum() -> None:
    """§5.2 and §17 R7. NULL here makes any predicate that checks it UNKNOWN."""
    projection = _hero_projection(outstanding_currency=None)
    assert projection.values["case.outstanding_currency"] is None


# ---------------------------------------------------------------------------
# Bindings — §7.3 and §10.4.
# ---------------------------------------------------------------------------


def test_an_unresolved_binding_raises_rather_than_evaluating() -> None:
    """§10.4. This is an ERROR, and the trigger stays ARMED for inspection.

    "Silently forgetting an obligation because of an internal error is the worst
    possible failure mode for this product."
    """
    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    with pytest.raises(BindingUnresolved) as excinfo:
        build_projection(
            case_row=canon.case_row(),
            commitment_rows={},
            trigger_row=canon.trigger_row(),
            spec=spec,
        )
    assert excinfo.value.binding == "deposit"
    assert excinfo.value.commitment_id == canon.HERO_COMMITMENT_ID


def test_a_commitment_row_for_another_case_is_simply_absent() -> None:
    """The security control is ``m.case_id = $1`` in query (2), not a filter here.

    A binding naming another case's commitment returns no row, which surfaces as
    ``BINDING_UNRESOLVED`` rather than as a cross-case read. This test states
    that consequence so a future refactor that "helpfully" looks the commitment
    up by id alone breaks it.
    """
    import uuid

    spec = parse_spec(canon.hero_predicate_document(), resolve_field)
    with pytest.raises(BindingUnresolved):
        build_projection(
            case_row=canon.case_row(),
            commitment_rows={uuid.uuid4(): canon.commitment_row()},
            trigger_row=canon.trigger_row(),
            spec=spec,
        )


def test_the_trigger_columns_are_readable_and_carry_the_armed_generation() -> None:
    projection = _hero_projection()
    assert projection.values["trigger.not_before"] == canon.TRIGGER_WAKE_AT
    assert projection.values["trigger.evaluation_version"] == 1
    assert projection.values["trigger.basis_case_revision"] == 11


def test_the_projection_carries_the_revision_the_fire_guard_compares() -> None:
    """``case_revision`` is re-read FOR UPDATE inside the fire transaction.

    If it moved between the read and the write, the evaluation was computed on
    data that is no longer current and the whole evaluation restarts. That guard
    needs the observed revision to be a first-class field, not a dict lookup a
    caller might forget.
    """
    projection = _hero_projection(revision=17)
    assert projection.case_revision == 17
    assert projection.values["case.revision"] == 17


def test_the_projection_module_reads_no_wall_clock() -> None:
    import inspect

    from services.control_plane.app.triggers import projection as projection_mod

    source = inspect.getsource(projection_mod)
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("datetime.now(", "time.time(", "date.today(", "utcnow("):
        assert banned not in body, f"{banned} must not appear in the projection"
