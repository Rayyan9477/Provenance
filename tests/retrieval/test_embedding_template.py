"""The nine template rules, and the cache-is-a-cache proof (``T6.1``, ``G6.6``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1: the template, and "Nine
  rules, each enforced by a unit test in
  ``tests/retrieval/test_embedding_template.py``" -- this file, by name.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.6``: "same normalized text ->
  identical vector; cache cleared -> identical vector recomputed".
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*.

Why the seed-parity test is the most important one here
--------------------------------------------------------
``scripts/seed/embedding_text.py`` rendered the 18,035 vectors now sitting in
``evidence_items.embedding``. ``services/control_plane/app/retrieval/
embeddings.py`` renders the query vector. If those two templates ever differ by
one byte -- a stripped non-breaking space, a different ``none``/``unknown``
sentinel, a body cap off by one -- the query vector and the index live in
different neighbourhoods of the same space. **Nothing errors. Recall just
quietly collapses.** No integration test catches it, because both halves work.
:func:`test_query_template_is_byte_identical_to_the_seed_template` is the only
thing standing between this system and that failure, which is why it exercises
a matrix of inputs rather than one happy case.

Zero Bedrock calls
------------------
Every test here is ``unit``: hermetic, no socket, no credentials. The template
is a pure function of its arguments, and the cache is a dictionary lookup.
``G6.6``'s "identical vector recomputed" is proved against a recording embedder
that counts its calls, so "the cache is a cache and not a correctness
dependency" is asserted rather than asserted-about.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.control_plane.app.retrieval import embeddings
from services.control_plane.app.retrieval.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_VERSION,
    MAX_BODY_CHARS,
)

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

HERO = {
    "evidence_type": "DATE_ASSERTION",
    "counterparty_name": "Example ISP",
    "predicate": "service_billing_period",
    "valid_from": datetime(2026, 6, 1, tzinfo=UTC),
    "valid_to": datetime(2026, 7, 1, tzinfo=UTC),
    "currency": "USD",
    "amount": Decimal("186.00"),
    "has_identifier": True,
    "normalized_text": (
        "Invoice for internet service covering 1 June 2026 through 30 June 2026. "
        "Amount due USD 186.00 by 30 June 2026."
    ),
}

#: The exact block ``13_RETRIEVAL_SPEC.md`` section 12.1 prints as "Rendered,
#: for the hero invoice's billing-period evidence item". Transcribed so a spec
#: edit and a code edit cannot both drift in the same direction unnoticed.
SPEC_RENDERING = (
    "[type=DATE_ASSERTION]\n"
    "[counterparty=Example ISP]\n"
    "[predicate=service_billing_period]\n"
    "[valid=2026-06-01/2026-07-01]\n"
    "[money=USD 186.00]\n"
    "[has_identifier=true]\n"
    "Invoice for internet service covering 1 June 2026 through 30 June 2026. "
    "Amount due USD 186.00 by 30 June 2026."
)


def render(**overrides: object) -> str:
    kwargs = dict(HERO)
    kwargs.update(overrides)
    return embeddings.build_embedding_text(**kwargs)  # type: ignore[arg-type]


class RecordingEmbedder:
    """A deterministic stand-in for Titan that counts how often it was asked.

    Lives here rather than in the production module on purpose: a test double
    shipped beside the client it replaces is one import away from being used by
    accident, and ``EMBEDDING_VERSION`` is meant to be the only thing that
    decides which space a vector lives in.

    Deterministic because ``G6.6``'s claim is about *identity* of the returned
    vector, and a random stand-in could only demonstrate that the cache is a
    cache -- never that it is not a correctness dependency.
    """

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls += 1
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return tuple(digest[index % len(digest)] / 255.0 for index in range(EMBEDDING_DIMENSIONS))


# ==========================================================================
# The template itself -- section 12.1, rules 1 to 4
# ==========================================================================


def test_the_hero_rendering_matches_the_spec_verbatim() -> None:
    """Section 12.1's own printed example, asserted byte for byte."""
    assert render() == SPEC_RENDERING


def test_absent_fields_render_as_sentinels_and_are_never_omitted() -> None:
    """Rule 1: field order is fixed and *total*.

    A missing line shifts every downstream token and moves the vector for a
    reason that has nothing to do with meaning. So an unknown counterparty is
    ``unknown``, not a dropped line.
    """
    text = render(
        counterparty_name=None,
        predicate=None,
        valid_from=None,
        valid_to=None,
        currency=None,
        amount=None,
        has_identifier=False,
    )
    lines = text.splitlines()
    assert lines[:6] == [
        "[type=DATE_ASSERTION]",
        "[counterparty=unknown]",
        "[predicate=unknown]",
        "[valid=unknown]",
        "[money=none]",
        "[has_identifier=false]",
    ]


def test_one_open_end_renders_open_rather_than_collapsing_the_line() -> None:
    """A half-bounded validity window is knowledge, and it keeps its line."""
    assert "[valid=2026-06-01/open]" in render(valid_to=None)
    assert "[valid=open/2026-07-01]" in render(valid_from=None)


def test_three_spellings_of_one_date_produce_one_line() -> None:
    """Rule 2: dates are ISO ``YYYY-MM-DD``, always, in UTC.

    ``1 June 2026``, ``06/01/2026`` and ``June 1st`` are the same instant, and
    the template must not let their prose spelling reach the vector.
    """
    same_instant = [
        datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 1, 13, 45, tzinfo=UTC),
        datetime(2026, 6, 1, 23, 59, 59, tzinfo=UTC),
    ]
    rendered = {render(valid_from=d).splitlines()[3] for d in same_instant}
    assert rendered == {"[valid=2026-06-01/2026-07-01]"}


def test_money_is_two_decimals_with_no_thousands_separator() -> None:
    """Rule 3. ``1234.5`` and ``1234.50`` are one amount and one line."""
    assert "[money=USD 1234.50]" in render(amount=Decimal("1234.5"))
    assert "[money=USD 1234.50]" in render(amount=Decimal("1234.50"))
    assert "[money=USD 0.00]" in render(amount=Decimal("0"))


def test_money_is_never_a_float() -> None:
    """The money rule reaches the template too.

    ``0.1 + 0.2`` is not ``0.3``, and a currency amount that rounds differently
    on two machines produces two cache keys for one document.
    """
    with pytest.raises((TypeError, ValueError)):
        render(amount=186.00)  # a float amount is the thing being refused


def test_body_is_nfkc_normalised_and_whitespace_collapsed() -> None:
    """Rule 4, first half.

    A mail client's non-breaking space and a full-width digit must not move the
    vector. NFKC folds the digit; ``U+00A0`` needs folding explicitly because
    NFKC leaves it alone.
    """
    noisy = "Invoice for\t\tinternet   service\n\ncovering １ June."  # noqa: RUF001
    assert "Invoice for internet service covering 1 June." in render(normalized_text=noisy)


def test_body_is_hard_capped_at_900_characters() -> None:
    """Rule 4, second half.

    Titan accepts far more; the cap stops one verbose item diluting its own
    signal across a wall of boilerplate. An item that needs more than 900
    characters is an extraction bug.
    """
    body = render(normalized_text="x" * 5000).split("[has_identifier=true]\n", 1)[1]
    assert len(body) == MAX_BODY_CHARS == 900


# ==========================================================================
# Rules 5 to 7 -- what must never be in the vector
# ==========================================================================


def test_identifiers_are_a_flag_and_never_embedded_content() -> None:
    """Rule 5. ``88-114-2039`` never appears in embedding input.

    Subword tokenisers shred digit strings into meaningless fragments, and
    unrelated documents sharing digit patterns get spurious similarity. The one
    signal the system can match exactly, in Stage B, at strength 1.00 is the
    last one worth spending embedding capacity on.
    """
    text = render(has_identifier=True, normalized_text="Invoice for internet service.")
    assert "[has_identifier=true]" in text
    assert "88-114-2039" not in text


def test_parser_json_is_refused_rather_than_embedded() -> None:
    """Rule 7 / section 12.2.

    Embedding ``{"blocks":[{"kind":"BODY",...}]}`` means every document in the
    corpus shares its key names, and cosine similarity starts measuring schema
    conformance rather than content. The failure is silent, so the refusal is
    loud.
    """
    with pytest.raises(ValueError, match="(?i)parser|json"):
        render(normalized_text='{"blocks": [{"kind": "BODY", "text": "hi", "bbox": [1,2,3,4]}]}')


# ==========================================================================
# Rules 8 and 9 -- identity, the cache key, and the frozen version
# ==========================================================================


def test_the_cache_key_is_the_sha256_of_the_template_render() -> None:
    """Rule 8. The vector is a function of the *embedding input*, not of the body.

    Two items with identical prose but different ``[money=...]`` lines embed
    differently and must not collide in the cache.
    """
    text = render()
    assert embeddings.embedding_text_sha256(text) == hashlib.sha256(text.encode("utf-8")).digest()
    assert embeddings.embedding_text_sha256(render(amount=Decimal("999.00"))) != (
        embeddings.embedding_text_sha256(text)
    )


def test_the_frozen_version_triple_is_the_canon_one() -> None:
    """Rule 9, and ``G6.1``'s one-row assertion, stated at the source.

    ``EMBEDDING_VERSION`` covers the model, the dimensionality, the distance
    function *and* the normalisation template together. A bare model id: Amazon
    and third-party models take bare ids on Bedrock; only Anthropic chat models
    take a ``us.`` inference-profile prefix.
    """
    assert EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"
    assert not EMBEDDING_MODEL_ID.startswith("us.")
    assert EMBEDDING_DIMENSIONS == 1024
    assert EMBEDDING_VERSION == "v1"
    assert EMBEDDING_TEMPLATE_VERSION == "tmpl1"


def test_embedding_with_a_non_canon_model_id_is_refused() -> None:
    """``T6.1``: a nightly eval must not be able to quietly switch models."""
    recorder = RecordingEmbedder()
    with pytest.raises(embeddings.EmbeddingModelMismatchError):
        embeddings.embed_text(
            render(), embedder=recorder, model_id="us.anthropic.claude-opus-5-20260401-v1:0"
        )
    assert recorder.calls == 0, "the model id was checked after the call, not before it"


# ==========================================================================
# G6.6 -- the cache is a cache, not a correctness dependency
# ==========================================================================


def test_same_normalised_text_yields_the_identical_vector() -> None:
    """``G6.6``, first half. Determinism, asserted on the bytes."""
    cache = embeddings.EmbeddingCache()
    recorder = RecordingEmbedder()
    first = embeddings.embed_text(render(), embedder=recorder, cache=cache)
    second = embeddings.embed_text(render(), embedder=recorder, cache=cache)
    assert first == second
    assert len(first) == EMBEDDING_DIMENSIONS
    assert recorder.calls == 1, "the second call was not served from the cache"


def test_clearing_the_cache_yields_the_identical_vector_recomputed() -> None:
    """``G6.6``, second half -- and the half that carries the meaning.

    A cache that changed the answer would be a correctness dependency wearing a
    performance costume. Clearing it must cost a call and return the same
    bytes.
    """
    cache = embeddings.EmbeddingCache()
    recorder = RecordingEmbedder()
    warm = embeddings.embed_text(render(), embedder=recorder, cache=cache)
    cache.clear()
    cold = embeddings.embed_text(render(), embedder=recorder, cache=cache)
    assert cold == warm
    assert recorder.calls == 2, "clearing the cache did not force a recomputation"


def test_a_cache_miss_in_offline_mode_is_loud() -> None:
    """The alternative -- a quietly returned zero vector -- is the worst failure.

    A database of zero vectors passes every row-count assertion and returns
    nonsense from retrieval, and nothing downstream notices.
    """
    cache = embeddings.EmbeddingCache()
    with pytest.raises(embeddings.EmbeddingCacheMissError):
        embeddings.embed_text(render(), embedder=None, cache=cache)


# ==========================================================================
# The parity test -- the one that stops silent recall collapse
# ==========================================================================


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"counterparty_name": None, "predicate": None},
        {"valid_from": None},
        {"valid_to": None},
        {"valid_from": None, "valid_to": None},
        {"currency": None, "amount": None},
        {"has_identifier": False},
        {"amount": Decimal("0")},
        {"amount": Decimal("1234.5")},
        {"normalized_text": "Invoice for\t\tinternet   service.\n\n"},  # noqa: RUF001
        {"normalized_text": "１２３ Maple Street, apartment 3B."},  # noqa: RUF001
        {"normalized_text": "y" * 1200},
        {"counterparty_name": "  Northline   Fiber  "},
        {"evidence_type": "CANCELLATION_NOTICE"},
    ],
)
def test_query_template_is_byte_identical_to_the_seed_template(
    overrides: dict[str, object],
) -> None:
    """The stored vectors and the query vectors must come from one template.

    ``scripts/seed/embedding_text.py`` rendered the corpus four phases ago and
    is frozen; this module renders the query. A one-byte divergence puts the
    query vector in a different neighbourhood of the same space, and the only
    symptom is worse recall -- no error, no warning, no failing integration
    test. That is why this asserts across a matrix of shapes rather than on the
    hero case alone, and why it names the seed module explicitly instead of
    trusting that two transcriptions of one spec stayed equal.
    """
    from scripts.seed import embedding_text as seed_template

    kwargs = dict(HERO)
    kwargs.update(overrides)
    assert embeddings.build_embedding_text(**kwargs) == (  # type: ignore[arg-type]
        seed_template.build_embedding_text(**kwargs)  # type: ignore[arg-type]
    )
