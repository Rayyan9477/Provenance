"""The re-evaluation is load-bearing, and this is how that is proved.

Authority
---------
- ``docs/quality/23_PHASE_GATES.md`` ``G10.7``::

      PV_SABOTAGE=triggers.evaluator.reevaluate_predicate pytest ... ; echo "exit=$?"
      #   -> FAILED; exit=1

- ``docs/EXECUTION/70_TASK_PLAN.md`` T10.4, and ``§23.5`` for the mechanism.

Why a green suite is not enough
-------------------------------
A passing test proves the tests pass. It does not prove they would fail if the
thing they test were broken. The claim this whole phase rests on — *the
predicate is re-evaluated against current canonical state at wake time, and a
scheduler event is never trusted* — is exactly the kind of claim a test suite
can appear to check while checking nothing, because the happy path looks
identical either way: the trigger fires, and it was going to fire anyway.

So the check is destructive. Neuter the re-evaluation and the resolution no-op
must go red. If it stays green, either nothing reaches the re-check or no test
depends on it, and both are the bug ``G10.7`` exists to find.

The wiring that makes the hook reachable
-----------------------------------------
``PV_SABOTAGE`` rebinds an attribute **on the module object**. A caller using
``from ... import reevaluate_predicate`` copies the reference at import time,
before the rebind is visible, and the sabotage silently never arrives — a green
run that proves the opposite of what it appears to.
:func:`test_the_service_reaches_the_hook_through_the_module_global` asserts the
call shape against the AST, which is the only reason the matrix entry can be
trusted.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from typing import Any

import pytest

from provenance_domain import money
from provenance_domain.enums import TriggerReasonCode, TriggerResult
from services.control_plane.app.triggers import evaluator as evaluator_mod
from services.control_plane.app.triggers import service as service_mod
from services.control_plane.app.triggers.evaluator import Evaluation, Tri
from services.control_plane.app.triggers.service import evaluate_trigger, scheduler_wake
from services.control_plane.tests.events._support import canon
from services.control_plane.tests.events._support.fakes import (
    FakeKernel,
    FakeProjectionReader,
    FakeTriggerStore,
)

pytestmark = pytest.mark.unit

TRACE_ID = uuid.UUID("2b6f8c14-9d33-4a02-8e77-13c5a90b6d21")


def _resolved_world() -> tuple[FakeTriggerStore, FakeProjectionReader, FakeKernel]:
    """D8's world: the deposit was paid in full and the case resolved."""
    row = canon.trigger_row()
    store = FakeTriggerStore(rows={row["id"]: row}, db_now=canon.DEMO_CLOCK_UTC)
    reader = FakeProjectionReader(
        case_row=canon.case_row(status="RESOLVED", resolved_at=canon.DEMO_CLOCK_UTC),
        commitment_rows={
            canon.HERO_COMMITMENT_ID: canon.commitment_row(
                status="FULFILLED", outstanding="0.0000", has_admitted_fulfillment=True
            )
        },
    )
    return store, reader, FakeKernel()


async def _evaluate(world: tuple[FakeTriggerStore, FakeProjectionReader, FakeKernel]) -> Any:
    store, reader, kernel = world
    return await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )


# ---------------------------------------------------------------------------
# The hook exists and is wired the way the mechanism requires.
# ---------------------------------------------------------------------------


def test_the_sabotage_hook_is_registered_under_the_gates_label() -> None:
    assert evaluator_mod.SABOTAGE_MODULE == "triggers.evaluator"
    assert evaluator_mod.SABOTAGE_HOOKS == ("reevaluate_predicate",)
    # Nothing is neutered on a normal run, or every other test here is a lie.
    assert evaluator_mod.SABOTAGED_SYMBOLS == ()


def test_the_service_reaches_the_hook_through_the_module_global() -> None:
    """``evaluator_mod.reevaluate_predicate(...)``, asserted against the AST.

    A bare-name call would mean a ``from``-import copied the reference before
    ``PV_SABOTAGE`` could rebind it, and the sabotage would never arrive.
    """
    tree = ast.parse(inspect.getsource(service_mod))
    attribute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reevaluate_predicate"
    ]
    assert attribute_calls, "the re-check must be called through the module object"
    for call in attribute_calls:
        assert isinstance(call.func, ast.Attribute)
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "evaluator_mod"

    bare_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "reevaluate_predicate"
    ]
    assert not bare_calls, "a bare-name call would defeat PV_SABOTAGE silently"


def test_the_service_does_not_from_import_the_hook() -> None:
    tree = ast.parse(inspect.getsource(service_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "reevaluate_predicate" not in {alias.name for alias in node.names}


def test_install_sabotage_can_reach_this_symbol() -> None:
    """The mechanism, exercised as a pure function over an explicit namespace."""
    namespace: dict[str, Any] = {"reevaluate_predicate": evaluator_mod.reevaluate_predicate}
    replaced = money.install_sabotage(
        namespace,
        evaluator_mod.SABOTAGE_MODULE,
        evaluator_mod.SABOTAGE_HOOKS,
        "triggers.evaluator.reevaluate_predicate",
    )
    assert replaced == ("reevaluate_predicate",)
    assert namespace["reevaluate_predicate"] is not evaluator_mod.reevaluate_predicate


def test_a_stale_matrix_entry_fails_loudly() -> None:
    """A symbol the matrix names but this module does not define must raise.

    Skipping it silently would report a green sabotage run for a symbol nobody
    neutered, which is the failure mode the guard exists to prevent.
    """
    with pytest.raises(KeyError):
        money.install_sabotage(
            {},
            evaluator_mod.SABOTAGE_MODULE,
            ("reevaluate_predicate",),
            "triggers.evaluator.reevaluate_predicate",
        )


# ---------------------------------------------------------------------------
# The destructive check itself.
# ---------------------------------------------------------------------------


async def test_the_resolution_no_op_holds_when_the_recheck_is_intact() -> None:
    """The control. D8's assertion, green, before anything is broken."""
    outcome = await _evaluate(_resolved_world())
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.CASE_RESOLVED
    assert outcome.fired_at is None


async def test_neutering_the_recheck_turns_the_resolution_no_op_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trust the timer instead of the world, and a resolved case fires.

    The stub is the bug written out: it returns the verdict the trigger held at
    arm time — back in May, when the deposit really was overdue — and ignores the
    projection it was handed. That is precisely "a trigger that fires because a
    timer said so", and the assertion below is that D8 no longer holds.
    """
    stale_truth = Evaluation(
        result=Tri.TRUE,
        evaluator_code_version=evaluator_mod.EVALUATOR_CODE_VERSION,
        predicate_sha256="0" * 64,
        observed={},
        node_trace=(),
    )
    monkeypatch.setattr(
        evaluator_mod,
        "reevaluate_predicate",
        lambda spec, values: stale_truth,
    )

    outcome = await _evaluate(_resolved_world())

    # Every one of D8's assertions is now false.
    assert outcome.result is not TriggerResult.DISARMED
    assert outcome.reason_code is not TriggerReasonCode.CASE_RESOLVED
    assert outcome.result is TriggerResult.FIRED
    assert outcome.fired_at is not None


async def test_the_identity_neutering_also_fails_rather_than_passing_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``make sabotage`` uses the identity stub, so that shape is checked too.

    It returns the spec it was handed, which has no ``result``. The evaluation
    cannot complete, and the suite goes red — a louder failure than the stale
    verdict above, and equally acceptable to ``G10.7``: what is not acceptable
    is green.
    """
    monkeypatch.setattr(
        evaluator_mod, "reevaluate_predicate", money.install_sabotage.__globals__["_identity"]
    )
    with pytest.raises(AttributeError):
        await _evaluate(_resolved_world())
