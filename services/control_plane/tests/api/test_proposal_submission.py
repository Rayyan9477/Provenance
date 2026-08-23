"""Section 9.7 bound: the app writes the proposal row, the Kernel decides it.

Why this file exists
--------------------
``internal.submit_proposal`` is the agent's only write path and the whole
product rests on it: *agents emit typed ``MemoryProposal``s; no model ever
holds a SQL write credential; the Memory Kernel is the sole canonical writer
and decides.* Until the app-side ``memory_proposals`` INSERT existed, an agent
could not reach the Kernel at all --
``memory_kernel.transaction.commit_proposal`` only ``UPDATE``s that row, and
``fk_kernel_decisions_proposal`` refuses the decision row when the proposal
row is absent.

What is asserted here, and why each one is not decoration
----------------------------------------------------------
* **The INSERT is the app's, under write rule ``W4``, and lives outside the
  Kernel.** Putting it in ``app/memory_kernel`` would make
  ``write_path_lint.kernel_statements`` count it as a Kernel write, which is a
  claim about who owns the statement and would be false.
* **Order.** The row is written *before* ``commit_proposal`` is called. The
  reverse order fails at the database on a foreign key, in production, on the
  one path the product rests on.
* **The model attribution is checked against ``agent_runs.model_route``.**
  Section 9.7's request body carries a ``model`` block; ``CANONICAL_DECISIONS.md``
  -> *Disclosure* requires the model claim to be checkable against persisted
  state rather than against a README. Both hold only if the wire's claim is
  *compared* to the row and refused on a mismatch.
* **The idempotency key is the presented header, not a mint.** The same defect
  ``D-00-0xx`` recorded on ``execute_action``: a minted key matches the row
  only when the caller supplied none.
* **A counterfactual run cannot submit at all.** ``ck_agent_runs_counterfactual_toolless``
  says a MEMORY OFF run was never given the proposal tool; the parity claim in
  ``CANONICAL_DECISIONS.md`` -> *Counterfactual* is worthless if the OFF column
  can write canonical memory.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[2] / "app"
SUBMISSION_MODULE = APP / "proposals" / "submission.py"

RUN_ID = uuid.UUID("018f9e90-0000-7000-8000-000000000001")
TENANT_ID = uuid.UUID("018f7a00-0000-7000-8000-00000000abcd")
USER_ID = uuid.UUID("018f7a01-0000-7000-8000-00000000abcd")
CASE_ID = uuid.UUID("018f8a10-4c22-7f31-9b7d-2ac1e5f09b41")
RELATIONSHIP_ID = uuid.UUID("018f7c00-0000-7000-8000-000000000004")
ARTIFACT_ID = uuid.UUID("018f9e80-0000-7000-8000-000000000001")
EVIDENCE_ID = uuid.UUID("018f8aa0-0000-7000-8000-000000000021")
PROPOSAL_ID = uuid.UUID("018f9fa0-0000-7000-8000-000000000001")
TRACE_ID = uuid.UUID("018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

#: The route the two live ``agent_runs`` rows on the cluster actually carry,
#: read from the database on 2026-08-24 rather than invented.
MODEL_ROUTE: dict[str, str] = {
    "tier_e": "gemini-3.5-flash-lite",
    "tier_r": "gemini-3.7-flash",
    "embeddings": "gemini-embedding-2",
}

#: The prompt asset that produced the extraction. It is on
#: ``agent_runs.model_calls[]`` only *after* section 9.9 settles the run, so at
#: 9.7 the caller is the only holder of it.
PROMPT_VERSION = "pv-extract-1.1.0"


def run_row(**overrides: Any) -> dict[str, Any]:
    """One ``agent_runs`` projection, as ``AGENT_RUN_SQL`` returns it."""
    row: dict[str, Any] = {
        "id": RUN_ID,
        "trace_id": TRACE_ID,
        "graph_name": "ingestion",
        "graph_version": "1.0.0",
        "model_route": dict(MODEL_ROUTE),
        "memory_mode": "ON",
        "is_counterfactual": False,
        "status": "RUNNING",
        "started_at": NOW,
        "finished_at": None,
        "expires_at": NOW,
        "input_artifact_id": ARTIFACT_ID,
        "allowed_case_ids": [str(CASE_ID)],
        "retrieval_candidate_count": None,
        "error_code": None,
        "tool_calls": None,
        "model_calls": None,
        "capability_status": None,
    }
    row.update(overrides)
    return row


def claim_dict(**overrides: Any) -> dict[str, Any]:
    """One ``ProposedClaim``, in the wire shape the contract package owns."""
    claim: dict[str, Any] = {
        "local_id": "cl_001",
        "claim_kind": "COUNTERPARTY_CLAIM",
        "subject_type": "RELATIONSHIP",
        "subject_id": str(RELATIONSHIP_ID),
        "predicate": "billing_period_covered",
        "object_type": "INTERVAL",
        "object_value": {"period_start": "2026-06-01", "period_end": "2026-06-30"},
        "actor_type": "COUNTERPARTY",
        "actor_ref": "Northline Fiber",
        "evidence_id": str(EVIDENCE_ID),
        "source_class": "PROVIDER_AGENT_WRITTEN",
        "modality": "ASSERTED_PAST",
        "extraction_confidence": "0.9700",
    }
    claim.update(overrides)
    return claim


def request_body(**overrides: Any) -> Any:
    """A ``MemoryProposalRequest``, valid unless a test moves one field."""
    from services.control_plane.app.api.schemas.internal import MemoryProposalRequest

    body: dict[str, Any] = {
        "schema_version": "1.0",
        "agent_run_id": str(RUN_ID),
        "proposal_id": str(PROPOSAL_ID),
        "trace_id": str(TRACE_ID),
        "user_id": str(USER_ID),
        "proposal_type": "INGESTION_INTERPRETATION",
        "source_artifact_ids": [str(ARTIFACT_ID)],
        "evidence_ids": [str(EVIDENCE_ID)],
        "identity": {
            "relationship_id": str(RELATIONSHIP_ID),
            "case_id": str(CASE_ID),
            "confidence": "0.9600",
        },
        "claims": [claim_dict()],
        "model": {
            "provider": "gemini",
            "model_id": MODEL_ROUTE["tier_e"],
            "tier": "E",
            "prompt_version": PROMPT_VERSION,
        },
    }
    body.update(overrides)
    return MemoryProposalRequest.model_validate(body)


# ==========================================================================
# Test doubles: one connection that records, one Kernel door that records
# ==========================================================================


class _Column:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _Cursor:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn
        self.description: tuple[_Column, ...] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        self._conn.record(sql, params)
        if self._conn.raise_on is not None and self._conn.raise_on[0] in sql:
            raise self._conn.raise_on[1]
        columns, rows = self._conn.answer(sql)
        self.description = tuple(_Column(name) for name in columns)
        self._rows = rows
        self.rowcount = len(rows) if rows else self._conn.written_rowcount(sql)

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    """Records statements in order and answers the ``agent_runs`` read."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[str] = []
        self.statements: list[tuple[str, Any]] = []
        self.raise_on: tuple[str, Exception] | None = None

    def record(self, sql: str, params: Any) -> None:
        self.statements.append((sql, params))
        if "FROM agent_runs" in sql:
            self.calls.append("read_agent_run")
        elif "INTO memory_proposals" in sql or "INTO\nmemory_proposals" in sql:
            self.calls.append("insert_proposal")
        else:  # pragma: no cover - a statement no test expects
            self.calls.append(f"other:{sql.strip()[:40]}")

    def answer(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        if "FROM agent_runs" in sql and self.row is not None:
            columns = list(self.row)
            return columns, [tuple(self.row[c] for c in columns)]
        return [], []

    def written_rowcount(self, sql: str) -> int:
        return 1 if "memory_proposals" in sql else 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    async def rollback(self) -> None:  # pragma: no cover - no retry here
        return None


class FakeSource:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    def connection(self) -> Any:
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> FakeConnection:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


class RecordingKernel:
    """The Kernel's door, recording what was pushed through it."""

    def __init__(self, conn: FakeConnection, result: Any = None) -> None:
        self._conn = conn
        self.proposals: list[Any] = []
        self.principals: list[Any] = []
        self.result = result

    async def commit(self, proposal: Any, *, principal: Any) -> Any:
        self._conn.calls.append("commit_proposal")
        self.proposals.append(proposal)
        self.principals.append(principal)
        return self.result if self.result is not None else accepted_result(proposal)


def accepted_result(proposal: Any) -> Any:
    from provenance_contracts.kernel import KernelCommitResult

    return KernelCommitResult(
        decision="ACCEPTED",
        proposal_id=proposal.proposal_id,
        kernel_decision_id=uuid.UUID("018f8b90-0000-7000-8000-000000000002"),
        proposal_status="ACCEPTED",
        trace_id=proposal.trace_id,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        case_id=CASE_ID,
        case_revision_before=12,
        case_revision_after=13,
        created_claim_ids=(uuid.UUID("018f8ab0-0000-7000-8000-000000000011"),),
        attention_required=True,
        reason_codes=("BELIEF_CREATED",),
        committed_at=NOW,
    )


def binding() -> Any:
    from provenance_contracts.identity import CapabilityBinding

    return CapabilityBinding(
        binding_id=RUN_ID,
        binding_kind="AGENT_RUN",
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        allowed_case_ids=(CASE_ID,),
        expires_at=datetime(2026, 9, 18, 13, 0, tzinfo=UTC),
        status="ACTIVE",
    )


def build_port(conn: FakeConnection, kernel: RecordingKernel | None = None) -> Any:
    from services.control_plane.app.actions import ActionPolicy
    from services.control_plane.app.api.adapters.internal import KernelInternalPort

    return KernelInternalPort(
        FakeSource(conn),
        kernel_pool=None,
        read=object(),
        policy=ActionPolicy(allowlist=frozenset(), execution_mode="DRY_RUN", recipient_mode="SINK"),
        sink=object(),
        clock=lambda: NOW,
        proposal_kernel=kernel if kernel is not None else RecordingKernel(conn),
    )


# ==========================================================================
# 1. The write rule: the INSERT is the app's, and it is not in the Kernel
# ==========================================================================


def test_the_app_side_proposal_insert_exists() -> None:
    """The blocker the register named, closed.

    ``CANONICAL_WRITE_STATEMENTS`` holds a ``memory_proposals`` INSERT, but it
    is the Kernel authoring its own deterministic trigger proposal. This is the
    other one -- the app's, under ``W4`` -- and without it an agent cannot
    reach the Kernel at all.
    """
    from services.control_plane.app.proposals import submission

    sql = submission.PROPOSAL_INSERT_SQL
    assert "INSERT INTO memory_proposals" in sql
    assert "ON CONFLICT DO NOTHING" in sql, (
        "a re-offered proposal must not raise: the Kernel decides replays by "
        "proposal_id (rule R6), and a unique violation here would refuse the "
        "retry before the Kernel ever saw it"
    )


def test_the_insert_is_permitted_by_w4_and_is_not_a_kernel_statement() -> None:
    """Rule 1 of this build, structurally: the Kernel is the sole canonical
    writer *except* the two ``W4`` INSERTs, and this is one of them.

    Asserted through the linter rather than by reading the path, because the
    linter is what ``G4.3`` runs. A module placed under ``app/memory_kernel``
    would pass a path check and would silently inflate ``kernel_statements``,
    which ``tests/kernel/test_obligations.py`` pins against the Kernel's own
    enumeration.
    """
    from tools import write_path_lint

    source = SUBMISSION_MODULE.read_text(encoding="utf-8")
    display = "services/control_plane/app/proposals/submission.py"
    result = write_path_lint.scan_source(source, display)

    assert result.violations == [], [str(v) for v in result.violations]
    assert result.canonical_statements >= 1, "the linter cannot see the INSERT at all"
    assert result.kernel_statements == 0, (
        "the app-side proposal INSERT is being counted as a Kernel write; "
        "kernel_statements is pinned against CANONICAL_WRITE_STATEMENTS"
    )
    assert "memory_proposals" in write_path_lint.APP_INSERT_PERMITTED


def test_the_module_holds_no_canonical_write_other_than_the_proposal_insert() -> None:
    """One statement, one table. A second canonical write here would be a
    second canonical writer wearing this module's name."""
    from tools import write_path_lint

    tables: set[str] = set()
    tree = ast.parse(SUBMISSION_MODULE.read_text(encoding="utf-8"))
    # The linter's own docstring filter, reused rather than restated: a module
    # that documents this rule must not trip it, and a second copy of the
    # filter would eventually disagree with the one that decides violations.
    for node in write_path_lint._literal_strings(tree):
        for match in write_path_lint._STATEMENT_RE.finditer(str(node.value)):
            table = match.group("table").lower()
            if table in write_path_lint.CANONICAL_TABLES:
                tables.add(f"{' '.join(match.group('op').split()).upper()} {table}")
    assert tables == {"INSERT INTO memory_proposals"}, tables


# ==========================================================================
# 2. The order, which is a foreign key and not a preference
# ==========================================================================


async def test_the_row_is_written_before_the_kernel_is_called() -> None:
    """``fk_kernel_decisions_proposal`` refuses a decision for a row that is
    not there, so the reverse order fails in production on the hero path."""
    conn = FakeConnection(run_row())
    port = build_port(conn)

    await port.submit_proposal(binding(), request_body(), idempotency_key="prop-run-1-attempt-1")

    assert conn.calls == ["read_agent_run", "insert_proposal", "commit_proposal"], conn.calls


async def test_the_proposal_handed_to_the_kernel_is_the_row_that_was_written() -> None:
    """One proposal, one row, one digest.

    ``uq_memory_proposals_payload`` is ``(tenant_id, user_id, payload_sha256)``,
    so a row whose digest was computed over something other than the object the
    Kernel decided would make a replay look like a new proposal.

    The expected digest is computed **here**, from the object the Kernel was
    handed, rather than by calling ``submission.payload_sha256`` again. The
    first version of this test did call it again, and its counterfactual --
    digesting ``str(proposal_id)`` instead of the payload -- came back
    **green**: both sides of the comparison moved together, so the assertion
    was a function compared with itself. Naming the definition is what makes it
    falsifiable.
    """
    import hashlib
    import json

    conn = FakeConnection(run_row())
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    await port.submit_proposal(binding(), request_body(), idempotency_key="prop-run-1-attempt-1")

    (proposal,) = kernel.proposals
    serialised = proposal.model_dump_json()
    _, params = next((sql, p) for sql, p in conn.statements if "memory_proposals" in sql)
    assert params["id"] == proposal.proposal_id
    assert params["payload_sha256"] == hashlib.sha256(serialised.encode("utf-8")).digest()
    assert len(params["payload_sha256"]) == 32, "ck_memory_proposals_payload_sha wants 32 bytes"
    assert params["payload"].obj == json.loads(serialised), (
        "the stored payload is a summary of the proposal rather than the "
        "proposal; the Memory Trace reads this column"
    )


async def test_the_kernel_is_given_the_binding_owner_and_never_the_body() -> None:
    """Tenancy comes from the capability, and the body is driven to disagree.

    ``MemoryProposal`` carries no ``tenant_id`` by design, and its ``user_id``
    is section 3.6's tripwire -- an assertion by the caller, compared and then
    discarded. So the body here names **another user**, and both the row and
    the principal must still be the capability's.

    Driving them apart is the whole test. The first version passed the ordinary
    fixture, whose ``user_id`` already equalled the binding's, and its
    counterfactual -- ``Principal(tenant_id, payload.user_id)`` -- came back
    **green**, because the two sources were the same value.

    The mismatched ``user_id`` is deliberately left **on** the proposal rather
    than corrected here: ``preflight`` refuses it with
    ``PRINCIPAL_USER_MISMATCH``, and an adapter that quietly rewrote the field
    would disarm the tripwire and turn a security event into a successful
    write. In the HTTP path ``assert_within_capability`` has already answered
    ``403``; this port is reachable without it.
    """
    other_user = uuid.UUID("018f7a02-0000-7000-8000-00000000abcd")
    conn = FakeConnection(run_row())
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    await port.submit_proposal(
        binding(), request_body(user_id=str(other_user)), idempotency_key="prop-run-1-attempt-1"
    )

    (principal,) = kernel.principals
    assert principal.tenant_id == TENANT_ID
    assert principal.user_id == USER_ID
    _, params = next((sql, p) for sql, p in conn.statements if "memory_proposals" in sql)
    assert params["tenant_id"] == TENANT_ID
    assert params["user_id"] == USER_ID
    (proposal,) = kernel.proposals
    assert proposal.user_id == other_user, (
        "the claimed user_id was rewritten to match the capability, which "
        "disarms section 3.6's tripwire before the Kernel can fire it"
    )


# ==========================================================================
# 3. The model attribution: compared against the row, never taken on trust
# ==========================================================================


async def test_the_model_id_is_checked_against_the_runs_persisted_route() -> None:
    """``CANONICAL_DECISIONS.md`` -> *Disclosure*, enforced rather than stated.

    Section 9.7's body carries a ``model`` block, unlike section 9.8's. That is
    only safe because the claimed id must be one the run row already records:
    ``agent_runs.model_route`` is what makes the shipped model checkable against
    persisted state, and a body that could name any id would replace that
    check with a caller's assertion.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    port = build_port(conn)
    body = request_body(
        model={
            "provider": "gemini",
            "model_id": "gemini-3.1-pro-preview",
            "tier": "E",
            "prompt_version": PROMPT_VERSION,
        }
    )

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(binding(), body, idempotency_key="prop-run-1-attempt-1")

    assert caught.value.code.value == "PROPOSAL_SCHEMA_INVALID"
    assert "insert_proposal" not in conn.calls, "a refused attribution still wrote a row"
    assert "commit_proposal" not in conn.calls


async def test_the_claimed_tier_selects_which_route_entry_must_hold_the_id() -> None:
    """The tier is checked, not searched for.

    Deriving it by scanning ``model_route`` for the claimed id is ambiguous the
    moment both tiers point at one model -- the documented response to a Tier R
    capacity failure (``CANONICAL_DECISIONS.md`` -> *Tier R fallback*) -- and a
    scan that silently picked one would record a tier the call may not have
    used. So the caller names the tier and the tier names the entry that has to
    match.
    """
    conn = FakeConnection(run_row())
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    await port.submit_proposal(
        binding(),
        request_body(
            model={
                "provider": "gemini",
                "model_id": MODEL_ROUTE["tier_r"],
                "tier": "R",
                "prompt_version": "pv-resolve-1.1.0",
            }
        ),
        idempotency_key="prop-run-1-attempt-1",
    )

    (proposal,) = kernel.proposals
    assert proposal.model.tier.value == "R"
    assert proposal.model.model_id == MODEL_ROUTE["tier_r"]


async def test_a_tier_r_id_claimed_as_tier_e_is_refused() -> None:
    """Both halves of the claim are checked together.

    The id is on the run's route, so an "is this id anywhere in the route"
    check would admit it. What makes the pair falsifiable is that the *entry
    the claimed tier names* must hold it.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(),
            request_body(
                model={
                    "provider": "gemini",
                    "model_id": MODEL_ROUTE["tier_r"],
                    "tier": "E",
                    "prompt_version": PROMPT_VERSION,
                }
            ),
            idempotency_key="prop-run-1-attempt-1",
        )

    assert caught.value.details["reason"] == "MODEL_NOT_ON_RUN_ROUTE"
    assert "insert_proposal" not in conn.calls


def test_no_proposal_can_be_attributed_to_an_embedding_model() -> None:
    """An embedding model reasons about nothing, so it attributes nothing.

    Refused twice on purpose, at two different boundaries. ``ProposalModel``
    admits only ``E`` and ``R``, so the wire cannot say ``EMBEDDING``; and
    ``resolve_attribution`` has no route key for that tier, so the refusal
    survives a schema that later widened. One of the two would be enough today
    and neither is enough on its own tomorrow.
    """
    from pydantic import ValidationError

    from services.control_plane.app.proposals import submission

    with pytest.raises(ValidationError):
        request_body(
            model={
                "provider": "gemini",
                "model_id": MODEL_ROUTE["embeddings"],
                "tier": "EMBEDDING",
                "prompt_version": PROMPT_VERSION,
            }
        )

    with pytest.raises(submission.ProposalRefusedError) as caught:
        submission.resolve_attribution(
            run_row(),
            provider="gemini",
            model_id=MODEL_ROUTE["embeddings"],
            tier="EMBEDDING",
            prompt_version=PROMPT_VERSION,
        )
    assert caught.value.reason_code == "MODEL_TIER_CANNOT_PRODUCE_A_PROPOSAL"


async def test_the_graph_name_and_version_come_from_the_row_not_the_body() -> None:
    """Two ``NOT NULL`` columns the caller has no way to assert."""
    conn = FakeConnection(run_row(graph_name="advocate", graph_version="2.4.1"))
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    await port.submit_proposal(binding(), request_body(), idempotency_key="prop-run-1-attempt-1")

    (proposal,) = kernel.proposals
    assert proposal.model.graph_name == "advocate"
    assert proposal.model.graph_version == "2.4.1"


async def test_the_persisted_row_records_the_model_that_produced_it() -> None:
    """Not ``deterministic.kernel``.

    ``scripts/seed/proposals.py`` writes ``deterministic.kernel`` because no
    model ran, and says so. A model *did* run here, and writing the seed's
    value would be a false attribution in the one system whose product is
    knowing who said what.
    """
    conn = FakeConnection(run_row())
    port = build_port(conn)

    await port.submit_proposal(binding(), request_body(), idempotency_key="prop-run-1-attempt-1")

    _, params = next((sql, p) for sql, p in conn.statements if "memory_proposals" in sql)
    assert params["model_id"] == MODEL_ROUTE["tier_e"]
    assert params["prompt_version"] == PROMPT_VERSION
    assert params["status"] == "SUBMITTED"


# ==========================================================================
# 4. The idempotency key is the caller's, and a counterfactual writes nothing
# ==========================================================================


async def test_the_idempotency_key_is_the_presented_header() -> None:
    """Read, not minted. A minted key equals the presented one only when the
    caller supplied none, which is the case that does not need a key."""
    conn = FakeConnection(run_row())
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    await port.submit_proposal(binding(), request_body(), idempotency_key="prop-018f9e90-attempt-1")

    (proposal,) = kernel.proposals
    assert proposal.idempotency_key == "prop-018f9e90-attempt-1"


async def test_a_key_the_contract_refuses_is_a_typed_refusal_not_a_500() -> None:
    """The two patterns are not the same set.

    ``idempotency.IDEMPOTENCY_KEY_PATTERN`` admits ``~`` and 255 characters;
    ``provenance_contracts.base.IdempotencyKey`` admits ``:`` and caps at 128.
    A key legal at the HTTP boundary and illegal in the contract must not
    arrive as an unhandled ``ValidationError``.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(), request_body(), idempotency_key="prop~run~1~attempt~1"
        )

    assert caught.value.code.value == "PROPOSAL_SCHEMA_INVALID"
    assert "insert_proposal" not in conn.calls


async def test_the_two_idempotency_key_patterns_disagree_in_both_directions() -> None:
    """The guard on the guard above.

    If the two patterns ever became the same set, the refusal test would pass
    vacuously -- no key could be legal at one boundary and illegal at the
    other -- and this file would be asserting nothing about that path.
    """
    import re

    from provenance_contracts.base import IdempotencyKey
    from services.control_plane.app.api.idempotency import IDEMPOTENCY_KEY_PATTERN

    contract = re.compile(IdempotencyKey.__metadata__[0].pattern)
    http_only = "prop~run~1~attempt~1"
    contract_only = "run:1:ab"
    assert IDEMPOTENCY_KEY_PATTERN.fullmatch(http_only) is not None
    assert contract.fullmatch(http_only) is None
    assert contract.fullmatch(contract_only) is not None
    assert IDEMPOTENCY_KEY_PATTERN.fullmatch(contract_only) is None


async def test_a_counterfactual_run_cannot_reach_the_kernel() -> None:
    """MEMORY OFF writes nothing, and the refusal is here rather than implied.

    ``ck_agent_runs_counterfactual_toolless`` records that a counterfactual run
    was never given the proposal tool. Nothing else in the request path reads
    ``is_counterfactual``: the capability record does not carry it, so without
    this check the OFF column of the Judge Mode comparison could commit
    canonical memory and the parity claim would be decorative.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row(is_counterfactual=True, memory_mode="OFF"))
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
        )

    assert caught.value.code.value == "CAPABILITY_SCOPE_MISMATCH"
    assert conn.calls == ["read_agent_run"], conn.calls


async def test_a_run_that_does_not_resolve_is_absent_rather_than_assumed() -> None:
    """``D-00-005``: no row is "not loaded", never "an empty route"."""
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(None)
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
        )

    assert caught.value.code.value == "AGENT_RUN_NOT_FOUND"
    assert conn.calls == ["read_agent_run"]


# ==========================================================================
# 5. A proposal the contract refuses never reaches the row
# ==========================================================================


async def test_a_proposal_type_the_contract_does_not_know_is_a_422() -> None:
    """Section 9.7's own example prints ``EVIDENCE_INTERPRETATION``, which is
    not a ``ProposalType`` member and which ``ck_memory_proposals_type`` does
    not admit either. The contract package is the authority and the refusal is
    typed rather than a 500."""
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(),
            request_body(proposal_type="EVIDENCE_INTERPRETATION"),
            idempotency_key="prop-run-1-attempt-1",
        )

    assert caught.value.code.value == "PROPOSAL_SCHEMA_INVALID"
    assert conn.calls == ["read_agent_run"], "a refused proposal wrote a row"


async def test_a_claim_citing_undeclared_evidence_is_refused_before_the_row() -> None:
    """``MemoryProposal._evidence_references_are_declared`` exists so the Kernel
    can ownership-check the whole set in one read. A row written before that
    check would leave a ``SUBMITTED`` proposal nothing will ever decide."""
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    port = build_port(conn)

    with pytest.raises(ApiError):
        await port.submit_proposal(
            binding(),
            request_body(evidence_ids=[]),
            idempotency_key="prop-run-1-attempt-1",
        )

    assert conn.calls == ["read_agent_run"]


# ==========================================================================
# 5b. The database refuses the row, and the agent is told so rather than 500d
# ==========================================================================


def check_violation(constraint: str | None) -> Exception:
    """A ``CheckViolation`` carrying the ``diag`` the driver would have built.

    ``psycopg.errors.Error.diag`` is a read-only property over the wire result,
    so a hand-made exception has an empty one. Subclassing is the only way to
    plant a constraint name, and it is safe here because the production code
    reads exactly one attribute off it.
    """
    import psycopg.errors as pgerr

    class _Diag:
        constraint_name = constraint

    class _CheckViolation(pgerr.CheckViolation):
        @property
        def diag(self) -> Any:  # type: ignore[override]
            return _Diag()

    return _CheckViolation("failed to satisfy CHECK constraint")


async def test_a_check_the_database_refuses_becomes_a_named_refusal() -> None:
    """Measured against the live cluster on 2026-08-24, not imagined.

    ``PROPOSAL_INSERT_SQL`` was run against ``provenance`` inside a rolled-back
    transaction with the model route the two real ``agent_runs`` rows carry.
    Both shipping ids -- ``gemini-3.5-flash-lite`` and ``gemini-3.7-flash`` --
    came back ``CheckViolation``, ``constraint=ck_memory_proposals_model``,
    because the applied schema still admits only the four Bedrock-era ids;
    migration ``0009`` widens exactly that CHECK to the Gemini set and is
    deliberately unapplied. The same statement with an admitted id wrote one
    row and read back ``SUBMITTED``, so the statement itself is right.

    So this path is reachable **today**, on the product's central path, and the
    only question is what the agent is told. A raw ``CheckViolation`` surfaces
    as a ``500`` -- "something went wrong on our side" -- which is both wrong
    and unactionable. This turns it into a refusal that names the constraint
    the database named.

    The check is deliberately **not** made before the write. Doing that needs a
    copy of the CHECK's admitted set in Python, and a second registry for one
    fact is the defect ``STATUS.md`` section 5 records twice. The database owns
    the list; this reads its answer.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    conn.raise_on = ("memory_proposals", check_violation("ck_memory_proposals_model"))
    kernel = RecordingKernel(conn)
    port = build_port(conn, kernel)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
        )

    assert caught.value.code.value == "PROPOSAL_SCHEMA_INVALID"
    assert caught.value.details["reason"] == "PROPOSAL_ROW_REFUSED_BY_SCHEMA"
    assert caught.value.details["model_id"] == MODEL_ROUTE["tier_e"]
    assert kernel.proposals == [], (
        "the Kernel was called for a proposal row that does not exist; "
        "fk_kernel_decisions_proposal would refuse the decision row"
    )


async def test_the_refusal_names_the_constraint_the_database_named() -> None:
    """Not a guess and not a constant.

    ``exc.diag.constraint_name`` is what CockroachDB returned. A hard-coded
    ``ck_memory_proposals_model`` here would keep reporting that constraint
    after some *other* CHECK started refusing -- ``ck_memory_proposals_type``,
    ``ck_memory_proposals_schema_version``, ``ck_memory_proposals_payload_sha``
    -- and send the reader to the wrong line of the wrong migration.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row())
    conn.raise_on = ("memory_proposals", check_violation("ck_memory_proposals_type"))
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.submit_proposal(
            binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
        )

    assert caught.value.details["constraint"] == "ck_memory_proposals_type"


# ==========================================================================
# 6. The receipt the agent reads back
# ==========================================================================


async def test_the_receipt_carries_no_owner_ids() -> None:
    """Section 9.2's rule applied to 9.7's response: the graph never needs
    ``tenant_id`` or ``user_id``, and ``KernelCommitResult`` carries both. A
    ``model_dump()`` of the receipt would hand the model an id it should never
    see or be able to repeat."""
    conn = FakeConnection(run_row())
    port = build_port(conn)

    row = await port.submit_proposal(
        binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
    )

    assert "tenant_id" not in row
    assert "user_id" not in row
    assert row["decision"] == "ACCEPTED"
    assert row["case_revision_before"] == 12
    assert row["case_revision_after"] == 13
    assert row["proposal_id"] == str(PROPOSAL_ID)
    assert row["reason_codes"] == ["BELIEF_CREATED"]


async def test_the_receipt_reports_the_claim_the_kernel_actually_created() -> None:
    """``created_claims`` is the receipt's, not the request's.

    Recorded deviation: section 9.7 prints ``created_claims[].client_ref``, and
    ``KernelCommitResult`` carries no local-id mapping -- ``CommitEffects``
    holds ``claim_ids`` and ``pipeline.ClaimWrite`` has no ``local_id``. The
    id is reported and the ``client_ref`` is not invented.
    """
    conn = FakeConnection(run_row())
    port = build_port(conn)

    row = await port.submit_proposal(
        binding(), request_body(), idempotency_key="prop-run-1-attempt-1"
    )

    assert row["created_claims"] == [{"claim_id": "018f8ab0-0000-7000-8000-000000000011"}]


# ==========================================================================
# 6b. The door to all of the above: a RUNNING run has a live capability
# ==========================================================================


class _CapabilityConnection:
    """Answers ``AGENT_RUN_CAPABILITY_SQL`` with one projected row."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def cursor(self) -> Any:
        row = self.row

        class _Cur:
            description = tuple(_Column(name) for name in row)

            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def execute(self, sql: str, params: Any = None) -> None:
                return None

            async def fetchall(self) -> list[tuple[Any, ...]]:
                return [tuple(row.values())]

        return _Cur()


def _capability_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "capability_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "artifact_id": ARTIFACT_ID,
        "allowed_case_ids": [str(CASE_ID)],
        "expires_at": datetime(2026, 9, 18, 13, 0, tzinfo=UTC),
        "status": "RUNNING",
        "trace_id": TRACE_ID,
    }
    row.update(overrides)
    return row


def test_the_capability_read_projects_the_runs_lifecycle_not_its_trace_metadata() -> None:
    """The half a row-level fake cannot see, and the half that was wrong.

    A double that answers ``load()`` with a dict returns whatever the test put
    in it, so it cannot tell which column the statement aliased to ``status``.
    This reads the statement.

    ``capability_status`` is section 9.9's ``JSONB`` trace metadata --
    ``ck_agent_runs_capability_status`` requires an *object* when it is not
    ``NULL``, and it is ``NULL`` for every run that has not finished. ``status``
    is the run's lifecycle: ``ck_agent_runs_status`` admits ``RUNNING``,
    ``SUCCEEDED``, ``FAILED``, ``ABANDONED``. Aliasing the first to ``status``
    made liveness depend on a column that is null exactly while the capability
    is alive.
    """
    from services.control_plane.app.api.adapters import directory

    sql = directory.AGENT_RUN_CAPABILITY_SQL
    assert "capability_status" not in sql, (
        "the capability read projects section 9.9's JSONB trace metadata as "
        "the liveness string; it is NULL for every live run"
    )
    assert "ar.status" in sql, "the run's lifecycle column is not projected at all"


async def test_a_running_agent_run_resolves_as_a_live_capability() -> None:
    """Everything above is unreachable over HTTP unless this holds.

    ``resolve_capability`` refuses anything whose record status is not exactly
    ``"ACTIVE"`` -- ``REVOKED`` is ``CAPABILITY_REVOKED`` and every other value
    is ``CAPABILITY_CONSUMED``. ``agent_runs`` has no ``ACTIVE`` state to
    project: ``ck_agent_runs_status`` admits ``RUNNING``, ``SUCCEEDED``,
    ``FAILED`` and ``ABANDONED``, so the liveness string has to be *derived*,
    exactly as ``_trigger`` derives it from ``state == 'ARMED'``.

    This is the defect this test was written to catch and did.
    ``AGENT_RUN_CAPABILITY_SQL`` projected ``ar.capability_status AS status``
    -- a ``JSONB`` **metadata** column that section 9.9 writes at run
    completion, ``NULL`` for every live run, and constrained by
    ``ck_agent_runs_capability_status`` to be an *object* when it is not. So
    ``str(row["status"])`` was ``"None"`` for a healthy ``RUNNING`` run and
    ``"{...}"`` for a finished one; neither is ``"ACTIVE"``, so **every**
    agent-run capability answered ``403 CAPABILITY_CONSUMED`` and no agent
    could reach any ``/internal/v1`` endpoint at all. Nothing caught it because
    no test drove ``SqlCapabilityStore._agent_run``: the route suites use a
    fake capability store, and the live path had never been exercised.
    """
    from services.control_plane.app.api.adapters.directory import SqlCapabilityStore

    store = SqlCapabilityStore(FakeSource(_CapabilityConnection(_capability_row())))
    record = await store.load("AGENT_RUN", str(RUN_ID))

    assert record is not None
    assert record.status == "ACTIVE", (
        "a RUNNING agent run does not resolve as a live capability, so every "
        "/internal/v1 call it makes is 403 CAPABILITY_CONSUMED"
    )
    assert record.allowed_case_ids == (CASE_ID,)
    assert record.artifact_id == ARTIFACT_ID


async def test_a_settled_agent_run_is_consumed_rather_than_live() -> None:
    """The other direction, which is what section 9.9 exists to produce.

    "Any subsequent call with this id returns ``403 CAPABILITY_CONSUMED``" is
    section 9.9's closing sentence, and it is only true if a terminal ``status``
    stops resolving as ``ACTIVE``. Asserted for all three terminal values
    rather than for one, because a mapping that special-cased ``SUCCEEDED``
    would leave a failed run's token live.
    """
    from services.control_plane.app.api.adapters.directory import SqlCapabilityStore

    for terminal in ("SUCCEEDED", "FAILED", "ABANDONED"):
        source = FakeSource(_CapabilityConnection(_capability_row(status=terminal)))
        record = await SqlCapabilityStore(source).load("AGENT_RUN", str(RUN_ID))
        assert record is not None
        assert record.status == "CONSUMED", f"{terminal} still resolves as {record.status}"


# ==========================================================================
# 7. The register and the tree agree
# ==========================================================================


def test_submit_proposal_is_no_longer_declared_unbound() -> None:
    from services.control_plane.app.api import adapters

    assert "internal.submit_proposal" not in adapters.UNBOUND
    source = inspect.getsource(adapters.KernelInternalPort.submit_proposal)
    assert 'unbound("internal.submit_proposal")' not in source


def test_the_digest_agrees_with_the_one_the_seed_already_uses() -> None:
    """Two definitions of ``payload_sha256`` for one column is how a reseed
    stops recognising the row it already wrote.

    ``scripts/seed/loader.py`` writes ``memory_proposals`` rows through its own
    copy of this pair, and ``uq_memory_proposals_payload`` is keyed on the
    digest. Asserted against the seed's function rather than against a
    hard-coded hex string, so the two cannot drift apart in either direction.
    """
    from scripts.seed.proposals import CURATED_PROPOSALS
    from scripts.seed.proposals import payload_sha256 as seed_digest
    from scripts.seed.proposals import proposal_payload as seed_payload
    from services.control_plane.app.proposals import submission

    seeded = CURATED_PROPOSALS[0].proposal
    assert submission.payload_sha256(seeded) == seed_digest(seeded)
    assert submission.proposal_payload(seeded) == seed_payload(seeded)
