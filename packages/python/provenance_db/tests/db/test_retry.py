"""The retry loop against a live CockroachDB cluster — T3.2, layer L2.

Authority
---------
- ``quality/23_PHASE_GATES.md`` section 9 — ``G3.2``, ``G3.3``, ``G3.4``,
  ``G3.6``.
- ``EXECUTION/70_TASK_PLAN.md`` T3.2: "the 40001 is forced by **two
  overlapping transactions on one row**, not by monkeypatching the driver — a
  monkeypatched 40001 proves nothing about CockroachDB and is rejected at
  review".
- ``specs/12_KERNEL_ALGORITHMS.md`` section 7.

How the ``40001`` is forced, exactly
------------------------------------
Two real connections to ``provenance_ci``. The transaction under test reads
row ``k = 1``; a **second** connection then commits an update to that same row;
the transaction under test writes it. CockroachDB pushes the writer's
timestamp past the committed value (``WriteTooOldError``), tries to refresh the
transaction's reads, finds that the key it read has changed, and cannot — so it
returns ``40001`` to the client rather than retrying internally. Because the
first read has already been delivered to the client, the server-side automatic
retry is not available, which is what makes this deterministic rather than
lucky.

Observed on CockroachDB CCL v26.2.5::

    A read: (0,)
    B committed
    A update raised: SerializationFailure sqlstate= 40001
    restart transaction: TransactionRetryWithProtoRefreshError: WriteTooOldError

No table from the canonical schema is touched. Phase 2 is still in flight, so
each test owns a ``_pv_t3_``-prefixed table, creates it, and drops it again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import psycopg
import pytest
from psycopg import errors as pgerr
from pydantic import SecretStr

from provenance_db.pools import RolePool, SqlRole
from provenance_db.retry import (
    RETRYABLE_SQLSTATES,
    RetryExhausted,
    RetryPolicy,
    is_retryable,
    run_in_serializable_tx,
)
from provenance_db.urls import MappingDsnSource

pytestmark = [pytest.mark.db, pytest.mark.concurrency]

#: The shipped policy. Asserted here rather than a fast stand-in, because the
#: attempt cap and the real backoff are what ``G3.3`` reads off the raised
#: error. The exhaustion test therefore really does wait out four backoffs.
POLICY = RetryPolicy()

#: One connection is enough for a single-writer test, and opening the pool's
#: default two against a cloud cluster is most of the wall clock here.
SMALL: dict[str, int] = {"min_size": 1, "max_size": 2}


@pytest.fixture
async def retry_table(
    request: pytest.FixtureRequest, test_db_dsn: SecretStr, scratch_table: str
) -> AsyncIterator[str]:
    """A two-column scratch table seeded with one row, dropped on teardown."""
    table = f"{scratch_table}_{request.function.__name__.removeprefix('test_')}"
    dsn = test_db_dsn.get_secret_value()
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await conn.execute(f"CREATE TABLE {table} (k INT PRIMARY KEY, v INT NOT NULL)")
        await conn.execute(f"INSERT INTO {table} (k, v) VALUES (1, 0)")
    try:
        yield table
    finally:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")


def ci_source(test_db_dsn: SecretStr) -> MappingDsnSource:
    return MappingDsnSource({SqlRole.MIGRATOR.value: test_db_dsn})


async def read_over_a_second_connection(dsn: SecretStr, query: str) -> Any:
    """Open a connection that is not the one under test, and read committed state."""
    async with await psycopg.AsyncConnection.connect(
        dsn.get_secret_value(), autocommit=True
    ) as conn:
        cur = await conn.execute(query)
        row = await cur.fetchone()
        assert row is not None
        return row[0]


def contending_callback(
    table: str, competitor: psycopg.AsyncConnection[Any], contend_while: Callable[[int], bool]
) -> tuple[Callable[[Any, datetime], Any], list[int]]:
    """A callback that lets a second connection commit between its read and its write."""
    attempts: list[int] = []

    async def callback(conn: Any, tx_now: datetime) -> int:
        attempts.append(len(attempts) + 1)
        cur = await conn.execute(f"SELECT v FROM {table} WHERE k = 1")
        row = await cur.fetchone()
        assert row is not None
        if contend_while(len(attempts)):
            await competitor.execute(f"UPDATE {table} SET v = v + 1 WHERE k = 1")
        await conn.execute(f"UPDATE {table} SET v = v + 10 WHERE k = 1")
        return int(row[0])

    return callback, attempts


async def test_injected_40001_retries_and_commits(test_db_dsn: SecretStr, retry_table: str) -> None:
    """``G3.2``. Two overlapping transactions, twice, then a clean commit.

    The final value proves which writes survived: the competitor's two ``+1``
    commits did, both rolled-back ``+10`` attempts did not, and the third
    attempt's ``+10`` did. ``2 + 10 = 12``. A loop that replayed a stale plan
    instead of re-reading would land on ``10``.
    """
    async with await psycopg.AsyncConnection.connect(
        test_db_dsn.get_secret_value(), autocommit=True
    ) as competitor:
        callback, attempts = contending_callback(retry_table, competitor, lambda n: n <= 2)
        async with RolePool(SqlRole.MIGRATOR, ci_source(test_db_dsn), **SMALL) as pool:
            outcome = await run_in_serializable_tx(pool, callback, config=POLICY)

    print(f"retry_count={outcome.retry_count}")
    assert outcome.retry_count == 2
    assert outcome.attempts == 3
    assert len(attempts) == 3
    assert outcome.value == 2, "the third attempt must read the competitor's committed value"

    final = await read_over_a_second_connection(
        test_db_dsn, f"SELECT v FROM {retry_table} WHERE k = 1"
    )
    assert final == 12


async def test_retry_exhaustion_raises(test_db_dsn: SecretStr, retry_table: str) -> None:
    """``G3.3``. Contention on every attempt exhausts the budget and raises.

    Nothing is enqueued and nothing is written: the caller re-drives over
    ``503`` + ``Retry-After``, which is the whole of the recovery path
    (``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*).
    """
    async with await psycopg.AsyncConnection.connect(
        test_db_dsn.get_secret_value(), autocommit=True
    ) as competitor:
        callback, attempts = contending_callback(retry_table, competitor, lambda n: True)
        async with RolePool(SqlRole.MIGRATOR, ci_source(test_db_dsn), **SMALL) as pool:
            with pytest.raises(RetryExhausted) as caught:
                await run_in_serializable_tx(pool, callback, config=POLICY)

    assert caught.value.attempts == 5
    assert caught.value.sqlstate == "40001"
    assert len(attempts) == 5

    final = await read_over_a_second_connection(
        test_db_dsn, f"SELECT v FROM {retry_table} WHERE k = 1"
    )
    assert final == 5, "five competitor increments and not one byte from the exhausted caller"


async def test_rollback_leaves_no_partial_writes(test_db_dsn: SecretStr, retry_table: str) -> None:
    """``G3.4``. Row counts before == after, read over a SECOND connection.

    The failure is a real ``23505`` from the database rather than a raised
    Python exception, so the rollback being asserted is the database's, not
    the wrapper's.
    """
    count_query = f"SELECT count(*) FROM {retry_table}"
    before = await read_over_a_second_connection(test_db_dsn, count_query)

    async def callback(conn: Any, tx_now: datetime) -> None:
        await conn.execute(f"INSERT INTO {retry_table} (k, v) VALUES (2, 20)")
        await conn.execute(f"INSERT INTO {retry_table} (k, v) VALUES (3, 30)")
        await conn.execute(f"INSERT INTO {retry_table} (k, v) VALUES (1, 99)")

    async with RolePool(SqlRole.MIGRATOR, ci_source(test_db_dsn)) as pool:
        with pytest.raises(pgerr.UniqueViolation):
            await run_in_serializable_tx(pool, callback, config=POLICY)

    after = await read_over_a_second_connection(test_db_dsn, count_query)
    print(f"rows before={before} after={after}")
    assert before == 1
    assert after == before

    survivor = await read_over_a_second_connection(
        test_db_dsn, f"SELECT v FROM {retry_table} WHERE k = 1"
    )
    assert survivor == 0, "the two rows that succeeded before the violation are gone too"


async def test_the_sqlstate_cockroach_returns_is_the_one_the_classifier_retries(
    test_db_dsn: SecretStr, retry_table: str
) -> None:
    """The join between the unit classifier and the cluster.

    ``test_retry_semantics.py`` asserts that ``40001`` is retryable against a
    constant. This asserts that the constant is the value CockroachDB actually
    sends for the interleaving above — without that, the unit test is a
    tautology about a string this repository wrote down for itself.
    """
    async with (
        await psycopg.AsyncConnection.connect(test_db_dsn.get_secret_value()) as under_test,
        await psycopg.AsyncConnection.connect(
            test_db_dsn.get_secret_value(), autocommit=True
        ) as competitor,
    ):
        with pytest.raises(pgerr.Error) as caught:
            async with under_test.transaction():
                cur = await under_test.execute(f"SELECT v FROM {retry_table} WHERE k = 1")
                assert await cur.fetchone() is not None
                await competitor.execute(f"UPDATE {retry_table} SET v = v + 1 WHERE k = 1")
                await under_test.execute(f"UPDATE {retry_table} SET v = v + 10 WHERE k = 1")

    assert caught.value.sqlstate == "40001"
    assert isinstance(caught.value, pgerr.SerializationFailure)
    assert caught.value.sqlstate in RETRYABLE_SQLSTATES
    assert is_retryable(caught.value.sqlstate) is True
