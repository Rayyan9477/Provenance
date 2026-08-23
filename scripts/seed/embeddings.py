"""Seed embeddings -- Titan v1 and Gemini v2 -- with an on-disk cache (``T2.8``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon* (frozen
  2026-08-24, supersedes the Bedrock canon for new work):
  ``gemini-embedding-2`` at ``output_dimensionality=1536``,
  ``embedding_version = 'v2'``, id read from ``GEMINI_EMBEDDING_MODEL_ID``,
  and **every id in that section is UNPROBED**.
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*:
  ``amazon.titan-embed-text-v2:0``, invoked by **bare id**, 1024 dimensions,
  ``"normalize": true``, verified L2 norm ``1.0000000``, region ``us-east-1``.
- ``docs/specs/10_DATABASE_DDL.md`` section 17.7 rule 5 and section 20 risk 10.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 23, third failure mode.

Two providers, one resolver
---------------------------
The pivot to Gemini does not replace the Titan path; it adds a second one
beside it. The 18,035 vectors in ``evidence_items.embedding`` were rendered by
Titan at 1024 dimensions and stay uninterpretable without its constants, which
is what the canon says in as many words. :data:`ACTIVE_SEED_EMBEDDING_PROFILE`
-- one environment variable -- decides which space a seed run writes.

The trap the profile exists to close: **the cache key does not change between
the two spaces.** It is ``sha256(embedding_text)``, and the template is
identical under v1 and v2, so every key collides exactly. One parquet file
holding both would serve Titan vectors to a Gemini corpus with no error
anywhere. Each profile therefore carries its own cache path, and
:class:`VectorCache` refuses to be pointed at the other one's file.

The failure this module is written against
------------------------------------------
> **Embeddings recomputed on every reseed.** ``db/seeds/vectors.parquet`` must
> be populated at **first** generation. Populating it later means every
> ``make demo-reset && make seed`` -- including the one ``S10`` mandates within
> hours of the deadline -- repeats the full Bedrock spend and its wall-clock
> cost.

Cost is not the binding constraint: 18,035 texts at roughly forty tokens each is
about 720k tokens, single-digit US cents. **Time** is. Sequential invocation
measured 0.6-0.8 s per call against ``us-east-1``, which is over three hours for
the corpus. The pool below turns that into minutes, and the cache turns every
subsequent run into seconds.

Why the cache is keyed on the template render, not on ``normalized_text``
-------------------------------------------------------------------------
The vector is a function of the **embedding input**, which is
``13_RETRIEVAL_SPEC.md`` section 12.1's six header lines plus the capped body --
not of ``normalized_text`` alone. Two evidence items with identical body text
but different ``[money=...]`` lines embed differently and must not share a cache
entry. Section 17.7 says to key on ``normalized_text_sha256``; doing so would be
wrong for exactly that reason, and the discrepancy is reported rather than
silently followed. The database column keeps its specified meaning
(``sha256(normalized_text)``); only the cache key differs.

Why the identifier form matters here
------------------------------------
Anthropic chat models are invoked by inference-profile id and reject the bare
form; every other provider is the mirror image and rejects the profile form.
Titan is an Amazon model, so the id below carries **no** ``us.`` prefix. A
client that applied one rule uniformly could not call both families. The Gemini
Developer API is a third form again -- a plain model name, no prefix of any
kind -- which is the reason the id is passed through from configuration
unmodified rather than assembled.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from scripts.seed.embedding_text import EMBEDDING_MODEL_ID, EMBEDDING_VERSION, embedding_text_sha256

#: A single embedding: ``profile.dimensions`` float32 components, unit norm.
Vector = np.ndarray[Any, np.dtype[np.float32]]

__all__ = [
    "ACTIVE_SEED_EMBEDDING_PROFILE",
    "CACHE_PATH",
    "EMBEDDING_DIMENSIONS",
    "GEMINI_CACHE_PATH",
    "GEMINI_EMBEDDING_DIMENSIONS",
    "GEMINI_EMBEDDING_MODEL_ID",
    "GEMINI_EMBEDDING_MODEL_ID_CANDIDATES",
    "GEMINI_EMBEDDING_MODEL_ID_DEFAULT",
    "GEMINI_EMBEDDING_TASK_TYPE",
    "GEMINI_EMBEDDING_VERSION",
    "GEMINI_V2",
    "NORM_TOLERANCE",
    "SEED_EMBEDDING_PROFILES",
    "TITAN_V1",
    "EmbeddingCacheMissError",
    "EmbeddingResolver",
    "SeedEmbeddingProfile",
    "VectorCache",
    "invoke_gemini",
    "seed_embedding_profile",
]

_SEEDS_DIR = Path(__file__).resolve().parents[2] / "db" / "seeds"

#: ``db/seeds/vectors.parquet`` -- ``T2.8``'s "Creates" list names this path.
#: The v1 cache keeps the bare name because ``db/seeds/MANIFEST.json``,
#: ``scripts/seed/manifest.py`` and the committed artifact all refer to it.
CACHE_PATH = _SEEDS_DIR / "vectors.parquet"

#: The v2 cache. A **separate file**, because the two spaces share a key space.
GEMINI_CACHE_PATH = _SEEDS_DIR / "vectors_v2.parquet"

EMBEDDING_DIMENSIONS = 1024
DEFAULT_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# The Gemini v2 space
#
# Mirrored here rather than imported from
# services/control_plane/app/retrieval/config.py: the seed package is
# deliberately self-contained (see scripts/seed/embedding_text.py, which
# transcribes the template for the same reason), and
# tests/retrieval/test_gemini_embedder.py::
# test_the_seed_gemini_profile_matches_the_control_plane_profile compares the
# two mirrors field by field so the copy is checked rather than trusted.
# ---------------------------------------------------------------------------

#: PROBE REQUIRED -- documented, not yet invoked.
#:
#: The models page prints ``gemini-embedding-2-preview``; the embeddings page
#: prints ``gemini-embedding-2``. There is no API key, so nothing here has been
#: settled by invocation. On Bedrock, every documented-but-unprobed id turned
#: out to be wrong -- ``list-foundation-models`` returned ids that were not
#: invocable (``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*). Treat
#: this as a hypothesis until ``ops/gemini-probe.txt`` records a live call.
GEMINI_EMBEDDING_MODEL_ID_DEFAULT = "gemini-embedding-2"
GEMINI_EMBEDDING_MODEL_ID_CANDIDATES = ("gemini-embedding-2", "gemini-embedding-2-preview")
GEMINI_EMBEDDING_MODEL_ID = os.environ.get(
    "GEMINI_EMBEDDING_MODEL_ID", GEMINI_EMBEDDING_MODEL_ID_DEFAULT
)
GEMINI_EMBEDDING_DIMENSIONS = 1536
GEMINI_EMBEDDING_VERSION = "v2"

#: Symmetric, and shared with the control plane. Both sides of every comparison
#: in this system are renders of the same evidence template, so the asymmetric
#: ``RETRIEVAL_QUERY``/``RETRIEVAL_DOCUMENT`` pair would place them in
#: deliberately different places for no benefit -- and a seed that chose
#: differently from the query path would reproduce the template-divergence
#: failure one layer down, where no byte diff would show it.
GEMINI_EMBEDDING_TASK_TYPE = "SEMANTIC_SIMILARITY"

#: How far a returned vector's L2 norm may sit from 1.0. See
#: ``services/control_plane/app/retrieval/config.EMBEDDING_NORM_TOLERANCE``:
#: loose enough that renormalisation rounding cannot trip it, tight enough that
#: losing normalisation entirely cannot hide.
NORM_TOLERANCE = 1e-4

_GEMINI_API_KEY_ENV_VAR = "GEMINI_API_KEY"
_EMBEDDING_PROVIDER_ENV_VAR = "PROVENANCE_EMBEDDING_PROVIDER"

#: Concurrency for the cold path. High enough that 18,035 calls finish in
#: minutes, low enough that Bedrock's on-demand quota answers rather than
#: throttles. Overridable for a slower account.
DEFAULT_WORKERS = int(os.environ.get("PROVENANCE_SEED_EMBED_WORKERS", "24"))

_MAX_ATTEMPTS = 6

#: Vectors between on-disk checkpoints of the cache. Each checkpoint rewrites
#: the whole parquet, so this trades a handful of ~100 MB writes against losing
#: up to this many Bedrock calls to an interruption.
CHECKPOINT_EVERY = 4_000
_THREAD_LOCAL = threading.local()


class EmbeddingCacheMissError(RuntimeError):
    """Raised in ``cache-only`` mode when a text has no cached vector.

    Deliberately loud. The alternative -- quietly writing a zero vector -- would
    produce a database that passes every row-count assertion and returns
    nonsense from retrieval, which is the worst failure this seed can have
    because nothing downstream would notice.
    """


# ---------------------------------------------------------------------------
# Profiles -- one per embedding space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedEmbeddingProfile:
    """One embedding space: a model, a width, a version, and its cache file.

    Frozen and passed as a unit because the fields are only ever correct
    together. A resolver that took ``model_id`` and ``dimensions`` separately
    can be handed Titan's id with Gemini's width, and the result is a parquet
    file of the wrong-shaped vectors under the right-looking name.
    """

    provider: str
    model_id: str
    dimensions: int
    embedding_version: str
    cache_path: Path
    #: Gemini only. ``None`` for Bedrock, which has no such parameter.
    task_type: str | None = None


#: The space the committed corpus is in. Verified L2 norm ``1.0000000``.
TITAN_V1 = SeedEmbeddingProfile(
    provider="bedrock",
    model_id=EMBEDDING_MODEL_ID,
    dimensions=EMBEDDING_DIMENSIONS,
    embedding_version=EMBEDDING_VERSION,
    cache_path=CACHE_PATH,
)

#: The space new work writes. UNPROBED -- see the model id above.
GEMINI_V2 = SeedEmbeddingProfile(
    provider="gemini",
    model_id=GEMINI_EMBEDDING_MODEL_ID,
    dimensions=GEMINI_EMBEDDING_DIMENSIONS,
    embedding_version=GEMINI_EMBEDDING_VERSION,
    cache_path=GEMINI_CACHE_PATH,
    task_type=GEMINI_EMBEDDING_TASK_TYPE,
)

SEED_EMBEDDING_PROFILES = {"bedrock": TITAN_V1, "gemini": GEMINI_V2}

_PROFILE_BY_CACHE_PATH = {
    profile.cache_path: profile for profile in SEED_EMBEDDING_PROFILES.values()
}


def seed_embedding_profile(provider: str) -> SeedEmbeddingProfile:
    """The profile for *provider*, or a refusal naming the legal values."""
    try:
        return SEED_EMBEDDING_PROFILES[provider]
    except KeyError:
        raise ValueError(
            f"{_EMBEDDING_PROVIDER_ENV_VAR}={provider!r} is not a known embedding "
            f"provider; expected one of {sorted(SEED_EMBEDDING_PROFILES)}"
        ) from None


#: Which space a seed run writes.
#:
#: Defaults to ``bedrock``, deliberately. Flipping the default before migration
#: ``0009`` widens the column to ``VECTOR(1536)`` and before a
#: ``GEMINI_API_KEY`` exists would make ``make seed`` re-embed 18,035 texts
#: against a model nobody has invoked and then fail on insert. The Gemini path
#: is one environment variable away; it is not switched on by assumption.
ACTIVE_SEED_EMBEDDING_PROFILE = seed_embedding_profile(
    os.environ.get(_EMBEDDING_PROVIDER_ENV_VAR, "bedrock")
)


# ---------------------------------------------------------------------------
# The on-disk cache
# ---------------------------------------------------------------------------


@dataclass
class VectorCache:
    """A parquet file mapping ``sha256(embedding_text)`` to one vector.

    The width, the model id and the embedding version stamped into the file all
    come from :attr:`profile`, never from a module constant, because this class
    now serves two spaces whose keys are indistinguishable.
    """

    path: Path = CACHE_PATH
    profile: SeedEmbeddingProfile = TITAN_V1
    vectors: dict[bytes, Vector] = field(default_factory=dict)
    _dirty: bool = False

    def __post_init__(self) -> None:
        """Refuse a path that belongs to the *other* space.

        A tmp path is fine -- tests need one. Pointing the Gemini profile at
        ``vectors.parquet`` is not: the keys match, so nothing would error and
        1024-float vectors would be served to a 1536-dimensional index.
        """
        owner = _PROFILE_BY_CACHE_PATH.get(self.path)
        if owner is not None and owner is not self.profile:
            raise ValueError(
                f"{self.path.name} is the {owner.embedding_version} cache and this "
                f"is the {self.profile.embedding_version} profile. The two spaces "
                "share a key space -- sha256(embedding_text) is identical under "
                "both -- so one file holding both would be a silent ranking "
                "corruption rather than an error."
            )

    @classmethod
    def for_profile(cls, profile: SeedEmbeddingProfile) -> VectorCache:
        """The cache belonging to *profile*, at that profile's own file."""
        return cls(path=profile.cache_path, profile=profile)

    def load(self) -> VectorCache:
        if not self.path.is_file():
            return self
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        table = pq.read_table(self.path)
        keys = table.column("text_sha256").to_pylist()
        raw = table.column("embedding").to_pylist()
        for key, values in zip(keys, raw, strict=True):
            self.vectors[bytes(key)] = np.asarray(values, dtype=np.float32)
        return self

    def save(self) -> None:
        """Write atomically: a half-written cache is worse than no cache."""
        if not self._dirty and self.path.is_file():
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        keys = sorted(self.vectors)
        table = pa.table(
            {
                "text_sha256": pa.array(keys, type=pa.binary()),
                "embedding": pa.array(
                    [self.vectors[k].tolist() for k in keys],
                    type=pa.list_(pa.float32(), self.profile.dimensions),
                ),
                "model_id": pa.array([self.profile.model_id] * len(keys), type=pa.string()),
                "embedding_version": pa.array(
                    [self.profile.embedding_version] * len(keys), type=pa.string()
                ),
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(self.path)
        self._dirty = False

    def put(self, key: bytes, vector: Vector) -> None:
        self.vectors[key] = vector
        self._dirty = True

    def __contains__(self, key: bytes) -> bool:
        return key in self.vectors

    def __len__(self) -> int:
        return len(self.vectors)

    def content_sha256(self) -> str:
        """A hash over the cache's *contents*, not its bytes.

        ``10_DATABASE_DDL.md`` section 20 risk 10: "Generate the cache once,
        commit its manifest hash, and treat the cache directory as a build
        artifact." Hashing the parquet file itself would not serve that purpose:
        the bytes move with the pyarrow version and the compression codec, so a
        colleague regenerating an identical cache would get a different digest
        and read it as corruption. Hashing the sorted (key, float32 bytes) pairs
        is a statement about the vectors, which is the thing that has to be
        identical for an eval number to be comparable.
        """
        digest = hashlib.sha256()
        for key in sorted(self.vectors):
            digest.update(key)
            digest.update(self.vectors[key].astype(np.float32).tobytes())
        return digest.hexdigest()


# ---------------------------------------------------------------------------
# Bedrock -- the v1 path
# ---------------------------------------------------------------------------


def _client(region: str) -> Any:
    """One ``bedrock-runtime`` client per thread.

    botocore clients are documented as safe to *call* from multiple threads, but
    the shared connection pool serialises under load and the seed's whole point
    here is concurrency. A client per worker is a few kilobytes each.
    """
    existing = getattr(_THREAD_LOCAL, "client", None)
    if existing is None:
        import boto3  # type: ignore[import-untyped]

        existing = boto3.client("bedrock-runtime", region_name=region)
        _THREAD_LOCAL.client = existing
    return existing


def invoke_titan(text: str, region: str) -> Vector:
    """One Titan v2 call. No retry -- :func:`_invoke` owns that.

    ``normalize: true`` is not decoration: the ANN index is
    ``vector_cosine_ops`` and unit vectors make cosine and L2 rank identically,
    which is the property ``ops/decisions/VECTOR_INDEX_VARIANT.md`` records as
    "a free property rather than a correctness dependency".
    """
    body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True})
    response = _client(region).invoke_model(modelId=EMBEDDING_MODEL_ID, body=body)
    payload = json.loads(response["body"].read())
    vector: Vector = np.asarray(payload["embedding"], dtype=np.float32)
    if vector.shape != (EMBEDDING_DIMENSIONS,):
        raise RuntimeError(f"Titan returned {vector.shape}, expected {EMBEDDING_DIMENSIONS}")
    return vector


# ---------------------------------------------------------------------------
# Gemini -- the v2 path
# ---------------------------------------------------------------------------


def _gemini_client() -> Any:
    """One ``genai.Client`` per thread, built on first use.

    Same reasoning as :func:`_client`: the SDK client wraps an ``httpx``
    connection pool that serialises under load, and this module's whole purpose
    is concurrency. The key is read here rather than at import so the module
    stays importable without credentials.
    """
    existing = getattr(_THREAD_LOCAL, "gemini_client", None)
    if existing is None:
        from google import genai  # imported lazily: no key exists in most runs

        api_key = os.environ.get(_GEMINI_API_KEY_ENV_VAR)
        if not api_key:
            raise RuntimeError(
                f"{_GEMINI_API_KEY_ENV_VAR} is not set, so no Gemini embedding call "
                "can be made. Run with --embeddings cache-only, or set the key."
            )
        existing = genai.Client(api_key=api_key)
        _THREAD_LOCAL.gemini_client = existing
    return existing


def invoke_gemini(
    text: str,
    *,
    client: Any | None = None,
    profile: SeedEmbeddingProfile = GEMINI_V2,
) -> Vector:
    """One ``gemini-embedding-2`` call. No retry -- :func:`_invoke` owns that.

    **UNPROBED.** No API key exists, so neither the model id nor this request
    shape has been settled by invocation. The SDK surface *is* verified against
    the installed ``google-genai`` 1.60.0.

    ``output_dimensionality`` is sent explicitly because the model's own
    default is 3072, and the returned norm is *measured* because
    ``gemini-embedding-2`` auto-normalising truncated dimensions is a property
    of Google's model rather than of this code. That is the single reason the
    canon chose it over ``gemini-embedding-001``, and a corpus of 18,035
    un-normalised vectors would rank without erring and mean nothing.
    """
    sdk = client if client is not None else _gemini_client()
    response = sdk.models.embed_content(
        model=profile.model_id,
        contents=[text],
        config={
            "output_dimensionality": profile.dimensions,
            "task_type": profile.task_type,
        },
    )
    returned = response.embeddings
    if not returned or returned[0].values is None:
        raise RuntimeError(
            f"{profile.model_id} returned no embedding for a text of {len(text)} chars"
        )
    vector: Vector = np.asarray(returned[0].values, dtype=np.float32)
    if vector.shape != (profile.dimensions,):
        raise RuntimeError(
            f"{profile.model_id} returned {vector.shape[0]} dimensions, expected "
            f"{profile.dimensions} for embedding_version {profile.embedding_version}"
        )
    wide = vector.astype(np.float64)
    norm = float(np.sqrt(np.dot(wide, wide)))
    if abs(norm - 1.0) > NORM_TOLERANCE:
        raise RuntimeError(
            f"{profile.model_id} returned a vector of L2 norm {norm:.7f}; the ANN "
            "index is vector_cosine_ops and a corpus of un-normalised vectors "
            "ranks perfectly happily while meaning nothing"
        )
    return vector


# ---------------------------------------------------------------------------
# Dispatch and retry
# ---------------------------------------------------------------------------


def _invoke(text: str, region: str, profile: SeedEmbeddingProfile) -> Vector:
    """One embedding call for *profile*, with bounded retry on throttling.

    Retry lives here rather than in either client so both providers get the
    identical jittered backoff. Rate limits on the Gemini Developer API are
    unprobed and the canon flags them as a live risk: re-embedding 18,035 texts
    is the longest unattended job in the plan.
    """
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            if profile.provider == "gemini":
                return invoke_gemini(text, profile=profile)
            return invoke_titan(text, region)
        except Exception as exc:
            last = exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            # Jitter, because 24 workers backing off in lockstep is one worker.
            time.sleep(min(2**attempt, 20) * (0.5 + random.random()))
    raise RuntimeError(
        f"{profile.model_id} embedding failed after {_MAX_ATTEMPTS} attempts: {last}"
    )


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingResolver:
    """Cache-first embedding resolution with a reported call count.

    The call count is not a diagnostic nicety. ``T2.8``'s acceptance requires
    stating how many live Bedrock calls a first run and a second run make, and a
    number that is derived from the run rather than asserted in prose is the
    only kind worth reporting.
    """

    mode: str = "live"
    region: str = DEFAULT_REGION
    workers: int = DEFAULT_WORKERS
    profile: SeedEmbeddingProfile = ACTIVE_SEED_EMBEDDING_PROFILE
    cache: VectorCache | None = None
    live_calls: int = 0
    cache_hits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        """Resolve the cache from the profile, so the two cannot disagree.

        A default-constructed :class:`VectorCache` would silently be the v1 one
        whatever profile this resolver carries, which is exactly the collision
        the separate cache files exist to prevent.
        """
        if self.cache is None:
            self.cache = VectorCache.for_profile(self.profile).load()
        elif self.cache.profile is not self.profile:
            raise ValueError(
                f"resolver profile {self.profile.embedding_version} was handed a "
                f"{self.cache.profile.embedding_version} cache; one key space per "
                "embedding space, always"
            )

    @property
    def vector_cache(self) -> VectorCache:
        """:attr:`cache`, narrowed to non-``None``.

        The field is optional only so that ``__post_init__`` can resolve it
        from :attr:`profile`; a dataclass ``default_factory`` cannot see the
        other fields, and a default cache that ignored the profile would be the
        v1 one under a v2 resolver -- the exact collision the two files exist
        to prevent.
        """
        cache = self.cache
        if cache is None:  # pragma: no cover - __post_init__ guarantees this
            raise RuntimeError("EmbeddingResolver.__post_init__ did not run")
        return cache

    def resolve(self, texts: Sequence[str], *, label: str = "") -> dict[bytes, Vector]:
        """Return ``{sha256(text): vector}`` for every text, embedding on miss."""
        keys = [embedding_text_sha256(t) for t in texts]
        wanted = dict(zip(keys, texts, strict=True))
        missing = [(k, t) for k, t in wanted.items() if k not in self.vector_cache]
        self.cache_hits += len(wanted) - len(missing)

        if missing and self.mode == "cache-only":
            raise EmbeddingCacheMissError(
                f"{len(missing)} of {len(wanted)} texts have no cached vector and "
                f"--embeddings cache-only forbids a Bedrock call. Run once with "
                f"--embeddings live to populate {self.vector_cache.path}."
            )

        if missing:
            self._embed_missing(missing, label=label or "texts")
            self.vector_cache.save()

        return {k: self.vector_cache.vectors[k] for k in wanted}

    def _embed_missing(self, missing: list[tuple[bytes, str]], *, label: str) -> None:
        total = len(missing)
        started = time.perf_counter()
        done = 0
        checkpoint = False

        def _one(item: tuple[bytes, str]) -> tuple[bytes, Vector]:
            key, text = item
            return key, _invoke(text, self.region, self.profile)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for key, vector in pool.map(_one, missing):
                with self._lock:
                    self.vector_cache.put(key, vector)
                    self.live_calls += 1
                    done += 1
                    if done % 500 == 0 or done == total:
                        rate = done / max(time.perf_counter() - started, 1e-6)
                        print(
                            f"    embeddings {label}: {done}/{total} "
                            f"({rate:.0f}/s, {len(self.vector_cache)} cached)",
                            flush=True,
                        )
                    # Checkpoint. Section 17.7 rule 5 requires the cache to
                    # "resume after interruption", and a cache written only on
                    # success does not: a corpus that takes twenty-five minutes
                    # to embed will eventually be interrupted, and losing all of
                    # it is the difference between a two-minute retry and paying
                    # the whole Bedrock spend again.
                    if done % CHECKPOINT_EVERY == 0:
                        checkpoint = True
                if checkpoint:
                    self.vector_cache.save()
                    checkpoint = False

    def warm(self, texts: Iterable[str], *, label: str = "") -> None:
        """Resolve without keeping the result, for a pure cache-population run."""
        self.resolve(list(texts), label=label)
