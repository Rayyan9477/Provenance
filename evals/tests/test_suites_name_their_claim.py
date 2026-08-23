"""Every suite must name the product claim it measures, and there are four.

`docs/00_PRODUCT.md` section 2.2 makes four structural claims about what
ordinary RAG cannot do. A suite that does not name one of them is measuring
something nobody asked for, and -- worse -- a claim with no suite is a claim
nobody is checking. This file is the join between the two lists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.runner.suites import extraction, memory, retraction, retrieval

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT = REPO_ROOT / "docs" / "00_PRODUCT.md"

SUITE_MODULES = (retrieval, retraction, extraction, memory)

#: The four sentences of section 2.2, shortened to a fragment that must occur
#: verbatim in the document. Quoting the document rather than paraphrasing it
#: means an edit to section 2.2 fails this test instead of silently
#: invalidating every claim string in the suites.
SECTION_2_2_FRAGMENTS = (
    "Whichever ranks higher wins the answer",
    "not as a *claim by an interested party*",
    "no write path, so there is no state to be wrong about",
    "keep their embeddings and keep resurfacing",
)


def _section_2_2() -> str:
    text = PRODUCT.read_text(encoding="utf-8")
    body = text.split("### 2.2 Not RAG", 1)[1]
    return body.split("### 2.3", 1)[0]


def test_the_four_claims_are_still_the_four_claims_the_document_makes() -> None:
    section = _section_2_2()
    missing = [fragment for fragment in SECTION_2_2_FRAGMENTS if fragment not in section]
    assert missing == [], (
        "docs/00_PRODUCT.md section 2.2 no longer contains "
        f"{missing}. The suites quote it; if the document moved, the suites are "
        "measuring a claim that is no longer made."
    )


def test_every_suite_names_the_claim_it_measures() -> None:
    for module in SUITE_MODULES:
        assert module.CLAIM, f"{module.__name__} declares no CLAIM"
        assert (
            "00_PRODUCT.md section 2.2" in module.CLAIM
        ), f"{module.__name__}.CLAIM does not cite the section it is testing."


def test_every_section_2_2_claim_is_covered_by_at_least_one_suite() -> None:
    claims = " ".join(module.CLAIM for module in SUITE_MODULES)
    # The suite strings quote the document with the markdown emphasis removed.
    uncovered = [
        fragment for fragment in SECTION_2_2_FRAGMENTS if fragment.replace("*", "") not in claims
    ]
    assert uncovered == [], (
        f"section 2.2 makes claims no suite names: {uncovered}. A claim with no "
        f"suite is a claim nobody is checking."
    )


def test_suite_ids_are_unique() -> None:
    ids = [module.SUITE_ID for module in SUITE_MODULES]
    assert len(set(ids)) == len(ids), f"duplicate suite ids: {ids}"


def test_the_retrieval_suite_reports_the_cutoff_production_actually_uses() -> None:
    from services.control_plane.app.retrieval.config import K_FINAL

    assert K_FINAL in retrieval.CUTOFFS, (
        f"recall is not reported at k={K_FINAL}, which is what retrieval "
        f"returns in production. Every other cutoff is context for that one."
    )
    assert max(retrieval.CUTOFFS) <= retrieval.CAP, (
        "the ranking is read less deeply than a reported cutoff, so the deepest "
        "recall figure is bounded by something the report does not say."
    )
