"""Section 9.9 bound: the run is settled and the capability is burned.

Why this is on the critical path rather than a nicety
------------------------------------------------------
Section 9.9's closing sentence is "any subsequent call with this id returns
``403 CAPABILITY_CONSUMED``", and that is only true if something moves
``agent_runs.status`` off ``RUNNING``. The capability read derives liveness
from that column (see ``adapters/directory.py``), so while section 9.9 was
unbound an agent's token stayed live until ``expires_at`` whatever the run did.

Why the register's stated blocker did not hold
------------------------------------------------
``internal.complete_agent_run`` was declared as waiting on
``app/observability``, on the grounds that writing ``tool_calls``,
``model_calls`` and ``capability_status`` "from a stub would fabricate a
trace". Two facts settle that the other way:

* Those three columns are **caller-reported by construction**, and are
  disclosed as such -- ``frontend/32_JUDGE_MODE.md`` section 6.4, and migration
  ``0008``'s own column comment: "Caller-reported by the agent runtime over
  ``POST /internal/v1/agent-runs/{id}/complete``". Persisting what the caller
  reported is the opposite of fabricating it; the fabrication would be a server
  that *invented* the arrays.
* Each entry is a **closed** model (``ToolCallRecord``, ``ModelCallRecord``), so
  returned rows and SQL text cannot be smuggled into the Memory Trace through a
  key nobody rejected. That is the guard section 9.9 asks for and it is in the
  schema already.

What ``app/observability`` is still needed for is the **span** DAG behind
``read.get_trace`` and ``read.memory_trace``, and those two stay unbound.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit

RUN_ID = uuid.UUID("018f9e90-0000-7000-8000-000000000001")
TENANT_ID = uuid.UUID("018f7a00-0000-7000-8000-00000000abcd")
USER_ID = uuid.UUID("018f7a01-0000-7000-8000-00000000abcd")
CASE_ID = uuid.UUID("018f8a10-4c22-7f31-9b7d-2ac1e5f09b41")

STARTED = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 18, 12, 0, 9, 890000, tzinfo=UTC)


def run_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": RUN_ID,
        "trace_id": uuid.UUID("018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90"),
        "graph_name": "ingestion",
        "graph_version": "1.0.0",
        "model_route": {"tier_e": "gemini-3.5-flash-lite", "tier_r": "gemini-3.7-flash"},
        "memory_mode": "ON",
        "is_counterfactual": False,
        "status": "RUNNING",
        "started_at": STARTED,
        "finished_at": None,
        "expires_at": NOW,
        "input_artifact_id": None,
        "allowed_case_ids": [str(CASE_ID)],
        "retrieval_candidate_count": None,
        "error_code": None,
        "tool_calls": None,
        "model_calls": None,
        "capability_status": None,
    }
    row.update(overrides)
    return row


def completion(**overrides: Any) -> Any:
    from services.control_plane.app.api.schemas.internal import AgentRunCompleteRequest

    body: dict[str, Any] = {
        "status": "SUCCEEDED",
        "tool_calls": [
            {
                "sequence": 1,
                "mcp_server": "cockroachdb-mcp",
                "tool_name": "query_agent_case_context",
                "view_name": "agent_case_context_v1",
                "sql_role": "pv_agent_reader",
                "access_mode": "READ_ONLY",
                "filter_summary": "user_id = <run user>",
                "rows_returned": 1,
                "duration_ms": 44,
                "denied": False,
            }
        ],
        "model_calls": [
            {
                "node": "extract_structured_evidence",
                "tier": "E",
                "model_id": "gemini-3.5-flash-lite",
                "prompt_version": "pv-extract-1.1.0",
                "input_tokens": 3184,
                "output_tokens": 742,
                "repair_attempts": 0,
            }
        ],
    }
    body.update(overrides)
    return AgentRunCompleteRequest.model_validate(body)


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


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
        self._conn.statements.append((sql, params))
        if "FROM agent_runs" in sql:
            self._conn.calls.append("read_agent_run")
            row = self._conn.row
            if row is None:
                self.description, self._rows, self.rowcount = (), [], 0
                return
            self.description = tuple(_Column(name) for name in row)
            self._rows = [tuple(row.values())]
            self.rowcount = 1
            return
        self._conn.calls.append("settle_agent_run")
        self.description, self._rows = (), []
        self.rowcount = self._conn.settled_rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self, row: dict[str, Any] | None, settled_rows: int = 1) -> None:
        self.row = row
        self.settled_rows = settled_rows
        self.calls: list[str] = []
        self.statements: list[tuple[str, Any]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    async def rollback(self) -> None:  # pragma: no cover
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


def build_port(conn: FakeConnection) -> Any:
    from services.control_plane.app.actions import ActionPolicy
    from services.control_plane.app.api.adapters.internal import KernelInternalPort

    return KernelInternalPort(
        FakeSource(conn),
        kernel_pool=None,
        read=object(),
        policy=ActionPolicy(allowlist=frozenset(), execution_mode="DRY_RUN", recipient_mode="SINK"),
        sink=object(),
        clock=lambda: NOW,
    )


def settle_params(conn: FakeConnection) -> dict[str, Any]:
    return next(p for sql, p in conn.statements if "UPDATE agent_runs" in sql)


# ==========================================================================
# 1. The statement, and what it is allowed to be
# ==========================================================================


def test_settling_a_run_is_not_a_canonical_write() -> None:
    """``agent_runs`` is deliberately absent from ``CANONICAL_TABLES``.

    The Kernel holds only ``SELECT`` on it, so there is no Kernel-only write to
    protect and an app ``UPDATE`` here is not an exception to write rule ``W2``
    -- it is outside its scope. Asserted rather than assumed, because if that
    ever changed this module would become a second canonical writer and
    ``write_path_lint`` would be the thing that noticed.
    """
    from pathlib import Path

    from tools import write_path_lint

    assert "agent_runs" not in write_path_lint.CANONICAL_TABLES

    module = Path(__file__).resolve().parents[2] / "app" / "observability" / "runs.py"
    result = write_path_lint.scan_source(
        module.read_text(encoding="utf-8"), "services/control_plane/app/observability/runs.py"
    )
    assert result.violations == [], [str(v) for v in result.violations]


def test_the_statement_guards_on_the_run_still_being_open() -> None:
    """A settle that matched a finished run would rewrite a closed trace.

    ``status = 'RUNNING'`` in the predicate is what makes the second call a
    zero-row update rather than a silent overwrite of the first call's
    ``tool_calls``. The capability layer refuses the second call first; this is
    the guard that does not depend on it.
    """
    from services.control_plane.app.observability import runs

    assert "UPDATE agent_runs" in runs.SETTLE_AGENT_RUN_SQL
    assert "status = 'RUNNING'" in runs.SETTLE_AGENT_RUN_SQL
    assert "tenant_id = %(tenant_id)s" in runs.SETTLE_AGENT_RUN_SQL
    assert "user_id = %(user_id)s" in runs.SETTLE_AGENT_RUN_SQL


# ==========================================================================
# 2. What is written, and what is not invented
# ==========================================================================


async def test_the_run_is_settled_with_what_the_caller_reported() -> None:
    conn = FakeConnection(run_row())
    port = build_port(conn)

    row = await port.complete_agent_run(binding(), completion())

    assert conn.calls == ["read_agent_run", "settle_agent_run"], conn.calls
    params = settle_params(conn)
    assert params["status"] == "SUCCEEDED"
    assert params["finished_at"] == NOW
    assert params["tool_calls"].obj[0]["tool_name"] == "query_agent_case_context"
    assert params["model_calls"].obj[0]["prompt_version"] == "pv-extract-1.1.0"
    assert row["agent_run_id"] == str(RUN_ID)
    assert row["status"] == "SUCCEEDED"
    assert row["capability_status"] == "CONSUMED"
    assert row["duration_ms"] == 9890


async def test_the_model_calls_column_is_the_column_the_disclosure_reads() -> None:
    """``agent_runs.model_calls[].prompt_version`` is the field
    ``internal.create_action_intent`` is still waiting on, and this is the only
    writer of it. Persisted exactly as reported, keys and all: a server that
    filled ``duration_ms`` or ``started_at`` with a default would put invented
    measurements into the Memory Trace.
    """
    conn = FakeConnection(run_row())
    port = build_port(conn)

    await port.complete_agent_run(binding(), completion())

    entry = settle_params(conn)["model_calls"].obj[0]
    assert set(entry) == {
        "node",
        "tier",
        "model_id",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "repair_attempts",
    }, entry


async def test_an_empty_report_is_written_as_empty_not_as_absent() -> None:
    """``D-00-005``, in the direction that is usually got wrong.

    A run that bound no MCP tool reported ``[]``, and ``[]`` is a *measurement*.
    Leaving the column ``NULL`` would render in Judge Mode as "the tool calls
    are unknown", which is a different claim and the one that makes a reader
    stop trusting the panel.
    """
    conn = FakeConnection(run_row())
    port = build_port(conn)

    await port.complete_agent_run(binding(), completion(tool_calls=[], model_calls=[]))

    params = settle_params(conn)
    assert params["tool_calls"].obj == []
    assert params["model_calls"].obj == []


async def test_a_counterfactual_run_records_that_it_held_no_proposal_tool() -> None:
    """``ck_agent_runs_counterfactual_toolless`` refuses the row otherwise.

    ``capability_status->>'proposal_tool_bound'`` must be false for a
    counterfactual run, and it is not a caller-reported field: the server
    decides, and ``submit_proposal`` enforces the same fact by refusing a
    counterfactual run outright. One rule, written down where the CHECK can see
    it.
    """
    conn = FakeConnection(run_row(is_counterfactual=True, memory_mode="OFF"))
    port = build_port(conn)

    await port.complete_agent_run(binding(), completion())

    status = settle_params(conn)["capability_status"].obj
    assert status["proposal_tool_bound"] is False

    conn = FakeConnection(run_row())
    await build_port(conn).complete_agent_run(binding(), completion())
    assert settle_params(conn)["capability_status"].obj["proposal_tool_bound"] is True


# ==========================================================================
# 3. Refusals
# ==========================================================================


async def test_a_failed_run_without_an_error_code_is_refused_by_the_schema() -> None:
    """``ck_agent_runs_error`` is ``status NOT IN ('FAILED','ABANDONED') OR
    error_code IS NOT NULL``. Refusing at the schema turns a ``CheckViolation``
    -- a 500 naming nothing -- into a ``422`` naming the field."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        completion(status="FAILED")
    with pytest.raises(ValidationError):
        completion(status="ABANDONED")
    # And the legal shape still constructs, so the guard above is not vacuous.
    assert completion(status="FAILED", error_code="GRAPH_NODE_RAISED").error_code


async def test_a_run_that_does_not_resolve_is_absent_rather_than_settled() -> None:
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(None)
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.complete_agent_run(binding(), completion())

    assert caught.value.code.value == "AGENT_RUN_NOT_FOUND"
    assert conn.calls == ["read_agent_run"]


async def test_a_second_settle_is_refused_rather_than_reported_as_success() -> None:
    """Zero rows updated means the run was already terminal.

    Returning ``200`` would tell the runtime its trace had been recorded when
    the row still holds the first call's arrays -- and the response would carry
    a ``finished_at`` no row has.
    """
    from services.control_plane.app.api.errors import ApiError

    conn = FakeConnection(run_row(), settled_rows=0)
    port = build_port(conn)

    with pytest.raises(ApiError) as caught:
        await port.complete_agent_run(binding(), completion())

    assert caught.value.code.value == "CAPABILITY_CONSUMED"


# ==========================================================================
# 4. The register and the tree agree
# ==========================================================================


def test_complete_agent_run_is_no_longer_declared_unbound() -> None:
    from services.control_plane.app.api import adapters

    assert "internal.complete_agent_run" not in adapters.UNBOUND
    source = inspect.getsource(adapters.KernelInternalPort.complete_agent_run)
    assert 'unbound("internal.complete_agent_run")' not in source


def test_the_two_trace_reads_are_still_unbound_and_say_why() -> None:
    """The blocker that survives.

    Settling a run records what the caller reported. It does not produce the
    span DAG section 8.28 builds from seventeen closed node types, so
    ``read.get_trace`` and ``read.memory_trace`` are unchanged -- and this
    asserts that binding 9.9 was not read as having closed them.
    """
    from services.control_plane.app.api import adapters

    for name in ("read.get_trace", "read.memory_trace"):
        assert name in adapters.UNBOUND
        assert "trace assembler" in adapters.UNBOUND[name]
