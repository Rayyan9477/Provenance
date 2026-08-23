"""Stage F's six statements, executed against the live schema (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 11.1.
- ``db/migrations/versions/`` -- the schema that actually exists.

What this lane proves that the unit lane cannot
------------------------------------------------
Section 11.1 prints six statements naming roughly seventy columns. Three of
those columns do not exist: F.2 selects ``ev.relationship_id`` and
``ev.case_id`` from ``evidence_items``, which carries neither -- the identity
link runs through ``claims``. A unit test that diffs the shipped SQL against
the spec text would pass on a statement that cannot run.

Executing each statement is the only assertion that distinguishes "transcribed
faithfully" from "runs". It is cheap -- each returns zero rows against empty
arrays -- and it fails with the offending column name in the message.

Why zero rows is the expected result today
-------------------------------------------
``beliefs``, ``belief_versions`` and ``belief_support`` are empty in the seeded
corpus: the belief graph is written by the Memory Kernel, and the seed stops at
evidence. So this lane proves the statements are *executable and scoped*, not
that the hero backstop fires. Test 18.13 -- the 15 May confirmation absent from
Stage D's top 60 and present in the final context at ``T2_GROUNDING_EXPANSION``
-- needs a seeded belief graph and is recorded as open rather than faked with a
fixture that would prove only that the fixture was written.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from services.control_plane.app.retrieval import grounding

pytestmark = [pytest.mark.db, pytest.mark.retrieval]


def _params(name: str, tenant_id: uuid.UUID, user_id: uuid.UUID) -> tuple[object, ...]:
    """Empty-array parameters for one statement, in its own binder's order."""
    if name == "F2_GROUNDED_EVIDENCE":
        return grounding.bind_grounded_evidence(
            tenant_id=tenant_id, user_id=user_id, belief_version_ids=[], already_present=[]
        )
    if name == "F3_LINEAGE":
        return grounding.bind_lineage(tenant_id=tenant_id, user_id=user_id, belief_ids=[])
    return grounding.bind_case_scoped(tenant_id=tenant_id, user_id=user_id, case_ids=[])


def test_every_stage_f_statement_executes_against_the_live_schema(
    rconn: psycopg.Connection, hero_user: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Each of the six runs and returns zero rows for an empty id array.

    A column the schema does not have raises ``UndefinedColumn`` here with the
    name in the message, which is the fastest available route from "the spec
    says ``ev.case_id``" to "the schema puts it on ``claims``".
    """
    tenant_id, user_id = hero_user
    assert len(grounding.STATEMENTS) == 6, (
        "the vacuity guard, first: a loop over an empty statement table executes "
        "nothing and passes forever, which is the shape of a lint that stopped "
        "working rather than of a schema that agrees with the spec"
    )
    for name in grounding.STATEMENTS:
        with rconn.cursor() as cur:
            cur.execute(grounding.render(name), _params(name, tenant_id, user_id))
            rows = cur.fetchall()
        assert rows == [], f"{name} returned rows against empty id arrays: {rows[:2]}"


def test_the_hero_has_no_evidence_grounding_yet_so_18_13_is_not_writable(
    rconn: psycopg.Connection, hero_user: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The honest precondition for the assertion above, stated as a test.

    Test 18.13 -- the 15 May confirmation absent from Stage D's top 60 and
    present in the final context at ``T2_GROUNDING_EXPANSION`` with a vector
    contribution of 0.0 -- needs one thing: a ``belief_support`` row of
    ``source_kind = 'EVIDENCE'`` in the hero user's scope. F.2 selects on
    exactly that predicate, so nothing else makes the backstop exercisable.

    Scoped to the hero user and to the ``EVIDENCE`` kind rather than counting
    the whole table: ``provenance_ci`` is shared, and a concurrent kernel suite
    writing a ``CLAIM``-kind edge for its own fixture user says nothing about
    whether this test can be written.

    **If this fails, that is a prompt and not a regression.** Recording the
    precondition as an assertion is what stops the zero-row result above from
    being read as evidence that the backstop works.
    """
    _tenant_id, user_id = hero_user
    with rconn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM belief_support WHERE user_id = %s AND source_kind = 'EVIDENCE'",
            (user_id,),
        )
        support = int(str(cur.fetchone()[0]))  # type: ignore[index]
    assert support == 0, (
        f"the hero user now has {support} EVIDENCE grounding edge(s). Stage F's "
        "backstop is testable end to end: write test 18.13 -- the 15 May "
        "termination confirmation absent from Stage D's top 60 and present in "
        "the final context at T2_GROUNDING_EXPANSION with a vector contribution "
        "of 0.0."
    )


def test_a_non_empty_id_array_binds_and_matches_nothing(
    rconn: psycopg.Connection, hero_user: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The array binding itself, exercised with values in it.

    The test above passes empty arrays, and an empty array is the one input for
    which ``ANY(...::UUID[])`` cannot distinguish a working cast from a broken
    one -- nothing matches either way. Binding two UUIDs that exist nowhere
    proves the cast resolves and the statement still returns cleanly, which is
    the half of the contract the empty case cannot reach.
    """
    tenant_id, user_id = hero_user
    absent = [uuid.UUID(int=1), uuid.UUID(int=2)]

    with rconn.cursor() as cur:
        cur.execute(
            grounding.render("F2_GROUNDED_EVIDENCE"),
            grounding.bind_grounded_evidence(
                tenant_id=tenant_id,
                user_id=user_id,
                belief_version_ids=absent,
                already_present=absent,
            ),
        )
        assert cur.fetchall() == []

        cur.execute(
            grounding.render("F3_LINEAGE"),
            grounding.bind_lineage(tenant_id=tenant_id, user_id=user_id, belief_ids=absent),
        )
        assert cur.fetchall() == []

        cur.execute(
            grounding.render("F1_CANONICAL_BELIEFS"),
            grounding.bind_case_scoped(tenant_id=tenant_id, user_id=user_id, case_ids=absent),
        )
        assert cur.fetchall() == []
