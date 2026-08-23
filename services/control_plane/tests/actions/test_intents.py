"""``T9.2`` / ``T9.3`` -- intent creation, the draft edit, and the approval freeze.

``G9.2``, quoted: "editing a draft invalidates a prior approval:
``approval_draft_sha256`` changes, execute -> ``409 ACTION_STALE``." The
execute half lives in ``test_executor.py``; this file owns everything up to and
including the moment a human clicks Approve.

``G9.6``, quoted: "an ``ActionIntent`` whose case has no committed
``kernel_decision`` -> ``409 NO_COMMITTED_BASIS``; a REJECTED proposal cannot
produce an ``ActionIntent`` at all."

The canon sentence, clause by clause
-------------------------------------
``CANONICAL_DECISIONS.md`` -> *External action*: **draft, validate grounding,
create intent, human approve, bind approval to case revision and draft SHA-256,
revalidate, execute idempotently.** The third and fifth clauses are asserted
here: an ungrounded draft never reaches an intent, and an approval writes both
bindings in the same statement that sets the status. Freezing them in a
follow-up ``UPDATE`` would leave a window in which an ``APPROVED`` row carried
no hash, which is precisely the state
``ck_action_intents_execution_needs_approval`` exists to make unrepresentable.
"""

from __future__ import annotations

import uuid

import pytest

from services.control_plane.app.actions import drafts, intents
from services.control_plane.app.actions import support_validation as sv
from services.control_plane.app.actions.store import ActionScope, InMemoryActionStore

pytestmark = pytest.mark.unit


def _scope(hero) -> ActionScope:
    return ActionScope(tenant_id=hero.tenant_id, user_id=hero.user_id)


def _service(store: InMemoryActionStore, policy, clock) -> intents.ActionIntentService:
    return intents.ActionIntentService(store=store, policy=policy, clock=clock)


def _request(hero, draft, **overrides):
    kwargs = {
        "case_id": hero.case_id,
        "action_type": "OUTBOUND_EMAIL_DISPUTE",
        "recipient": hero.recipient,
        "draft": draft,
        "rationale": "A counterparty claim asserts billable service inside a terminated period.",
        "supporting_belief_versions": (hero.belief_version_id,),
        "basis_case_revision": hero.basis_case_revision,
        "idempotency_key": "0" * 64,
        "created_by_agent_run_id": hero.agent_run_id,
    }
    kwargs.update(overrides)
    return intents.CreateIntentRequest(**kwargs)


async def _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock):
    """One committed case and one grounded intent in ``PROPOSED``."""
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = _service(memory_store, open_policy, clock)
    created = await service.create(_scope(hero), _request(hero, make_draft()))
    return service, created


# ==========================================================================
# Creation -- and the two ways it must refuse
# ==========================================================================


async def test_a_grounded_draft_creates_an_intent_in_a_pre_approval_state(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Creating an intent is not an action. Nothing has left the system."""
    _, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)

    assert created.intent.status in {"PROPOSED", "NEEDS_REVIEW"}
    assert created.intent.approval_draft_sha256 is None
    assert created.intent.approved_at is None
    assert created.intent.basis_case_revision == hero.basis_case_revision
    assert created.claims_unsupported == 0


async def test_an_ungrounded_draft_creates_no_intent_at_all(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.3``: ``DRAFT_CLAIM_UNSUPPORTED``, and **no** ``ActionIntent``.

    The second half is the one worth asserting. A system that created the row
    and marked it ``NEEDS_REVIEW`` would still be one human click away from
    sending an assertion the record cannot support.
    """
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = _service(memory_store, open_policy, clock)
    draft = make_draft(support_ids=(hero.phantom_support_id,))

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.create(_scope(hero), _request(hero, draft))

    assert raised.value.reason_code == sv.DRAFT_CLAIM_UNSUPPORTED
    assert memory_store.intents == ()


async def test_a_case_with_no_committed_kernel_decision_produces_no_intent(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.6``, first half: ``NO_COMMITTED_BASIS``.

    Invariant 4 stated as a test rather than as a principle: an action intent
    references committed rows only.
    """
    memory_store.put_snapshot(
        _scope(hero),
        sv.GroundingSnapshot(
            case_id=snapshot.case_id,
            case_revision=snapshot.case_revision,
            support_ids=snapshot.support_ids,
            current_belief_version_ids=snapshot.current_belief_version_ids,
            has_committed_kernel_decision=False,
        ),
    )
    service = _service(memory_store, open_policy, clock)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.create(_scope(hero), _request(hero, make_draft()))

    assert raised.value.reason_code == sv.NO_COMMITTED_BASIS
    assert memory_store.intents == ()


async def test_a_rejected_kernel_decision_is_not_a_committed_basis(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.6``, second half: a REJECTED proposal cannot produce an intent.

    A rejected proposal committed nothing, so the case revision it would be
    bound to describes a world the Kernel refused to enter.
    """
    assert sv.has_committed_basis("REJECTED_INVARIANT") is False
    assert sv.has_committed_basis("REJECTED_INVALID_PROVENANCE") is False
    assert sv.has_committed_basis("PENDING_HUMAN_REVIEW") is False
    assert sv.has_committed_basis("ACCEPTED") is True
    assert sv.has_committed_basis("ACCEPTED_WITH_CONFLICT") is True
    assert sv.has_committed_basis(None) is False

    memory_store.put_snapshot(
        _scope(hero),
        sv.GroundingSnapshot(
            case_id=snapshot.case_id,
            case_revision=snapshot.case_revision,
            support_ids=snapshot.support_ids,
            current_belief_version_ids=snapshot.current_belief_version_ids,
            has_committed_kernel_decision=sv.has_committed_basis("REJECTED_INVARIANT"),
        ),
    )
    service = _service(memory_store, open_policy, clock)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.create(_scope(hero), _request(hero, make_draft()))

    assert raised.value.reason_code == sv.NO_COMMITTED_BASIS


async def test_a_recipient_off_the_allowlist_creates_no_intent(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 9.8 step 3, and the fail-closed default behind it."""
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = _service(memory_store, open_policy, clock)
    draft = make_draft(recipient=hero.off_allowlist_recipient)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.create(
            _scope(hero), _request(hero, draft, recipient=hero.off_allowlist_recipient)
        )

    assert raised.value.reason_code == intents.RECIPIENT_NOT_ALLOWLISTED
    assert memory_store.intents == ()


async def test_tier_four_is_refused_before_the_database_has_to_refuse_it(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``ck_action_intents_tier4_blocked`` says ``risk_tier < 4``.

    Enforced here as well so the failure is legible rather than a ``23514``
    from three layers down. Consequential or ambiguous actions are never
    autonomous and are never even proposed in v1.
    """
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = _service(memory_store, open_policy, clock)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.create(_scope(hero), _request(hero, make_draft(), risk_tier=4))

    assert raised.value.reason_code == intents.RISK_TIER_NOT_PERMITTED


# ==========================================================================
# The approval freeze -- both bindings, one statement
# ==========================================================================


async def test_approval_freezes_the_draft_hash_and_the_case_revision_together(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """The canon sentence's fifth clause, asserted as one write.

    ``approval_draft_sha256`` and ``basis_case_revision`` are both on the row
    the moment the status becomes ``APPROVED``. The store records the statement
    count so "in one statement with the approval, never in a follow-up update"
    is checkable rather than asserted in a comment.
    """
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    approved_draft = {"subject": "Disputed invoice 88431", "body": "Hello,\n\nAlex Rivera"}
    before = memory_store.approval_statements

    record = await service.approve(
        _scope(hero),
        created.intent.id,
        intents.ApproveRequest(
            approved_draft=approved_draft,
            client_case_revision=hero.basis_case_revision,
            approved_by_user_id=hero.user_id,
        ),
    )

    assert record.intent.status == "APPROVED"
    assert record.intent.approval_draft_sha256 is not None
    assert record.intent.approved_by_user_id == hero.user_id
    assert record.intent.approved_at == hero.now
    assert record.intent.basis_case_revision == hero.basis_case_revision
    assert memory_store.approval_statements == before + 1


async def test_the_frozen_hash_is_over_what_the_client_submitted(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.26: "The server hashes this, not the stored draft."

    A race between a draft edit and an approval must not cause a different
    message to be sent than the one on the user's screen, so the subject and
    the body that arrive with the approval are what the digest covers.
    """
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    approved_draft = {"subject": "Edited on screen", "body": "Edited body"}

    record = await service.approve(
        _scope(hero),
        created.intent.id,
        intents.ApproveRequest(
            approved_draft=approved_draft,
            client_case_revision=hero.basis_case_revision,
            approved_by_user_id=hero.user_id,
        ),
    )

    assert record.intent.draft_payload["subject"] == "Edited on screen"
    assert record.intent.draft_payload["body"] == "Edited body"
    assert record.intent.approval_draft_sha256 == drafts.draft_digest(record.intent.draft_payload)
    assert record.intent.draft_sha256 == record.intent.approval_draft_sha256


async def test_an_approval_at_the_wrong_revision_is_refused_and_sends_the_intent_back(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.26 step 3, and section 7.3's ``ACTION_STALE`` shape.

    The response carries the current revision **and** the current draft hash so
    the UI can show what changed rather than only that something did.
    """
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    memory_store.advance_case_revision(_scope(hero), hero.case_id, to=14)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(
            _scope(hero),
            created.intent.id,
            intents.ApproveRequest(
                approved_draft={"subject": "s", "body": "b"},
                client_case_revision=hero.basis_case_revision,
                approved_by_user_id=hero.user_id,
            ),
        )

    assert raised.value.reason_code == intents.ACTION_STALE
    assert raised.value.details["stale_reason"] == "CASE_REVISION_ADVANCED"
    assert raised.value.details["current_case_revision"] == 14
    reloaded = await memory_store.load_intent(_scope(hero), created.intent.id)
    assert reloaded is not None
    assert reloaded.status == "NEEDS_REVIEW"
    assert reloaded.approval_draft_sha256 is None


async def test_an_approval_citing_a_superseded_belief_version_is_refused(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.26 step 4: ``stale_reason: "SUPPORT_SUPERSEDED"``.

    "We sent a letter citing a fact" and "we sent a letter citing a fact we no
    longer hold" are different sentences, and only one of them is defensible.
    """
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    memory_store.supersede_belief_versions(_scope(hero), hero.case_id, frozenset({uuid.uuid4()}))

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(
            _scope(hero),
            created.intent.id,
            intents.ApproveRequest(
                approved_draft={"subject": "s", "body": "b"},
                client_case_revision=hero.basis_case_revision,
                approved_by_user_id=hero.user_id,
            ),
        )

    assert raised.value.reason_code == intents.ACTION_STALE
    assert raised.value.details["stale_reason"] == "SUPPORT_SUPERSEDED"


async def test_unacknowledged_warnings_block_the_approval(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.26 step 5, with ``details.unacknowledged[]``."""
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = _service(memory_store, open_policy, clock)
    created = await service.create(
        _scope(hero),
        _request(hero, make_draft(), warnings=("OPEN_CONFLICT_REQUIRES_HUMAN",)),
    )
    assert created.intent.status == "NEEDS_REVIEW"

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(
            _scope(hero),
            created.intent.id,
            intents.ApproveRequest(
                approved_draft={"subject": "s", "body": "b"},
                client_case_revision=hero.basis_case_revision,
                approved_by_user_id=hero.user_id,
            ),
        )

    assert raised.value.reason_code == intents.VALIDATION_FAILED
    assert raised.value.details["unacknowledged"] == ["OPEN_CONFLICT_REQUIRES_HUMAN"]


async def test_an_already_approved_intent_cannot_be_approved_again(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Approval is a human act performed once. ``409 ACTION_NOT_APPROVABLE``."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    request = intents.ApproveRequest(
        approved_draft={"subject": "s", "body": "b"},
        client_case_revision=hero.basis_case_revision,
        approved_by_user_id=hero.user_id,
    )
    await service.approve(_scope(hero), created.intent.id, request)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(_scope(hero), created.intent.id, request)

    assert raised.value.reason_code == intents.ACTION_NOT_APPROVABLE


async def test_another_users_intent_is_invisible_rather_than_forbidden(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 1.7: a 404/403 split on a guessable id is an enumeration oracle."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    intruder = ActionScope(tenant_id=hero.tenant_id, user_id=hero.other_user_id)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(
            intruder,
            created.intent.id,
            intents.ApproveRequest(
                approved_draft={"subject": "s", "body": "b"},
                client_case_revision=hero.basis_case_revision,
                approved_by_user_id=hero.other_user_id,
            ),
        )

    assert raised.value.reason_code == intents.ACTION_INTENT_NOT_FOUND


# ==========================================================================
# The draft edit
# ==========================================================================


async def test_editing_a_draft_changes_the_hash(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.25 returns both digests so the UI can show the change."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)

    update = await service.update_draft(
        _scope(hero),
        created.intent.id,
        intents.UpdateDraftRequest(
            subject="A different subject",
            body="A different body",
            client_case_revision=hero.basis_case_revision,
        ),
    )

    assert update.previous_draft_sha256 == created.intent.draft_sha256
    assert update.intent.draft_sha256 != created.intent.draft_sha256
    assert update.intent.draft_sha256 == drafts.draft_digest(update.intent.draft_payload)


async def test_editing_a_draft_invalidates_a_prior_approval(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.2``'s first clause, on the intent side.

    The prior approval is not merely stale, it is **erased**: an approval that
    survived an edit would be a recorded human consent pointing at content the
    human never read.
    """
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    await service.approve(
        _scope(hero),
        created.intent.id,
        intents.ApproveRequest(
            approved_draft={"subject": "s", "body": "b"},
            client_case_revision=hero.basis_case_revision,
            approved_by_user_id=hero.user_id,
        ),
    )
    # The executor found the approval stale and sent it back for re-review.
    await memory_store.set_status(_scope(hero), created.intent.id, status="NEEDS_REVIEW")

    update = await service.update_draft(
        _scope(hero),
        created.intent.id,
        intents.UpdateDraftRequest(
            subject="Edited after the refusal",
            body="Edited after the refusal",
            client_case_revision=hero.basis_case_revision,
        ),
    )

    assert update.intent.approval_draft_sha256 is None
    assert update.intent.approved_at is None
    assert update.intent.approved_by_user_id is None
    assert update.intent.status == "NEEDS_REVIEW"


async def test_an_approved_draft_is_frozen(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.25: ``409 ACTION_DRAFT_FROZEN``."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    await service.approve(
        _scope(hero),
        created.intent.id,
        intents.ApproveRequest(
            approved_draft={"subject": "s", "body": "b"},
            client_case_revision=hero.basis_case_revision,
            approved_by_user_id=hero.user_id,
        ),
    )

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.update_draft(
            _scope(hero),
            created.intent.id,
            intents.UpdateDraftRequest(
                subject="s2", body="b2", client_case_revision=hero.basis_case_revision
            ),
        )

    assert raised.value.reason_code == intents.ACTION_DRAFT_FROZEN


async def test_the_recipient_is_not_editable(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.25: "``recipient`` is deliberately **not** editable."

    Changing it would change the action's blast radius after grounding
    validation ran against a specific counterparty. The field is absent from
    the request type rather than ignored in the handler.
    """
    assert "recipient" not in intents.UpdateDraftRequest.__annotations__


# ==========================================================================
# Rejection
# ==========================================================================


async def test_rejection_is_recorded_without_side_effects(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 8.27. Rejection is evidence about the user's own position."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)

    record = await service.reject(
        _scope(hero),
        created.intent.id,
        intents.RejectRequest(
            reason_code="WRONG_FACTS",
            reason_text="The termination date should be 31 May, not 30 May.",
        ),
    )

    assert record.intent.status == "REJECTED"
    assert record.intent.rejected_at == hero.now
    assert record.intent.rejection_reason == "WRONG_FACTS"
    assert record.intent.approval_draft_sha256 is None


async def test_a_rejected_intent_cannot_then_be_approved(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)
    await service.reject(
        _scope(hero), created.intent.id, intents.RejectRequest(reason_code="NOT_NOW")
    )

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.approve(
            _scope(hero),
            created.intent.id,
            intents.ApproveRequest(
                approved_draft={"subject": "s", "body": "b"},
                client_case_revision=hero.basis_case_revision,
                approved_by_user_id=hero.user_id,
            ),
        )

    assert raised.value.reason_code == intents.ACTION_NOT_APPROVABLE


async def test_an_unknown_rejection_reason_code_is_refused(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """The vocabulary is closed. ``CANONICAL_DECISIONS.md`` -> closed domain
    vocabularies: no layer-local aliases."""
    service, created = await _seeded(memory_store, snapshot, hero, make_draft, open_policy, clock)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await service.reject(
            _scope(hero), created.intent.id, intents.RejectRequest(reason_code="BECAUSE")
        )

    assert raised.value.reason_code == intents.VALIDATION_FAILED
