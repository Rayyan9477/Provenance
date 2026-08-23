"""T4.10 - the shape of the one canonical transaction, asserted hermetically.

Authority: ``specs/10_DATABASE_DDL.md`` section 13 (**the statement order IS the
specification**) and ``specs/12_KERNEL_ALGORITHMS.md`` section 7.

What this module can prove without a database
---------------------------------------------
Three things, and they are the three that go wrong silently:

1. **Order.** Foreign keys are validated at statement time, so the decision row
   must precede the rows whose NOT NULL foreign keys reference it, and the case
   UPDATE must precede the outbox row whose ``aggregate_version`` is the
   post-increment revision. Writing the outbox first produces a
   plausible-looking row with the wrong version.
2. **No second retry loop.** ``provenance_db.retry.run_in_serializable_tx`` is
   the one 40001 loop in the repository, proven against a real two-connection
   interleaving. A second one here would have its own untested backoff.
3. **No side effect after the cap.** Retry exhaustion returns
   ``RETRYABLE_CONCURRENCY`` with ``RETRY_EXHAUSTED_NOT_ENQUEUED`` and enqueues
   nothing, because no kernel retry queue exists and the control plane holds no
   queue-publish permission.

The commit itself is asserted against a live cluster in
``services/control_plane/tests/db/test_kernel_hero.py`` over a **second
connection opened after the transaction closed**. An in-transaction read-back is
not evidence of a commit.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from provenance_db.retry import UNIQUE_VIOLATION_MAP
from provenance_domain.enums import (
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EventType,
    KernelDecision,
    KernelReasonCode,
    SubjectType,
    TransitionType,
    TriggerType,
)
from services.control_plane.app.memory_kernel import case_ops, decisions, pipeline, transaction
from tools import txn_purity_lint

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
CASE = uuid.UUID(int=0x2001)
PROPOSAL = uuid.UUID(int=0x9001)
TRACE = uuid.UUID(int=0x9002)
DECISION_ID = uuid.UUID(int=0x9003)
EVIDENCE = uuid.UUID(int=0x6101)
BELIEF = uuid.UUID(int=0x5101)
TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return [self._row] if self._row is not None else []


class RecordingConnection:
    """Records every statement in the order it was executed."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[Any] = []

    async def execute(self, query: str, params: Any = None) -> FakeCursor:
        self.statements.append(" ".join(query.split()))
        self.params.append(params)
        return FakeCursor((1,))


def _plan() -> pipeline.WritePlan:
    """The hero's write plan, hand-built so the ordering test owns its input."""
    support = pipeline.SupportEdgeWrite(
        edge_id=uuid.UUID(int=0x5201),
        belief_version_id=uuid.UUID(int=0x5301),
        source_kind="CLAIM",
        source_id=uuid.UUID(int=0x4101),
        relation="CONTRADICTS",
    )
    version = pipeline.BeliefVersionWrite(
        version_id=uuid.UUID(int=0x5301),
        belief_id=BELIEF,
        version_no=2,
        value_type="MONEY",
        value_json={"amount": "186.0000", "currency": "USD"},
        epistemic_status=EpistemicStatus.DISPUTED,
        belief_confidence=Decimal("0.6000"),
        supersedes_version_id=uuid.UUID(int=0x5001),
        supersession_reason_code="BELIEF_MARKED_DISPUTED",
        support=(support,),
    )
    return pipeline.WritePlan(
        claims=(
            pipeline.ClaimWrite(
                claim_id=uuid.UUID(int=0x4101),
                case_id=CASE,
                relationship_id=uuid.UUID(int=0x1001),
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=uuid.UUID(int=0x1001),
                predicate="outstanding_balance",
                object_type="MONEY",
                object_json={"amount": "186.0000", "currency": "USD"},
                actor_type="COUNTERPARTY",
                actor_id=None,
                evidence_id=EVIDENCE,
                claim_kind="COUNTERPARTY_CLAIM",
                extraction_confidence=Decimal("0.9000"),
            ),
        ),
        beliefs=(
            pipeline.BeliefWrite(
                belief_id=BELIEF,
                case_id=CASE,
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=uuid.UUID(int=0x1001),
                predicate="outstanding_balance",
                exists=True,
            ),
        ),
        belief_versions=(version,),
        supersedes=(pipeline.SupersedeWrite(version_id=uuid.UUID(int=0x5001)),),
        commitments=(
            pipeline.CommitmentRowWrite(
                commitment_id=uuid.UUID(int=0x3101),
                case_id=CASE,
                obligor_type="COUNTERPARTY",
                obligor_id="harborview-property-management",
                beneficiary_type="USER",
                beneficiary_id=str(USER),
                commitment_type="DEPOSIT_RETURN",
                description="Return of the USD 1,800.00 security deposit.",
                source_claim_id=uuid.UUID(int=0x4101),
                status=CommitmentStatus.ACTIVE,
                currency="USD",
                committed_amount=Decimal("1800.0000"),
                fulfilled_amount=Decimal("0.0000"),
                outstanding_amount=Decimal("1800.0000"),
                due_at=datetime(2026, 6, 15, tzinfo=UTC),
            ),
        ),
        trigger_arms=(
            pipeline.TriggerArmWrite(
                trigger_id=uuid.UUID(int=0xC101),
                case_id=CASE,
                trigger_type=TriggerType.COMMITMENT_DEADLINE,
                predicate_ast={"op": "CONST", "value": True},
                basis_case_revision=13,
                not_before=datetime(2026, 6, 15, 0, 1, tzinfo=UTC),
            ),
        ),
        trigger_disarms=(
            pipeline.TriggerDisarmWrite(
                trigger_id=uuid.UUID(int=0xC102),
                last_reason_code="COMMITMENT_SATISFIED",
            ),
        ),
        conflicts=(
            pipeline.ConflictRowWrite(
                conflict_id=uuid.UUID(int=0x2201),
                case_id=CASE,
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=uuid.UUID(int=0x1001),
                predicate="outstanding_balance",
                left_source_kind="BELIEF_VERSION",
                left_source_id=uuid.UUID(int=0x5001),
                right_source_kind="CLAIM",
                right_source_id=uuid.UUID(int=0x5301),
                conflict_type=ConflictType.VALUE_CONFLICT,
                status=ConflictStatus.NEEDS_HUMAN,
                severity=ConflictSeverity.HIGH,
                requires_human=True,
            ),
        ),
        case_update=case_ops.CaseUpdate(
            case_id=CASE,
            status_before=CaseStatus.RESOLVED,
            status_after=CaseStatus.REOPENED,
            revision_before=12,
            revision_after=13,
            reopen_delta=1,
            attention_after=AttentionLevel.URGENT,
            reason_code="CONTRADICTORY_EVIDENCE",
            changed=True,
        ),
        transitions=(
            pipeline.StateTransitionWrite(
                transition_id=uuid.UUID(int=0xA101),
                case_id=CASE,
                case_revision=13,
                transition_type=TransitionType.CASE_STATUS,
                subject_kind="CASE",
                subject_id=CASE,
                from_state="RESOLVED",
                to_state="REOPENED",
                reason_code="CONTRADICTORY_EVIDENCE",
            ),
        ),
        outbox=(
            pipeline.OutboxWrite(
                event_id=uuid.UUID(int=0xB101),
                aggregate_type="CASE",
                aggregate_id=CASE,
                aggregate_version=13,
                event_type=EventType.CASE_REOPENED,
                payload={"case_id": str(CASE)},
            ),
        ),
    )


def _row() -> decisions.DecisionRow:
    return decisions.build_decision_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        decision=KernelDecision.ACCEPTED_WITH_CONFLICT,
        reason_codes=(KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
        case_id=CASE,
        case_revision_before=12,
        case_revision_after=13,
        tx_now=TX_NOW,
    )


# ---------------------------------------------------------------------------
# The order IS the specification
# ---------------------------------------------------------------------------


def test_the_declared_order_is_ddl_section_13() -> None:
    assert transaction.STATEMENT_ORDER == (
        "read_case",
        "kernel_decisions",
        "claims",
        "belief_versions",
        "belief_support",
        "beliefs_pointer",
        "belief_versions_supersede",
        "conflicts",
        "commitments_insert",
        "fulfillments",
        "commitments",
        "cases",
        "state_transitions",
        "prospective_triggers",
        "memory_proposals",
        "outbox_events",
    )


@pytest.mark.asyncio
async def test_the_plan_is_executed_in_the_declared_order() -> None:
    conn = RecordingConnection()
    executed = await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    # Positions, not set membership: one plan can issue a label more than once
    # -- two claims, a trigger armed and another disarmed -- and an equality
    # against a de-duplicated list would silently stop checking the moment it
    # did. What section 13 fixes is that the positions never go backwards.
    positions = [transaction.STATEMENT_ORDER.index(label) for label in executed]
    assert positions == sorted(positions), f"executed out of DDL section 13 order: {executed}"
    assert len(set(executed)) > 1, "the fixture must exercise more than one statement"


@pytest.mark.asyncio
async def test_a_repeated_label_is_still_checked_for_order() -> None:
    """The ordering assertion above has to survive a plan that issues one label
    twice; before ``prospective_triggers`` carried both an arm and a disarm, no
    fixture did, and the check was an equality that could not have failed."""
    conn = RecordingConnection()
    executed = await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    assert executed.count("prospective_triggers") == 2
    positions = [transaction.STATEMENT_ORDER.index(label) for label in executed]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_the_decision_row_precedes_every_row_that_references_it() -> None:
    """``belief_versions.kernel_decision_id`` and
    ``state_transitions.kernel_decision_id`` are NOT NULL foreign keys."""
    conn = RecordingConnection()
    executed = await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    order = list(executed)
    assert order.index("kernel_decisions") < order.index("belief_versions")
    assert order.index("kernel_decisions") < order.index("state_transitions")


@pytest.mark.asyncio
async def test_the_case_update_precedes_the_outbox_row() -> None:
    """The order is what makes ``aggregate_version`` equal the post-increment
    revision."""
    conn = RecordingConnection()
    executed = await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    order = list(executed)
    assert order.index("cases") < order.index("outbox_events")
    assert order.index("cases") < order.index("state_transitions")


@pytest.mark.asyncio
async def test_the_outbox_row_carries_the_post_increment_revision() -> None:
    conn = RecordingConnection()
    await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    outbox_params = [
        params
        for statement, params in zip(conn.statements, conn.params, strict=True)
        if statement.startswith("INSERT INTO outbox_events")
    ]
    assert outbox_params, "no outbox row was written inside the transaction"
    assert outbox_params[0]["aggregate_version"] == 13


@pytest.mark.asyncio
async def test_the_case_update_carries_the_optimistic_predicate() -> None:
    conn = RecordingConnection()
    await transaction.apply_write_plan(
        conn,
        _plan(),
        row=_row(),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    case_statements = [s for s in conn.statements if s.startswith("UPDATE cases")]
    assert case_statements
    assert "AND revision = %(revision_before)s" in case_statements[0]


@pytest.mark.asyncio
async def test_a_noop_plan_writes_no_case_row_and_no_outbox_row() -> None:
    """Rule R2: no state_transitions row, no outbox_events row, no touch of
    ``cases``. The ledger row is still written, because audit is not optional."""
    conn = RecordingConnection()
    executed = await transaction.apply_write_plan(
        conn,
        pipeline.WritePlan(),
        row=decisions.build_decision_row(
            decision_id=DECISION_ID,
            tenant_id=TENANT,
            user_id=USER,
            proposal_id=PROPOSAL,
            trace_id=TRACE,
            decision=KernelDecision.NOOP_DUPLICATE,
            reason_codes=(KernelReasonCode.PROPOSAL_ALREADY_DECIDED,),
            case_id=CASE,
            case_revision_before=12,
            case_revision_after=12,
        ),
        context=transaction.CommitContext(
            tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
        ),
        tx_now=TX_NOW,
    )
    assert "kernel_decisions" in executed
    assert "cases" not in executed
    assert "outbox_events" not in executed
    assert "state_transitions" not in executed


# ---------------------------------------------------------------------------
# One retry loop, and no side effect after the cap
# ---------------------------------------------------------------------------


def test_the_module_does_not_write_a_second_retry_loop() -> None:
    """``provenance_db.retry.run_in_serializable_tx`` is the only 40001 loop."""
    source = Path(inspect.getsourcefile(transaction) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    sleeps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"sleep", "uniform"}
    ]
    assert not sleeps, "backoff belongs to provenance_db.retry, not to the Kernel"
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "40001" not in literals, "SQLSTATE classification belongs to provenance_db.retry"
    assert "run_in_serializable_tx" in source


def test_the_transaction_body_reaches_no_network() -> None:
    """``tools/txn_purity_lint`` over this module, run as a test rather than
    only at the gate."""
    source = Path(inspect.getsourcefile(transaction) or "").read_text(encoding="utf-8")
    result = txn_purity_lint.scan_source(source, "memory_kernel/transaction.py")
    assert result.violations == ()
    assert result.scanned >= 1, "the linter found no callback to scan in transaction.py"


def test_retry_exhaustion_performs_no_side_effect() -> None:
    result = transaction.retry_exhausted_result(
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        tenant_id=TENANT,
        user_id=USER,
        decision_id=DECISION_ID,
        case_id=CASE,
        attempts=5,
    )
    assert result.decision is KernelDecision.RETRYABLE_CONCURRENCY
    assert result.retry_exhausted
    assert result.outbox_event_ids == ()
    assert result.committed_at is None
    assert result.created_claim_ids == ()


def test_the_kernel_never_enqueues_its_own_re_drive() -> None:
    """Section 7.4: the control-plane task role deliberately carries no
    ``sqs:*`` permission and there is no kernel retry queue.

    Asserted over the AST rather than over the file's text: the module's own
    docstring explains the rule and names ``sqs:*`` while doing so, and a
    substring scan cannot tell an explanation from a call.
    """
    source = Path(inspect.getsourcefile(transaction) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for forbidden in ("send_message", "publish", "put_events", "put_object", "send_email"):
        assert forbidden not in called, f"{forbidden} is a side effect after the cap"
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported.isdisjoint({"boto3", "botocore", "httpx", "requests", "aiohttp"})


def test_uuids_are_generated_per_attempt_not_closed_over() -> None:
    """Section 7.3 rule 4: deterministic UUIDs across attempts are forbidden.
    Idempotency comes from ``proposal_id`` and the unique constraints."""
    source = Path(inspect.getsourcefile(transaction) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    callback = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_callback"
        ),
        None,
    )
    assert callback is not None, "the transaction callback must be a named function"
    calls = [
        node
        for node in ast.walk(callback)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "uuid4"
    ]
    assert calls, "the callback must mint fresh ids on every attempt"


def test_the_commit_entry_point_is_async_and_takes_a_pool() -> None:
    signature = inspect.signature(transaction.commit_proposal)
    assert "pool" in signature.parameters
    assert inspect.iscoroutinefunction(transaction.commit_proposal)


def test_statement_labels_are_unique() -> None:
    assert len(set(transaction.STATEMENT_ORDER)) == len(transaction.STATEMENT_ORDER)


def test_every_executed_label_is_declared() -> None:
    """A label the executor emits but the order does not declare is a statement
    nobody reviewed."""
    source = Path(inspect.getsourcefile(transaction) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    emitted = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    undeclared = emitted - set(transaction.STATEMENT_ORDER)
    assert not undeclared, f"undeclared statement labels: {sorted(undeclared)}"


def test_the_context_carries_no_proposal_payload() -> None:
    """Section 7.3 rule 2: fresh reads every attempt. A context that closed over
    a computed plan would replay a plan built against a rolled-back snapshot."""
    fields: Sequence[str] = tuple(transaction.CommitContext.__dataclass_fields__)
    assert "plan" not in fields
    assert "snapshot" not in fields


def test_the_module_exports_only_what_the_caller_needs() -> None:
    exported: Mapping[str, object] = {
        name: getattr(transaction, name) for name in transaction.__all__
    }
    assert "commit_proposal" in exported
    assert "STATEMENT_ORDER" in exported


# ---------------------------------------------------------------------------
# The 23505 constraint-name map — T4.13
#
# `12_KERNEL_ALGORITHMS.md` section 7.5 keyed its unique-violation table by the
# names PostgreSQL AUTO-GENERATES (`<table>_<cols>_key`). `10_DATABASE_DDL.md`
# declares every one of those constraints with an EXPLICIT `uq_*` name instead,
# and `diag.constraint_name` returns the declared one. The two never met, so
# `provenance_db.retry.UNIQUE_VIOLATION_MAP` matched nothing this schema can
# raise and every 23505 fell through to REJECTED_INVARIANT.
#
# Observed against `provenance_ci`: replaying a payment raised
# `uq_fulfillments_commitment_evidence` and the Kernel reported
# REJECTED_INVARIANT / INVARIANT_UNIQUE_VIOLATION where section 9.3 requires
# NOOP_DUPLICATE -- "Payment already applied."
#
# The worst of it was silent. `belief_versions_belief_version_no_key` was the
# ONLY entry carrying `retry_as_serialization_failure=True`, so the version race
# -- two writers reaching the same (belief_id, version_no) -- could never be
# retried. Contention was reported as corruption, and `G4.7` is the assertion
# that would have caught it.
#
# This was first bridged here, in the Kernel, by a CONSTRAINT_ALIASES rename
# table plus a SCHEMA_ONLY_VIOLATIONS table for constraints section 7.5 omitted.
# The map has since been corrected AT SOURCE, so both were removed: two layers
# were doing one job and the outer one existed only to compensate for the inner
# one. A workaround kept after its cause is fixed becomes a second place the
# truth lives.
#
# What remains is the assertion that would have caught the defect on day one,
# and it is the only one here that cannot be satisfied by reading the spec:
# every key must be a constraint THE DATABASE ACTUALLY DECLARES.
# ---------------------------------------------------------------------------


def test_every_mapped_constraint_is_one_the_ddl_declares() -> None:
    """Each key must appear in a migration as an explicitly named constraint.

    This is the check whose absence let the whole table sit dead. It is a static
    scan of `db/migrations/versions/` rather than a database query, so it runs in
    the hermetic unit lane and fails at the moment a name drifts -- not at the
    moment a duplicate row is written in production.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[4]
    migrations = (repo_root / "db" / "migrations" / "versions").glob("*.py")
    declared = set()
    for path in migrations:
        # Bare identifiers, not quoted ones: the migrations spell constraint
        # names inside SQL text and op.create_unique_constraint calls, so a
        # quote-anchored pattern matched zero of the thirty-six actually present
        # and the assertion would have passed by finding nothing to check.
        declared |= set(re.findall(r"(uq_[a-z0-9_]+)", path.read_text(encoding="utf-8")))

    assert declared, "no uq_* constraint names found in db/migrations/versions/"

    unknown = sorted(name for name in UNIQUE_VIOLATION_MAP if name not in declared)
    assert not unknown, (
        f"UNIQUE_VIOLATION_MAP keys that no migration declares: {unknown}. "
        "diag.constraint_name returns the DECLARED name, so an entry the schema "
        "never raises is dead: the 23505 falls through to REJECTED_INVARIANT and "
        "nothing reports that the mapping was missed."
    )


def test_the_version_race_is_mapped_and_is_retryable() -> None:
    """The single entry whose absence was invisible.

    Two writers reaching the same (belief_id, version_no) is CONTENTION. The
    loser must retry and will then read the winner's version. Classified as an
    invariant breach instead, it becomes a lost update reported as corruption.
    """
    outcome = UNIQUE_VIOLATION_MAP["uq_belief_versions_chain"]
    assert outcome.retry_as_serialization_failure is True
    assert outcome.decision is KernelDecision.RETRYABLE_CONCURRENCY

    retryable = [k for k, v in UNIQUE_VIOLATION_MAP.items() if v.retry_as_serialization_failure]
    assert retryable == ["uq_belief_versions_chain"], (
        "exactly one constraint is a retryable race; a second would mean some "
        "other duplicate is being retried instead of refused"
    )


def test_the_kernel_adds_no_second_mapping_table() -> None:
    """The bridge is gone and must not come back.

    A rename table in the Kernel would put the outcome for a constraint in two
    files, and the copy that is read is decided by import order.
    """
    for attribute in ("CONSTRAINT_ALIASES", "SCHEMA_ONLY_VIOLATIONS"):
        assert not hasattr(transaction, attribute), (
            f"{attribute} is back. The 23505 map is corrected at source in "
            "provenance_db.retry.UNIQUE_VIOLATION_MAP; a second table here is a "
            "second place the truth lives."
        )


def test_a_duplicate_payment_is_a_noop_and_not_an_invariant_breach() -> None:
    """The observed failure, as a unit test. `uq_fulfillments_commitment_evidence`
    is what the same bank-transfer email raises on its second arrival, and the
    user-facing consequence of getting it wrong is "your payment was rejected"
    instead of "you already sent this"."""
    outcome = transaction.mapped_unique_violation("uq_fulfillments_commitment_evidence")
    assert outcome.decision is KernelDecision.NOOP_DUPLICATE
    assert outcome.reason_code is KernelReasonCode.FULFILLMENT_EVIDENCE_DUPLICATE


def test_an_unknown_constraint_is_an_invariant_breach_not_a_duplicate() -> None:
    """Fail closed. "This row already existed" and "this write was wrong" have
    opposite consequences, so an unrecognised name is never guessed into a NOOP."""
    outcome = transaction.mapped_unique_violation("uq_something_nobody_mapped")
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert transaction.mapped_unique_violation(None).decision is KernelDecision.REJECTED_INVARIANT


def test_a_re_admitted_claim_is_a_duplicate_not_an_invariant_breach() -> None:
    """`uq_claims_evidence_proposition` is declared by the DDL and absent from
    section 7.5 altogether, so it fell through to `INVARIANT_UNIQUE_VIOLATION`.

    Observed against `provenance_ci`: re-submitting the same payment evidence
    raised this constraint, not `uq_fulfillments_commitment_evidence` -- DDL
    section 13 puts `claims` at statement 3 and `fulfillments` at statement 6,
    so the claims guard fires first and the fulfillments guard is never reached.

    The DDL's own comment on the constraint settles what it means: "One evidence
    item states one thing about one subject/predicate exactly once. Re-processing
    the same artifact therefore cannot double-count a claim." That is a
    duplicate. Reporting "your payment was rejected as an invariant breach" for
    "you already sent this" is wrong in the only sense the product cares about.
    """
    outcome = transaction.mapped_unique_violation("uq_claims_evidence_proposition")
    assert outcome.decision is KernelDecision.NOOP_DUPLICATE
    assert outcome.reason_code is KernelReasonCode.CLAIM_SEMANTIC_DUPLICATE
    assert outcome.retry_as_serialization_failure is False
