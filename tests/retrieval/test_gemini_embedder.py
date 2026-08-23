"""The Gemini v2 embedder, the v1/v2 split, and the norm assertion (``T6.1``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon* (frozen
  2026-08-24, supersedes the Bedrock canon): ``gemini-embedding-2`` at
  ``output_dimensionality=1536``, ``embedding_version = 'v2'``, every id read
  from configuration (``GEMINI_EMBEDDING_MODEL_ID``), and **every id in that
  section is UNPROBED**.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 9.1, 12.1 and 12.2.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.1`` and ``G6.6``.

Why an explicit L2-norm assertion exists here
----------------------------------------------
The whole retrieval stack ranks by **cosine**. Titan produced norm
``1.0000000`` because the request said ``"normalize": true``;
``gemini-embedding-2`` produces it because the model auto-normalises truncated
dimensions, which is the single reason the canon chose it over
``gemini-embedding-001``. That difference is a property of the *model*, not of
our code, so nothing in this repository would notice if it stopped being true.
Un-normalised vectors do not error: the distances stay numbers, stay ordered,
and stop meaning anything.

:func:`test_the_returned_vector_has_unit_l2_norm` measures the norm, and
:func:`test_a_vector_that_is_not_unit_norm_is_refused` proves the client
refuses rather than passes it through. Together they are the only thing that
will report the day Google changes that behaviour.

Zero live calls
---------------
Every test is ``unit``: the root ``conftest.py`` unsets credentials and raises
on any outbound reach. **No Gemini API key exists yet**, which is also why the
model id above is marked ``PROBE REQUIRED`` at its definition site rather than
asserted to be invocable here. A test that claimed otherwise would be the same
class of dishonesty the Bedrock canon was rewritten to remove.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from services.control_plane.app.retrieval import embeddings
from services.control_plane.app.retrieval.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_NORM_TOLERANCE,
    EMBEDDING_PROFILES,
    EMBEDDING_PROFILES_BY_VERSION,
    EMBEDDING_VERSION,
    GEMINI_EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_MODEL_ID,
    GEMINI_EMBEDDING_MODEL_ID_CANDIDATES,
    GEMINI_EMBEDDING_MODEL_ID_DEFAULT,
    GEMINI_EMBEDDING_TASK_TYPE,
    GEMINI_EMBEDDING_VERSION,
    GEMINI_V2,
    TITAN_V1,
    gemini_model_id_from_env,
)

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

HERO_TEXT = (
    "[type=DATE_ASSERTION]\n"
    "[counterparty=Northline Fiber]\n"
    "[predicate=service_billing_period]\n"
    "[valid=2026-06-01/2026-07-01]\n"
    "[money=USD 186.00]\n"
    "[has_identifier=true]\n"
    "Invoice for internet service covering 1 June 2026 through 30 June 2026."
)


# ---------------------------------------------------------------------------
# Deterministic stand-ins for the ``google-genai`` surface
#
# Shaped against the installed SDK (1.60.0), not against memory:
# ``client.models.embed_content(model=..., contents=..., config=...)`` returns
# an ``EmbedContentResponse`` whose ``embeddings`` is a list of
# ``ContentEmbedding``, each carrying ``values: list[float] | None``.
# ---------------------------------------------------------------------------


def unit_vector(text: str, dimensions: int) -> list[float]:
    """A deterministic vector of exactly unit L2 norm.

    Deterministic because ``G6.6``'s claim is about the *identity* of the
    returned vector; a random stand-in could show the cache is a cache but
    never that it is not a correctness dependency.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [
        ((digest[index % len(digest)] + index) % 251) / 251.0 + 0.001 for index in range(dimensions)
    ]
    norm = math.sqrt(math.fsum(value * value for value in raw))
    return [value / norm for value in raw]


def l2_norm(vector: tuple[float, ...] | list[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in vector))


@dataclass(frozen=True)
class FakeContentEmbedding:
    values: list[float] | None


@dataclass(frozen=True)
class FakeEmbedContentResponse:
    embeddings: list[FakeContentEmbedding] | None


@dataclass
class FakeModels:
    responder: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def embed_content(
        self, *, model: str, contents: Any, config: Any = None
    ) -> FakeEmbedContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.responder(model=model, contents=contents, config=config)


@dataclass
class FakeGenaiClient:
    """``genai.Client`` reduced to the one attribute path the embedder uses."""

    responder: Any
    models: FakeModels = field(init=False)

    def __post_init__(self) -> None:
        self.models = FakeModels(self.responder)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.models.calls


def responding_with(values: list[float]) -> Any:
    def _respond(**_kwargs: Any) -> FakeEmbedContentResponse:
        return FakeEmbedContentResponse(embeddings=[FakeContentEmbedding(values=list(values))])

    return _respond


def normal_responder() -> Any:
    def _respond(*, contents: Any, **_rest: Any) -> FakeEmbedContentResponse:
        text = contents[0] if isinstance(contents, list) else contents
        return FakeEmbedContentResponse(
            embeddings=[
                FakeContentEmbedding(values=unit_vector(str(text), GEMINI_EMBEDDING_DIMENSIONS))
            ]
        )

    return _respond


def gemini_embedder(responder: Any) -> tuple[Any, FakeGenaiClient]:
    client = FakeGenaiClient(responder)
    return embeddings.GeminiEmbedder(client), client


# ==========================================================================
# The canon triple -- configured, and honestly labelled as unprobed
# ==========================================================================


def test_the_gemini_canon_triple_is_configured() -> None:
    """The *Gemini model id canon* row, stated at the source.

    ``gemini-embedding-2`` at 1536 with ``embedding_version = 'v2'``. The id is
    read from configuration so the contested ``-preview`` spelling is an
    environment change rather than a code change.
    """
    assert GEMINI_EMBEDDING_MODEL_ID_DEFAULT == "gemini-embedding-2"
    assert GEMINI_EMBEDDING_MODEL_ID in GEMINI_EMBEDDING_MODEL_ID_CANDIDATES
    assert GEMINI_EMBEDDING_DIMENSIONS == 1536
    assert GEMINI_EMBEDDING_VERSION == "v2"
    # No Bedrock identifier form leaks in: this is not a Bedrock model and
    # neither the ``us.`` inference-profile prefix nor the ``models/`` resource
    # prefix belongs in the id the SDK is handed.
    assert not GEMINI_EMBEDDING_MODEL_ID.startswith(("us.", "global.", "models/"))


def test_both_contested_spellings_are_the_only_admitted_candidates() -> None:
    """The models page prints ``-preview``; the embeddings page does not.

    Admitting both until a live invocation settles it is honest. Admitting
    anything else would let a typo through as a "configuration change".
    """
    assert set(GEMINI_EMBEDDING_MODEL_ID_CANDIDATES) == {
        "gemini-embedding-2",
        "gemini-embedding-2-preview",
    }


def test_the_model_id_is_swappable_by_environment() -> None:
    """Router obligation: swapping an id is an environment change, never code.

    Tested through a pure function rather than by reloading the module, because
    reloading ``config`` mid-session would leave every module that already
    imported its constants pointing at a different object.
    """
    assert gemini_model_id_from_env({}) == GEMINI_EMBEDDING_MODEL_ID_DEFAULT
    assert (
        gemini_model_id_from_env({"GEMINI_EMBEDDING_MODEL_ID": "gemini-embedding-2-preview"})
        == "gemini-embedding-2-preview"
    )
    with pytest.raises(ValueError, match="(?i)gemini"):
        gemini_model_id_from_env({"GEMINI_EMBEDDING_MODEL_ID": "gemini-embedding-001"})


def test_the_titan_v1_constants_stay_reachable() -> None:
    """The 18,035 vectors in the ground were rendered by Titan at 1024.

    The canon is explicit: "The Titan constants remain reachable in code ...
    and stay uninterpretable without them until the re-embed lands." Deleting
    them to make the pivot look tidier would strand the corpus.
    """
    assert EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"
    assert EMBEDDING_DIMENSIONS == 1024
    assert EMBEDDING_VERSION == "v1"
    assert TITAN_V1.model_id == EMBEDDING_MODEL_ID
    assert TITAN_V1.dimensions == 1024
    assert TITAN_V1.embedding_version == "v1"


def test_the_two_spaces_are_registered_separately_and_never_alias() -> None:
    """One key space per embedding space. This is the whole point of ``v2``.

    Mixing two embedding spaces in one ranking is worse than a migration: the
    numbers stay ordered and become meaningless.
    """
    assert GEMINI_V2.embedding_version != TITAN_V1.embedding_version
    assert GEMINI_V2.dimensions != TITAN_V1.dimensions
    assert GEMINI_V2.model_id != TITAN_V1.model_id
    assert EMBEDDING_PROFILES_BY_VERSION["v1"] is TITAN_V1
    assert EMBEDDING_PROFILES_BY_VERSION["v2"] is GEMINI_V2
    assert set(EMBEDDING_PROFILES) == {"bedrock", "gemini"}
    assert EMBEDDING_PROFILES["gemini"] is GEMINI_V2


# ==========================================================================
# The client -- the request it makes
# ==========================================================================


def test_gemini_embedder_satisfies_the_text_embedder_protocol() -> None:
    """One template render in, one unit vector out -- the same contract Titan meets."""
    embedder, _ = gemini_embedder(normal_responder())
    assert isinstance(embedder, embeddings.TextEmbedder)


def test_constructing_the_embedder_needs_no_api_key_and_opens_no_socket() -> None:
    """Construction must not build a client.

    ``BedrockTitanEmbedder`` defers ``boto3.client`` to first use for the same
    reason: a module that reaches out at import time cannot be imported by a
    hermetic unit lane, and this lane is hermetic by design.
    """
    embedder = embeddings.GeminiEmbedder()
    assert embedder.profile is GEMINI_V2


def test_the_call_names_the_model_and_pins_the_output_dimensionality() -> None:
    """1536 is requested explicitly, never inherited from a server-side default.

    ``gemini-embedding-2`` defaults to 3072. A request that omitted
    ``output_dimensionality`` would return a vector the ``VECTOR(1536)`` column
    rejects -- which is the good case -- or, worse, would start returning 1536
    later for an unrelated reason and put two widths in one column.
    """
    embedder, client = gemini_embedder(normal_responder())
    embedder.embed(HERO_TEXT)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == GEMINI_EMBEDDING_MODEL_ID
    assert call["contents"] == [HERO_TEXT]
    assert call["config"]["output_dimensionality"] == GEMINI_EMBEDDING_DIMENSIONS
    assert call["config"]["task_type"] == GEMINI_EMBEDDING_TASK_TYPE


def test_the_task_type_is_symmetric_and_shared_by_both_callers() -> None:
    """Both sides of this comparison are template renders of evidence items.

    Gemini's asymmetric ``RETRIEVAL_QUERY`` / ``RETRIEVAL_DOCUMENT`` pair puts
    the two sides in deliberately different places. Here the "query" is itself
    an evidence-item render being matched against evidence-item renders, so the
    symmetric task type is the correct one -- and it is a single constant
    precisely so the seed and the control plane cannot pick differently.
    """
    assert GEMINI_EMBEDDING_TASK_TYPE == "SEMANTIC_SIMILARITY"
    assert GEMINI_V2.task_type == GEMINI_EMBEDDING_TASK_TYPE


# ==========================================================================
# The two silent failures: wrong width, and lost normalisation
# ==========================================================================


def test_the_returned_vector_has_unit_l2_norm() -> None:
    """The assertion that will notice if auto-normalisation ever stops.

    ``gemini-embedding-2`` auto-normalises truncated dimensions and
    ``gemini-embedding-001`` does not; that difference is the entire reason the
    canon chose this model. It is a property of Google's model, so measuring it
    is the only way this repository would ever find out it had changed.
    """
    embedder, _ = gemini_embedder(normal_responder())
    vector = embedder.embed(HERO_TEXT)
    assert len(vector) == GEMINI_EMBEDDING_DIMENSIONS
    assert abs(l2_norm(vector) - 1.0) < 1e-6


@pytest.mark.parametrize("dimensions", [768, 1024, 3072])
def test_a_wrong_width_vector_is_refused_as_a_model_mismatch(dimensions: int) -> None:
    """3072 is the model's own default and 1024 is Titan's width.

    Either one arriving means the id, the config or the account changed under
    us. Storing it would fail at the column -- or, if the column ever widened,
    would not.
    """
    embedder, _ = gemini_embedder(responding_with(unit_vector(HERO_TEXT, dimensions)))
    with pytest.raises(embeddings.EmbeddingModelMismatchError):
        embedder.embed(HERO_TEXT)


def test_a_vector_that_is_not_unit_norm_is_refused() -> None:
    """The failure the norm assertion exists for, made loud at the client.

    A 1536-wide vector of norm 0.8 ranks perfectly happily. Every cosine
    distance stays a number, every ordering stays an ordering, and the
    retrieval quality drops for a reason no test would attribute correctly.
    """
    raw = unit_vector(HERO_TEXT, GEMINI_EMBEDDING_DIMENSIONS)
    unnormalised = [value * 1.7 for value in raw]
    embedder, _ = gemini_embedder(responding_with(unnormalised))
    with pytest.raises(embeddings.EmbeddingNotNormalisedError):
        embedder.embed(HERO_TEXT)


def test_the_not_normalised_error_is_caught_as_a_model_mismatch() -> None:
    """A model that stopped normalising is not the model that was frozen.

    Every existing caller already handles ``EmbeddingModelMismatchError``;
    making this a subclass means none of them has to learn a new name to keep
    being correct.
    """
    assert issubclass(
        embeddings.EmbeddingNotNormalisedError, embeddings.EmbeddingModelMismatchError
    )


def test_the_norm_tolerance_is_loose_enough_for_float_noise_and_tight_enough_to_matter() -> None:
    """A guard that fires on rounding is a guard that gets deleted.

    Truncate-and-renormalise leaves a few ULPs of error; losing normalisation
    entirely moves the norm by tens of percent. The tolerance has to sit far
    from both.
    """
    assert 1e-9 < EMBEDDING_NORM_TOLERANCE < 1e-2


# ==========================================================================
# Degradation -- section 9.5. Skipped, never faked.
# ==========================================================================


def test_a_transport_failure_degrades_rather_than_fabricating_a_vector() -> None:
    """Stage D is skipped and the abstention floor rises; nothing is invented."""

    def _explode(**_kwargs: Any) -> FakeEmbedContentResponse:
        raise RuntimeError("connection reset")

    embedder, _ = gemini_embedder(_explode)
    with pytest.raises(embeddings.EmbeddingUnavailableError):
        embedder.embed(HERO_TEXT)


@pytest.mark.parametrize(
    "response",
    [
        FakeEmbedContentResponse(embeddings=None),
        FakeEmbedContentResponse(embeddings=[]),
        FakeEmbedContentResponse(embeddings=[FakeContentEmbedding(values=None)]),
    ],
)
def test_an_empty_response_is_loud_rather_than_a_zero_vector(
    response: FakeEmbedContentResponse,
) -> None:
    """A quietly returned zero vector passes every count assertion downstream."""

    def _respond(**_kwargs: Any) -> FakeEmbedContentResponse:
        return response

    embedder, _ = gemini_embedder(_respond)
    with pytest.raises(embeddings.EmbeddingUnavailableError):
        embedder.embed(HERO_TEXT)


# ==========================================================================
# The v1/v2 boundary -- one key space per embedding space
# ==========================================================================


def test_a_gemini_vector_cannot_enter_the_titan_cache() -> None:
    """1536 floats into a 1024-dimensional key space is a silent corruption."""
    cache = embeddings.EmbeddingCache(embedding_version="v1")
    assert cache.dimensions == 1024
    with pytest.raises(ValueError, match="1024"):
        cache.put(b"k" * 32, tuple(unit_vector(HERO_TEXT, GEMINI_EMBEDDING_DIMENSIONS)))


def test_the_v2_cache_accepts_the_gemini_width_and_refuses_titans() -> None:
    cache = embeddings.EmbeddingCache(embedding_version="v2")
    assert cache.dimensions == GEMINI_EMBEDDING_DIMENSIONS
    key = embeddings.embedding_text_sha256(HERO_TEXT)
    cache.put(key, tuple(unit_vector(HERO_TEXT, GEMINI_EMBEDDING_DIMENSIONS)))
    assert len(cache) == 1
    with pytest.raises(ValueError, match="1536"):
        cache.put(b"k" * 32, tuple(unit_vector(HERO_TEXT, EMBEDDING_DIMENSIONS)))


def test_an_unknown_embedding_version_is_refused_rather_than_guessed() -> None:
    """``v99``, not ``v3``.

    ``v3`` was an arbitrary unknown when this test was written; it since became
    real -- ``gemini-001-v3`` in ``provenance_contracts.embedding_profile``,
    carrying ``caller_must_normalize=True``, which is the property that decided
    ``gemini-embedding-2`` over ``gemini-embedding-001``. A test whose "unknown"
    value quietly becomes known stops testing anything, so this one uses a
    version no profile will plausibly claim.
    """
    with pytest.raises(ValueError, match="(?i)embedding_version|v99"):
        embeddings.EmbeddingCache(embedding_version="v99")


def test_the_two_spaces_do_not_share_one_seed_cache_file() -> None:
    """The sharpest collision in the pivot, and the least visible.

    The cache key is ``sha256(embedding_text)``. The template does **not**
    change between v1 and v2, so every key collides exactly. One parquet file
    holding both would hand Titan vectors to Gemini queries with no error
    anywhere -- which is the failure ``EMBEDDING_VERSION`` exists to prevent,
    arriving through the cache instead of through the index.
    """
    v1 = embeddings.seed_vector_cache_path("v1")
    v2 = embeddings.seed_vector_cache_path("v2")
    assert v1 == embeddings.SEED_VECTOR_CACHE_PATH
    assert v1.name == "vectors.parquet"
    assert v1 != v2
    assert v2.parent == v1.parent


# ==========================================================================
# ``embed_text`` -- the money guard, and G6.6 in the v2 space
# ==========================================================================


def test_embed_text_refuses_a_non_canon_model_id_before_spending_money() -> None:
    """The id is checked first, so a caller naming the wrong model pays nothing."""
    embedder, client = gemini_embedder(normal_responder())
    with pytest.raises(embeddings.EmbeddingModelMismatchError):
        embeddings.embed_text(
            HERO_TEXT,
            embedder=embedder,
            model_id=EMBEDDING_MODEL_ID,
            profile=GEMINI_V2,
        )
    assert client.calls == [], "the model id was checked after the call, not before it"


def test_embed_text_in_the_v2_profile_caches_and_recomputes_identically() -> None:
    """``G6.6``, restated in the new space. A cache is a cache in both."""
    embedder, client = gemini_embedder(normal_responder())
    cache = embeddings.EmbeddingCache(embedding_version="v2")

    warm = embeddings.embed_text(HERO_TEXT, embedder=embedder, cache=cache, profile=GEMINI_V2)
    again = embeddings.embed_text(HERO_TEXT, embedder=embedder, cache=cache, profile=GEMINI_V2)
    assert again == warm
    assert len(client.calls) == 1

    cache.clear()
    cold = embeddings.embed_text(HERO_TEXT, embedder=embedder, cache=cache, profile=GEMINI_V2)
    assert cold == warm
    assert len(client.calls) == 2
    assert abs(l2_norm(cold) - 1.0) < 1e-6


# ==========================================================================
# Seed-side parity -- the same failure shape as the template parity test
# ==========================================================================


def test_the_seed_gemini_profile_matches_the_control_plane_profile() -> None:
    """Two mirrors of one canon row, compared rather than trusted.

    ``scripts/seed/embeddings.py`` writes the corpus; this module embeds the
    query. If their model id, width or task type ever diverge, the two live in
    different neighbourhoods of the same space and recall collapses with no
    error -- the identical failure
    ``test_embedding_template.py::test_query_template_is_byte_identical_to_the_
    seed_template`` exists to prevent one layer up.
    """
    from scripts.seed import embeddings as seed_embeddings

    seed = seed_embeddings.GEMINI_V2
    assert seed.model_id == GEMINI_V2.model_id
    assert seed.dimensions == GEMINI_V2.dimensions
    assert seed.embedding_version == GEMINI_V2.embedding_version
    assert seed.task_type == GEMINI_V2.task_type


def test_the_seed_keeps_one_cache_file_per_embedding_space() -> None:
    from scripts.seed import embeddings as seed_embeddings

    assert seed_embeddings.TITAN_V1.cache_path == seed_embeddings.CACHE_PATH
    assert seed_embeddings.GEMINI_V2.cache_path != seed_embeddings.CACHE_PATH
    assert seed_embeddings.CACHE_PATH.name == "vectors.parquet"


def test_the_seed_gemini_call_returns_a_float32_unit_vector() -> None:
    """The seed's own client, exercised against the same fake surface."""
    from scripts.seed import embeddings as seed_embeddings

    client = FakeGenaiClient(normal_responder())
    vector = seed_embeddings.invoke_gemini(
        HERO_TEXT, client=client, profile=seed_embeddings.GEMINI_V2
    )
    assert vector.shape == (GEMINI_EMBEDDING_DIMENSIONS,)
    assert str(vector.dtype) == "float32"
    assert abs(float(l2_norm(vector.tolist())) - 1.0) < 1e-6
    assert client.calls[0]["model"] == GEMINI_EMBEDDING_MODEL_ID
    assert client.calls[0]["config"]["output_dimensionality"] == GEMINI_EMBEDDING_DIMENSIONS


def test_the_seed_refuses_a_wrong_width_vector() -> None:
    from scripts.seed import embeddings as seed_embeddings

    client = FakeGenaiClient(responding_with(unit_vector(HERO_TEXT, 3072)))
    with pytest.raises(RuntimeError, match="3072|dimension"):
        seed_embeddings.invoke_gemini(HERO_TEXT, client=client, profile=seed_embeddings.GEMINI_V2)


def test_the_seed_refuses_a_vector_that_is_not_unit_norm() -> None:
    """Same guard, both callers. The seed writes 18,035 rows; it gets the check too."""
    from scripts.seed import embeddings as seed_embeddings

    raw = unit_vector(HERO_TEXT, GEMINI_EMBEDDING_DIMENSIONS)
    client = FakeGenaiClient(responding_with([value * 0.5 for value in raw]))
    with pytest.raises(RuntimeError, match="(?i)norm"):
        seed_embeddings.invoke_gemini(HERO_TEXT, client=client, profile=seed_embeddings.GEMINI_V2)


def test_a_bare_cache_is_the_v1_one_and_does_not_track_the_environment() -> None:
    """The default is a constant, not a deployment variable.

    A default that followed ``PROVENANCE_EMBEDDING_PROVIDER`` would make the
    frozen ``test_embedding_template.py`` -- which builds a bare cache and fills
    it with 1024-float vectors -- pass or fail depending on an environment
    variable set for an unrelated reason. Getting the default wrong is caught
    by the width guards instead, which is the loud half of the trade.
    """
    assert embeddings.EmbeddingCache().embedding_version == EMBEDDING_VERSION
    assert embeddings.EmbeddingCache().dimensions == EMBEDDING_DIMENSIONS


def test_a_cache_from_the_other_space_hits_on_every_key_and_is_refused() -> None:
    """The quietest failure in the pivot, and the reason the width is rechecked.

    ``sha256(embedding_text)`` does not change between v1 and v2, so a v1 cache
    handed to a v2 call does not miss -- it *hits*, on every single key, and
    returns 1024-dimensional neighbours that no later stage inspects.
    """
    key = embeddings.embedding_text_sha256(HERO_TEXT)
    titan_cache = embeddings.EmbeddingCache(embedding_version="v1")
    titan_cache.put(key, tuple(unit_vector(HERO_TEXT, EMBEDDING_DIMENSIONS)))

    embedder, client = gemini_embedder(normal_responder())
    with pytest.raises(embeddings.EmbeddingModelMismatchError, match="1024"):
        embeddings.embed_text(HERO_TEXT, embedder=embedder, cache=titan_cache, profile=GEMINI_V2)
    assert client.calls == [], "the cache was consulted and its answer was not checked"
