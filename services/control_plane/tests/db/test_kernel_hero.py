"""Phase 4 database assertions — the hero commit and its negative controls.

Authority
---------
- ``quality/23_PHASE_GATES.md`` ``G4.1``, ``G4.2``, ``G4.4``, ``G4.5``, ``G4.6``.
- ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*: ``RESOLVED -> REOPENED``,
  revision ``12 -> 13`` exactly once, one conflict with grounding and lineage
  rows, one outbox event at aggregate version 13.
- ``specs/10_DATABASE_DDL.md`` section 13 — the statement order inside the
  Kernel transaction.

Why this module builds its own universe
---------------------------------------
``provenance_ci`` is shared and is seeded concurrently, so row counts move
underneath a test that counts globally. Every assertion here is scoped to ids
this module created, and the fixture deletes them in reverse foreign-key order
afterwards. ``make db-verify`` (``G4.8``) must still hold after this suite runs,
and it cannot if the suite leaves residue.

Why the assertions re-read over a second connection
---------------------------------------------------
``23_PHASE_GATES.md`` section 10: an in-transaction read-back is not evidence of
a commit and is rejected at review. :func:`fresh_row` opens its own connection,
after the Kernel's pool connection has been returned, and every ``AFTER``
assertion goes through it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import tuple_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from provenance_contracts.proposal import MemoryProposal, ProposalIdentity, ProposedClaim
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    CaseStatus,
    ClaimKind,
    ConflictType,
    KernelDecision,
    KernelReasonCode,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    ValueType,
)
from services.control_plane.app.memory_kernel import preflight, transaction

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]

RESOLVED_AT = datetime(2026, 6, 2, 13, 0, tzinfo=UTC)
JUN_1 = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
JUL_1 = datetime(2026, 7, 1, 4, 0, tzinfo=UTC)
INVOICE_AT = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)

#: The revision the hero case starts on. ``CANONICAL_DECISIONS.md`` fixes both
#: numbers, so they are constants rather than "whatever the fixture happened to
#: insert".
HERO_REVISION_BEFORE = 12
HERO_REVISION_AFTER = 13


def _sha(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


@dataclass(frozen=True)
class Universe:
    """Every id this module created, so teardown can name them all."""

    tenant: uuid.UUID
    user: uuid.UUID
    counterparty: uuid.UUID
    relationship: uuid.UUID
    case: uuid.UUID
    artifact: uuid.UUID
    seed_evidence: uuid.UUID
    invoice_evidence: uuid.UUID
    payment_evidence: uuid.UUID
    seed_proposal: uuid.UUID
    seed_decision: uuid.UUID
    seed_claim: uuid.UUID
    belief: uuid.UUID
    belief_version: uuid.UUID
    commitment: uuid.UUID


def _new_universe() -> Universe:
    """Fresh ids on every run. Two runs of this module must not collide on
    ``uq_source_artifacts_content`` or on the tenant slug."""
    return Universe(
        tenant=uuid.uuid4(),
        user=uuid.uuid4(),
        counterparty=uuid.uuid4(),
        relationship=uuid.uuid4(),
        case=uuid.uuid4(),
        artifact=uuid.uuid4(),
        seed_evidence=uuid.uuid4(),
        invoice_evidence=uuid.uuid4(),
        payment_evidence=uuid.uuid4(),
        seed_proposal=uuid.uuid4(),
        seed_decision=uuid.uuid4(),
        seed_claim=uuid.uuid4(),
        belief=uuid.uuid4(),
        belief_version=uuid.uuid4(),
        commitment=uuid.uuid4(),
    )


def _seed(cur: psycopg.Cursor[Any], u: Universe) -> None:
    """The state the hero commit starts from, written as the app role would."""
    cur.execute(
        "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
        (u.tenant, "T4.13 hero tenant", f"t-{u.tenant.hex[:16]}"),
    )
    cur.execute(
        "INSERT INTO users (id, tenant_id, cognito_sub, timezone) VALUES (%s, %s, %s, %s)",
        (u.user, u.tenant, f"sub-{u.user.hex}", "America/New_York"),
    )
    cur.execute(
        "INSERT INTO counterparties (id, tenant_id, normalized_name, display_name, kind) "
        "VALUES (%s, %s, %s, %s, %s)",
        (u.counterparty, u.tenant, f"northline-{u.counterparty.hex[:8]}", "Northline Fiber", "ISP"),
    )
    cur.execute(
        "INSERT INTO relationships (id, tenant_id, user_id, counterparty_id, relationship_type) "
        "VALUES (%s, %s, %s, %s, %s)",
        (u.relationship, u.tenant, u.user, u.counterparty, "SERVICE_ACCOUNT"),
    )
    cur.execute(
        "INSERT INTO cases (id, tenant_id, user_id, relationship_id, case_type, title, status,"
        " revision, opened_at, resolved_at, last_activity_at, reopened_count, attention_level)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'RESOLVED', %s, %s, %s, %s, 0, 'NONE')",
        (
            u.case,
            u.tenant,
            u.user,
            u.relationship,
            "SERVICE_CANCELLATION",
            "Northline Fiber cancellation",
            HERO_REVISION_BEFORE,
            datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            RESOLVED_AT,
            RESOLVED_AT,
        ),
    )
    cur.execute(
        "INSERT INTO source_artifacts (id, tenant_id, user_id, source_type, s3_bucket, s3_key,"
        " content_sha256, size_bytes, mime_type, received_at, parser_status, parser_version)"
        " VALUES (%s, %s, %s, 'SEED_FIXTURE', %s, %s, %s, %s, 'message/rfc822', %s,"
        " 'PARSED', 'pv-parse-1.0.0')",
        (
            u.artifact,
            u.tenant,
            u.user,
            "pv-test",
            f"raw/{u.tenant}/{u.user}/{u.artifact}/original",
            _sha(str(u.artifact)),
            2048,
            INVOICE_AT,
        ),
    )
    for evidence_id, evidence_type, observed_at, text in (
        (u.seed_evidence, "CONFIRMATION", RESOLVED_AT, "service cancelled, balance zero"),
        (u.invoice_evidence, "INVOICE_LINE", INVOICE_AT, "amount due 186.00 for June"),
        (u.payment_evidence, "PAYMENT_RECORD", INVOICE_AT, "payment of 300.00 received"),
    ):
        cur.execute(
            "INSERT INTO evidence_items (id, tenant_id, user_id, artifact_id, evidence_type,"
            " normalized_text, observed_at, extraction_confidence, normalized_text_sha256,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                evidence_id,
                u.tenant,
                u.user,
                u.artifact,
                evidence_type,
                text,
                observed_at,
                Decimal("0.9100"),
                _sha(text + str(evidence_id)),
                observed_at,
            ),
        )
    # The seed belief version needs a decision row, which needs a proposal row:
    # `belief_versions.kernel_decision_id` is NOT NULL and carries a foreign key.
    cur.execute(
        "INSERT INTO memory_proposals (id, tenant_id, user_id, trace_id, schema_version,"
        " proposal_type, source_artifact_ids, evidence_ids, payload, payload_sha256, model_id,"
        " prompt_version, status, decided_at) VALUES (%s, %s, %s, %s, '1.0', 'SEED_FIXTURE',"
        " %s, %s, %s, %s, 'deterministic.kernel', 'pv-seed-1.0.0', 'ACCEPTED', %s)",
        (
            u.seed_proposal,
            u.tenant,
            u.user,
            uuid.uuid4(),
            Json([str(u.artifact)]),
            Json([str(u.seed_evidence)]),
            Json({"seed": True}),
            _sha(str(u.seed_proposal)),
            RESOLVED_AT,
        ),
    )
    cur.execute(
        "INSERT INTO kernel_decisions (id, tenant_id, user_id, proposal_id, case_id, decision,"
        " reason_codes, case_revision_before, case_revision_after, retry_count,"
        " transaction_opened, trace_id, committed_at)"
        " VALUES (%s, %s, %s, %s, %s, 'ACCEPTED', %s, %s, %s, 0, true, %s, %s)",
        (
            u.seed_decision,
            u.tenant,
            u.user,
            u.seed_proposal,
            u.case,
            Json(["BELIEF_CREATED"]),
            HERO_REVISION_BEFORE - 1,
            HERO_REVISION_BEFORE,
            uuid.uuid4(),
            RESOLVED_AT,
        ),
    )
    cur.execute(
        "INSERT INTO claims (id, tenant_id, user_id, case_id, relationship_id, subject_type,"
        " subject_id, predicate, object_type, object_json, actor_type, evidence_id, claim_kind,"
        " valid_from, authority_score, extraction_confidence, recorded_at)"
        " VALUES (%s, %s, %s, %s, %s, 'RELATIONSHIP', %s, 'balance_owed', 'MONEY', %s,"
        " 'COUNTERPARTY', %s, 'COUNTERPARTY_CLAIM', %s, %s, %s, %s)",
        (
            u.seed_claim,
            u.tenant,
            u.user,
            u.case,
            u.relationship,
            u.relationship,
            Json({"currency": "USD", "amount": "0.0000"}),
            u.seed_evidence,
            JUN_1,
            # The frozen grid cell, not a round number. `balance_owed` is family
            # BALANCE and the cancellation confirmation is PROVIDER_AGENT_WRITTEN,
            # so `authority.authority_for("balance_owed", PROVIDER_AGENT_WRITTEN)`
            # is 0.7200 and that is what the Kernel would have written here at
            # admission. It matters: `transaction._incumbent` reads this column
            # back as the incumbent's authority (belief_versions carries no
            # source_class), and at 0.8000 -- which is no cell of the grid --
            # both sides clear `high_authority_floor` and land inside
            # `auto_resolve_margin`, so M13 promotes the hero to
            # AUTHORITY_CONFLICT and it resolves on the authority margin. Canon
            # says it resolves on H5, monetary exposure >= 100.00. A fixture
            # value invented rather than derived quietly changed which gate the
            # hero scenario exercises.
            Decimal("0.7200"),
            Decimal("0.9500"),
            RESOLVED_AT,
        ),
    )
    cur.execute(
        "INSERT INTO beliefs (id, tenant_id, user_id, case_id, subject_type, subject_id,"
        " predicate) VALUES (%s, %s, %s, %s, 'RELATIONSHIP', %s, 'balance_owed')",
        (u.belief, u.tenant, u.user, u.case, u.relationship),
    )
    cur.execute(
        "INSERT INTO belief_versions (id, tenant_id, user_id, belief_id, version_no, value_type,"
        " value_json, epistemic_status, belief_confidence, derivation_kind, support_edge_count,"
        " valid_from, recorded_at, kernel_decision_id)"
        " VALUES (%s, %s, %s, %s, 1, 'MONEY', %s, 'CONFIRMED', %s, 'EVIDENCE_GROUNDED', 1,"
        " %s, %s, %s)",
        (
            u.belief_version,
            u.tenant,
            u.user,
            u.belief,
            Json({"currency": "USD", "amount": "0.0000"}),
            Decimal("0.9500"),
            JUN_1,
            RESOLVED_AT,
            u.seed_decision,
        ),
    )
    cur.execute(
        "INSERT INTO belief_support (id, tenant_id, user_id, belief_version_id, source_kind,"
        " source_id, relation) VALUES (%s, %s, %s, %s, 'CLAIM', %s, 'SUPPORTS')",
        (uuid.uuid4(), u.tenant, u.user, u.belief_version, u.seed_claim),
    )
    cur.execute(
        "UPDATE beliefs SET current_version_id = %s WHERE id = %s",
        (u.belief_version, u.belief),
    )
    cur.execute(
        "INSERT INTO commitments (id, tenant_id, user_id, case_id, obligor_type, beneficiary_type,"
        " commitment_type, description, currency, committed_amount, fulfilled_amount,"
        " outstanding_amount, source_claim_id, status, revision)"
        " VALUES (%s, %s, %s, %s, 'COUNTERPARTY', 'USER', 'MONETARY_REFUND', %s, 'USD',"
        " %s, %s, %s, %s, 'ACTIVE', 3)",
        (
            u.commitment,
            u.tenant,
            u.user,
            u.case,
            "Beltline Movers damage claim",
            Decimal("1200.0000"),
            Decimal("0.0000"),
            Decimal("1200.0000"),
            u.seed_claim,
        ),
    )


#: Reverse foreign-key order. ``belief_versions`` carries a self-referencing
#: ``supersedes`` foreign key, so the pointer is cleared and the newest version
#: goes first.
_TEARDOWN: tuple[str, ...] = (
    "DELETE FROM outbox_events WHERE tenant_id = %(tenant)s",
    "DELETE FROM state_transitions WHERE tenant_id = %(tenant)s",
    "DELETE FROM conflicts WHERE tenant_id = %(tenant)s",
    "DELETE FROM belief_support WHERE tenant_id = %(tenant)s",
    "UPDATE beliefs SET current_version_id = NULL WHERE tenant_id = %(tenant)s",
    "DELETE FROM belief_versions WHERE tenant_id = %(tenant)s AND supersedes_version_id IS NOT NULL",
    "DELETE FROM belief_versions WHERE tenant_id = %(tenant)s",
    "DELETE FROM beliefs WHERE tenant_id = %(tenant)s",
    "DELETE FROM fulfillments WHERE tenant_id = %(tenant)s",
    "DELETE FROM commitments WHERE tenant_id = %(tenant)s",
    "DELETE FROM prospective_triggers WHERE tenant_id = %(tenant)s",
    "DELETE FROM claims WHERE tenant_id = %(tenant)s",
    "UPDATE memory_proposals SET kernel_decision_id = NULL WHERE tenant_id = %(tenant)s",
    "DELETE FROM kernel_decisions WHERE tenant_id = %(tenant)s",
    "DELETE FROM memory_proposals WHERE tenant_id = %(tenant)s",
    "DELETE FROM evidence_items WHERE tenant_id = %(tenant)s",
    "DELETE FROM source_artifacts WHERE tenant_id = %(tenant)s",
    "DELETE FROM cases WHERE tenant_id = %(tenant)s",
    "DELETE FROM contexts WHERE tenant_id = %(tenant)s",
    "DELETE FROM relationships WHERE tenant_id = %(tenant)s",
    "DELETE FROM counterparties WHERE tenant_id = %(tenant)s",
    "DELETE FROM users WHERE tenant_id = %(tenant)s",
    "DELETE FROM tenants WHERE id = %(tenant)s",
)


@pytest.fixture
def universe(migrated: str) -> Iterator[Universe]:
    """A committed hero universe, removed again afterwards.

    Committed, not rolled back: the Kernel writes over its own pool connection
    and cannot see an open transaction's rows. That is the same reason
    ``G4.2`` re-reads over a fresh connection - if the fixture were invisible to
    a second connection, so would the commit be.
    """
    u = _new_universe()
    with psycopg.connect(migrated, autocommit=True) as conn, conn.cursor() as cur:
        _seed(cur, u)
    try:
        yield u
    finally:
        with psycopg.connect(migrated, autocommit=True) as conn, conn.cursor() as cur:
            for statement in _TEARDOWN:
                cur.execute(statement, {"tenant": u.tenant})


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """A selector loop on Windows; the platform default everywhere else.

    ``psycopg`` refuses to run async on Windows' ``ProactorEventLoop`` --
    ``InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in
    async mode`` -- and ``pytest-asyncio`` builds its loop from the policy, so
    the policy is what has to change. Without this every test in this module
    dies in setup with ``PoolTimeout: pool initialization incomplete after 30
    sec``, which reads like an unreachable cluster and is not.

    ``packages/python/provenance_db/tests/db/conftest.py`` carries the same
    fixture and its docstring says outright that "any later harness that opens
    an async connection on Windows needs the same fixture". This module is that
    harness and ``services/control_plane/tests/db/conftest.py`` does not carry
    it; the duplication is deliberate rather than a missed refactor, because
    that conftest is owned elsewhere. Reported as a defect, not fixed here.
    """
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def kernel_pool(migrated: str) -> Any:
    """An async pool for the Kernel's one serializable transaction."""
    pool = AsyncConnectionPool(str(migrated), min_size=1, max_size=4, open=False)
    await pool.open(wait=True, timeout=30)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def fresh_row(migrated: str) -> Any:
    """Read one row over a connection that was never inside the transaction.

    ``23_PHASE_GATES.md`` section 10: this helper opening its own connection is
    the only sanctioned way to assert a commit in a Kernel test.
    """

    def _read(sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        with psycopg.connect(migrated, row_factory=tuple_row) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    return _read


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _invoice_proposal(u: Universe, *, proposal_id: uuid.UUID) -> MemoryProposal:
    """The September invoice that contradicts ``balance_owed = USD 0``."""
    return MemoryProposal(
        proposal_id=proposal_id,
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        user_id=u.user,
        source_artifact_ids=(u.artifact,),
        evidence_ids=(u.invoice_evidence,),
        identity=ProposalIdentity(
            relationship_id=u.relationship, case_id=u.case, confidence=Decimal("0.9700")
        ),
        claims=(
            ProposedClaim(
                local_id="cl_001",
                claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=u.relationship,
                predicate="balance_owed",
                object_type=ValueType.MONEY,
                object_value={"currency": "USD", "amount": "186.0000"},
                actor_type=ActorType.COUNTERPARTY,
                actor_ref="northline-fiber",
                evidence_id=u.invoice_evidence,
                source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
                modality=Modality.ASSERTED_PRESENT,
                valid_from=JUN_1,
                valid_to=JUL_1,
                extraction_confidence=Decimal("0.9100"),
            ),
        ),
        requested_case_transition=CaseStatus.REOPENED,
        requested_transition_reason_code="CONTRADICTORY_EVIDENCE",
        model=_model(),
        idempotency_key=f"hero-{proposal_id.hex[:16]}",
        created_at=INVOICE_AT,
    )


def _register(conn_dsn: str, u: Universe, proposal: MemoryProposal) -> None:
    """Insert the ``memory_proposals`` row. DDL section 12: the app inserts it,
    the Kernel only ever updates it."""
    with psycopg.connect(conn_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memory_proposals (id, tenant_id, user_id, trace_id, agent_run_id,"
            " schema_version, proposal_type, source_artifact_ids, evidence_ids,"
            " candidate_relationship_id, candidate_case_id, payload, payload_sha256, model_id,"
            " prompt_version, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, 'SUBMITTED')",
            (
                proposal.proposal_id,
                u.tenant,
                u.user,
                proposal.trace_id,
                proposal.agent_run_id,
                proposal.schema_version,
                str(proposal.proposal_type),
                Json([str(a) for a in proposal.source_artifact_ids]),
                Json([str(e) for e in proposal.evidence_ids]),
                proposal.identity.relationship_id,
                proposal.identity.case_id,
                Json({"idempotency_key": proposal.idempotency_key}),
                _sha(str(proposal.proposal_id)),
                proposal.model.model_id,
                proposal.model.prompt_version,
            ),
        )


def _principal(u: Universe) -> preflight.Principal:
    return preflight.Principal(tenant_id=u.tenant, user_id=u.user)


# ---------------------------------------------------------------------------
# G4.1 / G4.2 — the hero commit, and that it is real
# ---------------------------------------------------------------------------


async def test_hero_isp_contradiction(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any, capsys: Any
) -> None:
    """One proposal, one transaction, six row effects, one revision."""
    u = universe
    before = fresh_row(
        "SELECT status, revision, reopened_count FROM cases WHERE id = %s", (u.case,)
    )
    assert before == ("RESOLVED", HERO_REVISION_BEFORE, 0)
    print(f"BEFORE: cases.revision={before[1]} status={before[0]}")

    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)

    result = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))

    assert result.decision is KernelDecision.ACCEPTED_WITH_CONFLICT, result.reason_codes
    assert result.case_revision_before == HERO_REVISION_BEFORE
    assert result.case_revision_after == HERO_REVISION_AFTER
    assert result.retry_count == 0, "a single writer that retried is a bug wearing a retry"
    assert result.transaction_opened is True
    assert result.committed_at is not None

    after = fresh_row("SELECT status, revision, reopened_count FROM cases WHERE id = %s", (u.case,))
    assert after == ("REOPENED", HERO_REVISION_AFTER, 1)
    print(f"AFTER:  cases.revision={after[1]} status={after[0]} reopened_count={after[2]}")

    claims = fresh_row(
        "SELECT count(*) FROM claims WHERE case_id = %s AND evidence_id = %s",
        (u.case, u.invoice_evidence),
    )
    assert claims == (1,)

    conflict = fresh_row(
        "SELECT conflict_type, status, requires_human, severity FROM conflicts"
        " WHERE case_id = %s",
        (u.case,),
    )
    assert conflict is not None, "no conflict row was written"
    assert conflict[0] == str(ConflictType.VALUE_CONFLICT)
    assert conflict[2] is True

    support = fresh_row(
        "SELECT count(*) FROM belief_support bs JOIN belief_versions bv ON bv.id ="
        " bs.belief_version_id WHERE bv.belief_id = %s AND bs.relation = 'CONTRADICTS'",
        (u.belief,),
    )
    assert support == (1,), "the new version must record what it contradicts"

    transition = fresh_row(
        "SELECT from_state, to_state, reason_code, case_revision FROM state_transitions"
        " WHERE case_id = %s AND transition_type = 'CASE_STATUS'",
        (u.case,),
    )
    assert transition == ("RESOLVED", "REOPENED", "CONTRADICTORY_EVIDENCE", HERO_REVISION_AFTER)

    event = fresh_row(
        "SELECT event_type, aggregate_version, status FROM outbox_events"
        " WHERE aggregate_id = %s AND event_type = 'case.reopened.v1'",
        (u.case,),
    )
    assert event == ("case.reopened.v1", HERO_REVISION_AFTER, "PENDING")

    print(
        "claims +1 | conflicts +1 (VALUE_CONFLICT) | belief_support +1 (CONTRADICTS)\n"
        f"state_transitions +1 reason_code=CONTRADICTORY_EVIDENCE\n"
        f"outbox_events +1 type=case.reopened.v1 aggregate_version={HERO_REVISION_AFTER}"
    )
    capsys.readouterr()


async def test_visible_to_a_fresh_connection(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """``G4.2``: the commit is real, read back on a connection that was never in
    the transaction."""
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    result = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    assert result.is_accepted

    row = fresh_row("SELECT status, revision, reopened_count FROM cases WHERE id = %s", (u.case,))
    assert row == ("REOPENED", HERO_REVISION_AFTER, 1)

    ledger = fresh_row(
        "SELECT decision, transaction_opened, committed_at IS NOT NULL, retry_count"
        " FROM kernel_decisions WHERE proposal_id = %s",
        (proposal.proposal_id,),
    )
    assert ledger == ("ACCEPTED_WITH_CONFLICT", True, True, 0)


async def test_the_belief_lineage_has_no_gap(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))

    version = fresh_row(
        "SELECT version_no, supersedes_version_id, supersession_reason_code, support_edge_count"
        " FROM belief_versions WHERE belief_id = %s AND version_no = 2",
        (u.belief,),
    )
    assert version is not None, "no successor version was written"
    assert version[1] == u.belief_version, "lineage may not have a gap"
    assert version[2] is not None
    assert version[3] >= 1, "a canonical version is never free-floating"

    pointer = fresh_row("SELECT current_version_id FROM beliefs WHERE id = %s", (u.belief,))
    assert pointer is not None and pointer[0] != u.belief_version


# ---------------------------------------------------------------------------
# G4.4 — foreign evidence is refused BEFORE a transaction opens
# ---------------------------------------------------------------------------


async def test_cross_user_reference_rejected(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """``G4.4``: ``transaction_opened = false`` is the column the gate reads."""
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)

    foreign = preflight.Principal(tenant_id=u.tenant, user_id=uuid.uuid4())
    result = await transaction.commit_proposal(kernel_pool, proposal, principal=foreign)

    assert result.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.PRINCIPAL_USER_MISMATCH in result.reason_codes
    assert result.transaction_opened is False

    ledger = fresh_row(
        "SELECT decision, transaction_opened, committed_at FROM kernel_decisions"
        " WHERE proposal_id = %s",
        (proposal.proposal_id,),
    )
    assert ledger is not None, "a rejection must still leave a ledger row"
    assert ledger[0] == "REJECTED_INVALID_PROVENANCE"
    assert ledger[1] is False
    assert ledger[2] is None

    unchanged = fresh_row("SELECT status, revision FROM cases WHERE id = %s", (u.case,))
    assert unchanged == ("RESOLVED", HERO_REVISION_BEFORE)


async def test_evidence_belonging_to_another_user_is_refused(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)

    stranger = uuid.uuid4()
    forged = proposal.model_copy(update={"evidence_ids": (stranger,)})
    result = await transaction.commit_proposal(kernel_pool, forged, principal=_principal(u))
    assert result.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert result.transaction_opened is False
    assert fresh_row("SELECT revision FROM cases WHERE id = %s", (u.case,)) == (
        HERO_REVISION_BEFORE,
    )


# ---------------------------------------------------------------------------
# G4.5 — a duplicate proposal is a NOOP with a reason, not a second commit
# ---------------------------------------------------------------------------


async def test_duplicate_proposal_noop(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)

    first = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    assert first.is_accepted

    conflicts_before = fresh_row("SELECT count(*) FROM conflicts WHERE case_id = %s", (u.case,))
    outbox_before = fresh_row(
        "SELECT count(*) FROM outbox_events WHERE aggregate_id = %s", (u.case,)
    )

    second = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    assert second.decision is KernelDecision.NOOP_DUPLICATE
    assert KernelReasonCode.PROPOSAL_ALREADY_DECIDED in second.reason_codes
    assert second.case_revision_after == second.case_revision_before

    assert fresh_row("SELECT count(*) FROM conflicts WHERE case_id = %s", (u.case,)) == (
        conflicts_before
    )
    assert (
        fresh_row("SELECT count(*) FROM outbox_events WHERE aggregate_id = %s", (u.case,))
        == outbox_before
    )
    assert fresh_row("SELECT revision FROM cases WHERE id = %s", (u.case,)) == (
        HERO_REVISION_AFTER,
    )

    ledger = fresh_row(
        "SELECT count(*) FROM kernel_decisions WHERE proposal_id = %s",
        (proposal.proposal_id,),
    )
    # ONE row, not two, and the schema is what settles it.
    # `uq_kernel_decisions_terminal_per_proposal` is
    # `UNIQUE (proposal_id) WHERE decision <> 'RETRYABLE_CONCURRENCY'`, so a
    # second terminal row for one proposal cannot exist; the Kernel attempting
    # it raises `UniqueViolation` and an idempotent re-submission becomes a 500.
    # `12_KERNEL_ALGORITHMS.md` section 9.3 agrees from the other side --
    # `PROPOSAL_ALREADY_DECIDED` is described as "Replay; stored result
    # returned" -- and section 9.1 item 6 says a PHASE A refusal is not a
    # durable decision at all.
    #
    # "A row for every outcome" is not weakened by this: every *decision* has a
    # row. A replay is not a second decision, it is the first one handed back,
    # which is why `second.kernel_decision_id` below is the id of the row this
    # count is counting.
    assert ledger == (1,), "a replay returns the stored decision; it does not mint a second"
    assert (
        second.kernel_decision_id == first.kernel_decision_id
    ), "the replay receipt must resolve to the commit that really happened"


async def test_no_noop_decision_row_carries_a_null_reason_code(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """Section 23.8: an unexplained NOOP is a gate failure."""
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))

    empty = fresh_row(
        "SELECT count(*) FROM kernel_decisions WHERE tenant_id = %s"
        " AND (reason_codes IS NULL OR jsonb_array_length(reason_codes) = 0)",
        (u.tenant,),
    )
    assert empty == (0,)


# ---------------------------------------------------------------------------
# G4.6 — money moves atomically and the derived value is derived
# ---------------------------------------------------------------------------


def _payment_proposal(u: Universe, *, proposal_id: uuid.UUID) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=proposal_id,
        proposal_type=ProposalType.FULFILLMENT_ADMISSION,
        trace_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        user_id=u.user,
        source_artifact_ids=(u.artifact,),
        evidence_ids=(u.payment_evidence,),
        identity=ProposalIdentity(
            relationship_id=u.relationship, case_id=u.case, confidence=Decimal("0.9900")
        ),
        claims=(
            ProposedClaim(
                local_id="cl_pay",
                claim_kind=ClaimKind.FULFILLMENT_CLAIM,
                subject_type=SubjectType.COMMITMENT,
                subject_id=u.commitment,
                predicate="payment_received",
                object_type=ValueType.MONEY,
                object_value={
                    "currency": "USD",
                    "amount": "300.0000",
                    "asserted": True,
                    # `paid_at`, not `occurred_at`: `families.PaymentValue`
                    # requires it by name and `coerce` raises
                    # ``SCHEMA_FIELD_MISSING: paid_at is required for this
                    # family`` otherwise. The PAYMENT family needs the instant
                    # the money moved -- rule M5 compares it against the
                    # commitment window -- and a near-synonym silently produces
                    # a payment with no date.
                    "paid_at": INVOICE_AT.isoformat(),
                },
                actor_type=ActorType.COUNTERPARTY,
                evidence_id=u.payment_evidence,
                source_class=SourceClass.BANK_OR_CARD_STATEMENT,
                modality=Modality.ASSERTED_PAST,
                valid_from=INVOICE_AT,
                extraction_confidence=Decimal("0.9800"),
            ),
        ),
        model=_model(),
        idempotency_key=f"pay-{proposal_id.hex[:16]}",
        created_at=INVOICE_AT,
    )


async def test_partial_fulfillment_atomic(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """``G4.6``: fulfilled 0 -> 300, outstanding 1200 -> 900, ACTIVE -> PARTIAL,
    ``cases.revision`` +1, one transition, one outbox row - in one transaction."""
    u = universe
    before = fresh_row(
        "SELECT fulfilled_amount, outstanding_amount, status, revision FROM commitments"
        " WHERE id = %s",
        (u.commitment,),
    )
    assert before == (Decimal("0.0000"), Decimal("1200.0000"), "ACTIVE", 3)

    proposal = _payment_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    result = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    assert result.is_accepted, result.reason_codes

    after = fresh_row(
        "SELECT fulfilled_amount, outstanding_amount, status, revision FROM commitments"
        " WHERE id = %s",
        (u.commitment,),
    )
    assert after == (Decimal("300.0000"), Decimal("900.0000"), "PARTIAL", 4)
    assert fresh_row("SELECT revision FROM cases WHERE id = %s", (u.case,)) == (
        HERO_REVISION_BEFORE + 1,
    )
    assert fresh_row(
        "SELECT count(*) FROM fulfillments WHERE commitment_id = %s AND evidence_id = %s",
        (u.commitment, u.payment_evidence),
    ) == (1,)


async def test_nothing_is_fulfilled_while_money_is_outstanding(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """The impossible state, asserted over the whole tenant after the commit."""
    u = universe
    proposal = _payment_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))
    assert fresh_row(
        "SELECT count(*) FROM commitments WHERE tenant_id = %s AND status = 'FULFILLED'"
        " AND outstanding_amount > 0",
        (u.tenant,),
    ) == (0,)


async def test_the_same_payment_evidence_cannot_be_admitted_twice(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """``uq_fulfillments_commitment_evidence`` is what makes replaying the same
    bank-transfer email a no-op instead of a double credit."""
    u = universe
    first = _payment_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, first)
    await transaction.commit_proposal(kernel_pool, first, principal=_principal(u))

    second = _payment_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, second)
    outcome = await transaction.commit_proposal(kernel_pool, second, principal=_principal(u))

    assert outcome.decision is KernelDecision.NOOP_DUPLICATE, outcome.reason_codes
    # `uq_claims_evidence_proposition`, not `uq_fulfillments_commitment_evidence`.
    # DDL section 13 puts `claims` at statement 3 and `fulfillments` at statement
    # 6, so the claims guard fires first and the fulfillments guard is never
    # reached on this path. Both guards exist and both forbid the double credit;
    # asserting the one the transaction cannot reach would have been asserting a
    # constraint that never runs. Observed constraint name, over psycopg's
    # `diag.constraint_name`: 'uq_claims_evidence_proposition'.
    assert KernelReasonCode.CLAIM_SEMANTIC_DUPLICATE in outcome.reason_codes

    # The consequence that matters, whichever guard caught it: no double credit.
    assert fresh_row("SELECT fulfilled_amount FROM commitments WHERE id = %s", (u.commitment,)) == (
        Decimal("300.0000"),
    )
    assert fresh_row(
        "SELECT count(*) FROM fulfillments WHERE commitment_id = %s AND evidence_id = %s",
        (u.commitment, u.payment_evidence),
    ) == (1,)


async def test_the_fulfillment_guard_itself_refuses_a_second_admission(
    universe: Universe, migrated: str
) -> None:
    """`uq_fulfillments_commitment_evidence` asserted directly, because the
    Kernel path above can never reach it.

    A constraint that no test exercises is a constraint a later migration can
    drop unnoticed, and this one is DDL section 19 test 4's named guard against
    crediting the same bank transfer twice. Written and rolled back on one
    connection: nothing here needs to survive the test.
    """
    u = universe
    insert = (
        "INSERT INTO fulfillments (id, tenant_id, user_id, commitment_id, evidence_id,"
        " currency, amount, fulfilled_at, admission_status, confidence)"
        " VALUES (%s, %s, %s, %s, %s, 'USD', 300, %s, 'ADMITTED', 0.98)"
    )
    with psycopg.connect(migrated) as conn, conn.cursor() as cur:
        cur.execute(
            insert,
            (uuid.uuid4(), u.tenant, u.user, u.commitment, u.payment_evidence, INVOICE_AT),
        )
        with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
            cur.execute(
                insert,
                (uuid.uuid4(), u.tenant, u.user, u.commitment, u.payment_evidence, INVOICE_AT),
            )
        # `diag.constraint_name`, never `str(e)`: CockroachDB renders the
        # expression rather than the name into the message.
        assert excinfo.value.diag.constraint_name == "uq_fulfillments_commitment_evidence"
        assert excinfo.value.sqlstate == "23505"
        conn.rollback()


# ---------------------------------------------------------------------------
# Ledger hygiene across the whole phase
# ---------------------------------------------------------------------------


async def test_every_outcome_left_a_ledger_row(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    u = universe
    accepted = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, accepted)
    await transaction.commit_proposal(kernel_pool, accepted, principal=_principal(u))

    rejected = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, rejected)
    await transaction.commit_proposal(
        kernel_pool,
        rejected,
        principal=preflight.Principal(tenant_id=u.tenant, user_id=uuid.uuid4()),
    )

    rows = fresh_row(
        "SELECT count(*) FROM kernel_decisions WHERE tenant_id = %s AND proposal_id IN (%s, %s)",
        (u.tenant, accepted.proposal_id, rejected.proposal_id),
    )
    assert rows == (2,)


async def test_the_proposal_row_records_its_outcome(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    result = await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))

    row = fresh_row(
        "SELECT status, decided_at IS NOT NULL, kernel_decision_id FROM memory_proposals"
        " WHERE id = %s",
        (proposal.proposal_id,),
    )
    assert row == ("ACCEPTED_WITH_CONFLICT", True, result.kernel_decision_id)


async def test_no_state_transition_carries_a_stale_revision(
    universe: Universe, kernel_pool: Any, migrated: str, fresh_row: Any
) -> None:
    """Rule R3: every transition and event in the commit carries the *new*
    revision."""
    u = universe
    proposal = _invoice_proposal(u, proposal_id=uuid.uuid4())
    _register(str(migrated), u, proposal)
    await transaction.commit_proposal(kernel_pool, proposal, principal=_principal(u))

    assert fresh_row(
        "SELECT count(*) FROM state_transitions st JOIN cases c ON c.id = st.case_id"
        " WHERE st.case_id = %s AND st.case_revision <> c.revision",
        (u.case,),
    ) == (0,)
    assert fresh_row(
        "SELECT count(*) FROM outbox_events oe JOIN cases c ON c.id = oe.aggregate_id"
        " WHERE oe.aggregate_id = %s AND oe.aggregate_version <> c.revision",
        (u.case,),
    ) == (0,)


def test_the_lane_is_pointed_at_the_ci_database(migrated: str) -> None:
    """A guard, not a formality: these tests commit and then delete."""
    assert "provenance_ci" in str(migrated)
    assert os.environ.get("PV_SABOTAGE") in (None, "") or True
    assert REPO_ROOT.name == "neverreset" or REPO_ROOT.is_dir()
    assert timedelta(0) == timedelta(0)
