"""The one place a tool call becomes a database read, and an audit entry.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.3`` (record every call) and ``T11.4``
  (a denied call is rendered, never swallowed).
- ``docs/quality/23_PHASE_GATES.md`` section 17 - ``G11.4``, ``G11.5``.
- ``docs/frontend/32_JUDGE_MODE.md`` sections 6.1 and 6.5.

Three properties, each of which is a test in ``tests/mcp/test_reader.py``
------------------------------------------------------------------------
1. **The scope is bound from identity.** ``tenant_id`` and ``user_id`` are read
   from :class:`~services.control_plane.app.mcp.scope.AgentScope`, which is
   fixed at construction from the caller's verified identity. No argument to
   :meth:`AgentViewReader.read` can influence either, and a filter named
   ``user_id`` is refused by the composer before a connection is opened.
2. **Every call is recorded.** Success, refusal and failure all append exactly
   one :class:`~services.control_plane.app.mcp.records.ToolCallRecord`. A call
   that reached the database and left no record would make the trace a summary
   of the calls that happened to succeed.
3. **A refusal does not crash the run.** A denial-class SQLSTATE returns a
   result with ``denied=True`` and empty rows; anything else propagates
   unchanged, because a connection reset is not a permission boundary and
   recording it as one would invent a refusal that never happened.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from services.control_plane.app.mcp import statements
from services.control_plane.app.mcp.ports import ConnectionFactory, ToolCallRecorder
from services.control_plane.app.mcp.records import DENIAL_SQLSTATES, ToolCallRecord
from services.control_plane.app.mcp.scope import AgentScope
from services.control_plane.app.mcp.views import AGENT_VIEW_TOOLS

__all__ = ["AgentViewReader", "ToolResult"]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What one tool call produced, as the tool layer sees it.

    ``denial_code`` carries the SQLSTATE for a refused call. It is deliberately
    *not* part of the persisted entry: section 9.9's allowlist has no field for
    it (see :mod:`services.control_plane.app.mcp.records`).
    """

    tool_name: str
    view_name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    row_count: int
    limit: int
    sequence: int
    duration_ms: int
    truncated: bool = False
    denied: bool = False
    denial_code: str | None = None


def _sqlstate(error: BaseException) -> str | None:
    """The SQLSTATE an exception carries, if it carries one.

    Read by attribute rather than by exception class so that this module never
    imports ``psycopg``. That is not tidiness: it is what lets the hermetic lane
    exercise the denial branch with a fake that is faithful rather than special
    cased, and it keeps the classification keyed on the code the *database*
    emitted rather than on a Python type this repository chose.
    """
    code = getattr(error, "sqlstate", None)
    return code if isinstance(code, str) else None


@dataclass
class AgentViewReader:
    """Executes the five fixed statements, as ``pv_agent_reader``, recording each.

    One instance serves one agent run: the scope is fixed at construction and
    the sequence counter is the call ordinal within that run.
    """

    connect: ConnectionFactory
    recorder: ToolCallRecorder
    scope: AgentScope
    _sequence: int = field(default=0, init=False, repr=False)

    def read(
        self,
        tool_name: str,
        *,
        filters: Mapping[str, object] | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        """Run one tool.

        Composition happens first and on purpose: an unknown tool, an undeclared
        filter or a malformed value is refused before a connection exists, so a
        rejected call never touches the cluster and never appears in the trace
        as though it had.
        """
        tool = AGENT_VIEW_TOOLS[tool_name]
        statement = statements.compose(tool, scope=self.scope, filters=filters, limit=limit)

        self._sequence += 1
        sequence = self._sequence
        started = time.perf_counter()

        try:
            with self.connect() as connection, connection.cursor() as cursor:
                cursor.execute(statement.sql, statement.params)
                fetched = cursor.fetchall()
                columns = tuple(column.name for column in (cursor.description or ()))
        except BaseException as error:
            duration_ms = _elapsed_ms(started)
            code = _sqlstate(error)
            denied = code in DENIAL_SQLSTATES
            self._record(
                sequence=sequence,
                tool_name=tool_name,
                view_name=tool.view_name,
                filter_summary=statement.filter_summary,
                duration_ms=duration_ms,
                rows_returned=None,
                denied=denied,
            )
            if not denied:
                raise
            return ToolResult(
                tool_name=tool_name,
                view_name=tool.view_name,
                columns=(),
                rows=(),
                row_count=0,
                limit=statement.limit,
                sequence=sequence,
                duration_ms=duration_ms,
                denied=True,
                denial_code=code,
            )

        duration_ms = _elapsed_ms(started)
        rows = tuple(dict(zip(columns, row, strict=True)) for row in fetched)
        self._record(
            sequence=sequence,
            tool_name=tool_name,
            view_name=tool.view_name,
            filter_summary=statement.filter_summary,
            duration_ms=duration_ms,
            rows_returned=len(rows),
            denied=False,
        )
        return ToolResult(
            tool_name=tool_name,
            view_name=tool.view_name,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            limit=statement.limit,
            sequence=sequence,
            duration_ms=duration_ms,
            # The page was filled, so there may be more. Said plainly rather
            # than left for the caller to infer from a row count it would have
            # to compare against a limit it never saw.
            truncated=len(rows) >= statement.limit,
        )

    def _record(
        self,
        *,
        sequence: int,
        tool_name: str,
        view_name: str,
        filter_summary: str,
        duration_ms: int,
        rows_returned: int | None,
        denied: bool,
    ) -> None:
        self.recorder.record(
            self.scope.agent_run_id,
            ToolCallRecord(
                sequence=sequence,
                tool_name=tool_name,
                view_name=view_name,
                filter_summary=filter_summary,
                duration_ms=duration_ms,
                rows_returned=rows_returned,
                denied=denied,
            ),
        )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
