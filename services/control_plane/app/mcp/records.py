"""The audit record one MCP tool call leaves behind.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero commit canon*, row "MCP tool-call
  naming": "Column ``agent_runs.tool_calls``; HTTP field ``mcp_tool_calls[]``.
  ``agent_runs.mcp_tool_calls`` is not a column name."
- ``db/migrations/versions/0008_events_infrastructure.py`` -> ``AGENT_RUNS`` and
  ``ck_agent_runs_tool_calls``: the column is ``JSONB`` and must hold an array.
- ``docs/specs/15_API_SPEC.md`` section 9.9 and
  ``services/control_plane/app/api/schemas/internal.py::ToolCallRecord`` - the
  closed allowlist of keys an entry may carry.
- ``docs/frontend/32_JUDGE_MODE.md`` section 6.1 - the fields Judge Mode renders.

Why the allowlist is enforced here as well as at the endpoint
-------------------------------------------------------------
The control plane rejects an entry carrying a key outside the allowlist with a
``422``. That is the enforcement. :data:`TOOL_CALL_ALLOWLIST` and
:meth:`ToolCallRecord.as_json_entry` are what make this package *unable* to
produce such an entry in the first place, so the failure surfaces as a red test
in this lane rather than as a 422 in production. The keys that are absent are
the point: no ``sql``, no ``rows``, no ``result``. A trace that carried returned
rows would put the corpus into the Memory Trace through the back door.

Why there is no error-message field
------------------------------------
``G11.5`` wants a denied call rendered with its SQL error class. The allowlist in
section 9.9 has no field for one, and this package does not get to widen another
lane's request schema, so the SQLSTATE is carried on the in-process
:class:`~services.control_plane.app.mcp.reader.ToolResult` (``denial_code``) and
the persisted entry carries ``denied: true``. Recorded here rather than smoothed
over: rendering the error class in the trace needs
``AgentRunCompleteRequest.ToolCallRecord`` to grow one field, and that is a
change to a file this task does not own.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "ACCESS_MODE",
    "DENIAL_SQLSTATES",
    "MCP_SERVER_NAME",
    "SQL_ROLE",
    "TOOL_CALLS_COLUMN",
    "TOOL_CALLS_HTTP_FIELD",
    "TOOL_CALL_ALLOWLIST",
    "ToolCallRecord",
]

#: This build's own MCP server over the five agent-safe views.
#:
#: Deliberately **not** ``cockroachdb-cloud-managed-mcp``. That name belongs to a
#: CockroachDB product, and ``CANONICAL_DECISIONS.md`` -> *Gemini model id canon*
#: records that the CockroachDB entry was discarded when the build pivoted. A
#: trace field naming a sponsor product this build does not run would be a small,
#: checkable dishonesty of exactly the kind the pack exists to prevent.
MCP_SERVER_NAME: Final[str] = "provenance-agent-views-mcp"

#: The only role this server ever authenticates as.
SQL_ROLE: Final[str] = "pv_agent_reader"

#: Read-only, and it is the grant that makes it so.
ACCESS_MODE: Final[Literal["READ_ONLY"]] = "READ_ONLY"

#: The column. Not ``mcp_tool_calls`` - that spelling fails the DDL check.
TOOL_CALLS_COLUMN: Final[str] = "tool_calls"

#: The HTTP field the same array is rendered as. Column ``tool_calls``, field
#: ``mcp_tool_calls`` - that pairing is fixed.
TOOL_CALLS_HTTP_FIELD: Final[str] = "mcp_tool_calls"

#: Section 9.9's closed key set, in the order that section prints it.
TOOL_CALL_ALLOWLIST: Final[tuple[str, ...]] = (
    "sequence",
    "mcp_server",
    "tool_name",
    "view_name",
    "sql_role",
    "access_mode",
    "filter_summary",
    "rows_returned",
    "duration_ms",
    "denied",
)

#: The SQLSTATEs that mean "the database refused this", as opposed to "the
#: database could not answer this".
#:
#: ``42501`` is ``insufficient_privilege`` and is what ``pv_agent_reader`` gets
#: for a base table on this cluster - probed, not assumed, and re-asserted live
#: by ``tests/db/test_mcp_server.py``. ``25006`` is
#: ``read_only_sql_transaction``, the read-only session's own refusal.
#:
#: A transport error such as ``08006`` is deliberately absent. Recording a
#: connection reset as a denial would put a permission refusal in the Memory
#: Trace that never happened, and a trace that invents refusals is no more
#: trustworthy than one that hides them.
DENIAL_SQLSTATES: Final[frozenset[str]] = frozenset({"42501", "25006"})


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One entry of ``agent_runs.tool_calls``.

    ``rows_returned`` is ``None`` rather than ``0`` when the call did not
    complete. Zero rows and no answer are different facts and the trace should
    not render them identically.
    """

    sequence: int
    tool_name: str
    view_name: str
    filter_summary: str
    duration_ms: int
    rows_returned: int | None = None
    denied: bool = False
    mcp_server: str = MCP_SERVER_NAME
    sql_role: str = SQL_ROLE
    access_mode: Literal["READ_ONLY"] = ACCESS_MODE

    def as_json_entry(self) -> dict[str, object]:
        """The JSONB element, carrying exactly the allowlisted keys.

        Projected *from* the allowlist rather than written out beside it. A
        field added to this dataclass and not to the allowlist is dropped, which
        is the safe direction: a key nobody reviewed can never reach the trace.
        A name in the allowlist with no matching field raises ``KeyError`` here,
        which is the loud direction.
        """
        fields: dict[str, object] = dataclasses.asdict(self)
        return {key: fields[key] for key in TOOL_CALL_ALLOWLIST}
