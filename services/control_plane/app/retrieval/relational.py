"""Stage E — relational validation and contradiction pruning (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 10 and 10.1.

Stage D produced up to twenty rows ranked by a number that knows nothing about
accounts, cases or time. Stage E asks what is structurally true about each one
and turns semantic neighbours into validated candidates.

Two reasons to prune, and no third
------------------------------------
Stage E is the only stage permitted to **remove** a Stage D candidate, and the
list is closed at two:

1. **Identity contradiction.** The candidate carries a non-null identifier that
   is a known reference for a *different* relationship of this user, and the
   incoming artifact's references are non-empty and disjoint from it. Two
   documents that both name account numbers and name different ones are not
   about the same thing, however similar their prose. This is the Northline
   Fiber shape exactly: ``NF-4471-8802`` and ``NF-9913-2250``, one counterparty,
   two relationships, near-identical billing language.
2. **Version mismatch.** Impossible given the Stage D predicate, and asserted
   here so a future path that bypasses Stage D cannot smuggle a vector from
   another embedding space into a ranking.

Everything else is down-weighted, never removed. A candidate with no case, no
relationship and no matching identifier survives as ``T3_VECTOR_ONLY``, and
that is the only path by which genuinely novel evidence -- a counterparty the
user has never had a relationship with -- reaches the model at all. Pruning it
would make the system unable to learn anything it did not already know.

The "known reference" condition is load-bearing
-------------------------------------------------
Pruning requires the candidate's identifier to be a known key of a *different*
relationship. Without that condition the first document from a new counterparty
would be pruned for naming an account number Provenance has never seen -- which
is precisely the case where the system most needs to look.

Recorded schema discrepancy
----------------------------
Section 10's statement reads ``ev.relationship_id``, ``ev.case_id`` and
``ev.identifier_norm``. Migration ``0002`` gives ``evidence_items`` none of
those columns; the identity link runs through ``claims``, which carries
``case_id`` and ``relationship_id``. This module therefore takes already-mapped
rows and does not name a column that does not exist. Reported rather than
resolved by inventing a migration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, TypeVar

__all__ = [
    "PRUNE_IDENTITY_CONTRADICTION",
    "PRUNE_VERSION_MISMATCH",
    "prune",
]

PRUNE_IDENTITY_CONTRADICTION: Final[str] = "PRUNED_IDENTITY_CONTRADICTION"
PRUNE_VERSION_MISMATCH: Final[str] = "PRUNED_VERSION_MISMATCH"

_Row = TypeVar("_Row", bound=Mapping[str, Any])


def prune(
    candidates: Iterable[_Row],
    *,
    artifact_refs: Sequence[str],
    known_refs: Sequence[str],
    embedding_version: str,
) -> tuple[list[_Row], list[tuple[Any, str]]]:
    """``(survivors, [(evidence_id, note)])``. Two reasons, and no third.

    Args:
        candidates: Stage D rows, already mapped.
        artifact_refs: normalised identifiers extracted from the incoming
            artifact. **Empty means "no identity signal", not "no match"** --
            with nothing to contradict, nothing is pruned.
        known_refs: every normalised identifier this user's relationships are
            known by. An identifier outside this set is new, not contradictory.
        embedding_version: the version the query vector was produced under.
    """
    kept: list[_Row] = []
    pruned: list[tuple[Any, str]] = []
    known = set(known_refs)
    incoming = set(artifact_refs)

    for row in candidates:
        if row.get("embedding_version") != embedding_version:
            pruned.append((row.get("evidence_id"), PRUNE_VERSION_MISMATCH))
            continue

        identifier = row.get("identifier_norm")
        contradicts_identity = (
            identifier is not None
            and bool(incoming)
            and identifier in known
            and identifier not in incoming
        )
        if contradicts_identity:
            pruned.append((row.get("evidence_id"), PRUNE_IDENTITY_CONTRADICTION))
            continue

        kept.append(row)

    return kept, pruned
