"""The embedding profile: one named, coherent tuple instead of four free fields.

Why a profile rather than four widened ``Literal``s
---------------------------------------------------
``settings.py``'s ``_embedding_contract_is_frozen`` validator carries a note
written for exactly this moment:

    the ``Literal`` on a model id is the thing most likely to be widened by a
    future task (a second embedding model, a second version), and this
    validator is what still fails the build when someone widens the type
    without widening the ``VECTOR(1024)`` column.

Widening ``model_id``, ``dimensions`` and ``version`` as three independent
fields would admit incoherent combinations -- a Titan model id at 1536
dimensions, or Gemini vectors written into a ``VECTOR(1024)`` column. Both
would be accepted at startup and both fail silently at ranking time, because
cosine over mismatched spaces still returns ordered numbers.

Binding the four values into one named profile makes the incoherent
combinations unrepresentable rather than merely discouraged.

The normalization field is the sharp one
-----------------------------------------
``amazon.titan-embed-text-v2:0`` normalizes server-side (``"normalize": true``,
measured L2 norm 1.0000000). ``gemini-embedding-2`` auto-normalizes truncated
widths. ``gemini-embedding-001`` **does not** -- it requires the caller to
normalize any width other than 3072. That difference is invisible in a model id
string and fatal to a cosine ranking, so it is a typed field on the profile.
"""

from __future__ import annotations

import pytest

from provenance_contracts.embedding_profile import (
    ACTIVE_PROFILE_ENV,
    EMBEDDING_PROFILES,
    EMBEDDING_PROFILES_BY_VERSION,
    EmbeddingProfile,
    profile_for,
)

pytestmark = pytest.mark.unit


class TestTheProfilesThemselves:
    def test_the_legacy_titan_profile_matches_the_corpus_on_disk(self) -> None:
        """18,035 vectors in ``evidence_items`` were rendered by this profile.

        They stay uninterpretable without it, which is why it is not deleted.
        """
        titan = EMBEDDING_PROFILES["titan-v1"]
        assert titan.model_id == "amazon.titan-embed-text-v2:0"
        assert titan.dimensions == 1024
        assert titan.embedding_version == "v1"

    def test_the_gemini_profile_matches_the_canon(self) -> None:
        gemini = EMBEDDING_PROFILES["gemini-v2"]
        assert gemini.model_id == "gemini-embedding-2"
        assert gemini.dimensions == 1536
        assert gemini.embedding_version == "v2"

    def test_every_profile_declares_a_column_width_equal_to_its_dimensions(self) -> None:
        """The mismatch this guards is the one that ranks in the wrong space."""
        for name, profile in EMBEDDING_PROFILES.items():
            assert profile.dimensions == profile.column_width, name

    def test_every_profile_has_a_distinct_version(self) -> None:
        """Two profiles sharing a version would mix spaces in one index.

        ``G6.1`` asserts the corpus carries exactly one ``embedding_version``.
        That assertion is only meaningful if the version identifies the space.
        """
        versions = [p.embedding_version for p in EMBEDDING_PROFILES.values()]
        gemini_versions = [
            p.embedding_version
            for p in EMBEDDING_PROFILES.values()
            if p.model_id.startswith("gemini")
        ]
        assert len(set(versions)) >= 2
        # Both Gemini profiles write into the same space only if they are the
        # same model; they are not, so they must not share a version.
        assert len(set(gemini_versions)) == len(gemini_versions)


class TestNormalizationIsTypedNotDocumented:
    """The failure this prevents is silent, so it must not live in prose."""

    def test_titan_normalizes_server_side(self) -> None:
        assert EMBEDDING_PROFILES["titan-v1"].caller_must_normalize is False

    def test_gemini_embedding_2_auto_normalizes_truncated_widths(self) -> None:
        assert EMBEDDING_PROFILES["gemini-v2"].caller_must_normalize is False

    def test_gemini_embedding_001_requires_the_caller_to_normalize(self) -> None:
        """Documented at https://ai.google.dev/gemini-api/docs/embeddings.

        If this ever flips to False without the docs changing, a cosine ranking
        silently degrades and no other test in the repository notices.
        """
        assert EMBEDDING_PROFILES["gemini-001-v3"].caller_must_normalize is True

    def test_the_two_gemini_profiles_disagree_about_normalization(self) -> None:
        """The whole reason ``gemini-embedding-2`` was chosen over ``001``.

        A test asserting only that both exist would pass with the distinction
        erased. This one fails if they are ever made the same.
        """
        assert (
            EMBEDDING_PROFILES["gemini-v2"].caller_must_normalize
            != EMBEDDING_PROFILES["gemini-001-v3"].caller_must_normalize
        )


class TestProfileLookup:
    def test_a_known_name_resolves(self) -> None:
        assert profile_for("gemini-v2").dimensions == 1536

    def test_an_unknown_name_is_a_startup_failure_naming_the_options(self) -> None:
        """A typo must not silently fall back to a default space."""
        with pytest.raises(ValueError) as excinfo:
            profile_for("gemini-v99")
        message = str(excinfo.value)
        assert "gemini-v99" in message
        assert "gemini-v2" in message, "the error must name the valid options"

    def test_the_env_var_name_is_stated_once(self) -> None:
        assert ACTIVE_PROFILE_ENV == "PV_EMBEDDING_PROFILE"


class TestTheProfileIsImmutable:
    def test_a_profile_cannot_be_mutated_at_runtime(self) -> None:
        """A mutable profile is a profile a test fixture can silently change."""
        titan = EMBEDDING_PROFILES["titan-v1"]
        with pytest.raises((AttributeError, TypeError)):
            titan.dimensions = 1536  # type: ignore[misc]


class TestUnprobedIdsAreMarked:
    """``probed`` and ``evidence`` must agree, in both directions.

    This asserted ``gemini-v2.probed is False`` until 2026-08-24, when the
    probe ran and the answer became True. A test written against the *state*
    fails the moment the state legitimately changes, and the pressure then is
    to delete it -- which would remove the guard at exactly the moment there is
    finally something to guard.

    So it asserts the *property* instead: a profile claiming to be probed must
    cite a transcript that has been run, and a profile citing an unrun
    transcript must not claim to be probed. Both halves fail on the mistake
    they exist to catch, and neither has to be revisited the next time a probe
    lands.
    """

    #: A profile whose evidence still says this cannot claim to be probed.
    NOT_RUN_MARKERS = ("NOT YET RUN", "NOT RUN", "TODO", "PENDING")

    def test_a_probed_profile_cites_evidence_that_was_actually_run(self) -> None:
        for name, profile in EMBEDDING_PROFILES.items():
            if not profile.probed:
                continue
            assert profile.evidence.strip(), f"{name} claims probed with no evidence"
            for marker in self.NOT_RUN_MARKERS:
                assert marker not in profile.evidence.upper(), (
                    f"{name} claims probed=True while its evidence still says "
                    f"{marker!r}. One of the two is lying."
                )

    def test_an_unprobed_profile_does_not_cite_a_completed_run(self) -> None:
        """The other direction: a profile left at False after its probe ran.

        That failure is quieter than the first and costs more -- a working id
        sitting behind a flag that says it is unverified is how a capability
        gets routed to a fallback nobody re-examines.
        """
        for name, profile in EMBEDDING_PROFILES.items():
            if profile.probed:
                continue
            assert any(marker in profile.evidence.upper() for marker in self.NOT_RUN_MARKERS), (
                f"{name} is marked probed=False but its evidence "
                f"({profile.evidence!r}) does not say the run is outstanding"
            )

    def test_the_probed_flag_is_not_uniformly_one_value(self) -> None:
        """Vacuity guard. Both tests above pass trivially if every profile
        agrees, so this asserts the corpus actually exercises both branches."""
        values = {p.probed for p in EMBEDDING_PROFILES.values()}
        assert values == {True, False}, f"only {values} present; one branch is untested"

    def test_the_two_settled_profiles_are_marked_probed(self) -> None:
        """Named, not counted. A count admits any substitute."""
        assert EMBEDDING_PROFILES["titan-v1"].probed is True
        assert EMBEDDING_PROFILES["gemini-v2"].probed is True

    def test_a_profile_knows_the_evidence_path_that_would_settle_it(self) -> None:
        assert "gemini-probe" in EMBEDDING_PROFILES["gemini-v2"].evidence


def test_profile_is_hashable_so_it_can_key_a_cache() -> None:
    """``EmbeddingCache`` is keyed per space; an unhashable profile blocks that."""
    assert isinstance(hash(EMBEDDING_PROFILES["gemini-v2"]), int)


def test_all_profiles_are_the_declared_type() -> None:
    for profile in EMBEDDING_PROFILES.values():
        assert isinstance(profile, EmbeddingProfile)


def test_this_module_is_the_only_place_the_profile_is_defined() -> None:
    """Drift guard, added after the duplication actually happened.

    Two concurrent agents independently declared an ``EmbeddingProfile`` -- one
    here, one in ``services/control_plane/app/retrieval/config.py`` -- with
    overlapping but not identical fields. Both were reasonable; together they
    were a second source of truth for which space a vector belongs to.

    ``00_IMPLEMENTATION_MAP.md`` section 6 forbids exactly this: *"Coding
    agents must not re-declare JSON shapes independently in multiple
    services."* The repository has paid for the pattern twice already -- the
    ``23505`` map keyed on constraint names the schema never declared, and four
    documents specifying four different repository trees.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    definitions = []
    for path in root.rglob("*.py"):
        parts = path.parts
        if any(p in {"node_modules", ".venv", "__pycache__", "cdk.out", "tmp"} for p in parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"^class EmbeddingProfile\b", text, re.M):
            definitions.append(path.relative_to(root).as_posix())

    assert definitions == [
        "packages/python/provenance_contracts/src/provenance_contracts/embedding_profile.py"
    ], f"EmbeddingProfile is declared in more than one place: {definitions}"


class TestAProfileTheDatabaseWillNotAccept:
    """``gemini-001-v3`` is constructible and its rows are refused at INSERT.

    Found by cross-reading migration ``0009``: ``ck_evidence_embedding_model``
    admits only ``gemini-embedding-2`` and ``gemini-embedding-2-preview``,
    because ``CANONICAL_DECISIONS.md`` -> *Gemini model id canon* chose ``-2``
    precisely on the grounds that ``001`` does not auto-normalize truncated
    widths and this stack ranks by cosine. The database refusing a ``001``
    vector is the boundary enforcing the canon, not drifting from it.

    So the CHECK is right and the profile is what needs constraining. The
    tempting and wrong fix is widening the CHECK to admit ``001``, which would
    undo the argument the canon was decided on.

    The cost of leaving this open is not a stack trace. Someone sets
    ``PV_EMBEDDING_PROFILE=gemini-001-v3``, re-embeds 18,035 texts -- real spend,
    roughly an hour unattended -- and learns at the first INSERT that the column
    will not take them. The refusal has to land before the spend, which is why
    it is a property of selecting a profile rather than of writing a row.
    """

    def test_the_shipped_gemini_profile_is_writable(self) -> None:
        assert EMBEDDING_PROFILES["gemini-v2"].writable is True

    def test_the_legacy_titan_profile_is_writable(self) -> None:
        """The corpus on disk was written under it; it must stay interpretable
        AND re-writable until 0009 lands."""
        assert EMBEDDING_PROFILES["titan-v1"].writable is True

    def test_the_001_profile_is_declared_unwritable(self) -> None:
        assert EMBEDDING_PROFILES["gemini-001-v3"].writable is False

    def test_selecting_an_unwritable_profile_refuses_before_any_spend(self) -> None:
        from provenance_contracts.embedding_profile import require_writable

        with pytest.raises(ValueError) as excinfo:
            require_writable(EMBEDDING_PROFILES["gemini-001-v3"])
        message = str(excinfo.value)
        assert "gemini-001-v3" in message
        assert "ck_evidence_embedding_model" in message, (
            "the refusal must name the constraint that would reject the rows, "
            "so the reader can check the claim instead of trusting it"
        )

    def test_selecting_a_writable_profile_returns_it(self) -> None:
        from provenance_contracts.embedding_profile import require_writable

        profile = EMBEDDING_PROFILES["gemini-v2"]
        assert require_writable(profile) is profile

    def test_reading_an_unwritable_profile_is_still_allowed(self) -> None:
        """Refusing to *write* is not refusing to *interpret*.

        Rows already carrying ``embedding_version='v3'`` must stay readable, or
        the refusal would strand exactly the data it was meant to protect.
        """
        assert profile_for("gemini-001-v3").dimensions == 1536
        assert EMBEDDING_PROFILES_BY_VERSION["v3"].model_id == "gemini-embedding-001"
