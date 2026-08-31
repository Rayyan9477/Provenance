"""The one output contract both halves of the Judge Mode counterfactual use.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Counterfactual*: "Memory OFF and ON use
  the same artifact, model, prompt, and graph. OFF receives empty retrieval and
  State Proof."
- ``docs/CANONICAL_DECISIONS.md`` -> *Counterfactual parity canon*: the only
  permitted differences are ``retrieval_enabled``,
  ``canonical_memory_enabled``, ``corpus_size_visible`` and the resulting
  ``output``.
- ``docs/specs/15_API_SPEC.md`` section 8.31 -- the ``output`` object this
  model is the shape of.
- ``docs/specs/14_PROMPTS.md`` section 6.4 -- MEMORY OFF uses
  ``pv-draft-1.0.0`` unchanged, with an empty TRUSTED STRUCTURED CONTEXT block.

Why this is not ``DraftAction``
--------------------------------
Section 6.4's table names ``DraftAction`` as the output schema for both sides,
and ``DraftAction`` **cannot be a response schema for the MEMORY OFF side**.
Four of its required fields are facts about committed state or about the
server's own routing decision:

* ``case_id`` and ``basis_case_revision`` -- there is no case under MEMORY OFF,
  by construction. A model asked for them would invent them.
* ``basis_proof_hash`` -- a ``Sha256Hex`` of a State Proof that MEMORY OFF is
  defined not to have.
* ``generated_by`` -- a ``ModelAttribution``. Letting the model state which
  model produced the output is precisely the disclosure failure
  ``CANONICAL_DECISIONS.md`` -> *Disclosure* exists to prevent: the attribution
  has to come from the router that made the call, and it does --
  ``agent_runs.model_calls[]`` records it.

So the contract the model fills is the *reading*, and the server owns every
field that is a fact about itself. The parity guarantee is unaffected: parity
compares six recorded fields, and both sides send this same class.

Every bound is chosen to survive ``to_wire_schema``
---------------------------------------------------
No optional fields and no ``Decimal``. ``str | None`` renders as an ``anyOf``
with a null branch and ``Decimal`` bounds render as ``ge``/``le`` strings --
the failure ``wire_schema.py`` was written for. Absence is expressed as an
empty string or an empty tuple *with a field that says which*, never as a null
the reader has to guess about: ``draft_text`` is empty exactly when
``recommended_action`` is ``NONE``, and that pairing is validated here rather
than left to a reader's charity.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

__all__ = [
    "CLASSIFICATIONS",
    "COUNTERFACTUAL_SCHEMA_VERSION",
    "RECOMMENDED_ACTIONS",
    "CounterfactualReading",
]

#: Bumped when the shape changes, and recorded on both runs so a stored
#: comparison can never be rendered against a different contract.
COUNTERFACTUAL_SCHEMA_VERSION = "counterfactual/1.0.0"

#: Closed. Both sides receive the identical set, so a difference in the value
#: chosen is a difference in what the run could see -- which is the experiment.
CLASSIFICATIONS = (
    "ROUTINE_DOCUMENT",
    "COUNTERPARTY_CLAIM_CONSISTENT_WITH_RECORD",
    "COUNTERPARTY_CLAIM_CONTRADICTING_RECORD",
    "OBLIGATION_OUTSTANDING",
    "NO_ACTION_NEEDED",
)

#: ``NONE`` plus the five ``ck_action_intents_type`` values. The database's
#: enumeration rather than a second one: a recommendation this system could not
#: subsequently draft would read as a capability and is not one.
RECOMMENDED_ACTIONS = (
    "NONE",
    "OUTBOUND_EMAIL_DISPUTE",
    "OUTBOUND_EMAIL_FOLLOW_UP",
    "OUTBOUND_EMAIL_CANCELLATION_PROOF",
    "OUTBOUND_EMAIL_DEPOSIT_DEMAND",
    "INTERNAL_REMINDER",
)


class CounterfactualReading(BaseModel):
    """What one side of the counterfactual made of the artifact it was given."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    classification: Literal[
        "ROUTINE_DOCUMENT",
        "COUNTERPARTY_CLAIM_CONSISTENT_WITH_RECORD",
        "COUNTERPARTY_CLAIM_CONTRADICTING_RECORD",
        "OBLIGATION_OUTSTANDING",
        "NO_ACTION_NEEDED",
    ]
    conflicts_detected: Annotated[int, Field(ge=0, le=20)]
    recommended_action: Literal[
        "NONE",
        "OUTBOUND_EMAIL_DISPUTE",
        "OUTBOUND_EMAIL_FOLLOW_UP",
        "OUTBOUND_EMAIL_CANCELLATION_PROOF",
        "OUTBOUND_EMAIL_DEPOSIT_DEMAND",
        "INTERNAL_REMINDER",
    ]
    draft_text: Annotated[str, StringConstraints(max_length=4000)]
    support_ids: tuple[Annotated[str, StringConstraints(max_length=64)], ...] = Field(
        default=(), max_length=20
    )
    omitted_because_unsupported: tuple[Annotated[str, StringConstraints(max_length=300)], ...] = (
        Field(default=(), max_length=8)
    )
    why: Annotated[str, StringConstraints(min_length=1, max_length=400)]

    @model_validator(mode="after")
    def _a_recommendation_carries_a_draft(self) -> CounterfactualReading:
        """``NONE`` and an empty draft go together, in both directions.

        A recommended outbound action with no draft is a promise the panel
        cannot show; a draft under ``NONE`` is a letter the system just said
        should not be sent. Either way the two fields describe different
        decisions and the panel would render the disagreement as a result.
        """
        recommends = self.recommended_action != "NONE"
        drafted = bool(self.draft_text.strip())
        if recommends and not drafted:
            raise ValueError(
                f"recommended_action={self.recommended_action} with an empty draft_text"
            )
        if drafted and not recommends:
            raise ValueError("draft_text was written but recommended_action is NONE")
        return self
