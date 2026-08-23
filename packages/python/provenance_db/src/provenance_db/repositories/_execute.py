"""One statement executor for the whole read layer — retry, and row mapping.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 7.1 — the retryable SQLSTATEs, and
  ``provenance_db.retry`` which owns them.
- ``CANONICAL_DECISIONS.md`` -> *Transaction isolation*: ``SERIALIZABLE`` with
  bounded retry for ``40001``.
- ``specs/10_DATABASE_DDL.md`` section 12 — nothing here writes.

Why the read path needs its own loop
-------------------------------------
:func:`provenance_db.retry.run_in_serializable_tx` takes a **pool** and owns the
transaction: it opens a connection per attempt, sets the isolation level, and
re-runs a callback that must re-read everything it depends on. That is the
right shape for the Kernel's write transaction and the wrong shape for a read,
which is handed a connection by its caller and has nothing to re-plan.

So this module reuses the parts of ``retry.py`` that carry the contract — the
SQLSTATE set through :func:`~provenance_db.retry.is_retryable`, the backoff
through :func:`~provenance_db.retry.backoff_delay_seconds`, the cap through
:class:`~provenance_db.retry.RetryPolicy`, and the exhaustion signal through
:class:`~provenance_db.retry.RetryExhausted` — and adds only the loop. A second
hard-coded ``"40001"`` in this package is how the two drift; there is not one.

``retry.is_retryable`` is reached **through the module object** and never by a
``from``-import. ``PV_SABOTAGE`` rebinds that attribute, and a name bound at
import time would keep calling the original — so the sabotage lane would report
green for a symbol nobody neutered. The same rule ``retry.py`` applies to
itself.

Under ``SERIALIZABLE`` a statement that fails with ``40001`` leaves its
transaction aborted, so every subsequent statement on that connection fails
with ``25P02`` until it is rolled back. :func:`_fetch_all` therefore rolls back
before retrying. Without that the "retry" issues the same statement into a
poisoned transaction and reports a different, more confusing SQLSTATE.

Why every name here starts with an underscore
----------------------------------------------
``tests/db/test_repository_read_only.py`` walks every module in this package
and requires each **public** function to take a ``Principal`` or an explicit
``(tenant_id, user_id)`` pair. That rule is right, and these helpers cannot
satisfy it: they receive SQL and parameters that a scoped caller has already
built. Underscore-prefixing them keeps the guard meaningful rather than
weakening it with an exemption list — the scan skips private names by design,
and no caller outside this package can reach them.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any, Final

import psycopg.errors as pgerr

from provenance_contracts.identity import Principal
from provenance_db import retry

__all__: list[str] = []

_JITTER: Final[random.Random] = random.Random()

#: What psycopg accepts as the second argument to ``execute``.
_Params = Mapping[str, Any] | Sequence[Any] | None


def _scope(principal: Principal) -> dict[str, uuid.UUID]:
    """The two ids every statement in this package binds.

    Taken from the verified :class:`~provenance_contracts.identity.Principal`
    and from nowhere else. ``15_API_SPEC.md`` section 3 states the rule the
    other way round — "never a caller-supplied ``user_id``" — and this is where
    it lands: there is no code path in this package that reads an owner id from
    anything but a principal, so there is no endpoint that can forget.
    """
    return {"tenant_id": principal.tenant_id, "user_id": principal.user_id}


def _owner(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """The same two bindings as :func:`_scope`, for a caller holding ids.

    The control plane's read adapters hold an
    ``services.control_plane.app.api.ports.OwnerScope`` -- ``(tenant_id,
    user_id)`` and nothing else -- rather than a full
    :class:`~provenance_contracts.identity.Principal`, because an
    ``OwnerScope`` is what ``app/api/deps.py`` builds from a verified principal
    and is the only thing the routes ever pass down. Synthesising a
    ``Principal`` in the adapter just to satisfy a signature would mean
    inventing a ``cognito_sub`` and a token window that no token ever carried,
    which is a worse shape than a two-argument pair: a fabricated identity that
    looks authentic is exactly what the type is supposed to prevent.

    ``tests/db/test_repository_read_only.py`` admits both forms for this
    reason -- "a ``Principal`` **or** an explicit ``(tenant_id, user_id)``
    pair" -- and the predicate stays in the SQL either way.
    """
    return {"tenant_id": tenant_id, "user_id": user_id}


def _rows_as_mappings(columns: list[str], records: Iterable[Any]) -> list[dict[str, Any]]:
    """Pair *columns* with each record, whatever row factory produced it.

    This was ``dict(zip(columns, record, strict=True))``, which is right for the
    tuple rows psycopg returns by default and silently wrong for anything else.
    A caller using ``row_factory=dict_row`` hands back a **mapping**, iterating
    a mapping yields its *keys*, and the lengths match -- so ``strict=True``
    never fires and every value is replaced by the name of its own column::

        columns   = ['id', 'distance', 'text']
        tuple row -> {'id': 7,    'distance': 0.42,       'text': 'hello'}
        dict  row -> {'id': 'id', 'distance': 'distance', 'text': 'text'}

    No exception, correct-looking shape. A retrieval ranking would then sort by
    the string ``"distance"`` for every candidate: a stable total order carrying
    no information, so the symptom is not a crash but plausible wrong results.

    ``strict=True`` was added to catch exactly this class and cannot, because
    the failure preserves length. A guard that appears to cover a case and does
    not is worse than none, because it stops anyone looking again.

    A mapping is accepted rather than refused -- it is a legitimate row factory
    -- but its keys must still agree with ``cursor.description``. Returning a
    row whose keys disagree would trade one quiet wrong answer for another.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, Mapping):
            missing = [name for name in columns if name not in record]
            if missing:
                raise ValueError(
                    f"row is missing {missing}, which cursor.description declares. "
                    "The statement and its description disagree."
                )
            rows.append({name: record[name] for name in columns})
        else:
            rows.append(dict(zip(columns, record, strict=True)))
    return rows


async def _fetch_all(
    conn: Any,
    sql: str,
    params: _Params = None,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Every row of *sql*, as mappings keyed by ``cursor.description``.

    Mappings rather than tuples: a positional row silently reorders when a
    projection changes, and the caller that reads ``row[3]`` keeps working
    while reading the wrong column.

    Args:
        conn: an open ``psycopg.AsyncConnection``.
        sql: the statement. Built by the caller, which owns the scoping.
        params: bound parameters. **Values, never fragments** — the query
            vector in particular (``D-06-001``).
        policy: attempt cap and backoff. Defaults to the v1 policy.
        sleep: injected for tests; the default is real.
        rng: injected for tests; the default is a module-level ``Random``.

    Raises:
        RetryExhausted: every attempt failed with a retryable SQLSTATE.
        psycopg.Error: any non-retryable database error, unchanged and
            immediately. A check violation is not contention and waiting will
            not help.
    """
    jitter = rng if rng is not None else _JITTER
    last: BaseException | None = None

    for attempt in range(1, policy.max_tx_attempts + 1):
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                description = cursor.description or ()
                columns = [column.name for column in description]
                records = await cursor.fetchall()
        except pgerr.Error as error:
            # Through the module object: PV_SABOTAGE rebinds the attribute
            # there, and a from-import would resolve to a reference captured
            # before the rebind.
            if not retry.is_retryable(getattr(error, "sqlstate", None)):
                raise
            last = error
            await conn.rollback()
            if attempt == policy.max_tx_attempts:
                break
            await sleep(retry.backoff_delay_seconds(attempt, policy, jitter))
            continue
        return _rows_as_mappings(columns, records)

    raise retry.RetryExhausted(policy.max_tx_attempts, last)


async def _fetch_one(
    conn: Any,
    sql: str,
    params: _Params = None,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> dict[str, Any] | None:
    """The first row, or ``None``.

    ``None`` is the answer for both "no such row" and "that row belongs to
    somebody else", and deliberately so: distinguishing them would turn every
    by-id read into an existence oracle across the tenant space.
    """
    rows = await _fetch_all(conn, sql, params, policy=policy, sleep=sleep, rng=rng)
    return rows[0] if rows else None
