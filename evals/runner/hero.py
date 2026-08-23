"""Which rows belong to the hero, decided by provenance and never by text.

The mistake this module exists to make unrepresentable
------------------------------------------------------
An earlier attempt at this measurement located the hero June invoice with
``normalized_text ILIKE '%june%' AND normalized_text ILIKE '%186%'``, took the
first hit, and reported ``FAIL, rank 2254 of 18035``. Every row it matched was
a **decoy** -- Aster Line Internet, Rookery Data Services, Selkirk Water
Authority -- because the decoys were generated to be near-neighbours of the
hero content *on purpose*. The verdict was about the wrong document, so it was
not a weak measurement; it was a measurement of something else.

Two facts settle membership, and both are checkable against the cluster:

1. Hero rows are identified by joining ``evidence_items`` to
   ``source_artifacts`` and testing ``s3_key``. Text never decides membership.
2. The prefix is ``raw/hero/hero/``, **not** ``raw/hero/``. The 16,000 hero
   decoys are staged under ``raw/hero/decoys/``, so the shorter prefix matches
   every decoy in the tenant and reaches the same wrong answer by a second
   route. Measured: 16,000 keys under ``raw/hero/decoys/`` and 34 under
   ``raw/hero/hero/``.

The June invoice is not in ``evidence_items`` at all. ``demo/artifacts/``
carries 35 files and ``source_artifacts`` carries 34 hero rows; the missing one
is ``northline-june-invoice.eml``, which the demo ingests live to create the
conflict. Its rank is not a measurement that can be taken today, and this
module refuses to invent one.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DECOY_KEY_PREFIXES",
    "FORBIDDEN_SELECTORS",
    "HERO_KEY_PREFIX",
    "LIVE_INGEST_ARTIFACT",
    "is_decoy_key",
    "is_hero_key",
]

#: Where the 34 curated hero artifacts are staged.
HERO_KEY_PREFIX: Final[str] = "raw/hero/hero/"

#: Where the 18,000 synthetic near-neighbour decoys are staged, across the hero
#: tenant and the two isolation tenants.
DECOY_KEY_PREFIXES: Final[tuple[str, ...]] = (
    "raw/hero/decoys/",
    "raw/iso-a/decoys/",
    "raw/iso-b/decoys/",
)

#: The one demo artifact deliberately absent from ``source_artifacts``.
LIVE_INGEST_ARTIFACT: Final[str] = "northline-june-invoice.eml"

#: SQL fragments that would reintroduce text-matched membership. Asserted
#: against this package's own source, because the failure mode is a plausible
#: query that returns rows rather than an error that stops the run.
FORBIDDEN_SELECTORS: Final[tuple[str, ...]] = (
    "ILIKE",
    "normalized_text LIKE",
    "exact_text LIKE",
)


def is_hero_key(s3_key: str) -> bool:
    """True for a curated hero artifact key; false for a decoy.

    The comparison is against the four-segment prefix. ``raw/hero/`` is the
    trap: it matches all 16,000 hero decoys as well as the 34 curated
    artifacts, so a join written with it looks provenance-based and selects the
    same rows a text search would.
    """
    return s3_key.startswith(HERO_KEY_PREFIX)


def is_decoy_key(s3_key: str) -> bool:
    """True for a synthetic decoy key in any of the three seeded tenants."""
    return any(s3_key.startswith(prefix) for prefix in DECOY_KEY_PREFIXES)
