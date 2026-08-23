"""Everything this harness reads from the cluster, and nothing it writes.

Read-only by construction, three ways
-------------------------------------
1. The session is put into ``READ ONLY`` before the first statement, so a write
   is refused by the database rather than by this module's good intentions.
2. Every statement in this file is a ``SELECT``.
   ``evals/tests/test_no_write_statements.py`` scans this package's own source
   for write verbs, because "we only read" is a claim and a scan is a check.
3. The vector search is issued through
   ``provenance_db.repositories.evidence.ann_search`` -- the one sanctioned ANN
   entry point -- rather than through a second copy of the statement. A second
   copy is how the shipped query and the measured query drift, and then the
   number describes a query nothing in production runs.

Which SQL role
--------------
``pv_app_reader_writer``. ``pv_ops_reader`` is the natural choice for a
verifier and it cannot be used: migration ``0008`` grants it eleven operational
tables and the five agent views, and says in its own comment "Nothing else: no
``evidence_items``, no ``claims``, no ``belief_versions``". ``pv_agent_reader``
reaches only the views, whose base tables are denied by the database. So the
role that can see the evidence plane is the read-write one, and the read-only
session above is what makes the boundary hold anyway. This is recorded in the
report rather than left for a reader to infer.

Windows
-------
psycopg's async connection refuses the proactor event loop, which is the
default on Windows. :func:`run` supplies a selector loop. A caller who reaches
for a bare ``asyncio.run`` gets a connection failure that reads like a network
problem.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import selectors
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeVar

import psycopg

from evals.runner.hero import DECOY_KEY_PREFIXES, HERO_KEY_PREFIX
from provenance_contracts.identity import Principal

__all__ = [
    "CorpusCensus",
    "EvidenceRow",
    "HeroWorld",
    "ann_probe",
    "connect",
    "load_hero_world",
    "read_census",
    "run",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The role whose grants reach ``evidence_items``. See the module docstring.
EVAL_SQL_ROLE: Final[str] = "pv_app_reader_writer"

#: The live database. ``pv_migrator`` resolves to ``provenance_ci`` in this
#: ``.env`` while every other role resolves to ``provenance``, so the target is
#: named explicitly rather than inherited.
EVAL_DATABASE: Final[str] = "provenance"

T = TypeVar("T")


def _load_dotenv() -> None:
    """Populate ``os.environ`` from the gitignored ``.env``, printing nothing.

    ``Settings`` deliberately refuses to parse a dotenv (``settings.py:331``).
    This is a command run by hand, so loading here cannot surprise a test.
    Reuses ``scripts/mint_local_token._load_dotenv`` rather than restating it:
    two readers of one file is how the seed came to point at two databases.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.mint_local_token import _load_dotenv as load

    load(REPO_ROOT)


def run(coro: Awaitable[T]) -> T:
    """``asyncio.run`` on a selector loop, which is the only kind psycopg takes."""
    return asyncio.run(  # type: ignore[return-value,arg-type]
        coro,  # type: ignore[arg-type]
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


async def connect() -> psycopg.AsyncConnection[Any]:
    """A read-only connection as :data:`EVAL_SQL_ROLE` against the live database."""
    _load_dotenv()
    from scripts.seed.db import role_dsn

    dsn = str(role_dsn(EVAL_SQL_ROLE, database=EVAL_DATABASE))
    conn = await psycopg.AsyncConnection.connect(dsn)
    await conn.set_read_only(True)
    async with conn.cursor() as cur:
        await cur.execute("SELECT current_user, current_database()")
        row = await cur.fetchone()
    user, database = (str(row[0]), str(row[1])) if row else ("<unknown>", "<unknown>")
    if user != EVAL_SQL_ROLE or database != EVAL_DATABASE:
        await conn.close()
        raise RuntimeError(
            f"the eval connection authenticated as {user!r} on {database!r}; "
            f"expected {EVAL_SQL_ROLE!r} on {EVAL_DATABASE!r}. Measuring the "
            f"wrong database is how '26 tables checked, 26 match' was reported "
            f"against a database holding zero evidence rows."
        )
    return conn


async def _rows(conn: psycopg.AsyncConnection[Any], sql: str, params: Any = None) -> list[Any]:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def scalar(conn: psycopg.AsyncConnection[Any], sql: str, params: Any = None) -> Any:
    """The first column of the first row. ``None`` when there is no row."""
    rows = await _rows(conn, sql, params)
    return rows[0][0] if rows else None


# ---------------------------------------------------------------------------
# The census -- printed at the top of every report so a reader can tell which
# corpus the numbers below describe.
# ---------------------------------------------------------------------------

CENSUS_SQL: Final[str] = """
SELECT
  (SELECT count(*) FROM evidence_items),
  (SELECT count(*) FROM evidence_items WHERE retraction_status = 'ACTIVE'),
  (SELECT count(*) FROM evidence_items WHERE retraction_status <> 'ACTIVE'),
  (SELECT count(*) FROM evidence_items WHERE embedding IS NULL),
  (SELECT count(DISTINCT embedding_version) FROM evidence_items),
  (SELECT count(*) FROM source_artifacts WHERE s3_key LIKE %(hero)s),
  (SELECT count(*) FROM source_artifacts WHERE s3_key LIKE %(decoy)s)
"""


@dataclass(frozen=True)
class CorpusCensus:
    """What is actually in the corpus, counted at query time."""

    evidence_total: int
    evidence_active: int
    evidence_non_active: int
    evidence_without_embedding: int
    embedding_versions: int
    hero_artifacts: int
    decoy_artifacts: int
    embedding_model: str
    embedding_version: str


async def read_census(conn: psycopg.AsyncConnection[Any]) -> CorpusCensus:
    row = (
        await _rows(
            conn,
            CENSUS_SQL,
            {"hero": f"{HERO_KEY_PREFIX}%", "decoy": f"{DECOY_KEY_PREFIXES[0]}%"},
        )
    )[0]
    model = await scalar(
        conn,
        "SELECT embedding_model FROM evidence_items GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
    )
    version = await scalar(
        conn,
        "SELECT embedding_version FROM evidence_items GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
    )
    return CorpusCensus(
        evidence_total=int(row[0]),
        evidence_active=int(row[1]),
        evidence_non_active=int(row[2]),
        evidence_without_embedding=int(row[3]),
        embedding_versions=int(row[4]),
        hero_artifacts=int(row[5]),
        decoy_artifacts=int(row[6]),
        embedding_model=str(model),
        embedding_version=str(version),
    )


# ---------------------------------------------------------------------------
# The hero world: rows, their provenance, and their case membership.
# ---------------------------------------------------------------------------

#: Hero evidence, selected by joining through ``source_artifacts``. There is no
#: text predicate anywhere in this statement and there must never be one --
#: every decoy in this corpus was generated to read like hero content.
HERO_EVIDENCE_SQL: Final[str] = f"""
SELECT e.id, e.tenant_id, e.user_id, e.artifact_id, a.s3_key, e.evidence_type,
       e.retraction_status, e.is_retrieval_eligible, e.normalized_text,
       e.embedding::text
FROM evidence_items e
JOIN source_artifacts a ON a.id = e.artifact_id
WHERE a.s3_key LIKE '{HERO_KEY_PREFIX}%'
ORDER BY a.s3_key, e.id
"""

#: Case membership, through ``claims``. ``evidence_items`` carries no
#: ``case_id`` -- the level that binds an observation to a case is the claim,
#: which is the whole point of the six-way separation.
CASE_MEMBERSHIP_SQL: Final[str] = """
SELECT c.evidence_id, c.case_id
FROM claims c
WHERE c.evidence_id IS NOT NULL
"""

HERO_ARTIFACT_IDS_SQL: Final[str] = f"""
SELECT id, s3_key FROM source_artifacts WHERE s3_key LIKE '{HERO_KEY_PREFIX}%'
"""


@dataclass(frozen=True)
class EvidenceRow:
    """One hero evidence row, with the provenance that identified it."""

    evidence_id: uuid.UUID
    artifact_id: uuid.UUID
    s3_key: str
    evidence_type: str
    retraction_status: str
    is_retrieval_eligible: bool
    normalized_text: str
    embedding: tuple[float, ...]

    @property
    def artifact_name(self) -> str:
        return self.s3_key.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class HeroWorld:
    """The hero's rows, their case membership, and a principal to query as."""

    principal: Principal
    rows: tuple[EvidenceRow, ...]
    cases_by_evidence: dict[uuid.UUID, frozenset[uuid.UUID]]
    hero_artifact_ids: frozenset[uuid.UUID]

    @property
    def active_rows(self) -> tuple[EvidenceRow, ...]:
        return tuple(row for row in self.rows if row.retraction_status == "ACTIVE")

    @property
    def non_active_rows(self) -> tuple[EvidenceRow, ...]:
        return tuple(row for row in self.rows if row.retraction_status != "ACTIVE")

    def gold_for(self, row: EvidenceRow) -> frozenset[uuid.UUID]:
        """Other ACTIVE hero rows sharing at least one case with *row*.

        The gold set is defined by the record, not by a hand-written list: two
        evidence items belong together when the Kernel-written claims over them
        point at the same case. A hand-written list would encode this author's
        reading of the corpus; the join encodes the corpus.
        """
        mine = self.cases_by_evidence.get(row.evidence_id, frozenset())
        if not mine:
            return frozenset()
        return frozenset(
            other.evidence_id
            for other in self.active_rows
            if other.evidence_id != row.evidence_id
            and self.cases_by_evidence.get(other.evidence_id, frozenset()) & mine
        )


def _parse_vector(text: str) -> tuple[float, ...]:
    return tuple(float(part) for part in text.strip()[1:-1].split(","))


async def load_hero_world(conn: psycopg.AsyncConnection[Any]) -> HeroWorld:
    rows = [
        EvidenceRow(
            evidence_id=record[0],
            artifact_id=record[3],
            s3_key=str(record[4]),
            evidence_type=str(record[5]),
            retraction_status=str(record[6]),
            is_retrieval_eligible=bool(record[7]),
            normalized_text=str(record[8]),
            embedding=_parse_vector(str(record[9])),
        )
        for record in await _rows(conn, HERO_EVIDENCE_SQL)
    ]
    if not rows:
        raise RuntimeError(
            f"no evidence joined to an artifact under {HERO_KEY_PREFIX!r}. Either "
            f"the corpus is unseeded or the prefix moved; either way the numbers "
            f"below would describe nothing."
        )

    membership: dict[uuid.UUID, set[uuid.UUID]] = {}
    for evidence_id, case_id in await _rows(conn, CASE_MEMBERSHIP_SQL):
        membership.setdefault(evidence_id, set()).add(case_id)

    artifact_ids = frozenset(record[0] for record in await _rows(conn, HERO_ARTIFACT_IDS_SQL))

    first = rows[0]
    tenant_id, user_id = await _owner_of(conn, first.evidence_id)
    now = dt.datetime.now(dt.UTC)
    principal = Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        cognito_sub=os.environ.get("PV_EVAL_SUBJECT", "seed-hero-alex-rivera"),
        token_issued_at=now,
        token_expires_at=now + dt.timedelta(hours=1),
        request_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )
    return HeroWorld(
        principal=principal,
        rows=tuple(rows),
        cases_by_evidence={key: frozenset(value) for key, value in membership.items()},
        hero_artifact_ids=artifact_ids,
    )


async def _owner_of(
    conn: psycopg.AsyncConnection[Any], evidence_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    row = (
        await _rows(
            conn,
            "SELECT tenant_id, user_id FROM evidence_items WHERE id = %s",
            (evidence_id,),
        )
    )[0]
    return row[0], row[1]


# ---------------------------------------------------------------------------
# The probe.
# ---------------------------------------------------------------------------


async def ann_probe(
    conn: psycopg.AsyncConnection[Any],
    principal: Principal,
    vector: Sequence[float],
    *,
    k: int,
    embedding_version: str,
) -> list[dict[str, Any]]:
    """One vector search through the single sanctioned entry point.

    The vector is a **corpus vector**, handed in by the caller. Nothing here
    embeds text: there is no Titan credential on this machine, and a vector
    from a different model would land in a different space -- producing
    distances that stay ordered and stop meaning anything.
    """
    from provenance_db.repositories.evidence import ann_search

    return await ann_search(
        conn,
        principal,
        list(vector),
        limit=k,
        embedding_version=embedding_version,
    )


#: Injected by the tests so a suite can be exercised without a cluster.
ConnectionFactory = Callable[[], Awaitable[psycopg.AsyncConnection[Any]]]
