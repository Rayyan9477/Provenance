"""Determinism of the seed pipeline (``T2.8``).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, ``T2.8`` -- "Tests first":
  ``sid('case','isp-cancellation')`` is stable across processes, two consecutive
  ``make seed`` runs produce identical row counts, and every seeded timestamp is
  an offset from ``DEMO_ANCHOR`` rather than an absolute literal.
- ``docs/specs/10_DATABASE_DDL.md`` section 17.1 -- ``PROVENANCE_SEED_NS``,
  ``uuid5`` minting, ``DEMO_ANCHOR = 2026-09-18T09:00:00-04:00``,
  ``random.Random(20260817)``.
- ``docs/quality/22_EVAL_DATASETS.md`` section 7.2 rule 6 -- the corpus is
  byte-reproducible across machines.

Why "across processes" is the assertion and not "twice in one process"
----------------------------------------------------------------------
``uuid5`` is a pure function, so calling it twice in one interpreter proves
nothing at all; a module-level ``uuid4()`` cache would pass that test. The
failure this guards against is a seed whose ids depend on ``PYTHONHASHSEED``,
on dict ordering, on ``id()`` values, or on a clock read at import. Every one of
those is stable inside one process and unstable across two, so the id assertions
here spawn a **second interpreter** and compare its stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]

#: ``10_DATABASE_DDL.md`` section 17.1, verbatim. A test that imported this
#: value from the module under test could not detect the namespace changing.
EXPECTED_NAMESPACE = uuid.UUID("6f2b1c40-0000-4000-8000-70726f76656e")

#: ``10_DATABASE_DDL.md`` section 17.1 and ``quality/20_TDD_STRATEGY.md``
#: section 4.1. The root ``conftest.py`` pins the same instant.
EXPECTED_ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4)))

#: ``CANONICAL_DECISIONS.md`` -> Hero dataset canon.
DEPOSIT_DUE_AT = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
FINAL_INSPECTION = datetime(2026, 5, 16, tzinfo=UTC)
DAYS_OVERDUE = 95
WAKE_MARGIN_SECONDS = 60


def _in_a_fresh_interpreter(source: str) -> str:
    """Run *source* in a new Python process rooted at the repository."""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "child interpreter failed\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# ids.py -- uuid5 under a frozen namespace
# ---------------------------------------------------------------------------


def test_seed_namespace_is_the_frozen_one() -> None:
    from scripts.seed.ids import PROVENANCE_SEED_NS

    assert PROVENANCE_SEED_NS == EXPECTED_NAMESPACE


def test_sid_is_uuid5_not_uuid4() -> None:
    from scripts.seed.ids import PROVENANCE_SEED_NS, sid

    minted = sid("case", "isp-cancellation")
    assert minted.version == 5
    assert minted == uuid.uuid5(PROVENANCE_SEED_NS, "case:isp-cancellation")


def test_sid_case_isp_cancellation_is_stable_across_processes() -> None:
    """The exact assertion ``T2.8`` names, run in a second interpreter."""
    from scripts.seed.ids import sid

    here = str(sid("case", "isp-cancellation"))
    there = _in_a_fresh_interpreter(
        "from scripts.seed.ids import sid; print(sid('case', 'isp-cancellation'))"
    )
    assert here == there
    assert here == str(uuid.uuid5(EXPECTED_NAMESPACE, "case:isp-cancellation"))


def test_every_seeded_id_is_stable_across_processes() -> None:
    """Not just the one id the plan names -- the whole minted world."""
    from scripts.seed.world import all_seeded_ids

    here = {k: str(v) for k, v in sorted(all_seeded_ids().items())}
    there = json.loads(
        _in_a_fresh_interpreter(
            "import json;"
            "from scripts.seed.world import all_seeded_ids;"
            "print(json.dumps({k: str(v) for k, v in sorted(all_seeded_ids().items())}))"
        )
    )
    assert here == there
    assert len(here) >= 40, "the seeded world should mint at least forty stable ids"


# ---------------------------------------------------------------------------
# Timestamps are offsets from DEMO_ANCHOR, never absolute literals
# ---------------------------------------------------------------------------


def test_demo_anchor_is_the_frozen_instant() -> None:
    from scripts.seed.ids import DEMO_ANCHOR

    assert DEMO_ANCHOR == EXPECTED_ANCHOR


def test_deposit_due_at_derives_from_the_anchor_and_is_95_days_overdue() -> None:
    from scripts.seed.ids import DEMO_ANCHOR, DEPOSIT_DUE_AT

    assert DEPOSIT_DUE_AT.replace(tzinfo=UTC) == DEPOSIT_DUE_AT
    assert DEPOSIT_DUE_AT.isoformat() == "2026-06-15T00:00:00+00:00"
    overdue = (DEMO_ANCHOR.astimezone(UTC).date() - DEPOSIT_DUE_AT.date()).days
    assert overdue == DAYS_OVERDUE


def test_final_inspection_and_the_thirty_day_promise_agree() -> None:
    from scripts.seed.ids import DEPOSIT_DUE_AT, FINAL_INSPECTION_AT

    assert FINAL_INSPECTION_AT.date().isoformat() == "2026-05-16"
    assert (DEPOSIT_DUE_AT.date() - FINAL_INSPECTION_AT.date()).days == 30


def test_trigger_wake_is_due_at_plus_the_wake_margin() -> None:
    from scripts.seed.ids import DEPOSIT_DUE_AT, TRIGGER_WAKE_AT, WAKE_MARGIN_SECONDS

    assert WAKE_MARGIN_SECONDS == 60
    assert DEPOSIT_DUE_AT + timedelta(seconds=WAKE_MARGIN_SECONDS) == TRIGGER_WAKE_AT
    assert TRIGGER_WAKE_AT.isoformat() == "2026-06-15T00:01:00+00:00"


def test_no_seed_module_contains_a_bare_absolute_timestamp_literal() -> None:
    """Every seeded instant is an offset from the anchor.

    ``ids.py`` is the one module allowed to name absolute instants, because it
    is where the anchor and the four canon dates are declared. Anywhere else, a
    ``datetime(2026, ...)`` literal is a date that stops moving with the demo
    clock -- the exact drift ``23_PHASE_GATES.md`` section 1559 describes.
    """
    import ast

    seed_dir = REPO_ROOT / "scripts" / "seed"
    offenders: list[str] = []
    for path in sorted(seed_dir.rglob("*.py")):
        if path.name == "ids.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"datetime", "date"} and node.args:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "absolute datetime/date literals outside scripts/seed/ids.py: " + ", ".join(offenders)
    )


def test_no_seed_module_reads_the_wall_clock() -> None:
    """``datetime.now()`` / ``utcnow()`` / ``time.time()`` make a seed unrepeatable."""
    import ast

    seed_dir = REPO_ROOT / "scripts" / "seed"
    banned = {"now", "utcnow", "today", "time", "monotonic"}
    offenders: list[str] = []
    for path in sorted(seed_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in banned
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"datetime", "date", "time"}
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], "wall-clock reads in the seed: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# The decoy corpus is byte-reproducible
# ---------------------------------------------------------------------------


def test_decoy_rng_seed_is_the_frozen_one() -> None:
    from scripts.seed.decoys import DECOY_PLAN, NEAR_MISS_QUOTA, RNG_SEED

    assert RNG_SEED == 20260817
    assert DECOY_PLAN == {"hero": 16_000, "iso-a": 1_000, "iso-b": 1_000}
    assert NEAR_MISS_QUOTA == 120


def test_decoy_corpus_is_identical_across_processes() -> None:
    """A hash over the whole generated corpus, computed in two interpreters."""
    from scripts.seed.decoys import corpus_fingerprint

    here = corpus_fingerprint()
    there = _in_a_fresh_interpreter(
        "from scripts.seed.decoys import corpus_fingerprint; print(corpus_fingerprint())"
    )
    assert here == there
    assert len(here) == 64


def test_decoy_generation_is_repeatable_within_a_process() -> None:
    from scripts.seed.decoys import generate_decoys

    first = list(generate_decoys())
    second = list(generate_decoys())
    assert len(first) == 18_000
    assert [d.id for d in first] == [d.id for d in second]
    assert [d.normalized_text for d in first] == [d.normalized_text for d in second]


def test_decoy_ids_are_unique() -> None:
    from scripts.seed.decoys import generate_decoys

    ids = [d.id for d in generate_decoys()]
    assert len(set(ids)) == len(ids)


def test_near_misses_are_isp_invoices_within_25_dollars_of_186() -> None:
    """``22_EVAL_DATASETS.md`` section 7.2 rule 2 -- the quota that makes the
    retrieval eval measure discrimination rather than recall against noise."""
    from decimal import Decimal

    from scripts.seed.decoys import NEAR_MISS_QUOTA, generate_decoys

    near_misses = [d for d in generate_decoys() if d.is_near_miss]
    assert len(near_misses) == NEAR_MISS_QUOTA
    for decoy in near_misses:
        assert decoy.amount is not None
        assert abs(decoy.amount - Decimal("186.00")) <= Decimal("25.00")
        assert decoy.amount != Decimal("186.00"), "a near-miss is near, not equal"


def test_decoy_observed_at_is_inside_the_540_day_lookback() -> None:
    from scripts.seed.decoys import generate_decoys
    from scripts.seed.ids import DEMO_ANCHOR

    anchor = DEMO_ANCHOR.astimezone(UTC)
    for decoy in generate_decoys():
        delta = (anchor - decoy.observed_at).days
        assert 0 <= delta <= 540, f"{decoy.id} observed {delta} days before the anchor"


def test_isolation_decoys_reuse_the_hero_vocabulary() -> None:
    """``22_EVAL_DATASETS.md`` section 7.2 rule 3 -- the isolation tripwire only
    works if the other tenants' text is near-identical to the hero's."""
    from scripts.seed.decoys import generate_decoys

    iso = [d for d in generate_decoys() if d.bucket in {"iso-a", "iso-b"}]
    assert len(iso) == 2_000
    assert sum("Northline Fiber" in d.normalized_text for d in iso) >= 100


def test_no_decoy_embedding_text_contains_an_identifier() -> None:
    """``13_RETRIEVAL_SPEC.md`` section 12.1 rule 5 -- identifiers are a flag,
    never embedded input."""
    import re

    from scripts.seed.decoys import generate_decoys

    account_shape = re.compile(r"\b[A-Z]{2,4}-\d{3,}")
    offenders = [d.id for d in generate_decoys() if account_shape.search(d.embedding_text())]
    assert offenders == []
