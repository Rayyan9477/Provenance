"""Layer 3 of the lifecycle filter, proved on its own (``T6.5``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 13.3 -- three enforcement layers,
  "defence in depth, because a single missed predicate is a silent correctness
  failure".
- ``docs/quality/23_PHASE_GATES.md`` ``G6.7``.

Why a layer that is normally a no-op gets its own file
--------------------------------------------------------
Layer 1 -- the ``retraction_status = 'ACTIVE'`` predicate in the ANN statement
-- does the work on every real query, so layer 3 removes nothing and a test
that only ran the happy path could not tell whether layer 3 existed at all.
That is precisely the shape of a defence that quietly stops working.

These tests hand :func:`predicates.active_rows` rows that never went near the
database, so the function is the only thing standing between a retracted row
and the caller. And :func:`predicates.retraction_filter` -- the ``G6.7`` symbol
-- is exercised on the statement it assembles, which is what makes neutering it
observable rather than theoretical.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.retrieval import ann, predicates

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]


def row(status: str) -> dict[str, object]:
    return {"evidence_id": f"ev-{status.lower()}", "retraction_status": status}


def test_the_filter_admits_only_active_rows() -> None:
    """All four lifecycle states in, one out.

    A filter written as ``<> 'RETRACTED'`` passes any test that only checks the
    RETRACTED case and lets SUPERSEDED and QUARANTINED rows straight through,
    which is why all four go in together.
    """
    kept = predicates.active_rows(
        [row("ACTIVE"), row("RETRACTED"), row("SUPERSEDED"), row("QUARANTINED")]
    )
    assert [item["evidence_id"] for item in kept] == ["ev-active"]


def test_a_row_with_no_lifecycle_column_is_dropped_not_admitted() -> None:
    """Absence of the column means the query did not select it.

    Admitting a row whose lifecycle is unknown is the mistake this exists to
    prevent, and "unknown" is the state a hurried new query produces.
    """
    assert predicates.active_rows([{"evidence_id": "ev-1"}]) == []


def test_the_statement_the_repository_executes_carries_the_predicate() -> None:
    """``G6.7``'s symbol, observed on its output rather than on its source.

    ``PV_SABOTAGE=retrieval.predicates.retraction_filter`` replaces the function
    with the identity, so the assembled statement loses its lifecycle predicate
    and this fails. That is the entry the sabotage matrix claims, asserted at
    the point the claim is about.
    """
    assert predicates.RETRACTION_PREDICATE in ann.render_ann_sql()
    assert predicates.RETRACTION_PREDICATE in ann.CANONICAL_ANN_SQL


def test_the_positive_control_form_genuinely_lacks_the_predicate() -> None:
    """``G6.3(d)`` needs an unfiltered statement to exist, and to really be one.

    If the "unfiltered" form silently kept the predicate, part (d) would fail
    to surface the superseded fixture and a reviewer would conclude the corpus
    was wrong rather than the control.
    """
    assert predicates.RETRACTION_PREDICATE not in ann.render_ann_sql(retraction_filter=False)


def test_the_filter_is_idempotent() -> None:
    """Applying it twice must not produce two predicates.

    A duplicated ``AND retraction_status = 'ACTIVE'`` is harmless to the
    planner and fatal to the byte-comparison against the spec, so the failure
    would show up as an unrelated test going red.
    """
    base = ann._ANN_SQL_BEFORE_LIFECYCLE_FILTER
    once = predicates.retraction_filter(base)
    twice = predicates.retraction_filter(once)
    assert once == twice
    assert once.count(predicates.RETRACTION_PREDICATE) == 1


def test_the_filter_refuses_a_statement_it_cannot_place_the_predicate_in() -> None:
    """No anchor means no safe insertion point, and a guess would be worse.

    Silently returning the statement unchanged is what the identity function
    does, and it is the defect -- so the real function has to be loud instead.
    """
    with pytest.raises(predicates.RetractionFilterNotAppliedError):
        predicates.retraction_filter("SELECT id FROM evidence_items WHERE user_id = $1")
