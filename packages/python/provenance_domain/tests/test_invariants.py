"""T1.4 — the four canon invariants, the grounding invariant, and the map.

Sources, transcribed by hand rather than imported:

* `docs/00_PRODUCT.md` §0.1 — the four invariants, in their original wording:
  1. Evidence is append-only.  2. Beliefs are revisable.
  3. State is transactional.   4. Actions are permissioned.
* `docs/00_PRODUCT.md` §0.2 — the grounding invariant: a canonical belief
  version must carry at least one `belief_support` edge unless it is an
  explicitly declared deterministic derivation, in which case it carries a
  `source_kind = 'DERIVATION'` edge instead and is still GROUNDED.
* `docs/specs/11_CONTRACTS.md` §5.1 — `derive_outstanding`,
  `derive_commitment_status`, `assert_commitment_consistent`,
  `assert_revision_increment`, the money scale rules, and the three
  configuration thresholds.
* `docs/specs/10_DATABASE_DDL.md` §5.4 and §5.6 — the append-only shape of
  `evidence_items`: rows are never deleted, `normalized_text`, `exact_text`,
  `source_locator` and `embedding` are never overwritten, and retraction is a
  one-way status transition that must say when and why.
* `docs/specs/11_CONTRACTS.md` §12 (`BeliefVersionRef`) and §14
  (`LineageEntry`) — a version above 1 must name the version it supersedes,
  and a supersession without a reason code is unauditable.
* `docs/quality/23_PHASE_GATES.md` §23.15 — the count of tests is not
  reportable evidence; the map is. Two tests at the end of this file are about
  the map itself, because a map that names a skipped test proves nothing and
  the tool that says so must itself be proven.

Every literal amount here is a `Decimal`. A `float` in a money test would be
the defect `no-float-money` exists to catch, written into the proof itself.

The `G1.7` sabotage
-------------------
`PV_SABOTAGE=provenance_domain.money.outstanding` rebinds `outstanding` **on
the module object**. `invariants.derive_outstanding` reaches it through
`money.outstanding`, so the rebind is visible here and the invariant-3 tests
below go red. That is the required outcome: a green run under sabotage is a
gate failure, not a relief.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from provenance_domain import invariants, money, transitions
from provenance_domain.enums import (
    ActionState,
    CommitmentStatus,
    EpistemicStatus,
    RetractionStatus,
    SupportSourceKind,
)
from tools import invariant_map_check

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
THIS_FILE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
INVARIANTS_MD = REPO_ROOT / "packages" / "python" / "provenance_domain" / "INVARIANTS.md"


# ---------------------------------------------------------------------------
# Fixtures, hand-built rather than imported from the module under test
# ---------------------------------------------------------------------------

_EVIDENCE_ID = "0199f2c2-0000-4000-8000-000000001b07"
_CORRECTION_ID = "0199f2c2-0000-4000-8000-000000001b99"


def _evidence_row(**overrides: object) -> dict[str, object]:
    """One `evidence_items` row, ACTIVE, with the columns that matter."""
    row: dict[str, object] = {
        "id": _EVIDENCE_ID,
        "artifact_id": "0199f2c1-0000-4000-8000-00000000a41d",
        "evidence_type": "DATE_ASSERTION",
        "normalized_text": "Service period 2026-06-01 through 2026-06-30",
        "exact_text": "Service period 06/01/2026 - 06/30/2026",
        "source_locator": '{"part":"text/plain","char_start":412,"char_end":455}',
        "extraction_confidence": Decimal("0.9840"),
        "source_authority": Decimal("0.8800"),
        "embedding": "<1024-dim>",
        "normalized_text_sha256": "7d2fc19a" * 8,
        "retraction_status": str(RetractionStatus.ACTIVE),
        "retracted_at": None,
        "retracted_by_evidence_id": None,
        "retraction_reason_code": None,
        "is_retrieval_eligible": True,
    }
    row.update(overrides)
    return row


def _retracted_row(**overrides: object) -> dict[str, object]:
    """The same row after the Kernel's `§5.6` status-block update.

    Defaults are MERGED rather than passed as keywords beside ``**overrides``.
    Forwarding both raises ``TypeError: got multiple values for keyword
    argument`` the moment a caller overrides one of the five status-block
    columns -- which is exactly what a test of a bad retraction must do.
    """
    fields: dict[str, object] = {
        "retraction_status": str(RetractionStatus.SUPERSEDED),
        "retracted_at": "2026-09-18T14:05:11Z",
        "retracted_by_evidence_id": _CORRECTION_ID,
        "retraction_reason_code": "USER_CORRECTION",
        "is_retrieval_eligible": False,
    }
    fields.update(overrides)
    return _evidence_row(**fields)


def _revision(**overrides: object) -> invariants.BeliefRevision:
    fields: dict[str, Any] = {
        "belief_id": "0199f2d0-0000-4000-8000-0000000000b1",
        "previous_version_id": "0199f2d1-0000-4000-8000-000000000001",
        "previous_version_no": 1,
        "new_version_id": "0199f2d1-0000-4000-8000-000000000002",
        "new_version_no": 2,
        "supersedes_version_id": "0199f2d1-0000-4000-8000-000000000001",
        "supersession_reason_code": "CONTRADICTORY_EVIDENCE",
    }
    fields.update(overrides)
    return invariants.BeliefRevision(**fields)


def _approved_action(**overrides: object) -> invariants.ActionExecution:
    fields: dict[str, Any] = {
        "from_state": str(ActionState.APPROVED),
        "to_state": str(ActionState.EXECUTING),
        "reason_code": "REVALIDATION_PASSED",
        "proposal_committed": True,
        "from_agent_scratchpad": False,
        "approval_draft_sha256": "a" * 64,
        "draft_sha256": "a" * 64,
        "basis_case_revision": 12,
        "current_case_revision": 12,
    }
    fields.update(overrides)
    return invariants.ActionExecution(**fields)


# ---------------------------------------------------------------------------
# Invariant 1 — evidence is append-only
# ---------------------------------------------------------------------------


def test_invariant_1_evidence_is_append_only() -> None:
    """00_PRODUCT.md §0.1.1 — admitted evidence is never rewritten or deleted.

    Corrections arrive as *new* evidence. The one permitted change to an
    existing row is the retraction status block of `10_DATABASE_DDL.md` §5.6,
    which points at the evidence that superseded it; `normalized_text`,
    `exact_text`, `source_locator` and `embedding` are never touched.
    """
    before = _evidence_row()

    # An unchanged row is trivially append-only.
    assert invariants.evidence_change_is_append_only(before, _evidence_row())

    # The §5.6 retraction is the only permitted mutation, and it is one-way.
    retracted = invariants.evidence_change_is_append_only(before, _retracted_row())
    assert retracted.append_only is True
    assert retracted.code is None
    assert set(retracted.changed_fields) <= invariants.EVIDENCE_MUTABLE_FIELDS
    assert "retraction_status" in retracted.changed_fields

    # Rewriting the observation is the failure this invariant exists to stop.
    rewritten = invariants.evidence_change_is_append_only(
        before, _evidence_row(normalized_text="Service period 2026-07-01 through 2026-07-31")
    )
    assert not rewritten
    assert rewritten.code == "EVIDENCE_FIELD_REWRITTEN"
    assert rewritten.changed_fields == ("normalized_text",)
    with pytest.raises(invariants.InvariantViolation) as exc:
        invariants.assert_evidence_append_only(
            before, _evidence_row(embedding="<a different 1024-dim vector>")
        )
    assert exc.value.code == "EVIDENCE_FIELD_REWRITTEN"

    # Deletion is not a change; it is the absence of the record.
    deleted = invariants.evidence_change_is_append_only(before, None)
    assert not deleted
    assert deleted.code == "EVIDENCE_DELETED"

    # The mutable set is exactly the retraction block. Nothing else joins it.
    assert (
        frozenset(
            {
                "retraction_status",
                "retracted_at",
                "retracted_by_evidence_id",
                "retraction_reason_code",
                "is_retrieval_eligible",
            }
        )
        == invariants.EVIDENCE_MUTABLE_FIELDS
    )


def test_append_only_allows_only_the_retraction_status_block() -> None:
    """`ck_evidence_retraction_consistent`: say when, and say why.

    A non-ACTIVE row without `retracted_at` and a reason code is a row that
    was quietly hidden from retrieval. The database refuses it; this predicate
    refuses it before the database is ever reached.
    """
    before = _evidence_row()

    unexplained = invariants.evidence_change_is_append_only(
        before,
        _evidence_row(
            retraction_status=str(RetractionStatus.RETRACTED),
            is_retrieval_eligible=False,
        ),
    )
    assert not unexplained
    assert unexplained.code == "EVIDENCE_RETRACTION_UNEXPLAINED"

    bad_reason = invariants.evidence_change_is_append_only(
        before, _retracted_row(retraction_reason_code="BECAUSE_I_SAID_SO")
    )
    assert not bad_reason
    assert bad_reason.code == "EVIDENCE_RETRACTION_REASON_UNKNOWN"

    # `is_retrieval_eligible` is a STORED computed column: it may not disagree
    # with the status it is computed from, or retracted vectors keep ranking.
    desynced = invariants.evidence_change_is_append_only(
        before, _retracted_row(is_retrieval_eligible=True)
    )
    assert not desynced
    assert desynced.code == "EVIDENCE_RETRIEVAL_FLAG_DESYNC"

    # An ACTIVE row must not pretend it was retracted.
    pretending = invariants.evidence_change_is_append_only(
        before, _evidence_row(retracted_at="2026-09-18T14:05:11Z")
    )
    assert not pretending
    assert pretending.code == "EVIDENCE_RETRACTION_UNEXPLAINED"

    # Every reason code the DDL allows, and nothing else.
    assert (
        frozenset(
            {
                "USER_CORRECTION",
                "EXTRACTION_ERROR",
                "SOURCE_WITHDRAWN",
                "DUPLICATE_OF_OTHER",
                "PARSER_DEFECT",
                "ADVERSARIAL_CONTENT",
            }
        )
        == invariants.RETRACTION_REASON_CODES
    )


def test_append_only_refuses_unretraction_identity_change_and_self_retraction() -> None:
    """Retraction is one-way, the row keeps its identity, and nothing retracts itself."""
    active = _evidence_row()
    retracted = _retracted_row()

    unretracted = invariants.evidence_change_is_append_only(retracted, active)
    assert not unretracted
    assert unretracted.code == "EVIDENCE_UNRETRACTED"

    # SUPERSEDED -> RETRACTED is still a rewrite of a settled fact.
    restatused = invariants.evidence_change_is_append_only(
        retracted, _retracted_row(retraction_status=str(RetractionStatus.RETRACTED))
    )
    assert not restatused
    assert restatused.code == "EVIDENCE_UNRETRACTED"

    # A row whose id moved is a different row, and comparing them is a defect.
    reidentified = invariants.evidence_change_is_append_only(
        active, _evidence_row(id=_CORRECTION_ID)
    )
    assert not reidentified
    assert reidentified.code == "EVIDENCE_IDENTITY_CHANGED"

    # `ck_evidence_no_self_retract`.
    itself = invariants.evidence_change_is_append_only(
        active, _retracted_row(retracted_by_evidence_id=_EVIDENCE_ID)
    )
    assert not itself
    assert itself.code == "EVIDENCE_SELF_RETRACTION"

    # A column that vanished, or one that appeared, is a schema change and not
    # an append. Both are refused rather than diffed away.
    dropped = dict(active)
    del dropped["exact_text"]
    assert invariants.evidence_change_is_append_only(active, dropped).code == (
        "EVIDENCE_FIELD_DROPPED"
    )
    grown = _evidence_row(sentiment_score=Decimal("0.5000"))
    assert invariants.evidence_change_is_append_only(active, grown).code == "EVIDENCE_FIELD_ADDED"

    # An unknown status is refused rather than assumed benign.
    unknown = invariants.evidence_change_is_append_only(
        active, _retracted_row(retraction_status="ARCHIVED")
    )
    assert not unknown
    assert unknown.code == "EVIDENCE_RETRACTION_STATUS_UNKNOWN"


# ---------------------------------------------------------------------------
# Invariant 2 — beliefs are revisable
# ---------------------------------------------------------------------------


def test_invariant_2_beliefs_are_revisable() -> None:
    """00_PRODUCT.md §0.1.2 — a changed conclusion creates a new belief version.

    The prior version and the reason it was superseded are both preserved. A
    revision that edits the previous row in place destroys the lineage the
    product exists to render, so reusing the version id is refused outright.
    """
    verdict = invariants.belief_revision_verdict(_revision())
    assert verdict
    assert verdict.code is None
    invariants.assert_belief_revisable(_revision())

    # v1 is a creation: it has no predecessor and needs no supersession reason.
    root = invariants.belief_revision_verdict(
        _revision(
            previous_version_id=None,
            previous_version_no=0,
            new_version_no=1,
            supersedes_version_id=None,
            supersession_reason_code=None,
        )
    )
    assert root
    assert root.code is None

    # Editing the prior version in place is not a revision, it is a rewrite.
    overwritten = invariants.belief_revision_verdict(
        _revision(new_version_id="0199f2d1-0000-4000-8000-000000000001")
    )
    assert not overwritten
    assert overwritten.code == "BELIEF_VERSION_OVERWRITTEN"

    # The hero supersession reason code is `CONTRADICTORY_EVIDENCE`.
    assert _revision().supersession_reason_code == "CONTRADICTORY_EVIDENCE"


def test_revision_requires_predecessor_and_supersession_reason() -> None:
    """11_CONTRACTS.md §12 and §14 — lineage may not have a gap or a silence."""
    missing = invariants.belief_revision_verdict(_revision(supersedes_version_id=None))
    assert not missing
    assert missing.code == "LINEAGE_PREDECESSOR_MISSING"

    mismatched = invariants.belief_revision_verdict(
        _revision(supersedes_version_id="0199f2d1-0000-4000-8000-0000000000ff")
    )
    assert not mismatched
    assert mismatched.code == "LINEAGE_PREDECESSOR_MISMATCH"

    silent = invariants.belief_revision_verdict(_revision(supersession_reason_code=None))
    assert not silent
    assert silent.code == "SUPERSESSION_UNEXPLAINED"
    blank = invariants.belief_revision_verdict(_revision(supersession_reason_code="   "))
    assert not blank
    assert blank.code == "SUPERSESSION_UNEXPLAINED"

    # v_{n+1}, not v_{n+2}: a skipped number is an unexplained lost version.
    gap = invariants.belief_revision_verdict(_revision(new_version_no=3))
    assert not gap
    assert gap.code == "LINEAGE_GAP"

    # A root version that names a predecessor is a contradiction in terms.
    rooted = invariants.belief_revision_verdict(
        _revision(previous_version_id=None, previous_version_no=0, new_version_no=1)
    )
    assert not rooted
    assert rooted.code == "LINEAGE_ROOT_HAS_PREDECESSOR"

    with pytest.raises(invariants.InvariantViolation) as exc:
        invariants.assert_belief_revisable(_revision(supersedes_version_id=None))
    assert exc.value.code == "LINEAGE_PREDECESSOR_MISSING"


# ---------------------------------------------------------------------------
# Invariant 3 — state is transactional
# ---------------------------------------------------------------------------


def test_invariant_3_state_is_transactional() -> None:
    """00_PRODUCT.md §0.1.3 — no impossible partial aggregate state.

    The hero commitment: USD 420 owed, USD 200 paid, USD 220 outstanding,
    PARTIAL. `11_CONTRACTS.md` §5.1 names this exact scenario in
    `assert_commitment_consistent`'s docstring.

    This test goes red under `PV_SABOTAGE=provenance_domain.money.outstanding`
    and that is deliberate: `derive_outstanding` reaches the identity through
    the `money` module global, so neutering it is visible here.
    """
    amounts = invariants.derive_outstanding(
        currency="USD",
        committed=Decimal("420.00"),
        fulfilled=Decimal("200.00"),
        fulfilment_currency="USD",
    )
    assert amounts.outstanding == Decimal("220.0000")
    assert str(amounts.outstanding) == "220.0000"
    assert amounts.committed == Decimal("420.0000")
    assert amounts.fulfilled == Decimal("200.0000")
    assert amounts.currency == "USD"

    status = invariants.derive_commitment_status(
        amounts, current=CommitmentStatus.ACTIVE, has_blocking_conflict=False
    )
    assert status is CommitmentStatus.PARTIAL
    invariants.assert_commitment_consistent(amounts, status)

    # One canonical commit moves a case revision by exactly one; a no-op does
    # not move it at all.
    invariants.assert_revision_increment(12, 13, changed=True)
    invariants.assert_revision_increment(12, 12, changed=False)
    with pytest.raises(invariants.InvariantViolation) as exc:
        invariants.assert_revision_increment(12, 14, changed=True)
    assert exc.value.code == "REVISION_INCREMENT"


def test_derive_outstanding_never_clamps_over_fulfilment() -> None:
    """11_CONTRACTS.md §5.1 — over-fulfilment yields a negative outstanding.

    12_KERNEL_ALGORITHMS.md §4.3 makes that visibility the whole point of "do
    not silently clamp": the Kernel turns the negative number into a
    `FULFILLMENT_CONFLICT` with an audit trail. Returning zero here would
    destroy the only evidence that anything was wrong.
    """
    over = invariants.derive_outstanding(
        currency="USD",
        committed=Decimal("420.00"),
        fulfilled=Decimal("500.00"),
        fulfilment_currency="USD",
    )
    assert over.outstanding == Decimal("-80.0000")
    assert over.committed == Decimal("420.0000")

    disputed = invariants.derive_commitment_status(
        over, current=CommitmentStatus.PARTIAL, has_blocking_conflict=False
    )
    assert disputed is CommitmentStatus.DISPUTED

    settled = invariants.derive_outstanding(
        currency="USD",
        committed=Decimal("1200.0000"),
        fulfilled=Decimal("1200.0000"),
        fulfilment_currency="USD",
    )
    assert settled.outstanding == Decimal("0.0000")

    # A negative commitment is not an obligation, and a negative fulfilment is
    # a refund, which is its own event. Neither is a clamping candidate.
    with pytest.raises(invariants.InvariantViolation) as negative:
        invariants.derive_outstanding(
            currency="USD",
            committed=Decimal("-1.00"),
            fulfilled=Decimal("0.00"),
            fulfilment_currency="USD",
        )
    assert negative.value.code == "NEGATIVE_COMMITMENT"
    with pytest.raises(invariants.InvariantViolation) as refund:
        invariants.derive_outstanding(
            currency="USD",
            committed=Decimal("100.00"),
            fulfilled=Decimal("-1.00"),
            fulfilment_currency="USD",
        )
    assert refund.value.code == "NEGATIVE_FULFILMENT"


def test_derive_outstanding_refuses_to_cross_currencies() -> None:
    """USD minus EUR is not a number; it is a missing conversion event."""
    with pytest.raises(invariants.CurrencyMismatchError) as exc:
        invariants.derive_outstanding(
            currency="USD",
            committed=Decimal("420.00"),
            fulfilled=Decimal("200.00"),
            fulfilment_currency="EUR",
        )
    assert exc.value.code == "CURRENCY_MISMATCH"
    assert "explicit conversion" in str(exc.value)
    assert issubclass(invariants.CurrencyMismatchError, invariants.InvariantViolation)
    assert issubclass(invariants.InvariantViolation, ValueError)

    # The signature is §5.1's, keyword-only, with the fulfilment currency
    # carried separately so the mismatch is expressible at all.
    signature = inspect.signature(invariants.derive_outstanding)
    assert list(signature.parameters) == [
        "currency",
        "committed",
        "fulfilled",
        "fulfilment_currency",
    ]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in signature.parameters.values())


def test_derive_outstanding_calls_money_outstanding_through_the_module_global() -> None:
    """The `G1.7` wiring, asserted structurally rather than hoped for.

    `PV_SABOTAGE` rebinds the named symbol **on the module object** at import.
    A `from provenance_domain.money import outstanding` in `invariants.py`
    would copy the reference before the rebind is visible, the sabotage would
    never reach this file, and `G1.7` would report a green sabotage run — which
    `23_PHASE_GATES.md` §23 counts as a gate failure, not a pass.

    Replacing the attribute on the module here reproduces exactly what the
    sabotage hook does, so this assertion fails if the import style ever
    regresses to a `from`-import or the subtraction is inlined locally.
    """
    sentinel = Decimal("777.0000")
    original = money.outstanding
    try:
        money.outstanding = lambda *_a, **_k: sentinel  # type: ignore[assignment]
        amounts = invariants.derive_outstanding(
            currency="USD",
            committed=Decimal("420.00"),
            fulfilled=Decimal("200.00"),
            fulfilment_currency="USD",
        )
    finally:
        money.outstanding = original  # type: ignore[assignment]
    assert amounts.outstanding == sentinel, (
        "invariants.derive_outstanding did not reach money.outstanding through the "
        "module global; the PV_SABOTAGE hook cannot reach this file"
    )

    # And the module really does hold a module reference, not a copied function.
    #
    # Checked against the AST, not the source text. A substring scan reports the
    # docstring above -- which quotes the forbidden import in order to forbid it
    # -- as a violation. Prose describing a prohibition is not a breach of it,
    # and a guard that cannot tell the two apart trains people to delete the
    # explanation instead of the defect.
    tree = ast.parse(inspect.getsource(invariants))
    from_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("provenance_domain.money")
    ]
    assert not from_imports, (
        "invariants.py `from`-imports out of provenance_domain.money at line(s) "
        f"{[n.lineno for n in from_imports]}. That copies the reference before "
        "PV_SABOTAGE can rebind it on the module object, so the sabotage never "
        "reaches this file and G1.7 reports a green run it did not earn."
    )
    module_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "provenance_domain"
        for alias in node.names
    ]
    assert "money" in module_imports, (
        "invariants.py must import the `money` MODULE (`from provenance_domain "
        "import money`), so that attribute lookup happens at call time"
    )
    attribute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "outstanding"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "money"
    ]
    assert attribute_calls, "no `money.outstanding(...)` attribute call found in invariants.py"


def test_derive_commitment_status_is_the_only_money_to_status_map() -> None:
    """11_CONTRACTS.md §5.1 — the only place status is computed from money."""

    def amounts(committed: str, fulfilled: str) -> invariants.CommitmentAmounts:
        return invariants.derive_outstanding(
            currency="USD",
            committed=Decimal(committed),
            fulfilled=Decimal(fulfilled),
            fulfilment_currency="USD",
        )

    fully_paid = amounts("420.00", "420.00")
    assert (
        invariants.derive_commitment_status(
            fully_paid, current=CommitmentStatus.PARTIAL, has_blocking_conflict=False
        )
        is CommitmentStatus.FULFILLED
    )
    # A blocking conflict outranks the arithmetic in every case.
    assert (
        invariants.derive_commitment_status(
            fully_paid, current=CommitmentStatus.PARTIAL, has_blocking_conflict=True
        )
        is CommitmentStatus.DISPUTED
    )

    untouched = amounts("420.00", "0.00")
    assert (
        invariants.derive_commitment_status(
            untouched, current=CommitmentStatus.PROPOSED, has_blocking_conflict=False
        )
        is CommitmentStatus.PROPOSED
    )
    assert (
        invariants.derive_commitment_status(
            untouched, current=CommitmentStatus.ACTIVE, has_blocking_conflict=False
        )
        is CommitmentStatus.ACTIVE
    )

    # Zero committed and zero fulfilled is not FULFILLED: nothing was owed, so
    # nothing was discharged.
    nothing = amounts("0.00", "0.00")
    assert (
        invariants.derive_commitment_status(
            nothing, current=CommitmentStatus.ACTIVE, has_blocking_conflict=False
        )
        is CommitmentStatus.ACTIVE
    )


def test_assert_commitment_consistent_rejects_impossible_aggregates() -> None:
    """The aggregate must not be left in a state the arithmetic forbids."""
    good = invariants.CommitmentAmounts(
        currency="USD",
        committed=Decimal("420.0000"),
        fulfilled=Decimal("200.0000"),
        outstanding=Decimal("220.0000"),
    )
    invariants.assert_commitment_consistent(good, CommitmentStatus.PARTIAL)

    undreived = invariants.CommitmentAmounts(
        currency="USD",
        committed=Decimal("420.0000"),
        fulfilled=Decimal("200.0000"),
        outstanding=Decimal("0.0000"),
    )
    with pytest.raises(invariants.InvariantViolation) as exc:
        invariants.assert_commitment_consistent(undreived, CommitmentStatus.PARTIAL)
    assert exc.value.code == "OUTSTANDING_NOT_DERIVED"

    with pytest.raises(invariants.InvariantViolation) as fulfilled_exc:
        invariants.assert_commitment_consistent(good, CommitmentStatus.FULFILLED)
    assert fulfilled_exc.value.code == "FULFILLED_WITH_OUTSTANDING"

    nothing_paid = invariants.CommitmentAmounts(
        currency="USD",
        committed=Decimal("420.0000"),
        fulfilled=Decimal("0.0000"),
        outstanding=Decimal("420.0000"),
    )
    with pytest.raises(invariants.InvariantViolation) as partial_exc:
        invariants.assert_commitment_consistent(nothing_paid, CommitmentStatus.PARTIAL)
    assert partial_exc.value.code == "PARTIAL_WITHOUT_FULFILMENT"


def test_assert_revision_increment_moves_by_exactly_one() -> None:
    """Invariant 3, expressed as the case revision counter.

    A no-op that increments the revision invalidates every approval bound to
    the old number (`11_CONTRACTS.md` §17 `basis_case_revision`), and a real
    change that does not increment it lets a stale approval execute.
    """
    invariants.assert_revision_increment(0, 1, changed=True)
    invariants.assert_revision_increment(41, 41, changed=False)

    with pytest.raises(invariants.InvariantViolation) as still:
        invariants.assert_revision_increment(41, 41, changed=True)
    assert still.value.code == "REVISION_INCREMENT"
    assert "changed=True" in still.value.detail

    with pytest.raises(invariants.InvariantViolation) as moved:
        invariants.assert_revision_increment(41, 42, changed=False)
    assert moved.value.code == "REVISION_INCREMENT"

    with pytest.raises(invariants.InvariantViolation):
        invariants.assert_revision_increment(41, 40, changed=True)


# ---------------------------------------------------------------------------
# Invariant 4 — actions are permissioned
# ---------------------------------------------------------------------------


def test_invariant_4_actions_are_permissioned() -> None:
    """00_PRODUCT.md §0.1.4 — no uncommitted proposal and no agent scratchpad
    may produce an external side effect.

    `EXECUTING` is the state in which bytes leave the building. Reaching it
    requires a committed proposal, a legal guarded transition, and an approval
    that still binds to the draft and the case revision it was given.
    """
    invariants.assert_action_permissioned(_approved_action())

    with pytest.raises(invariants.InvariantViolation) as uncommitted:
        invariants.assert_action_permissioned(_approved_action(proposal_committed=False))
    assert uncommitted.value.code == "ACTION_UNCOMMITTED_PROPOSAL"

    with pytest.raises(invariants.InvariantViolation) as scratchpad:
        invariants.assert_action_permissioned(_approved_action(from_agent_scratchpad=True))
    assert scratchpad.value.code == "ACTION_FROM_SCRATCHPAD"

    # There is no route to EXECUTING from PROPOSED or NEEDS_REVIEW: the only
    # initial edge is APPROVED -> EXECUTING, guarded by REVALIDATION_PASSED.
    for origin in (ActionState.PROPOSED, ActionState.NEEDS_REVIEW):
        with pytest.raises(invariants.InvariantViolation) as illegal:
            invariants.assert_action_permissioned(_approved_action(from_state=str(origin)))
        assert illegal.value.code == "ACTION_TRANSITION_ILLEGAL"

    with pytest.raises(invariants.InvariantViolation) as unguarded:
        invariants.assert_action_permissioned(_approved_action(reason_code=None))
    assert unguarded.value.code == "ACTION_TRANSITION_ILLEGAL"

    # A retry from FAILED_RETRYABLE uses the same guard and is permitted.
    invariants.assert_action_permissioned(
        _approved_action(from_state=str(ActionState.FAILED_RETRYABLE))
    )


def test_action_execution_binds_to_draft_hash_and_case_revision() -> None:
    """An approval binds to a `sha256` and a `case.revision`, or it is stale."""
    with pytest.raises(invariants.InvariantViolation) as rehashed:
        invariants.assert_action_permissioned(_approved_action(draft_sha256="b" * 64))
    assert rehashed.value.code == "ACTION_DRAFT_HASH_CHANGED"

    with pytest.raises(invariants.InvariantViolation) as revised:
        invariants.assert_action_permissioned(_approved_action(current_case_revision=13))
    assert revised.value.code == "ACTION_CASE_REVISION_CHANGED"

    with pytest.raises(invariants.InvariantViolation) as no_hash:
        invariants.assert_action_permissioned(_approved_action(approval_draft_sha256=None))
    assert no_hash.value.code == "ACTION_DRAFT_HASH_MISSING"

    with pytest.raises(invariants.InvariantViolation) as no_revision:
        invariants.assert_action_permissioned(_approved_action(basis_case_revision=None))
    assert no_revision.value.code == "ACTION_CASE_REVISION_MISSING"

    # A transition that is not into EXECUTING performs no side effect, so the
    # binding is not required to evaluate it.
    invariants.assert_action_permissioned(
        invariants.ActionExecution(
            from_state=str(ActionState.APPROVED),
            to_state=str(ActionState.CANCELLED_STALE),
            reason_code="CASE_REVISION_CHANGED",
            proposal_committed=True,
        )
    )


# ---------------------------------------------------------------------------
# The grounding invariant
# ---------------------------------------------------------------------------


def test_grounding_invariant_holds_for_evidence_and_derivation() -> None:
    """00_PRODUCT.md §0.2 — grounded by an edge, or by a declared derivation.

    A canonical belief version must carry at least one `belief_support` edge
    unless it declares a deterministic derivation, in which case it carries a
    `source_kind = 'DERIVATION'` edge instead and is still GROUNDED.
    """
    grounded = invariants.grounding_verdict(support_edge_count=1)
    assert grounded
    assert grounded.route == "EVIDENCE"
    assert grounded.source_kind is SupportSourceKind.EVIDENCE
    assert grounded.derivation is None

    ungrounded = invariants.grounding_verdict(support_edge_count=0)
    assert not ungrounded
    assert ungrounded.route == "NONE"
    assert ungrounded.code == "BELIEF_NOT_GROUNDED"
    with pytest.raises(invariants.InvariantViolation) as exc:
        invariants.assert_grounded(support_edge_count=0)
    assert exc.value.code == "BELIEF_NOT_GROUNDED"

    # The derivation route: zero evidence edges and still grounded.
    derived = invariants.grounding_verdict(
        support_edge_count=0,
        derivation_kind="outstanding_from_committed_minus_fulfilled",
        derivation_version="1.0.0",
    )
    assert derived
    assert derived.route == "DERIVATION"
    assert derived.source_kind is SupportSourceKind.DERIVATION
    assert derived.derivation is not None
    assert derived.derivation.name == "outstanding_from_committed_minus_fulfilled"
    assert derived.derivation.input_kinds == ("COMMITMENT", "FULFILLMENT")
    invariants.assert_grounded(
        support_edge_count=0,
        derivation_kind="outstanding_from_committed_minus_fulfilled",
        derivation_version="1.0.0",
    )

    # It is the same registry `money.py` names, not a second copy of the rule.
    assert money.DERIVATION_NAME == "outstanding_from_committed_minus_fulfilled"
    assert derived.derivation.function_version == money.DERIVATION_VERSION


def test_grounding_refuses_an_unregistered_or_unversioned_derivation() -> None:
    """The exemption is closed, or the invariant is decoration.

    If any string could buy grounding, an agent could ground any claim by
    inventing a derivation name. Failing closed here is the whole value of
    `11_CONTRACTS.md` §5.3.
    """
    invented = invariants.grounding_verdict(
        support_edge_count=0, derivation_kind="outstanding_but_vibes", derivation_version="1.0.0"
    )
    assert not invented
    assert invented.code == "DERIVATION_UNREGISTERED"
    assert invented.route == "NONE"

    stale = invariants.grounding_verdict(
        support_edge_count=0,
        derivation_kind="outstanding_from_committed_minus_fulfilled",
        derivation_version="9.9.9",
    )
    assert not stale
    assert stale.code == "DERIVATION_UNREGISTERED"

    unversioned = invariants.grounding_verdict(
        support_edge_count=0, derivation_kind="outstanding_from_committed_minus_fulfilled"
    )
    assert not unversioned
    assert unversioned.code == "DERIVATION_VERSION_MISSING"

    # A retracted version is no longer canonical, so the invariant does not
    # constrain it. `11_CONTRACTS.md` §12 makes the same exemption read-side.
    retracted = invariants.grounding_verdict(
        support_edge_count=0, epistemic_status=EpistemicStatus.RETRACTED
    )
    assert retracted
    assert retracted.route == "RETRACTED_EXEMPT"
    invariants.assert_grounded(support_edge_count=0, epistemic_status=EpistemicStatus.RETRACTED)

    # A negative edge count is a caller bug, not a grounding question.
    with pytest.raises(ValueError):
        invariants.grounding_verdict(support_edge_count=-1)


# ---------------------------------------------------------------------------
# Transition legality, money scale, and the thresholds
# ---------------------------------------------------------------------------


def test_transition_legality_delegates_to_the_transitions_module() -> None:
    """T1.2 owns the tables. This module must not hold a second copy of them.

    The delegation is asserted the same way the money identity is: by
    replacing the attribute on the module object and watching the call follow
    it. A copied reference here would mean the grid could drift between two
    files with nothing to notice.
    """
    verdict = invariants.assert_transition_legal(
        "case", "RESOLVED", "REOPENED", reason_code="CONTRADICTORY_EVIDENCE"
    )
    assert verdict.legal is True
    assert verdict.code == "G1"

    for wrong in ("CONTRADICTORY_EVIDENCE_ADMITTED", "RC_CONTRADICTORY_EVIDENCE", None):
        with pytest.raises(invariants.InvariantViolation) as exc:
            invariants.assert_transition_legal("case", "RESOLVED", "REOPENED", reason_code=wrong)
        assert exc.value.code == "TRANSITION_ILLEGAL"

    # No table is re-declared here; the module reaches T1.2's.
    source = inspect.getsource(invariants)
    assert "CASE_TRANSITIONS" not in source
    assert "transitions.legal_transition(" in source

    calls: list[tuple[str, str, str]] = []
    original = transitions.legal_transition

    def _spy(machine: Any, frm: Any, to: Any, **kwargs: Any) -> Any:
        calls.append((str(machine), str(frm), str(to)))
        return original(machine, frm, to, **kwargs)

    try:
        transitions.legal_transition = _spy  # type: ignore[assignment]
        invariants.assert_transition_legal("commitment", "ACTIVE", "PARTIAL")
    finally:
        transitions.legal_transition = original  # type: ignore[assignment]
    assert calls == [("commitment", "ACTIVE", "PARTIAL")]


def test_money_scale_is_asserted_not_rounded() -> None:
    """11_CONTRACTS.md §5.1 — round at the source, never here."""
    assert Decimal("0.0001") == invariants.MONEY_EXPONENT
    assert invariants.quantise_money(Decimal("186.00")) == Decimal("186.0000")
    assert str(invariants.quantise_money(Decimal("186.00"))) == "186.0000"
    assert str(invariants.quantise_money(Decimal("186.0000"))) == "186.0000"

    invariants.assert_money_scale(Decimal("186.0000"))
    invariants.assert_money_scale(Decimal("-80.0000"))

    with pytest.raises(invariants.InvariantViolation) as scale:
        invariants.assert_money_scale(Decimal("1.00001"))
    assert scale.value.code == "MONEY_SCALE"
    assert "round at the source" in scale.value.detail

    for not_a_number in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(invariants.InvariantViolation) as finite:
            invariants.assert_money_scale(not_a_number)
        assert finite.value.code == "MONEY_NOT_FINITE"

    with pytest.raises(invariants.InvariantViolation):
        invariants.quantise_money(Decimal("1.00001"))


def test_thresholds_are_declared_once_here() -> None:
    """11_CONTRACTS.md §10 imports the human-review floor from this module.

    `provenance_contracts/resolution.py` currently carries a local copy with a
    recorded deviation note; T1.6 deletes it. The values must match exactly
    while both exist, or two modules disagree about when a human is required.
    """
    assert Decimal("0.70") == invariants.HUMAN_REVIEW_CONFIDENCE_FLOOR
    assert Decimal("0.90") == invariants.IDENTITY_STRONG_THRESHOLD
    assert Decimal("0.15") == invariants.IDENTITY_MARGIN_THRESHOLD

    # Thresholds are configuration constants, never prompt text
    # (03_AGENTS_LANGGRAPH_CONTRACTS.md §5.7).
    for name in (
        "HUMAN_REVIEW_CONFIDENCE_FLOOR",
        "IDENTITY_STRONG_THRESHOLD",
        "IDENTITY_MARGIN_THRESHOLD",
    ):
        assert name in invariants.__all__
        assert isinstance(getattr(invariants, name), Decimal)


# ---------------------------------------------------------------------------
# The map itself — 23_PHASE_GATES.md §23.15
# ---------------------------------------------------------------------------


def test_invariants_md_maps_five_invariants_to_real_functions_and_tests() -> None:
    """Five rows: the four canon invariants plus grounding.

    Every named function must import, and every named test must exist in this
    file. "We have N tests" is not reportable evidence; this map is.
    """
    rows = invariant_map_check.parse_table(INVARIANTS_MD.read_text(encoding="utf-8"))
    assert len(rows) == 5

    assert [row.name for row in rows] == [
        "Evidence is append-only",
        "Beliefs are revisable",
        "State is transactional",
        "Actions are permissioned",
        "Grounding",
    ]

    node_ids: set[str] = set()
    for row in rows:
        assert row.functions, f"{row.name} names no enforcing function"
        assert row.tests, f"{row.name} names no proving test"
        for dotted in row.functions:
            resolved = invariant_map_check.resolve_function(dotted)
            assert callable(resolved), f"{dotted} is not callable"
        for test_name, location in zip(row.tests, row.test_locations, strict=True):
            assert location.path == THIS_FILE
            assert test_name in globals(), f"{test_name} is not defined in {THIS_FILE}"
            node_ids.add(f"{location.path}::{test_name}")

    verdicts = [
        invariant_map_check.verdict_for(row, collected=node_ids, runnable=node_ids) for row in rows
    ]
    assert all(v.mapped for v in verdicts)
    assert invariant_map_check.summary_line(verdicts) == "5 invariants, 5 mapped, 0 UNPROVEN"

    # The money identity is named by the state-transactional row, so the G1.7
    # sabotage lands on a mapped test rather than beside one.
    state_row = next(row for row in rows if row.name == "State is transactional")
    assert "provenance_domain.invariants.derive_outstanding" in state_row.functions
    assert "test_invariant_3_state_is_transactional" in state_row.tests


def test_invariant_map_check_reports_unproven_for_missing_or_skipped_tests() -> None:
    """Skipped counts as unproven. That is the entire point of the tool.

    A silent pass on a missing test is the vacuity failure `§23.15` exists to
    catch: "we have 240 tests" in the report and "invariant 3 is proven by
    test X" nowhere.

    The synthetic table below names a test file that really exists, with test
    names that do not. `verdict_for` treats a row pointing at a nonexistent
    PATH as a problem in its own right -- correctly, since a map naming a file
    that is not there proves nothing -- and that is a different failure from
    the four this test is about. Using a real path isolates the collected /
    skipped / missing / unresolvable-function distinctions being asserted.
    """
    table = (
        "| Invariant | Enforcing function | Function `file:line` | Proving test "
        "| Test `file:line` |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Present | `provenance_domain.invariants.derive_outstanding` "
        "| `packages/python/provenance_domain/src/provenance_domain/invariants.py:1` "
        "| `test_present` | `packages/python/provenance_domain/tests/test_invariants.py:1` |\n"
        "| Skipped | `provenance_domain.invariants.derive_outstanding` "
        "| `packages/python/provenance_domain/src/provenance_domain/invariants.py:1` "
        "| `test_skipped` | `packages/python/provenance_domain/tests/test_invariants.py:2` |\n"
        "| Missing | `provenance_domain.invariants.derive_outstanding` "
        "| `packages/python/provenance_domain/src/provenance_domain/invariants.py:1` "
        "| `test_missing` | `packages/python/provenance_domain/tests/test_invariants.py:3` |\n"
        "| NoSuchFn | `provenance_domain.invariants.no_such_function` "
        "| `packages/python/provenance_domain/src/provenance_domain/invariants.py:1` "
        "| `test_present` | `packages/python/provenance_domain/tests/test_invariants.py:1` |\n"
    )
    rows = invariant_map_check.parse_table(table)
    assert len(rows) == 4

    collected = {
        "packages/python/provenance_domain/tests/test_invariants.py::test_present",
        "packages/python/provenance_domain/tests/test_invariants.py::test_skipped",
    }
    runnable = {"packages/python/provenance_domain/tests/test_invariants.py::test_present"}

    verdicts = [
        invariant_map_check.verdict_for(row, collected=collected, runnable=runnable) for row in rows
    ]
    present, skipped, missing, no_function = verdicts

    assert present.mapped is True
    assert present.problems == ()

    assert skipped.mapped is False
    assert any("skipped" in problem for problem in skipped.problems)

    assert missing.mapped is False
    assert any("not collected" in problem for problem in missing.problems)

    assert no_function.mapped is False
    assert any("no_such_function" in problem for problem in no_function.problems)

    assert invariant_map_check.summary_line(verdicts) == "4 invariants, 1 mapped, 3 UNPROVEN"

    # The gate reads the last line, so the count and the wording are contract.
    assert invariant_map_check.summary_line([]) == "0 invariants, 0 mapped, 0 UNPROVEN"

    # The spec spells the argument `provenance_domain/INVARIANTS.md`; the file
    # lives under `packages/python/`. The tool resolves the shorthand rather
    # than making the documented gate command fail.
    assert invariant_map_check.resolve_map_path("provenance_domain/INVARIANTS.md") == INVARIANTS_MD
    assert invariant_map_check.resolve_map_path(str(INVARIANTS_MD)) == INVARIANTS_MD
