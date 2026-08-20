#!/usr/bin/env python
"""Compare ``db/seeds/MANIFEST.json`` against the live row counts — T2.7.

Authority
---------
- ``quality/23_PHASE_GATES.md`` ``G2.6``::

      python -m tools.manifest_check db/seeds/MANIFEST.json
      #   -> "26 tables checked, 26 match"

- ``EXECUTION/70_TASK_PLAN.md`` ``T2.7`` sub-task: "Write ``tools/manifest_check.py``
  to read ``db/seeds/MANIFEST.json`` and compare expected against actual row
  counts per table, printing ``26 tables checked, 26 match``."
- ``specs/10_DATABASE_DDL.md`` section 20 risk 2: "The canonical table count is
  26 ... The migration chain, expected-table manifest, gates, and submission
  material must all assert 26."
- ``EXECUTION/70_TASK_PLAN.md`` ``T2.8`` — the task that writes the manifest.

Where the 26 comes from
-----------------------
``db/expected_tables.txt``, never the manifest's own key count. A manifest
listing four tables, all four matching, must not be able to print
``26 tables checked, 26 match``; the sentence a gate reviewer reads as proof has
to be anchored to the canonical set, not to whatever the seed felt like
declaring. A table in the manifest that is not canonical is an error for the
same reason, in the other direction.

The manifest does not exist yet
-------------------------------
``T2.8`` writes it. Until then this tool **fails**, naming the file, the task
that owns it and the shape it expects. It does not treat an absent manifest as
"nothing to check": that would turn ``G2.6`` into a green line about a file
nobody has written.

Accepted manifest shapes
------------------------
Preferred::

    {"seed_profile": "all", "row_counts": {"tenants": 3, "evidence_items": 18035, ...}}

Also accepted, so a manifest carrying a digest beside each count still works::

    {"row_counts": {"evidence_items": {"rows": 18035, "sha256": "..."}}}

and a flat ``{"tenants": 3, ...}`` mapping of table to integer.

Credentials
-----------
The DSN is read from ``--dsn``, then ``PROVENANCE_TEST_DB_URL``, then
``COCKROACH_DATABASE_URL``, then the gitignored ``.env``. It is never printed:
:func:`scrub` redacts any database URL out of an error before it reaches stdout,
stderr or a committed gate log (``quality/23_PHASE_GATES.md`` section 2.2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The authority for "26". Relative to the repository root.
CANONICAL_TABLES_FILE = "db/expected_tables.txt"

#: Where ``T2.8`` writes the manifest, quoted in the absent-file error.
DEFAULT_MANIFEST = "db/seeds/MANIFEST.json"

#: Keys under which a manifest may nest its table -> count mapping.
MAPPING_KEYS: tuple[str, ...] = ("row_counts", "tables", "counts")

#: Keys under which a per-table object may carry its count.
COUNT_KEYS: tuple[str, ...] = ("rows", "count", "row_count")

#: ``.env`` keys tried in order when no DSN is passed on the command line.
DSN_ENV_KEYS: tuple[str, ...] = ("PROVENANCE_TEST_DB_URL", "COCKROACH_DATABASE_URL")

REPO_ROOT = Path(__file__).resolve().parents[1]

_URL_PATTERN = re.compile(r"(?i)\b(postgres(?:ql)?://)[^\s'\"<>]+")

#: A counter maps a list of table names to their live row counts.
RowCounter = Callable[[Sequence[str]], Mapping[str, int]]


class ManifestError(Exception):
    """The manifest is absent, unreadable, or not shaped like a manifest."""


def scrub(text: str) -> str:
    """Redact any database URL out of *text*."""
    return _URL_PATTERN.sub(r"\1<redacted>", text)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def canonical_tables(path: Path | None = None) -> tuple[str, ...]:
    """The canonical table names, sorted, from ``db/expected_tables.txt``."""
    source = path if path is not None else REPO_ROOT / CANONICAL_TABLES_FILE
    if not source.is_file():
        raise ManifestError(
            f"{source} does not exist. It is written by hand in T2.6 and is the "
            "authority for the canonical table count (10_DATABASE_DDL.md section 20)."
        )
    names = [line.strip() for line in source.read_text(encoding="utf-8").splitlines()]
    return tuple(sorted(name for name in names if name))


def _as_count(table: str, value: object) -> int:
    if isinstance(value, bool):  # bool is an int subclass; a count is never a bool
        raise ManifestError(f"{table}: expected an integer row count, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        for key in COUNT_KEYS:
            if key in value:
                return _as_count(table, value[key])
        raise ManifestError(
            f"{table}: object value carries no row count; expected one of {COUNT_KEYS}"
        )
    raise ManifestError(f"{table}: expected an integer row count, got {value!r}")


def load_manifest(path: Path) -> dict[str, int]:
    """Read *path* and return its table -> expected row count mapping."""
    if not path.is_file():
        raise ManifestError(
            f"{path} does not exist.\n"
            f"  {DEFAULT_MANIFEST} is T2.8's deliverable (EXECUTION/70_TASK_PLAN.md, "
            "the seed pipeline). This check does not pass without it.\n"
            "  Expected shape:\n"
            '    {"seed_profile": "all",\n'
            '     "row_counts": {"tenants": 3, "evidence_items": 18035, ...}}\n'
            f"  with one entry for every table in {CANONICAL_TABLES_FILE} (26 of them)."
        )
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"{path} must contain a JSON object, not {type(payload).__name__}")

    mapping: Any = None
    for key in MAPPING_KEYS:
        if key in payload:
            mapping = payload[key]
            break
    if mapping is None:
        mapping = {k: v for k, v in payload.items() if not isinstance(v, str)}
    if not isinstance(mapping, dict) or not mapping:
        raise ManifestError(
            f"{path} carries no table -> row count mapping; expected a "
            f"{MAPPING_KEYS[0]!r} object"
        )
    return {str(table): _as_count(str(table), value) for table, value in mapping.items()}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mismatch:
    """One table whose manifest count and live count disagree."""

    table: str
    expected: int
    actual: int

    def line(self) -> str:
        delta = self.actual - self.expected
        return f"  {self.table}: manifest {self.expected}, database {self.actual} " f"({delta:+d})"


@dataclass(frozen=True)
class Report:
    """The outcome of one comparison."""

    checked: int
    matched: int
    mismatches: tuple[Mismatch, ...]
    missing: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.missing and not self.unknown

    @property
    def summary_line(self) -> str:
        return f"{self.checked} tables checked, {self.matched} match"


def compare(
    *,
    expected: Mapping[str, int],
    actual: Mapping[str, int],
    canonical: Sequence[str],
) -> Report:
    """Compare manifest counts against live counts over the canonical table set.

    ``checked`` is always ``len(canonical)``. A table the manifest omits counts
    as unmatched, so a partial manifest cannot report a full pass.
    """
    missing = tuple(table for table in canonical if table not in expected)
    unknown = tuple(sorted(table for table in expected if table not in set(canonical)))
    mismatches = tuple(
        Mismatch(table=table, expected=expected[table], actual=actual.get(table, -1))
        for table in canonical
        if table in expected and expected[table] != actual.get(table, -1)
    )
    matched = sum(
        1 for table in canonical if table in expected and expected[table] == actual.get(table, -1)
    )
    return Report(
        checked=len(canonical),
        matched=matched,
        mismatches=mismatches,
        missing=missing,
        unknown=unknown,
    )


# ---------------------------------------------------------------------------
# The live counts
# ---------------------------------------------------------------------------


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


def resolve_dsn(explicit: str | None = None) -> str:
    """Find a DSN without ever returning it to an output stream."""
    if explicit:
        return explicit
    for key in DSN_ENV_KEYS:
        value = os.environ.get(key) or _dotenv_value(key)
        if value:
            return value
    raise ManifestError(
        "no database URL: pass --dsn, or set one of " + ", ".join(DSN_ENV_KEYS) + " in .env"
    )


def psycopg_counter(dsn: str) -> RowCounter:
    """A :data:`RowCounter` backed by one connection to the live cluster."""

    def _count(tables: Sequence[str]) -> Mapping[str, int]:
        import psycopg
        from psycopg import sql

        counts: dict[str, int] = {}
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)),
                )
                row = cur.fetchone()
                counts[table] = int(row[0]) if row is not None else -1
        return counts

    return _count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.manifest_check",
        description="Compare a seed manifest against the live row counts (G2.6).",
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        default=DEFAULT_MANIFEST,
        help=f"path to the seed manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="database URL; otherwise " + " then ".join(DSN_ENV_KEYS) + ", then .env",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, counter: RowCounter | None = None) -> int:
    """Entry point. ``0`` every table matches, ``1`` it does not, ``2`` bad input."""
    args = _parser().parse_args(list(argv) if argv is not None else sys.argv[1:])

    try:
        canonical = canonical_tables()
        expected = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2

    preflight = compare(expected=expected, actual=dict.fromkeys(canonical, -1), canonical=canonical)
    if preflight.missing or preflight.unknown:
        if preflight.missing:
            print(
                f"manifest covers {len(canonical) - len(preflight.missing)} of "
                f"{len(canonical)} canonical tables; absent: " + ", ".join(preflight.missing),
                file=sys.stderr,
            )
        if preflight.unknown:
            print(
                "manifest names tables that are not in "
                f"{CANONICAL_TABLES_FILE}: " + ", ".join(preflight.unknown),
                file=sys.stderr,
            )
        print(
            "refusing to report a table count this manifest does not cover.",
            file=sys.stderr,
        )
        return 1

    try:
        counts = (
            counter(canonical)
            if counter is not None
            else psycopg_counter(resolve_dsn(args.dsn))(canonical)
        )
    except ManifestError as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2
    except Exception as exc:  # any driver error is scrubbed and reported, never raised
        print(f"could not count rows: {scrub(str(exc))}", file=sys.stderr)
        return 2

    report = compare(expected=expected, actual=counts, canonical=canonical)
    for mismatch in report.mismatches:
        print(mismatch.line())
    print(report.summary_line)
    if not report.ok:
        print(
            f"{len(report.mismatches)} table(s) do not match the manifest. A seed that "
            "does not reproduce its own manifest is not deterministic (G2.6).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through `python -m`
    raise SystemExit(main())
