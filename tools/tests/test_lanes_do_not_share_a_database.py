"""The retrieval lane and the migration lane must not share a database.

What happened
-------------
Both lanes pinned ``provenance_ci``. They have **incompatible** requirements on
it:

* the retrieval lane needs it **seeded** -- 18,035 evidence rows, 3 retraction
  fixtures, an ANN index. Roughly an hour to build;
* the ``db`` lane's migration drill (``test_migrations.py``) downgrades to base
  and re-upgrades, because that is how it proves the chain is reversible. It
  therefore **destroys** whatever the database held.

Measured: an hour-long seed of ``provenance_ci`` produced 6 passing retraction
tests and moved ``make sabotage`` to 13/13 caught. A subsequent ``pytest -m db``
run left ``evidence_items`` at **0 rows**, and both went straight back --
retrieval to skipping, sabotage to 12-caught-1-cannot-run.

Neither lane is wrong. The retrieval lane is right to refuse any database but
its own, so a stray write cannot reach the demo corpus. The migration drill is
right to rebuild from base, because a chain that has never been run from base
has never been tested. They simply cannot both own one database.

Why this is a test and not a comment
-------------------------------------
The failure is silent and delayed: seeding succeeds, the retrieval lane passes,
and the corpus disappears the next time somebody runs an unrelated lane. Nothing
errors. The only symptom is a suite that was green becoming skipped, which reads
like a configuration problem rather than a deliberate wipe.

So the constraint is asserted where it cannot be forgotten -- and it is asserted
on the *names*, because that is what actually determines whether two lanes
collide.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

RETRIEVAL_CONFTEST = _REPO_ROOT / "tests" / "retrieval" / "conftest.py"
DB_LANE_CONFTEST = _REPO_ROOT / "services" / "control_plane" / "tests" / "db" / "conftest.py"

_NAME = re.compile(r"^(?:CI_DATABASE_NAME|EVAL_DATABASE_NAME)\s*=\s*[\"']([a-z_]+)[\"']", re.M)
_ENV = re.compile(r"^(?:TEST_URL_ENV|EVAL_URL_ENV)\s*=\s*[\"']([A-Z_]+)[\"']", re.M)


def _declared(path: Path, pattern: re.Pattern[str]) -> set[str]:
    return set(pattern.findall(path.read_text(encoding="utf-8")))


def test_both_conftests_declare_a_database() -> None:
    """Vacuity guard: a regex that matched nothing would pass everything below."""
    assert _declared(RETRIEVAL_CONFTEST, _NAME), "no database name found in the retrieval conftest"
    assert _declared(DB_LANE_CONFTEST, _NAME), "no database name found in the db-lane conftest"


def test_the_two_lanes_name_different_databases() -> None:
    """The regression. Sharing one database means one lane silently wipes the
    other's precondition, an hour of seeding at a time."""
    retrieval = _declared(RETRIEVAL_CONFTEST, _NAME)
    db_lane = _declared(DB_LANE_CONFTEST, _NAME)
    shared = retrieval & db_lane
    assert not shared, (
        f"both lanes pin {sorted(shared)}. The db lane's migration drill downgrades "
        "to base and re-upgrades, destroying the seeded corpus the retrieval lane "
        "requires. Give them separate databases."
    )


def test_they_read_different_environment_variables() -> None:
    """Separate names are not enough if one variable feeds both.

    A single `PROVENANCE_TEST_DB_URL` pointed at one database would put the
    lanes back on top of each other while the constants still disagreed, so the
    test above would pass and the wipe would happen anyway.
    """
    retrieval = _declared(RETRIEVAL_CONFTEST, _ENV)
    db_lane = _declared(DB_LANE_CONFTEST, _ENV)
    assert retrieval, "the retrieval conftest declares no DSN environment variable"
    assert db_lane, "the db-lane conftest declares no DSN environment variable"
    shared = retrieval & db_lane
    assert not shared, (
        f"both lanes read {sorted(shared)}, so one variable decides both and they "
        "land on the same database however the constants are named."
    )


def test_neither_lane_may_name_the_demo_database() -> None:
    """`provenance` carries the seeded demo state. Neither lane may touch it —
    the retrieval lane writes nothing but the migration drill drops schemas."""
    for path in (RETRIEVAL_CONFTEST, DB_LANE_CONFTEST):
        for name in _declared(path, _NAME):
            assert name != "provenance", f"{path.name} names the demo database"
