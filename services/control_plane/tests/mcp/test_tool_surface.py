"""The exposed surface: exactly five tools, no SQL passthrough, no identity argument.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts*: the five agent-safe
  view names, verbatim.
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 12: no arbitrary SQL
  tool is exposed to agents.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``: "Restrict the exposed surface to
  the five agent views. Do not expose arbitrary SQL tools to agents."
- ``docs/implementation/04_API_EVENTS_SECURITY.md`` section 21.

Why the assertions read the *advertised* schema
-----------------------------------------------
A Python signature is what this repository sees; ``tools/list`` is what a model
sees. They can diverge - a decorator, a ``**kwargs`` passthrough or a wrapper
that forwards an extra field would show up in one and not the other. The tests
below read :meth:`FastMCP.list_tools`, which is the same call a client makes,
and check the Python signatures as well. An argument that should not exist has
to be absent from both.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from provenance_domain.enums import AgentSafeView
from services.control_plane.app.mcp import server as mcp_server
from services.control_plane.app.mcp import views

pytestmark = pytest.mark.unit

#: Transcribed from ``CANONICAL_DECISIONS.md`` -> *Names and counts*, not
#: imported, so that a rename of the enum member cannot silently move the
#: contract. The enum equality below is the second half of the same check.
CANON_VIEW_NAMES: frozenset[str] = frozenset(
    {
        "agent_case_context_v1",
        "agent_active_beliefs_v1",
        "agent_belief_lineage_v1",
        "agent_evidence_retrieval_v1",
        "agent_open_obligations_v1",
    }
)

#: A parameter with any of these names would let the caller name the owner of
#: the rows. ``user_id`` is the one the task plan calls out; the rest are the
#: synonyms a later edit would reach for.
IDENTITY_PARAMETERS: frozenset[str] = frozenset(
    {
        "user_id",
        "tenant_id",
        "owner_id",
        "owner",
        "principal",
        "principal_id",
        "subject",
        "cognito_sub",
        "agent_run_id",
        "scope",
    }
)

#: A parameter with any of these names would be a SQL passthrough by another
#: name: the caller would be choosing the relation, the projection or the
#: predicate rather than a value bound into a fixed one.
PASSTHROUGH_PARAMETERS: frozenset[str] = frozenset(
    {
        "sql",
        "query",
        "statement",
        "where",
        "filter",
        "filters",
        "order_by",
        "select",
        "columns",
        "projection",
        "table",
        "relation",
        "view",
        "view_name",
        "expression",
    }
)


async def _advertised(reader: Any) -> dict[str, dict[str, Any]]:
    """``{tool_name: inputSchema}`` exactly as a client would receive it."""
    server = mcp_server.build_mcp_server(reader=reader)
    return {tool.name: dict(tool.inputSchema) for tool in await server.list_tools()}


def test_the_registry_covers_exactly_the_five_canon_views() -> None:
    exposed = {spec.view_name for spec in views.AGENT_VIEW_TOOLS.values()}
    assert exposed == CANON_VIEW_NAMES
    assert exposed == {member.value for member in AgentSafeView}
    assert len(views.AGENT_VIEW_TOOLS) == 5


def test_tool_names_are_a_bijection_with_the_views() -> None:
    """Five tools, five views, one each.

    Two tools onto one view would make ``view_name`` in the trace ambiguous;
    one tool onto two views would mean a caller-selected relation.
    """
    view_names = [spec.view_name for spec in views.AGENT_VIEW_TOOLS.values()]
    assert len(set(view_names)) == len(view_names) == len(views.AGENT_VIEW_TOOLS)


async def test_exactly_five_tools_are_advertised(reader: Any) -> None:
    advertised = await _advertised(reader)
    assert set(advertised) == set(views.AGENT_VIEW_TOOLS)
    assert len(advertised) == 5


async def test_no_advertised_tool_accepts_an_identity_argument(reader: Any) -> None:
    """The rule the task plan states without qualification.

    "A tool must not accept a ``user_id`` parameter at all - if it did, an agent
    could name a different owner, and no amount of downstream checking recovers
    from an argument that should not exist."
    """
    offenders: dict[str, list[str]] = {}
    for name, schema in (await _advertised(reader)).items():
        properties = set(schema.get("properties", {}))
        leaked = sorted(properties & IDENTITY_PARAMETERS)
        if leaked:
            offenders[name] = leaked
    assert offenders == {}, f"tools advertise identity arguments: {offenders}"


def test_no_tool_callable_accepts_an_identity_argument() -> None:
    """The same rule, checked against the Python signatures.

    The advertised schema is generated from these; checking both is what catches
    a wrapper that forwards a field the schema never mentions.
    """
    offenders: dict[str, list[str]] = {}
    for name, function in mcp_server.TOOL_FUNCTIONS.items():
        parameters = inspect.signature(function).parameters
        leaked = sorted(set(parameters) & IDENTITY_PARAMETERS)
        variadic = [
            parameter
            for parameter in parameters.values()
            if parameter.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        ]
        if leaked or variadic:
            offenders[name] = leaked or [str(p) for p in variadic]
    assert offenders == {}, f"tool callables accept identity arguments: {offenders}"


async def test_no_advertised_tool_is_a_sql_passthrough(reader: Any) -> None:
    """``00_IMPLEMENTATION_MAP.md`` section 12 forbids an arbitrary SQL tool."""
    offenders: dict[str, list[str]] = {}
    for name, schema in (await _advertised(reader)).items():
        leaked = sorted(set(schema.get("properties", {})) & PASSTHROUGH_PARAMETERS)
        if leaked:
            offenders[name] = leaked
    assert offenders == {}, f"tools accept a SQL passthrough argument: {offenders}"


async def test_no_tool_is_named_like_a_sql_escape_hatch(reader: Any) -> None:
    forbidden = {"sql", "query", "execute", "run_sql", "raw", "run_query", "select"}
    assert set(await _advertised(reader)) & forbidden == set()


def test_every_declared_filter_binds_a_column_the_view_projects() -> None:
    """A filter naming a column the view does not project is either a typo or a
    reach past the view's projection. Both are caught here rather than by a
    runtime ``UndefinedColumn``."""
    for tool_name, spec in views.AGENT_VIEW_TOOLS.items():
        undeclared = {f.column for f in spec.filters} - set(spec.columns)
        assert undeclared == set(), f"{tool_name} filters on unprojected {sorted(undeclared)}"


def test_every_view_projects_the_tenancy_pair_and_no_tool_can_widen_it() -> None:
    """The scope columns exist on every view, so the ``WHERE`` is writable, and
    no tool may declare a filter on either of them."""
    for tool_name, spec in views.AGENT_VIEW_TOOLS.items():
        assert set(views.SCOPE_COLUMNS) <= set(spec.columns), tool_name
        filtered = {f.column for f in spec.filters} & set(views.SCOPE_COLUMNS)
        assert filtered == set(), f"{tool_name} lets a caller filter on {sorted(filtered)}"


async def test_every_tool_advertises_a_bounded_limit(reader: Any) -> None:
    """An unbounded page is a denial-of-service tool with a friendly name."""
    for name, schema in (await _advertised(reader)).items():
        limit = schema.get("properties", {}).get("limit")
        assert limit is not None, f"{name} advertises no limit"
        assert limit.get("maximum") == views.AGENT_VIEW_TOOLS[name].max_limit, name
