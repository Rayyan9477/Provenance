"""Every read is scoped by identity, and every call is recorded.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero commit canon*: "Column
  ``agent_runs.tool_calls``; HTTP field ``mcp_tool_calls[]``.
  ``agent_runs.mcp_tool_calls`` is not a column name."
- ``docs/frontend/32_JUDGE_MODE.md`` section 6.1 - the fields a rendered call
  carries; section 6.5 - a denied call is rendered, never swallowed.
- ``docs/specs/15_API_SPEC.md`` section 9.9 - the closed allowlist of keys an
  entry may carry, mirrored by
  ``services/control_plane/app/api/schemas/internal.py::ToolCallRecord``.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.3``, ``T11.4``.

Why the recorder is a port
--------------------------
``pv_agent_reader`` holds no grant on ``agent_runs`` - that is the whole point
of the role - so the reader physically cannot write its own audit row. The
record therefore leaves this package through a narrow port and is persisted by
a principal that holds ``UPDATE`` on ``agent_runs``. Tested against a fake here;
the port's live implementation is the control plane's internal endpoint, which
is another agent's file.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.control_plane.app.mcp import records, statements, views
from services.control_plane.app.mcp.reader import AgentViewReader
from services.control_plane.app.mcp.scope import AgentScope
from services.control_plane.tests.mcp.conftest import (
    FakeConnection,
    FakeDatabaseError,
    FakeRecorder,
    factory_for,
)

pytestmark = pytest.mark.unit

CASE_CONTEXT = "read_case_context"


def _reader(
    connection: FakeConnection, recorder: FakeRecorder, scope: AgentScope
) -> AgentViewReader:
    return AgentViewReader(connect=factory_for(connection), recorder=recorder, scope=scope)


@pytest.fixture
def scope(scope_ids: tuple[uuid.UUID, uuid.UUID, uuid.UUID]) -> AgentScope:
    tenant_id, user_id, agent_run_id = scope_ids
    return AgentScope(tenant_id=tenant_id, user_id=user_id, agent_run_id=agent_run_id)


def test_the_scope_is_bound_from_identity_not_from_arguments(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    connection = FakeConnection(columns=("case_id",), rows=((uuid.uuid4(),),))
    _reader(connection, recorder, scope).read(CASE_CONTEXT)
    executed = connection.executed[-1]
    assert executed.params[0] == scope.tenant_id
    assert executed.params[1] == scope.user_id


def test_a_tool_cannot_be_handed_a_user_id(scope: AgentScope, recorder: FakeRecorder) -> None:
    """Not "is ignored" - refused. ``user_id`` is not a declared filter on any
    view, so the composer has nowhere to put it."""
    connection = FakeConnection(columns=("case_id",))
    reader = _reader(connection, recorder, scope)
    for tool_name in views.AGENT_VIEW_TOOLS:
        with pytest.raises(statements.UndeclaredFilterError) as excinfo:
            reader.read(tool_name, filters={"user_id": str(uuid.uuid4())})
        assert "user_id" in str(excinfo.value)
    assert connection.executed == [], "a refused call must not reach the database"


def test_every_statement_the_reader_issues_is_a_select(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    connection = FakeConnection(columns=("case_id",))
    reader = _reader(connection, recorder, scope)
    for tool_name in views.AGENT_VIEW_TOOLS:
        spec = views.AGENT_VIEW_TOOLS[tool_name]
        filters = {
            f.name: (uuid.uuid4() if f.kind == views.FilterKind.UUID else sorted(f.allowed)[0])
            for f in spec.filters
            if f.required
        }
        reader.read(tool_name, filters=filters)
    assert connection.statements, "nothing was executed; this assertion would be vacuous"
    for statement in connection.statements:
        assert statement.startswith("SELECT ")


def test_rows_are_returned_keyed_by_the_projected_columns(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    case_id = uuid.uuid4()
    connection = FakeConnection(columns=("case_id", "title"), rows=((case_id, "Deposit"),))
    result = _reader(connection, recorder, scope).read(CASE_CONTEXT)
    assert result.rows == ({"case_id": case_id, "title": "Deposit"},)
    assert result.row_count == 1
    assert result.denied is False


def test_each_call_is_recorded_with_the_fields_judge_mode_renders(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    connection = FakeConnection(columns=("case_id",), rows=((uuid.uuid4(),), (uuid.uuid4(),)))
    _reader(connection, recorder, scope).read(CASE_CONTEXT)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert recorder.agent_run_id == scope.agent_run_id
    assert call.sequence == 1
    assert call.tool_name == CASE_CONTEXT
    assert call.view_name == "agent_case_context_v1"
    assert call.sql_role == "pv_agent_reader"
    assert call.access_mode == "READ_ONLY"
    assert call.mcp_server == records.MCP_SERVER_NAME
    assert call.rows_returned == 2
    assert call.duration_ms >= 0
    assert call.denied is False
    assert call.filter_summary


def test_the_sequence_is_the_call_ordinal_within_the_run(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    connection = FakeConnection(columns=("case_id",))
    reader = _reader(connection, recorder, scope)
    reader.read(CASE_CONTEXT)
    reader.read("read_open_obligations")
    reader.read(CASE_CONTEXT)
    assert [call.sequence for call in recorder.calls] == [1, 2, 3]


def test_the_persisted_entry_carries_only_the_allowlisted_keys(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    """Section 9.9's allowlist, closed. ``returned_rows``, ``sql`` and ``result``
    are what it exists to keep out of the Memory Trace."""
    connection = FakeConnection(columns=("case_id",), rows=((uuid.uuid4(),),))
    _reader(connection, recorder, scope).read(CASE_CONTEXT)
    entry = recorder.calls[0].as_json_entry()
    assert set(entry) == set(records.TOOL_CALL_ALLOWLIST)
    assert "mcp_tool_calls" not in entry, "that is the HTTP field name, never a key"
    assert "sql" not in entry
    assert "rows" not in entry
    assert "returned_rows" not in entry


def test_the_column_is_tool_calls_and_the_http_field_is_mcp_tool_calls() -> None:
    """``CANONICAL_DECISIONS.md``: the pairing is fixed, and getting it backwards
    fails the DDL check rather than reading oddly."""
    assert records.TOOL_CALLS_COLUMN == "tool_calls"
    assert records.TOOL_CALLS_HTTP_FIELD == "mcp_tool_calls"
    assert records.TOOL_CALLS_COLUMN != "mcp_tool_calls"


def test_a_refusal_by_the_database_is_recorded_and_does_not_crash_the_run(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    """``G11.5``: "an agent attempt ... produces a trace entry with
    ``denied=true`` and the SQL error class, and the run does not crash"."""
    connection = FakeConnection(
        columns=("case_id",),
        error=FakeDatabaseError(
            "user pv_agent_reader does not have SELECT privilege on relation cases",
            sqlstate="42501",
        ),
    )
    result = _reader(connection, recorder, scope).read(CASE_CONTEXT)

    assert result.denied is True
    assert result.rows == ()
    assert result.denial_code == "42501"
    assert len(recorder.calls) == 1
    assert recorder.calls[0].denied is True
    assert recorder.calls[0].rows_returned is None


def test_a_read_only_transaction_refusal_is_a_denial_too(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    connection = FakeConnection(
        columns=("case_id",),
        error=FakeDatabaseError("cannot execute in a read-only transaction", sqlstate="25006"),
    )
    result = _reader(connection, recorder, scope).read(CASE_CONTEXT)
    assert result.denied is True
    assert result.denial_code == "25006"


def test_an_unrelated_database_error_is_recorded_and_re_raised(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    """A connection reset is not a permission boundary, and recording it as
    ``denied`` would put a refusal in the trace that never happened."""
    connection = FakeConnection(
        columns=("case_id",),
        error=FakeDatabaseError("connection reset by peer", sqlstate="08006"),
    )
    with pytest.raises(FakeDatabaseError):
        _reader(connection, recorder, scope).read(CASE_CONTEXT)
    assert len(recorder.calls) == 1
    assert recorder.calls[0].denied is False
    assert recorder.calls[0].rows_returned is None


def test_the_denial_sqlstates_are_the_ones_the_cluster_actually_raises() -> None:
    """Probed, not assumed: ``pv_agent_reader`` reading ``evidence_items``
    raises SQLSTATE ``42501`` on this cluster (recorded in
    ``tests/db/test_mcp_server.py``, which asserts it live)."""
    assert "42501" in records.DENIAL_SQLSTATES
    assert "25006" in records.DENIAL_SQLSTATES
    assert "08006" not in records.DENIAL_SQLSTATES


def test_an_unknown_tool_name_is_refused(scope: AgentScope, recorder: FakeRecorder) -> None:
    connection = FakeConnection(columns=("case_id",))
    with pytest.raises(KeyError):
        _reader(connection, recorder, scope).read("read_users")
    assert connection.executed == []
    assert recorder.calls == []


def test_the_result_is_truncated_at_the_limit_rather_than_silently(
    scope: AgentScope, recorder: FakeRecorder
) -> None:
    spec = views.AGENT_VIEW_TOOLS[CASE_CONTEXT]
    rows: tuple[tuple[Any, ...], ...] = tuple((uuid.uuid4(),) for _ in range(3))
    connection = FakeConnection(columns=("case_id",), rows=rows)
    result = _reader(connection, recorder, scope).read(CASE_CONTEXT, limit=3)
    assert result.truncated is True
    assert result.limit == 3
    assert spec.max_limit >= 3
