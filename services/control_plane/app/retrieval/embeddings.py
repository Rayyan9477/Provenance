"""The embedding-input template, the two model clients, and the cache (``T6.1``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1 -- the template and its nine
  rules -- and section 9.1's frozen properties.
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon* (frozen
  2026-08-24, supersedes the Bedrock canon for new work):
  ``gemini-embedding-2`` at ``output_dimensionality=1536``,
  ``embedding_version = 'v2'``, id read from ``GEMINI_EMBEDDING_MODEL_ID``.
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*:
  ``amazon.titan-embed-text-v2:0`` by **bare id**, 1024 dimensions,
  ``"normalize": true``, measured L2 norm ``1.0000000``, region ``us-east-1``.
  Still here, because the 18,035 vectors in the ground are Titan's.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.1`` and ``G6.6``.

Two clients, two spaces, and the reason both stay
--------------------------------------------------
:class:`BedrockTitanEmbedder` writes into ``v1`` (1024 dimensions) and
:class:`GeminiEmbedder` into ``v2`` (1536). They are not alternatives to be
chosen per call: each names an embedding space, and the corpus is in exactly
one of them at a time. :data:`~...config.ACTIVE_EMBEDDING_PROFILE` is the one
answer to which. Titan is not deleted because deleting it would leave the
18,035 vectors currently in ``evidence_items.embedding`` uninterpretable.

Normalisation is the difference that has no error path
-------------------------------------------------------
This stack ranks by cosine. Titan returned unit vectors because the request
said ``"normalize": true``. ``gemini-embedding-2`` returns them because the
model auto-normalises truncated dimensions -- which is the whole reason the
canon chose it over ``gemini-embedding-001``, whose 1536-wide output must be
normalised by hand. Forgetting that normalisation is **silent**: every cosine
distance stays a number, every ordering stays an ordering, and the ranking
stops meaning anything. :class:`GeminiEmbedder` therefore *measures* the norm
on every call rather than trusting the model's documented behaviour, and
raises :class:`EmbeddingNotNormalisedError` when it drifts.

One template, two callers, and the failure between them
--------------------------------------------------------
``scripts/seed/embedding_text.py`` rendered the 18,035 vectors now sitting in
``evidence_items.embedding``. This module renders the query vector. **If the two
differ by one byte, the query vector and the index live in different
neighbourhoods of the same space.** Nothing errors; recall quietly collapses.
Both halves keep working, so no integration test notices.

``tests/retrieval/test_embedding_template.py::test_query_template_is_byte_
identical_to_the_seed_template`` runs both implementations over a matrix of
shapes and compares the bytes. That test, not this docstring, is what holds the
two together -- and it is why this module transcribes section 12.1 rather than
paraphrasing it.

Two deliberate deviations from the section 12.1 snippet, both refusals
----------------------------------------------------------------------
1. **A ``float`` amount is refused.** Section 12.1 prints ``f"{amount:.2f}"``,
   which formats a float perfectly happily. Money is ``Decimal`` everywhere
   else in this system for the usual reason, and here it has a second one: a
   currency amount that rounds differently on two machines produces two cache
   keys, two Bedrock calls and two vectors for one document.
2. **Parser JSON is refused.** Section 12.1 rule 7 and section 12.2 forbid it
   at length -- embedding ``{"blocks":[...]}`` makes cosine similarity measure
   schema conformance rather than content, and the failure is silent. A rule
   that is only a paragraph is a rule someone follows until they are in a
   hurry, so it is a structural refusal here.

Neither deviation changes the bytes for any valid input, which is what the
parity test asserts.

Bedrock spend
-------------
:class:`EmbeddingCache` is keyed on ``sha256(embedding_text)`` -- the *template
render*, not ``normalized_text``, because two items with identical prose and
different ``[money=...]`` lines embed differently and must not collide.
``db/seeds/vectors.parquet`` is the seed's cache in that same key space, so the
control plane reads vectors the seed already paid for. ``G6.6`` requires that
clearing the cache yields the identical vector recomputed: the cache is a
performance device and never a correctness dependency, and
:func:`embed_text` is written so that difference is observable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from services.control_plane.app.retrieval.config import (
    ACTIVE_EMBEDDING_PROFILE,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_NORM_TOLERANCE,
    EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_TIMEOUT_MS,
    EMBEDDING_VERSION,
    GEMINI_API_KEY_ENV_VAR,
    GEMINI_V2,
    MAX_BODY_CHARS,
    EmbeddingProfile,
    embedding_profile_for_version,
)

__all__ = [
    "SEED_VECTOR_CACHE_PATH",
    "BedrockTitanEmbedder",
    "EmbeddingCache",
    "EmbeddingCacheMissError",
    "EmbeddingModelMismatchError",
    "EmbeddingNotNormalisedError",
    "EmbeddingUnavailableError",
    "GeminiEmbedder",
    "ParserJsonRefusedError",
    "TextEmbedder",
    "build_embedding_text",
    "embed_text",
    "embedding_text_sha256",
    "l2_norm",
    "seed_vector_cache_path",
]

_SEEDS_DIR: Final[Path] = Path(__file__).resolve().parents[4] / "db" / "seeds"


def seed_vector_cache_path(embedding_version: str = EMBEDDING_VERSION) -> Path:
    """One parquet file per embedding space, and never one for two.

    The cache key is ``sha256(embedding_text)``. The **template does not
    change** between ``v1`` and ``v2``, so every key in the v1 cache collides
    exactly with the key the same evidence item would produce under v2. A
    single file holding both would hand Titan vectors to Gemini queries with no
    error anywhere -- the failure ``EMBEDDING_VERSION`` exists to prevent,
    arriving through the cache rather than through the index.

    ``v1`` keeps the bare ``vectors.parquet`` name because
    ``db/seeds/MANIFEST.json``, ``scripts/seed/manifest.py`` and the committed
    cache all already refer to it by that name.
    """
    profile = embedding_profile_for_version(embedding_version)
    stem = (
        "vectors" if profile.embedding_version == "v1" else f"vectors_{profile.embedding_version}"
    )
    return _SEEDS_DIR / f"{stem}.parquet"


#: The seed's on-disk cache for the ``v1`` corpus. Reading it is what keeps a
#: retrieval test run at zero live model calls.
SEED_VECTOR_CACHE_PATH: Final[Path] = seed_vector_cache_path(EMBEDDING_VERSION)

_WS = re.compile(r"\s+")

#: U+00A0. NFKC leaves it alone, so the template folds it explicitly -- a mail
#: client's non-breaking space must not move the vector.
#: Written as ``chr(0xA0)`` rather than as a literal because a
#: non-breaking space and a space are indistinguishable in a diff, and the
#: difference here is the whole point.
_NBSP: Final[str] = chr(0xA0)


class EmbeddingModelMismatchError(ValueError):
    """A caller asked for a model other than the frozen one.

    ``embedding_version`` covers the model, the dimensionality, the distance
    function *and* the normalisation template together, and every row in the
    index was written under one. A nightly eval that quietly switched models
    would produce vectors in a different space and rank them against this one
    without a single error.
    """


class EmbeddingNotNormalisedError(EmbeddingModelMismatchError):
    """A returned vector's L2 norm is not 1.0, and cosine ranking assumes it is.

    A subclass of :class:`EmbeddingModelMismatchError` rather than a sibling,
    because that is what it means: a model that stopped auto-normalising is not
    the model that was frozen, whatever id it answered to. Every caller that
    already handles a model mismatch keeps being correct without learning a new
    name.

    The reason this is a runtime check and not a comment: ``001`` requires
    manual normalisation at non-3072 widths and ``gemini-embedding-2`` does
    not, so the *only* thing standing between a model-id change and a silently
    meaningless ranking is a measurement.
    """


class EmbeddingCacheMissError(LookupError):
    """No cached vector, and no embedder was supplied.

    Deliberately loud. The alternative -- quietly returning a zero vector --
    produces a system that passes every count assertion and returns nonsense
    from retrieval, which is the worst failure available here because nothing
    downstream notices.
    """


class EmbeddingUnavailableError(RuntimeError):
    """Bedrock failed or timed out. Stage D is skipped, not faked.

    Section 9.5: the abstention floor rises from 0.42 to 0.62 and
    ``EMBEDDING_UNAVAILABLE`` is recorded. Silent degradation here would
    produce confident identity resolutions built on identifier matches alone,
    and the resulting ``PENDING_IDENTITY`` rate would look like a model problem
    rather than an infrastructure one.
    """


class ParserJsonRefusedError(ValueError):
    """Section 12.2. Parser output is never the input to an embedding call."""


@runtime_checkable
class TextEmbedder(Protocol):
    """Anything that turns one template render into one unit vector.

    ``runtime_checkable`` so a test can assert a new client satisfies the
    contract by ``isinstance``. That check is structural -- it sees the method
    name and nothing else -- which is why the unit-norm and width guarantees
    are asserted separately rather than trusted to the Protocol.
    """

    def embed(self, text: str) -> tuple[float, ...]:  # pragma: no cover - protocol
        ...


def l2_norm(vector: Sequence[float]) -> float:
    """The Euclidean norm, summed exactly.

    ``math.fsum`` rather than a plain loop: over 1536 terms the naive sum
    accumulates enough error to make a tight tolerance a source of false
    alarms, and a guard that fires on rounding is a guard somebody deletes.
    """
    return math.sqrt(math.fsum(value * value for value in vector))


def _clean(value: str) -> str:
    """NFKC, fold U+00A0, collapse whitespace, strip. Section 12.1's ``_clean``."""
    normalised = unicodedata.normalize("NFKC", value)
    normalised = normalised.replace(_NBSP, " ")
    return _WS.sub(" ", normalised).strip()


def _looks_like_parser_json(text: str) -> bool:
    """A conservative structural test, not a heuristic on content.

    Only text that *parses* as a JSON object or array is refused. Prose that
    merely contains a brace is not: a false positive here would drop a
    legitimate evidence item out of the index entirely, which is a worse
    failure than the one being prevented.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        parsed = json.loads(stripped)
    except (ValueError, RecursionError):
        return False
    return isinstance(parsed, dict | list)


def build_embedding_text(
    *,
    evidence_type: str,
    counterparty_name: str | None,
    predicate: str | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    currency: str | None,
    amount: Decimal | None,
    has_identifier: bool,
    normalized_text: str,
) -> str:
    """The six fixed header lines plus a capped body. Section 12.1, verbatim.

    Field order is fixed and **total**: absent fields render as ``unknown`` /
    ``none`` / ``false`` and are never omitted, because a missing line shifts
    every downstream token and moves the vector for a reason that has nothing
    to do with meaning.

    Raises:
        TypeError: *amount* is a ``float``. See deviation 1.
        ParserJsonRefusedError: *normalized_text* is parser output. Deviation 2.
    """
    if isinstance(amount, float):
        raise TypeError(
            "embedding input takes Decimal money, never float: a float amount "
            "rounds differently on two machines and produces two cache keys, "
            "two Bedrock calls and two vectors for one document"
        )
    if _looks_like_parser_json(normalized_text):
        raise ParserJsonRefusedError(
            "parser JSON must never be embedded (13_RETRIEVAL_SPEC.md section "
            "12.2): its key names repeat in every artifact, so cosine "
            "similarity starts measuring schema conformance rather than "
            "content, and recall collapses toward random with no error anywhere"
        )

    if valid_from or valid_to:
        low = valid_from.date().isoformat() if valid_from else "open"
        high = valid_to.date().isoformat() if valid_to else "open"
        valid = f"{low}/{high}"
    else:
        valid = "unknown"

    money = f"{currency} {amount:.2f}" if currency and amount is not None else "none"
    body = _clean(normalized_text)[:MAX_BODY_CHARS]

    return (
        f"[type={evidence_type}]\n"
        f"[counterparty={_clean(counterparty_name) if counterparty_name else 'unknown'}]\n"
        f"[predicate={predicate or 'unknown'}]\n"
        f"[valid={valid}]\n"
        f"[money={money}]\n"
        f"[has_identifier={'true' if has_identifier else 'false'}]\n"
        f"{body}"
    )


def embedding_text_sha256(text: str) -> bytes:
    """The cache key. Change the template and every key changes with it.

    Rule 8. Keyed on the template render rather than on ``normalized_text``:
    ``10_DATABASE_DDL.md`` section 17.7 says to key on
    ``normalized_text_sha256``, which would be wrong here because the vector is
    a function of the *embedding input*. The column keeps its specified
    meaning; only the cache key differs. Reported as a discrepancy.
    """
    return hashlib.sha256(text.encode("utf-8")).digest()


class EmbeddingCache:
    """``sha256(embedding_text) -> vector``, for one frozen embedding version.

    The version is part of the identity of the cache, not a field on each
    entry: mixing two embedding spaces under one key space is the failure
    ``EMBEDDING_VERSION`` exists to make impossible. That matters more since
    the Gemini pivot than it did before -- ``sha256(embedding_text)`` is
    identical under v1 and v2 because the *template* did not change, so the two
    key spaces overlap perfectly and a cache that took vectors from both would
    be indistinguishable from a correct one until someone looked at a ranking.

    *embedding_version* defaults to ``v1`` and **not** to the active profile.
    A default that tracked the environment would make this class's behaviour --
    and the frozen ``test_embedding_template.py`` that exercises it -- depend on
    a deployment variable. Getting it wrong is not silent: :meth:`put` refuses a
    wrong-width vector and :func:`embed_text` refuses a wrong-width cache *hit*,
    so a v2 caller that forgets to say so is told immediately rather than
    quietly served 1024-dimensional neighbours.
    """

    __slots__ = ("_vectors", "dimensions", "embedding_version")

    def __init__(
        self,
        vectors: dict[bytes, tuple[float, ...]] | None = None,
        *,
        embedding_version: str = EMBEDDING_VERSION,
    ) -> None:
        self._vectors: dict[bytes, tuple[float, ...]] = dict(vectors or {})
        self.embedding_version = embedding_version
        #: Derived from the version rather than taken from a module constant:
        #: the width a cache accepts is a property of the space it holds, and a
        #: v2 cache silently checking v1's 1024 would let every Gemini vector
        #: through the wrong gate.
        self.dimensions = embedding_profile_for_version(embedding_version).dimensions

    def __len__(self) -> int:
        return len(self._vectors)

    def get(self, key: bytes) -> tuple[float, ...] | None:
        return self._vectors.get(key)

    def put(self, key: bytes, vector: tuple[float, ...]) -> None:
        if len(vector) != self.dimensions:
            raise ValueError(
                f"refusing to cache a {len(vector)}-dimension vector under "
                f"embedding_version {self.embedding_version!r}; that space is "
                f"{self.dimensions}-dimensional and a wrong-width vector would "
                "be a silent ranking corruption"
            )
        self._vectors[key] = vector

    def clear(self) -> None:
        """Empty the cache. ``G6.6`` requires the next read to recompute."""
        self._vectors.clear()

    @classmethod
    def from_parquet(
        cls,
        path: Path | None = None,
        *,
        embedding_version: str = EMBEDDING_VERSION,
    ) -> EmbeddingCache:
        """Load the seed's vectors. Missing file yields an empty cache.

        A missing cache is a cost and a delay, never a correctness problem --
        which is the property ``G6.6`` asserts and the reason this returns
        empty rather than raising.

        *path* defaults to the file for *embedding_version*, not to a constant:
        the v1 and v2 caches share a key space and must never share a file.
        """
        if path is None:
            path = seed_vector_cache_path(embedding_version)
        if not path.is_file():
            return cls(embedding_version=embedding_version)
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        table = pq.read_table(path)
        keys = table.column("text_sha256").to_pylist()
        raw = table.column("embedding").to_pylist()
        vectors = {
            bytes(key): tuple(float(value) for value in values)
            for key, values in zip(keys, raw, strict=True)
        }
        return cls(vectors, embedding_version=embedding_version)


class BedrockTitanEmbedder:
    """``amazon.titan-embed-text-v2:0``, invoked by bare id.

    Amazon and third-party models take bare ids on Bedrock; only Anthropic chat
    models take a ``us.`` inference-profile prefix. The two rules are mirror
    images, so a client that applied one uniformly could not call both
    families.

    **This is a network call and it never happens inside a transaction
    callback.** ``tools/txn_purity_lint.py`` scans for exactly that, and the
    reason is not hygiene: the callback runs once per retry, so a model call
    inside it is charged again on every attempt while the transaction holds its
    locks. Retrieval embeds first and passes the vector in as a bound
    parameter -- which is also, separately, what ``D-06-001`` requires.
    """

    __slots__ = ("_client", "region")

    def __init__(self, client: Any | None = None, *, region: str = "us-east-1") -> None:
        self._client = client
        self.region = region

    def _bedrock(self) -> Any:
        if self._client is None:
            import boto3  # type: ignore[import-untyped]

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def embed(self, text: str) -> tuple[float, ...]:
        """One 1024-float unit vector, or :class:`EmbeddingUnavailableError`."""
        payload = json.dumps(
            {"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True}
        )
        try:
            response = self._bedrock().invoke_model(modelId=EMBEDDING_MODEL_ID, body=payload)
            body = json.loads(response["body"].read())
            vector = tuple(float(value) for value in body["embedding"])
        except Exception as exc:  # degradation is the contract here, so every failure is caught
            raise EmbeddingUnavailableError(
                f"Titan embedding failed within {EMBEDDING_TIMEOUT_MS} ms budget; "
                "Stage D is skipped and the abstention floor rises"
            ) from exc
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingModelMismatchError(
                f"Titan returned {len(vector)} dimensions, expected {EMBEDDING_DIMENSIONS}"
            )
        return vector


class GeminiEmbedder:
    """``gemini-embedding-2`` at 1536 dimensions, via the ``google-genai`` SDK.

    **PROBED.** ``ops/gemini-probe.txt`` invokes this id and records
    ``PASS  gemini-embedding-2  dims=1536 l2_norm=1.0000003 unit=True`` -- the
    width and the unit-normalisation this class depends on, both measured rather
    than read from documentation. ``gemini-embedding-001`` returns 0.6935943 in
    the same run, which is why the width is sent explicitly below. The SDK
    surface is separately verified against
    the installed ``google-genai`` 1.60.0:
    ``client.models.embed_content(model=..., contents=..., config=...)``
    returns an ``EmbedContentResponse`` whose ``embeddings`` is a list of
    ``ContentEmbedding``, each carrying ``values: list[float] | None``.

    Three things this class refuses to leave to the model
    -----------------------------------------------------
    1. **The width.** ``output_dimensionality`` is sent explicitly on every
       call. The model's own default is 3072; a request that omitted it would
       either be rejected by the ``VECTOR(1536)`` column or -- worse, if the
       column ever widened -- put two widths in one index.
    2. **The task type.** Sent explicitly and from a single shared constant, so
       the seed and the control plane cannot pick differently and land the two
       halves of one comparison in different neighbourhoods.
    3. **The norm.** Measured, not assumed. See
       :class:`EmbeddingNotNormalisedError`.

    **This is a network call and it never happens inside a transaction
    callback**, for the same reason as :class:`BedrockTitanEmbedder`: the
    callback runs once per retry, so a model call inside it is charged again on
    every attempt while the transaction holds its locks.
    ``tools/txn_purity_lint.py`` scans for that; the import below is lazy and
    lives inside a method, so it is nowhere near one.
    """

    __slots__ = ("_api_key", "_client", "profile")

    def __init__(
        self,
        client: Any | None = None,
        *,
        api_key: str | None = None,
        profile: EmbeddingProfile = GEMINI_V2,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.profile = profile

    def _genai(self) -> Any:
        """Build the SDK client on first use, never at construction.

        Construction must stay free of credentials and sockets so the hermetic
        unit lane can import and instantiate this class. The key is read here
        rather than stored on the instance so it never appears in a ``repr``.
        """
        if self._client is None:
            from google import genai  # imported lazily: see the class docstring

            api_key = self._api_key or os.environ.get(GEMINI_API_KEY_ENV_VAR)
            if not api_key:
                raise EmbeddingUnavailableError(
                    f"{GEMINI_API_KEY_ENV_VAR} is not set, so no Gemini embedding "
                    "call can be made. Stage D is skipped and the abstention "
                    "floor rises; it is never faked."
                )
            self._client = genai.Client(api_key=api_key)
        return self._client

    def embed(self, text: str) -> tuple[float, ...]:
        """One 1536-float unit vector, or a loud refusal.

        Raises:
            EmbeddingUnavailableError: the call failed, timed out, or returned
                no vector. Stage D is skipped, not faked.
            EmbeddingModelMismatchError: the vector came back the wrong width.
            EmbeddingNotNormalisedError: the vector is not unit norm.
        """
        try:
            response = self._genai().models.embed_content(
                model=self.profile.model_id,
                contents=[text],
                config={
                    "output_dimensionality": self.profile.dimensions,
                    "task_type": self.profile.task_type,
                },
            )
            returned = response.embeddings
            if not returned or returned[0].values is None:
                raise ValueError("the response carried no embedding")
            vector = tuple(float(value) for value in returned[0].values)
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:  # degradation is the contract, so every failure is caught
            raise EmbeddingUnavailableError(
                f"Gemini embedding failed within the {self.profile.timeout_ms} ms "
                "budget; Stage D is skipped and the abstention floor rises"
            ) from exc

        # Deliberately outside the try. A wrong width or a lost normalisation
        # is a statement about *which model answered*, not about whether the
        # network worked, and reporting it as an outage would make the one
        # failure this class exists to catch look like a blip.
        if len(vector) != self.profile.dimensions:
            raise EmbeddingModelMismatchError(
                f"{self.profile.model_id!r} returned {len(vector)} dimensions, "
                f"expected {self.profile.dimensions} for embedding_version "
                f"{self.profile.embedding_version!r}"
            )
        norm = l2_norm(vector)
        if abs(norm - 1.0) > EMBEDDING_NORM_TOLERANCE:
            raise EmbeddingNotNormalisedError(
                f"{self.profile.model_id!r} returned a vector of L2 norm "
                f"{norm:.7f} at {self.profile.dimensions} dimensions. This "
                "stack ranks by cosine and the canon chose this model because "
                "it auto-normalises truncated dimensions; if that stopped "
                "being true, every distance stays a number, every ordering "
                "stays an ordering, and the ranking stops meaning anything."
            )
        return vector


def embed_text(
    text: str,
    *,
    embedder: TextEmbedder | None,
    cache: EmbeddingCache | None = None,
    model_id: str | None = None,
    profile: EmbeddingProfile = ACTIVE_EMBEDDING_PROFILE,
) -> tuple[float, ...]:
    """The one way a query vector is produced.

    The model id is checked **before** anything else, so a caller naming the
    wrong model cannot spend money finding out. Cache hits are served without a
    call; a miss with no embedder raises rather than fabricating a vector.

    *model_id* defaults to *profile*'s own id, so the guard compares a caller's
    claim against the space actually being written rather than against a
    module constant that may name the other one.

    Raises:
        EmbeddingModelMismatchError: *model_id* is not *profile*'s canon id.
        EmbeddingCacheMissError: cache miss with no embedder available.
    """
    if model_id is None:
        model_id = profile.model_id
    if model_id != profile.model_id:
        raise EmbeddingModelMismatchError(
            f"embedding_version {profile.embedding_version!r} / template "
            f"{EMBEDDING_TEMPLATE_VERSION!r} is frozen to {profile.model_id!r}; "
            f"{model_id!r} would write vectors into a different space and rank "
            "them against this one with no error anywhere"
        )
    key = embedding_text_sha256(text)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            # The one place a vector reaches a caller without any model having
            # been asked. ``sha256(embedding_text)`` is identical under v1 and
            # v2 -- the template did not change -- so a cache built for the
            # other space *hits* on every key and returns wrong-width vectors
            # that no downstream stage inspects. The width is the only cheap
            # thing here that distinguishes the two spaces, so it is checked.
            if len(hit) != profile.dimensions:
                raise EmbeddingModelMismatchError(
                    f"cache for embedding_version {cache.embedding_version!r} "
                    f"returned a {len(hit)}-dimension vector while this call is "
                    f"in {profile.embedding_version!r} at {profile.dimensions} "
                    "dimensions. The two spaces share a key space, so a cache "
                    "from the wrong one hits on every key and is silent."
                )
            return hit
    if embedder is None:
        raise EmbeddingCacheMissError(
            "no cached vector for this template render and no embedder was "
            "supplied. Returning a zero vector here would pass every row-count "
            "assertion and return nonsense from retrieval, so this is loud."
        )
    vector = embedder.embed(text)
    if cache is not None:
        cache.put(key, vector)
    return vector
