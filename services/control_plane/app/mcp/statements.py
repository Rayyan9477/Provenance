"""Statement composition: fixed text, bound values, no passthrough.

Authority
---------
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 12 - no arbitrary SQL
  tool.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``.
- ``docs/frontend/32_JUDGE_MODE.md`` section 6.1 and section 12 -
  ``filter_summary`` is a rendered predicate template with values elided.

The invariant, stated so it can be tested rather than believed
--------------------------------------------------------------
**For a given tool and a given set of filter *names*, the composed SQL is one
fixed string.** Values change the ``params`` tuple and nothing else. That is
strictly stronger than "we escape our inputs": escaping is a transformation
applied to a value on its way into the text, and a transformation can be wrong.
Here the value never enters the text at all, so there is nothing to escape.

``tests/mcp/test_statements.py::test_statement_text_is_independent_of_every_parameter_value``
composes each tool twice with two different value sets and asserts the two SQL
strings are byte-identical. Any interpolation - quoted, escaped, encoded, or
routed through a helper - makes those strings differ, which is why the test is
phrased as an equality on the statement rather than as a search for a payload.

Every identifier in the statement comes from
:mod:`services.control_plane.app.mcp.views`, which is a frozen module-level
registry. No caller value ever reaches an identifier position, and the only
positions a caller value can occupy are ``%s``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from services.control_plane.app.mcp.scope import AgentScope
from services.control_plane.app.mcp.views import (
    SCOPE_COLUMNS,
    AgentViewTool,
    FilterKind,
    FilterSpec,
)

__all__ = [
    "InvalidFilterValueError",
    "McpStatementError",
    "MissingRequiredFilterError",
    "Statement",
    "UndeclaredFilterError",
    "compose",
]

#: The placeholder psycopg binds. Written once so the count assertion in the
#: tests and the text built here cannot drift apart.
PLACEHOLDER: Final[str] = "%s"


class McpStatementError(ValueError):
    """A tool call that will not be composed into a statement."""


class UndeclaredFilterError(McpStatementError):
    """A filter name the tool's registry entry does not declare.

    This is the class that refuses ``user_id``. It is raised before a connection
    is opened, so an attempt to name another owner never reaches the database at
    all - and it is a refusal rather than a silent drop, because a silently
    ignored ``user_id`` looks to the caller exactly like an honoured one.
    """


class MissingRequiredFilterError(McpStatementError):
    """A required anchor was not supplied."""


class InvalidFilterValueError(McpStatementError):
    """A value that is not of the declared kind, or outside a closed vocabulary.

    The value is never quoted into the message. It has already been established
    that the value is not what it claimed to be; echoing it into a log adds
    nothing and puts caller-controlled text into the transcript.
    """


@dataclass(frozen=True, slots=True)
class Statement:
    """One composed read.

    ``sql`` carries no value. ``params`` carries every value, in the order the
    placeholders appear: the scope pair first, then the filters in registry
    order, then the limit. ``filter_summary`` is the trace rendering, with the
    values elided.

    ``limit`` repeats the clamped page size that is also the last bound
    parameter. It is a field rather than something a caller re-derives with
    ``params[-1]``: that index is correct only for as long as the limit stays
    last, which is a layout detail no consumer should be asserting against.
    """

    sql: str
    params: tuple[object, ...]
    filter_summary: str
    limit: int


def _coerce(spec: FilterSpec, value: object) -> object:
    """Validate *value* against *spec* and return the object to bind."""
    if spec.kind is FilterKind.UUID:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError) as error:
            raise InvalidFilterValueError(f"{spec.name} must be a UUID") from error
    text = str(value)
    if spec.kind is FilterKind.ENUM:
        if text not in spec.allowed:
            raise InvalidFilterValueError(f"{spec.name} must be one of {sorted(spec.allowed)}")
        return text
    if len(text) > spec.max_length:
        raise InvalidFilterValueError(f"{spec.name} must be at most {spec.max_length} characters")
    return text


def _clamp_limit(tool: AgentViewTool, limit: int | None) -> int:
    if limit is None:
        return tool.default_limit
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise InvalidFilterValueError("limit must be an integer")
    if limit < 1:
        raise InvalidFilterValueError("limit must be at least 1")
    return min(limit, tool.max_limit)


def _declared(tool: AgentViewTool, filters: Mapping[str, object]) -> tuple[FilterSpec, ...]:
    """The registry entries for *filters*, in registry order.

    Raises on anything the registry does not declare - which includes both
    scope columns, since no tool declares a filter on either.
    """
    unknown = sorted(set(filters) - {spec.name for spec in tool.filters})
    if unknown:
        raise UndeclaredFilterError(
            f"{tool.tool_name} does not accept {unknown}; "
            f"declared filters are {sorted(spec.name for spec in tool.filters)}"
        )
    for spec in tool.filters:
        if spec.required and spec.name not in filters:
            raise MissingRequiredFilterError(f"{tool.tool_name} requires {spec.name}")
    return tuple(spec for spec in tool.filters if spec.name in filters)


def compose(
    tool: AgentViewTool,
    *,
    scope: AgentScope,
    filters: Mapping[str, object] | None = None,
    limit: int | None = None,
) -> Statement:
    """Build the one statement *tool* is allowed to issue.

    The scope predicate is not optional and not appendable: it is written first,
    unconditionally, from ``scope``. There is no code path through this function
    that produces a statement without it.
    """
    supplied: Mapping[str, object] = filters or {}
    selected = _declared(tool, supplied)
    bound_limit = _clamp_limit(tool, limit)

    # Identifiers, all of them from the frozen registry. Values, all of them in
    # `params`. The two lists below are the only place the statement is built,
    # and neither reads `supplied`.
    predicates: list[str] = [f"{column} = {PLACEHOLDER}" for column in SCOPE_COLUMNS]
    summary: list[str] = [
        "tenant_id = <scope tenant>",
        "user_id = <scope user>",
    ]
    params: list[object] = [scope.tenant_id, scope.user_id]

    for spec in selected:
        predicates.append(f"{spec.column} = {PLACEHOLDER}")
        summary.append(f"{spec.column} = <{spec.name}>")
        params.append(_coerce(spec, supplied[spec.name]))

    params.append(bound_limit)

    sql = (
        "SELECT "
        + ", ".join(tool.columns)
        + " FROM "
        + tool.view_name
        + " WHERE "
        + " AND ".join(predicates)
        + " ORDER BY "
        + ", ".join(tool.order_by)
        + f" LIMIT {PLACEHOLDER}"
    )
    return Statement(
        sql=sql,
        params=tuple(params),
        filter_summary=" AND ".join(summary) + " LIMIT <limit>",
        limit=bound_limit,
    )
