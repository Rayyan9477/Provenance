"""Hero membership is provenance, never text -- and the prefix is the long one.

Both halves of this file guard one recorded mistake. A previous measurement
found "the hero June invoice" with a text `ILIKE` and reported `FAIL, rank 2254
of 18035` against a decoy. The prefix half is the same mistake by a second
route: `raw/hero/` matches all 16,000 hero decoys, so a join that looks
provenance-based selects the identical wrong rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.runner.hero import (
    DECOY_KEY_PREFIXES,
    FORBIDDEN_SELECTORS,
    HERO_KEY_PREFIX,
    LIVE_INGEST_ARTIFACT,
    is_decoy_key,
    is_hero_key,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "evals" / "runner"


def test_the_hero_prefix_is_the_long_one_that_excludes_the_decoy_tree() -> None:
    assert HERO_KEY_PREFIX == "raw/hero/hero/"
    assert is_hero_key("raw/hero/hero/northline-final-invoice.eml") is True
    assert is_hero_key("raw/hero/decoys/02254.txt") is False, (
        "a hero decoy was admitted as a hero row. 'raw/hero/' matches all "
        "16,000 decoys in the hero tenant, which is how a text search and a "
        "provenance join reach the same wrong answer."
    )


def test_decoys_in_every_tenant_are_recognised_as_decoys() -> None:
    assert is_decoy_key("raw/hero/decoys/00000.txt") is True
    assert is_decoy_key("raw/iso-a/decoys/00000.txt") is True
    assert is_decoy_key("raw/iso-b/decoys/00999.txt") is True
    assert is_decoy_key("raw/hero/hero/harborview-followup.eml") is False


def test_a_key_cannot_be_both_hero_and_decoy() -> None:
    keys = (
        "raw/hero/hero/northline-final-invoice.eml",
        "raw/hero/decoys/00001.txt",
        "raw/iso-a/decoys/00001.txt",
    )
    for key in keys:
        assert not (is_hero_key(key) and is_decoy_key(key)), key


def test_the_decoy_prefixes_cover_all_three_seeded_tenants() -> None:
    assert set(DECOY_KEY_PREFIXES) == {
        "raw/hero/decoys/",
        "raw/iso-a/decoys/",
        "raw/iso-b/decoys/",
    }


def test_the_live_ingest_artifact_is_named_so_nobody_ranks_it() -> None:
    assert LIVE_INGEST_ARTIFACT == "northline-june-invoice.eml"
    assert (REPO_ROOT / "demo" / "artifacts" / LIVE_INGEST_ARTIFACT).is_file(), (
        "the artifact the demo ingests live is missing from demo/artifacts/. "
        "Its absence from source_artifacts is deliberate; its absence from disk "
        "would be a broken demo."
    )


def test_no_module_in_the_runner_selects_hero_rows_by_text() -> None:
    offenders: list[str] = []
    for path in sorted(RUNNER.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # The constant tuple itself names the forbidden fragments; skip it.
        if path.name == "hero.py":
            continue
        for fragment in FORBIDDEN_SELECTORS:
            if fragment in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {fragment}")
    assert offenders == [], (
        "a runner module selects evidence by matching its text. Every decoy in "
        "this corpus was generated to be a near-neighbour of hero content on "
        "purpose, so a text match returns decoys that read exactly right. "
        f"Offending fragments: {offenders}"
    )
