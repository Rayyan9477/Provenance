"""``python -m scripts.seed`` -- the deterministic seed pipeline (``T2.8``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.9 -- the CLI surface.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5 ``T2.8`` sub-tasks 1-11.

Usage
-----
::

    python -m scripts.seed --profile all --reset      # full rebuild
    python -m scripts.seed --profile hero             # curated rows only
    python -m scripts.seed --profile isolation        # the two decoy tenants
    python -m scripts.seed --profile all --perturb    # make seed-perturb
    python -m scripts.seed --profile all --restore    # undo it, row for row
    python -m scripts.seed --verify                   # section 18 queries only
    python -m scripts.seed --row-counts               # the idempotence diff input
    python -m scripts.seed --outstanding-total        # USD 2,020.00, and where it comes from
    python -m scripts.seed --write-manifest           # regenerate MANIFEST.json
    python -m scripts.seed --recreate-database provenance   # make demo-reset, half one
    python -m scripts.seed --check-manifest           # 26 tables checked, 26 match

Step 1, the guard
-----------------
The seed refuses to run unless ``APP_ENV`` is ``local`` or ``demo``, and
``--reset`` refuses anywhere else full stop. This is the cheapest available
protection against the one irreversible mistake in this repository -- truncating
a populated database -- and it is checked before a connection is opened, so a
misconfigured run costs nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.seed import db as dbmod
from scripts.seed import manifest as manifestmod
from scripts.seed.artifacts import materialize
from scripts.seed.evidence import ARTIFACT_SOURCES, JUNE_INVOICE
from scripts.seed.loader import run_seed
from scripts.seed.retractions import RETRACTION_SOURCES
from scripts.seed.verify import OUTSTANDING_TOTAL_SQL, outstanding_total, run_verification

ALLOWED_APP_ENVS = frozenset({"local", "demo"})

#: Tables the idempotence diff compares. Deliberately the whole canonical set,
#: not the three ``G2.6`` prints: a loader that duplicates ``relationships``
#: while leaving ``evidence_items`` alone would pass the three-table version.
ROW_COUNT_TABLES: tuple[str, ...] = tuple(sorted(manifestmod.canonical_tables()))


def _app_env() -> str:
    value = os.environ.get("APP_ENV")
    if value:
        return value.strip()
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.is_file():
        for raw in dotenv.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("APP_ENV="):
                return raw.split("=", 1)[1].strip()
    return ""


def _guard(reset: bool) -> None:
    env = _app_env()
    if env not in ALLOWED_APP_ENVS:
        raise SystemExit(
            f"refusing to seed: APP_ENV is {env!r}, and the seed only runs when APP_ENV "
            f"is one of {sorted(ALLOWED_APP_ENVS)}. This guard exists because --reset "
            f"truncates every canonical table."
        )
    if reset and env not in ALLOWED_APP_ENVS:  # pragma: no cover - unreachable, kept explicit
        raise SystemExit("refusing to --reset outside a local or demo environment")


def _row_counts(dsn: str, *, scoped: bool = False) -> str:
    """``table,count`` for all 26 canonical tables, sorted.

    ``--scoped`` restricts every count to the three seeded tenants. On a
    database that holds nothing but the seed the two are identical; on the
    shared ``provenance_ci`` they are not, because other phases' database tests
    create their own fixture tenants and cases there. The idempotence assertion
    is about *this seed's* footprint, so the scoped form is the one that
    measures what it claims to measure.
    """
    import psycopg

    from scripts.seed.manifest import scoped_count_sql, seed_tenant_ids

    tenants = seed_tenant_ids()
    lines: list[str] = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table in ROW_COUNT_TABLES:
            # The table names come from db/expected_tables.txt, not from user input.
            if scoped:
                cur.execute(scoped_count_sql(table), (tenants,))
            else:
                cur.execute(f"SELECT count(*) FROM {table}")
            row = cur.fetchone()
            lines.append(f"{table},{int(row[0]) if row else -1}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.seed", description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("all", "hero", "isolation", "schema-only"),
        default="all",
        help=(
            "all: everything, including the step-9 kernel replay; "
            "hero: curated rows, hero decoys, and the replay; "
            "isolation: the two decoy tenants, no replay; "
            "schema-only: everything EXCEPT the replay -- the twelve "
            "Kernel-written tables stay empty"
        ),
    )
    parser.add_argument("--reset", action="store_true", help="truncate in reverse FK order first")
    parser.add_argument(
        "--perturb",
        action="store_true",
        help="reseed with the outcome-bearing rows removed or shifted (make seed-perturb)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="undo --perturb exactly, row for row (no reload, no index rebuild)",
    )
    parser.add_argument(
        "--embeddings",
        choices=("live", "cache-only"),
        default="live",
        help="cache-only refuses to call Bedrock and fails loudly on a cache miss",
    )
    parser.add_argument("--database", default=None, help="override the database name in every DSN")
    parser.add_argument("--verify", action="store_true", help="run the section 18 queries and exit")
    parser.add_argument("--row-counts", action="store_true", help="print per-table counts and exit")
    parser.add_argument(
        "--scoped",
        action="store_true",
        help="restrict --row-counts / --check-manifest to the three seeded tenants",
    )
    parser.add_argument("--write-manifest", action="store_true", help="regenerate MANIFEST.json")
    parser.add_argument("--check-manifest", action="store_true", help="compare manifest to the db")
    parser.add_argument(
        "--outstanding-total",
        action="store_true",
        help="print the seeded outstanding total and its per-counterparty breakdown",
    )
    parser.add_argument(
        "--recreate-database",
        metavar="NAME",
        default=None,
        help=(
            "DROP and CREATE the named database, then exit (make demo-reset). "
            "Requires PROVENANCE_CONFIRM_DESTRUCTIVE=yes."
        ),
    )
    parser.add_argument(
        "--materialize-artifacts",
        action="store_true",
        help="write demo/artifacts/ and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Every SQL role must name the same database before anything is written.
    # Without this the seed splits itself: ROLE_DSN_ENV prefers PV_DB_MIGRATOR_CI
    # for the migrator and COCKROACH_DATABASE_URL for the app role, so a .env
    # holding both sends the small planes and the index to one database and the
    # bulk load to another -- and manifest_check, which prefers a third key,
    # validated whichever it resolved. That combination printed
    # "26 tables checked, 26 match" over a database with zero evidence rows.
    target = dbmod.assert_roles_agree_on_database(database=args.database)
    if not args.materialize_artifacts and not args.write_manifest:
        print(f"target database: {target}")

    if args.materialize_artifacts:
        written = materialize(ARTIFACT_SOURCES + RETRACTION_SOURCES + (JUNE_INVOICE,))
        print(f"wrote {len(written)} artifacts to demo/artifacts/")
        return 0

    if args.recreate_database:
        _guard(reset=True)
        dbmod.recreate_database(args.recreate_database)
        print(f"dropped and recreated {args.recreate_database}; now run alembic upgrade head")
        return 0

    if args.write_manifest:
        path = manifestmod.write_manifest()
        print(f"wrote {path.relative_to(manifestmod.REPO_ROOT)}")
        return 0

    dsn = dbmod.role_dsn("pv_migrator", database=args.database)

    if args.row_counts:
        print(_row_counts(dsn, scoped=args.scoped))
        return 0

    if args.check_manifest:
        comparison = manifestmod.compare(dsn, scoped=args.scoped)
        print(comparison.summary_line())
        for mismatch in comparison.mismatches:
            print(f"  MISMATCH {mismatch}", file=sys.stderr)
        return 0 if comparison.ok else 1

    if args.outstanding_total:
        from scripts.seed.obligations import COMMITMENTS
        from scripts.seed.obligations import outstanding_total as fixture_total

        print("SQL:" + OUTSTANDING_TOTAL_SQL)
        total, rows, breakdown = outstanding_total(dsn)
        print(f"database: USD {total} over {rows} commitment rows")
        for name, kind, status, amount in breakdown:
            print(f"  {name}: {kind} {status} outstanding={amount}")
        print(f"fixtures: USD {fixture_total()} over {len(COMMITMENTS)} commitment fixtures")
        for commitment in COMMITMENTS:
            print(
                f"  {commitment.slug}: {commitment.commitment_type} {commitment.status} "
                f"outstanding={commitment.outstanding_amount}"
            )
        if rows == 0:
            print(
                "NOTE: commitments is empty because T2.8 step 9 is deferred to Phase 4. "
                "The seed does not raw-INSERT canonical rows; the Kernel writes them."
            )
        return 0

    if args.verify:
        report = run_verification(dsn)
        print(report.summary_line())
        for failure in report.failures:
            print(f"  FAIL {failure}", file=sys.stderr)
        return 0 if report.ok else 1

    if args.perturb and args.restore:
        raise SystemExit("--perturb and --restore are opposites; pass one")

    _guard(args.reset)

    # The artifact bytes are part of the seed, not a side quest: the curated
    # rows carry hashes of these exact files and `demo/artifacts/` is the one
    # sanctioned location for them.
    materialize(ARTIFACT_SOURCES + RETRACTION_SOURCES + (JUNE_INVOICE,))

    print(
        f"seeding profile={args.profile} reset={args.reset} "
        f"perturb={args.perturb} restore={args.restore}",
        flush=True,
    )
    result = run_seed(
        profile=args.profile,
        reset=args.reset,
        perturb=args.perturb,
        restore=args.restore,
        embeddings_mode=args.embeddings,
        database=args.database,
    )

    print("  [11] verification queries")
    verification = run_verification(dsn)
    print(f"       {verification.summary_line()}")
    for failure in verification.failures:
        print(f"       FAIL {failure}", file=sys.stderr)

    print(result.line())
    for table, count in sorted(result.rows.items()):
        print(f"  {table}: {count} rows offered")

    return 0 if verification.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
