"""One named, coherent embedding space -- model, width, version, normalization.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon* (frozen 2026-08-24)
  and *Bedrock model id canon* (superseded for new work, retained because the
  corpus on disk was written under it).
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 6: *"Coding agents
  must not re-declare JSON shapes independently in multiple services."* This
  module is that single declaration; ``app/retrieval/config.py`` and
  ``scripts/seed/`` both consume it rather than restating it.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 9.1 -- the frozen properties.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.1``: the corpus carries exactly one
  ``embedding_version``.

Why these values travel together
---------------------------------
``settings.py``'s ``_embedding_contract_frozen`` validator carries a note
written before this pivot existed: the ``Literal`` on a model id is the field
most likely to be widened by a future task, and that validator is what still
fails the build when someone widens the type *without* widening the
``VECTOR(1024)`` column.

Widening ``model_id``, ``dimensions`` and ``embedding_version`` as three
independent fields admits combinations that are individually legal and jointly
meaningless: a Titan id at 1536 dimensions, or Gemini vectors written into a
``VECTOR(1024)`` column. Neither raises. Cosine over two different spaces still
returns ordered numbers, so ranking degrades with nothing reporting a fault.

``caller_must_normalize`` is the field that earns the design
-------------------------------------------------------------
Three models, three different normalization contracts, and the difference is
invisible in the id string:

- ``amazon.titan-embed-text-v2:0`` normalizes server-side when invoked with
  ``"normalize": true``. Measured L2 norm ``1.0000000``.
- ``gemini-embedding-2`` auto-normalizes truncated widths (768, 1536).
- ``gemini-embedding-001`` does **not**. Any width other than 3072 must be
  normalized by the caller.

This stack ranks by cosine. Omitting a required normalization does not raise,
does not warn, and does not change the shape of any result.

The cache-key collision this exists to make impossible
--------------------------------------------------------
The embedding cache is keyed on ``sha256(embedding_text)`` -- the *template
render* -- and **the template is identical across v1 and v2**. Every key in
``db/seeds/vectors.parquet`` therefore collides *exactly* with the key the same
evidence item produces under Gemini. A single shared cache file would not miss;
it would **hit on every key** and serve 1024-dimensional Titan neighbours to a
1536-dimensional Gemini query, with no error anywhere. ``cache_file`` is part of
the profile so the two spaces cannot share one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "ACTIVE_PROFILE_ENV",
    "EMBEDDING_PROFILES",
    "EMBEDDING_PROFILES_BY_VERSION",
    "EmbeddingProfile",
    "profile_for",
    "profile_for_version",
    "require_writable",
]

#: The environment variable that selects the active profile. Named once here so
#: no other module spells it.
#:
#: It said ``PV_EMBEDDING_PROFILE`` until 2026-08-31, which nothing read. The
#: decision is made by ``PROVENANCE_EMBEDDING_PROVIDER`` in
#: ``retrieval/config.py`` and ``scripts/seed/embeddings.py``, so the one place
#: that promised to name the variable once was naming a different variable --
#: and `profile_for`'s error below therefore sent a reader who had mis-set the
#: real knob to go and look at a knob that does nothing.
ACTIVE_PROFILE_ENV: Final[str] = "PROVENANCE_EMBEDDING_PROVIDER"


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """A complete, self-consistent embedding space.

    Frozen because a mutable profile is one a test fixture can change without
    the change being visible at the call site that depended on it.
    """

    name: str
    #: ``bedrock`` or ``gemini``. Selects the *client*, not the space.
    provider: str
    model_id: str
    dimensions: int
    embedding_version: str
    #: What ``VECTOR(n)`` the schema must declare for this space.
    column_width: int
    #: True when the model returns un-normalized vectors and the caller is
    #: obliged to normalize before a cosine comparison.
    caller_must_normalize: bool
    timeout_ms: int
    #: The seed's vector cache for this space. Never shared -- see the module
    #: docstring on the key collision.
    cache_file: str
    #: Whether any id in this profile has actually been invoked.
    probed: bool
    #: Whether the database will accept rows written under this profile.
    #:
    #: False means the model is a legitimate *reading* of existing rows but
    #: ``ck_evidence_embedding_model`` would refuse an INSERT. Selecting such a
    #: profile has to fail before the re-embed, not after: the sequence costs
    #: real spend and roughly an hour unattended, and the first symptom would
    #: otherwise be a CheckViolation on the first row.
    writable: bool
    #: Where the evidence for ``probed`` lives, or would live.
    evidence: str
    #: Gemini only. ``None`` for Bedrock, which has no such parameter.
    task_type: str | None = None

    def __post_init__(self) -> None:
        if self.dimensions != self.column_width:
            raise ValueError(
                f"profile {self.name!r} declares {self.dimensions} dimensions but a "
                f"VECTOR({self.column_width}) column; vectors written under it would "
                "be rejected by the schema or silently truncated"
            )


EMBEDDING_PROFILES: Final[dict[str, EmbeddingProfile]] = {
    # The corpus on disk. 18,035 rows in `evidence_items` were rendered under
    # this profile and stay uninterpretable without it, so it is retained after
    # the pivot rather than deleted.
    "titan-v1": EmbeddingProfile(
        name="titan-v1",
        provider="bedrock",
        model_id="amazon.titan-embed-text-v2:0",
        dimensions=1024,
        embedding_version="v1",
        column_width=1024,
        caller_must_normalize=False,
        timeout_ms=400,
        cache_file="vectors.parquet",
        probed=True,
        writable=True,
        evidence="ops/bedrock-probe.txt (2026-08-17T22:14:20Z, L2 norm 1.0000000)",
    ),
    # Canon after the pivot, PROBED 2026-08-24. `caller_must_normalize=False`
    # is the load-bearing claim here and it is now a measurement rather than a
    # documentation quote: PB-G4 returned an L2 norm of 1.0000003 at 1536,
    # against 0.6935943 for `gemini-embedding-001` in the same run.
    "gemini-v2": EmbeddingProfile(
        name="gemini-v2",
        provider="gemini",
        model_id="gemini-embedding-2",
        dimensions=1536,
        embedding_version="v2",
        column_width=1536,
        caller_must_normalize=False,
        timeout_ms=800,
        cache_file="vectors_v2.parquet",
        probed=True,
        writable=True,
        evidence=(
            "ops/gemini-probe.txt (PB-G4, 2026-08-24): dims=1536 "
            "l2_norm=1.0000003 unit_normalised=True; PB-G5 drift=0.000e+00"
        ),
        # Symmetric, because both sides of every comparison here are renders of
        # the same evidence template. The asymmetric RETRIEVAL_QUERY /
        # RETRIEVAL_DOCUMENT pair would put the query and the corpus in
        # different spaces, which is the same failure one layer down.
        task_type="SEMANTIC_SIMILARITY",
    ),
    # The documented alternative, kept because it is the fallback if
    # `gemini-embedding-2` turns out to be preview-only or rate-limited. Its
    # normalization contract differs, which is precisely why it is a separate
    # profile and not an id swap.
    "gemini-001-v3": EmbeddingProfile(
        name="gemini-001-v3",
        provider="gemini",
        model_id="gemini-embedding-001",
        dimensions=1536,
        embedding_version="v3",
        column_width=1536,
        caller_must_normalize=True,
        timeout_ms=800,
        cache_file="vectors_v3.parquet",
        probed=False,
        # Migration 0009's `ck_evidence_embedding_model` admits only the two
        # `gemini-embedding-2` spellings. That is the canon being enforced, not
        # drift: `-2` was chosen over `001` precisely because `001` does not
        # auto-normalize truncated widths, and a missed normalization in a
        # cosine ranking is silent. Widening the CHECK to admit `001` would undo
        # the argument the choice was made on, so the profile is constrained
        # instead. Kept declared so rows already carrying `v3` stay readable.
        writable=False,
        evidence="ops/gemini-probe.txt (PB-G4) -- NOT YET RUN",
        task_type="SEMANTIC_SIMILARITY",
    ),
}

#: Keyed by ``embedding_version`` so a cache, a row or a query carrying only the
#: version string can still be told how wide its vectors must be.
EMBEDDING_PROFILES_BY_VERSION: Final[dict[str, EmbeddingProfile]] = {
    profile.embedding_version: profile for profile in EMBEDDING_PROFILES.values()
}


def profile_for(name: str) -> EmbeddingProfile:
    """Resolve a profile by name, or refuse.

    Refusing beats defaulting. A typo that silently selected another profile
    would write vectors into one space and rank them against another, and the
    only symptom would be recall quietly collapsing.
    """
    try:
        return EMBEDDING_PROFILES[name]
    except KeyError:
        options = ", ".join(sorted(EMBEDDING_PROFILES))
        raise ValueError(
            f"{ACTIVE_PROFILE_ENV}={name!r} is not a known embedding profile. "
            f"Valid profiles: {options}."
        ) from None


def profile_for_version(embedding_version: str) -> EmbeddingProfile:
    """The profile for an ``embedding_version``, or a refusal.

    Guessing a width for an unknown version is how a short vector reaches a
    cache or an index without anything erroring.
    """
    try:
        return EMBEDDING_PROFILES_BY_VERSION[embedding_version]
    except KeyError:
        known = ", ".join(sorted(EMBEDDING_PROFILES_BY_VERSION))
        raise ValueError(
            f"embedding_version {embedding_version!r} names no embedding space in "
            f"this build; known versions are {known}."
        ) from None


def require_writable(profile: EmbeddingProfile) -> EmbeddingProfile:
    """Return *profile*, or refuse if the database will not accept its rows.

    Called when a profile is **selected**, never when one is read. Refusing to
    write is not refusing to interpret: rows already carrying an unwritable
    profile's ``embedding_version`` must stay readable, or the guard would
    strand exactly the data it exists to protect.

    The refusal names the constraint so a reader can check the claim rather
    than trust it.
    """
    if profile.writable:
        return profile
    raise ValueError(
        f"embedding profile {profile.name!r} ({profile.model_id}) cannot be "
        "written: migration 0009's ck_evidence_embedding_model does not admit "
        f"{profile.model_id!r}. Selecting it would re-embed the corpus -- real "
        "spend, roughly an hour unattended -- and fail at the first INSERT. "
        "Existing rows under this profile remain readable."
    )
