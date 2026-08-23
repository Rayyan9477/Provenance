"""Fixtures for the cross-package retrieval lane (``T6.1``-``T6.6``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Repository layout canon*: top-level
  ``tests/`` holds only genuinely cross-package suites, and ``retrieval/`` is
  named as one of the three. Retrieval spans ``provenance_contracts`` (the
  ``RetrievalContext`` shape), ``provenance_domain`` (the enums and the money
  type), the live database, and ``services.control_plane.app.retrieval``, so it
  belongs here rather than beside any one package.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 9, 13 and 14.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.1``-``G6.7``.

Credential hygiene, restated because this file resolves a DSN
--------------------------------------------------------------
``services/control_plane/tests/db/conftest.py`` explains the failure at length:
pytest renders every test-function argument in its failure header with
``repr()``, so a session fixture returning a plain ``str`` DSN writes the live
migrator password into the output of **any** failing test in the lane -- and
that output is committed as gate evidence. :class:`MaskedDsn` is the same
``str`` subclass, reproduced rather than imported because that conftest is
owned by another task and a cross-directory ``import conftest`` does not
resolve under ``--import-mode=importlib``. ``test_no_credential_in_pytest_
output.py`` is the regression guard for both copies.

Zero Bedrock calls in this lane
--------------------------------
Every vector this lane needs already exists: 18,035 rows in ``evidence_items``
carry a stored ``embedding``, and ``db/seeds/vectors.parquet`` carries the same
vectors keyed by the sha256 of their template render. A query vector is
therefore *read*, never *computed*. That is not a convenience -- ``T6.1``'s own
acceptance criterion is that "clearing the cache yields the identical vector
recomputed", which is only meaningful if the normal path never pays for a call.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The only database this lane is permitted to touch.
#:
#: **Not ``provenance_ci``**, which this lane used to share with the migration
#: lane. Their requirements are incompatible: this lane needs the database
#: *seeded* -- 18,035 evidence rows, 3 retraction fixtures, an ANN index, about
#: an hour to build -- and ``services/control_plane/tests/db`` runs a migration
#: drill that downgrades to base and re-upgrades, because a chain that has never
#: been run from base has never been tested. That drill therefore destroys
#: whatever the database held.
#:
#: Measured: an hour-long seed of ``provenance_ci`` produced 6 passing retraction
#: tests and moved ``make sabotage`` to 13/13 caught. A later ``pytest -m db``
#: left ``evidence_items`` at 0 rows and both went straight back. Nothing errored
#: -- the suite simply returned to skipping, which reads like a misconfiguration
#: rather than a deliberate wipe.
#:
#: Neither lane was wrong. They cannot both own one database.
#:
#: The demo database ``provenance`` is off-limits to both: it carries the seeded
#: state the recorded submission runs against.
EVAL_DATABASE_NAME = "provenance_eval"

#: Kept as an alias so nothing that imported the old name breaks silently, and
#: so a reader grepping for it lands on the explanation above.
CI_DATABASE_NAME = EVAL_DATABASE_NAME

EVAL_URL_ENV = "PROVENANCE_EVAL_DB_URL"

_URL_PATTERN = re.compile(r"(?i)\b(postgres(?:ql)?://)[^\s'\"<>]+")

#: ``ops/decisions/VECTOR_INDEX_VARIANT.md`` -- VARIANT A, prefix ``user_id``.
ANN_INDEX = "evidence_embedding_ann_idx"

#: ``scripts/seed/ids.py``: ``PROVENANCE_SEED_NS``, reproduced so this lane can
#: name a seeded row without importing the seed package. Never regenerate it.
PROVENANCE_SEED_NS = uuid.UUID("6f2b1c40-0000-4000-8000-70726f76656e")


def sid(*parts: str) -> uuid.UUID:
    """``sid('evidence', 'isp-wrong-term-date')`` -- the same UUID forever."""
    return uuid.uuid5(PROVENANCE_SEED_NS, ":".join(parts))


#: ``docs/specs/10_DATABASE_DDL.md`` -- the owning spec for section 5.5.
DDL_SPEC = REPO_ROOT / "docs" / "specs" / "10_DATABASE_DDL.md"


def _scrub(text: str) -> str:
    return _URL_PATTERN.sub(r"\1<redacted>", text)


@pytest.fixture(scope="session")
def ddl_spec_text() -> str:
    """``10_DATABASE_DDL.md`` read as text, so the shipped SQL can be diffed
    against the block the spec prints rather than against a transcription."""
    return DDL_SPEC.read_text(encoding="utf-8")


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
    """A DSN whose ``repr()`` -- the one path pytest uses -- is redacted.

    ``str(dsn)`` and f-string interpolation deliberately still yield the real
    value, because every consumer hands it straight to psycopg. This narrows
    the accident; it does not make the value safe to print on purpose.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"MaskedDsn({_scrub(str(self))!r})"


@pytest.fixture(scope="session")
def retrieval_dsn() -> MaskedDsn:
    """The seeded evaluation database, or a skip that says how to build one.

    The skip message names the command deliberately. "the retrieval lane needs a
    CockroachDB cluster" sent a reader looking for a cluster they already had;
    what was missing was a *seeded* database, and the seed is an hour, so
    guessing is expensive.
    """
    dsn = os.environ.get(EVAL_URL_ENV) or _dotenv_value(EVAL_URL_ENV)
    if not dsn:
        pytest.skip(
            f"{EVAL_URL_ENV} is not set. This lane needs a SEEDED database of its "
            f"own -- {EVAL_DATABASE_NAME!r} -- because the migration lane rebuilds "
            "provenance_ci from base and would wipe it. Build one with:\n"
            f"    python -m scripts.seed --profile all --embeddings cache-only "
            f"--database {EVAL_DATABASE_NAME}"
        )
    name = dsn.rsplit("/", 1)[-1].split("?", 1)[0]
    if name != EVAL_DATABASE_NAME:
        raise RuntimeError(
            f"{EVAL_URL_ENV} names database {name!r}; the retrieval lane may only "
            f"touch {EVAL_DATABASE_NAME!r}. It must NOT be pointed at "
            "'provenance_ci' (the migration drill wipes it) or at 'provenance' "
            "(the demo corpus)."
        )
    return MaskedDsn(dsn)


@pytest.fixture
def rconn(retrieval_dsn: MaskedDsn) -> Iterator[psycopg.Connection]:
    """A read-only connection to the seeded corpus.

    ``READ ONLY`` is the point rather than a precaution: ``13_RETRIEVAL_SPEC.md``
    section 6 makes R-3 structural -- a retrieval bug that tries to write fails
    with ``25006 read_only_sql_transaction`` instead of corrupting memory -- and
    a lane that proves retrieval correctness from a read-write connection has
    not proved that. It also means this lane cannot disturb the concurrent seed.
    """
    conn = psycopg.connect(retrieval_dsn)
    try:
        conn.read_only = True
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(scope="session")
def hero_user(retrieval_dsn: MaskedDsn) -> tuple[uuid.UUID, uuid.UUID]:
    """``(tenant_id, user_id)`` for Alex Rivera, resolved rather than hard-coded."""
    return _user_by_email(retrieval_dsn, "alex.rivera@example.invalid")


@pytest.fixture(scope="session")
def iso_a_user(retrieval_dsn: MaskedDsn) -> tuple[uuid.UUID, uuid.UUID]:
    return _user_by_email(retrieval_dsn, "iso-a@example.invalid")


@pytest.fixture(scope="session")
def iso_b_user(retrieval_dsn: MaskedDsn) -> tuple[uuid.UUID, uuid.UUID]:
    return _user_by_email(retrieval_dsn, "iso-b@example.invalid")


def _user_by_email(dsn: str, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tenant_id, id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"the corpus is not seeded: no user {email!r}")
    return (row[0], row[1])


@pytest.fixture
def stored_vector(rconn: psycopg.Connection) -> Callable[[uuid.UUID], str]:
    """The stored 1024-float embedding of one seeded evidence row, as a literal.

    Reading a vector the seed already paid Bedrock for is what keeps this lane
    at zero live model calls. It is also *stronger* than a freshly computed
    query vector for the isolation proof: a row's own embedding is at cosine
    distance exactly 0.0 from itself, so it is rank 1 in any unfiltered search
    over the combined corpus and its absence cannot be luck.
    """

    def _vector(evidence_id: uuid.UUID) -> str:
        with rconn.cursor() as cur:
            cur.execute(
                "SELECT embedding::STRING FROM evidence_items WHERE id = %s", (evidence_id,)
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            pytest.skip(f"evidence {evidence_id} has no stored embedding")
        return str(row[0])

    return _vector


#: A 1024-float vector literal, as CockroachDB echoes it back inside a plan.
#: The planner renders the bound value in full on several lines, which is ~14 KB
#: per occurrence and four occurrences per plan. An assertion that fails while
#: printing 56 KB of floats is an assertion nobody reads, and the transcript it
#: writes into ``ops/tdd/`` is worse than useless.
_VECTOR_LITERAL = re.compile(r"'\[-?\d[^']{200,}\]'")


@pytest.fixture
def explain(rconn: psycopg.Connection) -> Callable[..., str]:
    """``EXPLAIN (VERBOSE) <sql>`` with bound parameters, as one elided string.

    Parameters are bound, not interpolated. ``D-06-001`` is precisely a defect
    about *how the query vector reaches the planner*, so an EXPLAIN helper that
    substituted values into the SQL string would test a query shape the
    repository never emits and would report the defect fixed.

    Long vector literals are replaced by a marker. Only the *shape* of the plan
    is ever asserted -- which node was chosen, which index it names, whether the
    span is a prefix or a full scan -- and none of that is carried by the
    floats.
    """

    def _run(sql: str, params: object = None) -> str:
        with rconn.cursor() as cur:
            cur.execute("EXPLAIN (VERBOSE) " + sql, params)
            plan = "\n".join(str(row[0]) for row in cur.fetchall())
        return _VECTOR_LITERAL.sub("'<1024-float query vector>'", plan)

    return _run
