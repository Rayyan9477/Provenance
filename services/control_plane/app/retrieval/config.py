"""Every tunable constant in the retrieval path, in one file (``T6.1``-``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 19 -- "the complete tunable
  surface, in one file, so a reviewer can see every magic number at once".
- ``docs/specs/10_DATABASE_DDL.md`` section 5.5 -- ``k_raw`` and ``k_final``.
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon* and *Embeddings*.

Two constant sets, and they disagree
------------------------------------
Section 19 prints ``VECTOR_TARGET = 20`` with ``VECTOR_OVERFETCH = 3``, i.e. a
fetch limit of 60. ``10_DATABASE_DDL.md`` section 5.5 -- which section 3 of the
retrieval spec itself declares authoritative over any query in that document --
prints ``k_raw = greatest(40, 4 * k_final)`` with ``k_final = 20``, i.e. 80.
The DDL wins, so :data:`K_RAW` is the value bound into the canonical statement;
:data:`VECTOR_OVERFETCH` is retained under its own name because the eval
harness is told to calibrate *that* ratio. The discrepancy is reported, not
resolved by picking a favourite.

Honest statement about the Stage G weights
------------------------------------------
Section 11.3 says it, ``config.py`` is told to say it, and this is where it is
said: **these nine constants are engineering judgement, not measured
calibration.** They were chosen to produce the correct ordering on the hero
corpus and to encode the product's stated priorities. Nobody should read the
two-decimal precision as the output of an optimisation.

Two embedding spaces, named separately, on purpose
---------------------------------------------------
``CANONICAL_DECISIONS.md`` -> *Gemini model id canon* (2026-08-24) moves new
work to ``gemini-embedding-2`` at 1536 dimensions under ``embedding_version``
``v2``, and says in the same breath why the old constants stay:

    The Titan constants remain reachable in code because the 18,035 vectors
    currently in ``evidence_items`` were rendered by Titan at 1024 dimensions
    and stay uninterpretable without them until the re-embed lands.

So the unqualified triple -- :data:`EMBEDDING_MODEL_ID`,
:data:`EMBEDDING_DIMENSIONS`, :data:`EMBEDDING_VERSION` -- continues to name
**the space that is in the ground today**, which is also the space
``packages/python/provenance_contracts`` freezes by ``Literal`` and the
``VECTOR(1024)`` column enforces. The Gemini space is a second, separately
named triple, and :data:`ACTIVE_EMBEDDING_PROFILE` is the single answer to
"which space is being written now". Nothing in this file quietly re-points an
existing name at a different vector space: that is precisely the substitution
``EMBEDDING_VERSION`` exists to make impossible, and doing it in the constant
that *names* the version would be the purest form of it.

**Every Gemini id here is PROBED**, by invocation rather than by listing:
``ops/gemini-probe.txt`` records ``PASS 11 | FAIL 0 | CANNOT RUN 0``, and
``gemini-embedding-2`` returns 1536 dimensions at L2 norm 1.0000003 there. That
caution was earned -- the last time this pack froze model ids from
documentation, all of them were wrong; see ``CANONICAL_DECISIONS.md`` ->
*Bedrock model id canon*, where ``list-foundation-models`` returned ids that
were not invocable -- which is why the ids were settled by calling them.

Probed is not the same as active. The corpus in the ground is 18,035 vectors in
the ``titan-v1`` space at ``VECTOR(1024)``, and :data:`ACTIVE_EMBEDDING_PROFILE`
still resolves to it. A working id and a populated index are different facts,
and this paragraph used to conflate them in the other direction.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

__all__ = [
    "ACTIVE_EMBEDDING_PROFILE",
    "B_CORROBORATION",
    "B_CORROBORATION_CAP",
    "CASE_STATE_SALIENCE",
    "CONTEXT_TOKEN_BUDGET",
    "DEFAULT_LOOKBACK_DAYS",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_NORM_TOLERANCE",
    "EMBEDDING_PROFILES",
    "EMBEDDING_PROFILES_BY_VERSION",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_PROVIDER_ENV_VAR",
    "EMBEDDING_TEMPLATE_VERSION",
    "EMBEDDING_TIMEOUT_MS",
    "EMBEDDING_VERSION",
    "FUTURE_HORIZON_DAYS",
    "GEMINI_API_KEY_ENV_VAR",
    "GEMINI_EMBEDDING_DIMENSIONS",
    "GEMINI_EMBEDDING_MAX_INPUT_TOKENS",
    "GEMINI_EMBEDDING_MODEL_ID",
    "GEMINI_EMBEDDING_MODEL_ID_CANDIDATES",
    "GEMINI_EMBEDDING_MODEL_ID_DEFAULT",
    "GEMINI_EMBEDDING_MODEL_ID_ENV_VAR",
    "GEMINI_EMBEDDING_TASK_TYPE",
    "GEMINI_EMBEDDING_TIMEOUT_MS",
    "GEMINI_EMBEDDING_VERSION",
    "GEMINI_V2",
    "K_FINAL",
    "K_RAW",
    "MAX_BODY_CHARS",
    "MAX_CASE_CANDIDATES",
    "MAX_EVIDENCE_SNIPPETS",
    "MAX_RELATIONSHIP_CANDIDATES",
    "POSITIVE_WEIGHTS",
    "P_SUPERSEDED",
    "RECENCY_HALF_LIFE_DAYS",
    "RESERVED_CONFLICT_SLOTS",
    "RESERVED_VECTOR_ONLY_SLOTS",
    "SNIPPET_MAX_CHARS",
    "SNIPPET_SQUEEZED_CHARS",
    "TAU_ABSTAIN",
    "TAU_ABSTAIN_DEGRADED",
    "TAU_IDENTITY_ACCEPT",
    "TAU_IDENTITY_MARGIN",
    "TEMPORAL_HALF_LIFE_DAYS",
    "TEMPORAL_SLACK_DAYS",
    "TITAN_V1",
    "VECTOR_FETCH_LIMIT",
    "VECTOR_OVERFETCH",
    "VECTOR_SEARCH_BEAM_SIZE",
    "VECTOR_TARGET",
    "W_AUTHORITY",
    "W_GROUNDING",
    "W_IDENTITY",
    "W_RECENCY",
    "W_STATE",
    "W_TEMPORAL",
    "W_VECTOR",
    "EmbeddingProfile",
    "embedding_profile",
    "embedding_profile_for_version",
    "gemini_model_id_from_env",
    "k_raw_for",
]

# ---- Stage C ---------------------------------------------------------------
TEMPORAL_SLACK_DAYS: Final[int] = 45
DEFAULT_LOOKBACK_DAYS: Final[int] = 540
FUTURE_HORIZON_DAYS: Final[int] = 400

# ---- Stage D: the v1 space, Titan, and what is in the ground today ---------
#: Bare model id. Amazon and third-party models take bare ids on Bedrock; only
#: Anthropic chat models take a ``us.`` inference-profile prefix. The two rules
#: are mirror images (``CANONICAL_DECISIONS.md`` -> Bedrock model id canon), so
#: a client that applied one uniformly could not call both families.
#:
#: **Legacy, and deliberately not renamed.** The 18,035 vectors in
#: ``evidence_items.embedding`` were rendered by this model at this width, and
#: ``provenance_contracts`` freezes the triple by ``Literal``. New work reads
#: :data:`ACTIVE_EMBEDDING_PROFILE`, not these three names.
EMBEDDING_MODEL_ID: Final = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS: Final = 1024
EMBEDDING_TEMPLATE_VERSION: Final = "tmpl1"
EMBEDDING_VERSION: Final = "v1"
EMBEDDING_TIMEOUT_MS: Final[int] = 400
MAX_BODY_CHARS: Final[int] = 900

# ---- Stage D: the v2 space, Gemini ----------------------------------------
#
# CANONICAL_DECISIONS.md -> *Gemini model id canon*, frozen 2026-08-24.

#: PROBE REQUIRED -- documented, not yet invoked.
#:
#: The models page prints ``gemini-embedding-2-preview``; the embeddings page
#: prints ``gemini-embedding-2``. No API key exists, so neither spelling can be
#: settled by invocation, and the canon's migration ``0009`` CHECK admits both
#: for exactly that reason. On Bedrock, *every* documented-but-unprobed id
#: turned out to be wrong -- ``list-foundation-models`` returned ids that were
#: not invocable, Anthropic models needed a ``us.`` prefix and every other
#: provider rejected it. See ``CANONICAL_DECISIONS.md`` -> *Bedrock model id
#: canon*. Treat this string as a hypothesis until ``ops/gemini-probe.txt``
#: records a live call.
GEMINI_EMBEDDING_MODEL_ID_DEFAULT: Final[str] = "gemini-embedding-2"

#: The two spellings a probe may resolve to, and nothing else. A registry of
#: two candidates is honest about the ambiguity; a free-form environment string
#: would let a typo land as a "configuration change" and write 18,035 vectors
#: into a space named by a model that does not exist.
GEMINI_EMBEDDING_MODEL_ID_CANDIDATES: Final[tuple[str, ...]] = (
    "gemini-embedding-2",
    "gemini-embedding-2-preview",
)

GEMINI_EMBEDDING_MODEL_ID_ENV_VAR: Final[str] = "GEMINI_EMBEDDING_MODEL_ID"
GEMINI_API_KEY_ENV_VAR: Final[str] = "GEMINI_API_KEY"
EMBEDDING_PROVIDER_ENV_VAR: Final[str] = "PROVENANCE_EMBEDDING_PROVIDER"


def gemini_model_id_from_env(env: Mapping[str, str]) -> str:
    """Resolve ``GEMINI_EMBEDDING_MODEL_ID``, refusing anything off the list.

    The canon's router obligation is that swapping an id is an environment
    change and never a code change. That is only safe while the set of legal
    values is closed: the whole point of the two-candidate registry is that the
    ``-preview`` question gets answered by a probe rather than by whoever last
    edited a deployment variable.

    A pure function of a mapping rather than a read of ``os.environ`` so it can
    be tested without reloading this module -- a reload would leave every
    importer holding constants from the previous incarnation.

    Raises:
        ValueError: *env* names an id outside
            :data:`GEMINI_EMBEDDING_MODEL_ID_CANDIDATES`.
    """
    model_id = env.get(GEMINI_EMBEDDING_MODEL_ID_ENV_VAR, GEMINI_EMBEDDING_MODEL_ID_DEFAULT)
    if model_id not in GEMINI_EMBEDDING_MODEL_ID_CANDIDATES:
        raise ValueError(
            f"{GEMINI_EMBEDDING_MODEL_ID_ENV_VAR}={model_id!r} is not a Gemini "
            f"embedding id this build recognises. Legal values are "
            f"{GEMINI_EMBEDDING_MODEL_ID_CANDIDATES}; a different model is a "
            "different vector space and needs a new embedding_version, a "
            "migration and a re-embed, not an environment variable."
        )
    return model_id


#: PROBE REQUIRED -- documented, not yet invoked. See the default above.
GEMINI_EMBEDDING_MODEL_ID: Final[str] = gemini_model_id_from_env(os.environ)

#: 1536, from Google's recommended list (768 / 1536 / 3072). Halves storage and
#: index-build cost against the 3072 default at little quality cost under MRL
#: truncation. Requested **explicitly** on every call: the model's own default
#: is 3072, and inheriting a server-side default is how two widths end up in
#: one column.
GEMINI_EMBEDDING_DIMENSIONS: Final[int] = 1536

#: ``v2``. It must differ from :data:`EMBEDDING_VERSION`, and the difference is
#: the entire safety property: ``embedding_version`` covers the model, the
#: dimensionality, the distance function and the normalisation template
#: together, so two spaces sharing one version string would be ranked against
#: each other with no error anywhere.
GEMINI_EMBEDDING_VERSION: Final[str] = "v2"

#: PROBE REQUIRED -- documented, not yet invoked.
#:
#: Symmetric on purpose. Gemini's ``RETRIEVAL_QUERY``/``RETRIEVAL_DOCUMENT``
#: pair deliberately places the two sides of a comparison differently, which is
#: right when a short question is matched against long documents. Here both
#: sides are renders of the same six-header evidence template -- the "query" is
#: an incoming evidence item being matched against stored evidence items -- so
#: the symmetric task type is the correct one. It is a single shared constant
#: because a seed that wrote ``RETRIEVAL_DOCUMENT`` while the control plane
#: sent ``RETRIEVAL_QUERY`` would reproduce the byte-divergence failure that
#: ``tests/retrieval/test_embedding_template.py`` exists to prevent, one layer
#: down where no template diff would show it.
GEMINI_EMBEDDING_TASK_TYPE: Final[str] = "SEMANTIC_SIMILARITY"

#: ``gemini-embedding-2`` raises the input ceiling from 2,048 to 8,192 tokens.
#: Recorded rather than enforced: :data:`MAX_BODY_CHARS` caps the body at 900
#: characters, so the template render is two orders of magnitude inside it.
GEMINI_EMBEDDING_MAX_INPUT_TOKENS: Final[int] = 8192

#: A wider budget than Titan's 400 ms. Bedrock ``us-east-1`` was measured at
#: 0.6-0.8 s per call; the Gemini Developer API is an unprobed public endpoint
#: reached from a different cloud, and a budget set below the real latency
#: would make :class:`EmbeddingUnavailableError` the normal case and the raised
#: abstention floor the normal behaviour.
GEMINI_EMBEDDING_TIMEOUT_MS: Final[int] = 2_000

#: How far the measured L2 norm of a returned vector may sit from 1.0.
#:
#: ``gemini-embedding-2`` auto-normalises truncated dimensions, which is the
#: only reason the canon chose it over ``gemini-embedding-001``. That is a
#: property of Google's model rather than of this code, so it is measured on
#: every call. The tolerance is loose enough that truncate-and-renormalise
#: rounding cannot trip it and tight enough that losing normalisation --
#: which moves the norm by tens of percent -- cannot hide. A guard that fires
#: on float noise is a guard somebody deletes.
EMBEDDING_NORM_TOLERANCE: Final[float] = 1e-4

# ---------------------------------------------------------------------------
# Embedding spaces.
#
# The profiles are DEFINED in ``provenance_contracts.embedding_profile`` and
# only re-exported here. They were briefly declared in both places, which is
# the drift `00_IMPLEMENTATION_MAP.md` section 6 forbids in as many words --
# *"Coding agents must not re-declare JSON shapes independently in multiple
# services"* -- and which this repository has already paid for twice: the
# `23505` map keyed on constraint names the schema never declared, and four
# documents specifying four different repository trees.
#
# `provenance_contracts` is the correct home because the seed
# (`scripts/seed/embeddings.py`) and the control plane must agree on the space
# byte for byte, and neither may import the other.
# ---------------------------------------------------------------------------
from provenance_contracts.embedding_profile import (  # noqa: E402
    EMBEDDING_PROFILES_BY_VERSION,
    EmbeddingProfile,
    profile_for,
    profile_for_version,
    require_writable,
)

#: The space the corpus is in today. 18,035 vectors, ``VECTOR(1024)``.
TITAN_V1: Final[EmbeddingProfile] = profile_for("titan-v1")

#: The space new work writes. UNPROBED -- see the canon section named above.
GEMINI_V2: Final[EmbeddingProfile] = profile_for("gemini-v2")

#: Keyed by **provider**, which is what ``PROVENANCE_EMBEDDING_PROVIDER``
#: carries. The contracts registry is keyed by profile *name*; this is a view
#: over it, not a second registry, so adding a profile there cannot leave this
#: mapping stale.
EMBEDDING_PROFILES: Final[dict[str, EmbeddingProfile]] = {
    TITAN_V1.provider: TITAN_V1,
    GEMINI_V2.provider: GEMINI_V2,
}


#: Local spelling of the contracts helper. The name is kept because the seed
#: and the cache both call it, and renaming a working call site adds risk
#: without adding meaning.
embedding_profile_for_version = profile_for_version


def embedding_profile(provider: str) -> EmbeddingProfile:
    """The profile for *provider*, or a refusal naming the legal values."""
    try:
        return EMBEDDING_PROFILES[provider]
    except KeyError:
        raise ValueError(
            f"{EMBEDDING_PROVIDER_ENV_VAR}={provider!r} is not a known embedding "
            f"provider; expected one of {sorted(EMBEDDING_PROFILES)}"
        ) from None


#: Which space is being written **now**.
#:
#: Defaults to ``bedrock`` and not to ``gemini``, deliberately. Three things
#: must land before the default can flip, and none of them is owned here:
#: migration ``0009`` widening the column to ``VECTOR(1536)``, a
#: ``GOOGLE_API_KEY`` that makes the id probeable, and the ``Literal`` freezes
#: in ``provenance_contracts``. Flipping the default first would make
#: ``make seed`` re-embed 18,035 texts against a model nobody has invoked and
#: insert 1536 floats into a 1024-wide column. The Gemini path is built,
#: tested and one environment variable away; it is not switched on by
#: assumption.
EMBEDDING_PROVIDER: Final[str] = os.environ.get(EMBEDDING_PROVIDER_ENV_VAR, "bedrock")
#: `require_writable` is applied at SELECTION, which is the only moment it is
#: cheap. A profile the database will not accept fails here, before the
#: re-embed — rather than at the first INSERT, after 18,035 texts have been
#: embedded at real cost and roughly an hour unattended. Reading rows written
#: under an unwritable profile stays allowed; `profile_for_version` does not go
#: through this guard, deliberately.
ACTIVE_EMBEDDING_PROFILE: Final[EmbeddingProfile] = require_writable(
    embedding_profile(EMBEDDING_PROVIDER)
)


VECTOR_TARGET: Final[int] = 20
VECTOR_OVERFETCH: Final[int] = 3  # ASSUMPTION, not measured. See section 9.3.
VECTOR_FETCH_LIMIT: Final[int] = VECTOR_TARGET * VECTOR_OVERFETCH
VECTOR_SEARCH_BEAM_SIZE: Final[int] = 8  # DEFAULT. Do not change before section 16.3.

#: ``10_DATABASE_DDL.md`` section 5.5. This is the value bound into the
#: canonical statement, and it is what ``G6.2``'s EXPLAIN runs against.
K_FINAL: Final[int] = 20


def k_raw_for(k_final: int = K_FINAL) -> int:
    """``greatest(40, 4 * k_final)`` -- the DDL section 5.5 over-fetch formula.

    A function rather than a literal because the formula is the contract: a
    reviewer changing ``k_final`` gets the matching ``k_raw`` for free, and
    ``k_raw == k_final`` is the specific bug the over-fetch exists to prevent
    (a run of retracted near-neighbours silently shrinks the result set).
    """
    if k_final < 1:
        raise ValueError("k_final must be at least 1")
    return max(40, 4 * k_final)


K_RAW: Final[int] = k_raw_for()

# ---- Stage G: weights (positives sum to 1.00) ------------------------------
W_IDENTITY: Final[float] = 0.34
W_VECTOR: Final[float] = 0.18
W_AUTHORITY: Final[float] = 0.14
W_STATE: Final[float] = 0.12
W_TEMPORAL: Final[float] = 0.10
W_GROUNDING: Final[float] = 0.07
W_RECENCY: Final[float] = 0.05

#: The seven positive weights, in the order section 11.3 justifies them.
POSITIVE_WEIGHTS: Final[tuple[float, ...]] = (
    W_IDENTITY,
    W_VECTOR,
    W_AUTHORITY,
    W_STATE,
    W_TEMPORAL,
    W_GROUNDING,
    W_RECENCY,
)

P_SUPERSEDED: Final[float] = 0.15
B_CORROBORATION: Final[float] = 0.03
B_CORROBORATION_CAP: Final[float] = 0.06
TEMPORAL_HALF_LIFE_DAYS: Final[float] = 90.0
RECENCY_HALF_LIFE_DAYS: Final[float] = 180.0

#: Section 11.3. ``RESOLVED`` is 0.35 and deliberately not 0.0: the hero
#: scenario turns on retrieving a case resolved four months ago, and zeroing
#: resolved cases would make the product's central claim undemonstrable.
#: ``None`` is unbound evidence -- neutral, not penalised.
CASE_STATE_SALIENCE: Final[dict[str | None, float]] = {
    "REOPENED": 1.00,
    "DISPUTED": 0.95,
    "ACTIONABLE": 0.90,
    "AWAITING_USER": 0.80,
    "IN_PROGRESS": 0.75,
    "WAITING": 0.65,
    "OPEN": 0.60,
    "BLOCKED": 0.55,
    "RESOLVED": 0.35,
    "SUPERSEDED": 0.10,
    None: 0.50,
}

# ---- Stage G: abstention ---------------------------------------------------
TAU_IDENTITY_ACCEPT: Final[float] = 0.90
TAU_IDENTITY_MARGIN: Final[float] = 0.15
TAU_ABSTAIN: Final[float] = 0.42
TAU_ABSTAIN_DEGRADED: Final[float] = 0.62

# ---- Stage H: bounds -------------------------------------------------------
MAX_RELATIONSHIP_CANDIDATES: Final[int] = 3
MAX_CASE_CANDIDATES: Final[int] = 3
MAX_EVIDENCE_SNIPPETS: Final[int] = 10
RESERVED_VECTOR_ONLY_SLOTS: Final[int] = 2
RESERVED_CONFLICT_SLOTS: Final[int] = 3
SNIPPET_MAX_CHARS: Final[int] = 240
SNIPPET_SQUEEZED_CHARS: Final[int] = 120
CONTEXT_TOKEN_BUDGET: Final[int] = 6000
