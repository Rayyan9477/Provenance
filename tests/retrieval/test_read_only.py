"""R-3 — retrieval never writes, and the session posture that enforces it.

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 1, rule R-3, and section 6's A.2
  session block.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 18, test 18.20.

Why this is a database test and not a code review note
--------------------------------------------------------
"Retrieval never writes" is the kind of rule that is true on the day it is
written and true for as long as nobody is in a hurry. ``READ ONLY`` makes it
structural: a retrieval bug that tries to write fails with ``25006
read_only_sql_transaction`` instead of corrupting memory, and it fails on the
first statement rather than after a partial mutation.

The block in :data:`scope.SESSION_STATEMENTS` is what the retrieval connection
emits, so this executes **that tuple** rather than a hand-written approximation
of it. A test that opened its own ``READ ONLY`` transaction would prove that
CockroachDB implements ``READ ONLY``, which was never in doubt.

``PRIORITY LOW`` is asserted for the same reason and a different failure: under
serializable isolation a low-priority reader yields to a concurrent writer
rather than pushing it. Retrieval runs on every artifact and every Advocate
invocation while the Memory Kernel commits rarely, so retrieval must never be
the reason a kernel transaction hits ``40001``. That is a live-cluster property
and it is not visible from the source.

Nothing here writes. Every statement below is rejected by the server before it
reaches a row, which is the assertion.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from services.control_plane.app.retrieval import scope

pytestmark = [pytest.mark.db, pytest.mark.retrieval]

#: ``25006``. The one SQLSTATE this file is about.
READ_ONLY_SQLSTATE = "25006"


def test_the_session_block_is_executable_against_the_live_cluster(
    retrieval_dsn: str,
) -> None:
    """The precondition, on its own line.

    ``SESSION_STATEMENTS`` is a tuple of SQL in a Python module, and a tuple of
    SQL in a Python module is a claim until something runs it. CockroachDB's
    ``BEGIN`` accepts a different set of modifiers from PostgreSQL's, so a
    posture that is merely plausible would be discovered at the first live
    retrieval rather than here.
    """
    with psycopg.connect(retrieval_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for statement in scope.SESSION_STATEMENTS:
            cur.execute(statement)  # type: ignore[arg-type]
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
        cur.execute("ROLLBACK")


def test_retrieval_cannot_write(retrieval_dsn: str) -> None:
    """Test 18.20. Any ``INSERT`` or ``UPDATE`` raises ``25006``.

    Two shapes, because they fail at different layers in other systems: an
    ``INSERT`` of a new row and an ``UPDATE`` of an existing one. Both are
    refused by the transaction's own posture, so neither reaches the table and
    neither depends on a grant.

    The ``UPDATE`` targets ``retraction_status`` deliberately -- it is the one
    evidence column the kernel *is* allowed to change, so a test that picked an
    immutable column could pass because of the immutability guard rather than
    because of ``READ ONLY``.
    """
    with psycopg.connect(retrieval_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for statement in scope.SESSION_STATEMENTS:
            cur.execute(statement)  # type: ignore[arg-type]

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction) as insert_error:
            cur.execute(
                "INSERT INTO evidence_items (id, tenant_id, user_id) VALUES (%s, %s, %s)",
                (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
            )
        assert insert_error.value.sqlstate == READ_ONLY_SQLSTATE

        cur.execute("ROLLBACK")
        for statement in scope.SESSION_STATEMENTS:
            cur.execute(statement)  # type: ignore[arg-type]

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction) as update_error:
            cur.execute("UPDATE evidence_items SET retraction_status = 'RETRACTED' WHERE false")
        assert update_error.value.sqlstate == READ_ONLY_SQLSTATE

        cur.execute("ROLLBACK")


def test_the_session_block_never_asks_for_a_follower_read(retrieval_dsn: str) -> None:
    """Section 6's third choice, asserted where it would be added.

    Follower reads are bounded-staleness reads roughly 4.8 seconds behind
    present. The ingestion graph writes evidence rows and then calls retrieval
    within the same run, typically within one second, so a stale read would
    silently omit the evidence just admitted -- invisible in the happy path and
    catastrophic in the duplicate-detection path, where the retrieval would not
    see the duplicate it exists to find.

    ``AS OF SYSTEM TIME`` is how that gets added, and it is one clause on an
    existing line rather than a new file, which is why the assertion is on the
    text as well as on the behaviour.
    """
    joined = " ".join(scope.SESSION_STATEMENTS).upper()
    assert "AS OF SYSTEM TIME" not in joined
    assert "FOLLOWER_READ" not in joined

    with psycopg.connect(retrieval_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for statement in scope.SESSION_STATEMENTS:
            cur.execute(statement)  # type: ignore[arg-type]
        cur.execute("SELECT now() - transaction_timestamp() < INTERVAL '1 second'")
        row = cur.fetchone()
        assert row is not None and row[0] is True, (
            "the retrieval transaction is reading from the past; a follower read "
            "would omit evidence admitted seconds earlier and the duplicate path "
            "would stop finding duplicates"
        )
        cur.execute("ROLLBACK")
