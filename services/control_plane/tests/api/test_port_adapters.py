"""The port adapters: real repositories behind the protocols -- ``T8.9``.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8, 9 and 1.7 (the tenancy rule).
- ``services/control_plane/app/api/ports.py`` -- the 47 methods these
  adapters implement, and the module docstring explaining why the routes
  depend on protocols rather than on SQL.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer*: only the deterministic
  Memory Kernel writes canonical tables.

What this suite is actually defending
--------------------------------------
Three properties, and each of them fails silently without a test.

1. **The scoping predicate lives in the SQL, once.** The adapters translate
   an :class:`OwnerScope` into repository arguments and translate rows into
   response shapes; they never build a ``WHERE``. A second definition of a
   scoping predicate is how a cross-tenant leak gets in, and it is invisible
   in review because both copies look right.
2. **A method with no backing raises.** Returning ``None`` or ``[]`` from an
   unimplemented read renders in the UI as "no data" and is indistinguishable
   from a genuine empty result -- a user would read "no conflicts on this
   case" from a method that was never written.
3. **``db_ok`` is a cached bit.** ``GET /v1/version`` is unauthenticated
   (section 8.2), so a readiness probe that queried CockroachDB per call
   would be an availability oracle for anyone holding the URL.
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.control_plane.app.api import ports
from services.control_plane.app.api.ports import OwnerScope

pytestmark = pytest.mark.unit

ADAPTERS_PKG = Path(__file__).resolve().parents[2] / "app" / "api" / "adapters"

#: The two adapter modules permitted to hold a statement of their own, and the
#: reason each one is here rather than in ``provenance_db.repositories``.
#:
#: ``directory.py`` resolves ``cognito_sub`` -> ``users`` and a capability id ->
#: its owning row. Both reads *produce* the scope rather than consuming one, so
#: neither can satisfy the repository package's guard
#: (``test_no_read_signature_omits_both_a_principal_and_an_explicit_pair``),
#: which requires every public read to take a principal or an explicit
#: ``(tenant_id, user_id)`` pair. There is no scoping predicate to duplicate:
#: these statements are where a scope first comes from.
#:
#: ``catalog.py`` reads ``information_schema`` and issues the readiness probe.
#: Neither touches a user-owned table at all.
SQL_BEARING_MODULES: frozenset[str] = frozenset({"directory.py", "catalog.py"})

#: Tables only the Memory Kernel may write (``10_DATABASE_DDL.md`` section 12).
CANONICAL_TABLES: frozenset[str] = frozenset(
    {
        "counterparties",
        "relationships",
        "contexts",
        "cases",
        "evidence_items",
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "conflicts",
        "commitments",
        "fulfillments",
        "state_transitions",
        "memory_proposals",
        "kernel_decisions",
        "prospective_triggers",
        "outbox_events",
    }
)

_WRITE_VERB = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_]*)", re.I)
_SELECT_WITH_FROM = re.compile(r"(?is)\bSELECT\b.*?\bFROM\b")

SCOPE = OwnerScope(
    tenant_id=uuid.UUID("eaf56bfd-2fa3-5de4-bf55-34478e87b351"),
    user_id=uuid.UUID("88f54715-2808-58e8-8591-93f515ee21ba"),
)
CASE_ID = uuid.UUID("018f8a10-4c22-7f31-9b7d-2ac1e5f09b41")

#: An opaque key of the shape section 9.8 stores when the Advocate supplies
#: one. Deliberately not derivable from the intent: a fixture whose key
#: happens to equal a mint over the row cannot catch a minted-key defect.
_ADVOCATE_KEY = "advocate-run-9f31-attempt-1"

#: The counterparty address, allowlisted by default and withdrawn by one
#: test. Named once so a refusal test cannot pass because it quietly used a
#: different address from the one the fixture allowlisted.
_ALLOWED_RECIPIENT = "billing@northlinefiber.example"

#: One supporting belief version, so `SUPPORT_BELIEF_SUPERSEDED` has
#: something to supersede. An empty tuple is vacuously a subset of every
#: set, which would make that gate unassertable through the adapter.
_SUPPORT_ID = uuid.UUID("018f8a10-4c22-7f31-9b7d-2ac1e5f09c02")


# ==========================================================================
# A fake connection, so the adapters can be driven with no cluster
# ==========================================================================


class _Column:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _Cursor:
    def __init__(self, recorder: RecordingConnection) -> None:
        self._recorder = recorder
        self.description: tuple[_Column, ...] | None = None
        self._rows: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        self._recorder.statements.append((sql, params))
        columns, rows = self._recorder.answer(sql)
        self.description = tuple(_Column(name) for name in columns)
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class RecordingConnection:
    """Every statement the adapter issued, and canned rows for each.

    Answers are keyed on a substring of the statement so a test can seed one
    projection without transcribing the whole query.
    """

    def __init__(
        self, answers: Sequence[tuple[str, list[str], list[tuple[Any, ...]]]] = ()
    ) -> None:
        self.statements: list[tuple[str, Any]] = []
        self._answers = list(answers)

    def answer(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        for needle, columns, rows in self._answers:
            if needle in sql:
                return columns, rows
        return [], []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    async def rollback(self) -> None:  # pragma: no cover - no retry in these tests
        return None


class RecordingSource:
    """A :class:`ConnectionSource` over one :class:`RecordingConnection`."""

    def __init__(self, conn: RecordingConnection) -> None:
        self.conn = conn
        self.checkouts = 0

    def connection(self) -> Any:
        source = self

        class _Ctx:
            async def __aenter__(self) -> RecordingConnection:
                source.checkouts += 1
                return source.conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


# ==========================================================================
# 1. Every protocol method exists, and none of them lies about having data
# ==========================================================================


def _protocol_methods(protocol: type) -> list[str]:
    return sorted(
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and inspect.isfunction(value)
    )


def _adapters() -> Any:
    from services.control_plane.app.api import adapters

    return adapters


@pytest.mark.parametrize(
    ("protocol", "factory"),
    [
        (ports.ReadPort, "SqlReadPort"),
        (ports.WritePort, "KernelWritePort"),
        (ports.InternalPort, "KernelInternalPort"),
        (ports.UserDirectory, "SqlUserDirectory"),
    ],
)
def test_every_protocol_method_is_present_on_its_adapter(protocol: type, factory: str) -> None:
    """A missing method is an ``AttributeError`` at request time, in production.

    The protocols are structural, so nothing checks this at import; that is
    what makes it worth asserting once, here, over the whole surface.
    """
    adapter = getattr(_adapters(), factory)
    missing = [name for name in _protocol_methods(protocol) if not hasattr(adapter, name)]
    assert missing == [], f"{factory} does not implement: {missing}"


def test_the_four_adapters_cover_all_forty_seven_methods() -> None:
    """The vacuity guard. ``0 missing`` over ``0`` methods is a suite that
    stopped seeing the surface it was written to protect."""
    total = sum(
        len(_protocol_methods(p))
        for p in (ports.ReadPort, ports.WritePort, ports.InternalPort, ports.UserDirectory)
    )
    assert total == 47, f"the port surface moved: {total} methods, not 47"


@pytest.mark.parametrize(
    ("protocol", "factory"),
    [
        (ports.ReadPort, "SqlReadPort"),
        (ports.WritePort, "KernelWritePort"),
        (ports.InternalPort, "KernelInternalPort"),
    ],
)
def test_every_method_is_async(protocol: type, factory: str) -> None:
    adapter = getattr(_adapters(), factory)
    not_async = [
        name
        for name in _protocol_methods(protocol)
        if not inspect.iscoroutinefunction(getattr(adapter, name))
    ]
    assert not_async == [], f"{factory} has non-async members: {not_async}"


def test_an_unbound_method_raises_and_names_what_it_needs() -> None:
    """Rule 2 of the module docstring, asserted on the declared list.

    ``UNBOUND`` is the adapters' own statement of what is not wired yet. The
    test requires each entry to be real -- the attribute exists -- and each
    message to name a subsystem, so the failure is actionable rather than a
    bare ``NotImplementedError``.
    """
    adapters = _adapters()
    assert adapters.UNBOUND, "no method is declared unbound; the register is vacuous"
    for qualified, reason in adapters.UNBOUND.items():
        port, _, method = qualified.partition(".")
        adapter = getattr(
            adapters,
            {"read": "SqlReadPort", "write": "KernelWritePort", "internal": "KernelInternalPort"}[
                port
            ],
        )
        assert hasattr(adapter, method), f"{qualified} is registered but not defined"
        assert len(reason) > 30, f"{qualified} raises without naming what it needs: {reason!r}"


# ==========================================================================
# 2. The scoping predicate lives in the SQL, and the SQL lives in the
#    repositories
# ==========================================================================


def _adapter_modules() -> Iterator[Path]:
    for path in sorted(ADAPTERS_PKG.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _docstring_ids(tree: ast.AST) -> set[int]:
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(id(value))
    return found


def _sql_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and _SELECT_WITH_FROM.search(node.value)
    ]


def test_the_adapters_package_exists_and_is_scanned() -> None:
    modules = list(_adapter_modules())
    assert modules, f"no adapter modules under {ADAPTERS_PKG}"


def test_only_the_two_declared_modules_carry_sql() -> None:
    """Rule 1. Every user-scoped statement belongs to ``provenance_db``.

    The exemptions are the two scope-*producing* reads and the schema
    catalogue, enumerated in :data:`SQL_BEARING_MODULES` above with the reason
    each one cannot live in the repository package.
    """
    offenders = [
        path.name
        for path in _adapter_modules()
        if path.name not in SQL_BEARING_MODULES and _sql_constants(path)
    ]
    assert offenders == [], f"SQL in adapter modules that must hold none: {offenders}"


def test_no_exempt_statement_consumes_a_scope() -> None:
    """The exemption is bounded by *what the statement does with the owner*.

    These two modules hold scope-**producing** reads: they are keyed on a
    unique identifier the caller already holds -- a ``cognito_sub``, a
    capability id, an alias digest -- and they *select* ``tenant_id`` and
    ``user_id`` out of whichever row matches. So the invariant is not "which
    tables do they touch" but "do they ever bind an owner as a parameter".

    A statement here that bound ``%(tenant_id)s`` would be a second definition
    of a scoping predicate, which is the thing the whole indirection exists to
    prevent -- and it would also be circular, since these reads are where a
    scope comes from. Binding one is the exact failure this asserts against.
    """
    offenders: list[str] = []
    for path in _adapter_modules():
        if path.name not in SQL_BEARING_MODULES:
            continue
        for statement in _sql_constants(path):
            for parameter in ("%(tenant_id)s", "%(user_id)s"):
                if parameter in statement:
                    offenders.append(f"{path.name}: binds {parameter}")
    assert offenders == [], f"a scope-producing statement consumed a scope: {offenders}"


def test_every_exempt_statement_is_keyed_on_a_unique_identifier() -> None:
    """The other half: a scope-producing read must select exactly one row.

    ``users`` is unique on ``cognito_sub``, ``ingest_aliases`` on
    ``alias_hash``, and the three capability tables on their primary key. A
    statement here keyed on anything else would return rows belonging to
    several owners and the caller would take the first, which is a cross-user
    leak with no predicate missing anywhere.
    """
    unique_keys = ("cognito_sub", "alias_hash", "capability_id", "table_schema")
    offenders: list[str] = []
    for path in _adapter_modules():
        if path.name not in SQL_BEARING_MODULES:
            continue
        for statement in _sql_constants(path):
            if not any(key in statement for key in unique_keys):
                offenders.append(f"{path.name}: {statement[:80]}")
    assert offenders == [], f"a scope-producing statement is not keyed uniquely: {offenders}"


def test_no_adapter_writes_a_canonical_table() -> None:
    """``tools/write_path_lint.py`` checks this across the tree; asserting it
    here puts the failure in the suite of the task that could cause it."""
    offenders: list[str] = []
    for path in _adapter_modules():
        for statement in _sql_constants(path):
            for verb, table in _WRITE_VERB.findall(statement):
                if table.lower() in CANONICAL_TABLES:
                    offenders.append(f"{path.name}: {verb.upper()} {table}")
    assert offenders == [], "\n".join(offenders)


# ==========================================================================
# 3. The hero reads actually bind the owner
# ==========================================================================


async def test_case_revision_binds_tenant_and_user() -> None:
    conn = RecordingConnection([("FROM cases", ["revision"], [(13,)])])
    port = _adapters().SqlReadPort(RecordingSource(conn))

    revision = await port.case_revision(SCOPE, CASE_ID)

    assert revision == 13
    sql, params = conn.statements[0]
    assert "tenant_id = %(tenant_id)s" in sql
    assert "user_id = %(user_id)s" in sql
    assert params["tenant_id"] == SCOPE.tenant_id
    assert params["user_id"] == SCOPE.user_id


async def test_a_case_belonging_to_another_user_reads_as_absent() -> None:
    """Section 1.7: ``None`` is "no such row *for this scope*", and the route
    turns it into a typed 404 rather than a 403."""
    conn = RecordingConnection()
    port = _adapters().SqlReadPort(RecordingSource(conn))

    assert await port.case_revision(SCOPE, CASE_ID) is None
    assert await port.get_case(SCOPE, CASE_ID) is None


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: p.me(SCOPE), id="me"),
        pytest.param(lambda p: p.dashboard(SCOPE), id="dashboard"),
        pytest.param(lambda p: p.list_cases(SCOPE, limit=25), id="list_cases"),
        pytest.param(lambda p: p.get_case(SCOPE, CASE_ID), id="get_case"),
        pytest.param(lambda p: p.case_revision(SCOPE, CASE_ID), id="case_revision"),
        pytest.param(lambda p: p.list_commitments(SCOPE, limit=25), id="list_commitments"),
        pytest.param(lambda p: p.list_contexts(SCOPE, limit=25), id="list_contexts"),
        pytest.param(lambda p: p.list_relationships(SCOPE, limit=25), id="list_relationships"),
        pytest.param(lambda p: p.list_triggers(SCOPE, limit=25), id="list_triggers"),
        pytest.param(lambda p: p.list_artifacts(SCOPE, limit=25), id="list_artifacts"),
        pytest.param(lambda p: p.list_action_intents(SCOPE, limit=25), id="list_action_intents"),
        pytest.param(lambda p: p.ingest_alias(SCOPE), id="ingest_alias"),
    ],
)
async def test_every_bound_read_binds_the_owner_on_every_statement(call: Any) -> None:
    """The property the whole indirection exists for, over the hero surface.

    Not "the first statement is scoped" -- *every* statement. A read model
    assembled from four queries leaks through whichever one was written last.
    """
    conn = RecordingConnection()
    port = _adapters().SqlReadPort(RecordingSource(conn))

    await call(port)

    assert conn.statements, "the adapter issued no statement at all"
    for sql, params in conn.statements:
        assert "tenant_id" in sql and "user_id" in sql, f"unscoped statement: {sql[:120]}"
        assert params["tenant_id"] == SCOPE.tenant_id
        assert params["user_id"] == SCOPE.user_id


async def test_case_scoped_reads_return_none_for_a_case_the_scope_does_not_own() -> None:
    conn = RecordingConnection()
    port = _adapters().SqlReadPort(RecordingSource(conn))

    assert await port.list_timeline(SCOPE, CASE_ID, limit=25) is None
    assert await port.list_conflicts(SCOPE, CASE_ID, limit=25) is None
    assert await port.state_proof(SCOPE, CASE_ID) is None


# ==========================================================================
# 4. db_ok
# ==========================================================================


async def test_db_ok_is_a_cached_bit_and_queries_nothing_when_read() -> None:
    """Section 8.2. ``/v1/version`` is unauthenticated; a probe per call turns
    it into an availability oracle for anyone with the URL."""
    conn = RecordingConnection([("SELECT 1", ["ok"], [(1,)])])
    source = RecordingSource(conn)
    health = _adapters().DbHealth(source)

    assert health.ok() is False  # nothing has been observed yet
    for _ in range(50):
        health.ok()
    assert conn.statements == [], "reading the bit issued a query"
    assert source.checkouts == 0, "reading the bit took a connection from the pool"

    await health.refresh()
    assert health.ok() is True
    assert len(conn.statements) == 1


async def test_db_ok_clears_on_stop() -> None:
    """Once nothing is observing, ``true`` is a claim about a measurement that
    stopped happening. A draining process answers ``false``."""
    conn = RecordingConnection([("SELECT 1", ["ok"], [(1,)])])
    health = _adapters().DbHealth(RecordingSource(conn))

    await health.refresh()
    assert health.ok() is True
    await health.stop()
    assert health.ok() is False


async def test_db_ok_goes_false_when_the_probe_fails() -> None:
    class _Failing(RecordingSource):
        def connection(self) -> Any:
            raise RuntimeError("cluster unreachable")

    health = _adapters().DbHealth(_Failing(RecordingConnection()))
    await health.refresh()
    assert health.ok() is False


# ==========================================================================
# 5. The action plane (Phase 9), bound through app/actions
# ==========================================================================
#
# Authority: `specs/15_API_SPEC.md` sections 8.25-8.27, 9.8 and 9.11, and
# `services/control_plane/app/actions/__init__.py`, which states the binding
# contract these tests hold the adapter to.
#
# The interesting risk here is not "does approve() call approve()". It is the
# *error* translation. `app/actions` raises `ActionRefusedError(reason_code,
# **details)` and deliberately does not import `ErrorCode` -- that keeps the
# action plane free of `app/api`, and it makes the mapping this adapter's
# responsibility. A mapping that swallows an unknown reason code turns a
# precise refusal into a 500, and a mapping that drifts when the action plane
# adds a code fails in production rather than in CI.


def _action_reason_codes() -> dict[str, str]:
    """Every refusal code ``app/actions`` can raise, read from its own modules.

    Enumerated from the ``Final[str]`` constants rather than transcribed,
    because a transcribed list is a second definition that goes stale in
    exactly the situation it exists to cover: the action plane adding a code.
    """
    from services.control_plane.app.actions import intents, policy, support_validation

    found: dict[str, str] = {}
    for module in (intents, policy, support_validation):
        for name in dir(module):
            if not name.isupper() or name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, str) and value == name:
                found[value] = module.__name__
    return found


def test_the_action_reason_code_scan_finds_codes_at_all() -> None:
    """Vacuity guard. An empty scan makes every assertion below pass forever."""
    codes = _action_reason_codes()
    assert len(codes) >= 10, f"the reason-code scan found only {sorted(codes)}"
    assert "ACTION_STALE" in codes
    assert "NO_COMMITTED_BASIS" in codes


def test_every_action_reason_code_has_a_mapping() -> None:
    """The drift guard, and the reason this file scans instead of listing.

    When Phase 9 adds a refusal code, this fails here -- in the unit lane, on
    the change that introduced it -- rather than as an unmapped
    ``500 INTERNAL_ERROR`` the first time a user reaches that path in the demo.
    """
    from services.control_plane.app.api.adapters import action_errors

    unmapped = sorted(set(_action_reason_codes()) - set(action_errors.REFUSAL_STATUS))
    assert unmapped == [], (
        f"app/actions can raise {unmapped}, and adapters/action_errors.py has no "
        "entry for them. An unmapped reason code reaches the client as a 500."
    )


@pytest.mark.parametrize(
    ("reason_code", "status"),
    [
        ("ACTION_NOT_APPROVABLE", 409),
        ("ACTION_DRAFT_FROZEN", 409),
        ("ACTION_STALE", 409),
        ("ACTION_ALREADY_EXECUTED", 409),
        ("IDEMPOTENCY_CONFLICT", 409),
        ("NO_COMMITTED_BASIS", 409),
        ("VALIDATION_FAILED", 422),
        ("RECIPIENT_NOT_ALLOWLISTED", 422),
        ("DRAFT_CLAIM_UNSUPPORTED", 422),
        ("RISK_TIER_NOT_PERMITTED", 422),
        ("BASIS_REVISION_MISMATCH", 422),
        ("BASIS_CASE_MISMATCH", 422),
        # The only 5xx in the table: the store never loaded a citation set, so
        # the grounding question was not asked. Not the caller's fault, and not
        # fixable by the caller sending anything different.
        ("SUPPORT_SET_UNAVAILABLE", 500),
    ],
)
def test_an_action_refusal_maps_to_the_documented_status(reason_code: str, status: int) -> None:
    """The table Phase 9 handed over, asserted rather than trusted."""
    from services.control_plane.app.actions import ActionRefusedError
    from services.control_plane.app.api.adapters import action_errors
    from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS

    error = action_errors.as_api_error(ActionRefusedError(reason_code, sample="detail"))
    assert (
        DEFAULT_HTTP_STATUS[error.code] == status
    ), f"{reason_code} -> {error.code} is not {status}"


def test_a_missing_support_set_never_renders_as_an_ungrounded_draft() -> None:
    """The one collapse that would be invisible in a response body.

    ``app/actions`` split ``SUPPORT_SET_UNAVAILABLE`` out of
    ``DRAFT_CLAIM_UNSUPPORTED`` because a store that never loaded a citation
    set would otherwise report every claim in every draft as unsupported --
    rendering *identically* to a correctly refused ungrounded draft, which is
    this product's headline refusal. One is the system working. The other is
    the system not knowing and saying so anyway, in the same words.

    Mapping both onto ``DRAFT_UNSUPPORTED_CLAIM`` here would rebuild that
    collapse one layer up, immediately after the action plane took it apart.
    Three assertions rather than one, because "the codes differ" would still
    pass with both of them 4xx.
    """
    from services.control_plane.app.actions import ActionRefusedError
    from services.control_plane.app.api.adapters import action_errors
    from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS, ErrorCode

    unavailable = action_errors.as_api_error(ActionRefusedError("SUPPORT_SET_UNAVAILABLE"))
    unsupported = action_errors.as_api_error(ActionRefusedError("DRAFT_CLAIM_UNSUPPORTED"))

    assert unavailable.code is not unsupported.code
    assert unavailable.code is not ErrorCode.DRAFT_UNSUPPORTED_CLAIM
    assert (
        DEFAULT_HTTP_STATUS[unavailable.code] >= 500
    ), "a store that did not load the support set is not a client error"
    assert DEFAULT_HTTP_STATUS[unsupported.code] == 422
    # And it must not have taken the 409 fallback, which is what an unmapped
    # code silently does and which would read as plausible in a log.
    assert unavailable.code is not ErrorCode.ACTION_NOT_APPROVABLE
    assert unavailable.details is not None
    assert unavailable.details["reason_code"] == "SUPPORT_SET_UNAVAILABLE"


def test_a_server_side_refusal_is_not_tracked_as_an_approximation() -> None:
    """``INTERNAL_ERROR`` is the right code here, not the nearest one.

    Section 4.3's catalogue is closed and public. Enumerating internal defects
    in it would describe our plumbing to callers while giving them nothing to
    act on, since the remedy is a deploy rather than a different request. This
    entry is therefore deliberately absent from :data:`APPROXIMATED`, and
    asserting the absence stops somebody adding it there and then "closing the
    gap" by growing the public catalogue.
    """
    from services.control_plane.app.api.adapters import action_errors

    assert "SUPPORT_SET_UNAVAILABLE" not in action_errors.APPROXIMATED


def test_the_true_reason_code_always_survives_into_details() -> None:
    """Two of Phase 9's codes have no ``ErrorCode`` member.

    ``NO_COMMITTED_BASIS`` (``G9.6`` requires ``409 NO_COMMITTED_BASIS``) and
    ``RISK_TIER_NOT_PERMITTED`` are not in ``app/api/errors.py``, which this
    task does not own. Mapping them onto a neighbouring code is the honest
    interim: the status is right and the **exact** reason code still reaches
    the client, under ``details.reason_code``, so a judge reading the response
    sees the real refusal rather than an approximation of it.
    """
    from services.control_plane.app.actions import ActionRefusedError
    from services.control_plane.app.api.adapters import action_errors

    for code in sorted(_action_reason_codes()):
        error = action_errors.as_api_error(ActionRefusedError(code, extra="x"))
        assert error.details is not None, f"{code} lost its details"
        assert (
            error.details.get("reason_code") == code
        ), f"{code} did not survive into details; the client would see only {error.code}"
        assert error.details.get("extra") == "x", f"{code} dropped the refusal's own details"


def test_action_intent_not_found_is_none_and_not_an_error() -> None:
    """Section 1.7, and the route's own contract.

    ``routes/actions.py`` writes ``if row is None: raise absent(...)``. The
    adapter therefore returns ``None`` rather than raising, so the 404 is
    produced in one place. Raising here would also work, and would put two
    definitions of "another user's intent looks absent" in the codebase.
    """
    from services.control_plane.app.actions import ActionRefusedError
    from services.control_plane.app.api.adapters import action_errors

    assert action_errors.is_absent(ActionRefusedError("ACTION_INTENT_NOT_FOUND", x=1)) is True
    assert action_errors.is_absent(ActionRefusedError("ACTION_STALE")) is False


def test_the_action_methods_are_no_longer_declared_unbound() -> None:
    """Four of the five Phase 9 handed over are bound."""
    adapters = _adapters()
    for method in (
        "write.update_draft",
        "write.approve",
        "write.reject",
        "internal.execute_action",
    ):
        assert method not in adapters.UNBOUND, f"{method} is bound but still declared unbound"


#: The ``DraftAction`` fields section 9.8's request body cannot fill on its own.
#:
#: Deliberately **not** called irreducible, which is what an earlier version of
#: this file called them and was wrong to. Three of the four are sourceable from
#: persisted state and one is on the wire already; what stops the binding is
#: narrower than this set, and :data:`UNSOURCEABLE_AT_INTENT_TIME` names it.
#:
#: ``draft_id``, ``claim_id`` and the claim character offsets are absent from
#: this set on purpose: a fresh uuid is an identity rather than an assertion,
#: and the offsets are recoverable by locating the quoted span in the body.
BODY_CANNOT_FILL: frozenset[str] = frozenset(
    {"generated_by", "support_kind", "requested_outcome", "basis_proof_hash"}
)

#: What the server genuinely cannot source at the moment section 9.8 runs.
#:
#: ``prompt_version`` is the last of ``ModelAttribution``'s six fields. The
#: other five are on the ``agent_runs`` row ``agent_run_id`` already names --
#: ``graph_name`` and ``graph_version`` are ``NOT NULL`` columns, ``model_id``
#: is ``model_route.tier_r``, ``provider`` follows from the id shape the
#: contract's validator dispatches on, and ``tier`` is R. ``prompt_version``
#: lives on ``agent_runs.model_calls[]``, which only
#: ``POST /internal/v1/agent-runs/{id}/complete`` (section 9.9) writes. Section
#: 9.8 runs before the run completes, so the column is null by **ordering**.
#:
#: ``support_kind`` and ``basis_proof_hash`` are derivable from a State Proof
#: and are not derivable from what ``ActionStore.grounding_snapshot`` hands this
#: adapter, which is a flat ``frozenset[UUID]`` carrying neither the kind nor
#: the hash.
UNSOURCEABLE_AT_INTENT_TIME: frozenset[str] = frozenset(
    {"prompt_version", "support_kind", "basis_proof_hash"}
)


def _draft_action_gaps() -> frozenset[str]:
    """Try to build a ``DraftAction`` from everything section 9.8's body carries.

    Returns the field names pydantic reports missing. This is the claim under
    ``internal.create_action_intent``'s register entry, **executed** rather than
    transcribed: a register entry is prose, and prose describing a schema drifts
    from the schema silently. Everything the adapter could legitimately mint is
    supplied here, so what comes back is the irreducible remainder rather than a
    list padded with fields nobody was blocked on.
    """
    from pydantic import ValidationError

    from provenance_contracts.actions import DraftAction
    from services.control_plane.app.api.schemas.internal import AdvocacyActionIntentRequest

    request = AdvocacyActionIntentRequest(
        agent_run_id=uuid.uuid4(),
        case_id=CASE_ID,
        basis_case_revision=13,
        action_type="OUTBOUND_EMAIL_DISPUTE",
        recipient="billing@northlinefiber.example",
        draft={
            "subject": "Disputed invoice 88431",
            "body": "The deposit of USD 186.00 is overdue.",
            "claims": [
                {
                    "sentence_or_span": "The deposit of USD 186.00 is overdue.",
                    "support_ids": [str(uuid.uuid4())],
                }
            ],
        },
        rationale="A counterparty claim contradicts a written confirmation.",
        supporting_belief_versions=[str(uuid.uuid4())],
    )
    body = request.draft.model_dump()
    claim = dict(body["claims"][0])
    claim.update(claim_id="dc_1", char_start=0, char_end=len(claim["sentence_or_span"]))
    try:
        DraftAction(
            draft_id=uuid.uuid4(),
            case_id=request.case_id,
            basis_case_revision=request.basis_case_revision,
            action_type=request.action_type,
            recipient=request.recipient,
            subject=body["subject"],
            body=body["body"],
            claims=(claim,),
            generated_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        )
    except ValidationError as exc:
        return frozenset(
            str(error["loc"][-1]) for error in exc.errors() if error["type"] == "missing"
        )
    return frozenset()


def test_the_draft_action_probe_is_not_vacuous() -> None:
    """The guard on the guard.

    An empty return from :func:`_draft_action_gaps` is the *success* signal, so
    a probe that silently stopped constructing anything -- a renamed schema, a
    swallowed import -- would read as "the blocker is gone", and this file would
    then be demanding that an unbindable method be bound.
    """
    assert _draft_action_gaps(), (
        "AdvocacyActionIntentRequest now fills every DraftAction field. If that "
        "is real, bind internal.create_action_intent and delete its UNBOUND entry."
    )


def test_the_model_attribution_is_sourced_from_the_run_row_not_the_request() -> None:
    """Section 9.8's missing ``model`` block is a security property.

    If the request body carried one, a caller could **claim** which model ran.
    ``agent_runs.model_route`` is what makes the submission's model disclosure
    checkable against persisted state rather than against a README, and
    ``CANONICAL_DECISIONS.md`` -> *Disclosure* calls claiming a model you did
    not run exactly the kind of small checkable dishonesty this pack exists to
    prevent. So "add a ``model`` field to the schema" is the wrong fix, and this
    test exists to stop somebody proposing it after reading the register entry
    too quickly.

    Asserted in both directions: the body offers no way to claim one, and the
    row genuinely carries five of the six fields, so the register's claim that
    only ``prompt_version`` is missing is checked rather than trusted.
    """
    from provenance_db.repositories import agent_runs
    from services.control_plane.app.api.schemas.internal import (
        AdvocacyActionIntentRequest,
        AgentRunCompleteRequest,
        ModelCallRecord,
    )

    fields = set(AdvocacyActionIntentRequest.model_fields)
    assert not fields & {
        "model",
        "generated_by",
        "model_route",
        "prompt_version",
    }, "section 9.8's body now lets a caller assert its own model attribution"
    assert AdvocacyActionIntentRequest.model_config.get("extra") == "forbid"

    projection = agent_runs.AGENT_RUN_SQL if hasattr(agent_runs, "AGENT_RUN_SQL") else ""
    source = projection or inspect.getsource(agent_runs)
    for column in ("graph_name", "graph_version", "model_route"):
        assert column in source, f"agent_runs no longer projects {column}"

    # And the sixth: `prompt_version` is reachable only through section 9.9's
    # completion body, which settles the run *after* section 9.8 has had to
    # create the intent. That ordering is the whole blocker.
    #
    # Re-derived 2026-08-24, when section 9.9 was bound. The earlier version of
    # this assertion was `"internal.complete_agent_run" in UNBOUND` and it went
    # red on that binding -- correctly, because it pinned a *state* and the
    # state legitimately changed. It says nothing about whether the blocker
    # moved. The property does: `agent_runs.model_calls` still has exactly one
    # writer, that writer is still the run-completion statement, and section
    # 9.8 still runs before it. Asserting the property keeps both directions
    # checked instead of deleting the guard the moment there is something to
    # guard (`STATUS.md` section 7).
    assert "prompt_version" in ModelCallRecord.model_fields
    assert "model_calls" in AgentRunCompleteRequest.model_fields

    writers = sorted(
        path.name
        for path in _control_plane_modules()
        if "model_calls = %(model_calls)s" in path.read_text(encoding="utf-8")
    )
    assert writers == ["runs.py"], (
        f"agent_runs.model_calls now has {len(writers)} writers ({writers}); "
        "if one of them runs before section 9.8, prompt_version is no longer "
        "blocked by ordering and internal.create_action_intent should be "
        "re-derived"
    )
    assert "internal.create_action_intent" in _adapters().UNBOUND


def test_create_action_intent_is_still_unbound_and_says_why() -> None:
    """The one that stays unbound, and the register names the real reason.

    Not "section 9.8's body is missing a model block" -- that omission is
    deliberate and correct, and :func:`test_the_model_attribution_is_sourced_from_the_run_row_not_the_request`
    holds it in place. What actually blocks the binding is narrower and in two
    parts, both of them ordering or plumbing rather than schema:

    * ``prompt_version`` is the one ``ModelAttribution`` field on no row this
      request can reach. It arrives on ``agent_runs.model_calls[]`` at section
      9.9, which settles the run *after* section 9.8 must have created the
      intent.
    * ``support_kind`` and ``basis_proof_hash`` are derivable from a State
      Proof, and ``ActionStore.grounding_snapshot`` hands this adapter a flat
      ``frozenset[UUID]`` carrying neither. Re-reading the proof here to
      recover them would build a second definition of "why does Provenance
      believe this", which is the exact duplication
      ``internal.run_state_proof`` delegates to the read port to avoid.

    The last assertion is the one that would have caught the earlier, wrong
    version of this entry: the register must **not** claim
    ``requested_outcome`` blocks anything. It is on the wire already, optional,
    and an absent one is a ``422``, not an invitation to invent a value. Naming
    a non-blocker as a blocker is how a register sends somebody to change a
    schema that did not need changing.
    """
    adapters = _adapters()
    assert "internal.create_action_intent" in adapters.UNBOUND
    reason = adapters.UNBOUND["internal.create_action_intent"]
    assert (
        _draft_action_gaps() == BODY_CANNOT_FILL
    ), "the DraftAction gap moved; the register entry now describes a different world"
    unnamed = sorted(field for field in UNSOURCEABLE_AT_INTENT_TIME if field not in reason)
    assert unnamed == [], (
        f"internal.create_action_intent is blocked on {unnamed}, and its UNBOUND "
        "entry does not name them. A register that names half a gap sends the "
        "reader to grep for the other half."
    )
    assert "requested_outcome" in reason and "NOT blockers" in reason, (
        "the register must say which of the four fields are NOT blocking; "
        "listing requested_outcome among them sends somebody to widen a schema "
        "that already carries it"
    )


# --------------------------------------------------------------------------
# The approval round trip, through the real Phase 9 service
# --------------------------------------------------------------------------
#
# `InMemoryActionStore` is exported by `app/actions` for exactly this: it lets
# the adapter be driven against the *real* `ActionIntentService`, its real
# revalidation and its real refusals, with no cluster. Mocking the service
# instead would test that the adapter can call a mock.


async def _seeded_intent_port(
    *, basis: int = 13, case_revision: int | None = None
) -> tuple[Any, Any]:
    """A write port over an in-memory store holding one approvable intent."""
    from datetime import UTC, datetime

    from services.control_plane.app.actions import (
        ActionPolicy,
        ActionScope,
        InMemoryActionStore,
        drafts,
    )
    from services.control_plane.app.actions.store import NewActionIntent
    from services.control_plane.app.actions.support_validation import GroundingSnapshot

    now = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
    store = InMemoryActionStore()
    action_scope = ActionScope(tenant_id=SCOPE.tenant_id, user_id=SCOPE.user_id)
    draft = {"subject": "Disputed invoice 88431", "body": "Hello,\n\nPlease cancel it.\n\nAlex"}
    intent_id = uuid.UUID("018f9c2f-1111-7abc-8def-000000000001")
    await store.insert_intent(
        action_scope,
        NewActionIntent(
            id=intent_id,
            case_id=CASE_ID,
            action_type="OUTBOUND_EMAIL_DISPUTE",
            recipient="billing@northlinefiber.example",
            draft_payload=draft,
            draft_sha256=drafts.draft_digest(draft),
            rationale="A counterparty claim contradicts a written confirmation.",
            supporting_belief_versions=(),
            basis_case_revision=basis,
            status="NEEDS_REVIEW",
            risk_tier=3,
            idempotency_key="seed-intent-key-000000001",
        ),
        now=now,
    )
    store.put_snapshot(
        action_scope,
        GroundingSnapshot(
            case_id=CASE_ID,
            case_revision=basis if case_revision is None else case_revision,
            support_ids=frozenset(),
            current_belief_version_ids=frozenset(),
            has_committed_kernel_decision=True,
        ),
    )
    port = _adapters().KernelWritePort(
        RecordingSource(RecordingConnection()),
        kernel_pool=None,
        read=_StubRead(),
        policy=ActionPolicy(
            allowlist=frozenset({"billing@northlinefiber.example"}),
            execution_mode="ENABLED",
            recipient_mode="DIRECT",
        ),
        clock=lambda: now,
        store_factory=lambda _conn: store,
    )
    return port, intent_id


class _StubRead:
    """Only ``case_revision`` is reached by the write port."""

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None:
        del scope, case_id
        return 13


class _Approve:
    """The shape ``routes/actions.py`` hands the port for section 8.26."""

    def __init__(self, subject: str, body: str, revision: int) -> None:
        self.approved_draft = {"subject": subject, "body": body}
        self.client_case_revision = revision
        self.acknowledge_warnings: list[str] = []


async def test_approve_round_trips_through_the_real_action_service() -> None:
    """Not a mock: the real service, its real revalidation, its real store."""
    port, intent_id = await _seeded_intent_port()

    row = await port.approve(
        SCOPE,
        intent_id,
        _Approve("Disputed invoice 88431", "Hello,\n\nPlease cancel it.\n\nAlex", 13),
    )

    assert row is not None
    assert row["status"] == "APPROVED"
    assert row["approved_case_revision"] == 13
    # 64 hex characters: the digest of the draft the *client* submitted, which
    # is what the executor re-checks in section 9.11.
    assert isinstance(row["approval_draft_sha256"], str)
    assert len(row["approval_draft_sha256"]) == 64


async def test_approve_takes_the_approver_from_the_scope() -> None:
    """The authorisation boundary, asserted at the adapter.

    ``ApproveRequest`` carries ``approved_by_user_id`` and the HTTP body does
    not. If the adapter ever read one from the payload, a caller could name
    somebody else as the approver of an outbound letter.
    """
    from services.control_plane.app.actions import ActionScope

    port, intent_id = await _seeded_intent_port()
    payload = _Approve("Disputed invoice 88431", "Hello,\n\nPlease cancel it.\n\nAlex", 13)
    payload.approved_by_user_id = uuid.uuid4()  # type: ignore[attr-defined]

    await port.approve(SCOPE, intent_id, payload)

    store = port._store(None)
    intent = await store.load_intent(
        ActionScope(tenant_id=SCOPE.tenant_id, user_id=SCOPE.user_id), intent_id
    )
    assert intent is not None
    assert intent.approved_by_user_id == SCOPE.user_id


async def test_a_stale_approval_is_refused_and_carries_what_moved() -> None:
    """Section 7.3: "the case moved" is useless to a user who cannot see what."""
    from services.control_plane.app.api.errors import ApiError

    port, intent_id = await _seeded_intent_port(basis=13, case_revision=14)

    with pytest.raises(ApiError) as raised:
        await port.approve(SCOPE, intent_id, _Approve("s", "b", 13))

    assert raised.value.code.value == "ACTION_STALE"
    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "ACTION_STALE"
    assert raised.value.details.get("current_case_revision") == 14


async def test_an_intent_for_another_user_reads_as_absent() -> None:
    """Section 1.7 again, on the mutating surface: ``None``, so the route 404s."""
    port, _ = await _seeded_intent_port()
    other = OwnerScope(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    assert await port.approve(other, uuid.uuid4(), _Approve("s", "b", 13)) is None


# --------------------------------------------------------------------------
# Execution, through the real Phase 9 executor
# --------------------------------------------------------------------------


class _Binding:
    """The fields ``execute_action`` reads off a ``CapabilityBinding``."""

    def __init__(self, intent_id: uuid.UUID) -> None:
        self.binding_id = intent_id
        self.tenant_id = SCOPE.tenant_id
        self.user_id = SCOPE.user_id


class _Execute:
    """Section 9.11's request body."""

    def __init__(self, digest: str, revision: int) -> None:
        self.expected_draft_sha256 = digest
        self.expected_case_revision = revision


async def _approved_executor_port(
    *, execution_mode: str = "ENABLED", allowlist: frozenset[str] | None = None
) -> Any:
    """An internal port over a store holding one *approved* intent.

    Returns the port together with the store and the sink rather than the port
    alone. ``G9.1`` requires "provider calls made: 0, asserted against the
    sink's call log, not a mock counter", and ``G9.1`` also reads the
    ``action_executions`` row -- neither is reachable from a fixture that hands
    back only the thing under test. A refusal test that cannot see the sink is
    a test that asserts an exception type and calls it "never sends".
    """
    from datetime import UTC, datetime

    from services.control_plane.app.actions import (
        ActionPolicy,
        ActionScope,
        DemoSink,
        InMemoryActionStore,
        drafts,
    )
    from services.control_plane.app.actions.store import NewActionIntent
    from services.control_plane.app.actions.support_validation import GroundingSnapshot

    now = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
    store = InMemoryActionStore()
    sink = DemoSink()
    action_scope = ActionScope(tenant_id=SCOPE.tenant_id, user_id=SCOPE.user_id)
    draft = {"subject": "Disputed invoice 88431", "body": "Hello,\n\nPlease cancel it.\n\nAlex"}
    digest = drafts.draft_digest(draft)
    intent_id = uuid.UUID("018f9c2f-1111-7abc-8def-000000000002")
    await store.insert_intent(
        action_scope,
        NewActionIntent(
            id=intent_id,
            case_id=CASE_ID,
            action_type="OUTBOUND_EMAIL_DISPUTE",
            recipient=_ALLOWED_RECIPIENT,
            draft_payload=draft,
            draft_sha256=digest,
            rationale="A counterparty claim contradicts a written confirmation.",
            supporting_belief_versions=(_SUPPORT_ID,),
            basis_case_revision=13,
            status="NEEDS_REVIEW",
            risk_tier=3,
            idempotency_key=_ADVOCATE_KEY,
        ),
        now=now,
    )
    store.put_snapshot(
        action_scope,
        GroundingSnapshot(
            case_id=CASE_ID,
            case_revision=13,
            support_ids=frozenset({_SUPPORT_ID}),
            current_belief_version_ids=frozenset({_SUPPORT_ID}),
            has_committed_kernel_decision=True,
        ),
    )
    # Approve an EDITED body, so `approval_draft_sha256` moves off the
    # creation digest. Together with the opaque key above this makes a minted
    # idempotency key impossible to match by coincidence -- which is the only
    # reason the original defect looked green.
    approved = dict(draft, body="Hello,\n\nPlease cancel invoice 88431.\n\nAlex")
    approved_digest = drafts.draft_digest(approved)
    await store.record_approval(
        action_scope,
        intent_id,
        draft_payload=approved,
        draft_sha256=approved_digest,
        approved_by_user_id=SCOPE.user_id,
        approved_at=now,
    )
    await store.set_status(action_scope, intent_id, status="APPROVED")
    port = _adapters().KernelInternalPort(
        RecordingSource(RecordingConnection()),
        kernel_pool=None,
        read=_StubRead(),
        policy=ActionPolicy(
            allowlist=frozenset({_ALLOWED_RECIPIENT}) if allowlist is None else allowlist,
            execution_mode=execution_mode,
            recipient_mode="DIRECT",
        ),
        sink=sink,
        clock=lambda: now,
        store_factory=lambda _conn: store,
    )
    return SimpleNamespace(
        port=port,
        intent_id=intent_id,
        digest=approved_digest,
        store=store,
        sink=sink,
        scope=action_scope,
    )


async def test_the_approved_intent_is_actually_sendable_before_anything_is_moved() -> None:
    """The vacuity guard for every refusal below.

    Each of those moves one fact and asserts the send is blocked. If the
    fixture were unsendable to begin with -- a stale revision, an
    unallowlisted recipient, a digest that never matched -- every one of them
    would pass while proving nothing at all.
    """
    fx = await _approved_executor_port()

    row = await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert row["status"] == "EXECUTED"
    assert len(fx.sink.messages) == 1


async def test_execute_reports_the_recorded_finish_time_not_the_adapter_clock() -> None:
    """Section 9.11's ``executed_at``.

    Phase 9 surfaces the ``action_executions.finished_at`` it wrote, so this
    is the time the attempt was *recorded*, not the time this adapter observed
    the return. Those are different facts and only the first is auditable.
    """
    fx = await _approved_executor_port()

    row = await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert row["status"] == "EXECUTED"
    assert row["executed_at"] is not None
    assert row["executed_at"] == fx.store.executions[0].finished_at
    assert row["provider"] == "SAFE_SINK"
    assert row["revalidation"] == {
        "case_revision": 13,
        "draft_hash_match": True,
        "support_still_current": True,
        "recipient_allowlisted": True,
    }


# -- 9.11: the idempotency key is the row's, not one the adapter invents ---


async def test_the_execute_key_is_the_intents_own_and_survives_an_edited_approval() -> None:
    """Section 9.11: "The key **must** equal ``action_intents.idempotency_key``."

    The regression this guards is not hypothetical and not an edge case. The
    adapter used to *mint* a key from the intent identity plus the approved
    draft digest. That value equals the stored key only by coincidence, and
    two ordinary things break the coincidence: section 9.8 step 7 makes the
    row's key the Advocate's own **request** key whenever it supplied one, and
    a human changing a single word before approving moves
    ``approval_draft_sha256`` off the digest the intent was created with.
    Editing the draft before approving is the ordinary path through section
    8.26, not a corner of it.

    Either alone turned every execution of a legitimately approved intent into
    ``409 IDEMPOTENCY_CONFLICT`` -- an error shaped like a concurrency problem
    for a reason that has nothing to do with concurrency, which is exactly the
    kind of thing that gets debugged as a database fault. The fixture supplies
    both: an opaque caller-chosen key and an approval that edited the body.

    Passing the row's own key through cannot express the failure at all, and
    the sink's log is asserted rather than the return value, because the key
    is only load-bearing at the provider boundary.
    """
    fx = await _approved_executor_port()

    row = await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert row["status"] == "EXECUTED"
    assert fx.sink.messages[0].idempotency_key == _ADVOCATE_KEY


async def test_a_second_execute_replays_the_first_and_reaches_the_provider_once() -> None:
    """``G9.4``, through the adapter: two executes, one message.

    The correlation id is asserted equal rather than merely present. A replay
    that minted a new id would be indistinguishable from a second send in
    every record anybody would later consult.
    """
    fx = await _approved_executor_port()
    payload = _Execute(fx.digest.hex(), 13)

    first = await fx.port.execute_action(_Binding(fx.intent_id), payload)
    second = await fx.port.execute_action(_Binding(fx.intent_id), payload)

    assert len(fx.sink.messages) == 1, "the second execute reached the provider"
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["provider_correlation_id"] == first["provider_correlation_id"]
    assert second["action_execution_id"] == first["action_execution_id"]


# -- 9.11: the revalidation gate, one moved fact at a time ----------------


async def test_a_stale_execution_raises_and_never_sends() -> None:
    """Section 9.11: any revalidation failure is a 409 and nothing is sent.

    ``G9.1``'s shape exactly: approved at ``basis_case_revision = 13``, an
    unrelated Kernel commit moves the case to 14, the executor refuses. The
    sink's call log is asserted, not a mock counter, and so is the
    ``action_executions`` row -- "we refused to send this, at this revision,
    for this reason" is the record a person asking *why didn't it go out*
    needs, and an exception type alone does not leave one.
    """
    from services.control_plane.app.api.errors import ApiError

    fx = await _approved_executor_port()
    fx.store.advance_case_revision(fx.scope, CASE_ID, to=14)

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 14))

    assert raised.value.code.value == "ACTION_STALE"
    assert raised.value.details is not None
    # The blocking reason list is the whole point: "the case moved" is useless
    # to a human who cannot see *what* moved.
    assert "CASE_REVISION_MOVED" in raised.value.details["blocking_reasons"]
    assert raised.value.details["current_case_revision"] == 14
    assert fx.sink.calls == (), "a stale approval reached the provider"
    assert [(row.status, row.error_code) for row in fx.store.executions] == [
        ("ABORTED_STALE", "CASE_REVISION_MOVED")
    ]


async def test_a_tampered_stored_draft_blocks_the_send() -> None:
    """The draft-hash binding, moved on its own -- ``G9.2``'s second half.

    ``tamper_draft_payload`` changes the stored draft **without** touching
    ``approval_draft_sha256``: the state an edit that bypassed the approval
    freeze would leave behind. No supported code path produces it, which is
    precisely why the executor re-hashes the payload instead of trusting the
    column, and why proving the hash binding needs a fixture that can move one
    fact and leave every other one alone. Moving the revision instead would
    prove the revision binding twice and this one never.
    """
    from services.control_plane.app.api.errors import ApiError

    fx = await _approved_executor_port()
    fx.store.tamper_draft_payload(
        fx.scope,
        fx.intent_id,
        {"subject": "Disputed invoice 88431", "body": "Wire the money to me instead."},
    )

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "DRAFT_HASH_CHANGED"
    assert fx.sink.calls == ()
    assert fx.store.executions[0].error_code == "DRAFT_HASH_CHANGED"


async def test_a_caller_naming_the_wrong_digest_blocks_the_send() -> None:
    """``expected_draft_sha256`` is hex on the wire and bytes in the executor.

    The conversion is this adapter's (``_expected_digest``), so a caller naming
    a digest the row does not carry must still block. A conversion that
    silently produced ``None`` on an unexpected shape would turn the caller's
    strongest statement about what it believes it is approving into no
    statement at all -- and it would fail open, which is the direction that
    matters.
    """
    from services.control_plane.app.api.errors import ApiError

    fx = await _approved_executor_port()

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute("9a" * 32, 13))

    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "DRAFT_HASH_CHANGED"
    assert fx.sink.calls == ()


async def test_a_superseded_support_belief_blocks_the_send() -> None:
    """Contradicting evidence can arrive between the click and the send."""
    from services.control_plane.app.api.errors import ApiError

    fx = await _approved_executor_port()
    fx.store.supersede_belief_versions(fx.scope, CASE_ID, frozenset())

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "SUPPORT_BELIEF_SUPERSEDED"
    assert fx.sink.calls == ()


async def test_an_uncommitted_basis_answers_its_own_code_and_not_action_stale() -> None:
    """``G9.6``: an intent whose case has no committed ``kernel_decision``
    answers ``409 NO_COMMITTED_BASIS``.

    The code matters as much as the status, and a test asserting only the
    status could not tell the two apart -- both are 409. ``ACTION_STALE`` tells
    a client the world moved and to reload; ``NO_COMMITTED_BASIS`` says there
    was never a committed basis for the send to bind to. That is invariant 4,
    and a client that retries it is retrying the invariant. The two refusals
    lead to opposite next actions, which is why ``errors.py`` gained a member
    rather than borrowing a neighbour's.
    """
    from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS, ApiError, ErrorCode

    fx = await _approved_executor_port()
    fx.store.withdraw_committed_basis(fx.scope, CASE_ID)

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert raised.value.code is ErrorCode.NO_COMMITTED_BASIS
    assert raised.value.code is not ErrorCode.ACTION_STALE
    assert DEFAULT_HTTP_STATUS[raised.value.code] == 409
    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "NO_COMMITTED_BASIS"
    assert fx.sink.calls == ()


async def test_a_recipient_dropped_from_the_allowlist_is_422_and_not_a_stale_409() -> None:
    """``G9.5``, and section 9.11's error list, which carries ``422
    RECIPIENT_NOT_ALLOWED`` beside ``409 ACTION_STALE``.

    An operator narrowing the allowlist between approval and execution has not
    moved the case. Answering ``ACTION_STALE`` would send the client off to
    reload a case that will read exactly the same next time, and the reload
    would not change the answer -- a retry loop against a deliberate operator
    decision.
    """
    from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS, ApiError, ErrorCode

    fx = await _approved_executor_port(allowlist=frozenset())

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert raised.value.code is ErrorCode.RECIPIENT_NOT_ALLOWED
    assert DEFAULT_HTTP_STATUS[raised.value.code] == 422
    assert raised.value.details is not None
    assert raised.value.details["reason_code"] == "RECIPIENT_NOT_ALLOWLISTED"
    assert fx.sink.calls == ()


async def test_the_status_follows_the_same_element_the_ledger_records() -> None:
    """Several facts can move at once, and then the two records must agree.

    ``revalidate`` returns its reasons in a fixed order and the executor writes
    ``blocking[0]`` to ``action_executions.error_code``. The adapter derives
    the HTTP code from that same element, so the code a client reads and the
    code an operator later finds on the row are one fact read twice rather than
    two facts that can disagree in an incident review.
    """
    from services.control_plane.app.api.errors import ApiError

    fx = await _approved_executor_port()
    fx.store.advance_case_revision(fx.scope, CASE_ID, to=14)
    fx.store.supersede_belief_versions(fx.scope, CASE_ID, frozenset())

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 14))

    assert raised.value.details is not None
    reasons = raised.value.details["blocking_reasons"]
    assert "CASE_REVISION_MOVED" in reasons
    assert "SUPPORT_BELIEF_SUPERSEDED" in reasons
    assert raised.value.details["reason_code"] == reasons[0]
    assert fx.store.executions[0].error_code == reasons[0]


async def test_an_intent_outside_the_capabilitys_scope_is_a_typed_404() -> None:
    """``InternalPort.execute_action`` is typed ``-> Row``, not ``Row | None``.

    The internal route has no ``if row is None`` branch, so returning ``None``
    would serialise a ``200 null`` body for an intent that does not exist for
    this capability -- a success envelope around nothing. Section 9.11 lists
    ``404 ACTION_INTENT_NOT_FOUND``, and section 1.7 keeps it indistinguishable
    from absence: an action-intent id is guessable enough that a 404/403 split
    would be an enumeration oracle over another user's disputes.
    """
    from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS, ApiError, ErrorCode

    fx = await _approved_executor_port()
    foreign = _Binding(fx.intent_id)
    foreign.tenant_id = uuid.UUID("11111111-2222-4333-8444-555555555555")

    with pytest.raises(ApiError) as raised:
        await fx.port.execute_action(foreign, _Execute(fx.digest.hex(), 13))

    assert raised.value.code is ErrorCode.ACTION_INTENT_NOT_FOUND
    assert DEFAULT_HTTP_STATUS[raised.value.code] == 404
    assert fx.sink.calls == ()


# -- 9.11: the kill switch, which is a 200 body rather than an error ------


async def test_the_kill_switch_reports_no_revalidation_rather_than_three_failures() -> None:
    """The kill switch never runs revalidation, so it must not report one.

    ``G9.6``'s disabled path returns ``NOT_EXECUTED`` with
    ``blocking_reasons=(ACTION_EXECUTION_DISABLED,)`` *before* any gate is
    evaluated. Deriving the revalidation block from "were there blocking
    reasons" would render ``draft_hash_match: false`` beside two more falses --
    three failed checks that never ran, on an intent with nothing wrong with
    it. ``None`` is the only honest answer.
    """
    fx = await _approved_executor_port(execution_mode="DISABLED")

    row = await fx.port.execute_action(_Binding(fx.intent_id), _Execute(fx.digest.hex(), 13))

    assert row["status"] == "NOT_EXECUTED"
    assert row["error_code"] == "ACTION_EXECUTION_DISABLED"
    assert row["executed_at"] is None
    assert row["action_execution_id"] is None
    # Every flag is `None`, not `false`. `false` would assert the result of a
    # comparison nothing performed; `true` would assert the other one just as
    # falsely.
    revalidation = row["revalidation"]
    assert revalidation["draft_hash_match"] is None
    assert revalidation["support_still_current"] is None
    assert revalidation["recipient_allowlisted"] is None
    # And the rollback position is genuinely a rollback: no ledger residue to
    # explain later, and the intent stays APPROVED so flipping the switch back
    # needs no second human consent.
    assert fx.sink.calls == ()
    assert fx.store.executions == ()
    assert fx.store.intents[0].status == "APPROVED"


# ==========================================================================
# 6. The blocking-reason table, kept honest the way the refusal table is
# ==========================================================================
#
# `REFUSAL_STATUS` translates something `app/actions` *raised*.
# `BLOCKING_STATUS` translates something `revalidate` *returned* and the
# executor had already written to `action_executions.error_code`. They are
# different vocabularies over the same subsystem, and only one of them had a
# drift guard until now -- which is how `NO_COMMITTED_BASIS` and
# `RECIPIENT_NOT_ALLOWLISTED` were both being answered as `ACTION_STALE`.


def _executor_blocking_codes() -> frozenset[str]:
    """Every blocking reason the executor can return, read from its module.

    Enumerated rather than transcribed, for the same reason
    :func:`_action_reason_codes` is: a transcribed list goes stale in exactly
    the situation it exists to cover, which is the action plane adding a code.
    """
    from services.control_plane.app.actions import executor

    return frozenset(
        value
        for name in dir(executor)
        if name.isupper() and not name.startswith("_")
        for value in [getattr(executor, name)]
        if isinstance(value, str) and value == name
    )


def test_the_blocking_code_scan_finds_codes_at_all() -> None:
    """Vacuity guard. An empty scan makes every assertion below pass forever,
    which is the failure a scan exists to remove rather than to introduce."""
    codes = _executor_blocking_codes()
    assert len(codes) >= 6, f"the blocking-code scan found only {sorted(codes)}"
    assert "CASE_REVISION_MOVED" in codes
    assert "DRAFT_HASH_CHANGED" in codes


def test_every_blocking_reason_has_a_status() -> None:
    """An unmapped blocking reason silently takes the 409 fallback.

    Survivable, and wrong in the direction that costs something:
    ``RECIPIENT_NOT_ALLOWLISTED`` answered as ``ACTION_STALE`` tells a client
    to reload a case that has not moved.
    """
    from services.control_plane.app.api.adapters import action_errors

    unmapped = sorted(_executor_blocking_codes() - set(action_errors.BLOCKING_STATUS))
    assert unmapped == [], (
        f"actions/executor.py can block on {unmapped}, and adapters/action_errors.py "
        "has no entry for them."
    )


def test_the_blocking_table_names_no_code_the_executor_cannot_produce() -> None:
    """The other direction, and the one a completeness test usually forgets.

    An entry for a string nothing returns is a rule about a situation that
    cannot arise. It costs nothing at run time and it reads in review as
    coverage, which is worse than absent.
    """
    from services.control_plane.app.api.adapters import action_errors

    invented = sorted(set(action_errors.BLOCKING_STATUS) - _executor_blocking_codes())
    assert invented == [], f"BLOCKING_STATUS maps reasons the executor never returns: {invented}"


def test_blocking_error_refuses_to_invent_a_reason() -> None:
    """There is no such thing as a refusal with no reason, and a 409 whose body
    says nothing is worse than a crash -- it looks like an answer."""
    from services.control_plane.app.api.adapters import action_errors

    with pytest.raises(ValueError):
        action_errors.blocking_error(())


def test_no_committed_basis_no_longer_needs_an_approximation() -> None:
    """``errors.py`` gained ``ErrorCode.NO_COMMITTED_BASIS`` (409) for ``G9.6``,
    so the interim mapping onto a neighbouring code is now the bug.

    :data:`APPROXIMATED` is asserted by equality rather than by membership: the
    set may shrink as ``ErrorCode`` grows and must never grow, and only an
    equality assertion says the second half.
    """
    from services.control_plane.app.api.adapters import action_errors
    from services.control_plane.app.api.errors import ErrorCode

    assert action_errors.REFUSAL_STATUS["NO_COMMITTED_BASIS"] is ErrorCode.NO_COMMITTED_BASIS
    assert action_errors.BLOCKING_STATUS["NO_COMMITTED_BASIS"] is ErrorCode.NO_COMMITTED_BASIS
    assert (
        frozenset({"RISK_TIER_NOT_PERMITTED"}) == action_errors.APPROXIMATED
    ), "the approximated set moved; it may shrink as ErrorCode grows, never grow"


# ==========================================================================
# 6. The ingestion path: six methods that still refuse, and the mechanism
#    behind each refusal -- executed rather than transcribed
# ==========================================================================
#
# ``write.create_correction``, ``write.upload_intent``,
# ``write.complete_artifact``, ``internal.ingest_artifact``,
# ``internal.artifact_content`` and ``internal.register_evidence`` are the
# whole artifact-and-evidence ingestion path, and all six are still in
# ``UNBOUND``.
#
# These tests exist because of what a register entry *is*: prose. Prose
# describing a mechanism drifts from the mechanism silently, and the entry that
# drifts is read by somebody deciding whether the method can be bound yet --
# the worst possible moment to be reading a stale sentence. So each probe below
# **runs** the blocker it names, each carries a vacuity guard so that a probe
# which stopped measuring anything reads as a failure rather than as good news,
# and each asserts that the register entry names what the probe found. The
# shape is ``test_create_action_intent_is_still_unbound_and_says_why`` above,
# applied to six more methods.
#
# Every one of them flips direction when its blocker is lifted: it stops
# demanding that the method be unbound and starts demanding that it be bound.
# That is deliberate. ``STATUS.md`` section 7: a test pinned to a *state* fails
# when the state legitimately changes, and the pressure then is to delete the
# guard exactly when there is finally something to guard. These assert the
# *property* -- the register agrees with the tree -- so both directions stay
# checked.

#: ``services/control_plane/tests/api`` -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations" / "versions"
CONTROL_PLANE_APP = Path(__file__).resolve().parents[2] / "app"

#: Sections 8.14, 8.18, 8.19, 9.1, 9.3 and 9.4.
INGESTION_METHODS: frozenset[str] = frozenset(
    {
        "write.create_correction",
        "write.upload_intent",
        "write.complete_artifact",
        "internal.ingest_artifact",
        "internal.artifact_content",
        "internal.register_evidence",
    }
)

#: The three whose register entry must name the object-store client.
OBJECT_STORE_DEPENDENTS: frozenset[str] = frozenset(
    {"write.upload_intent", "write.complete_artifact", "internal.ingest_artifact"}
)


def _control_plane_modules() -> Iterator[Path]:
    for path in sorted(CONTROL_PLANE_APP.rglob("*.py")):
        if "__pycache__" not in path.parts and "tests" not in path.parts:
            yield path


def _object_store_call_sites() -> list[str]:
    """Every place the control plane reaches an object store, found by AST.

    By AST and not by substring, and the distinction is load-bearing in the
    direction that costs something. ``adapters/unbound.py`` and
    ``adapters/render.py`` both write "pre-signed" in prose, and
    ``app/retrieval/embeddings.py`` holds a real ``boto3.client`` -- for
    ``bedrock-runtime``, which is a *model* client and not a store. A substring
    scan for ``boto3`` or ``presigned`` would report a store that does not
    exist, and the test below would then stop demanding a refusal and start
    demanding a binding, on the strength of a docstring.
    """
    found: list[str] = []
    for path in _control_plane_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken module is its own failure
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                "generate_presigned_url",
                "generate_presigned_post",
            ):
                found.append(f"{path.name}:{node.lineno}: .{node.attr}")
            elif isinstance(node, ast.ImportFrom) and node.module == "google.cloud":
                found.extend(
                    f"{path.name}:{node.lineno}: from google.cloud import storage"
                    for alias in node.names
                    if alias.name == "storage"
                )
            elif isinstance(node, ast.Import):
                found.extend(
                    f"{path.name}:{node.lineno}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("google.cloud.storage")
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "client"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "boto3"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "s3"
            ):
                found.append(f"{path.name}:{node.lineno}: boto3.client(s3)")
    return found


def test_the_control_plane_reaches_no_object_store_and_the_register_says_so() -> None:
    """Sections 8.18, 8.19 and 9.1 all end at bytes nobody here can address.

    Section 8.18 returns a pre-signed ``PUT``; 8.19 runs ``HeadObject`` and a
    checksum comparison against the stored object; 9.1 has to move the Lambda's
    bytes into the ``raw/`` prefix ``source_artifacts`` requires. All three need
    one client and there is none -- nothing under
    ``services/control_plane/app`` mints a pre-signed URL or constructs a
    storage client.

    Asserted as an agreement rather than as a state. The day a client lands this
    stops demanding the three refusals and starts demanding the three bindings,
    which is the only version of this guard that survives the change it is
    waiting for.
    """
    adapters = _adapters()
    sites = _object_store_call_sites()
    if sites:
        still_refused = sorted(OBJECT_STORE_DEPENDENTS & set(adapters.UNBOUND))
        assert still_refused == [], (
            f"an object-store client now exists ({sites}); {still_refused} are "
            "refused for a reason that no longer holds. Bind them and delete "
            "their UNBOUND entries."
        )
        return
    bound = sorted(OBJECT_STORE_DEPENDENTS - set(adapters.UNBOUND))
    assert bound == [], (
        f"{bound} are bound and the control plane can address no object at all. "
        "A pre-signed URL nobody can PUT to is worse than a refusal: the client "
        "believes the upload succeeded."
    )
    unnamed = sorted(
        name for name in OBJECT_STORE_DEPENDENTS if "object-store" not in adapters.UNBOUND[name]
    )
    assert unnamed == [], (
        f"{unnamed} are blocked on the object-store client and their UNBOUND "
        "entries do not name it, so the reader has to grep for the blocker."
    )


def _migration_source() -> str:
    """Every migration, concatenated. One string, so one scan sees all of them."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS_DIR.glob("[0-9]*.py"))
    )


def _created_tables() -> frozenset[str]:
    return frozenset(re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", _migration_source()))


def test_nothing_in_this_system_persists_a_content_block() -> None:
    """Section 9.3's whole payload has no store, and 9.4's span check no referent.

    ``ContentBlock`` is a real, typed contract -- block id, kind, text, sha256,
    source locator -- and **no migration creates anywhere to put one**. Not a
    table, not a column. The parser that would produce them does not exist
    either: ``app/ingestion`` is a one-line docstring,
    ``workers/textract_complete`` is an empty module, and
    ``agents/runtime/state.py``'s ``ArtifactReader`` is a Protocol whose only
    implementation is a test fake.

    That settles two methods rather than one. ``internal.artifact_content``
    *is* the blocks, so it has nothing to return. And section 9.4 steps 1 and 2
    -- every ``block_id`` must exist in the bound artifact, every
    ``exact_text`` must be a substring of the cited block -- are named in the
    spec as "the deterministic defence against a model inventing a quotation",
    and they would have nothing to check against. Admitting evidence with that
    defence absent is ``D-00-005`` inverted: performing the action while unable
    to perform its guard, into a table that is append-only and therefore cannot
    be corrected afterwards.

    The seed is what makes the gap easy to miss and worth naming: every
    ``source_artifacts`` row it writes carries ``parser_status = 'PARSED'`` and
    ``parser_version = 'seed-1.0.0'``, so the column asserts a parse whose
    output nobody can read back.
    """
    from provenance_contracts.ingestion import ContentBlock, NormalizedContent

    tables = _created_tables()
    # Vacuity guards, both directions. A scan that found no migrations, or a
    # contracts package that stopped declaring the shape, would make every
    # assertion below true about nothing.
    assert len(tables) >= 20, f"the migration scan saw only {sorted(tables)}"
    assert {"evidence_items", "source_artifacts"} <= tables, sorted(tables)
    assert "blocks" in NormalizedContent.model_fields
    assert {"block_id", "kind", "text", "source_locator"} <= set(ContentBlock.model_fields)

    block_tables = sorted(name for name in tables if "block" in name.lower())
    block_columns = sorted(
        set(re.findall(r"^\s+(\w*block\w*)\s+(?:JSONB|STRING|BYTES)", _migration_source(), re.M))
    )
    adapters = _adapters()
    if block_tables or block_columns:
        assert "internal.artifact_content" not in adapters.UNBOUND, (
            f"blocks are persisted now ({block_tables or block_columns}); section "
            "9.3 has a source and internal.artifact_content can bind."
        )
        return
    # ``span`` is required of section 9.4's entry and not of 9.3's, and the
    # asymmetry is the point rather than an oversight. 9.3 *is* the blocks, so
    # naming them is the whole reason. 9.4 is blocked by what it cannot *check*
    # -- step 2 is a span containment test -- and an entry that says only
    # "blocks" has described the missing input without describing the guard
    # that goes missing with it. A counterfactual that deleted the guard's name
    # and left the word "blocks" standing elsewhere in the sentence passed an
    # earlier, weaker version of this assertion.
    required_words = {
        "internal.artifact_content": ("block",),
        "internal.register_evidence": ("block", "span"),
    }
    for name, words in required_words.items():
        assert name in adapters.UNBOUND, f"{name} is bound and nothing persists a block"
        reason = adapters.UNBOUND[name].lower()
        missing = [word for word in words if word not in reason]
        assert missing == [], (
            f"{name} is blocked on content blocks that nothing persists, and its "
            f"UNBOUND entry never says {missing}: {adapters.UNBOUND[name]!r}"
        )


def _s3_key_prefix_required() -> str:
    """The prefix ``ck_source_artifacts_s3_key_shape`` requires, read from the DDL.

    Read rather than restated. A constant here would be a second copy of a
    database constraint, and the failure mode of a second copy is that it goes
    on agreeing with itself after the first one moves.
    """
    match = re.search(
        r"ck_source_artifacts_s3_key_shape\s+CHECK\s*\(\s*s3_key\s+LIKE\s+'([^']+)'\s*\)",
        _migration_source(),
    )
    assert match is not None, "no migration declares ck_source_artifacts_s3_key_shape"
    pattern = match.group(1)
    assert pattern.endswith("%") and len(pattern) > 1, f"unexpected LIKE pattern {pattern!r}"
    return pattern[:-1]


def test_the_ses_key_section_9_1_carries_is_refused_by_the_artifact_table() -> None:
    """``internal.ingest_artifact`` fails at the database, not at a missing helper.

    Section 9.1's request body carries the key the SES Lambda wrote --
    ``ses/2026/06/05/0100018f9e70abcd-3f8a1c9d`` in the spec's own example --
    and ``source_artifacts`` will not store it: the table constrains
    ``s3_key LIKE 'raw/%'``, which is section 8.18's fixed layout
    ``raw/{tenant_id}/{user_id}/{artifact_id}/original`` enforced at the
    boundary so that a bad pre-sign cannot write outside the prefix.

    The row can therefore only be written *after* the bytes have been copied
    into that prefix, and what copies them is the object-store client
    ``write.upload_intent`` is waiting on. Synthesising a ``raw/`` key without
    the copy is the option this test exists to close off: it satisfies the
    CHECK and stores a locator for bytes nobody wrote, and the first symptom is
    a download that 404s months later against a row that looks perfect.

    The constraint is read out of the migration rather than restated here, and
    it was separately confirmed present on the live cluster
    (``pg_get_constraintdef`` over ``source_artifacts``) rather than assumed
    from the migration alone.
    """
    from services.control_plane.app.api.schemas.internal import IngestArtifactRequest

    required = _s3_key_prefix_required()
    assert required, "the CHECK requires no prefix at all; section 9.1 is unblocked"

    ses_key = "ses/2026/06/05/0100018f9e70abcd-3f8a1c9d"
    # Vacuity guard: the body must actually accept the key the spec prints, or
    # this test measures a schema rejection instead of a database one.
    request = IngestArtifactRequest(
        alias_hash="b64:9tKp3f0Zx1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q=",
        s3_bucket="provenance-inbound-us-east-1",
        s3_key=ses_key,
        received_at=datetime(2026, 6, 5, 14, 19, tzinfo=UTC),
        size_bytes=214882,
        content_sha256="3f8a1c9d5e2b47a0c6d8f1e3b5a7c9d1e3f5a7b9c1d3e5f7a9b1c3d5e7f9a1b3",
        ses_verdicts={
            "spf": "PASS",
            "dkim": "PASS",
            "dmarc": "PASS",
            "spam": "PASS",
            "virus": "PASS",
        },
    )
    assert request.s3_key == ses_key

    adapters = _adapters()
    if request.s3_key.startswith(required):
        assert "internal.ingest_artifact" not in adapters.UNBOUND, (
            "the inbound key now satisfies ck_source_artifacts_s3_key_shape, so "
            "section 9.1 can insert its row."
        )
        return
    assert "internal.ingest_artifact" in adapters.UNBOUND
    reason = adapters.UNBOUND["internal.ingest_artifact"]
    assert required in reason, (
        "internal.ingest_artifact is blocked by a key-prefix CHECK its register "
        f"entry never mentions ({required!r}): {reason!r}"
    )


def test_the_kernel_holds_no_evidence_items_update_so_a_retraction_has_no_writer() -> None:
    """Section 8.14 step 5 asks for a statement that exists nowhere in the tree.

    A ``RETRACT_EVIDENCE`` correction is defined as the Kernel setting the
    target's ``retraction_status``, ``retracted_by_evidence_id`` and
    ``retraction_reason_code``, then re-evaluating every belief version the
    retracted evidence grounded. That is an ``UPDATE`` on ``evidence_items``.

    ``memory_kernel.transaction.CANONICAL_WRITE_STATEMENTS`` is the Kernel's own
    enumeration of every canonical write it holds, and none of them touches
    ``evidence_items``. Write rule ``W2`` forbids every other module from
    holding one, and ``W4`` grants the app an ``INSERT`` and deliberately not an
    ``UPDATE``, because only the Kernel may retract evidence. So one of section
    8.14's six correction types has no writer anywhere in the system, and
    binding ``write.create_correction`` would mean refusing ``RETRACT_EVIDENCE``
    from inside a method that advertises it.

    Asserted against the Kernel's own tuple rather than by grepping for SQL:
    ``tests/kernel/test_obligations.py`` compares the linter's count against
    that tuple, so the two cannot drift apart without something going red.
    """
    from services.control_plane.app.memory_kernel import transaction
    from tools import write_path_lint

    statements = transaction.CANONICAL_WRITE_STATEMENTS
    # Vacuity guard: a tuple that stopped listing statements would make the
    # absence below true for the wrong reason.
    assert len(statements) >= 17, statements
    assert any("claims INSERT" in entry for entry in statements), statements

    writers = [entry for entry in statements if "evidence_items" in entry]
    assert "evidence_items" in write_path_lint.APP_INSERT_PERMITTED
    assert "evidence_items" not in write_path_lint.DISPATCHER_UPDATE_PERMITTED

    adapters = _adapters()
    if writers:
        assert "write.create_correction" not in adapters.UNBOUND, (
            f"the Kernel now holds {writers}; section 8.14 step 5 has a writer and "
            "RETRACT_EVIDENCE can be performed."
        )
        return
    assert "write.create_correction" in adapters.UNBOUND
    reason = adapters.UNBOUND["write.create_correction"]
    assert "RETRACT_EVIDENCE" in reason, (
        "write.create_correction cannot perform one of its six correction types "
        f"and the register does not say which: {reason!r}"
    )


def _no_model_attributions() -> list[str]:
    """Every spelling of "no model produced this", tried against the contract.

    Returns the ones that construct. An empty list is the finding rather than a
    disappointment: the contract has no way to say it, which is half of what
    blocks ``write.create_correction``.
    """
    from pydantic import ValidationError

    from provenance_contracts.resolution import ModelAttribution
    from provenance_domain.enums import ModelTier

    built: list[str] = []
    for provider in ("bedrock", "gemini"):
        for model_id in ("deterministic.kernel", "none", "human"):
            for tier in ModelTier:
                try:
                    ModelAttribution(
                        provider=provider,  # type: ignore[arg-type]
                        model_id=model_id,
                        tier=tier,
                        prompt_version="pv-correction-1.0.0",
                        graph_name="correction",
                        graph_version="1.0.0",
                    )
                except (ValidationError, ValueError):
                    continue
                built.append(f"{provider}/{model_id}/{tier}")
    return built


def test_model_attribution_cannot_express_the_proposal_a_correction_makes() -> None:
    """A user's typed sentence had no model behind it, and the contract insists.

    Section 8.14 step 4 builds a ``MemoryProposal`` and submits it to the
    Kernel. ``MemoryProposal.model`` is a required ``ModelAttribution`` and
    ``ModelTier`` has exactly three members -- ``E``, ``R``, ``EMBEDDING``.
    None of them means "nothing inferred this", which is precisely what a
    correction is: the user typed it.

    The database already has the value. ``ck_memory_proposals_model`` admits
    ``deterministic.kernel`` beside the three chat ids for exactly this case,
    and every ``memory_proposals`` row on the cluster carries it. So the column
    can say it and the contract cannot, and filling the required field with a
    chat id would write a **false attribution** into ``memory_proposals`` --
    the row ``CANONICAL_DECISIONS.md`` -> *Disclosure* relies on to make the
    shipped model checkable against persisted state rather than against a
    README. A fabricated provenance record inside the provenance system is the
    worst place in the product to put one, and it is the same reason
    ``internal.submit_proposal`` is refused.

    ``scripts/seed/proposals.py`` records this gap and works around it, naming
    the tier-E id in the typed attribution while writing ``deterministic.kernel``
    into the column. That is defensible for a fixture that discloses itself as
    one. It is not defensible for a user's correction.
    """
    from provenance_contracts.resolution import ModelAttribution
    from provenance_domain.enums import ModelTier

    # Vacuity guard: a constructor that refused everything would make the
    # finding below meaningless.
    ModelAttribution(
        provider="gemini",
        model_id="gemini-3.5-flash-lite",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )
    admitted = re.search(
        r"ck_memory_proposals_model\s+CHECK\s*\(model_id IN \((.*?)\)\)",
        _migration_source(),
        re.S,
    )
    assert admitted is not None, "no migration declares ck_memory_proposals_model"
    assert "deterministic.kernel" in admitted.group(1), (
        "the column no longer has a value for a proposal no model produced; the "
        "mismatch this test is about has changed shape."
    )

    expressible = _no_model_attributions()
    adapters = _adapters()
    if expressible:
        assert "write.create_correction" not in adapters.UNBOUND, (
            f"ModelAttribution can now say {expressible}, so a correction can "
            "carry an honest attribution and section 8.14 step 4 is unblocked."
        )
        return
    assert "write.create_correction" in adapters.UNBOUND
    reason = adapters.UNBOUND["write.create_correction"]
    assert "ModelAttribution" in reason and "deterministic.kernel" in reason, (
        "write.create_correction is blocked because the contract cannot express "
        f"what the column already has, and the register does not say so: {reason!r}"
    )


def _profile_the_evidence_table_admits() -> tuple[int, frozenset[str]]:
    """``(VECTOR width, admitted embedding_model set)`` as ``0002`` declares them.

    Read out of the applied migration rather than out of ``0009``. ``0009``
    widens both and is deliberately unapplied -- it drops the embedding quartet
    and refuses without an exact-count acknowledgement -- so reading it would
    describe a schema no cluster has.
    """
    source = (MIGRATIONS_DIR / "0002_evidence_plane.py").read_text(encoding="utf-8")
    width = re.search(r"embedding\s+VECTOR\((\d+)\)", source)
    models = re.search(r"ck_evidence_embedding_model CHECK \((.*?)\n\s*\),", source, re.S)
    assert width is not None, "0002 no longer declares evidence_items.embedding"
    assert models is not None, "0002 no longer declares ck_evidence_embedding_model"
    return int(width.group(1)), frozenset(re.findall(r"'([^']+)'", models.group(1)))


def test_register_evidence_cannot_stamp_an_embedding_this_column_would_accept() -> None:
    """Section 9.4 step 4 has an embedder and nowhere to put what it returns.

    Step 4 is not optional decoration: it computes the vector server-side and
    stamps ``embedding_model`` and ``embedding_version`` so a future migration
    can build a parallel index rather than mixing vector spaces. The embedder
    this build ships is ``GeminiEmbedder`` over ``gemini-embedding-2`` at 1536
    dimensions -- probed, unit-normalised, and canon since the pivot. The
    applied schema is ``VECTOR(1024)`` with ``ck_evidence_embedding_model``
    admitting ``amazon.titan-embed-text-v2:0`` and nothing else, because the
    18,035 rows already in the ground are Titan's. Migration ``0009`` widens
    both and is deliberately unapplied.

    So the only legal write is ``embedding = NULL``, which
    ``ck_evidence_embedding_provenance`` permits and which silently excludes the
    row from every ANN query for good -- ``evidence_items`` is append-only, so
    it cannot be corrected in place. A row that exists and never retrieves is
    the "absence is not emptiness" failure with nothing erroring, which is
    exactly the shape ``ann_search()`` returning zero rows for every query
    already took once.
    """
    from provenance_contracts.embedding_profile import EMBEDDING_PROFILES

    width, admitted = _profile_the_evidence_table_admits()
    # Vacuity guards: a registry with one profile, or a CHECK read as empty,
    # would make the mismatch below unfalsifiable.
    assert len(EMBEDDING_PROFILES) >= 2, sorted(EMBEDDING_PROFILES)
    assert admitted, "ck_evidence_embedding_model was read as admitting nothing"

    writable = sorted(
        profile.name
        for profile in EMBEDDING_PROFILES.values()
        if profile.model_id in admitted and profile.column_width == width
    )
    gemini = EMBEDDING_PROFILES["gemini-v2"]
    assert gemini.model_id not in admitted or gemini.column_width != width, (
        "the applied schema now accepts the shipping embedding profile; section "
        "9.4 step 4 has somewhere to write and the blocker is gone."
    )
    assert writable == ["titan-v1"], (
        f"the set of profiles this schema can accept moved to {writable}; the "
        "register entry under internal.register_evidence describes a different "
        "world."
    )

    adapters = _adapters()
    assert "internal.register_evidence" in adapters.UNBOUND
    reason = adapters.UNBOUND["internal.register_evidence"]
    assert str(width) in reason and gemini.model_id in reason, (
        "internal.register_evidence cannot stamp an embedding and the register "
        f"names neither the column width nor the model that will not fit: {reason!r}"
    )


def test_the_six_ingestion_methods_agree_with_the_register() -> None:
    """The register's headline claim, checked against the tree rather than read.

    ``STATUS.md`` counts bound ports off this dict, so an entry left behind
    after a binding overstates what is missing and an entry deleted early
    understates it -- and both are silent, because nothing calls these methods
    in the hermetic suites. This asserts the property that makes the count worth
    quoting: for each of the six, the register and the adapter body say the same
    thing, and never one without the other.
    """
    adapters = _adapters()
    ports_by_name = {
        "write": adapters.KernelWritePort,
        "internal": adapters.KernelInternalPort,
    }
    for qualified in sorted(INGESTION_METHODS):
        port, _, method = qualified.partition(".")
        assert hasattr(ports_by_name[port], method), f"{qualified} is registered but not defined"
        declared = qualified in adapters.UNBOUND
        source = inspect.getsource(getattr(ports_by_name[port], method))
        refuses = f'unbound("{qualified}")' in source
        assert declared == refuses, (
            f"{qualified}: declared unbound={declared} but the body "
            f"{'refuses' if refuses else 'does not refuse'}. The register and the "
            "adapter disagree, and STATUS.md quotes the register."
        )
