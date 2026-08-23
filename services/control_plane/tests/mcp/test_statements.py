"""The composed statement: fixed text, bound values, scope that cannot be named.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``, and the task brief: "Tools take
  typed parameters and compose a fixed statement; there is no passthrough. Add a
  test that fails if any tool parameter reaches a statement uninterpolated."
- ``docs/specs/10_DATABASE_DDL.md`` section 14 - the five views and their columns.
- ``docs/frontend/32_JUDGE_MODE.md`` section 6.1 - ``filter_summary`` is a
  rendered predicate template with values elided.

The load-bearing test in this file
----------------------------------
:func:`test_statement_text_is_independent_of_every_parameter_value` is the one
that means "no parameter reaches the statement uninterpolated", and it is
phrased as a property rather than as a string search because a string search
can be defeated by an encoding. If a value were concatenated into the SQL, then
composing the same tool twice with two different values would produce two
different SQL strings. It does not matter how the value was escaped, quoted or
transformed on the way in - a statement whose text depends on a value is a
statement that carries one. The test asserts the texts are byte-identical.

Verified by neutering before it was trusted: replacing the bound predicate with
an f-string interpolation of the value turns this test red, and it is the only
test in this file that catches every form of the mistake.
"""

from __future__ import annotations

import uuid

import pytest

from services.control_plane.app.mcp import statements, views
from services.control_plane.app.mcp.scope import AgentScope

pytestmark = pytest.mark.unit

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")

SCOPE = AgentScope(tenant_id=TENANT, user_id=USER, agent_run_id=RUN)

#: A value that is only harmless when it is bound. If any of these reaches the
#: statement text, the assertion that finds it is the least of the problems.
INJECTIONS: tuple[str, ...] = (
    "'; DROP TABLE cases; --",
    "x' OR '1'='1",
    "\\'; SELECT * FROM evidence_items; --",
    "1 UNION ALL SELECT cognito_sub FROM users",
)


def _sample_value(spec: views.FilterSpec, *, variant: int) -> object:
    """A legal value for *spec*, distinct per *variant*."""
    if spec.kind == views.FilterKind.UUID:
        return uuid.UUID(int=variant + 1)
    if spec.kind == views.FilterKind.ENUM:
        return sorted(spec.allowed)[variant % len(spec.allowed)]
    return f"value-{variant}"


def _all_filters(spec: views.AgentViewTool, *, variant: int) -> dict[str, object]:
    return {f.name: _sample_value(f, variant=variant) for f in spec.filters}


def _required_filters(spec: views.AgentViewTool, *, variant: int = 0) -> dict[str, object]:
    return {f.name: _sample_value(f, variant=variant) for f in spec.filters if f.required}


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_statement_text_is_independent_of_every_parameter_value(tool_name: str) -> None:
    """The whole no-passthrough claim, as an executable property."""
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    first = statements.compose(spec, scope=SCOPE, filters=_all_filters(spec, variant=0), limit=7)
    second = statements.compose(spec, scope=SCOPE, filters=_all_filters(spec, variant=1), limit=7)
    assert first.sql == second.sql
    assert first.filter_summary == second.filter_summary
    assert first.params != second.params, (
        "the two calls must differ somewhere; if the parameters are equal too, "
        "this test is comparing a statement against itself and proves nothing"
    )


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_the_scope_is_the_first_two_bound_parameters(tool_name: str) -> None:
    """``tenant_id`` and ``user_id`` come from the caller's verified identity.

    Asserted positionally because that is what makes the predicate unskippable:
    every statement this module composes starts with the same two binds.
    """
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    statement = statements.compose(
        spec, scope=SCOPE, filters=_all_filters(spec, variant=0), limit=5
    )
    assert statement.params[0] == TENANT
    assert statement.params[1] == USER
    assert "tenant_id = %s" in statement.sql
    assert "user_id = %s" in statement.sql


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
@pytest.mark.parametrize("payload", INJECTIONS)
def test_no_caller_value_appears_in_the_statement_text(tool_name: str, payload: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    text_filters = [f for f in spec.filters if f.kind == views.FilterKind.TEXT]
    if not text_filters:
        pytest.skip(f"{tool_name} declares no free-text filter")
    filters = _required_filters(spec)
    filters[text_filters[0].name] = payload
    statement = statements.compose(spec, scope=SCOPE, filters=filters, limit=5)
    assert payload not in statement.sql
    assert payload not in statement.filter_summary
    assert payload in statement.params


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_every_placeholder_has_exactly_one_bound_parameter(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    for variant in (0, 1):
        statement = statements.compose(
            spec, scope=SCOPE, filters=_all_filters(spec, variant=variant), limit=9
        )
        assert statement.sql.count("%s") == len(statement.params)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_the_statement_reads_the_view_and_never_a_base_table(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    statement = statements.compose(spec, scope=SCOPE, filters=_required_filters(spec), limit=5)
    assert statement.sql.startswith("SELECT ")
    assert f"FROM {spec.view_name}" in statement.sql
    lowered = statement.sql.lower()
    for keyword in ("insert", "update", "delete", "upsert", "grant", "revoke", ";"):
        assert keyword not in lowered, f"{tool_name} composed a {keyword!r}: {statement.sql}"


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_an_undeclared_filter_is_refused(tool_name: str) -> None:
    """The registry is the allowlist. Anything else is not a filter, it is an
    attempt to reach a column the tool does not expose."""
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    filters = _required_filters(spec)
    filters["cognito_sub"] = "not-a-declared-filter"
    with pytest.raises(statements.UndeclaredFilterError):
        statements.compose(spec, scope=SCOPE, filters=filters, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_a_filter_naming_a_scope_column_is_refused(tool_name: str) -> None:
    """Belt and braces on the rule above: even if a later edit declared a
    ``user_id`` filter, composing one is refused."""
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    for column in views.SCOPE_COLUMNS:
        filters = _required_filters(spec)
        filters[column] = str(uuid.uuid4())
        with pytest.raises(statements.UndeclaredFilterError):
            statements.compose(spec, scope=SCOPE, filters=filters, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_a_missing_required_filter_is_refused(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    if not any(f.required for f in spec.filters):
        pytest.skip(f"{tool_name} declares no required filter")
    with pytest.raises(statements.MissingRequiredFilterError):
        statements.compose(spec, scope=SCOPE, filters={}, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_the_limit_is_bound_and_clamped(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    statement = statements.compose(
        spec, scope=SCOPE, filters=_required_filters(spec), limit=10_000_000
    )
    assert statement.params[-1] == spec.max_limit
    assert "LIMIT %s" in statement.sql
    assert "10000000" not in statement.sql

    with pytest.raises(statements.InvalidFilterValueError):
        statements.compose(spec, scope=SCOPE, filters=_required_filters(spec), limit=0)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_a_malformed_uuid_is_refused_rather_than_bound(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    uuid_filters = [f for f in spec.filters if f.kind == views.FilterKind.UUID]
    if not uuid_filters:
        pytest.skip(f"{tool_name} declares no uuid filter")
    filters = _required_filters(spec)
    filters[uuid_filters[0].name] = "00000000-0000-0000-0000-00000000000Z"
    with pytest.raises(statements.InvalidFilterValueError):
        statements.compose(spec, scope=SCOPE, filters=filters, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_a_value_outside_a_closed_vocabulary_is_refused(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    enum_filters = [f for f in spec.filters if f.kind == views.FilterKind.ENUM]
    if not enum_filters:
        pytest.skip(f"{tool_name} declares no closed-vocabulary filter")
    filters = _required_filters(spec)
    filters[enum_filters[0].name] = "NOT_A_MEMBER"
    with pytest.raises(statements.InvalidFilterValueError):
        statements.compose(spec, scope=SCOPE, filters=filters, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_an_overlong_text_value_is_refused(tool_name: str) -> None:
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    text_filters = [f for f in spec.filters if f.kind == views.FilterKind.TEXT]
    if not text_filters:
        pytest.skip(f"{tool_name} declares no free-text filter")
    filters = _required_filters(spec)
    filters[text_filters[0].name] = "x" * (text_filters[0].max_length + 1)
    with pytest.raises(statements.InvalidFilterValueError):
        statements.compose(spec, scope=SCOPE, filters=filters, limit=5)


@pytest.mark.parametrize("tool_name", sorted(views.AGENT_VIEW_TOOLS))
def test_the_filter_summary_elides_values(tool_name: str) -> None:
    """``32_JUDGE_MODE.md`` section 12: the template is recorded, the values are
    not. A ``filter_summary`` carrying the value would put user data into the
    Memory Trace through the back door."""
    spec = views.AGENT_VIEW_TOOLS[tool_name]
    statement = statements.compose(
        spec, scope=SCOPE, filters=_all_filters(spec, variant=0), limit=5
    )
    assert str(TENANT) not in statement.filter_summary
    assert str(USER) not in statement.filter_summary
    assert "tenant_id = <scope tenant>" in statement.filter_summary
    assert "user_id = <scope user>" in statement.filter_summary
    for declared in spec.filters:
        assert f"{declared.column} = <{declared.name}>" in statement.filter_summary
