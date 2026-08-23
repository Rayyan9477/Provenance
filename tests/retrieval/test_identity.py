"""Stage B — deterministic identity precedes vector similarity (``T6.2``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 5, 7.1 and 7.2.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.2``: "Identity order is frozen canon:
  exact identifiers and deterministic signals precede vector similarity; vector
  output is advisory and never canonical truth."
- ``docs/quality/23_PHASE_GATES.md`` ``G6.4``.

The two failures this file exists to catch
-------------------------------------------
**Weak-signal stacking.** ``match_strength`` is the **maximum** over matched
features, never the sum. Summing lets three weak signals -- a phone suffix, an
amount, a name -- outvote one exact account number, which is precisely the
adjudication-by-similarity failure this product exists to fix. A sum is also
the more natural thing to write, which is why it gets its own test rather than
a comment.

**Stage inversion.** If the vector call is issued *before* Stage B rather than
concurrently with it, retrieval becomes a search engine with a database
attached. The ordering is asserted on the recorded call sequence, not on a
docstring.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.retrieval import identity

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

#: Section 7.2, transcribed. The table is the contract; a weight edited in the
#: module without a matching spec edit fails here.
SPEC_STRENGTHS = {
    "EXACT_ACCOUNT_REF": 1.00,
    "EXACT_ALT_IDENTIFIER": 0.97,
    "EMAIL_THREAD": 0.93,
    "PRIOR_ARTIFACT_REF": 0.90,
    "SERVICE_ADDRESS": 0.78,
    "SENDER_DOMAIN": 0.72,
    "COUNTERPARTY_NAME": 0.60,
    "AMOUNT": 0.55,
    "PHONE_SUFFIX": 0.45,
}


def test_the_feature_strength_table_matches_the_spec() -> None:
    """Section 7.2, every row."""
    assert {k: round(v, 2) for k, v in identity.FEATURE_STRENGTH.items()} == SPEC_STRENGTHS


def test_match_strength_is_the_maximum_and_never_the_sum() -> None:
    """Section 7.2. Three weak signals must not outvote one exact identifier.

    ``0.72 + 0.60 + 0.55 = 1.87`` under a sum, and ``0.72`` under a max. The
    exact-account candidate scores ``1.00`` either way; only under the max does
    it still win.
    """
    weak = ("SENDER_DOMAIN", "COUNTERPARTY_NAME", "AMOUNT")
    strong = ("EXACT_ACCOUNT_REF",)
    assert identity.match_strength(weak) == pytest.approx(0.72)
    assert identity.match_strength(strong) == pytest.approx(1.00)
    assert identity.match_strength(strong) > identity.match_strength(weak)


def test_match_strength_of_nothing_is_zero() -> None:
    """No matched feature is no evidence of aboutness, not a small amount of it."""
    assert identity.match_strength(()) == 0.0


def test_an_unknown_feature_name_is_refused() -> None:
    """A typo in a feature name must not silently score zero.

    Scoring it zero would make a mis-spelled ``EXACT_ACCOUNT_REF`` look exactly
    like an unmatched one, and the candidate would quietly lose its strongest
    signal.
    """
    with pytest.raises(KeyError):
        identity.match_strength(("EXACT_ACOUNT_REF",))


def test_feature_count_is_carried_separately_from_strength() -> None:
    """Section 7.2's last line: the *count* feeds the rerank as a corroboration
    bonus; it never feeds the strength."""
    assert identity.feature_count(("SENDER_DOMAIN", "AMOUNT", "PHONE_SUFFIX")) == 3
    assert identity.feature_count(()) == 0


def test_deterministic_signals_are_evaluated_before_vector_similarity() -> None:
    """``T6.2`` acceptance, asserted on call ordering.

    ``13_RETRIEVAL_SPEC.md`` section 5 issues the embedding call *concurrently*
    with Stage B, which is a latency choice; what is canon is that the vector
    *result* is consumed after the deterministic candidates exist and never
    instead of them.
    """
    order = identity.stage_order()
    assert order.index("IDENTITY") < order.index("VECTOR")


def test_names_are_matched_exactly_and_never_fuzzily() -> None:
    """Section 7.2: "Names are not keys. Exact match only; no fuzzy distance, ever."

    Case and surrounding whitespace are normalisation, not fuzziness. An edit
    distance is fuzziness, and ``Northline Fiber`` must not match
    ``Northline Fibre``.
    """
    assert identity.name_matches("Northline Fiber", "  northline   fiber ")
    assert not identity.name_matches("Northline Fiber", "Northline Fibre")
    assert not identity.name_matches("Northline Fiber", "Northline")
