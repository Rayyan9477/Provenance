"""``ModelAttribution`` must be able to describe the model that actually ran.

The gap this closes
--------------------
``ModelAttribution`` declared ``provider: Literal["bedrock"]`` and required
``"anthropic."`` in every non-embedding model id. After the pivot the models
serving Tier E and Tier R are ``gemini-3.5-flash-lite`` and
``gemini-3.7-flash``, so **no agent run could be recorded at all** -- the
contract raised on the only ids the system can now call.

That matters more than a type error. ``agent_runs.model_route`` is what makes
the submission's model disclosure checkable against persisted state rather than
against a claim in a README. A contract that cannot express the model that ran
turns a verifiable fact back into an assertion.

Why validation is dispatched per provider rather than relaxed
--------------------------------------------------------------
The cheap fix is to delete the id-shape check. That would discard the most
expensive lesson in this repository: on Bedrock, Anthropic chat models are
invocable **only** through an inference-profile id (``us.``/``global.``) while
every other provider is invocable **only** by bare id -- mirror-image rules, so
one uniform check cannot serve both families (``D-00-040``).

Gemini adds a third rule: bare ids, and no ``anthropic.`` anywhere. Keeping a
per-provider shape check preserves the property that a stale or malformed id is
caught at the contract boundary instead of at invocation time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ModelTier

pytestmark = pytest.mark.unit


def _attribution(**overrides: object) -> ModelAttribution:
    base: dict[str, object] = {
        "provider": "gemini",
        "model_id": "gemini-3.7-flash",
        "tier": ModelTier.R,
        "prompt_version": "pv-draft-1.0.0",
        "graph_name": "advocate",
        "graph_version": "1.0.0",
    }
    return ModelAttribution(**{**base, **overrides})  # type: ignore[arg-type]


class TestAGeminiRunCanBeRecorded:
    """The whole point: these are the only ids the system can now call."""

    @pytest.mark.parametrize(
        "model_id", ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    )
    def test_each_canon_tier_model_is_accepted(self, model_id: str) -> None:
        assert _attribution(model_id=model_id).model_id == model_id

    def test_the_provider_is_recorded_not_assumed(self) -> None:
        assert _attribution().provider == "gemini"

    def test_tier_e_accepts_the_extraction_model(self) -> None:
        got = _attribution(tier=ModelTier.E, model_id="gemini-3.5-flash-lite")
        assert got.tier is ModelTier.E


class TestTheBedrockRulesAreUnchanged:
    """``D-00-040`` cost a full re-probe. Nothing here may relax it."""

    def test_an_anthropic_inference_profile_id_is_still_accepted(self) -> None:
        got = _attribution(
            provider="bedrock", model_id="us.anthropic.claude-opus-4-6-v1", tier=ModelTier.R
        )
        assert got.model_id == "us.anthropic.claude-opus-4-6-v1"

    def test_a_bare_anthropic_id_is_still_refused(self) -> None:
        """A bare id returns ValidationException from Bedrock at call time."""
        with pytest.raises(ValidationError, match="inference profile"):
            _attribution(provider="bedrock", model_id="anthropic.claude-opus-4-6-v1")

    def test_a_non_anthropic_id_on_bedrock_is_still_refused(self) -> None:
        with pytest.raises(ValidationError):
            _attribution(provider="bedrock", model_id="gemini-3.7-flash")


class TestTheProviderRulesDoNotLeakIntoEachOther:
    """The mirror-image trap, stated as a test rather than as a comment."""

    def test_an_inference_profile_prefix_is_refused_on_gemini(self) -> None:
        """``us.gemini-3.7-flash`` is not a thing, and inventing it is exactly
        the error a uniformly-applied rule would produce."""
        with pytest.raises(ValidationError):
            _attribution(model_id="us.gemini-3.7-flash")

    def test_an_anthropic_id_is_refused_on_gemini(self) -> None:
        with pytest.raises(ValidationError):
            _attribution(model_id="us.anthropic.claude-opus-4-6-v1")

    def test_an_unknown_provider_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _attribution(provider="openai", model_id="gpt-5")


class TestTheEmbeddingTierTracksTheProfiles:
    """The embedding tier was frozen to one Titan id; there are now three spaces."""

    def test_the_titan_id_is_still_accepted(self) -> None:
        got = _attribution(
            provider="bedrock",
            model_id="amazon.titan-embed-text-v2:0",
            tier=ModelTier.EMBEDDING,
        )
        assert got.tier is ModelTier.EMBEDDING

    def test_the_gemini_embedding_id_is_accepted(self) -> None:
        got = _attribution(model_id="gemini-embedding-2", tier=ModelTier.EMBEDDING)
        assert got.model_id == "gemini-embedding-2"

    def test_an_embedding_id_no_profile_declares_is_refused(self) -> None:
        """Guessing a width for an unrecognised embedding model is how a short
        vector reaches an index without anything erroring."""
        with pytest.raises(ValidationError):
            _attribution(model_id="gemini-embedding-99", tier=ModelTier.EMBEDDING)

    def test_the_accepted_embedding_ids_come_from_the_profile_registry(self) -> None:
        """Counterfactual: a hand-maintained second list would drift.

        If this contract ever stops deriving its accepted ids from
        ``embedding_profile``, adding a profile there would silently fail to
        make its model recordable here.
        """
        from provenance_contracts.embedding_profile import EMBEDDING_PROFILES

        for profile in EMBEDDING_PROFILES.values():
            got = _attribution(
                provider=profile.provider,
                model_id=profile.model_id,
                tier=ModelTier.EMBEDDING,
            )
            assert got.model_id == profile.model_id
