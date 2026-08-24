"""Advocate-side schemas: the bounded input, and the Tier R attention output.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 5 (``classify_attention_need``), whose
  prompt text names every field of :class:`AttentionAssessment`.
- ``docs/specs/15_API_SPEC.md`` section 9.6, which prints the ``AdvocacyContext``
  wrapper the control plane returns beside the State Proof.
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 7.
- ``docs/CANONICAL_DECISIONS.md`` -> *Advocate attention classes*.

Why these live here and not in ``provenance_contracts``
--------------------------------------------------------
``14_PROMPTS.md`` section 5.3 points at ``provenance_contracts.advocacy``, a
module that does not exist: ``11_CONTRACTS.md`` section 15 was never
implemented. These are *model-output* and *agent-input* schemas rather than
persisted boundary contracts -- ``AttentionAssessment`` is explicitly "a model
output, not a column" -- so the agent runtime is a defensible home for them
until the contracts package grows section 15. If it does, this module should
re-export from there rather than keep a second definition; two definitions of
an output schema is the drift this repository spends most of its energy
preventing.

The five classes are not the four levels
----------------------------------------
``AdvocateAttentionClass`` has five members; ``cases.attention_level`` has four.
``CANONICAL_DECISIONS.md`` makes the separation binding: the five are "mapped
deterministically to case attention and action policy, never stored directly in
``cases.attention_level``", and the mapping belongs to the deterministic control
plane. **This module deliberately does not provide that mapping.** A helper here
would be the shortest available path to writing ``ACTION_REQUIRED`` into a
four-value column, which is the exact failure the separation exists to prevent.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import BoundaryContract, Contract, Revision, UtcDatetime
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActionType,
    AdvocateAttentionClass,
    CaseStatus,
    ModelTier,
)

__all__ = [
    "ActionPolicy",
    "AdvocacyContext",
    "AttentionAssessment",
    "CounterpartyRef",
    "UserCommunicationPreferences",
]

Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=600)]
Rationale = Annotated[str, StringConstraints(min_length=1, max_length=1200)]


class CounterpartyRef(Contract):
    """Who the case is with. A display name and a kind, and nothing else."""

    display_name: Label
    kind: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ActionPolicy(Contract):
    """What this case permits. Enforced deterministically, not by asking nicely.

    ``supported_actions`` is the closed set the Advocate may choose from;
    ``recipient_allowlist_domains`` is why an injected "send confirmation to
    billing@attacker.example" cannot change where a draft would go even if the
    model copied the address verbatim.
    """

    supported_actions: tuple[ActionType, ...] = Field(default=(), max_length=8)
    recipient: Annotated[str, StringConstraints(max_length=320)] | None = None
    recipient_allowlist_domains: tuple[Annotated[str, StringConstraints(max_length=253)], ...] = (
        Field(default=(), max_length=8)
    )
    requires_human_approval: Literal[True] = True
    max_body_chars: Annotated[int, Field(ge=200, le=20_000)] = 4000
    prohibited: tuple[Annotated[str, StringConstraints(max_length=64)], ...] = Field(
        default=(), max_length=12
    )

    @model_validator(mode="after")
    def _recipient_is_inside_the_allowlist(self) -> ActionPolicy:
        if self.recipient is None or not self.recipient_allowlist_domains:
            return self
        domain = self.recipient.rsplit("@", 1)[-1].lower()
        if domain not in {d.lower() for d in self.recipient_allowlist_domains}:
            raise ValueError(
                f"policy recipient domain {domain!r} is not in its own allowlist; a policy "
                "that contradicts itself cannot be the boundary an executor trusts"
            )
        return self


class UserCommunicationPreferences(Contract):
    """Formality, language and signature handling. Never content."""

    tone: Literal["NEUTRAL", "FIRM", "CONCILIATORY", "FIRM_POLITE"] = "NEUTRAL"
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] = "en"
    sign_off: Annotated[str, StringConstraints(max_length=120)] | None = None


class AdvocacyContext(BoundaryContract):
    """The bounded package the Advocate receives, and nothing else.

    Section 7 of the contracts document: no arbitrary unrelated personal
    memories, no other cases, no cross-user retrieval. The caps here are what
    make that a property of the type rather than a property of the query that
    happened to run.
    """

    case_id: uuid.UUID
    case_revision: Revision
    counterparty: CounterpartyRef
    current_case_state: CaseStatus
    action_policy: ActionPolicy
    user_communication_preferences: UserCommunicationPreferences = UserCommunicationPreferences()


class AttentionAssessment(BoundaryContract):
    """Tier R output of ``classify_attention_need``. Advisory, like every model output.

    ``advisory: Literal[True]`` is a field rather than a docstring so a consumer
    that treats an assessment as canonical has to visibly strip a flag saying it
    is not.
    """

    advisory: Literal[True] = True

    #: The *output schema* version from ``14_PROMPTS.md`` section 5.
    #: Deliberately not named ``schema_version``: :class:`BoundaryContract`
    #: already owns that name for the boundary-contract version ("1.0"), and
    #: two different versioning concepts under one field name is how a payload
    #: ends up carrying a number that means neither. ``ExtractionResult`` made
    #: the same distinction with ``extraction_schema_version``.
    output_schema_version: Literal["attention/1.0.0"] = "attention/1.0.0"
    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    case_id: uuid.UUID
    case_revision: Revision

    attention_class: AdvocateAttentionClass
    urgency: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    time_basis: Sentence | None = None
    primary_reason: Sentence
    rationale_summary: Rationale
    recommended_action_type: ActionType | None = None
    requires_human_decision: bool = False

    supporting_belief_version_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=20)
    supporting_conflict_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10)
    supporting_commitment_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10)
    suppression_reasons: tuple[Sentence, ...] = Field(default=(), max_length=8)

    generated_at: UtcDatetime
    model: ModelAttribution

    @model_validator(mode="after")
    def _classified_by_tier_r(self) -> AttentionAssessment:
        if self.model.tier is not ModelTier.R:
            raise ValueError("attention classification is a Tier R task")
        return self

    @model_validator(mode="after")
    def _human_decision_is_consistent(self) -> AttentionAssessment:
        if (
            self.attention_class is AdvocateAttentionClass.HUMAN_DECISION
            and not self.requires_human_decision
        ):
            raise ValueError(
                "HUMAN_DECISION without requires_human_decision would map to URGENT and "
                "then let an action proceed without the decision it names"
            )
        return self

    @model_validator(mode="after")
    def _every_reason_is_traceable(self) -> AttentionAssessment:
        """Section 5.1 rule 2: reason only from the State Proof supplied.

        A level above FYI asserts that something in committed state warrants the
        user's attention, so it must name at least one id from that state. NONE
        and FYI may legitimately cite nothing -- "nothing changed that matters"
        is not a claim about a particular row.
        """
        needs_support = self.attention_class in (
            AdvocateAttentionClass.ACTION_SUGGESTED,
            AdvocateAttentionClass.ACTION_REQUIRED,
            AdvocateAttentionClass.HUMAN_DECISION,
        )
        cited = (
            self.supporting_belief_version_ids
            + self.supporting_conflict_ids
            + self.supporting_commitment_ids
        )
        if needs_support and not cited:
            raise ValueError(
                f"{self.attention_class} cites no belief version, conflict or commitment; "
                "an attention level Provenance cannot point at is a notification it "
                "cannot justify"
            )
        return self

    @model_validator(mode="after")
    def _no_action_means_null_not_an_alias(self) -> AttentionAssessment:
        if (
            self.attention_class is AdvocateAttentionClass.NONE
            and self.recommended_action_type is not None
        ):
            raise ValueError(
                "attention_class NONE cannot recommend an action; there is no NO_ACTION "
                "alias and null is how the model says no"
            )
        return self
