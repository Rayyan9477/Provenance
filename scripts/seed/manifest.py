"""``db/seeds/MANIFEST.json`` -- the committed row-count contract (``T2.8``).

Authority
---------
- ``docs/quality/23_PHASE_GATES.md`` section 4.1: "A committed
  ``db/seeds/MANIFEST.json`` recording expected row counts per table", and
  ``G2.6``: ``python -m tools.manifest_check db/seeds/MANIFEST.json`` ->
  ``26 tables checked, 26 match``.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 24 risk 11 -- under
  ``--profile schema-only`` the manifest "must therefore encode expected
  zero-counts for the canonical tables".
- ``docs/quality/23_PHASE_GATES.md`` line 1559 -- "Seed dates are stored as
  **offsets from a seed epoch** recorded in ``db/seeds/MANIFEST.json``".

Ownership
---------
``T2.8`` owns this file; ``tools/manifest_check.py`` is written by another task
and only reads it. The shape below is therefore a published contract, and the
two fields the checker needs -- ``tables`` and nothing else -- are kept at the
top level and flat. Everything the checker does *not* need (``demo_anchor``,
``rng_seed``, ``corpus``, ``deferred``) is additive and cannot break it.

The ``deferred`` block is the point
-----------------------------------
Eight canonical tables are expected to hold **zero** rows while step 9 waits for
Phase 4. A bare ``0`` in ``tables`` would be indistinguishable from a loader
that silently failed, and ``26 tables checked, 26 match`` would then be a green
light over an unbuilt half of the seed. ``deferred`` names those tables and the
reason, so the zero is a claim someone made rather than an absence someone
missed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

__all__ = [
    "MANIFEST_PATH",
    "ManifestComparison",
    "build_manifest",
    "compare",
    "scoped_count_sql",
    "seed_tenant_ids",
    "write_manifest",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "db" / "seeds" / "MANIFEST.json"
EXPECTED_TABLES_PATH = REPO_ROOT / "db" / "expected_tables.txt"

#: The eight tables ``T2.8`` step 9 would populate, and does not yet.
DEFERRED_TABLES: tuple[str, ...] = (
    "claims",
    "beliefs",
    "belief_versions",
    "belief_support",
    "conflicts",
    "commitments",
    "fulfillments",
    "prospective_triggers",
    "state_transitions",
    "memory_proposals",
    "kernel_decisions",
    "outbox_events",
)

DEFERRED_REASON = (
    "step 9 (replay curated MemoryProposal fixtures through MemoryKernel.commit() as "
    "pv_kernel_writer) depends on Phase 4. Until the Kernel exists the seed runs with "
    "--profile schema-only and these tables are legitimately empty. Seeding them by raw "
    "INSERT would create a second canonical writer and is forbidden "
    "(70_TASK_PLAN.md T2.8 step 9)."
)


def canonical_tables() -> list[str]:
    """The 26 names, read from ``db/expected_tables.txt`` rather than restated."""
    return EXPECTED_TABLES_PATH.read_text(encoding="utf-8").split()


def build_manifest() -> dict[str, Any]:
    """The expected state of a completed ``--profile all`` run."""
    from scripts.seed.cases import CASES, CONTEXTS
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS
    from scripts.seed.decoys import DECOY_PLAN, NEAR_MISS_QUOTA, RNG_SEED, corpus_fingerprint
    from scripts.seed.embedding_text import (
        EMBEDDING_MODEL_ID,
        EMBEDDING_TEMPLATE_VERSION,
        EMBEDDING_VERSION,
    )

    # The cache is a build artifact, not a source file, so an absent one is a
    # legitimate state (a clone that has not seeded yet) and is recorded as
    # null rather than treated as an error.
    from scripts.seed.embeddings import CACHE_PATH, VectorCache
    from scripts.seed.evidence import CURATED_EVIDENCE
    from scripts.seed.ids import DEMO_ANCHOR, PROVENANCE_SEED_NS
    from scripts.seed.retractions import RETRACTION_FIXTURES
    from scripts.seed.tenants import HERO_USER, TENANTS, USERS
    from scripts.seed.world import curated_artifacts

    if CACHE_PATH.is_file():
        cache = VectorCache().load()
        cache_vectors: int | None = len(cache)
        cache_sha: str | None = cache.content_sha256()
    else:
        cache_vectors = None
        cache_sha = None

    decoy_total = sum(DECOY_PLAN.values())
    evidence_total = decoy_total + len(CURATED_EVIDENCE) + len(RETRACTION_FIXTURES)
    hero_scoped = DECOY_PLAN["hero"] + len(CURATED_EVIDENCE) + len(RETRACTION_FIXTURES)
    artifact_total = decoy_total + len(curated_artifacts())

    tables = dict.fromkeys(canonical_tables(), 0)
    tables.update(
        {
            "tenants": len(TENANTS),
            "users": len(USERS),
            "counterparties": len(COUNTERPARTIES),
            "relationships": len(RELATIONSHIPS),
            "contexts": len(CONTEXTS),
            "cases": len(CASES),
            "source_artifacts": artifact_total,
            "evidence_items": evidence_total,
        }
    )

    return {
        "manifest_version": "seed/1.0.0",
        "profile": "all",
        "seed_namespace": str(PROVENANCE_SEED_NS),
        "demo_anchor": DEMO_ANCHOR.isoformat(),
        "rng_seed": RNG_SEED,
        "embedding": {
            "model_id": EMBEDDING_MODEL_ID,
            "embedding_version": EMBEDDING_VERSION,
            "template_version": EMBEDDING_TEMPLATE_VERSION,
            "dimensions": 1024,
            "normalize": True,
            "cache": "db/seeds/vectors.parquet",
            "cache_vectors": cache_vectors,
            "cache_content_sha256": cache_sha,
        },
        "corpus": {
            "total": evidence_total,
            "user_scoped_hero": hero_scoped,
            "hero_user_id": str(HERO_USER.id),
            "decoys": dict(DECOY_PLAN),
            "curated": len(CURATED_EVIDENCE),
            "retraction_fixtures": len(RETRACTION_FIXTURES),
            "near_miss_quota": NEAR_MISS_QUOTA,
            "decoy_corpus_sha256": corpus_fingerprint(),
        },
        "deferred": {
            "step": 9,
            "reason": DEFERRED_REASON,
            "tables": list(DEFERRED_TABLES),
        },
        "tables": tables,
    }


def write_manifest(path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(), indent=2, sort_keys=False) + "\n", "utf-8")
    return path


@dataclass
class ManifestComparison:
    checked: int
    matched: int
    mismatches: list[str]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def summary_line(self) -> str:
        """The exact line ``G2.6`` expects: ``26 tables checked, 26 match``."""
        return f"{self.checked} tables checked, {self.matched} match"


def seed_tenant_ids() -> list[UUID]:
    """The three tenants this seed owns."""
    from scripts.seed.tenants import TENANTS

    return [t.id for t in TENANTS]


def scoped_count_sql(table: str) -> str:
    """``count(*)`` for *table*, restricted to the three seeded tenants.

    Every canonical table except ``tenants`` carries ``tenant_id``, so the
    seed's own footprint is one predicate.

    This exists because ``provenance_ci`` is shared. Other phases' database
    tests create their own fixture tenants, users, cases and belief versions in
    it, and an unscoped ``count(*)`` measures their work as well as this seed's
    -- which turns "the seed is idempotent" into "no other agent wrote anything
    in the last minute", a claim about scheduling rather than about the loader.
    The unscoped count remains the right assertion for ``G2.6`` against a
    database holding nothing but the seed (the demo database, or a freshly reset
    CI one), so it stays the default and this is opt-in.
    """
    column = "id" if table == "tenants" else "tenant_id"
    return f"SELECT count(*) FROM {table} WHERE {column} = ANY(%s)"


def compare(dsn: str, path: Path = MANIFEST_PATH, *, scoped: bool = False) -> ManifestComparison:
    """Compare the manifest's expected counts against the live database."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected: dict[str, int] = manifest["tables"]
    mismatches: list[str] = []
    matched = 0
    tenants = seed_tenant_ids()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table, count in sorted(expected.items()):
            # The table names come from the manifest, which this task owns.
            if scoped:
                cur.execute(scoped_count_sql(table), (tenants,))
            else:
                cur.execute(f"SELECT count(*) FROM {table}")
            row = cur.fetchone()
            actual = int(row[0]) if row else -1
            if actual == count:
                matched += 1
            else:
                mismatches.append(f"{table}: manifest {count}, actual {actual}")
    return ManifestComparison(checked=len(expected), matched=matched, mismatches=mismatches)
