"""DDL section 19 required database tests. ``T2.3`` opens with test 2 (``D2``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 6.3 (``belief_versions``) and
  section 19, test 2.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, ``T2.3`` acceptance.
- ``ops/gates/PHASE_02.md`` - ``G2.8``: "the schema refuses an ungrounded
  canonical belief version." This module is that assertion's reference.

The point of this file
----------------------
Grounding is the product's central claim: nothing becomes canonical without
evidence behind it. A Python guard that enforces it is a guard some future
caller bypasses. So ``ck_belief_versions_grounded`` is a **database** CHECK, and
the test asserts the **database error class** - not a raised ``ValueError``, not
a mocked repository. If the schema stopped refusing, this test would fail even
with every line of Kernel code intact.

``T2.4`` added test 5 (``D5``, ``G2.7``) and the schema halves of tests 3 and
4 (``D3``, ``D4``) below. Test 1 and the remaining section 19 tests need a
Kernel or a seeded corpus and defer to phases 4, 6, 9 and 10;
``ops/gates/PHASE_02.md`` lists those deferrals.

What "schema half" means here
-----------------------------
``D3`` and ``D4`` are behavioural tests of a Kernel that does not exist yet.
What *can* be asserted today is the half no Kernel can weaken: that the schema
refuses the impossible state each test is about. Writing them as skips would
leave nothing to fail if a later migration dropped the constraint, so they are
constraint tests now and grow a Kernel half in Phase 4.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import psycopg
import pytest
from psycopg.types.json import Json

pytestmark = pytest.mark.db

GROUNDING_CONSTRAINT = "ck_belief_versions_grounded"

#: SQLSTATE 23514, ``check_violation``. Asserted alongside the constraint name so
#: the test states which *class* of database refusal is required, not merely that
#: psycopg raised something.
CHECK_VIOLATION_SQLSTATE = "23514"


def _seed_identity(
    cursor: psycopg.Cursor,
    *,
    recorded_at: datetime,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert the minimum identity spine a belief version needs.

    Returns ``(tenant_id, user_id, belief_id)``. Every id is generated here
    rather than defaulted by the database: DDL conventions forbid
    ``gen_random_uuid()`` so the Kernel can build a whole write plan before the
    transaction opens.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    belief_id = uuid.uuid4()
    slug = f"t-{tenant_id.hex[:16]}"

    cursor.execute(
        "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
        (tenant_id, "G2.8 fixture tenant", slug),
    )
    cursor.execute(
        "INSERT INTO users (id, tenant_id, cognito_sub) VALUES (%s, %s, %s)",
        (user_id, tenant_id, f"sub-{user_id.hex}"),
    )
    cursor.execute(
        "INSERT INTO beliefs (id, tenant_id, user_id, subject_type, subject_id, predicate) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (belief_id, tenant_id, user_id, "RELATIONSHIP", uuid.uuid4(), "outstanding_balance"),
    )
    assert recorded_at.tzinfo is not None, "the fixture clock must be timezone-aware"
    return tenant_id, user_id, belief_id


def _insert_belief_version(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    belief_id: uuid.UUID,
    derivation_kind: str,
    support_edge_count: int,
    recorded_at: datetime,
    epistemic_status: str = "CONFIRMED",
) -> uuid.UUID:
    """Attempt one ``belief_versions`` insert. No Python validation in the path.

    ``kernel_decision_id`` carried no foreign key when this helper was written:
    DDL section 8.3 defers it, and at ``T2.3`` -- when only migrations 0001-0003
    existed -- a free-standing UUID was correct. Migration ``0005`` closes the
    deferral with ``fk_belief_versions_kernel_decision FOREIGN KEY (tenant_id,
    user_id, kernel_decision_id)``, so a random UUID is now a
    ``ForeignKeyViolation`` and the three positive tests below stopped being able
    to insert anything at all.

    A real ledger row is therefore created first, which is also what the Kernel
    does -- DDL section 13 statement 2 puts ``kernel_decisions`` before the rows
    whose NOT NULL foreign keys name it, for exactly this reason. The negative
    test is unaffected either way: it asserts on
    ``diag.constraint_name == ck_belief_versions_grounded``, so an insert refused
    by the wrong constraint fails rather than passing quietly.
    """
    decision_id = _seed_decision(
        cursor, tenant_id=tenant_id, user_id=user_id, recorded_at=recorded_at
    )
    version_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO belief_versions ("
        "  id, tenant_id, user_id, belief_id, version_no, value_type, value_json,"
        "  epistemic_status, belief_confidence, derivation_kind, support_edge_count,"
        "  recorded_at, kernel_decision_id"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            version_id,
            tenant_id,
            user_id,
            belief_id,
            1,
            "MONEY",
            Json({"amount": "186.00", "currency": "USD"}),
            epistemic_status,
            "0.9000",
            derivation_kind,
            support_edge_count,
            recorded_at,
            decision_id,
        ),
    )
    return version_id


def _seed_decision(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    recorded_at: datetime,
) -> uuid.UUID:
    """One ``kernel_decisions`` row, plus the proposal its foreign key names.

    ``case_id`` stays NULL: it is nullable, and a decision that touched no case
    should not claim one. That also keeps this helper from having to fabricate a
    counterparty, a relationship and a case for a test about ``belief_versions``.
    """
    proposal_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO memory_proposals (id, tenant_id, user_id, trace_id, schema_version,"
        " proposal_type, source_artifact_ids, evidence_ids, payload, payload_sha256,"
        " model_id, prompt_version, status, decided_at) VALUES (%s, %s, %s, %s, '1.0',"
        " 'SEED_FIXTURE', %s, %s, %s, %s, 'deterministic.kernel', 'pv-seed-1.0.0',"
        " 'ACCEPTED', %s)",
        (
            proposal_id,
            tenant_id,
            user_id,
            uuid.uuid4(),
            Json([]),
            Json([]),
            Json({"fixture": "test_kernel_required"}),
            hashlib.sha256(proposal_id.bytes).digest(),
            recorded_at,
        ),
    )
    cursor.execute(
        "INSERT INTO kernel_decisions (id, tenant_id, user_id, proposal_id, decision,"
        " reason_codes, retry_count, transaction_opened, trace_id, committed_at)"
        " VALUES (%s, %s, %s, %s, 'ACCEPTED', %s, 0, true, %s, %s)",
        (
            decision_id,
            tenant_id,
            user_id,
            proposal_id,
            Json(["BELIEF_CREATED"]),
            uuid.uuid4(),
            recorded_at,
        ),
    )
    return decision_id


def test_belief_cannot_be_canonical_without_grounding(db_connection, frozen_clock) -> None:
    """DDL section 19 test 2 / ``G2.8``: the schema refuses an ungrounded version.

    ``derivation_kind = 'EVIDENCE_GROUNDED'`` with ``support_edge_count = 0``
    claims canonical standing with nothing behind it. CockroachDB must reject the
    statement itself, so the guarantee survives any caller - including one that
    never goes through the Memory Kernel.

    Asserted as a database error class, per ``T2.3`` acceptance. Test 2's other
    half - a truthful count with the edges missing - satisfies this CHECK and is
    caught only by verification query V3, which ``T2.7`` builds.
    """
    with db_connection.cursor() as cur:
        tenant_id, user_id, belief_id = _seed_identity(cur, recorded_at=frozen_clock.now_utc())

        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_belief_version(
                cur,
                tenant_id=tenant_id,
                user_id=user_id,
                belief_id=belief_id,
                derivation_kind="EVIDENCE_GROUNDED",
                support_edge_count=0,
                recorded_at=frozen_clock.now_utc(),
            )

    error = excinfo.value
    assert (
        error.sqlstate == CHECK_VIOLATION_SQLSTATE
    ), f"expected SQLSTATE {CHECK_VIOLATION_SQLSTATE} (check_violation), got {error.sqlstate}"
    # CockroachDB's *message* renders the expression, not the constraint name -
    # "failed to satisfy CHECK constraint ((derivation_kind = ...) OR ...)". The
    # name is carried in the error diagnostics, which is the field to assert on:
    # a substring match against the message would also pass if some *other*
    # CHECK on this row happened to fire.
    assert error.diag.constraint_name == GROUNDING_CONSTRAINT, (
        f"the insert was refused, but by {error.diag.constraint_name!r}, "
        f"not by {GROUNDING_CONSTRAINT!r}: {error}"
    )
    db_connection.rollback()


def test_a_grounded_belief_version_is_accepted(db_connection, frozen_clock) -> None:
    """Positive control. Without it, test 2 passes on a table nothing can write to.

    One support edge is the minimum grounding an evidence-grounded version may
    claim, and it must be accepted or the CHECK is not a grounding rule, it is a
    write block.
    """
    with db_connection.cursor() as cur:
        tenant_id, user_id, belief_id = _seed_identity(cur, recorded_at=frozen_clock.now_utc())
        version_id = _insert_belief_version(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            belief_id=belief_id,
            derivation_kind="EVIDENCE_GROUNDED",
            support_edge_count=1,
            recorded_at=frozen_clock.now_utc(),
        )
        cur.execute("SELECT count(*) FROM belief_versions WHERE id = %s", (version_id,))
        assert cur.fetchone() == (1,)
    db_connection.rollback()


def test_a_deterministic_derivation_may_carry_no_support_edges(db_connection, frozen_clock) -> None:
    """The declared exemption, and only that one.

    ``DETERMINISTIC_DERIVATION`` is how a version says "I follow from other
    canonical state by a reviewed rule, not from evidence of my own". The
    exemption must be *declared* in the row, which is why it is a column value
    and not an absence.
    """
    with db_connection.cursor() as cur:
        tenant_id, user_id, belief_id = _seed_identity(cur, recorded_at=frozen_clock.now_utc())
        version_id = _insert_belief_version(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            belief_id=belief_id,
            derivation_kind="DETERMINISTIC_DERIVATION",
            support_edge_count=0,
            recorded_at=frozen_clock.now_utc(),
        )
        cur.execute("SELECT count(*) FROM belief_versions WHERE id = %s", (version_id,))
        assert cur.fetchone() == (1,)
    db_connection.rollback()


def test_the_hero_disposition_is_representable(db_connection, frozen_clock) -> None:
    """``RETAIN_INCUMBENT_DISPUTED``: value unchanged, ``CONFIRMED -> DISPUTED``.

    The hero commit does not overwrite the ISP balance; it marks the existing
    belief disputed. A schema whose ``epistemic_status`` cannot hold ``DISPUTED``
    makes that commit unrepresentable, so this is a schema test, not a Kernel one.
    """
    with db_connection.cursor() as cur:
        tenant_id, user_id, belief_id = _seed_identity(cur, recorded_at=frozen_clock.now_utc())
        version_id = _insert_belief_version(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            belief_id=belief_id,
            derivation_kind="EVIDENCE_GROUNDED",
            support_edge_count=2,
            recorded_at=frozen_clock.now_utc(),
            epistemic_status="DISPUTED",
        )
        cur.execute("SELECT epistemic_status FROM belief_versions WHERE id = %s", (version_id,))
        assert cur.fetchone() == ("DISPUTED",)
    db_connection.rollback()


# ==========================================================================
# DDL section 19 test 5 (``D5``) - money. ``T2.4`` / ``G2.7``.
# ==========================================================================

OUTSTANDING_BLOCKS_FULFILLED = "ck_commitments_outstanding_blocks_fulfilled"
FULFILLED_NEEDS_PAYMENT = "ck_commitments_fulfilled_needs_payment"
OUTSTANDING_IDENTITY = "ck_commitments_outstanding_identity"
FULFILLED_LE_COMMITTED = "ck_commitments_fulfilled_le_committed"
AMOUNTS_NONNEG = "ck_commitments_amounts_nonneg"
LIVE_CONFLICT_IDENTITY = "uq_conflicts_live_identity"
ONE_EVIDENCE_PER_COMMITMENT = "uq_fulfillments_commitment_evidence"

#: SQLSTATE 23505, ``unique_violation``.
UNIQUE_VIOLATION_SQLSTATE = "23505"


def _seed_case(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """A counterparty, a relationship and a case - the spine a commitment needs."""
    counterparty_id = uuid.uuid4()
    relationship_id = uuid.uuid4()
    case_id = uuid.uuid4()

    cursor.execute(
        "INSERT INTO counterparties (id, tenant_id, normalized_name, display_name, kind) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            counterparty_id,
            tenant_id,
            f"harborview property management {counterparty_id.hex[:8]}",
            "Harborview Property Management",
            "LANDLORD",
        ),
    )
    cursor.execute(
        "INSERT INTO relationships (id, tenant_id, user_id, counterparty_id, relationship_type,"
        " status) VALUES (%s, %s, %s, %s, %s, %s)",
        (relationship_id, tenant_id, user_id, counterparty_id, "TENANCY", "ACTIVE"),
    )
    cursor.execute(
        "INSERT INTO cases (id, tenant_id, user_id, relationship_id, case_type, title, status,"
        " attention_level, opened_at, last_activity_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            case_id,
            tenant_id,
            user_id,
            relationship_id,
            "DEPOSIT_RETURN",
            "D5 fixture case",
            "ACTIONABLE",
            "ATTENTION",
            now,
            now,
        ),
    )
    return case_id


def _seed_claim(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    now: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    """An artifact, an evidence item and a claim. Returns ``(claim_id, evidence_id)``.

    ``commitments.source_claim_id`` is ``NOT NULL`` with a composite foreign key,
    so a money test cannot skip the provenance chain even to assert a CHECK -
    which is itself the point: there is no path to a commitment that is not
    grounded in an evidence item belonging to the same user.
    """
    artifact_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    digest = artifact_id.bytes + evidence_id.bytes

    cursor.execute(
        # ck_source_artifacts_parsed_has_version (0002): a PARSED artifact must
        # name the parser that produced it, so the fixture supplies one.
        "INSERT INTO source_artifacts (id, tenant_id, user_id, source_type, s3_bucket, s3_key,"
        " content_sha256, size_bytes, mime_type, received_at, parser_status, parser_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            artifact_id,
            tenant_id,
            user_id,
            "SEED_FIXTURE",
            "pv-fixtures",
            # ck_source_artifacts_s3_key_shape (0002): every object lives under raw/.
            f"raw/d5/{artifact_id.hex}.eml",
            digest,
            1024,
            "message/rfc822",
            now,
            "PARSED",
            "fixture-1.0.0",
        ),
    )
    cursor.execute(
        "INSERT INTO evidence_items (id, tenant_id, user_id, artifact_id, evidence_type,"
        " normalized_text, normalized_text_sha256, observed_at, extraction_confidence,"
        " retraction_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            evidence_id,
            tenant_id,
            user_id,
            artifact_id,
            "AMOUNT_ASSERTION",
            "deposit of USD 1200.00 returnable within 30 days of inspection",
            evidence_id.bytes + claim_id.bytes,
            now,
            "0.9500",
            "ACTIVE",
        ),
    )
    cursor.execute(
        "INSERT INTO claims (id, tenant_id, user_id, case_id, subject_type, subject_id,"
        " predicate, object_type, object_json, actor_type, evidence_id, claim_kind,"
        " extraction_confidence, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            claim_id,
            tenant_id,
            user_id,
            case_id,
            "CASE",
            case_id,
            "deposit_owed",
            "MONEY",
            Json({"amount": "1200.0000", "currency": "USD"}),
            "COUNTERPARTY",
            evidence_id,
            "COMMITMENT_CLAIM",
            "0.9500",
            now,
        ),
    )
    return claim_id, evidence_id


def _insert_commitment(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    claim_id: uuid.UUID,
    status: str,
    committed: str | None,
    fulfilled: str | None,
    outstanding: str | None,
    currency: str | None = "USD",
) -> uuid.UUID:
    """One ``commitments`` insert, with no Python validation anywhere in the path."""
    commitment_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO commitments (id, tenant_id, user_id, case_id, obligor_type,"
        " beneficiary_type, commitment_type, description, currency, committed_amount,"
        " fulfilled_amount, outstanding_amount, source_claim_id, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            commitment_id,
            tenant_id,
            user_id,
            case_id,
            "COUNTERPARTY",
            "USER",
            "DEPOSIT_RETURN",
            "Return the security deposit within 30 days of inspection",
            currency,
            committed,
            fulfilled,
            outstanding,
            claim_id,
            status,
        ),
    )
    return commitment_id


def test_nothing_fulfilled_with_outstanding(db_connection, frozen_clock) -> None:
    """DDL section 19 test 5 / ``G2.7``: the schema refuses impossible money.

    ``UPDATE commitments SET status='FULFILLED'`` while ``outstanding_amount``
    is 220 must raise a CHECK violation. Asserted with the database error class
    and the constraint **name**, not with a Python guard and not with a substring
    of the message: CockroachDB renders the *expression* in the message text, so
    only ``diag.constraint_name`` says which rule refused - and a message match
    would also pass if some other CHECK on the row had fired.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        claim_id, _ = _seed_claim(
            cur, tenant_id=tenant_id, user_id=user_id, case_id=case_id, now=now
        )
        commitment_id = _insert_commitment(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case_id,
            claim_id=claim_id,
            status="PARTIAL",
            committed="420.0000",
            fulfilled="200.0000",
            outstanding="220.0000",
        )

        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            cur.execute(
                "UPDATE commitments SET status = 'FULFILLED' WHERE id = %s", (commitment_id,)
            )

    error = excinfo.value
    assert (
        error.sqlstate == CHECK_VIOLATION_SQLSTATE
    ), f"expected SQLSTATE {CHECK_VIOLATION_SQLSTATE} (check_violation), got {error.sqlstate}"
    assert error.diag.constraint_name in {
        OUTSTANDING_BLOCKS_FULFILLED,
        FULFILLED_NEEDS_PAYMENT,
    }, (
        f"the update was refused, but by {error.diag.constraint_name!r}; D5 requires "
        f"{OUTSTANDING_BLOCKS_FULFILLED!r} or its partner {FULFILLED_NEEDS_PAYMENT!r}, "
        "which forbids the same state from the other side"
    )
    db_connection.rollback()


def test_a_fully_paid_commitment_may_be_fulfilled(db_connection, frozen_clock) -> None:
    """Positive control for D5. Without it, the test above passes on a frozen table."""
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        claim_id, _ = _seed_claim(
            cur, tenant_id=tenant_id, user_id=user_id, case_id=case_id, now=now
        )
        commitment_id = _insert_commitment(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case_id,
            claim_id=claim_id,
            status="PARTIAL",
            committed="420.0000",
            fulfilled="420.0000",
            outstanding="0.0000",
        )
        cur.execute("UPDATE commitments SET status = 'FULFILLED' WHERE id = %s", (commitment_id,))
        cur.execute("SELECT status FROM commitments WHERE id = %s", (commitment_id,))
        assert cur.fetchone() == ("FULFILLED",)
    db_connection.rollback()


def test_the_outstanding_identity_cannot_be_violated(db_connection, frozen_clock) -> None:
    """M4: ``outstanding = committed - fulfilled``, or the row does not exist.

    "$420 promised, $200 paid, $220 outstanding" is arithmetic the product shows
    a user. This is the constraint that makes the fourth number impossible to
    disagree with the first three.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        claim_id, _ = _seed_claim(
            cur, tenant_id=tenant_id, user_id=user_id, case_id=case_id, now=now
        )

        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_commitment(
                cur,
                tenant_id=tenant_id,
                user_id=user_id,
                case_id=case_id,
                claim_id=claim_id,
                status="PARTIAL",
                committed="420.0000",
                fulfilled="200.0000",
                outstanding="300.0000",
            )

    assert excinfo.value.diag.constraint_name == OUTSTANDING_IDENTITY
    db_connection.rollback()


def test_over_payment_is_refused_rather_than_clamped(db_connection, frozen_clock) -> None:
    """M3: fulfilled never exceeds committed.

    DDL section 7.2: over-payment is an anomaly the Kernel must raise as a
    ``FULFILLMENT_CONFLICT``, never silently clamp. A schema that accepted it
    would let the clamp happen in Python, where nobody would see it. Either M3 or
    M1 may report first - both refuse the same row, and which one CockroachDB
    names is not a guarantee this test should pretend to make.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        claim_id, _ = _seed_claim(
            cur, tenant_id=tenant_id, user_id=user_id, case_id=case_id, now=now
        )

        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_commitment(
                cur,
                tenant_id=tenant_id,
                user_id=user_id,
                case_id=case_id,
                claim_id=claim_id,
                status="PARTIAL",
                committed="420.0000",
                fulfilled="500.0000",
                outstanding="-80.0000",
            )

    assert excinfo.value.diag.constraint_name in {FULFILLED_LE_COMMITTED, AMOUNTS_NONNEG}
    db_connection.rollback()


# ==========================================================================
# ``D4`` schema half - one evidence item admits against one commitment once.
# ==========================================================================


def test_one_evidence_item_cannot_be_admitted_twice(db_connection, frozen_clock) -> None:
    """``uq_fulfillments_commitment_evidence`` - DDL section 19 test 4.

    Replaying the same bank-transfer email must be a no-op, not a second credit.
    The Kernel recomputes ``fulfilled_amount`` from scratch on every commit so a
    retry cannot double-add; this constraint is why that recomputation has
    nothing extra to sum.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        claim_id, evidence_id = _seed_claim(
            cur, tenant_id=tenant_id, user_id=user_id, case_id=case_id, now=now
        )
        commitment_id = _insert_commitment(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case_id,
            claim_id=claim_id,
            status="ACTIVE",
            committed="1200.0000",
            fulfilled="0.0000",
            outstanding="1200.0000",
        )

        def admit() -> None:
            cur.execute(
                "INSERT INTO fulfillments (id, tenant_id, user_id, commitment_id, evidence_id,"
                " currency, amount, fulfilled_at, admission_status, confidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    uuid.uuid4(),
                    tenant_id,
                    user_id,
                    commitment_id,
                    evidence_id,
                    "USD",
                    "300.0000",
                    now,
                    "ADMITTED",
                    "0.9000",
                ),
            )

        admit()
        with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
            admit()

    error = excinfo.value
    assert error.sqlstate == UNIQUE_VIOLATION_SQLSTATE
    assert ONE_EVIDENCE_PER_COMMITMENT in f"{error.diag.constraint_name} {error}"
    db_connection.rollback()


# ==========================================================================
# ``D3`` schema half - one live conflict per (subject, predicate, two sources).
# ==========================================================================


def _insert_conflict(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    left_id: uuid.UUID,
    right_id: uuid.UUID,
    now: datetime,
    status: str = "NEEDS_HUMAN",
) -> uuid.UUID:
    conflict_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO conflicts (id, tenant_id, user_id, case_id, subject_type, subject_id,"
        " predicate, left_source_kind, left_source_id, right_source_kind, right_source_id,"
        " conflict_type, status, severity, requires_human, detected_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            conflict_id,
            tenant_id,
            user_id,
            case_id,
            "CASE",
            case_id,
            "outstanding_balance",
            "EVIDENCE",
            left_id,
            "EVIDENCE",
            right_id,
            "VALUE_CONFLICT",
            status,
            "HIGH",
            status == "NEEDS_HUMAN",
            now,
        ),
    )
    return conflict_id


def test_the_same_contradiction_cannot_be_raised_twice_while_live(
    db_connection, frozen_clock
) -> None:
    """``uq_conflicts_live_identity`` - DDL section 19 test 3's schema half.

    "Re-submitting the same proposal does not create a second conflict" is a
    Kernel behaviour resting on a partial unique index over the live statuses.
    Partial, not total, so a genuinely new contradiction between the same two
    sources can still be raised after the previous one was resolved.
    """
    now = frozen_clock.now_utc()
    left_id, right_id = sorted((uuid.uuid4(), uuid.uuid4()), key=lambda value: value.bytes)
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        _insert_conflict(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case_id,
            left_id=left_id,
            right_id=right_id,
            now=now,
        )
        with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
            _insert_conflict(
                cur,
                tenant_id=tenant_id,
                user_id=user_id,
                case_id=case_id,
                left_id=left_id,
                right_id=right_id,
                now=now,
            )

    error = excinfo.value
    assert LIVE_CONFLICT_IDENTITY in f"{error.diag.constraint_name} {error}"
    db_connection.rollback()


def test_conflict_sides_must_be_normalised_before_insert(db_connection, frozen_clock) -> None:
    """``ck_conflicts_side_order``: ``left_source_id <= right_source_id``.

    Without it the dedupe index above is defeated by argument order - the same
    contradiction inserted with the sides swapped is a different index key, and
    the "exactly one conflict" assertion in section 19 test 3 becomes untrue for
    a reason no reader would guess.
    """
    now = frozen_clock.now_utc()
    smaller, larger = sorted((uuid.uuid4(), uuid.uuid4()), key=lambda value: value.bytes)
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert_conflict(
                cur,
                tenant_id=tenant_id,
                user_id=user_id,
                case_id=case_id,
                left_id=larger,
                right_id=smaller,
                now=now,
            )

    assert excinfo.value.diag.constraint_name == "ck_conflicts_side_order"
    db_connection.rollback()


def test_a_needs_human_conflict_carries_no_resolution(db_connection, frozen_clock) -> None:
    """The hero conflict is ``NEEDS_HUMAN`` and is therefore unresolved.

    ``ck_conflicts_open_has_no_resolution`` is what stops a row claiming both. A
    conflict sitting in the human queue that already carries a ``resolved_at`` is
    the exact shape of the bug that would make the demo's review panel lie.
    """
    now = frozen_clock.now_utc()
    left_id, right_id = sorted((uuid.uuid4(), uuid.uuid4()), key=lambda value: value.bytes)
    with db_connection.cursor() as cur:
        tenant_id, user_id, _ = _seed_identity(cur, recorded_at=now)
        case_id = _seed_case(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        conflict_id = _insert_conflict(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case_id,
            left_id=left_id,
            right_id=right_id,
            now=now,
        )
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            cur.execute("UPDATE conflicts SET resolved_at = %s WHERE id = %s", (now, conflict_id))

    assert excinfo.value.diag.constraint_name == "ck_conflicts_open_has_no_resolution"
    db_connection.rollback()
