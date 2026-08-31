"""The repository read layer, proven without a cluster — T3.3 / T5.x / T6.x.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts*:
  ``provenance_db.repositories.evidence.ann_search()`` is **the** ANN entry
  point. This file holds it to that.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 13.3 layer 3 — "exactly one
  function ... issues vector SQL", with the retraction predicate asserted as a
  "tripwire, not decoration".
- ``docs/specs/10_DATABASE_DDL.md`` section 5.5 — the one sanctioned ANN shape,
  and section 12 — write-path ownership.
- ``docs/specs/12_KERNEL_ALGORITHMS.md`` section 7.1 — SQLSTATE ``40001``.
- Defect ``D-06-001``.

Why these tests are hermetic
-----------------------------
Every claim below is a property of the *statement and its parameters*, not of
any row a cluster happens to hold. ``D-06-001`` in particular has **no
result-set symptom at any corpus size** — the defect returns correct rows and
only changes latency — so a test that asserts on returned rows cannot see it.
What can see it is the pair the driver receives: the vector belongs in the
parameter tuple and must not appear in the statement text. That is checkable
with a fake cursor, on a laptop, in milliseconds, and it is checked here.

``tests/db/test_repository_reads.py`` carries the other half — the same
statements against the seeded corpus, plus the ``EXPLAIN`` that proves the
vector index is actually chosen.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import uuid
from decimal import Decimal
from typing import Any, Self

import psycopg.errors as pgerr
import pytest

from provenance_contracts.identity import Principal
from provenance_db import retry
from provenance_db.repositories import (
    actions,
    agent_runs,
    beliefs,
    cases,
    commitments,
    events,
    evidence,
    triggers,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes. Deliberately minimal: a cursor that records what it was asked to do.
# ---------------------------------------------------------------------------


class Column:
    """``psycopg``'s ``cursor.description`` entry, reduced to the one attribute
    the repositories read."""

    def __init__(self, name: str) -> None:
        self.name = name


class RecordingCursor:
    """Records every ``(sql, params)`` pair and replays a scripted result.

    *failures* is a list of exceptions raised, one per ``execute``, before the
    rows are served. That is how the ``40001`` retry is exercised without a
    cluster and without contention: contention is only one way to reach the
    SQLSTATE, and the handler is what is under test.
    """

    def __init__(
        self,
        rows: list[tuple[Any, ...]] | None = None,
        columns: list[str] | None = None,
        failures: list[BaseException] | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.description = [Column(name) for name in (columns or [])]
        self.failures = list(failures or [])
        self.calls: list[tuple[str, Any]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> Self:
        self.calls.append((sql, params))
        if self.failures:
            raise self.failures.pop(0)
        return self

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class RecordingConnection:
    """One cursor, handed out as often as asked, so a retry is visible."""

    def __init__(self, cursor: RecordingCursor) -> None:
        self.cursor_obj = cursor
        self.rollbacks = 0

    def cursor(self) -> RecordingCursor:
        return self.cursor_obj

    async def rollback(self) -> None:
        self.rollbacks += 1


class _NoSleep:
    """Backoff, without the wait. The delay is retry.py's contract, not this
    layer's, and sleeping for it here would buy nothing but a slow suite."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class _FixedJitter:
    def uniform(self, a: float, b: float) -> float:
        return a


def hero(tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> Principal:
    """A valid :class:`Principal`. Nothing here reads a token; the object is a
    carrier for the ``(tenant_id, user_id)`` pair the SQL binds."""
    now = dt.datetime.now(tz=dt.UTC)
    return Principal(
        tenant_id=tenant_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        cognito_sub="sub-alex-rivera",
        token_issued_at=now - dt.timedelta(minutes=1),
        token_expires_at=now + dt.timedelta(minutes=59),
        request_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


#: A 1024-dimension vector, the width ``evidence_items.embedding`` declares.
QUERY_VECTOR: list[float] = [0.001 * (index % 7) for index in range(1024)]


# ---------------------------------------------------------------------------
# 1. Nothing in the read layer raises NotImplementedError any more.
# ---------------------------------------------------------------------------


READ_METHODS: list[tuple[str, Any]] = [
    ("cases.get_case_snapshot", cases.get_case_snapshot),
    ("cases.get_case_revision", cases.get_case_revision),
    ("cases.list_open_cases", cases.list_open_cases),
    ("cases.list_cases", cases.list_cases),
    ("evidence.get_evidence_item", evidence.get_evidence_item),
    ("evidence.list_active_evidence_for_case", evidence.list_active_evidence_for_case),
    ("evidence.ann_search", evidence.ann_search),
    ("beliefs.get_active_beliefs_for_case", beliefs.get_active_beliefs_for_case),
    ("beliefs.get_belief_lineage", beliefs.get_belief_lineage),
    ("beliefs.get_belief_support", beliefs.get_belief_support),
    ("commitments.get_open_commitments", commitments.get_open_commitments),
    ("events.get_undispatched_outbox_events", events.get_undispatched_outbox_events),
    ("agent_runs.get_agent_run", agent_runs.get_agent_run),
    ("actions.get_action_intent", actions.get_action_intent),
    ("triggers.get_armed_triggers_for_case", triggers.get_armed_triggers_for_case),
]


def _invoke(name: str, func: Any, cursor: RecordingCursor) -> Any:
    """Call *func* with a fake connection and whatever extra arguments it
    declares, so one table can drive fifteen different signatures."""
    conn = RecordingConnection(cursor)
    principal = hero()
    kwargs: dict[str, Any] = {}
    parameters = inspect.signature(func).parameters
    if "case_id" in parameters:
        kwargs["case_id"] = uuid.uuid4()
    if "evidence_id" in parameters:
        kwargs["evidence_id"] = uuid.uuid4()
    if "belief_id" in parameters:
        kwargs["belief_id"] = uuid.uuid4()
    if "belief_version_id" in parameters:
        kwargs["belief_version_id"] = uuid.uuid4()
    if "run_id" in parameters:
        kwargs["run_id"] = uuid.uuid4()
    if "intent_id" in parameters:
        kwargs["intent_id"] = uuid.uuid4()
    if "query_embedding" in parameters:
        kwargs["query_embedding"] = QUERY_VECTOR
    if "embedding_version" in parameters:
        kwargs["embedding_version"] = "v1"
    if "limit" in parameters and parameters["limit"].default is inspect.Parameter.empty:
        kwargs["limit"] = 10
    return asyncio.run(func(conn, principal, **kwargs))


@pytest.mark.parametrize(("name", "func"), READ_METHODS, ids=[n for n, _ in READ_METHODS])
def test_no_read_method_still_raises_not_implemented(name: str, func: Any) -> None:
    """The spine, or the absence of one.

    Every method in this package raised ``NotImplementedError`` from T3.3 until
    now, which means nothing could read canonical state and the API could not
    boot. One parametrised case per method, so the report names exactly which
    ones are still declarations.
    """
    cursor = RecordingCursor(rows=[], columns=["id"])
    try:
        _invoke(name, func, cursor)
    except NotImplementedError as error:  # pragma: no cover - the failure path
        pytest.fail(f"{name} is still a declaration: {error}")


@pytest.mark.parametrize(("name", "func"), READ_METHODS, ids=[n for n, _ in READ_METHODS])
def test_every_read_binds_the_principal_scope(name: str, func: Any) -> None:
    """Scoping lives in the SQL and the parameters, never in the caller.

    Asserted on what the driver received rather than on the source text: a
    constant can carry ``tenant_id = %(tenant_id)s`` and still be executed with
    a dict that does not contain the key, and the failure mode of that is a
    query against somebody else's rows.
    """
    cursor = RecordingCursor(rows=[], columns=["id"])
    _invoke(name, func, cursor)
    assert cursor.calls, f"{name} issued no statement at all"
    for sql, params in cursor.calls:
        rendered = repr(params)
        assert "tenant_id" in sql, f"{name}: statement carries no tenant predicate"
        assert "user_id" in sql, f"{name}: statement carries no user predicate"
        assert "tenant_id" in rendered or _looks_bound(
            params
        ), f"{name}: the tenant scope was not bound"


def _looks_bound(params: Any) -> bool:
    """Positional binding: the scope arrives as values, not as named keys."""
    return isinstance(params, tuple | list) and len(params) > 0


# ---------------------------------------------------------------------------
# 2. D-06-001 — the query vector is a BOUND PARAMETER.
# ---------------------------------------------------------------------------


def test_the_ann_query_vector_is_a_bound_parameter_and_not_in_the_statement() -> None:
    """``D-06-001``, as a property of the pair the driver receives.

    A query vector supplied as a correlated subquery silently defeats vector
    index selection: correct results, no error, no warning, a FULL SCAN that
    survives ``ANALYZE`` and reproduces at 3 dimensions and at 1024. No
    assertion on returned rows can see it. This one can: the vector is in the
    parameters and the statement contains neither the vector nor a nested
    ``SELECT`` where the vector belongs.
    """
    cursor = RecordingCursor(rows=[], columns=["id"])
    conn = RecordingConnection(cursor)
    principal = hero()
    asyncio.run(
        evidence.ann_search(
            conn,
            principal,
            QUERY_VECTOR,
            limit=20,
            embedding_version="v1",
        )
    )
    assert len(cursor.calls) == 1, "ann_search issued more than one statement"
    sql, params = cursor.calls[0]

    assert isinstance(params, tuple | list), "the ANN parameters are not a bound sequence"
    vectors = [value for value in params if isinstance(value, str) and value.startswith("[")]
    assert vectors, "no vector literal was bound; the query vector did not reach the parameters"

    # The statement must carry placeholders where the vector goes, and must not
    # carry the vector itself in any form.
    assert "0.001" not in sql and "[0.0" not in sql, "the query vector was inlined into the SQL"
    for component in vectors:
        assert component not in sql, "the bound vector also appears in the statement text"

    # And it must not be computed there. A SELECT inside the ORDER BY block is
    # the exact shape D-06-001 names.
    ordering = sql.split("ORDER BY", 1)[1]
    assert (
        "SELECT" not in ordering.upper().split("LIMIT", 1)[0]
    ), "the ANN ordering contains a subquery: D-06-001 is back"


def test_ann_search_refuses_a_sql_shaped_query_vector() -> None:
    """The refusal is the boundary. A comment saying "never compute the vector
    inside the statement" is advice; this is what survives the next engineer in
    a hurry."""
    from services.control_plane.app.retrieval.ann import QueryVectorNotBoundError

    cursor = RecordingCursor(rows=[], columns=["id"])
    conn = RecordingConnection(cursor)
    with pytest.raises(QueryVectorNotBoundError):
        asyncio.run(
            evidence.ann_search(
                conn,
                hero(),
                "(SELECT embedding FROM evidence_items WHERE id = e.id)",  # type: ignore[arg-type]
                limit=20,
                embedding_version="v1",
            )
        )
    assert cursor.calls == [], "the offending statement reached the driver anyway"


def test_ann_search_executes_the_canonical_section_5_5_statement() -> None:
    """One door. The repository is the entry point ``CANONICAL_DECISIONS.md``
    names; the statement is the one ``10_DATABASE_DDL.md`` section 5.5
    sanctions. Delegation, not a second copy — a second copy is how the
    ``user_id`` prefix gets dropped from one of them."""
    from services.control_plane.app.retrieval import ann

    cursor = RecordingCursor(rows=[], columns=["id"])
    conn = RecordingConnection(cursor)
    asyncio.run(evidence.ann_search(conn, hero(), QUERY_VECTOR, limit=20, embedding_version="v1"))
    sql, _ = cursor.calls[0]
    assert ann.normalise_sql(sql) == ann.normalise_sql(ann.render_ann_sql())


def test_ann_search_asserts_the_retraction_predicate_before_executing() -> None:
    """``13_RETRIEVAL_SPEC.md`` section 13.3 layer 3, verbatim: "tripwire, not
    decoration".

    The spec puts the assertion in this function by name. Layer 1 is the
    predicate the database applies; this is the independent check that the
    predicate is still in the statement being sent. A single missed predicate
    is a *silent* correctness failure — a correction the user already made
    resurfaces and grounds a new belief — so the only affordable response is to
    make it take two independent mistakes rather than one.
    """
    from services.control_plane.app.retrieval import ann

    cursor = RecordingCursor(rows=[], columns=["id"])
    conn = RecordingConnection(cursor)

    unfiltered = ann.render_ann_sql(retraction_filter=False)
    assert "retraction_status = 'ACTIVE'" not in unfiltered, "the fixture is not a fixture"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ann, "render_ann_sql", lambda **_: unfiltered)
        with pytest.raises(evidence.RetractionFilterMissingError):
            asyncio.run(
                evidence.ann_search(conn, hero(), QUERY_VECTOR, limit=20, embedding_version="v1")
            )
    assert cursor.calls == [], "an unfiltered vector statement reached the driver"


def test_ann_search_binds_the_principal_and_not_a_caller_supplied_id() -> None:
    """Contract law L10 at the storage boundary: ownership comes from the
    verified principal, and there is no parameter through which a caller could
    name a different user."""
    principal = hero()
    cursor = RecordingCursor(rows=[], columns=["id"])
    conn = RecordingConnection(cursor)
    asyncio.run(
        evidence.ann_search(conn, principal, QUERY_VECTOR, limit=20, embedding_version="v1")
    )
    _, params = cursor.calls[0]
    assert principal.user_id in params
    assert principal.tenant_id in params

    signature = inspect.signature(evidence.ann_search)
    assert "user_id" not in signature.parameters
    assert "tenant_id" not in signature.parameters


def test_ann_search_maps_rows_by_column_name() -> None:
    """A positional tuple silently reorders when the projection changes. The
    repository returns mappings keyed by ``cursor.description``."""
    cursor = RecordingCursor(
        rows=[(uuid.UUID(int=1), "AMOUNT_ASSERTION", 0.0125)],
        columns=["id", "evidence_type", "distance"],
    )
    conn = RecordingConnection(cursor)
    rows = asyncio.run(
        evidence.ann_search(conn, hero(), QUERY_VECTOR, limit=20, embedding_version="v1")
    )
    assert rows == [
        {"id": uuid.UUID(int=1), "evidence_type": "AMOUNT_ASSERTION", "distance": 0.0125}
    ]


# ---------------------------------------------------------------------------
# 3. Layer 3's in-process half, on statements that project the column.
# ---------------------------------------------------------------------------


def test_lifecycle_filtered_reads_drop_non_active_rows_in_process() -> None:
    """``13_RETRIEVAL_SPEC.md`` section 13.3 layer 3, in-process half.

    ``predicates.active_rows`` is a no-op whenever the SQL predicate is intact,
    which is exactly why it is worth having. It is applied only to statements
    that *project* ``retraction_status``: fed rows that cannot carry the
    column it would drop all of them, which is a different bug wearing the same
    clothes.
    """
    cursor = RecordingCursor(
        rows=[
            (uuid.UUID(int=1), "ACTIVE"),
            (uuid.UUID(int=2), "SUPERSEDED"),
            (uuid.UUID(int=3), "RETRACTED"),
            (uuid.UUID(int=4), "QUARANTINED"),
        ],
        columns=["id", "retraction_status"],
    )
    conn = RecordingConnection(cursor)
    rows = asyncio.run(evidence.list_active_evidence_for_case(conn, hero(), uuid.uuid4(), limit=10))
    assert [row["id"] for row in rows] == [uuid.UUID(int=1)]


# ---------------------------------------------------------------------------
# 4. SQLSTATE 40001 — retried, bounded, and only for the retryable states.
# ---------------------------------------------------------------------------


def test_a_read_retries_a_serialization_failure() -> None:
    """``12_KERNEL_ALGORITHMS.md`` section 7.1. Three separate places in this
    codebase have been bitten by forgetting it, so the read path does not get
    to be the fourth."""
    cursor = RecordingCursor(
        rows=[(uuid.UUID(int=7), 9)],
        columns=["id", "revision"],
        failures=[pgerr.SerializationFailure("restart transaction")],
    )
    conn = RecordingConnection(cursor)
    sleep = _NoSleep()
    row = asyncio.run(
        cases.get_case_snapshot(conn, hero(), uuid.uuid4(), sleep=sleep, rng=_FixedJitter())
    )
    assert row == {"id": uuid.UUID(int=7), "revision": 9}
    assert len(cursor.calls) == 2, "the statement was not re-issued"
    assert conn.rollbacks == 1, "the aborted transaction was not rolled back before the retry"
    assert sleep.delays, "the retry did not back off"


def test_a_read_does_not_retry_a_non_retryable_sqlstate() -> None:
    """A check violation is not contention. Waiting will not help, and retrying
    turns one clear error into four slow ones."""
    cursor = RecordingCursor(
        rows=[], columns=["id"], failures=[pgerr.CheckViolation("ck_cases_status")]
    )
    conn = RecordingConnection(cursor)
    with pytest.raises(pgerr.CheckViolation):
        asyncio.run(
            cases.get_case_snapshot(
                conn, hero(), uuid.uuid4(), sleep=_NoSleep(), rng=_FixedJitter()
            )
        )
    assert len(cursor.calls) == 1


def test_a_read_gives_up_at_the_attempt_cap() -> None:
    """Bounded. An unbounded retry against a genuinely contended row is an
    outage that looks like a slow request."""
    policy = retry.RetryPolicy(max_tx_attempts=3)
    cursor = RecordingCursor(
        rows=[],
        columns=["id"],
        failures=[pgerr.SerializationFailure("restart") for _ in range(3)],
    )
    conn = RecordingConnection(cursor)
    with pytest.raises(retry.RetryExhausted) as caught:
        asyncio.run(
            cases.list_open_cases(conn, hero(), policy=policy, sleep=_NoSleep(), rng=_FixedJitter())
        )
    assert caught.value.attempts == 3
    assert len(cursor.calls) == 3


def test_the_read_retry_uses_the_shared_sqlstate_set() -> None:
    """One list of retryable states, in ``retry.py``, reached by attribute.

    A second hard-coded ``"40001"`` in this package is how the two drift, and
    it is also how ``PV_SABOTAGE`` — which rebinds ``retry.is_retryable`` on
    the module object — silently stops reaching the read path.
    """
    from provenance_db.repositories import _execute

    calls: list[str | None] = []
    original = retry.is_retryable

    def spy(sqlstate: str | None) -> bool:
        calls.append(sqlstate)
        return original(sqlstate)

    cursor = RecordingCursor(
        rows=[], columns=["id"], failures=[pgerr.SerializationFailure("restart")]
    )
    conn = RecordingConnection(cursor)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(retry, "is_retryable", spy)
        asyncio.run(
            _execute._fetch_all(
                conn,
                "SELECT id FROM cases WHERE tenant_id = %(tenant_id)s " "AND user_id = %(user_id)s",
                {"tenant_id": uuid.uuid4(), "user_id": uuid.uuid4()},
                sleep=_NoSleep(),
                rng=_FixedJitter(),
            )
        )
    assert calls == ["40001"], "the read path did not consult retry.is_retryable"


# ---------------------------------------------------------------------------
# 5. Money is Decimal, and stays Decimal.
# ---------------------------------------------------------------------------


def test_commitment_amounts_are_returned_as_decimal() -> None:
    """``DECIMAL(20,4)``, never float. A repository that coerces on the way out
    reintroduces the representation error the column type exists to prevent —
    and does it below every test that checks the arithmetic."""
    cursor = RecordingCursor(
        rows=[
            (uuid.UUID(int=1), "USD", Decimal("186.0000"), Decimal("0.0000"), Decimal("186.0000"))
        ],
        columns=[
            "id",
            "currency",
            "committed_amount",
            "fulfilled_amount",
            "outstanding_amount",
        ],
    )
    conn = RecordingConnection(cursor)
    rows = asyncio.run(commitments.get_open_commitments(conn, hero(), uuid.uuid4()))
    assert rows[0]["outstanding_amount"] == Decimal("186.0000")
    for column in ("committed_amount", "fulfilled_amount", "outstanding_amount"):
        assert isinstance(rows[0][column], Decimal), f"{column} was coerced away from Decimal"
        assert not isinstance(rows[0][column], float)


# ---------------------------------------------------------------------------
# 6. The write boundary, restated where a new statement would be added.
# ---------------------------------------------------------------------------


def test_no_repository_statement_is_a_write() -> None:
    """``10_DATABASE_DDL.md`` section 12. ``test_repository_read_only.py``
    checks the source; this checks the statements actually issued, which is the
    half a source scan cannot reach when a statement is assembled."""
    forbidden = ("insert ", "update ", "delete ", "upsert ", "truncate ")
    for name, func in READ_METHODS:
        cursor = RecordingCursor(rows=[], columns=["id"])
        _invoke(name, func, cursor)
        for sql, _ in cursor.calls:
            lowered = " ".join(sql.lower().split())
            for verb in forbidden:
                assert not lowered.startswith(verb), f"{name} issued a write: {verb.strip()}"
                assert f"; {verb}" not in lowered, f"{name} chained a write: {verb.strip()}"


def test_the_ann_entry_point_is_reachable_under_its_canonical_name() -> None:
    """``CANONICAL_DECISIONS.md`` prints the dotted path. Import it exactly as
    printed, so a rename that leaves the old docs standing fails here."""
    module = __import__("provenance_db.repositories.evidence", fromlist=["ann_search"])
    assert callable(module.ann_search)
    assert inspect.iscoroutinefunction(module.ann_search)


# ---------------------------------------------------------------------------
# The ANN cast width follows the active embedding profile
# ---------------------------------------------------------------------------
#
# render_ann_sql used to write "%s::VECTOR(1024)" as a literal. That is the
# correct width today -- the corpus is 18,035 titan-v1 vectors -- and silently
# wrong the moment the profile moves. Migration 0009 widens the column to 1536
# for the Gemini space, and config.py describes that flip as gated on
# prerequisites it does not own; with the width frozen here, the flip would
# leave the only ANN renderer in the system casting every query to the old
# width, and the first sign would be the database refusing a live query.


def test_the_ann_cast_width_is_the_active_profile_width() -> None:
    """The rendered cast agrees with the profile the system says is active."""
    import re

    from services.control_plane.app.retrieval.ann import render_ann_sql
    from services.control_plane.app.retrieval.config import ACTIVE_EMBEDDING_PROFILE

    widths = {int(w) for w in re.findall(r"::VECTOR\((\d+)\)", render_ann_sql())}
    assert widths, "the query no longer casts the bound vector at all"
    assert widths == {ACTIVE_EMBEDDING_PROFILE.column_width}, (
        f"render_ann_sql casts to {sorted(widths)} but the active profile "
        f"{ACTIVE_EMBEDDING_PROFILE.name!r} declares "
        f"VECTOR({ACTIVE_EMBEDDING_PROFILE.column_width})"
    )


def test_the_ann_cast_width_is_not_a_literal_in_the_source() -> None:
    """A regression guard: the failure mode is a hardcode, so look for one."""
    import inspect
    import re

    from services.control_plane.app.retrieval import ann

    source = inspect.getsource(ann.render_ann_sql)
    # Comments are stripped first: the fix's own explanation names the old
    # literal, and a guard that cannot tell code from prose about code would
    # fire on the commit that fixed the bug.
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"VECTOR\(\d+\)", code), (
        "the ANN cast width is hardcoded again; it must follow "
        "ACTIVE_EMBEDDING_PROFILE.column_width"
    )
    assert "column_width" in code


# ---------------------------------------------------------------------------
# Keyset pagination over an ASC NULLS LAST ordering
# ---------------------------------------------------------------------------
#
# `commitments.list_commitments_for_user` and `triggers.list_triggers_for_user`
# both order `<col> ASC NULLS LAST, id ASC` and both carried the naive keyset:
#
#     AND (%(after_col)s IS NULL
#          OR (col, id) > (%(after_col)s, %(after_id)s))
#
# which is wrong in two directions. With a cursor in the non-null section, a row
# whose col IS NULL makes the row comparison evaluate to NULL rather than true,
# so the whole NULLS-LAST tail is unreachable -- silently, because has_more has
# already gone false. And a cursor minted from inside that tail carries a null
# sort value, which satisfies the first branch and returns the list from the top
# again.
#
# On the live cluster this hid three of four commitments: the only row with a
# non-null due_at was page one, and the three without one could not be reached.
#
# These are text assertions because the behaviour needs a database. The live
# check that settles it is in the commit message: paging with limit=1 now
# reaches 4 of 4 commitments and 2 of 2 triggers with no duplicates.


NULLS_LAST_KEYSETS = [
    ("commitments", "list_commitments_for_user", "due_at", "cm"),
    ("triggers", "list_triggers_for_user", "not_before", "t"),
]


@pytest.mark.parametrize(("module", "func", "col", "alias"), NULLS_LAST_KEYSETS)
def test_the_nulls_last_keyset_is_not_the_naive_form(
    module: str, func: str, col: str, alias: str
) -> None:
    """The naive predicate must not be what ships."""
    import importlib
    import inspect

    mod = importlib.import_module(f"provenance_db.repositories.{module}")
    source = inspect.getsource(mod)

    naive = f"AND (%({'after_' + col})s::TIMESTAMPTZ IS NULL\n           OR ({alias}.{col}, {alias}.id) > ("
    assert naive not in source, (
        f"{module} is back on the naive keyset: rows with a NULL {col} are "
        "unreachable once a cursor is issued"
    )


@pytest.mark.parametrize(("module", "func", "col", "alias"), NULLS_LAST_KEYSETS)
def test_the_nulls_last_keyset_discriminates_on_after_id(
    module: str, func: str, col: str, alias: str
) -> None:
    """`after_id` is what distinguishes "no cursor" from "a cursor in the tail".

    The sort value is legitimately NULL inside the NULLS-LAST tail, so it cannot
    be the thing that means "no cursor was supplied". Only `after_id` can.
    """
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(f"provenance_db.repositories.{module}"))

    assert "%(after_id)s::UUID IS NULL" in source, (
        f"{module} no longer gates its keyset on after_id, so a cursor from "
        "inside the null tail cannot be told from no cursor at all"
    )
    assert (
        f"{alias}.{col} IS NULL AND {alias}.id > %(after_id)s::UUID" in source
    ), f"{module} has no branch that walks the NULL {col} tail by id"
    assert (
        f"THEN {alias}.{col} IS NULL" in source
    ), f"{module} does not admit the NULL {col} tail once past the non-null section"
