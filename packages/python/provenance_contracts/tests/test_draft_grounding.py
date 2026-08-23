"""L11 — every factual outbound claim cites State Proof support, by offset.

Written before ``provenance_contracts/actions.py`` exists (T1.6).

Authority
---------
- ``specs/11_CONTRACTS.md`` section 16 (``actions.py``) and section 20.6, which
  prints the first seven tests below.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, fifth sub-task: ``DraftAction`` and
  ``ActionIntentView``, with ``approval_draft_sha256`` and
  ``basis_case_revision`` required on the approved form.

Recorded deviation — ``test_model_tier_names_an_invocable_model``
------------------------------------------------------------------
Section 20.6 prints ``test_model_tier_is_frozen_to_one_model_id``, asserting
the substring ``"frozen to"`` when ``ModelTier.R`` is paired with a model id
other than ``anthropic.claude-opus-5``. ``resolution.py`` (T1.5) does not
implement that equality check, because ``CANONICAL_DECISIONS.md`` ->
*Bedrock model id canon (frozen 2026-08-17)* supersedes section 10's frozen id
map: bare ``anthropic.*`` ids are not invocable on Bedrock at all, and
``us.anthropic.claude-opus-5`` is denied to this account. The shipped validator
checks the inference-profile *shape* instead. The test below asserts the
shipped rule -- a bare id is refused -- which is the same protection the spec
test was reaching for. Reported, not silently resolved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from provenance_contracts.actions import (
    ActionExecutionView,
    ActionIntentView,
    DraftAction,
    DraftClaim,
    ExecutabilityVerdict,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActionState,
    ActionType,
    ExecutionStatus,
    ModelTier,
)

BODY = (
    "Your invoice covers 1-30 June 2026. "
    "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026. "
    "Please withdraw the charge of USD 186.00."
)
SPAN = "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026."
START = BODY.index(SPAN)

NOW = datetime(2026, 6, 5, tzinfo=UTC)

#: Allocated once at module scope, not per ``_draft()`` call. ``case_id`` is
#: part of the draft's content and is therefore inside ``sha256()``: a fresh id
#: per call made ``test_draft_hash_ignores_generation_metadata_but_not_content``
#: compare two drafts that differed in a field the hash is *supposed* to cover,
#: so it failed for the wrong reason. Corrected in T1.6.
DRAFT_CASE_ID = uuid.uuid4()

TIER_R = ModelAttribution(
    model_id="us.anthropic.claude-opus-4-6-v1",
    tier=ModelTier.R,
    prompt_version="advocate-1.1",
    graph_name="advocate_graph",
    graph_version="1.0.0",
)


def _draft(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "draft_id": uuid.uuid4(),
        "case_id": DRAFT_CASE_ID,
        "basis_case_revision": 8,
        "basis_proof_hash": "a" * 64,
        "action_type": ActionType.OUTBOUND_EMAIL_DISPUTE,
        "recipient": "billing@example-isp.test",
        "subject": "Invoice for June 2026 on a cancelled account",
        "body": BODY,
        "requested_outcome": "Withdraw the June invoice and confirm the account is closed.",
        "generated_by": TIER_R,
        "generated_at": NOW,
    }
    payload.update(overrides)
    return payload


def _grounded_claim(support_id: uuid.UUID) -> DraftClaim:
    return DraftClaim(
        claim_id="dc_1",
        sentence_or_span=SPAN,
        char_start=START,
        char_end=START + len(SPAN),
        support_ids=(support_id,),
        support_kind="BELIEF_VERSION",
    )


def _intent(draft: DraftAction, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_intent_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "case_id": draft.case_id,
        "action_type": draft.action_type,
        "status": ActionState.PROPOSED,
        "recipient": draft.recipient,
        "draft": draft,
        "draft_sha256": draft.sha256(),
        "rationale": "The invoice contradicts the recorded termination date.",
        "basis_case_revision": draft.basis_case_revision,
        "idempotency_key": f"intent:{draft.draft_id}",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.6 — draft grounding
# ---------------------------------------------------------------------------


def test_a_claim_must_cite_support() -> None:
    with pytest.raises(ValidationError):
        DraftClaim(
            claim_id="dc_1",
            sentence_or_span=SPAN,
            char_start=START,
            char_end=START + len(SPAN),
            support_ids=(),
            support_kind="BELIEF_VERSION",
        )


def test_a_claim_span_must_actually_be_in_the_body() -> None:
    bad = DraftClaim(
        claim_id="dc_1",
        sentence_or_span=SPAN,
        char_start=0,  # wrong offset
        char_end=len(SPAN),
        support_ids=(uuid.uuid4(),),
        support_kind="BELIEF_VERSION",
    )
    with pytest.raises(ValidationError) as excinfo:
        DraftAction(**_draft(claims=(bad,)))
    assert "not at the offsets it names" in str(excinfo.value)


def test_a_grounded_draft_validates() -> None:
    support_id = uuid.uuid4()
    draft = DraftAction(**_draft(claims=(_grounded_claim(support_id),)))
    assert draft.validate_against_proof(frozenset({support_id})) == ()
    assert draft.validate_against_proof(frozenset()) == ("dc_1",)


def test_draft_hash_ignores_generation_metadata_but_not_content() -> None:
    first = DraftAction(**_draft())
    same_content = DraftAction(
        **_draft(draft_id=uuid.uuid4(), generated_at=datetime(2027, 1, 1, tzinfo=UTC))
    )
    assert first.sha256() == same_content.sha256()

    edited = DraftAction(**_draft(body=BODY + " Thank you."))
    assert first.sha256() != edited.sha256()


def test_internal_vocabulary_cannot_leak_into_an_outbound_message() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DraftAction(**_draft(body=BODY + " Our belief_version 3 has a confidence score of 0.95."))
    assert "leaks internal vocabulary" in str(excinfo.value)


def test_advocacy_drafting_is_tier_r_only() -> None:
    tier_e = ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5",
        tier=ModelTier.E,
        prompt_version="advocate-1.1",
        graph_name="advocate_graph",
        graph_version="1.0.0",
    )
    with pytest.raises(ValidationError):
        DraftAction(**_draft(generated_by=tier_e))


def test_model_tier_names_an_invocable_model() -> None:
    """See the recorded deviation in this module's docstring."""
    with pytest.raises(ValidationError) as excinfo:
        ModelAttribution(
            model_id="anthropic.claude-sonnet-4-6",
            tier=ModelTier.R,
            prompt_version="advocate-1.1",
            graph_name="advocate_graph",
            graph_version="1.0.0",
        )
    assert "bare Bedrock model id" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T1.6 sub-task 5 — the approved form binds who, when, and exactly what
# ---------------------------------------------------------------------------


def test_an_intent_cannot_disagree_with_the_draft_it_carries() -> None:
    draft = DraftAction(**_draft())
    with pytest.raises(ValidationError) as excinfo:
        ActionIntentView(**_intent(draft, draft_sha256="b" * 64))
    assert "does not match the rendered draft" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        ActionIntentView(**_intent(draft, basis_case_revision=9))
    assert "disagree about the basis revision" in str(excinfo.value)


def test_an_approval_binds_who_when_and_exactly_what() -> None:
    """``approval_draft_sha256`` is required the moment the state is APPROVED.

    A partial approval and a post-approval status with no recorded hash are the
    same defect seen twice: an action could execute content no human ever saw.
    """
    draft = DraftAction(**_draft())
    with pytest.raises(ValidationError) as excinfo:
        ActionIntentView(**_intent(draft, approved_by_user_id=uuid.uuid4(), approved_at=NOW))
    assert "who, when, and exactly what" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        ActionIntentView(**_intent(draft, status=ActionState.APPROVED))
    assert "content no human ever saw" in str(excinfo.value)


def test_executability_fails_closed_on_every_staleness_axis() -> None:
    support_id = uuid.uuid4()
    draft = DraftAction(**_draft(claims=(_grounded_claim(support_id),)))
    approved = ActionIntentView(
        **_intent(
            draft,
            status=ActionState.APPROVED,
            approved_by_user_id=uuid.uuid4(),
            approved_at=NOW,
            approval_draft_sha256=draft.sha256(),
            supporting_belief_versions=(support_id,),
        )
    )
    allowed = approved.executability(
        current_case_revision=8,
        current_belief_version_ids=frozenset({support_id}),
        has_successful_execution=False,
    )
    assert allowed == ExecutabilityVerdict(allowed=True)

    moved_on = approved.executability(
        current_case_revision=9,
        current_belief_version_ids=frozenset(),
        has_successful_execution=True,
    )
    assert moved_on.allowed is False
    assert set(moved_on.blocking_reasons) == {
        "CASE_REVISION_CHANGED",
        "SUPPORT_BELIEF_SUPERSEDED",
        "ALREADY_EXECUTED",
    }

    unapproved = ActionIntentView(**_intent(draft))
    verdict = unapproved.executability(
        current_case_revision=8,
        current_belief_version_ids=frozenset({support_id}),
        has_successful_execution=False,
    )
    assert verdict.allowed is False
    assert "NOT_APPROVED" in verdict.blocking_reasons
    assert "DRAFT_HASH_CHANGED" in verdict.blocking_reasons


def test_a_verdict_may_not_be_allowed_and_blocked_at_once() -> None:
    with pytest.raises(ValidationError):
        ExecutabilityVerdict(allowed=True, blocking_reasons=("CASE_REVISION_CHANGED",))
    with pytest.raises(ValidationError) as excinfo:
        ExecutabilityVerdict(allowed=False)
    assert "a refusal must say why" in str(excinfo.value)


def test_an_execution_attempt_records_its_exact_request() -> None:
    attempt = ActionExecutionView(
        execution_id=uuid.uuid4(),
        attempt_no=1,
        provider="ses",
        request_sha256="c" * 64,
        status=ExecutionStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
    )
    assert attempt.attempt_no == 1
    with pytest.raises(ValidationError):
        ActionExecutionView(
            execution_id=uuid.uuid4(),
            attempt_no=6,  # the cap is five attempts
            provider="ses",
            request_sha256="c" * 64,
            status=ExecutionStatus.FAILED_FINAL,
            started_at=NOW,
        )
