"""Obligations and prospective memory, written by the Kernel against a cluster.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 13, the statement order.
- ``db/migrations/versions/0004_obligation_ledger.py`` — the real ``commitments``
  and ``fulfillments`` columns and the money invariants M1-M8, which are CHECK
  constraints rather than Kernel discipline.
- ``db/migrations/versions/0006_prospective_memory.py`` — the real
  ``prospective_triggers`` columns.
- ``CANONICAL_DECISIONS.md`` -> *Hero dataset canon*: the deposit is
  ``USD 1,800.00`` due ``2026-06-15T00:00:00Z`` and the wake is that instant
  plus ``WAKE_MARGIN_SECONDS``.

Why this module builds its own database
---------------------------------------
The same reason ``test_seed_step9.py`` does, and it is copied from there
deliberately rather than imported: the Kernel commits over its own pool
connection, so a second connection has to be able to see the result, which means
nothing here can be rolled back around a test. Sharing ``provenance_ci`` would
leave canonical rows behind for ``make db-verify`` to trip over and sharing
``provenance`` would corrupt the 18,035-row corpus. This module creates a
throwaway database, migrates it to the **deployed** head, replays into it, and
drops it in a ``finally``.

Every DSN is a :class:`scripts.seed.db.MaskedDsn` and every subprocess
transcript goes through :func:`scripts.seed.db.scrub`.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from provenance_contracts.predicates import PredicateNode
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposalIdentity,
    ProposedClaim,
    ProposedTrigger,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    ClaimKind,
    Modality,
    ModelTier,
    PredicateOp,
    ProposalType,
    SourceClass,
    SubjectType,
    TriggerMutationKind,
    TriggerType,
    ValueType,
)
from scripts.seed import db as dbmod
from scripts.seed.cases import case_of
from scripts.seed.evidence import CURATED_ARTIFACTS, CURATED_EVIDENCE, evidence_of
from scripts.seed.ids import DEMO_ANCHOR_UTC, DEPOSIT_DUE_AT, TRIGGER_WAKE_AT
from scripts.seed.loader import load_small_planes, run_replay
from scripts.seed.obligations import outstanding_total
from scripts.seed.proposals import CURATED_PROPOSALS, SeedProposal
from scripts.seed.tenants import HERO_USER

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

#: Never touched. ``provenance`` holds the demo corpus; ``provenance_ci`` is the
#: shared migration lane.
FORBIDDEN_DATABASES = frozenset({"provenance", "provenance_ci"})

#: ``0009`` drops the vector column; the lane is pinned to what is deployed.
DEPLOYED_HEAD = "0008_events_infrastructure"

_ADMIN_DSN_ENV = "PV_PROBE_DB_URL"


# ---------------------------------------------------------------------------
# The throwaway database
# ---------------------------------------------------------------------------


def _throwaway_name() -> str:
    name = f"pv_oblig_{uuid.uuid4().hex[:12]}"
    assert name not in FORBIDDEN_DATABASES
    return name


def _migrator_dsn(database: str) -> dbmod.MaskedDsn:
    try:
        return dbmod.role_dsn("pv_migrator", database=database)
    except RuntimeError as exc:  # pragma: no cover - unconfigured workstation
        pytest.skip(f"the db lane is not configured: {exc}")


def _dotenv_lines() -> list[str]:
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return []
    return [
        line.strip()
        for line in dotenv.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]


def _admin_dsn() -> dbmod.MaskedDsn:
    raw = os.environ.get(_ADMIN_DSN_ENV)
    if not raw:
        for line in _dotenv_lines():
            key, _, value = line.partition("=")
            if key.strip() == _ADMIN_DSN_ENV:
                raw = value.strip()
                break
    if not raw:
        pytest.skip(f"{_ADMIN_DSN_ENV} is not configured; cannot create a throwaway database")
    return dbmod.MaskedDsn(raw)


@pytest.fixture(scope="module")
def obligations_database() -> Iterator[str]:
    """A migrated, empty database of this module's own, dropped afterwards."""
    name = _throwaway_name()
    admin = _admin_dsn()
    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE {name}")
        cur.execute(f"ALTER DATABASE {name} OWNER TO pv_migrator")
    try:
        env = dict(os.environ)
        env["COCKROACH_DATABASE_URL"] = str(_migrator_dsn(name))
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", DEPLOYED_HEAD],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            pytest.fail(
                f"alembic upgrade {DEPLOYED_HEAD} failed on the throwaway database\n"
                f"exit={completed.returncode}\n"
                f"--- stdout ---\n{dbmod.scrub(completed.stdout or '')}\n"
                f"--- stderr ---\n{dbmod.scrub(completed.stderr or '')}"
            )
        yield name
    finally:
        with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {name} CASCADE")


_ARTIFACT_COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "source_type",
    "s3_bucket",
    "s3_key",
    "content_sha256",
    "size_bytes",
    "mime_type",
    "source_message_id",
    "sender",
    "sender_domain",
    "recipient",
    "subject",
    "received_at",
    "event_time",
    "parser_status",
    "parser_version",
    "created_at",
    "updated_at",
)

#: Deliberately without ``embedding``: nothing here reads a vector, and
#: resolving 32 real embeddings to prove a database write would make this module
#: depend on a model provider.
_EVIDENCE_COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "artifact_id",
    "evidence_type",
    "normalized_text",
    "exact_text",
    "source_locator",
    "actor_ref",
    "valid_from",
    "valid_to",
    "observed_at",
    "extraction_confidence",
    "source_authority",
    "normalized_text_sha256",
    "created_at",
)


def _load_prerequisites(database: str) -> None:
    now: datetime = DEMO_ANCHOR_UTC
    with dbmod.connect_as("pv_migrator", database=database) as conn:
        load_small_planes(conn)
        dbmod.insert_batches(
            conn,
            "source_artifacts",
            _ARTIFACT_COLUMNS,
            [
                (
                    a.id,
                    a.tenant_id,
                    a.user_id,
                    a.source_type,
                    a.s3_bucket,
                    a.s3_key,
                    a.content_sha256,
                    a.size_bytes,
                    a.mime_type,
                    a.source_message_id,
                    a.sender,
                    a.sender_domain,
                    a.recipient,
                    a.subject,
                    a.received_at,
                    a.event_time,
                    a.parser_status,
                    a.parser_version,
                    now,
                    now,
                )
                for a in CURATED_ARTIFACTS
            ],
        )
        dbmod.insert_batches(
            conn,
            "evidence_items",
            _EVIDENCE_COLUMNS,
            [
                (
                    e.id,
                    e.tenant_id,
                    e.user_id,
                    e.artifact_id,
                    e.evidence_type,
                    e.normalized_text,
                    e.exact_text,
                    None if e.source_locator is None else Jsonb(e.source_locator),
                    e.actor_ref,
                    e.valid_from,
                    e.valid_to,
                    e.observed_at,
                    e.extraction_confidence,
                    e.source_authority,
                    hashlib.sha256(e.normalized_text.encode("utf-8")).digest(),
                    now,
                )
                for e in CURATED_EVIDENCE
            ],
        )


@pytest.fixture(scope="module")
def replayed(obligations_database: str) -> Any:
    """The prerequisites loaded, then the curated replay run once."""
    _load_prerequisites(obligations_database)
    return run_replay(database=obligations_database)


def _rows(database: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Read over a connection that was never inside the Kernel's transaction."""
    with psycopg.connect(_migrator_dsn(database)) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(row) for row in cur.fetchall()]


def _count(database: str, table: str) -> int:
    return int(_rows(database, f"SELECT count(*) FROM {table}")[0][0])


# ---------------------------------------------------------------------------
# Committing extra proposals through the Kernel, the way the seed does
# ---------------------------------------------------------------------------


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _commit(database: str, case_slug: str, proposal: MemoryProposal) -> Any:
    """Register the proposal as the app, then commit it as the Kernel.

    ``INSERT INTO memory_proposals`` is the app's statement, not the Kernel's -
    DDL section 12 grants the app INSERT and the Kernel only UPDATE - so it is
    reached through ``scripts.seed.loader`` rather than written again here.
    """
    from scripts.seed.loader import _commit_pending, _new_event_loop, _register_proposals

    seeded = SeedProposal(case_slug=case_slug, case_revision=0, proposal=proposal)
    with dbmod.connect_as("pv_app_reader_writer", database=database) as conn:
        _register_proposals(conn, [seeded])
    dsn = dbmod.role_dsn("pv_kernel_writer", database=database)
    loop = _new_event_loop()
    try:
        results = loop.run_until_complete(_commit_pending(str(dsn), [seeded]))
    finally:
        loop.close()
    return results[0][1]


def _deposit_trigger_proposal() -> MemoryProposal:
    """The Harborview deposit's overdue trigger, as a proposal.

    ``10_DATABASE_DDL.md`` section 17.6, transcribed:
    ``AND(GT(FIELD("commitments.deposit.outstanding_amount"), CONST(0)),
    GTE(FIELD("clock.now"), FIELD("commitments.deposit.due_at")))``.
    """
    case = case_of("landlord-deposit")
    predicate = PredicateNode(
        op=PredicateOp.AND,
        args=(
            PredicateNode(
                op=PredicateOp.GT,
                args=(
                    PredicateNode(
                        op=PredicateOp.FIELD,
                        path="commitments.deposit.outstanding_amount",
                    ),
                    # A DECIMAL comparison takes a JSON **string**; a bare
                    # `0` is refused with `DECIMAL_MUST_BE_STRING`.
                    PredicateNode(op=PredicateOp.CONST, value="0"),
                ),
            ),
            PredicateNode(
                op=PredicateOp.GTE,
                args=(
                    PredicateNode(op=PredicateOp.FIELD, path="clock.now"),
                    PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.due_at"),
                ),
            ),
        ),
    )
    artifact = evidence_of("deposit-thirty-day-promise").artifact_id
    return MemoryProposal(
        proposal_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-deposit-trigger"),
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-deposit-trigger-trace"),
        agent_run_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-deposit-trigger-run"),
        user_id=HERO_USER.id,
        source_artifact_ids=(artifact,),
        evidence_ids=(),
        identity=ProposalIdentity(
            relationship_id=case.relationship_id,
            case_id=case.id,
            confidence=Decimal("1.0000"),
            resolved_by="DETERMINISTIC",
        ),
        trigger_mutations=(
            ProposedTrigger(
                local_id="tg_001",
                mutation_kind=TriggerMutationKind.ARM,
                trigger_type=TriggerType.COMMITMENT_DEADLINE,
                predicate=predicate,
                not_before=TRIGGER_WAKE_AT,
                expires_at=DEPOSIT_DUE_AT + timedelta(days=365),
                rationale=(
                    "The deposit was promised within 30 days of the final inspection; "
                    "wake once the deadline has passed and check whether it arrived."
                ),
            ),
        ),
        model=_model(),
        idempotency_key="pv.oblig.deposit-trigger",
        created_at=DEMO_ANCHOR_UTC,
    )


# ---------------------------------------------------------------------------
# Defect 1a — the four seeded commitments now land
# ---------------------------------------------------------------------------


def test_the_replay_committed_every_curated_proposal(
    obligations_database: str, replayed: Any
) -> None:
    """Nine cases now, not eight: ``movers-scheduling`` is back in the replay.

    The **decision count** is deliberately not asserted against
    ``len(CURATED_PROPOSALS)``. ``scripts/seed/loader.run_replay`` submits a
    second pass of fulfillment proposals, and that number is the seed's to
    change - it changed under this module once already. What is asserted is the
    property that matters: every curated proposal reached a terminal accepting
    decision, and nothing was rejected.
    """
    assert len(CURATED_PROPOSALS) == 9
    decided = {
        row[0]: row[1]
        for row in _rows(obligations_database, "SELECT proposal_id, decision FROM kernel_decisions")
    }
    for seeded in CURATED_PROPOSALS:
        assert seeded.proposal.proposal_id in decided, seeded.case_slug
        assert decided[seeded.proposal.proposal_id].startswith("ACCEPTED"), seeded.case_slug
    assert set(decided.values()) <= {"ACCEPTED", "ACCEPTED_WITH_CONFLICT"}


def test_the_four_seeded_commitments_are_written(obligations_database: str, replayed: Any) -> None:
    """``commitments`` was empty after every previous replay, because no
    ``INSERT INTO commitments`` existed anywhere in the repository."""
    assert _count(obligations_database, "commitments") == 4


def test_the_deposit_commitment_is_eighteen_hundred_due_the_fifteenth_of_june(
    obligations_database: str, replayed: Any
) -> None:
    """``CANONICAL_DECISIONS.md`` -> *Hero dataset canon*, read back over a
    second connection rather than asserted against the plan."""
    rows = _rows(
        obligations_database,
        "SELECT currency, committed_amount, fulfilled_amount, outstanding_amount, "
        "       due_at, status, commitment_type, revision "
        "  FROM commitments WHERE commitment_type = 'DEPOSIT_RETURN'",
    )
    assert len(rows) == 1
    currency, committed, fulfilled, outstanding, due_at, status, kind, revision = rows[0]
    assert currency == "USD"
    assert isinstance(committed, Decimal)
    assert committed == Decimal("1800.0000")
    assert fulfilled == Decimal("0.0000")
    assert outstanding == Decimal("1800.0000")
    assert outstanding == committed - fulfilled
    assert due_at.isoformat() == "2026-06-15T00:00:00+00:00"
    assert status == "ACTIVE"
    assert kind == "DEPOSIT_RETURN"
    assert revision == 0


def test_the_non_monetary_commitment_carries_no_amounts(
    obligations_database: str, replayed: Any
) -> None:
    """M2 and M6 in the database rather than in the planner: the ISP
    termination promise stores three NULLs and no currency."""
    rows = _rows(
        obligations_database,
        "SELECT currency, committed_amount, fulfilled_amount, outstanding_amount, status "
        "  FROM commitments WHERE commitment_type = 'SERVICE_TERMINATION'",
    )
    assert len(rows) == 1
    assert rows[0] == (None, None, None, None, "ACTIVE")


def test_every_commitment_is_traceable_to_an_admitted_claim(
    obligations_database: str, replayed: Any
) -> None:
    """``fk_commitments_source_claim`` is NOT NULL; this proves the join
    resolves rather than that the column is populated."""
    orphans = _rows(
        obligations_database,
        "SELECT c.id FROM commitments c "
        " WHERE NOT EXISTS (SELECT 1 FROM claims cl WHERE cl.id = c.source_claim_id)",
    )
    assert orphans == []


def test_the_outstanding_identity_holds_for_every_monetary_row(
    obligations_database: str, replayed: Any
) -> None:
    broken = _rows(
        obligations_database,
        "SELECT id FROM commitments "
        " WHERE committed_amount IS NOT NULL "
        "   AND outstanding_amount <> committed_amount - fulfilled_amount",
    )
    assert broken == []


def test_a_commitment_created_event_and_a_ledger_row_accompany_each(
    obligations_database: str, replayed: Any
) -> None:
    """Section 6.2: ``state_transitions`` and ``outbox_events`` are consequences
    of a canonical change, and a commitment is one.

    Scoped to the **creation** rows (``from_state IS NULL``): the seed's second
    pass admits two fulfillments, and each of those moves a commitment status
    too, so an unscoped count would measure the seed rather than this rule.
    """
    transitions = _rows(
        obligations_database,
        "SELECT subject_id, from_state, to_state FROM state_transitions "
        " WHERE transition_type = 'COMMITMENT_STATUS' AND from_state IS NULL",
    )
    assert len(transitions) == 4
    assert {row[2] for row in transitions} == {"ACTIVE"}
    assert {row[0] for row in transitions} == {
        row[0] for row in _rows(obligations_database, "SELECT id FROM commitments")
    }
    events = _rows(
        obligations_database,
        "SELECT aggregate_type, aggregate_id FROM outbox_events "
        " WHERE event_type = 'commitment.created.v1'",
    )
    #: One per commit, not one per commitment - four commitments across four
    #: different cases here, so four events, each on its own case aggregate.
    assert len(events) == 4
    assert {row[0] for row in events} == {"CASE"}


# ---------------------------------------------------------------------------
# Defect 2 — the claim-only case commits instead of crashing
# ---------------------------------------------------------------------------


def test_the_claim_only_case_commits_with_an_audited_reason_code(
    obligations_database: str, replayed: Any
) -> None:
    """``movers-scheduling`` is the only curated case with no mapped predicate.

    Before the fix this raised ``ValueError: ACCEPTED was built with no reason
    code`` inside the transaction and the fixture had to be withheld from the
    replay by a commented line and an import-time guard.
    """
    case_id = case_of("movers-scheduling").id
    rows = _rows(
        obligations_database,
        "SELECT decision, reason_codes FROM kernel_decisions WHERE case_id = %s",
        (case_id,),
    )
    assert len(rows) == 1
    decision, reason_codes = rows[0]
    assert decision == "ACCEPTED"
    assert reason_codes, "an accepted commit with no reason code cannot be audited"
    assert "CONFLICT_HINT_UNMAPPED_FAMILY" in reason_codes


def test_the_claim_only_case_admitted_its_claims_and_grounded_no_belief(
    obligations_database: str, replayed: Any
) -> None:
    """Section 6.2: admitting a claim is a memory change even if no belief
    moves - and section 2.1: an unmapped predicate never grounds one here."""
    case_id = case_of("movers-scheduling").id
    claims = _rows(
        obligations_database,
        "SELECT predicate FROM claims WHERE case_id = %s ORDER BY predicate",
        (case_id,),
    )
    assert [row[0] for row in claims] == ["move_completed", "move_rescheduled"]
    beliefs = _rows(obligations_database, "SELECT id FROM beliefs WHERE case_id = %s", (case_id,))
    assert beliefs == []


# ---------------------------------------------------------------------------
# USD 2,020.00
# ---------------------------------------------------------------------------


def _outstanding_total(database: str) -> Decimal:
    """Exactly the sum the landing screen renders: monetary rows only.

    A non-monetary commitment has ``outstanding_amount IS NULL`` and is not
    coerced to zero, because a NULL that silently becomes ``0.00`` hides the
    difference between "nothing owed" and "not a money obligation".
    """
    rows = _rows(
        database,
        "SELECT coalesce(sum(outstanding_amount), 0) FROM commitments "
        " WHERE outstanding_amount IS NOT NULL AND currency = 'USD'",
    )
    return Decimal(rows[0][0])


def test_the_seed_replay_produces_two_thousand_and_twenty(
    obligations_database: str, replayed: Any
) -> None:
    """The headline figure, computed by the Kernel from ``Decimal`` arithmetic.

    ``1,800`` (Harborview, untouched) plus ``220`` (Beltline, ``420`` committed
    less ``200`` admitted). ``2,350`` of relocation is fulfilled and contributes
    zero; the ISP termination is non-monetary and contributes nothing at all,
    because its ``outstanding_amount`` is NULL rather than a coerced zero.

    Every row came through ``MemoryKernel.commit()`` - there is no second
    writer - and the total is read back over a connection that was never inside
    the transaction. Compared against ``obligations.outstanding_total()`` rather
    than a literal, so the seed and the Kernel cannot drift apart silently.
    """
    assert _count(obligations_database, "fulfillments") == 2
    assert _outstanding_total(obligations_database) == Decimal("2020.0000")
    assert _outstanding_total(obligations_database) == outstanding_total()


def test_each_obligation_carries_the_projection_the_ledger_implies(
    obligations_database: str, replayed: Any
) -> None:
    """``outstanding = committed - admitted``, per obligation, read back.

    The three monetary commitments land on three different statuses from the
    same rule: nothing admitted stays ``ACTIVE``, part admitted becomes
    ``PARTIAL``, all admitted becomes ``FULFILLED``. M5 forbids the fourth
    combination - ``FULFILLED`` with a positive outstanding - and the database
    would have refused it.
    """
    rows = _rows(
        obligations_database,
        "SELECT commitment_type, committed_amount, fulfilled_amount, "
        "       outstanding_amount, status "
        "  FROM commitments WHERE currency = 'USD' ORDER BY committed_amount",
    )
    projections = {(row[0], str(row[1])): (str(row[2]), str(row[3]), row[4]) for row in rows}
    assert projections[("MONETARY_REIMBURSEMENT", "420.0000")] == (
        "200.0000",
        "220.0000",
        "PARTIAL",
    )
    assert projections[("DEPOSIT_RETURN", "1800.0000")] == ("0.0000", "1800.0000", "ACTIVE")
    assert projections[("MONETARY_REIMBURSEMENT", "2350.0000")] == (
        "2350.0000",
        "0.0000",
        "FULFILLED",
    )
    assert _outstanding_total(obligations_database) == outstanding_total()


def _commitment_id(database: str, kind: str, committed: str) -> uuid.UUID:
    rows = _rows(
        database,
        "SELECT id FROM commitments WHERE commitment_type = %s AND committed_amount = %s",
        (kind, Decimal(committed)),
    )
    assert len(rows) == 1, f"{kind} {committed}: {rows}"
    return uuid.UUID(str(rows[0][0]))


# ---------------------------------------------------------------------------
# Prospective memory — the second reveal
# ---------------------------------------------------------------------------


def test_the_seed_arms_the_deposit_and_the_damage_follow_up(
    obligations_database: str, replayed: Any
) -> None:
    """``prospective_triggers`` was empty and unfillable. The curated proposals
    now carry two ``ARM`` mutations and both land."""
    rows = _rows(
        obligations_database,
        "SELECT trigger_type, state, evaluation_version, schedule_name "
        "  FROM prospective_triggers ORDER BY trigger_type",
    )
    assert len(rows) == 2
    assert [row[0] for row in rows] == ["COMMITMENT_DEADLINE", "RESPONSE_DEADLINE"]
    assert {row[1] for row in rows} == {"ARMED"}
    assert {row[2] for row in rows} == {1}
    assert all(str(row[3]).endswith("-v1") for row in rows)


def test_a_trigger_can_be_armed_for_the_harborview_deposit(
    obligations_database: str, replayed: Any
) -> None:
    """One more, armed by a proposal this module builds, so the arm path is
    exercised end to end rather than only through the seed.

    ``ARCHITECTURE.md`` section 22's second reveal needs a row here, and the
    row has to be one the evaluator can read months later.
    """
    result = _commit(obligations_database, "landlord-deposit", _deposit_trigger_proposal())
    assert str(result.decision).startswith("ACCEPTED"), result.reason_codes

    armed_id = result.trigger_changes[0].trigger_id
    rows = _rows(
        obligations_database,
        "SELECT case_id, trigger_type, state, not_before, expires_at, "
        "       evaluation_version, basis_case_revision, last_result, fired_at, "
        "       predicate_ast, schedule_name "
        "  FROM prospective_triggers WHERE id = %s",
        (armed_id,),
    )
    assert len(rows) == 1
    (
        case_id,
        trigger_type,
        state,
        not_before,
        expires_at,
        evaluation_version,
        basis_case_revision,
        last_result,
        fired_at,
        predicate_ast,
        schedule_name,
    ) = rows[0]
    assert case_id == case_of("landlord-deposit").id
    assert trigger_type == "COMMITMENT_DEADLINE"
    assert state == "ARMED"
    # `CANONICAL_DECISIONS.md` -> Hero dataset canon: due_at + WAKE_MARGIN.
    assert not_before.isoformat() == "2026-06-15T00:01:00+00:00"
    assert expires_at > not_before
    #: `16_TRIGGER_DSL.md` section 9.1 precondition 5: a fresh arm is
    #: generation 1, and section 9.3 stamps `schedule_name` with it.
    assert evaluation_version == 1
    assert last_result is None
    assert fired_at is None
    #: The stored envelope, not a bare node dump: `commitments.deposit`
    #: resolves through `bindings`.
    assert predicate_ast["ast_version"] == "1.0"
    assert predicate_ast["predicate"]["op"] == "AND"
    binding = predicate_ast["bindings"]["deposit"]
    assert binding["kind"] == "COMMITMENT"
    assert uuid.UUID(binding["id"]) == _commitment_id(
        obligations_database, "DEPOSIT_RETURN", "1800.0000"
    )

    #: The schedule name is the wake identity and the idempotency key, so it
    #: has to agree with the generation stored beside it.
    assert schedule_name == f"pv-trg-{armed_id.hex}-v1"

    #: The stored row parses with the evaluator that will read it months later.
    from services.control_plane.app.triggers import ast as trigger_ast
    from services.control_plane.app.triggers import registry as trigger_registry

    spec = trigger_ast.parse_spec(predicate_ast, trigger_registry.resolve_field)
    assert sorted(spec.referenced_paths) == [
        "clock.now",
        "commitments.deposit.due_at",
        "commitments.deposit.outstanding_amount",
    ]

    revision = _rows(
        obligations_database,
        "SELECT revision FROM cases WHERE id = %s",
        (case_of("landlord-deposit").id,),
    )[0][0]
    assert basis_case_revision == revision, (
        "rule I8: the evaluator compares the case's current revision against "
        "the one the trigger was armed at, so they must start equal"
    )


def test_arming_a_trigger_recorded_a_ledger_row_and_an_event(
    obligations_database: str, replayed: Any
) -> None:
    """The arm is a canonical change (section 6.2), so it carries both.

    Three triggers are armed by the time this runs - two from the seed and one
    from the test above - and each carries exactly one ledger row and one event
    keyed on its own TRIGGER aggregate.
    """
    triggers = {
        row[0] for row in _rows(obligations_database, "SELECT id FROM prospective_triggers")
    }
    assert len(triggers) == 3
    transitions = _rows(
        obligations_database,
        "SELECT subject_id, from_state, to_state, reason_code FROM state_transitions "
        " WHERE transition_type = 'TRIGGER_STATE'",
    )
    assert {row[0] for row in transitions} == triggers
    assert {(row[1], row[2], row[3]) for row in transitions} == {(None, "ARMED", "TRIGGER_ARMED")}
    events = _rows(
        obligations_database,
        "SELECT aggregate_type, aggregate_id FROM outbox_events "
        " WHERE event_type = 'trigger.armed.v1'",
    )
    assert {row[0] for row in events} == {"TRIGGER"}
    assert {row[1] for row in events} == triggers


def test_the_deposit_is_still_outstanding_when_the_trigger_wakes(
    obligations_database: str, replayed: Any
) -> None:
    """The second reveal, as the query the overdue sweep actually runs.

    ``idx_commitments_overdue`` covers exactly this predicate. Nothing is
    mutated and nothing is reverted: the deposit is overdue because
    ``2026-06-15`` passed and ``USD 1,800.00`` never arrived.
    """
    rows = _rows(
        obligations_database,
        "SELECT outstanding_amount, currency, status FROM commitments "
        " WHERE due_at IS NOT NULL AND due_at < %s "
        "   AND outstanding_amount > 0 AND status IN ('ACTIVE', 'PARTIAL')",
        (DEMO_ANCHOR_UTC,),
    )
    assert rows == [(Decimal("1800.0000"), "USD", "ACTIVE")]
    overdue_days = (DEMO_ANCHOR_UTC - DEPOSIT_DUE_AT).days
    assert overdue_days == 95, "every 'days overdue' figure derives from due_at"


# ---------------------------------------------------------------------------
# A counterparty denial must not discharge the debt
# ---------------------------------------------------------------------------


def _denial_proposal(commitment_id: uuid.UUID, amount: str) -> MemoryProposal:
    """Beltline's support chat says the payment was never issued.

    ``22_EVAL_DATASETS.md`` CX-05's shape against real rows: the denial arrives
    as ``PROVIDER_AGENT_CHAT`` (PAYMENT authority 0.45) against a payment the
    Kernel admitted in the previous commit, and ``monetary_exposure = 200.00``
    is at or above the 100.00 gate.
    """
    evidence = evidence_of("damage-partial-payment")
    case = case_of("movers-damage")
    return MemoryProposal(
        proposal_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-denial"),
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-denial-trace"),
        agent_run_id=uuid.uuid5(uuid.NAMESPACE_URL, "pv-oblig-denial-run"),
        user_id=HERO_USER.id,
        source_artifact_ids=(evidence.artifact_id,),
        evidence_ids=(evidence.id,),
        identity=ProposalIdentity(
            relationship_id=case.relationship_id,
            case_id=case.id,
            confidence=Decimal("1.0000"),
            resolved_by="DETERMINISTIC",
        ),
        claims=(
            ProposedClaim(
                local_id="cl_001",
                claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
                subject_type=SubjectType.COMMITMENT,
                subject_id=commitment_id,
                predicate="payment_not_received",
                object_type=ValueType.MONEY,
                object_value={
                    "currency": "USD",
                    "amount": amount,
                    "paid_at": (DEPOSIT_DUE_AT - timedelta(days=21)).isoformat(),
                },
                actor_type=ActorType.COUNTERPARTY,
                actor_ref="Beltline Movers",
                evidence_id=evidence.id,
                source_class=SourceClass.PROVIDER_AGENT_CHAT,
                modality=Modality.ASSERTED_PAST,
                valid_from=DEPOSIT_DUE_AT - timedelta(days=21),
                valid_to=None,
                extraction_confidence=Decimal("0.9100"),
            ),
        ),
        model=_model(),
        idempotency_key="pv.oblig.denial",
        created_at=evidence.observed_at,
    )


def test_a_denial_raises_a_conflict_and_leaves_the_money_alone(
    obligations_database: str, replayed: Any
) -> None:
    """The blocker, against a cluster.

    ``pipeline._apply_payment`` read the ``PaymentValue``'s amount and currency
    and never its ``asserted`` flag, so a denial was admitted as a payment: the
    admitted total went 200 -> 400 and the outstanding 220 -> 20, with zero
    conflicts and no attention. Every DDL guard passed, because the money
    invariants hold perfectly for the wrong numbers.

    This also exercises ``_READ_LEDGER_SQL``'s read-back of the grounding claim
    and its authority - without which there is no margin to measure and no side
    id for the conflict, and no unit test can see the projection drift.
    """
    damage_id = _commitment_id(obligations_database, "MONETARY_REIMBURSEMENT", "420.0000")
    before = _rows(
        obligations_database,
        "SELECT fulfilled_amount, outstanding_amount, status, revision "
        "  FROM commitments WHERE id = %s",
        (damage_id,),
    )[0]
    assert before[:3] == (Decimal("200.0000"), Decimal("220.0000"), "PARTIAL")

    result = _commit(obligations_database, "movers-damage", _denial_proposal(damage_id, "200.00"))
    assert str(result.decision) == "ACCEPTED_WITH_CONFLICT", result.reason_codes

    after = _rows(
        obligations_database,
        "SELECT fulfilled_amount, outstanding_amount, status, revision "
        "  FROM commitments WHERE id = %s",
        (damage_id,),
    )[0]
    #: The money is untouched and the obligation is now disputed.
    assert after[0] == Decimal("200.0000")
    assert after[1] == Decimal("220.0000")
    assert after[2] == "DISPUTED"
    assert after[3] == before[3] + 1

    #: No second fulfillment row. The denial is not a payment.
    admitted = _rows(
        obligations_database,
        "SELECT count(*) FROM fulfillments WHERE commitment_id = %s",
        (damage_id,),
    )[0][0]
    assert int(admitted) == 1

    conflicts = _rows(
        obligations_database,
        "SELECT conflict_type, status, severity, requires_human, predicate, "
        "       left_source_id, right_source_id "
        "  FROM conflicts WHERE subject_id = %s",
        (damage_id,),
    )
    assert len(conflicts) == 1
    kind, status, severity, requires_human, predicate, left, right = conflicts[0]
    assert kind == "FULFILLMENT_CONFLICT"
    assert status == "NEEDS_HUMAN"
    assert severity == "HIGH"
    assert requires_human is True
    assert predicate == "payment_received"
    #: `ck_conflicts_side_order` accepted the row, which is the point: the sides
    #: are ordered rather than fixed to a position.
    assert str(left) <= str(right)

    #: Invariant 1: the denial is preserved as a claim whatever was decided.
    claims = _rows(
        obligations_database,
        "SELECT predicate, object_json FROM claims WHERE subject_id = %s "
        "   AND predicate = 'payment_not_received'",
        (damage_id,),
    )
    assert len(claims) == 1
    assert claims[0][1]["amount"] == "200.00"


def test_the_denial_does_not_change_the_outstanding_total(
    obligations_database: str, replayed: Any
) -> None:
    """USD 2,020.00 still. A denial moves attention, never money."""
    assert _outstanding_total(obligations_database) == Decimal("2020.0000")
