"""Connections, roles, bulk load, and the index dance (``T2.8`` steps 2-8).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 23 -- the mandatory drop / load /
  rebuild ordering, and the three failure modes it prevents.
- ``docs/specs/10_DATABASE_DDL.md`` section 12 (write-path ownership), section
  5.1 (the vector index), section 5.6 (the retraction UPDATE).
- ``db/migrations/versions/0002_evidence_plane.py`` -- ``VECTOR_INDEX_DDL``,
  reproduced here byte-identically so the seed's end state is the migration's.

The ordering, and why it is not a preference
--------------------------------------------
1. ``IMPORT INTO`` is **unsupported** on a table carrying a vector index. If the
   index exists, the fast bulk path is unavailable -- and ``0002``'s docstring
   says so, pointing here.
2. Large batch inserts into a vector-indexed table degrade badly, because every
   insert also performs partition maintenance on the ANN structure. An
   18,000-row load with the index live is dramatically slower than the same load
   followed by one index build. The symptom is not an error; the seed simply
   appears to hang.

So: drop the index, load, rebuild, and **wait for the schema-change job**. A
``CREATE INDEX`` that has returned is not a ``CREATE INDEX`` that has finished,
and a seed that silently leaves the index dropped produces a demo that works and
a ``G6.2`` that fails.

Concurrency
-----------
Two seed processes against one database are not safe, and the seed does not try
to make them safe -- there is no lock table to take and adding one would be a
migration. What it does do is wait for any in-flight ANN schema change before
touching the index, which removes the interleaving that costs an hour without
erroring: a queued ``DROP`` executing the moment a fifty-five-minute
``CREATE VECTOR INDEX`` succeeds. Run one seed at a time.

Credentials
-----------
Nothing here prints a DSN. :func:`role_dsn` resolves one from the environment or
from the gitignored ``.env`` and returns it wrapped, and every log line names
the *role*, never the URL. ``G0.3`` scans this repository with gitleaks and
``test_no_credential_in_pytest_output.py`` is the standing regression guard.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg

from provenance_contracts.settings import ROLE_DSN_BINDINGS

__all__ = [
    "ANN_DROP_THRESHOLD",
    "ANN_INDEX_NAME",
    "TRUNCATE_ORDER",
    "VECTOR_INDEX_DDL",
    "connect_as",
    "create_ann_index",
    "drop_ann_index",
    "existing_ids",
    "insert_batches",
    "pending_index_jobs",
    "recreate_database",
    "role_dsn",
    "truncate_all",
    "wait_for_index_job",
]

REPO_ROOT = Path(__file__).resolve().parents[2]

ANN_INDEX_NAME = "evidence_embedding_ann_idx"

#: Byte-identical to ``0002_evidence_plane.VECTOR_INDEX_DDL``. The end state of
#: the seed is the migration's end state; only the procedure differs.
VECTOR_INDEX_DDL = (
    "CREATE VECTOR INDEX evidence_embedding_ann_idx "
    "ON evidence_items (user_id, embedding vector_cosine_ops)"
)

_URL_PATTERN = re.compile(r"(?i)\b(postgres(?:ql)?://)[^\s'\"<>]+")

#: ``.env`` keys holding a login DSN per SQL role. Identical to the mapping in
#: ``services/control_plane/tests/db/conftest.py``, deliberately: two different
#: opinions about which key is which role is the kind of drift that produces a
#: seed loading evidence as the migrator and a gate that never notices.
#: Derived from ``provenance_contracts.settings.ROLE_DSN_BINDINGS`` rather than
#: restated, because this module used to restate it and the two drifted.
#:
#: `Settings` read `COCKROACH_KERNEL_URL`; this table read `PV_DB_KERNEL`; the
#: `.env` carried only the second. The kernel pool therefore failed to open
#: while the app pool opened fine, and the process started anyway. That is the
#: *second* time two registries for one fact cost something here -- the first
#: split the seed across two databases and produced "26 tables checked, 26
#: match" against a database holding zero evidence rows.
#:
#: **Precedence is aliases first, canonical name last**, which preserves the
#: order this table had before it was derived. Changing precedence is exactly
#: the class of edit that caused the split, so it is held fixed here and
#: `assert_roles_agree_on_database()` remains the thing that catches a
#: disagreement rather than this ordering.
ROLE_DSN_ENV: dict[str, tuple[str, ...]] = {
    role: tuple(name for name in (*binding.aliases, binding.env_var) if name)
    for role, binding in ROLE_DSN_BINDINGS.items()
    if binding.aliases or binding.env_var
}


def scrub(text: str) -> str:
    """Redact any database URL out of *text* before it can be printed."""
    return _URL_PATTERN.sub(r"\1<redacted>", text)


def _dotenv_value(name: str) -> str | None:
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return None
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return None


class MaskedDsn(str):
    """A DSN that does not print its password when anything reprs it.

    Same mechanism, and the same reason, as the fixture in
    ``services/control_plane/tests/db/conftest.py``: a ``str`` subclass is
    substitutable everywhere psycopg needs the real value, while ``repr`` -- the
    path a traceback, a log line or a pytest header takes -- is masked. The seed
    runs as a subprocess whose stdout lands in ``ops/tdd/`` transcripts, so this
    is not theoretical.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"MaskedDsn({scrub(str(self))!r})"


def database_name(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def recreate_database(name: str) -> None:
    """``DROP DATABASE ... CASCADE`` then ``CREATE DATABASE`` -- ``make demo-reset``.

    Deliberately in Python rather than in the Makefile. A recipe line would put
    the migrator DSN on a command line, where it lands in the process table, in
    shell history and in any CI log that echoes the recipe. Here the DSN is
    resolved from the environment and never leaves this process.

    Three guards, because this is the one irreversible operation in the
    repository: ``APP_ENV`` must be ``local`` or ``demo``,
    ``PROVENANCE_CONFIRM_DESTRUCTIVE`` must be ``yes``, and *name* must be one
    of the two databases this project owns. The seed's own ``--reset`` is the
    non-destructive alternative and is what routine work should use.
    """
    if os.environ.get("PROVENANCE_CONFIRM_DESTRUCTIVE") != "yes":
        raise SystemExit(
            "refusing to drop a database: set PROVENANCE_CONFIRM_DESTRUCTIVE=yes to "
            "confirm. `python -m scripts.seed --profile all --reset` truncates instead "
            "and is reversible by reseeding."
        )
    if name not in {"provenance", "provenance_ci"}:
        raise SystemExit(f"refusing to drop {name!r}: not a database this project owns")
    admin = role_dsn("pv_migrator", database="defaultdb")
    conn = psycopg.connect(admin, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {name} CASCADE")
            cur.execute(f"CREATE DATABASE {name}")
    finally:
        conn.close()


def _repoint(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def role_dsn(role: str, *, database: str | None = None) -> MaskedDsn:
    """The login DSN for *role*, optionally repointed at *database*.

    ``PROVENANCE_SEED_DATABASE`` exists so a developer can run the seed against
    ``provenance_ci`` with the ``.env`` that points at ``provenance``. Repointing
    can only move a connection towards the named database, never away from it,
    and the result is asserted before it is returned.
    """
    keys = ROLE_DSN_ENV.get(role)
    if keys is None:
        raise KeyError(f"no .env key is registered for role {role!r}")
    raw: str | None = None
    for key in keys:
        raw = os.environ.get(key) or _dotenv_value(key)
        if raw:
            break
    if not raw:
        raise RuntimeError(
            f"none of {', '.join(keys)} is configured; cannot connect as {role}. "
            f"The seed needs one login DSN per SQL role it writes as."
        )
    target = database or os.environ.get("PROVENANCE_SEED_DATABASE")
    if target:
        raw = _repoint(raw, target)
        assert database_name(raw) == target
    return MaskedDsn(raw)


def assert_roles_agree_on_database(*, database: str | None = None) -> str:
    """Every SQL role must resolve to the SAME database. Refuse otherwise.

    This exists because the seed silently split itself across two databases and
    then reported success.

    ``ROLE_DSN_ENV`` resolves each role independently and prefers a different key
    per role -- ``PV_DB_MIGRATOR_CI`` for the migrator, ``COCKROACH_DATABASE_URL``
    for the app role. Once a ``.env`` carries both a CI key and a demo key those
    two point at DIFFERENT DATABASES, and nothing noticed:

      * the migrator probe read ``provenance_ci`` and reported
        "corpus already complete: 0 rows to load";
      * the app role would have bulk-loaded into ``provenance``;
      * ``tools/manifest_check`` prefers ``PROVENANCE_TEST_DB_URL`` and validated
        ``provenance_ci`` -- a THIRD independent choice;
      * ``make seed`` printed "26 tables checked, 26 match" while the database it
        had been asked to seed held zero evidence rows.

    Every component behaved exactly as written. The failure was that nothing
    asked whether they agreed, so the result was a green log for work that did
    not happen -- the shape ``23_PHASE_GATES.md`` section 23 exists to prevent.

    Returns the agreed database name, so a caller can print what it is about to
    write to rather than inferring it.
    """
    resolved: dict[str, str] = {}
    for role in ROLE_DSN_ENV:
        try:
            resolved[role] = database_name(str(role_dsn(role, database=database)))
        except RuntimeError:
            continue  # an unconfigured role is a separate, louder failure
    if not resolved:
        raise RuntimeError("no SQL role has a configured DSN; the seed cannot connect")
    names = set(resolved.values())
    if len(names) > 1:
        detail = "; ".join(f"{role} -> {name}" for role, name in sorted(resolved.items()))
        raise RuntimeError(
            "the SQL roles resolve to DIFFERENT databases, so this seed would write "
            "part of the corpus to one and part to another: "
            + detail
            + ". Pass --database explicitly, or set PROVENANCE_SEED_DATABASE, so every "
            "role is repointed at one target. Refusing rather than seeding a split "
            "corpus: a half-seeded pair of databases is harder to detect than a "
            "failure, and manifest_check would validate whichever one it happens to "
            "resolve."
        )
    return names.pop()


@contextmanager
def connect_as(role: str, *, database: str | None = None) -> Iterator[psycopg.Connection[Any]]:
    """A connection as *role*, asserted to actually **be** that role.

    The assertion is the cheapest possible proof that the grant boundary is
    real: a ``.env`` key silently pointing at the wrong role would otherwise
    make the seed load evidence as ``pv_migrator`` while every log line claimed
    ``pv_app_reader_writer``.
    """
    dsn = role_dsn(role, database=database)
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            row = cur.fetchone()
        actual = str(row[0]) if row else "<unknown>"
        if actual != role:
            raise RuntimeError(
                f"connection for {role!r} authenticated as {actual!r}; "
                f"the .env key for this role points at the wrong credential"
            )
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 2 -- truncate in reverse foreign-key order
# ---------------------------------------------------------------------------

#: Reverse foreign-key order across all 26 canonical tables. Truncating in one
#: statement with ``CASCADE`` would work, but it would also silently truncate a
#: table nobody listed -- and the point of ``--reset`` is that its blast radius
#: is enumerated where a reviewer can read it.
TRUNCATE_ORDER: tuple[str, ...] = (
    "processed_events",
    "outbox_events",
    "action_executions",
    "action_intents",
    "prospective_triggers",
    "state_transitions",
    "fulfillments",
    "commitments",
    "conflicts",
    "belief_support",
    "belief_versions",
    "beliefs",
    "claims",
    "kernel_decisions",
    "memory_proposals",
    "evidence_items",
    "source_artifacts",
    "cases",
    "contexts",
    "relationships",
    "counterparties",
    "ingest_aliases",
    "agent_runs",
    "idempotency_records",
    "users",
    "tenants",
)


def truncate_all(conn: psycopg.Connection[Any]) -> None:
    """Empty every canonical table, children first.

    ``beliefs.current_version_id`` and ``belief_versions.belief_id`` reference
    each other, so the pair cannot be truncated independently; ``CASCADE`` on
    the one statement resolves the cycle without widening the blast radius
    beyond the enumerated list, because every table in the graph is in it.
    """
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {', '.join(TRUNCATE_ORDER)} CASCADE")
    conn.commit()


# ---------------------------------------------------------------------------
# Steps 4, 7, 8 -- the index dance
# ---------------------------------------------------------------------------


def existing_ids(conn: psycopg.Connection[Any], table: str) -> set[UUID]:
    """Every primary key already in *table*.

    Eighteen thousand UUIDs is about 650 KB and one sequential scan, which is
    cheap next to what it buys: knowing whether the bulk load has any work to do
    before deciding to drop a vector index that takes fifty-three minutes to
    rebuild on this cluster.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {table}")  # a literal from the caller, never input
        return {row[0] for row in cur.fetchall()}


#: A poll that hits 40001 is retried rather than raised: an unhandled retry
#: error in the watcher discards a 55-minute build. Five attempts over ~15s.
_POLL_RETRIES: Final[int] = 5
_POLL_RETRY_BACKOFF: Final[float] = 1.0

#: How often the index wait re-reads ``SHOW JOBS``.
INDEX_JOB_POLL_SECONDS = 15.0

#: How long it will wait before giving up. The build was measured at 52-55
#: minutes over 18,035 rows on this cluster, so two hours is roughly double the
#: worst observed run: long enough not to abandon a healthy build, short enough
#: that a wedged one is not a hang.
INDEX_JOB_TIMEOUT_SECONDS = 7200.0


def pending_index_jobs(conn: psycopg.Connection[Any]) -> list[tuple[Any, ...]]:
    """Unfinished schema-change jobs touching the ANN index.

    Retries on ``40001`` itself, and that is not belt-and-braces. Splitting the
    wait into short polls fixed the LONG transaction being torn down, but each
    POLL is still a transaction against ``[SHOW JOBS]`` on a cluster that is
    mid-schema-change, and CockroachDB will hand it the same forced retry::

        psycopg.errors.SerializationFailure: restart transaction:
        TransactionRetryWithProtoRefreshError: forced by crdb_internal.force_retry()

    Observed at 19:33Z, one poll into a rebuild: the loop raised out of the seed
    and left 18,035 rows with no ANN index. An unhandled 40001 in the code that
    WATCHES a fifty-five-minute build discards the build.

    Rolling back before the retry is load-bearing: after a serialization failure
    the connection is in a failed transaction and every later statement returns
    ``current transaction is aborted`` until it is cleared.
    """
    last: Exception | None = None
    for attempt in range(_POLL_RETRIES):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id, status, coalesce(running_status, '') FROM [SHOW JOBS] "
                    "WHERE description ILIKE %s AND status NOT IN ('succeeded','failed','canceled')",
                    (f"%{ANN_INDEX_NAME}%",),
                )
                rows = [tuple(row) for row in cur.fetchall()]
            conn.commit()
            return rows
        except psycopg.errors.SerializationFailure as exc:  # 40001
            last = exc
            conn.rollback()
            time.sleep(_POLL_RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(
        f"[SHOW JOBS] returned 40001 on {_POLL_RETRIES} consecutive polls while waiting "
        f"for {ANN_INDEX_NAME}. The build may still be running; check SHOW JOBS by hand "
        f"before re-running the seed, because a second seed would drop the index this "
        f"one is building. Last error: {last}"
    )


def wait_for_index_job(
    conn: psycopg.Connection[Any],
    *,
    poll_seconds: float = INDEX_JOB_POLL_SECONDS,
    timeout_seconds: float = INDEX_JOB_TIMEOUT_SECONDS,
) -> None:
    """Step 8. Schema changes are asynchronous; a returned CREATE is not a done one.

    ``41_RUNBOOK.md`` section 4.2 gives this as one blocking statement::

        SHOW JOBS WHEN COMPLETE (SELECT job_id FROM [SHOW JOBS] WHERE ...);

    That does not survive contact with this cluster. psycopg opens a transaction
    per statement, the build takes fifty-five minutes, and CockroachDB tears down
    a transaction that old::

        psycopg.errors.SerializationFailure: restart transaction:
        TransactionRetryWithProtoRefreshError: forced by crdb_internal.force_retry()

    -- observed here at 19:33Z, after the wait had already held the session open.
    A poll is not a workaround for the runbook's intent, it *is* the intent: each
    read is its own short transaction, the wait survives an hour, and it prints
    progress so the difference between "building" and "wedged" is visible from
    the log rather than inferred from silence.
    """
    deadline = time.perf_counter() + timeout_seconds
    waited = 0.0
    while True:
        pending = pending_index_jobs(conn)
        if not pending:
            return
        if time.perf_counter() > deadline:
            raise RuntimeError(
                f"{ANN_INDEX_NAME} schema change still running after "
                f"{timeout_seconds / 60:.0f} minutes: {pending}"
            )
        if waited and waited % 300 < poll_seconds:
            print(
                f"    still waiting on {ANN_INDEX_NAME} ({waited / 60:.0f} min): {pending[0]}",
                flush=True,
            )
        time.sleep(poll_seconds)
        waited += poll_seconds


def drop_ann_index(conn: psycopg.Connection[Any]) -> bool:
    """Step 4. Returns True when an index was actually there to drop.

    Waits for any in-flight ANN schema change first, and this is not tidiness.
    CockroachDB serialises schema changes on a table, so a ``DROP`` issued while
    a ``CREATE VECTOR INDEX`` is backfilling does not fail and does not run --
    it **queues**, and executes the instant the create succeeds. Observed on
    this cluster: job 1202609471257542657 created the index at 19:23:16Z and job
    1202620322564341761 dropped it at 19:23:16Z, having been queued since
    18:28. Fifty-five minutes of index build, discarded in two seconds, by a
    second seed process that then exited before its own create.

    Waiting turns that into a serial second run that finds the index present and
    leaves it alone. It does not make concurrent seeds safe in general -- see the
    module docstring -- but it removes the one interleaving that silently
    destroys an hour of work.
    """
    wait_for_index_job(conn)
    existed = ann_index_exists(conn)
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {ANN_INDEX_NAME} CASCADE")
    conn.commit()
    return existed


def ann_index_exists(conn: psycopg.Connection[Any]) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM [SHOW INDEXES FROM evidence_items] WHERE index_name = %s",
            (ANN_INDEX_NAME,),
        )
        row = cur.fetchone()
    return bool(row and int(row[0]) > 0)


def create_ann_index(conn: psycopg.Connection[Any]) -> None:
    """Step 7. Only after the last evidence row is committed, never before."""
    wait_for_index_job(conn)
    if ann_index_exists(conn):
        return
    with conn.cursor() as cur:
        cur.execute(VECTOR_INDEX_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Step 6 -- the bulk load
# ---------------------------------------------------------------------------

#: ``70_TASK_PLAN.md`` T2.8 step 6 and ``10_DATABASE_DDL.md`` section 17.7:
#: "multi-row INSERT, 500 rows per statement, inside explicit transactions".
BATCH_ROWS = 500

#: Pending rows at or above which the ANN index is dropped for the load.
#:
#: Section 23 gives two reasons for the drop and both scale with the size of the
#: load: ``IMPORT INTO`` is unsupported on a vector-indexed table (irrelevant to
#: the batched-``INSERT`` path this seed actually uses), and inserts into one pay
#: ANN partition maintenance per row. The second is a cost proportional to row
#: count, so the correct response is a threshold, not a constant.
#:
#: The number that sets it: rebuilding this index over 18,035 rows was measured
#: at **52 minutes 56 seconds** on this cluster. Paying that to avoid partition
#: maintenance on three rows is not a trade, it is a mistake -- and it would
#: leave the index absent for those fifty-three minutes, which is section 23's
#: own first failure mode. One batch is the boundary: below it the load is a
#: single statement and maintenance is seconds.
ANN_DROP_THRESHOLD = BATCH_ROWS


def insert_batches(
    conn: psycopg.Connection[Any],
    table: str,
    columns: Sequence[str],
    rows: Sequence[tuple[Any, ...]],
    *,
    batch_rows: int = BATCH_ROWS,
    label: str = "",
    on_conflict: str = "ON CONFLICT DO NOTHING",
    placeholders: Sequence[str] | None = None,
) -> int:
    """Multi-row ``INSERT`` in explicit transactions. Returns rows attempted.

    ``ON CONFLICT DO NOTHING`` is what makes ``make seed`` idempotent without a
    truncate: every id is a ``uuid5``, so a reseed re-offers exactly the rows
    that are already there and changes nothing. That is a stronger property than
    truncate-and-reload, which would make "seeding twice produces identical
    counts" true of any loader at all.
    """
    if not rows:
        return 0
    cells = list(placeholders) if placeholders else ["%s"] * len(columns)
    placeholder = "(" + ", ".join(cells) + ")"
    prefix = f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
    written = 0
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        sql = prefix + ", ".join([placeholder] * len(chunk)) + " " + on_conflict
        params: list[Any] = [value for row in chunk for value in row]
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        written += len(chunk)
        if label and (written % (batch_rows * 8) == 0 or written == len(rows)):
            print(f"    {label}: {written}/{len(rows)} rows", flush=True)
    return written


def vector_literal(values: Sequence[float]) -> str:
    """A CockroachDB ``VECTOR`` literal.

    Six decimal places, not the full float32 repr. Titan v2 vectors are unit
    norm over 1024 dimensions, so a typical component is around 0.031 and six
    decimals is roughly five significant digits -- a cosine error below 1e-5,
    which is four orders of magnitude smaller than the gaps ANN ranking turns
    on. The saving is real: the full repr would put roughly 40% more bytes on
    the wire for 18,035 rows.
    """
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"
