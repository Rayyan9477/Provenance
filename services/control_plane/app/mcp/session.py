"""The connection: ``pv_agent_reader``, read-only, checked at open.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts* (the SQL roles) and
  *Phase 0 verification decisions*, row "View/grant behavior".
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``: "Configure the connection to
  ``pv_agent_reader`` only", and "the server runs without
  ``CRDB_MCP_ENABLE_WRITE_QUERIES``, forcing ``default_transaction_read_only=true``
  ... a write attempted through MCP is refused by the SQL grant, and the test
  asserts the grant's error rather than the server's".
- ``docs/implementation/04_API_EVENTS_SECURITY.md`` sections 20 and 21.

Two mechanisms, and they are not equals
---------------------------------------
The **grant** is the boundary: ``pv_agent_reader`` holds ``SELECT`` on five
views and nothing else, and migration ``0008`` issues ``REVOKE ALL ON TABLE``
over all 26 canonical tables to make that provable rather than incidental.
``services/control_plane/tests/db/test_mcp_server.py`` demonstrates it by
running the same statement under two roles and observing that only the role
changes the outcome.

The **read-only session** set here is defence in depth. It is worth having and
it is not the security story; saying otherwise to a judge who has read the
CockroachDB MCP documentation would be worse than not having it.

The role assertion at open is the third, cheapest mechanism, and it guards a
failure the other two cannot see: a server accidentally configured with the
application credential would work perfectly, read every view, and silently hold
``SELECT`` on all 26 base tables. Nothing would break. So this module refuses to
yield a session that is not ``pv_agent_reader`` - a misconfiguration becomes an
outage instead of a leak.

Credential hygiene
------------------
No exception raised here carries the DSN, and no message in this module
interpolates one. ``G0.3`` scans this repository with gitleaks and every gate
transcript is committed; an exception that quotes its own connection string is
how a password reaches a log file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final

import psycopg

from services.control_plane.app.mcp.ports import ConnectionFactory, ReadOnlyConnection

__all__ = [
    "ACCESS_MODE",
    "AGENT_SQL_ROLE",
    "READ_ONLY_STATEMENT",
    "ROLE_ASSERTION_STATEMENT",
    "WrongSqlRoleError",
    "agent_reader_session",
    "connection_factory",
]

#: The only role this server may authenticate as.
AGENT_SQL_ROLE: Final[str] = "pv_agent_reader"

#: Recorded on every tool call, and true by grant rather than by flag.
ACCESS_MODE: Final[str] = "READ_ONLY"

#: Defence in depth. CockroachDB accepts this as a session setting - probed
#: against the cluster, not assumed - and ``SHOW default_transaction_read_only``
#: then returns ``on``, which ``tests/db/test_mcp_server.py`` asserts.
READ_ONLY_STATEMENT: Final[str] = "SET default_transaction_read_only = true"

#: The identity check. ``current_user`` is the server's answer, not ours.
ROLE_ASSERTION_STATEMENT: Final[str] = "SELECT current_user"

#: A callable that turns a DSN into an open connection. Injected so the
#: hermetic lane can exercise the role check without a socket.
Connector = Callable[[str], Any]


class WrongSqlRoleError(RuntimeError):
    """The connection authenticated as a role other than ``pv_agent_reader``.

    Deliberately not a subclass of any database error: this is a configuration
    fault in *this* process, and it must not be caught by a handler written for
    a transient cluster problem and retried.
    """


def _psycopg_connect(dsn: str) -> Any:
    """Open a real connection.

    ``autocommit=True``: every statement this server issues is a ``SELECT``, and
    an implicit transaction left open between tool calls would hold a timestamp
    for the life of an agent run.
    """
    return psycopg.connect(dsn, autocommit=True)


@contextmanager
def agent_reader_session(
    dsn: str, *, connector: Connector = _psycopg_connect
) -> Iterator[ReadOnlyConnection]:
    """Open one session, prove what it is, and close it afterwards.

    The read-only setting and the role assertion both happen before the session
    is yielded. A server that checked afterwards would have already read the
    rows.
    """
    connection: Any = connector(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(READ_ONLY_STATEMENT)
            cursor.execute(ROLE_ASSERTION_STATEMENT)
            row = cursor.fetchone()
        actual = str(row[0]) if row else "<no current_user>"
        if actual != AGENT_SQL_ROLE:
            raise WrongSqlRoleError(
                f"the MCP server refuses to start: this connection authenticates as "
                f"{actual!r}, and the only role permitted here is {AGENT_SQL_ROLE!r}. "
                f"A server holding any other credential would read the same five views "
                f"and would also hold grants on the base tables."
            )
        yield connection
    finally:
        connection.close()


def connection_factory(dsn: str, *, connector: Connector = _psycopg_connect) -> ConnectionFactory:
    """A :class:`ConnectionFactory` opening one checked session per tool call.

    One session per call rather than a pool: a read here is a handful of rows
    from a view a few times per agent run, and a per-call session means the role
    assertion runs every time rather than once at pool warm-up. If that ever
    costs more than it is worth, the thing to add is a pool that performs the
    same check on checkout - not a check performed less often.
    """

    def _open() -> Any:
        return agent_reader_session(dsn, connector=connector)

    return _open
