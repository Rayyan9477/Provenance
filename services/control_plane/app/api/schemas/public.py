"""Request models for the public `/v1` surface.

Authority: ``specs/15_API_SPEC.md`` sections 8.14, 8.18, 8.19, 8.22, 8.25-8.27,
8.28 and 8.30.

Two shapes here are load-bearing rather than decorative.

**`DraftUpdateRequest` has no `recipient` field.** Section 8.25: changing the
recipient would change the blast radius of the action after the grounding
validation ran against a specific counterparty. Because the model forbids
extras, sending one is a ``422`` at the schema layer rather than a silent
no-op, and the client learns the field does not exist.

**`UploadIntentRequest.filename` rejects path separators and a leading dot.**
Section 8.18 says the user-supplied filename never becomes part of the object
key; the key is ``raw/{tenant_id}/{user_id}/{artifact_id}/original`` and the
server chooses it. Rejecting the traversal shape as well is belt and braces --
the filename is stored as metadata, and metadata containing a traversal
sequence is evidence of an attempt, not of a filename.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from services.control_plane.app.api.schemas.common import ApiRequest, Ratio

__all__ = [
    "ACTION_REJECT_REASONS",
    "CORRECTION_TARGETS",
    "TRACE_NODE_TYPES",
    "ApproveRequest",
    "ApprovedDraft",
    "ArtifactCompleteRequest",
    "CorrectionRequest",
    "CounterfactualRequest",
    "DraftUpdateRequest",
    "RejectRequest",
    "RotateAliasRequest",
    "UploadIntentRequest",
]

#: Section 8.28's closed node-type set. Seventeen members, and a trace node
#: whose type falls outside it is a bug in the assembler rather than a new kind
#: of node: the DAG is built from real rows, so every node has a row type.
TRACE_NODE_TYPES: Final[tuple[str, ...]] = (
    "API_REQUEST",
    "ARTIFACT_PARSE",
    "EMBEDDING",
    "AGENT_RUN",
    "MODEL_CALL",
    "MCP_TOOL_CALL",
    "RETRIEVAL",
    "PROPOSAL",
    "KERNEL_DECISION",
    "DB_TRANSACTION",
    "CANONICAL_CHANGE",
    "OUTBOX_EVENT",
    "EVENT_CONSUMER",
    "TRIGGER_EVALUATION",
    "ACTION_INTENT",
    "ACTION_APPROVAL",
    "ACTION_EXECUTION",
)

#: Section 8.14: which target field each correction type requires. The map is
#: data rather than a chain of branches so that the ``422
#: CORRECTION_TARGET_INVALID`` rule and the table in the spec cannot drift.
CORRECTION_TARGETS: Final[dict[str, str]] = {
    "BELIEF_INCORRECT": "affected_belief_id",
    "CONFIRM_BELIEF": "affected_belief_id",
    "EVIDENCE_INCORRECT": "affected_evidence_id",
    "RETRACT_EVIDENCE": "affected_evidence_id",
    "COMMITMENT_INCORRECT": "affected_commitment_id",
    "IDENTITY_INCORRECT": "affected_belief_id",
}

ACTION_REJECT_REASONS: Final[tuple[str, ...]] = (
    "NOT_NOW",
    "WRONG_FACTS",
    "WRONG_TONE",
    "WRONG_RECIPIENT",
    "HANDLED_ELSEWHERE",
    "OTHER",
)

Statement = Annotated[str, StringConstraints(min_length=1, max_length=2000)]

#: 1-255 characters, no path separator, no control character, no leading dot.
Filename = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=255,
        pattern=r"^[^./\\\x00-\x1f][^/\\\x00-\x1f]*$",
    ),
]

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CorrectionRequest(ApiRequest):
    """Section 8.14. A correction is evidence, not an edit."""

    correction_type: Literal[
        "BELIEF_INCORRECT",
        "EVIDENCE_INCORRECT",
        "RETRACT_EVIDENCE",
        "COMMITMENT_INCORRECT",
        "IDENTITY_INCORRECT",
        "CONFIRM_BELIEF",
    ]
    statement: Statement
    affected_belief_id: uuid.UUID | None = None
    affected_evidence_id: uuid.UUID | None = None
    affected_commitment_id: uuid.UUID | None = None
    proposed_value: dict[str, Any] | None = None
    user_explanation: Annotated[str, StringConstraints(max_length=2000)] | None = None
    client_case_revision: int = Field(ge=0)

    def missing_target(self) -> str | None:
        """The target field this correction type requires but does not carry."""
        required = CORRECTION_TARGETS[self.correction_type]
        return None if getattr(self, required) is not None else required


class UploadIntentRequest(ApiRequest):
    """Section 8.18. There is no ``s3_key`` field: the server chooses the key."""

    filename: Filename
    mime_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    size_bytes: int = Field(ge=1)
    sha256: Sha256Hex | None = None
    source_hint: Annotated[str, StringConstraints(max_length=64)] | None = None


class ArtifactCompleteRequest(ApiRequest):
    """Section 8.19. May be empty when ``sha256`` was given at upload-intent."""

    sha256: Sha256Hex | None = None
    size_bytes: int | None = Field(default=None, ge=1)


class RotateAliasRequest(ApiRequest):
    """Section 8.22. No fields: rotation takes no parameters, and an empty
    model is what makes a stray one a ``422`` rather than a shrug."""


class DraftUpdateRequest(ApiRequest):
    """Section 8.25. ``recipient`` is deliberately absent."""

    subject: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    client_case_revision: int = Field(ge=0)


class ApprovedDraft(ApiRequest):
    """Exactly what the user saw. The server hashes this, not the stored row."""

    subject: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=8000)]


class ApproveRequest(ApiRequest):
    """Section 8.26, the human authorisation boundary."""

    approved_draft: ApprovedDraft
    client_case_revision: int = Field(ge=0)
    acknowledge_warnings: list[Annotated[str, StringConstraints(max_length=100)]] = Field(
        default_factory=list
    )


class RejectRequest(ApiRequest):
    """Section 8.27. ``reason_text`` is required only for ``OTHER``."""

    reason_code: Literal[
        "NOT_NOW",
        "WRONG_FACTS",
        "WRONG_TONE",
        "WRONG_RECIPIENT",
        "HANDLED_ELSEWHERE",
        "OTHER",
    ]
    reason_text: Annotated[str, StringConstraints(max_length=1000)] | None = None

    @model_validator(mode="after")
    def _other_needs_words(self) -> RejectRequest:
        if self.reason_code == "OTHER" and not (self.reason_text or "").strip():
            raise ValueError("reason_text is required when reason_code is OTHER")
        return self


class CounterfactualRequest(ApiRequest):
    """Section 8.30. The artifact must belong to the calling user, which the
    port enforces through its scope argument rather than this model."""

    artifact_id: uuid.UUID
    modes: list[Literal["MEMORY_OFF", "MEMORY_ON"]] | None = None
    memory_on_strategy: Literal["REPLAY_COMMITTED", "RERUN_SANDBOXED"] = "REPLAY_COMMITTED"
    minimum_confidence: Ratio | None = None


class TriggerWakeRequest(ApiRequest):
    """Section 13.2's manual wake. Deliberately almost empty.

    ``ApiRequest`` forbids unknown fields, and that is the whole point of
    declaring a model for a body that carries nothing required: a caller who
    sends ``{"force": true}`` or ``{"result": "FIRED"}`` gets a 422 naming the
    field rather than a 200 that quietly ignored it.

    ``16_TRIGGER_DSL.md`` 13.2 prohibits a ``force`` parameter, and
    ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration* requires the no-op and
    the fire to come from the same entry point with no hidden state revert. A
    body that silently dropped an unknown field would let a demo script believe
    it had control over the verdict and be wrong in the one direction nobody
    checks. The refusal is the feature.

    ``note`` exists so an operator can say why they pressed it; it is recorded
    on the wake envelope and changes no outcome.
    """

    note: Annotated[str, StringConstraints(max_length=500)] | None = None
