"""The MCP server against the live cluster: the boundary is the grant.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 14, ``T11.1``-``T11.4``.
- ``docs/quality/23_PHASE_GATES.md`` section 17 - ``G11.1``, ``G11.2``, ``G11.4``,
  ``G11.5``.
- ``docs/CANONICAL_DECISIONS.md`` -> *Phase 0 verification decisions*, row
  "View/grant behavior": "MCP role reads views and is denied base tables. ...
  Stop Phase 11; do not weaken grants."
- ``db/migrations/versions/0008_events_infrastructure.py`` - the view definitions
  and ``REVOKE ALL ON TABLE <the 26> FROM pv_agent_reader``.

What this file proves that the hermetic lane cannot
---------------------------------------------------
``services/control_plane/tests/mcp/`` proves the server *composes* only
scoped ``SELECT`` statements against five views. That is a property of this
code, and code can be changed. This file proves the far-side property: even a
server that tried to read a base table would be refused, because the refusal is
issued by CockroachDB against ``pv_agent_reader``'s grants and not by anything
in this repository.

:func:`test_the_denial_comes_from_the_database_and_not_from_this_code` is the
one that makes that distinction checkable rather than asserted. It runs the
*same statement text* twice: once on the server's own connection, where it is
refused, and once on a connection holding the ``SELECT`` grant, where it
succeeds. If the refusal came from a guard in this package the statement would
fail on both. The only variable between the two runs is the SQL role.

``tests/db/conftest.py`` owns ``role_dsn``; every DSN it returns is a
``MaskedDsn`` and nothing here prints, returns or asserts on one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest

from provenance_domain.enums import AgentSafeView
from services.control_plane.app.mcp import records, session, statements, views
from services.control_plane.app.mcp.reader import AgentViewReader
from services.control_plane.app.mcp.scope import AgentScope

pytestmark = pytest.mark.db

#: A scope that owns nothing in ``provenance_ci``. Every read below is expected
#: to return zero rows; what is under test is *who may issue the statement* and
#: *whether the statement is well-formed against the real view*, neither of
#: which needs a seeded corpus.
EMPTY_SCOPE = AgentScope(
    tenant_id=uuid.UUID("00000000-0000-4000-8000-0000000000a1"),
    user_id=uuid.UUID("00000000-0000-4000-8000-0000000000a2"),
    agent_run_id=uuid.UUID("00000000-0000-4000-8000-0000000000a3"),
)

#: The statement the boundary is demonstrated with. A base table, named by the
#: migration's own ``REVOKE`` list.
BASE_TABLE_READ = "SELECT id FROM evidence_items LIMIT 1"


class _Recorder:
    """Collects records instead of writing them.

    ``pv_agent_reader`` holds no grant on ``agent_runs`` - deliberately - so the
    reader cannot persist its own audit row and the live persistence path is the
    control plane's internal endpoint. What is under test here is that the
    reader *produces* the record for every live call.
    """

    def __init__(self) -> None:
        self.calls: list[records.ToolCallRecord] = []

    def record(self, agent_run_id: uuid.UUID, call: records.ToolCallRecord) -> None:
        self.calls.append(call)


@pytest.fixture
def agent_dsn(migrated: str, role_dsn: Callable[[str], str]) -> str:
    """The ``pv_agent_reader`` DSN, against the migrated ``provenance_ci``."""
    return role_dsn("pv_agent_reader")


def _required_filters(spec: views.AgentViewTool) -> dict[str, object]:
    return {
        f.name: (uuid.uuid4() if f.kind == views.FilterKind.UUID else sorted(f.allowed)[0])
        for f in spec.filters
        if f.required
    }


# --------------------------------------------------------------------------
# The connection the server actually uses
# --------------------------------------------------------------------------


def test_the_server_connects_as_pv_agent_reader_and_nothing_else(agent_dsn: str) -> None:
    """``T11.2``: "Configure the connection to ``pv_agent_reader`` only"."""
    with session.agent_reader_session(agent_dsn) as opened, opened.cursor() as cur:
        cur.execute("SELECT current_user", ())
        row = cur.fetchall()[0]
    assert row[0] == session.AGENT_SQL_ROLE == "pv_agent_reader"


def test_the_server_session_is_read_only(agent_dsn: str) -> None:
    """Defence in depth, and labelled as such: the grants are the boundary."""
    with session.agent_reader_session(agent_dsn) as opened, opened.cursor() as cur:
        cur.execute("SHOW default_transaction_read_only", ())
        row = cur.fetchall()[0]
    assert str(row[0]).lower() in {"on", "true"}


def test_a_dsn_for_any_other_role_is_refused_at_open(role_dsn: Callable[[str], str]) -> None:
    """A server pointed at the app credential would work perfectly and would
    silently hold ``SELECT`` on all 26 tables. Refusing to open is what turns
    that misconfiguration into an outage instead of a leak."""
    with (
        pytest.raises(session.WrongSqlRoleError) as excinfo,
        session.agent_reader_session(role_dsn("pv_app_reader_writer")),
    ):
        pass
    assert "pv_app_reader_writer" in str(excinfo.value)
    assert "postgres" not in str(excinfo.value)


# --------------------------------------------------------------------------
# The boundary, demonstrated by refusal, from the far side
# --------------------------------------------------------------------------


def test_the_server_connection_is_refused_a_base_table(agent_dsn: str) -> None:
    """``G11.2``. The server's *own* connection attempts the read and is denied.

    Not "the server declines to issue the statement" - the statement is issued.
    A boundary enforced in application code is a convention.
    """
    with (
        session.agent_reader_session(agent_dsn) as opened,
        opened.cursor() as cur,
        pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo,
    ):
        cur.execute(BASE_TABLE_READ, ())
    assert excinfo.value.sqlstate == "42501"
    assert "pv_agent_reader" in str(excinfo.value)
    assert "evidence_items" in str(excinfo.value)


def test_the_denial_comes_from_the_database_and_not_from_this_code(
    agent_dsn: str, role_dsn: Callable[[str], str]
) -> None:
    """The same statement text, two roles, two outcomes.

    This is the test that distinguishes "the database refuses" from "our code
    refuses". A guard in this package would fail both runs; a grant fails
    exactly one. The SQLSTATE is CockroachDB's ``42501``
    (``insufficient_privilege``) and the message names the role - neither is a
    string this repository produces.
    """
    with (
        session.agent_reader_session(agent_dsn) as opened,
        opened.cursor() as cur,
        pytest.raises(psycopg.errors.InsufficientPrivilege) as denied,
    ):
        cur.execute(BASE_TABLE_READ, ())

    with psycopg.connect(role_dsn("pv_app_reader_writer")) as granted, granted.cursor() as cur:
        cur.execute(BASE_TABLE_READ)
        cur.fetchall()  # no exception: the statement itself is well-formed

    assert denied.value.sqlstate == "42501"
    assert denied.value.diag.message_primary is not None
    assert "pv_agent_reader" in str(denied.value.diag.message_primary)


def test_the_agent_role_holds_no_grant_on_any_base_table(db_connection) -> None:
    """The catalogue half, kept beside the refusal half.

    ``V9``/``G11.1``. It is the weaker of the two - a catalogue can be read
    correctly while a view still executes with the wrong privileges - which is
    why it is not the only assertion in this file.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND table_schema = 'public'",
            (session.AGENT_SQL_ROLE,),
        )
        held = {(str(row[0]), str(row[1])) for row in cur.fetchall()}
    view_names = {member.value for member in AgentSafeView}
    leaked = {entry for entry in held if entry[0] not in view_names}
    assert leaked == set(), f"pv_agent_reader can reach {sorted(leaked)}"
    assert {name for name, _ in held} == view_names


def test_a_write_through_the_server_connection_is_refused(agent_dsn: str) -> None:
    """Read-only in every sense: the role has no write grant, so this fails on
    the grant even before the read-only session is consulted."""
    with (
        session.agent_reader_session(agent_dsn) as opened,
        opened.cursor() as cur,
        pytest.raises(psycopg.Error) as excinfo,
    ):
        cur.execute("INSERT INTO claims (id) VALUES ('00000000-0000-0000-0000-000000000000')", ())
    assert excinfo.value.sqlstate in records.DENIAL_SQLSTATES


# --------------------------------------------------------------------------
# The five tools against the five real views
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_every_composed_statement_runs_against_the_real_view(
    agent_dsn: str, tool_name: str
) -> None:
    """Every projected column and every filter column is checked against the
    deployed view definition rather than against this repository's idea of it.

    A column that migration ``0008`` does not project raises ``42703``
    ``UndefinedColumn`` here, which is the failure mode a registry transcribed
    from memory produces.
    """
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    statement = statements.compose(
        spec, scope=EMPTY_SCOPE, filters=_required_filters(spec), limit=1
    )
    with session.agent_reader_session(agent_dsn) as opened, opened.cursor() as cur:
        cur.execute(statement.sql, statement.params)
        rows = cur.fetchall()
        returned = [column.name for column in (cur.description or ())]
    assert rows == []
    assert returned == list(spec.columns)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_every_declared_filter_column_exists_on_the_deployed_view(
    db_connection, tool_name: str
) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (spec.view_name,),
        )
        deployed = {str(row[0]) for row in cur.fetchall()}
    assert deployed, f"{spec.view_name} does not exist"
    assert set(spec.columns) <= deployed, sorted(set(spec.columns) - deployed)
    assert {f.column for f in spec.filters} <= deployed


def test_the_reader_records_a_tool_call_for_every_live_read(agent_dsn: str) -> None:
    """``T11.3``: every tool call is recorded so the run is auditable."""
    recorder = _Recorder()
    reader = AgentViewReader(
        connect=session.connection_factory(agent_dsn), recorder=recorder, scope=EMPTY_SCOPE
    )
    for tool_name in sorted(views.AGENT_VIEW_TOOLS):
        spec = views.AGENT_VIEW_TOOLS[tool_name]
        result = reader.read(tool_name, filters=_required_filters(spec), limit=1)
        assert result.denied is False
        assert result.rows == ()

    assert [call.sequence for call in recorder.calls] == [1, 2, 3, 4, 5]
    assert {call.view_name for call in recorder.calls} == {member.value for member in AgentSafeView}
    assert {call.sql_role for call in recorder.calls} == {"pv_agent_reader"}
    assert {call.access_mode for call in recorder.calls} == {"READ_ONLY"}
    assert {call.denied for call in recorder.calls} == {False}
    for call in recorder.calls:
        assert set(call.as_json_entry()) == set(records.TOOL_CALL_ALLOWLIST)
