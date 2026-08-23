"""The session: ``pv_agent_reader``, read-only, and it says so before it reads.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``: "Configure the connection to
  ``pv_agent_reader`` only", and "the server runs without
  ``CRDB_MCP_ENABLE_WRITE_QUERIES``, forcing ``default_transaction_read_only=true``".
- ``docs/implementation/04_API_EVENTS_SECURITY.md`` section 21.
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts* (the SQL roles).

Read-only twice over, and the order matters
-------------------------------------------
The grant is the boundary; the read-only session is defence in depth. Both are
here, and the session assertions are deliberately *not* presented as the
security control - ``tests/db/test_mcp_boundary.py`` and
``tests/db/test_mcp_server.py`` carry that claim, against the cluster. What this
module proves is narrower and still worth proving: the server refuses to run as
any role other than ``pv_agent_reader``, and it establishes that before it
issues a single tool statement. A server that checked afterwards would have
already read the rows.

No DSN is constructed, printed or asserted on anywhere in this file.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.control_plane.app.mcp import session

pytestmark = pytest.mark.unit

#: A DSN-shaped string carrying this project's own documented placeholder
#: password. ``placeholder`` is a member of the ``pv-db-connection-url-with-password``
#: allowlist in ``.gitleaks.toml``, which matters: the first draft of this
#: constant used a plausible-looking password and ``gitleaks detect`` reported
#: it, which would have failed ``G0.3`` on a file whose entire purpose is to
#: prove that credentials do not leak. It resolves to nothing; it exists so the
#: refusal below has something it could have leaked.
DSN = "postgresql://pv_agent_reader:placeholder@localhost:26257/provenance_ci"


class _RoleCursor:
    def __init__(self, connection: _RoleConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _RoleCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: Any = None) -> _RoleCursor:
        self._connection.executed.append(query)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return (self._connection.current_user,)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []

    @property
    def description(self) -> tuple[Any, ...] | None:
        return ()


class _RoleConnection:
    def __init__(self, current_user: str) -> None:
        self.current_user = current_user
        self.executed: list[str] = []
        self.closed = False

    def cursor(self) -> _RoleCursor:
        return _RoleCursor(self)

    def close(self) -> None:
        self.closed = True


def _connector_for(connection: _RoleConnection) -> Any:
    def _connect(dsn: str) -> _RoleConnection:
        assert dsn == DSN
        return connection

    return _connect


def test_the_session_declares_the_role_it_is_allowed_to_be() -> None:
    assert session.AGENT_SQL_ROLE == "pv_agent_reader"
    assert session.ACCESS_MODE == "READ_ONLY"


def test_the_session_sets_the_transaction_read_only_before_reading() -> None:
    connection = _RoleConnection("pv_agent_reader")
    with session.agent_reader_session(DSN, connector=_connector_for(connection)):
        pass
    executed = [statement.lower() for statement in connection.executed]
    assert any("default_transaction_read_only" in statement for statement in executed)
    assert any("current_user" in statement for statement in executed)


def test_a_session_that_is_not_the_agent_role_is_refused() -> None:
    """The grant is the boundary, so a server that quietly ran as
    ``pv_app_reader_writer`` would still function - and would silently have every
    base table. Refusing to start is what makes that misconfiguration loud."""
    connection = _RoleConnection("pv_app_reader_writer")
    with (
        pytest.raises(session.WrongSqlRoleError) as excinfo,
        session.agent_reader_session(DSN, connector=_connector_for(connection)),
    ):
        pass
    message = str(excinfo.value)
    assert "pv_app_reader_writer" in message
    assert "pv_agent_reader" in message
    assert connection.closed is True


def test_a_refusal_never_prints_the_dsn() -> None:
    """``G0.3`` scans this repository with gitleaks and every gate transcript is
    committed. An exception carrying its own connection string is how a password
    reaches a log file."""
    connection = _RoleConnection("pv_kernel_writer")
    with (
        pytest.raises(session.WrongSqlRoleError) as excinfo,
        session.agent_reader_session(DSN, connector=_connector_for(connection)),
    ):
        pass
    rendered = f"{excinfo.value!r} {excinfo.value!s}"
    assert "placeholder" not in rendered
    assert "postgresql://" not in rendered
    assert DSN not in rendered


def test_the_session_is_closed_when_the_block_ends() -> None:
    connection = _RoleConnection("pv_agent_reader")
    with session.agent_reader_session(DSN, connector=_connector_for(connection)):
        pass
    assert connection.closed is True
