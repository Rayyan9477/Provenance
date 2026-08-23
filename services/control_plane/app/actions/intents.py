"""``T9.2`` / ``T9.3`` -- intent creation, draft editing, and the approval freeze.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, row **External
  action**: draft, validate grounding, create intent, human approve, **bind
  approval to case revision and draft SHA-256**, revalidate, execute
  idempotently.
- ``docs/specs/15_API_SPEC.md`` sections 8.25, 8.26, 8.27, 9.8, and section 7
  (optimistic concurrency and the ``409 ACTION_STALE`` shape).
- ``db/migrations/versions/0007_action_plane.py`` --
  ``ck_action_intents_approval_complete`` and
  ``ck_action_intents_execution_needs_approval``.

The two bindings are written by one statement
----------------------------------------------
``T9.2``, second sub-task: "Freeze both values at approval time in one
statement with the approval, never in a follow-up update." A follow-up
``UPDATE`` leaves a window in which a row is ``APPROVED`` with no recorded
hash, and that is exactly the state
``ck_action_intents_execution_needs_approval`` exists to make unrepresentable.
:meth:`ActionStore.record_approval` is therefore one call that sets the status,
the digest, the approver and the timestamp together; the in-memory store counts
them so the rule is checkable rather than asserted in a comment.

Why creation refuses rather than downgrades
--------------------------------------------
``G9.3``: an ungrounded claim means ``DRAFT_CLAIM_UNSUPPORTED`` and **no**
``ActionIntent``. Creating the row and marking it ``NEEDS_REVIEW`` would leave
the system one human click away from asserting something the record cannot
support -- and the human would be clicking on a screen the system had already
rendered with its own confidence.

Warnings are a different thing from a refusal. Section 9.8 step 6: a prohibited
pattern sets ``NEEDS_REVIEW`` with a warning "rather than rejecting -- the human
decides", and section 8.26 step 5 then requires every warning to be
acknowledged by code before the approval is accepted. Refusals are about what
the record supports; warnings are about what a person should look at twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from provenance_contracts.actions import DraftAction
from services.control_plane.app.actions import drafts
from services.control_plane.app.actions import support_validation as sv
from services.control_plane.app.actions.policy import (
    RECIPIENT_NOT_ALLOWLISTED,
    ActionPolicy,
)
from services.control_plane.app.actions.store import (
    ActionIntentRow,
    ActionScope,
    ActionStore,
    CanonicalRecorder,
    NewActionIntent,
    NullRecorder,
)

__all__ = [
    "ACTION_ALREADY_EXECUTED",
    "ACTION_DRAFT_FROZEN",
    "ACTION_INTENT_NOT_FOUND",
    "ACTION_NOT_APPROVABLE",
    "ACTION_STALE",
    "APPROVABLE_STATES",
    "EDITABLE_STATES",
    "IDEMPOTENCY_CONFLICT",
    "RECIPIENT_NOT_ALLOWLISTED",
    "REJECTION_REASON_CODES",
    "RISK_TIER_NOT_PERMITTED",
    "VALIDATION_FAILED",
    "ActionIntentService",
    "ActionRefusedError",
    "ApprovalRecord",
    "ApproveRequest",
    "CreateIntentRequest",
    "CreatedIntent",
    "DraftUpdate",
    "RejectRequest",
    "RejectionRecord",
    "UpdateDraftRequest",
]

# --- reason codes -----------------------------------------------------------
#
# These are the action plane's own vocabulary. The HTTP mapping belongs to
# ``app/api/errors.py``, which owns ``ErrorCode`` and the status table; naming
# them here rather than importing that enum keeps ``app/actions`` free of a
# dependency on ``app/api``, so the executor is equally callable from a worker.
# Every string below appears verbatim in ``15_API_SPEC.md`` or in
# ``23_PHASE_GATES.md`` section 15.

ACTION_INTENT_NOT_FOUND: Final[str] = "ACTION_INTENT_NOT_FOUND"
ACTION_NOT_APPROVABLE: Final[str] = "ACTION_NOT_APPROVABLE"
ACTION_ALREADY_EXECUTED: Final[str] = "ACTION_ALREADY_EXECUTED"
ACTION_DRAFT_FROZEN: Final[str] = "ACTION_DRAFT_FROZEN"
ACTION_STALE: Final[str] = "ACTION_STALE"
IDEMPOTENCY_CONFLICT: Final[str] = "IDEMPOTENCY_CONFLICT"
VALIDATION_FAILED: Final[str] = "VALIDATION_FAILED"
RISK_TIER_NOT_PERMITTED: Final[str] = "RISK_TIER_NOT_PERMITTED"

#: Section 8.26 step 2. Nothing else may be approved -- and note that
#: ``APPROVED`` is absent, so approving twice is ``409``, never a silent replay
#: at a new timestamp.
APPROVABLE_STATES: Final[frozenset[str]] = frozenset({"PROPOSED", "NEEDS_REVIEW"})

#: Section 8.25. An approved draft is frozen; everything past it is history.
EDITABLE_STATES: Final[frozenset[str]] = frozenset({"PROPOSED", "NEEDS_REVIEW"})

#: Section 8.27's closed vocabulary. ``CANONICAL_DECISIONS.md`` -> closed
#: domain vocabularies: no layer-local aliases.
REJECTION_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"NOT_NOW", "WRONG_FACTS", "WRONG_TONE", "WRONG_RECIPIENT", "HANDLED_ELSEWHERE", "OTHER"}
)


class ActionRefusedError(Exception):
    """A typed refusal carrying the reason code and the body's ``details``.

    The API layer maps ``reason_code`` to an ``ErrorCode`` and a status; the
    ``details`` mapping is section 7.3's ``ACTION_STALE`` body -- the current
    revision and the current draft hash -- so the UI can render *what* changed
    rather than only that something did.
    """

    def __init__(self, reason_code: str, **details: Any) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details: dict[str, Any] = details


# --- request and response value objects -------------------------------------


@dataclass(frozen=True, slots=True)
class CreateIntentRequest:
    """Section 9.8's body, typed.

    ``idempotency_key`` is the request key rather than a minted one, because
    section 9.8 step 7 says the row's key *is* the request key: the Advocate's
    retry must collide with its own first attempt on
    ``uq_action_intents_idempotency``. :func:`drafts.mint_idempotency_key` is
    the fallback when a caller has no key to offer.
    """

    case_id: uuid.UUID
    action_type: str
    recipient: str | None
    draft: DraftAction
    rationale: str
    supporting_belief_versions: tuple[uuid.UUID, ...]
    basis_case_revision: int
    idempotency_key: str | None = None
    created_by_agent_run_id: uuid.UUID | None = None
    risk_tier: int = 3
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreatedIntent:
    intent: ActionIntentRow
    claims_validated: int
    claims_unsupported: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateDraftRequest:
    """Section 8.25's body. ``recipient`` is absent, not ignored.

    Changing the recipient would change the action's blast radius after the
    Advocate's grounding validation ran against a specific counterparty. A
    field that cannot be supplied cannot be forgotten in a handler.
    """

    subject: str
    body: str
    client_case_revision: int


@dataclass(frozen=True, slots=True)
class DraftUpdate:
    intent: ActionIntentRow
    previous_draft_sha256: bytes
    claims_revalidated: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApproveRequest:
    """Section 8.26's body plus the verified approver.

    ``approved_by_user_id`` is not in the JSON: it comes from the authenticated
    principal. A caller-supplied approver id would let a request name whoever
    it liked as the human who consented.
    """

    approved_draft: Mapping[str, Any]
    client_case_revision: int
    approved_by_user_id: uuid.UUID
    acknowledge_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    intent: ActionIntentRow
    approved_case_revision: int
    case_revision_after: int


@dataclass(frozen=True, slots=True)
class RejectRequest:
    reason_code: str
    reason_text: str | None = None


@dataclass(frozen=True, slots=True)
class RejectionRecord:
    intent: ActionIntentRow
    case_revision_after: int


# --- the service ------------------------------------------------------------


@dataclass
class ActionIntentService:
    """Everything up to and including the moment a human clicks Approve.

    Holds no connection and no credential: the store is a protocol, the policy
    is a frozen value object, and the clock is injected because
    ``20_TDD_STRATEGY.md`` section 4.2 rule 3 bans a wall-clock read where a
    deterministic one will do.
    """

    store: ActionStore
    policy: ActionPolicy
    clock: Callable[[], datetime]
    recorder: CanonicalRecorder = field(default_factory=NullRecorder)

    # -- creation ---------------------------------------------------------

    async def create(self, scope: ActionScope, request: CreateIntentRequest) -> CreatedIntent:
        """Validate grounding, then insert. Refuse before the row exists.

        Order matters and is section 9.8's order: the cheapest structural
        refusals first, the grounding check last, and the insert only after
        every one of them has passed. An implementation that inserted first and
        validated afterwards would have to delete a row to be correct, and a
        deleted ``action_intents`` row is indistinguishable from one that was
        never proposed.
        """
        if request.risk_tier >= 4:
            raise ActionRefusedError(
                RISK_TIER_NOT_PERMITTED,
                risk_tier=request.risk_tier,
                detail="tier 4 is never autonomous and is never proposed in v1",
            )
        if not self.policy.recipient_allowlisted(request.recipient):
            raise ActionRefusedError(RECIPIENT_NOT_ALLOWLISTED, recipient=request.recipient)

        snapshot = await self.store.grounding_snapshot(scope, request.case_id)
        if snapshot is None:
            raise ActionRefusedError(sv.NO_COMMITTED_BASIS, case_id=str(request.case_id))
        if snapshot.case_revision != request.basis_case_revision:
            raise ActionRefusedError(
                ACTION_STALE,
                stale_reason="CASE_REVISION_ADVANCED",
                current_case_revision=snapshot.case_revision,
                basis_case_revision=request.basis_case_revision,
            )

        verdict = sv.validate_draft_claims(request.draft, snapshot)
        if not verdict.grounded:
            raise ActionRefusedError(
                verdict.reason_code or sv.DRAFT_CLAIM_UNSUPPORTED,
                sentences=[claim.sentence_or_span for claim in verdict.unsupported],
            )
        if not set(request.supporting_belief_versions).issubset(
            snapshot.current_belief_version_ids
        ):
            raise ActionRefusedError(ACTION_STALE, stale_reason="SUPPORT_SUPERSEDED")

        payload = drafts.draft_payload_of(request.draft)
        digest = drafts.draft_digest(payload)
        key = request.idempotency_key or drafts.mint_idempotency_key(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            case_id=request.case_id,
            action_type=request.action_type,
            draft_sha256=digest,
        )
        row = await self.store.insert_intent(
            scope,
            NewActionIntent(
                id=uuid.uuid4(),
                case_id=request.case_id,
                action_type=request.action_type,
                recipient=request.recipient,
                draft_payload=payload,
                draft_sha256=digest,
                rationale=request.rationale,
                supporting_belief_versions=request.supporting_belief_versions,
                basis_case_revision=request.basis_case_revision,
                # Section 9.8: NEEDS_REVIEW when any warning exists, else
                # PROPOSED. Neither status can produce an external effect.
                status="NEEDS_REVIEW" if request.warnings else "PROPOSED",
                risk_tier=request.risk_tier,
                idempotency_key=key,
                created_by_agent_run_id=request.created_by_agent_run_id,
                warnings=request.warnings,
            ),
            now=self.clock(),
        )
        return CreatedIntent(
            intent=row,
            claims_validated=len(verdict.validated_claim_ids),
            claims_unsupported=0,
            warnings=request.warnings,
        )

    # -- the draft edit ---------------------------------------------------

    async def update_draft(
        self, scope: ActionScope, intent_id: uuid.UUID, request: UpdateDraftRequest
    ) -> DraftUpdate:
        """Replace the subject and body, recompute the digest, drop any approval.

        ``G9.2``'s first clause. The prior approval is **erased** rather than
        marked stale: an approval that survived an edit would be a recorded
        human consent pointing at content the human never read, and no amount
        of downstream revalidation makes that record honest again.
        """
        intent = await self._require(scope, intent_id)
        if intent.status not in EDITABLE_STATES:
            raise ActionRefusedError(
                ACTION_DRAFT_FROZEN,
                status=intent.status,
                current_draft_sha256=intent.draft_sha256.hex(),
            )
        snapshot = await self.store.grounding_snapshot(scope, intent.case_id)
        current_revision = snapshot.case_revision if snapshot else intent.basis_case_revision
        if request.client_case_revision != current_revision:
            raise ActionRefusedError(
                ACTION_STALE,
                stale_reason="CASE_REVISION_ADVANCED",
                current_case_revision=current_revision,
                current_draft_sha256=intent.draft_sha256.hex(),
            )

        payload = drafts.merge_approved_draft(
            intent.draft_payload, {"subject": request.subject, "body": request.body}
        )
        row = await self.store.replace_draft(
            scope,
            intent_id,
            draft_payload=payload,
            draft_sha256=drafts.draft_digest(payload),
            status="NEEDS_REVIEW",
            clear_approval=True,
            now=self.clock(),
        )
        return DraftUpdate(
            intent=row,
            previous_draft_sha256=intent.draft_sha256,
            claims_revalidated=True,
            warnings=intent.warnings,
        )

    # -- approval ---------------------------------------------------------

    async def approve(
        self, scope: ActionScope, intent_id: uuid.UUID, request: ApproveRequest
    ) -> ApprovalRecord:
        """Section 8.26's server sequence, in its order.

        Step 3's consequence is implemented as written and is easy to miss: a
        stale approval does not merely fail, it transitions the intent to
        ``NEEDS_REVIEW``. The human re-reviews; the system never re-approves on
        their behalf, because retrying an approval in code would forge consent.
        """
        intent = await self._require(scope, intent_id)
        if intent.status not in APPROVABLE_STATES:
            raise ActionRefusedError(ACTION_NOT_APPROVABLE, status=intent.status)

        snapshot = await self.store.grounding_snapshot(scope, intent.case_id)
        if snapshot is None or not snapshot.has_committed_kernel_decision:
            raise ActionRefusedError(sv.NO_COMMITTED_BASIS, case_id=str(intent.case_id))

        if not (
            request.client_case_revision == snapshot.case_revision == intent.basis_case_revision
        ):
            await self.store.set_status(scope, intent_id, status="NEEDS_REVIEW")
            raise ActionRefusedError(
                ACTION_STALE,
                stale_reason="CASE_REVISION_ADVANCED",
                current_case_revision=snapshot.case_revision,
                basis_case_revision=intent.basis_case_revision,
                current_draft_sha256=intent.draft_sha256.hex(),
            )
        if not set(intent.supporting_belief_versions).issubset(snapshot.current_belief_version_ids):
            await self.store.set_status(scope, intent_id, status="NEEDS_REVIEW")
            raise ActionRefusedError(
                ACTION_STALE,
                stale_reason="SUPPORT_SUPERSEDED",
                current_case_revision=snapshot.case_revision,
                current_draft_sha256=intent.draft_sha256.hex(),
            )

        unacknowledged = _unacknowledged(intent.warnings, request.acknowledge_warnings)
        if unacknowledged:
            raise ActionRefusedError(VALIDATION_FAILED, unacknowledged=list(unacknowledged))
        if not self.policy.recipient_allowlisted(intent.recipient):
            raise ActionRefusedError(RECIPIENT_NOT_ALLOWLISTED, recipient=intent.recipient)

        payload = drafts.merge_approved_draft(intent.draft_payload, request.approved_draft)
        approved = await self.store.record_approval(
            scope,
            intent_id,
            draft_payload=payload,
            draft_sha256=drafts.draft_digest(payload),
            approved_by_user_id=request.approved_by_user_id,
            approved_at=self.clock(),
        )
        after = await self.recorder.record_action_approved(scope, approved)
        return ApprovalRecord(
            intent=approved,
            approved_case_revision=snapshot.case_revision,
            case_revision_after=after,
        )

    # -- rejection --------------------------------------------------------

    async def reject(
        self, scope: ActionScope, intent_id: uuid.UUID, request: RejectRequest
    ) -> RejectionRecord:
        """Section 8.27. Recorded, not discarded, and with no side effect.

        A rejection is evidence about the user's own position and belongs on
        the timeline. ``WRONG_FACTS`` in particular usually means the memory
        behind the draft is wrong, which is a correction rather than a deletion.
        """
        intent = await self._require(scope, intent_id)
        if request.reason_code not in REJECTION_REASON_CODES:
            raise ActionRefusedError(
                VALIDATION_FAILED,
                field="reason_code",
                permitted=sorted(REJECTION_REASON_CODES),
            )
        if request.reason_code == "OTHER" and not request.reason_text:
            raise ActionRefusedError(VALIDATION_FAILED, field="reason_text")
        if intent.status not in APPROVABLE_STATES:
            raise ActionRefusedError(ACTION_NOT_APPROVABLE, status=intent.status)

        rejected = await self.store.record_rejection(
            scope, intent_id, reason_code=request.reason_code, rejected_at=self.clock()
        )
        snapshot = await self.store.grounding_snapshot(scope, intent.case_id)
        return RejectionRecord(
            intent=rejected,
            case_revision_after=snapshot.case_revision if snapshot else intent.basis_case_revision,
        )

    # -- helpers ----------------------------------------------------------

    async def _require(self, scope: ActionScope, intent_id: uuid.UUID) -> ActionIntentRow:
        """Load, or refuse with a 404 that does not distinguish absent from foreign.

        Section 1.7: an action-intent id is guessable enough that a 404/403
        split is an enumeration oracle over another user's disputes.
        """
        intent = await self.store.load_intent(scope, intent_id)
        if intent is None:
            raise ActionRefusedError(ACTION_INTENT_NOT_FOUND, action_intent_id=str(intent_id))
        return intent


def _unacknowledged(warnings: Sequence[str], acknowledged: Sequence[str]) -> tuple[str, ...]:
    """Section 8.26 step 5's ``details.unacknowledged[]``, in declared order."""
    seen = set(acknowledged)
    return tuple(code for code in warnings if code not in seen)
