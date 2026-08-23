"""End to end through the MCP layer: a client call becomes one scoped statement.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``-``T11.4``.
- ``docs/frontend/32_JUDGE_MODE.md`` section 6.5 - a denied call is visible, not
  rendered as an empty success.

Why this file exists beside ``test_tool_surface``
--------------------------------------------------
``test_tool_surface`` reads what the server *advertises*. This file invokes the
tools the way a client does - through :meth:`FastMCP.call_tool`, by name, with a
JSON-shaped argument dict - and follows the call all the way to the statement
the fake connection was handed. A surface that advertises the right schema and a
wiring that binds the wrong scope would pass the first file and fail this one.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from services.control_plane.app.mcp import server as mcp_server
from services.control_plane.app.mcp.reader import AgentViewReader
from services.control_plane.app.mcp.scope import AgentScope
from services.control_plane.app.mcp.statements import UndeclaredFilterError
from services.control_plane.tests.mcp.conftest import (
    FakeConnection,
    FakeDatabaseError,
    FakeRecorder,
    factory_for,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
SCOPE = AgentScope(tenant_id=TENANT, user_id=USER, agent_run_id=RUN)

#: A case the caller does not own. The point is that naming it changes nothing
#: about *whose* rows are read, because the owner is not an argument.
FOREIGN_CASE = uuid.UUID("44444444-4444-4444-8444-444444444444")


def _server(connection: FakeConnection, recorder: FakeRecorder) -> Any:
    reader = AgentViewReader(connect=factory_for(connection), recorder=recorder, scope=SCOPE)
    return mcp_server.build_mcp_server(reader=reader)


async def test_calling_a_tool_issues_one_scoped_select_and_records_it() -> None:
    case_id = uuid.uuid4()
    connection = FakeConnection(columns=("case_id", "title"), rows=((case_id, "Deposit"),))
    recorder = FakeRecorder()
    server = _server(connection, recorder)

    _, structured = await server.call_tool("read_case_context", {"limit": 5})

    executed = connection.executed[-1]
    assert executed.sql.startswith("SELECT ")
    assert "FROM agent_case_context_v1" in executed.sql
    assert executed.params[0] == TENANT
    assert executed.params[1] == USER
    assert executed.params[-1] == 5

    assert structured["view_name"] == "agent_case_context_v1"
    assert structured["sql_role"] == "pv_agent_reader"
    assert structured["access_mode"] == "READ_ONLY"
    assert structured["denied"] is False
    assert structured["rows"] == [{"case_id": str(case_id), "title": "Deposit"}]

    assert len(recorder.calls) == 1
    assert recorder.calls[0].view_name == "agent_case_context_v1"


async def test_a_client_supplied_user_id_is_rejected_by_the_tool_layer() -> None:
    """The argument does not exist, so the call fails validation before any of
    this package's code runs. Nothing reaches the database, and the scope of the
    read could not have been changed even if it had."""
    connection = FakeConnection(columns=("case_id",))
    server = _server(connection, FakeRecorder())

    with pytest.raises(UndeclaredFilterError) as excinfo:
        await server.call_tool("read_case_context", {"user_id": str(uuid.uuid4()), "limit": 5})
    assert "user_id" in str(excinfo.value)
    assert connection.executed == []


async def test_naming_another_users_case_still_reads_only_the_callers_rows() -> None:
    """``case_id`` is a legitimate filter, and it is *narrowing only*.

    The scope predicate is composed first and unconditionally, so a case id
    belonging to somebody else produces a statement that can only ever return
    zero rows - not one that returns theirs.
    """
    connection = FakeConnection(columns=("case_id",))
    server = _server(connection, FakeRecorder())

    await server.call_tool("read_case_context", {"case_id": str(FOREIGN_CASE)})

    executed = connection.executed[-1]
    assert executed.params[0] == TENANT
    assert executed.params[1] == USER
    assert executed.params[2] == FOREIGN_CASE
    assert executed.sql.count("tenant_id = %s") == 1
    assert executed.sql.count("user_id = %s") == 1


async def test_a_denied_call_is_returned_as_a_denial_not_as_an_empty_success() -> None:
    """``G11.5``. An agent that saw "no results" where the database said "no"
    would have no way to tell a boundary from an empty case."""
    connection = FakeConnection(
        columns=("case_id",),
        error=FakeDatabaseError(
            "user pv_agent_reader does not have SELECT privilege on relation cases",
            sqlstate="42501",
        ),
    )
    recorder = FakeRecorder()
    server = _server(connection, recorder)

    _, structured = await server.call_tool("read_case_context", {})

    assert structured["denied"] is True
    assert structured["denial_sqlstate"] == "42501"
    assert structured["rows"] == []
    assert recorder.calls[0].denied is True


async def test_a_tool_without_its_required_anchor_is_refused() -> None:
    connection = FakeConnection(columns=("belief_id",))
    server = _server(connection, FakeRecorder())
    with pytest.raises(ToolError):
        await server.call_tool("read_belief_lineage", {})
    assert connection.executed == []


async def test_there_is_no_sixth_tool_to_call() -> None:
    connection = FakeConnection(columns=("case_id",))
    server = _server(connection, FakeRecorder())
    for name in ("query", "run_sql", "read_users", "read_evidence_items"):
        with pytest.raises(ToolError):
            await server.call_tool(name, {})
    assert connection.executed == []
