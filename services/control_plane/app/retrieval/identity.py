"""Stage B — deterministic identity candidates (``T6.2``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 7.1, 7.2 and 7.3.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.2``: identity order is frozen canon.

This is the stage that makes retrieval a system of record rather than a search
engine. Everything here is exact matching on normalised keys: no fuzzy string
distance, no model, no embedding.

Why ``match_strength`` is a maximum
------------------------------------
Summing lets three weak signals -- a sender domain (0.72), a counterparty name
(0.60) and an amount (0.55) -- reach 1.87 and outvote one exact account
reference at 1.00. That is precisely the adjudication-by-accumulation failure
this stage exists to prevent, and it is the more natural thing to write, which
is why it has a test of its own rather than a comment.

The *count* of matched features is still informative, so it is carried
separately by :func:`feature_count` and spent in Stage G as a small
corroboration bonus with a hard cap. The cap exists for the same reason the max
does.

Names are not keys
-------------------
:func:`name_matches` normalises case and whitespace and then compares for
equality. Normalisation is not fuzziness: ``Northline Fiber`` and
``northline   fiber`` are one name written twice, while ``Northline Fibre`` is
a different string and must not match. Section 7.2 is explicit -- "Exact match
only; no fuzzy distance, ever" -- because an edit-distance threshold that is
right for typos is also right for two genuinely different counterparties whose
names differ by one letter.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

__all__ = [
    "FEATURE_STRENGTH",
    "STAGE_ORDER",
    "MatchKind",
    "feature_count",
    "match_strength",
    "name_matches",
    "normalise_name",
    "stage_order",
]


class MatchKind(StrEnum):
    """The nine deterministic signals of section 7.2, and only those nine."""

    EXACT_ACCOUNT_REF = "EXACT_ACCOUNT_REF"
    EXACT_ALT_IDENTIFIER = "EXACT_ALT_IDENTIFIER"
    EMAIL_THREAD = "EMAIL_THREAD"
    PRIOR_ARTIFACT_REF = "PRIOR_ARTIFACT_REF"
    SERVICE_ADDRESS = "SERVICE_ADDRESS"
    SENDER_DOMAIN = "SENDER_DOMAIN"
    COUNTERPARTY_NAME = "COUNTERPARTY_NAME"
    AMOUNT = "AMOUNT"
    PHONE_SUFFIX = "PHONE_SUFFIX"


#: Section 7.2's table, with its justifications compressed to one line each.
#:
#: ``PRIOR_ARTIFACT_REF`` is section 7.2's fourth row -- an order, booking or
#: invoice reference matched against a prior ``evidence_items.identifier_norm``.
#: The table gives it the ``EXACT_ALT_IDENTIFIER`` *kind* at strength 0.90,
#: which would make one kind carry two strengths; it is a distinct member here
#: so the mapping stays a function. Reported as a spec discrepancy.
FEATURE_STRENGTH: Final[dict[str, float]] = {
    # The counterparty's own primary key for this user. Collision requires the
    # counterparty to reuse an account number, which is a data-quality event.
    MatchKind.EXACT_ACCOUNT_REF.value: 1.00,
    # Same mechanism, but the alt bag is populated by earlier extraction rather
    # than by user confirmation, so it inherits extraction risk.
    MatchKind.EXACT_ALT_IDENTIFIER.value: 0.97,
    # Strong, but clients rewrite headers on forward.
    MatchKind.EMAIL_THREAD.value: 0.93,
    # Ties to a prior artifact rather than to the relationship directly; the
    # correct relationship follows by join, one hop of inference.
    MatchKind.PRIOR_ARTIFACT_REF.value: 0.90,
    # Discriminative across a user's small relationship set; degraded by the
    # deliberate unit-number drop.
    MatchKind.SERVICE_ADDRESS.value: 0.78,
    # Identifies the counterparty reliably, the *relationship* only when the
    # user has one relationship with that counterparty -- which is not
    # guaranteed. Two Northline Fiber accounts is the seeded counter-example.
    MatchKind.SENDER_DOMAIN.value: 0.72,
    # Names are not keys.
    MatchKind.COUNTERPARTY_NAME.value: 0.60,
    # $186 appears in unrelated places. Corroborating, never decisive.
    MatchKind.AMOUNT.value: 0.55,
    # 10,000-way; a user with twelve relationships expects collisions.
    MatchKind.PHONE_SUFFIX.value: 0.45,
}

#: Frozen canon. Exact identifiers and deterministic signals precede vector
#: similarity; vector output is advisory and never canonical truth.
#:
#: Section 5 issues the embedding call *concurrently* with Stage B, which is a
#: latency choice and not a reordering: on the identity-certain path the vector
#: arrives before it is needed and costs nothing on the critical path. What is
#: canon is that the vector *result* is consumed after the deterministic
#: candidates exist, never instead of them.
STAGE_ORDER: Final[tuple[str, ...]] = ("SCOPE", "IDENTITY", "TEMPORAL", "VECTOR")

_WS = re.compile(r"\s+")


def stage_order() -> tuple[str, ...]:
    """The frozen stage order, for the test that asserts identity precedes vector."""
    return STAGE_ORDER


def match_strength(kinds: Sequence[str]) -> float:
    """The **maximum** strength over matched features. Never the sum.

    Raises:
        KeyError: an unrecognised feature name. Scoring a typo as zero would
            make a mis-spelled ``EXACT_ACCOUNT_REF`` indistinguishable from an
            unmatched one, and the candidate would quietly lose its strongest
            signal with nothing to see in a log.
    """
    if not kinds:
        return 0.0
    strengths = []
    for kind in kinds:
        if kind not in FEATURE_STRENGTH:
            raise KeyError(
                f"{kind!r} is not one of the nine deterministic signals in "
                f"13_RETRIEVAL_SPEC.md section 7.2: {sorted(FEATURE_STRENGTH)}"
            )
        strengths.append(FEATURE_STRENGTH[kind])
    return max(strengths)


def feature_count(kinds: Sequence[str]) -> int:
    """How many distinct features matched. Feeds the rerank, never the strength."""
    return len(set(kinds))


def normalise_name(value: str) -> str:
    """NFKC, casefold, collapse whitespace. Normalisation, not fuzziness."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return _WS.sub(" ", folded).strip()


def name_matches(left: str, right: str) -> bool:
    """Exact match on the normalised form. No edit distance, ever."""
    return normalise_name(left) == normalise_name(right)
