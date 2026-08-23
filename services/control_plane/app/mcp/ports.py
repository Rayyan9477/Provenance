"""The two ports this package reaches the world through.

Authority
---------
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 5 - deployment units
  and their boundaries.
- ``packages/python/provenance_db/src/provenance_db/retry.py`` - the house
  pattern these Protocols follow: name the surface the caller needs, and let
  ``psycopg`` satisfy it structurally rather than annotating against
  ``psycopg``'s own types.

Why the recorder is a port and not a repository call
-----------------------------------------------------
``pv_agent_reader`` holds ``SELECT`` on five views and no grant of any kind on
``agent_runs``. That is not an oversight to route around - it is the boundary
this whole phase exists to demonstrate. The consequence is concrete: **the MCP
server physically cannot write its own audit row.** The record therefore leaves
this package through :class:`ToolCallRecorder` and is persisted by a principal
that holds ``UPDATE`` on ``agent_runs`` - in this build, the control plane's
internal agent-run endpoint, which is another lane's file.

A server that could write its own audit trail would also be a server that could
edit it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from services.control_plane.app.mcp.records import ToolCallRecord

__all__ = [
    "ColumnDescription",
    "ConnectionFactory",
    "ReadOnlyConnection",
    "ReadOnlyCursor",
    "ToolCallRecorder",
]


class ColumnDescription(Protocol):
    """One entry of DB-API ``cursor.description``. ``psycopg.Column`` satisfies it."""

    @property
    def name(self) -> str: ...


class ReadOnlyCursor(Protocol):
    """The cursor surface this package uses, and nothing more.

    There is no ``executemany``, no ``copy`` and no ``stream``: a port that
    exposes only what the caller needs is a second, weaker boundary underneath
    the SQL grant, and it costs nothing.
    """

    def execute(self, query: str, params: Any = None) -> Any: ...

    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    @property
    def description(self) -> Sequence[ColumnDescription] | None: ...


class ReadOnlyConnection(Protocol):
    """A connection that can hand out cursors and close. It cannot commit."""

    def cursor(self) -> AbstractContextManager[ReadOnlyCursor]: ...

    def close(self) -> None: ...


class ConnectionFactory(Protocol):
    """Opens one scoped, role-checked, read-only session per call."""

    def __call__(self) -> AbstractContextManager[ReadOnlyConnection]: ...


class ToolCallRecorder(Protocol):
    """Appends one entry to ``agent_runs.tool_calls`` for *agent_run_id*.

    The column is ``tool_calls``; the HTTP field the same array is rendered as is
    ``mcp_tool_calls[]``. ``agent_runs.mcp_tool_calls`` is not a column name.
    """

    def record(self, agent_run_id: uuid.UUID, call: ToolCallRecord) -> None: ...
