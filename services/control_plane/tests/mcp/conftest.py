"""Fakes for the hermetic MCP lane (``T11.2``-``T11.4``).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 14, tasks ``T11.2``-``T11.4``.
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts* (the five view names)
  and *Hero commit canon* (MCP tool-call naming).
- ``docs/frontend/32_JUDGE_MODE.md`` section 6 - what a tool-call record must
  carry.

Why fakes and not a database
----------------------------
Two different claims need two different lanes. *"The composed statement never
carries a caller value"* and *"every call is recorded"* are properties of this
package and are provable with no socket, so they live here under the ``unit``
marker and the root conftest's network guard. *"The database refuses the base
table"* is a property of the cluster and is unprovable without one, so it lives
in ``services/control_plane/tests/db/test_mcp_server.py``. Proving the second
claim with a fake would be proving that a fake was written to raise.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from services.control_plane.app.mcp.reader import AgentViewReader
from services.control_plane.app.mcp.scope import AgentScope

pytestmark = pytest.mark.unit


class FakeDatabaseError(Exception):
    """Shaped like a ``psycopg.Error``: it carries a ``sqlstate``.

    The reader classifies refusals by SQLSTATE rather than by exception class,
    so that it never has to import ``psycopg`` and so that this fake is a
    faithful stand-in rather than a special case.
    """

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class FakeColumn:
    """One entry of DB-API ``cursor.description``."""

    name: str


@dataclass(frozen=True, slots=True)
class ExecutedStatement:
    """What the fake cursor was actually asked to run."""

    sql: str
    params: tuple[Any, ...]


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self._description: tuple[FakeColumn, ...] | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: Any = None) -> FakeCursor:
        self._connection.executed.append(ExecutedStatement(sql=query, params=tuple(params or ())))
        if self._connection.error is not None:
            raise self._connection.error
        self._description = tuple(FakeColumn(name) for name in self._connection.columns)
        return self

    @property
    def description(self) -> tuple[FakeColumn, ...] | None:
        return self._description

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._connection.rows)


@dataclass
class FakeConnection:
    """A connection that records every statement it is handed."""

    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    error: Exception | None = None
    executed: list[ExecutedStatement] = field(default_factory=list)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @property
    def statements(self) -> list[str]:
        return [executed.sql for executed in self.executed]


def factory_for(connection: FakeConnection) -> Any:
    """A :class:`ConnectionFactory` yielding *connection*."""

    @contextmanager
    def _connect() -> Iterator[FakeConnection]:
        yield connection

    return _connect


class FakeRecorder:
    """Collects what would have been appended to ``agent_runs.tool_calls``."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def record(self, agent_run_id: uuid.UUID, call: Any) -> None:
        self.agent_run_id = agent_run_id
        self.calls.append(call)


@pytest.fixture
def recorder() -> FakeRecorder:
    return FakeRecorder()


@pytest.fixture
def scope_ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """``(tenant_id, user_id, agent_run_id)`` - fixed, so a leak is greppable."""
    return (
        uuid.UUID("11111111-1111-4111-8111-111111111111"),
        uuid.UUID("22222222-2222-4222-8222-222222222222"),
        uuid.UUID("33333333-3333-4333-8333-333333333333"),
    )


@pytest.fixture
def reader(
    recorder: FakeRecorder, scope_ids: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> AgentViewReader:
    """A reader over a fake connection, used where only the *surface* is under
    test. The surface is what a client sees, and it must be identical whether
    the connection behind it is real or not."""
    tenant_id, user_id, agent_run_id = scope_ids
    return AgentViewReader(
        connect=factory_for(FakeConnection(columns=("case_id",))),
        recorder=recorder,
        scope=AgentScope(tenant_id=tenant_id, user_id=user_id, agent_run_id=agent_run_id),
    )
