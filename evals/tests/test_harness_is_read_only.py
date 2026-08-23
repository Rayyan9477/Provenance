"""The harness must not write, and "we only read" is a claim, not a check.

`tools/write_path_lint` does not walk `evals/` -- its roots are `services`,
`packages`, `workers`, `agents` and `apps/web` -- so nothing else in the
repository would notice a write verb appearing here. This is that guard.

The corpus these suites measure is the one `db/seeds/MANIFEST.json` asserts
exact row counts over, and other lanes are verifying against it. A harness that
wrote a row to make a measurement possible would invalidate every other lane's
verification and its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALS = REPO_ROOT / "evals"

#: SQL that changes rows. `MERGE` and `UPSERT` are here because CockroachDB
#: accepts both and neither contains the word INSERT.
WRITE_VERBS = (
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "TRUNCATE",
    "UPSERT INTO",
    "MERGE INTO",
    "CREATE TABLE",
    "DROP TABLE",
    "ALTER TABLE",
)

#: Lines that legitimately name a write verb while issuing none: prose about
#: what the harness refuses to do.
_COMMENTARY = re.compile(r"^\s*(#|\*|--)|^\s*[\"']{3}")


def _python_sources() -> list[Path]:
    return [
        path
        for path in sorted(EVALS.rglob("*.py"))
        if "tests" not in path.parts and "reports" not in path.parts
    ]


def test_there_are_python_sources_to_scan() -> None:
    """A scan of nothing passes. Assert the scan has a subject."""
    sources = _python_sources()
    assert len(sources) >= 8, f"only {len(sources)} modules scanned; the guard is vacuous"


def test_no_eval_module_issues_a_statement_that_changes_a_row() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _COMMENTARY.match(line):
                continue
            upper = line.upper()
            for verb in WRITE_VERBS:
                if verb in upper:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert offenders == [], (
        "the eval harness contains a write statement. It measures the corpus "
        "db/seeds/MANIFEST.json asserts exact row counts over, while other "
        f"lanes verify against it. Offenders: {offenders}"
    )


def test_the_connection_helper_sets_the_session_read_only_before_it_returns() -> None:
    source = (EVALS / "runner" / "corpus.py").read_text(encoding="utf-8")
    assert "set_read_only(True)" in source, (
        "the eval connection is not put into a READ ONLY session. The database "
        "refusing a write is a boundary; this module's good intentions are not."
    )


def test_the_runner_names_the_database_rather_than_inheriting_it() -> None:
    source = (EVALS / "runner" / "corpus.py").read_text(encoding="utf-8")
    assert 'EVAL_DATABASE: Final[str] = "provenance"' in source, (
        "the eval database is not pinned. pv_migrator resolves to provenance_ci "
        "in this .env while every other role resolves to provenance, and a seed "
        "that split across the two once reported '26 tables checked, 26 match' "
        "against a database holding zero evidence rows."
    )
