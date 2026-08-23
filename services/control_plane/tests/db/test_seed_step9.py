"""Seed step 9 — the curated ``MemoryProposal`` replay, end to end.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5 ``T2.8`` sub-task 9: "Replay the
  curated ``MemoryProposal`` fixtures through ``MemoryKernel.commit()`` as
  ``pv_kernel_writer`` ... Seeding canonical rows by raw INSERT to unblock
  Phase 2 would create a second canonical writer and is forbidden."
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero dataset canon* and *Hero commit
  canon*: Alex Rivera, Northline Fiber's two relationships, the ISP case left
  ``RESOLVED`` at revision 12, and the June invoice **not** seeded.
- ``docs/quality/22_EVAL_DATASETS.md`` section 3 fixture
  ``the_move_baseline_rev12``: "Full hero world, case 1 at revision 12".

Why this module builds its own database
---------------------------------------
The shared lane fixtures in ``conftest.py`` resolve ``provenance_ci`` and
nothing else, and step 9 writes canonical rows for the whole hero world — it
cannot be rolled back around a test, because the Kernel commits over its own
pool connection and a second connection has to be able to see the result
(``23_PHASE_GATES.md`` section 10). Sharing ``provenance_ci`` with the rest of
the lane would therefore leave 40-odd canonical rows behind for
``make db-verify`` to trip over, and sharing the demo database ``provenance``
would corrupt the 18,035-row corpus outright.

So this module creates a throwaway database, migrates it, replays into it, and
drops it. Nothing here can reach ``provenance`` or ``provenance_ci``:
:func:`_throwaway_name` mints a name that is neither, and the fixture asserts
that before it issues ``CREATE DATABASE``.

Credential hygiene
------------------
Every DSN is a :class:`scripts.seed.db.MaskedDsn` and every subprocess
transcript goes through :func:`scripts.seed.db.scrub` before it can reach a
pytest failure header. ``test_no_credential_in_pytest_output.py`` is the
standing regression guard for the whole lane.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from provenance_contracts.proposal import MemoryProposal
from scripts.seed import db as dbmod
from scripts.seed.cases import case_of
from scripts.seed.evidence import CURATED_ARTIFACTS, CURATED_EVIDENCE
from scripts.seed.ids import DEMO_ANCHOR_UTC
from scripts.seed.loader import load_small_planes, replay_curated_proposals, run_replay
from scripts.seed.obligations import outstanding_total
from scripts.seed.proposals import (
    CURATED_PROPOSALS,
    SEED_MODEL_ID,
    curated_proposals,
    fulfillment_proposal_ids,
)
from scripts.seed.tenants import HERO_TENANT, HERO_USER

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

#: The two databases this module must never touch. ``provenance`` holds the
#: 18,035-row demo corpus; ``provenance_ci`` is the shared migration lane.
FORBIDDEN_DATABASES = frozenset({"provenance", "provenance_ci"})

#: The revision this lane migrates to, quoted from ``conftest.DEPLOYED_HEAD``
#: rather than imported: pytest exposes a conftest as a top-level ``conftest``
#: module and three of them exist in this repository, so importing by that name
#: is a coin toss. ``0009`` is not deployed -- it drops the vector column and
#: refuses to run without an explicit acknowledgement -- and the lane is pinned
#: to what is actually running.
DEPLOYED_HEAD = "0008_events_infrastructure"

#: ``CANONICAL_DECISIONS.md`` -> Hero dataset canon. The demo performs the move
#: to 13 live; a seed that spent it would make the demo a replay of itself.
HERO_CASE_SLUG = "isp-cancellation"
HERO_CASE_REVISION = 12
HERO_CASE_STATUS = "RESOLVED"


def _throwaway_name() -> str:
    name = f"pv_step9_{uuid.uuid4().hex[:12]}"
    assert name not in FORBIDDEN_DATABASES
    return name


def _migrator_dsn(database: str) -> dbmod.MaskedDsn:
    try:
        return dbmod.role_dsn("pv_migrator", database=database)
    except RuntimeError as exc:  # pragma: no cover - unconfigured workstation
        pytest.skip(f"the db lane is not configured: {exc}")


#: The credential that may issue ``CREATE DATABASE``.
#:
#: ``pv_migrator`` deliberately cannot: on this CockroachDB Cloud cluster it
#: owns every table in ``provenance`` and ``provenance_ci`` and holds no
#: cluster-level ``CREATEDB``, which is the right shape for a migration role and
#: is why ``role_dsn`` does not know this key. Creating the throwaway database
#: therefore needs the cluster owner, and the database is handed to
#: ``pv_migrator`` immediately so every statement after ``CREATE DATABASE`` runs
#: under the role the seed actually uses.
_ADMIN_DSN_ENV = "PV_PROBE_DB_URL"


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


def _dotenv_lines() -> list[str]:
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return []
    return [
        line.strip()
        for line in dotenv.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]


@pytest.fixture(scope="module")
def step9_database() -> Iterator[str]:
    """A migrated, empty database of this module's own, dropped afterwards."""
    name = _throwaway_name()
    admin = _admin_dsn()
    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE {name}")
        # Handed over immediately: everything from `alembic upgrade` onward runs
        # as `pv_migrator`, so the throwaway database is migrated by the same
        # role that migrates the real ones.
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
                "alembic upgrade head failed on the throwaway database\n"
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

#: Deliberately without ``embedding``: step 9 reads no vector, and resolving
#: 32 real Titan embeddings to prove a Kernel replay would make this module
#: depend on Bedrock to test a database write.
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
    """Steps 3 and 6 of the seed: the small planes and the curated corpus."""
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
def replayed(step9_database: str) -> Any:
    """The prerequisites loaded, then step 9 run once. Returns its report."""
    _load_prerequisites(step9_database)
    return run_replay(database=step9_database)


def _rows(database: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Read over a connection that was never inside the Kernel's transaction."""
    with psycopg.connect(_migrator_dsn(database)) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(row) for row in cur.fetchall()]


def _count(database: str, table: str) -> int:
    return int(_rows(database, f"SELECT count(*) FROM {table}")[0][0])


# ---------------------------------------------------------------------------
# The fixtures themselves — hermetic, no database needed
# ---------------------------------------------------------------------------


def test_every_curated_proposal_validates() -> None:
    """A ``MemoryProposal`` that does not validate is not a fixture."""
    proposals = curated_proposals()
    assert proposals, "step 9 has nothing to replay"
    assert all(isinstance(p, MemoryProposal) for p in proposals)


def test_one_proposal_per_curated_case() -> None:
    """One proposal per case: rule R1 spends exactly one case revision per
    accepted commit, so two proposals for one case would spend two."""
    case_ids = [p.identity.case_id for p in curated_proposals()]
    assert len(case_ids) == len(set(case_ids))


def test_proposal_ids_are_stable_across_processes() -> None:
    """Replay idempotence rests on the proposal id, so it is a ``uuid5``."""
    first = [p.proposal_id for p in curated_proposals()]
    second = [p.proposal_id for p in curated_proposals()]
    assert first == second


def test_the_june_invoice_is_not_seeded() -> None:
    """The hero event is what the demo performs live (``00_PRODUCT.md`` 2.3)."""
    blob = "\n".join(p.model_dump_json() for p in curated_proposals())
    assert "186" not in blob
    assert all(p.requested_case_transition is None for p in curated_proposals())


def test_no_proposal_carries_a_conflict_hint() -> None:
    """The hero conflict is detected at demo time, not asserted by the seed."""
    assert all(not p.conflict_hints for p in curated_proposals())


def test_every_belief_bearing_claim_names_a_kernel_surface_predicate() -> None:
    """A predicate outside the closed registry is admitted as a claim and never
    grounds a belief, so a typo would silently produce a claim-only seed."""
    from services.control_plane.app.memory_kernel import families

    mapped = [
        claim
        for proposal in curated_proposals()
        for claim in proposal.claims
        if families.family_of(claim.predicate) is not families.Family.UNMAPPED
    ]
    assert mapped, "no seeded claim maps to a predicate family; no belief would exist"
    for claim in mapped:
        family = families.family_of(claim.predicate)
        assert families.produces_belief(family), claim.predicate
        assert families.valid_subject_type(family, claim.subject_type), claim.predicate


def test_at_most_one_belief_bearing_claim_per_subject_and_family() -> None:
    """``uq_beliefs_proposition`` is ``(tenant, user, subject_type, subject_id,
    predicate)`` and ignores the case, so two claims of one family on one
    subject would be two INSERTs of the same belief row anywhere in the seed."""
    from services.control_plane.app.memory_kernel import families

    seen: set[tuple[str, uuid.UUID, str]] = set()
    for proposal in curated_proposals():
        for claim in proposal.claims:
            family = families.family_of(claim.predicate)
            if family is families.Family.UNMAPPED:
                continue
            assert claim.subject_id is not None
            key = (str(claim.subject_type), claim.subject_id, families.canonical_predicate(family))
            assert key not in seen, f"two beliefs would collide on {key}"
            seen.add(key)


def test_the_hero_balance_claim_keeps_the_conflict_a_value_conflict() -> None:
    """``PROVIDER_AGENT_WRITTEN`` scores ``BALANCE`` at 0.7200.

    Matcher ``M13`` promotes a ``VALUE_CONFLICT`` to ``AUTHORITY_CONFLICT`` when
    ``min(left, right) >= high_authority_floor`` and the two are within
    ``auto_resolve_margin``. The June invoice arrives as
    ``PROVIDER_SYSTEM_NOTICE`` at 0.9000, so an incumbent seeded any higher than
    0.7999 changes which rule decides the hero — and the canon fixes it as
    ``VALUE_CONFLICT`` produced by gate ``H5``.
    """
    from provenance_domain.enums import SourceClass
    from services.control_plane.app.memory_kernel import families
    from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG

    hero = next(p for p in curated_proposals() if p.identity.case_id == case_of(HERO_CASE_SLUG).id)
    balance = [c for c in hero.claims if families.family_of(c.predicate) is families.Family.BALANCE]
    assert len(balance) == 1
    authority = families.authority_for(
        families.Family.BALANCE, balance[0].source_class, DEFAULT_KERNEL_CONFIG
    )
    challenger = families.authority_for(
        families.Family.BALANCE, SourceClass.PROVIDER_SYSTEM_NOTICE, DEFAULT_KERNEL_CONFIG
    )
    assert (
        min(authority, challenger) < DEFAULT_KERNEL_CONFIG.high_authority_floor
    ), "M13 would promote the hero conflict to AUTHORITY_CONFLICT"


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def test_replay_commits_every_proposal(replayed: Any, step9_database: str) -> None:
    """Both passes, counted together: nine curated proposals and two payments."""
    expected = len(CURATED_PROPOSALS) + len(fulfillment_proposal_ids())
    assert replayed.committed == expected, replayed.decisions
    assert replayed.rejected == 0, replayed.decisions
    assert replayed.fulfillments_admitted == len(fulfillment_proposal_ids())
    assert _count(step9_database, "memory_proposals") == expected
    assert _count(step9_database, "kernel_decisions") == expected


def test_the_proposal_rows_name_the_deterministic_kernel(
    step9_database: str, replayed: Any
) -> None:
    """``ck_memory_proposals_model`` carries ``deterministic.kernel`` for exactly
    this: a seeded proposal was not produced by a model."""
    rows = _rows(step9_database, "SELECT DISTINCT model_id FROM memory_proposals")
    assert rows == [(SEED_MODEL_ID,)]


def _expected_beliefs() -> int:
    """One belief per distinct ``(subject_type, subject_id, canonical predicate)``.

    Derived from the fixtures rather than pinned, so adding a belief-bearing
    claim moves this number without a second edit -- and adding a *colliding*
    one makes it disagree with the row count, which is the failure worth having.
    """
    from services.control_plane.app.memory_kernel import families

    keys = set()
    for proposal in curated_proposals():
        for claim in proposal.claims:
            family = families.family_of(claim.predicate)
            if not families.produces_belief(family):
                continue
            keys.add(
                (str(claim.subject_type), claim.subject_id, families.canonical_predicate(family))
            )
    return len(keys)


def test_step_nine_row_counts(step9_database: str, replayed: Any) -> None:
    """Exactly which tables step 9 populates, and with how many rows.

    Every figure is derived from the fixtures rather than pinned, so a change to
    the curated set moves the expectation with it and a change that *collides*
    makes the two disagree -- which is the failure worth having.

    ``conflicts`` is zero and that is the point: the hero ``VALUE_CONFLICT`` is
    the demo's to detect.

    ``state_transitions`` and ``outbox_events`` are one per obligation opened,
    one per obligation whose status then moves, and one per trigger armed. No
    case status moves -- nothing requests a transition -- so the ``CASE_STATUS``
    half contributes nothing, and both payments do move their commitment
    (``ACTIVE -> PARTIAL`` for Beltline, ``ACTIVE -> FULFILLED`` for Kestrel).
    """
    beliefs = _expected_beliefs()
    proposals = len(CURATED_PROPOSALS) + len(fulfillment_proposal_ids())
    commitments = sum(len(p.commitments) for p in curated_proposals())
    triggers = sum(len(p.trigger_mutations) for p in curated_proposals())
    fulfillments = len(fulfillment_proposal_ids())
    expected = {
        "memory_proposals": proposals,
        "kernel_decisions": proposals,
        "claims": sum(len(p.claims) for p in curated_proposals()) + fulfillments,
        "beliefs": beliefs,
        "belief_versions": beliefs,
        "belief_support": beliefs,
        "conflicts": 0,
        "commitments": commitments,
        "fulfillments": fulfillments,
        "prospective_triggers": triggers,
        "state_transitions": commitments + fulfillments + triggers,
        "outbox_events": commitments + fulfillments + triggers,
    }
    actual = {table: _count(step9_database, table) for table in expected}
    assert actual == expected


def test_the_outstanding_total_is_the_canonical_figure(step9_database: str, replayed: Any) -> None:
    """USD 2,020.00 -- the one number the landing screen renders.

    Harborview 1,800.00 + Beltline 220.00 + Kestrel 0.00. Northline's
    termination is non-monetary and contributes NULL rather than a coerced
    zero, because a NULL that silently becomes 0.00 hides the difference
    between "nothing owed" and "not a money obligation".

    This is the assertion that makes the second replay pass necessary rather
    than tidy. Without it every obligation stands at its full committed amount
    and this query returns USD 4,570.00 -- a number that is wrong in the one
    place a judge looks first, and wrong in a direction that flatters us.
    """
    rows = _rows(
        step9_database,
        "SELECT coalesce(sum(outstanding_amount), 0) FROM commitments "
        "WHERE outstanding_amount IS NOT NULL",
    )
    assert rows[0][0] == outstanding_total() == Decimal("2020.00")


def test_the_partial_fulfillment_is_admitted_and_recomputed(
    step9_database: str, replayed: Any
) -> None:
    """Beltline: USD 420.00 committed, USD 200.00 paid, USD 220.00 outstanding.

    ``fulfilled_amount`` is recomputed from the ledger, never incremented
    (``12_KERNEL_ALGORITHMS.md`` section 4.2), so the three columns are read
    back together: a total that agreed with the ledger by luck rather than by
    derivation would pass a check of any one of them.
    """
    rows = _rows(
        step9_database,
        "SELECT c.status, c.committed_amount, c.fulfilled_amount, c.outstanding_amount, "
        "count(f.id) FROM commitments c LEFT JOIN fulfillments f ON f.commitment_id = c.id "
        "WHERE c.case_id = %s GROUP BY 1, 2, 3, 4",
        (case_of("movers-damage").id,),
    )
    assert rows == [("PARTIAL", Decimal("420.0000"), Decimal("200.0000"), Decimal("220.0000"), 1)]


def test_every_belief_version_is_grounded(step9_database: str, replayed: Any) -> None:
    """The grounding invariant, read back from the rows rather than trusted."""
    ungrounded = _rows(
        step9_database,
        "SELECT bv.id FROM belief_versions bv "
        "WHERE bv.derivation_kind <> 'DETERMINISTIC_DERIVATION' AND NOT EXISTS ("
        "  SELECT 1 FROM belief_support bs"
        "   WHERE bs.belief_version_id = bv.id AND bs.relation = 'SUPPORTS')",
    )
    assert ungrounded == []


def test_the_service_terminated_belief_exists(step9_database: str, replayed: Any) -> None:
    """The fact the June invoice contradicts. Rule N1 stores it under the
    family's canonical predicate ``service_active``, so a query for
    ``service_terminated`` finds nothing and that is correct."""
    from scripts.seed.counterparties import relationship_of

    rows = _rows(
        step9_database,
        "SELECT bv.value_json, bv.valid_from FROM beliefs b "
        "JOIN belief_versions bv ON bv.id = b.current_version_id "
        "WHERE b.subject_type = 'RELATIONSHIP' AND b.subject_id = %s "
        "AND b.predicate = 'service_active'",
        (relationship_of("northline-old").id,),
    )
    assert len(rows) == 1
    value, valid_from = rows[0]
    assert value == {"state": "TERMINATED"}
    assert valid_from is not None
    assert valid_from.date().isoformat() == "2026-05-31"


def test_the_seed_creates_no_conflict(step9_database: str, replayed: Any) -> None:
    """The hero ``VALUE_CONFLICT`` is the demo's, and there is no other."""
    assert _count(step9_database, "conflicts") == 0


def test_the_isp_case_is_left_resolved_at_revision_twelve(
    step9_database: str, replayed: Any
) -> None:
    rows = _rows(
        step9_database,
        "SELECT status, revision, reopened_count FROM cases WHERE id = %s",
        (case_of(HERO_CASE_SLUG).id,),
    )
    assert rows == [(HERO_CASE_STATUS, HERO_CASE_REVISION, 0)]


def test_every_case_is_left_at_its_canonical_revision(step9_database: str, replayed: Any) -> None:
    """``22_EVAL_DATASETS.md`` section 2 pins a revision per case as ground
    truth, so a replay that spent one is a replay that moved the baseline."""
    for seeded in CURATED_PROPOSALS:
        case = case_of(seeded.case_slug)
        rows = _rows(step9_database, "SELECT status, revision FROM cases WHERE id = %s", (case.id,))
        assert rows == [(case.status, case.revision)], seeded.case_slug


def test_replayed_cases_are_raised_to_at_least_info_attention(
    step9_database: str, replayed: Any
) -> None:
    """A fact the Kernel makes unavoidable, pinned here so it cannot drift
    silently, and reported rather than papered over.

    ``disposition.decide_no_incumbent`` returns ``case_attention = INFO`` for
    every first belief version and ``case_ops.plan_case_update`` takes the max
    against the case's current level, so a case that carries a belief cannot
    also be ``NONE``. ``ops/41_RUNBOOK.md`` and
    ``frontend/33_DESIGN_PROTOTYPE_PROMPT.md`` both describe the hero case as
    "revision 12, attention NONE" **and** ``22_EVAL_DATASETS.md`` describes the
    same baseline as carrying the ``service-status`` belief; those two are
    jointly unreachable through the Kernel. Nothing in this seed can fix that
    -- a post-hoc ``UPDATE cases SET attention_level`` would overwrite what the
    Kernel just decided, which is the opposite of a single canonical writer.
    """
    from provenance_domain.enums import AttentionLevel
    from scripts.seed.proposals import _HAS_BELIEF_BEARING_CLAIM

    order = (
        AttentionLevel.NONE,
        AttentionLevel.INFO,
        AttentionLevel.ATTENTION,
        AttentionLevel.URGENT,
    )
    for seeded in CURATED_PROPOSALS:
        case = case_of(seeded.case_slug)
        rows = _rows(step9_database, "SELECT attention_level FROM cases WHERE id = %s", (case.id,))
        actual = AttentionLevel(rows[0][0])
        declared = AttentionLevel(case.attention_level)
        # A case with no belief-bearing claim keeps its declared level: the lift
        # comes from `decide_no_incumbent`, and `movers-scheduling` creates no
        # belief for it to fire on. That case is the control here -- it shows the
        # lift is caused by belief creation rather than by any commit at all.
        floor = (
            AttentionLevel.INFO
            if _HAS_BELIEF_BEARING_CLAIM[seeded.case_slug]
            else AttentionLevel.NONE
        )
        assert order.index(actual) == max(order.index(declared), order.index(floor))


def test_the_hero_tenant_owns_every_row(step9_database: str, replayed: Any) -> None:
    for table in (
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "kernel_decisions",
        "commitments",
        "fulfillments",
    ):
        rows = _rows(step9_database, f"SELECT DISTINCT tenant_id, user_id FROM {table}")
        assert rows == [(HERO_TENANT.id, HERO_USER.id)], table


def test_replay_is_idempotent(step9_database: str, replayed: Any) -> None:
    """A second run must write nothing and must not move a revision.

    Idempotence comes from ``proposal_id`` and the unique constraints, which is
    what ``UNIQUE_VIOLATION_MAP`` and the Kernel's replay guard are for; nothing
    here pre-checks whether a row is already present.
    """
    before = {
        table: _count(step9_database, table)
        for table in (
            "claims",
            "beliefs",
            "belief_versions",
            "belief_support",
            "kernel_decisions",
            "memory_proposals",
            "conflicts",
            "commitments",
            "fulfillments",
            "state_transitions",
            "outbox_events",
        )
    }
    second = run_replay(database=step9_database)
    after = {table: _count(step9_database, table) for table in before}
    assert after == before
    assert second.committed == 0
    assert second.replayed == len(CURATED_PROPOSALS) + len(fulfillment_proposal_ids())
    assert second.positioned_cases == 0, "a second run must not move a revision counter"
    rows = _rows(
        step9_database,
        "SELECT status, revision FROM cases WHERE id = %s",
        (case_of(HERO_CASE_SLUG).id,),
    )
    assert rows == [(HERO_CASE_STATUS, HERO_CASE_REVISION)]


# ---------------------------------------------------------------------------
# Profile handling
# ---------------------------------------------------------------------------


def test_schema_only_still_defers_step_nine() -> None:
    status = replay_curated_proposals("schema-only")
    assert status.lower().startswith("deferred")


def test_isolation_profile_skips_step_nine() -> None:
    status = replay_curated_proposals("isolation")
    assert "skipped" in status.lower()


def test_movers_scheduling_is_the_claim_only_case_and_is_replayed() -> None:
    """Case 6 carries only unmapped predicates, and that is now a supported
    outcome rather than a crash.

    It used to be withheld: a commit admitting only unmapped claims reached
    ``decisions.build_decision_row`` with an empty reason-code tuple and raised
    ``ValueError: ACCEPTED was built with no reason code``. The Kernel now
    contributes ``CONFLICT_HINT_UNMAPPED_FAMILY`` for exactly that claim, so the
    case replays like the other eight and is the seed's standing exercise of
    that path.
    """
    from scripts.seed.proposals import _HAS_BELIEF_BEARING_CLAIM

    assert "movers-scheduling" in {s.case_slug for s in CURATED_PROPOSALS}
    assert _HAS_BELIEF_BEARING_CLAIM["movers-scheduling"] == ()


def test_the_claim_only_case_is_accepted_and_grounds_no_belief(
    step9_database: str, replayed: Any
) -> None:
    """Its claims are canonical; its case carries no belief. Both, together."""
    case_id = case_of("movers-scheduling").id
    claims = _rows(step9_database, "SELECT count(*) FROM claims WHERE case_id = %s", (case_id,))
    beliefs = _rows(step9_database, "SELECT count(*) FROM beliefs WHERE case_id = %s", (case_id,))
    assert claims[0][0] == 2
    assert beliefs[0][0] == 0
    decision = _rows(
        step9_database,
        "SELECT decision FROM kernel_decisions WHERE case_id = %s",
        (case_id,),
    )
    assert decision == [("ACCEPTED",)]


def test_the_deposit_commitment_is_written_with_its_canonical_due_date(
    step9_database: str, replayed: Any
) -> None:
    """USD 1,800.00 due ``2026-06-15T00:00:00Z`` -- 95 days before the demo
    clock, and every "days overdue" figure derives from it rather than being
    stored. Read back from the row the Kernel wrote, not from the fixture."""
    rows = _rows(
        step9_database,
        "SELECT commitment_type, currency, committed_amount, outstanding_amount, due_at, status "
        "FROM commitments WHERE case_id = %s",
        (case_of("landlord-deposit").id,),
    )
    assert len(rows) == 1
    kind, currency, committed, outstanding, due_at, status = rows[0]
    assert kind == "DEPOSIT_RETURN"
    assert (currency, committed, outstanding) == ("USD", Decimal("1800.0000"), Decimal("1800.0000"))
    assert due_at.isoformat() == "2026-06-15T00:00:00+00:00"
    assert status == "ACTIVE"
    assert (DEMO_ANCHOR_UTC.date() - due_at.date()).days == 95


def test_the_deposit_trigger_is_armed_at_the_canonical_wake_time(
    step9_database: str, replayed: Any
) -> None:
    """``2026-06-15T00:01:00Z`` -- ``due_at`` plus ``WAKE_MARGIN_SECONDS``.

    The margin is not cosmetic: ``16_TRIGGER_DSL.md`` section 12 has the
    scheduler jitter in both directions, so without it the common case wastes a
    ``WOKE_TOO_EARLY`` no-op on every deadline.

    ``basis_case_revision`` is the revision the arming commit *produced*, which
    is the case's declared revision -- rule ``R3``, and the number the evaluator
    compares against months later to decide whether the world moved underneath
    the trigger.
    """
    rows = _rows(
        step9_database,
        "SELECT trigger_type, state, not_before, basis_case_revision FROM prospective_triggers "
        "WHERE case_id = %s",
        (case_of("landlord-deposit").id,),
    )
    assert len(rows) == 1
    trigger_type, state, not_before, basis = rows[0]
    assert (trigger_type, state) == ("COMMITMENT_DEADLINE", "ARMED")
    assert not_before.isoformat() == "2026-06-15T00:01:00+00:00"
    assert basis == case_of("landlord-deposit").revision


def test_every_armed_trigger_binds_the_obligation_it_watches(
    step9_database: str, replayed: Any
) -> None:
    """A trigger's stored predicate must be readable by the evaluator that will
    wake it, and its binding must point at a commitment that exists.

    This is the assertion that would have caught a trigger armed with a bare
    predicate node: ``parse_spec`` refuses that envelope, and the refusal would
    otherwise have surfaced months later on a row nobody remembers writing.
    """
    from services.control_plane.app.triggers import ast as trigger_ast
    from services.control_plane.app.triggers import registry

    rows = _rows(step9_database, "SELECT id, predicate_ast FROM prospective_triggers")
    assert rows, "no trigger was armed"
    known = {row[0] for row in _rows(step9_database, "SELECT id FROM commitments")}
    for _trigger_id, document in rows:
        spec = trigger_ast.parse_spec(document, registry.resolve_field)
        bound = {b.commitment_id for b in spec.bindings}
        assert bound, "an armed trigger declares no binding"
        assert bound <= known, "an armed trigger binds a commitment that does not exist"


def test_the_non_monetary_commitment_carries_a_null_outstanding(
    step9_database: str, replayed: Any
) -> None:
    """Northline's termination is an obligation with no amount. A NULL coerced
    to 0.00 would hide the difference between "nothing owed" and "not a money
    obligation", and would make the landing total silently include it."""
    rows = _rows(
        step9_database,
        "SELECT commitment_type, currency, committed_amount, outstanding_amount "
        "FROM commitments WHERE case_id = %s",
        (case_of("isp-cancellation").id,),
    )
    assert rows == [("SERVICE_TERMINATION", None, None, None)]
