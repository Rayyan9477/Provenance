"""The MCP server: five tools, five views, one bound identity.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts* (the five views) and
  *Gemini model id canon* (``google-genai`` is the agent framework; it consumes
  MCP tools).
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``-``T11.4``.
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 12 - no arbitrary SQL
  tool is exposed to agents.
- ``docs/implementation/04_API_EVENTS_SECURITY.md`` section 21 - what may and
  may not be exposed.

Why the tools are methods on a bound object
--------------------------------------------
A server built by :func:`build_mcp_server` is built **for one agent run**. The
scope lives on the :class:`~services.control_plane.app.mcp.reader.AgentViewReader`
the tools close over, so no tool signature mentions an owner, a tenant or a run.
That is the whole design: an agent cannot name a different user because there is
no argument in which to name one, and ``tools/list`` says so to anything that
asks. ``tests/mcp/test_tool_surface.py`` reads the advertised schema - the same
bytes a client receives - and asserts the absence.

The tools are unbound methods in :data:`TOOL_FUNCTIONS` so their signatures can
be inspected without constructing a reader, and bound methods when registered so
FastMCP derives the advertised schema with ``self`` already removed.

What a tool returns
-------------------
Rows, plus the provenance of the read: which view was touched, under which SQL
role, in which access mode, and whether the call was refused. A denied call
returns that fact rather than an empty success - ``G11.5`` requires a refusal to
be visible, and a tool that rendered a denial as "no results" would make the
boundary invisible at exactly the moment it did its job.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Final

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock
from pydantic import Field

from services.control_plane.app.mcp.reader import AgentViewReader, ToolResult
from services.control_plane.app.mcp.records import ACCESS_MODE, MCP_SERVER_NAME, SQL_ROLE
from services.control_plane.app.mcp.statements import UndeclaredFilterError
from services.control_plane.app.mcp.views import AGENT_VIEW_TOOLS, DEFAULT_ROWS, MAX_ROWS

__all__ = [
    "AgentViewTools",
    "SERVER_INSTRUCTIONS",
    "TOOL_FUNCTIONS",
    "StrictArgumentServer",
    "build_mcp_server",
]

#: Shown to any client that connects. It states the boundary in the same words
#: the Judge Mode panel uses, so a model reading the server's instructions and a
#: judge reading the UI are told the same thing.
SERVER_INSTRUCTIONS: Final[str] = (
    "Governed read-only access to Provenance canonical memory. Every tool reads "
    "one agent-safe view as the SQL role pv_agent_reader, which holds SELECT on "
    "those five views and on nothing else. The permission boundary is the SQL "
    "grant, not this instruction. Reads are scoped to the caller's own records; "
    "no tool accepts a user or tenant argument, and there is no tool that runs "
    "arbitrary SQL."
)

#: The page size a tool uses when the caller does not choose one, and the
#: ceiling it cannot raise. Mirrored from the registry so the advertised schema
#: and the composer clamp to the same number.
_LimitArg = Annotated[int, Field(ge=1, le=MAX_ROWS)]
_OptionalId = str | None
_OptionalText = str | None


def _jsonable(value: object) -> object:
    """Render one cell as something an MCP client can carry.

    ``uuid``, ``datetime`` and ``Decimal`` all arrive from CockroachDB as Python
    objects. ``Decimal`` becomes a string rather than a float on purpose: these
    are money columns, and a float is a different number.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _payload(result: ToolResult) -> dict[str, object]:
    """The tool's return value, rows plus the provenance of the read."""
    return {
        "view_name": result.view_name,
        "mcp_server": MCP_SERVER_NAME,
        "sql_role": SQL_ROLE,
        "access_mode": ACCESS_MODE,
        "denied": result.denied,
        "denial_sqlstate": result.denial_code,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "columns": list(result.columns),
        "rows": [{key: _jsonable(cell) for key, cell in row.items()} for row in result.rows],
    }


def _filters(**supplied: object) -> dict[str, object]:
    """Drop the arguments the caller did not supply.

    ``None`` means "not filtered", not "filter on NULL". Passing it through
    would compose ``column = %s`` with a ``NULL`` bind, which matches nothing
    and would silently turn every unfilled argument into an empty result.
    """
    return {name: value for name, value in supplied.items() if value is not None}


class AgentViewTools:
    """The five tools, bound to one run's reader.

    No method on this class takes an owner, a tenant, a run id, a relation name,
    a column list, an ordering or a SQL fragment. What is left is a small set of
    typed values bound into a statement the registry fixed.
    """

    def __init__(self, reader: AgentViewReader) -> None:
        self._reader = reader

    def read_case_context(
        self,
        case_id: _OptionalId = None,
        status: _OptionalText = None,
        case_type: _OptionalText = None,
        attention_level: _OptionalText = None,
        counterparty_name: _OptionalText = None,
        limit: _LimitArg = DEFAULT_ROWS,
    ) -> dict[str, object]:
        """Read the caller's cases with counterparty and relationship context."""
        return _payload(
            self._reader.read(
                "read_case_context",
                filters=_filters(
                    case_id=case_id,
                    status=status,
                    case_type=case_type,
                    attention_level=attention_level,
                    counterparty_name=counterparty_name,
                ),
                limit=limit,
            )
        )

    def read_active_beliefs(
        self,
        case_id: _OptionalId = None,
        belief_id: _OptionalId = None,
        epistemic_status: _OptionalText = None,
        predicate: _OptionalText = None,
        limit: _LimitArg = DEFAULT_ROWS,
    ) -> dict[str, object]:
        """Read current belief versions with their grounding edges."""
        return _payload(
            self._reader.read(
                "read_active_beliefs",
                filters=_filters(
                    case_id=case_id,
                    belief_id=belief_id,
                    epistemic_status=epistemic_status,
                    predicate=predicate,
                ),
                limit=limit,
            )
        )

    def read_belief_lineage(
        self,
        belief_id: str,
        epistemic_status: _OptionalText = None,
        supersession_reason_code: _OptionalText = None,
        limit: _LimitArg = DEFAULT_ROWS,
    ) -> dict[str, object]:
        """Read one belief's ordered supersession chain and the reason for each change."""
        return _payload(
            self._reader.read(
                "read_belief_lineage",
                filters=_filters(
                    belief_id=belief_id,
                    epistemic_status=epistemic_status,
                    supersession_reason_code=supersession_reason_code,
                ),
                limit=limit,
            )
        )

    def read_evidence(
        self,
        evidence_id: _OptionalId = None,
        artifact_id: _OptionalId = None,
        evidence_type: _OptionalText = None,
        sender_domain: _OptionalText = None,
        limit: _LimitArg = DEFAULT_ROWS,
    ) -> dict[str, object]:
        """Read retrieval-eligible evidence. Retracted evidence is excluded by the view."""
        return _payload(
            self._reader.read(
                "read_evidence",
                filters=_filters(
                    evidence_id=evidence_id,
                    artifact_id=artifact_id,
                    evidence_type=evidence_type,
                    sender_domain=sender_domain,
                ),
                limit=limit,
            )
        )

    def read_open_obligations(
        self,
        case_id: _OptionalId = None,
        row_kind: _OptionalText = None,
        status: _OptionalText = None,
        subtype: _OptionalText = None,
        limit: _LimitArg = DEFAULT_ROWS,
    ) -> dict[str, object]:
        """Read unresolved obligations: open commitments and open conflicts."""
        return _payload(
            self._reader.read(
                "read_open_obligations",
                filters=_filters(
                    case_id=case_id,
                    row_kind=row_kind,
                    status=status,
                    subtype=subtype,
                ),
                limit=limit,
            )
        )


#: The tool callables, unbound, keyed by the name they are advertised under.
#: Keyed off the registry so a view added there without a tool here - or a tool
#: here without a view there - fails at import rather than at ``tools/list``.
TOOL_FUNCTIONS: Final[dict[str, Callable[..., dict[str, object]]]] = {
    name: getattr(AgentViewTools, name) for name in AGENT_VIEW_TOOLS
}


def _declared_arguments(tool_name: str) -> frozenset[str]:
    spec = AGENT_VIEW_TOOLS[tool_name]
    return frozenset({filter_spec.name for filter_spec in spec.filters} | {"limit"})


class StrictArgumentServer(FastMCP[Any]):
    """A FastMCP server that refuses arguments it does not declare.

    FastMCP builds each tool's argument model on ``pydantic``'s default
    ``extra="ignore"``, so a client that sends ``{"user_id": "...", "limit": 5}``
    gets a **successful call** with ``user_id`` silently discarded. Established
    by calling it, not by reading the source: the first version of
    ``tests/mcp/test_server_calls.py::test_a_client_supplied_user_id_is_rejected_by_the_tool_layer``
    failed with ``DID NOT RAISE``.

    Dropping the argument is safe - the scope is bound from the verified
    identity and nothing downstream consults it - but silence is the wrong
    answer here. A caller that sends ``user_id`` and receives rows has been
    given every reason to believe the argument was honoured, and the rows it
    got back belong to somebody else's request as far as it knows. The same
    reasoning is why :class:`~services.control_plane.app.mcp.statements.UndeclaredFilterError`
    refuses rather than drops, and this override is what extends it to the one
    layer above, where the argument is discarded before this package ever sees
    it.

    ``call_tool`` is the single entry point the low-level server routes tool
    invocations through (``FastMCP._setup_handlers``), so the guard covers real
    MCP traffic and not only direct calls.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        if name in AGENT_VIEW_TOOLS:
            undeclared = sorted(set(arguments) - _declared_arguments(name))
            if undeclared:
                raise UndeclaredFilterError(
                    f"{name} does not accept {undeclared}; "
                    f"declared arguments are {sorted(_declared_arguments(name))}"
                )
        return await super().call_tool(name, arguments)


def build_mcp_server(
    *, reader: AgentViewReader, name: str = MCP_SERVER_NAME
) -> StrictArgumentServer:
    """Build the server for one agent run.

    *reader* carries the scope. Nothing about the surface changes with it: the
    same five tools with the same five schemas are advertised for every run,
    which is why an agent cannot tell - and cannot influence - whose rows it is
    reading.
    """
    server = StrictArgumentServer(name=name, instructions=SERVER_INSTRUCTIONS)
    tools = AgentViewTools(reader)
    for tool_name, spec in AGENT_VIEW_TOOLS.items():
        server.tool(name=tool_name, description=spec.description)(getattr(tools, tool_name))
    return server
