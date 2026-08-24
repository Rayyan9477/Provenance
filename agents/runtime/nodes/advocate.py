"""The six Advocate nodes of ``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 6.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 6, 7, 8, 12.
- ``docs/specs/14_PROMPTS.md`` sections 5 and 6.
- ``docs/CANONICAL_DECISIONS.md`` -> *Advocate attention classes*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.5.

Same shape as the ingestion nodes: ``(state, deps) -> state``, frozen state,
one visit appended per node, failures recorded rather than raised.

Committed state, and nothing else
---------------------------------
:func:`load_state_proof` is the only door into this graph, and it refuses three
things before any model sees anything: a proof with no hash, a proof whose hash
disagrees with its content, and a ``MemoryMode.OFF`` proof. The first two mean
the proof was not rendered from a committed revision. The third is the Judge
Mode counterfactual's empty proof, which is a valid object and an invalid basis
for a real action -- exactly the kind of thing that is obvious until the day it
is not.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from agents.runtime.schemas.advocacy import ActionPolicy, AttentionAssessment
from agents.runtime.schemas.validation import (
    ValidationFailure,
    validate_attention,
    validate_draft,
)
from agents.runtime.state import (
    AdvocateGraphState,
    AdvocateOutcome,
    GraphError,
    ModelPending,
    ModelSuccess,
)
from provenance_contracts.actions import DraftAction
from provenance_contracts.proof import StateProof
from provenance_domain.enums import (
    ActionType,
    AdvocateAttentionClass,
    ConflictStatus,
    MemoryMode,
)

__all__ = [
    "ACTING_CLASSES",
    "classify_attention_need",
    "create_action_intent",
    "draft_action",
    "load_state_proof",
    "select_action_template",
    "select_action_type",
    "validate_draft_claims",
]

#: The classes that justify drafting something. ``NONE`` and ``FYI`` end the
#: run: "most changes do not need the user", and an unnecessary notification is
#: a real cost to someone trusting Provenance to be quiet until something
#: matters (``14_PROMPTS.md`` section 5.1 rule 3).
ACTING_CLASSES: frozenset[AdvocateAttentionClass] = frozenset(
    {
        AdvocateAttentionClass.ACTION_SUGGESTED,
        AdvocateAttentionClass.ACTION_REQUIRED,
        AdvocateAttentionClass.HUMAN_DECISION,
    }
)


def _visit(state: AdvocateGraphState, node: str, **updates: Any) -> AdvocateGraphState:
    return dataclasses.replace(state, visits=(*state.visits, node), **updates)


def _fail(
    state: AdvocateGraphState,
    node: str,
    *,
    code: str,
    detail: str,
    outcome: AdvocateOutcome,
    **updates: Any,
) -> AdvocateGraphState:
    return _visit(
        state,
        node,
        errors=(*state.errors, GraphError(node=node, code=code, detail=detail)),
        outcome=outcome,
        **updates,
    )


# ---------------------------------------------------------------------------
# load_state_proof
# ---------------------------------------------------------------------------


def load_state_proof(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """The Advocate's only view of memory, and the only door into this graph."""
    node = "load_state_proof"
    proof: StateProof = deps.proofs.get_state_proof(state.case_id)

    if proof.memory_mode is MemoryMode.OFF:
        return _fail(
            state,
            node,
            code="PROOF_MEMORY_OFF",
            detail=(
                "a memory-off proof is the Judge Mode counterfactual baseline and carries "
                "no committed state; it can produce a comparison, never an action"
            ),
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    if proof.proof_hash is None:
        return _fail(
            state,
            node,
            code="PROOF_NOT_COMMITTED",
            detail=(
                "the proof carries no hash, so it was not rendered from a committed "
                "revision; an advocate that drafts from an uncommitted proposal is "
                "invariant 4 waiting to happen"
            ),
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    if proof.proof_hash != proof.compute_hash():
        return _fail(
            state,
            node,
            code="PROOF_HASH_MISMATCH",
            detail="the proof's recorded hash does not match its content",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    if proof.case is None or proof.user_id != state.principal_ref.user_id:
        return _fail(
            state,
            node,
            code="PROOF_FOREIGN_OR_CASELESS",
            detail="the proof names no case, or belongs to another user",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )

    context = deps.proofs.get_action_policy(state.case_id)
    if context.case_revision != proof.case.revision:
        return _fail(
            state,
            node,
            code="POLICY_REVISION_MISMATCH",
            detail=(
                f"the action policy describes revision {context.case_revision} and the "
                f"proof describes {proof.case.revision}; the read was not point-in-time "
                "consistent"
            ),
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note="proof loaded")
    return _visit(state, node, state_proof=proof, advocacy_context=context)


# ---------------------------------------------------------------------------
# classify_attention_need
# ---------------------------------------------------------------------------


def _attention_context(state: AdvocateGraphState) -> dict[str, Any]:
    """The State Proof, rendered as trusted structured context.

    Every evidence excerpt reachable from here is a Provenance-normalized
    restatement of third-party text, so the block carries
    ``derived_from_untrusted: true``. That label is a convention rather than an
    enforcement mechanism (``14_PROMPTS.md`` risk R8), and it is here because
    the alternative -- omitting it -- would hand third-party restatements to the
    model at trusted-context authority.
    """
    proof = state.state_proof
    context = state.advocacy_context
    assert proof is not None and context is not None and proof.case is not None
    return {
        "case_id": str(state.case_id),
        "case_revision": proof.case.revision,
        "case_status": str(proof.case.status),
        "counterparty": context.counterparty.display_name,
        "supported_actions": [str(a) for a in context.action_policy.supported_actions],
        "active_conflicts": [
            {
                "conflict_id": str(c.conflict_id),
                "status": str(c.status),
                "severity": str(c.severity),
                "requires_human": c.requires_human,
            }
            for c in proof.conflicts
        ],
        "commitments": [
            {
                "commitment_id": str(c.commitment_id),
                "status": str(c.status),
                "outstanding": str(c.outstanding.amount) if c.outstanding else None,
                "due_at": c.due_at.isoformat() if c.due_at else None,
            }
            for c in proof.commitments
        ],
        "active_beliefs": [
            {
                "belief_version_id": str(b.current_version.belief_version_id),
                "predicate": b.predicate,
                "epistemic_status": str(b.current_version.epistemic_status),
            }
            for b in proof.beliefs
        ],
        "derived_from_untrusted": True,
    }


def classify_attention_need(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """Tier R triage. Decides whether the user needs to know, not what to say."""
    node = "classify_attention_need"
    proof = state.state_proof
    context = state.advocacy_context
    if proof is None or context is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_STATE_PROOF",
            detail="classification reached without a proof",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    route = deps.attention_route
    rendered = deps.renderer.render_user(trusted_context=_attention_context(state), blocks=())
    proof_ids = frozenset(str(i) for i in proof.support_ids())

    def _validate(assessment: AttentionAssessment) -> tuple[ValidationFailure, ...]:
        return validate_attention(
            assessment,
            supported_actions=[str(a) for a in context.action_policy.supported_actions],
            proof_ids=proof_ids,
        )

    outcome = deps.router.invoke(
        node,
        system=deps.renderer.render_system(route.prompt_version),
        user_text=rendered.user_text,
        schema=AttentionAssessment,
        validate=_validate,
    )
    if isinstance(outcome, ModelPending):
        return _fail(
            state,
            node,
            code=outcome.reason_code,
            detail="attention classification failed inside its budget",
            outcome=AdvocateOutcome.PENDING_HUMAN_REVIEW,
        )
    assert isinstance(outcome, ModelSuccess)
    assessment: AttentionAssessment = outcome.value

    # The validator runs again here for the same reason the ingestion graph
    # re-validates: the router is a dependency this graph does not own, and an
    # action type outside the policy is a policy violation rather than a
    # formatting one.
    failures = _validate(assessment)
    if failures:
        return _fail(
            state,
            node,
            code=failures[0].code,
            detail="; ".join(f"{f.path}: {f.detail}" for f in failures),
            outcome=AdvocateOutcome.PENDING_HUMAN_REVIEW,
            attention=assessment,
            model_routes=(*state.model_routes, outcome.route),
        )

    if assessment.attention_class not in ACTING_CLASSES:
        return _visit(
            state,
            node,
            attention=assessment,
            model_routes=(*state.model_routes, outcome.route),
            outcome=AdvocateOutcome.NO_ATTENTION,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note=str(assessment.attention_class))
    return _visit(
        state,
        node,
        attention=assessment,
        model_routes=(*state.model_routes, outcome.route),
    )


# ---------------------------------------------------------------------------
# select_action_template
# ---------------------------------------------------------------------------


def select_action_type(
    *, attention: AttentionAssessment, policy: ActionPolicy
) -> ActionType | None:
    """Deterministic template selection. Pure, so it is testable on its own.

    The model's recommendation is honoured only when it is inside the policy;
    otherwise the first supported action is used. Either way the chosen type
    comes from the policy, never from the artifact and never from the model's
    imagination -- which is what makes ``FILE_REGULATORY_COMPLAINT``
    unselectable rather than merely discouraged.
    """
    supported = policy.supported_actions
    if not supported:
        return None
    if attention.recommended_action_type in supported:
        return attention.recommended_action_type
    return supported[0]


def select_action_template(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """Deterministic. Chooses the action type, the recipient and the tone."""
    node = "select_action_template"
    attention = state.attention
    context = state.advocacy_context
    if attention is None or context is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_ATTENTION_ASSESSMENT",
            detail="template selection reached without an assessment",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    action_type = select_action_type(attention=attention, policy=context.action_policy)
    if action_type is None:
        return _fail(
            state,
            node,
            code="NO_SUPPORTED_ACTION",
            detail="the case permits no outbound action; nothing may be drafted",
            outcome=AdvocateOutcome.PENDING_HUMAN_REVIEW,
        )
    if context.action_policy.recipient is None:
        return _fail(
            state,
            node,
            code="NO_POLICY_RECIPIENT",
            detail=(
                "the action policy names no recipient; a recipient read from the artifact "
                "is how an injected footer becomes an outbound address"
            ),
            outcome=AdvocateOutcome.PENDING_HUMAN_REVIEW,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note=str(action_type))
    return _visit(state, node, action_type=action_type)


# ---------------------------------------------------------------------------
# draft_action
# ---------------------------------------------------------------------------


def _draft_context(state: AdvocateGraphState) -> dict[str, Any]:
    base = _attention_context(state)
    context = state.advocacy_context
    attention = state.attention
    assert context is not None and attention is not None
    base.update(
        {
            "action_type": str(state.action_type),
            "recipient_role": context.counterparty.kind,
            "requested_outcome_hint": attention.primary_reason,
            "user_preferences": {
                "tone": context.user_communication_preferences.tone,
                "language": context.user_communication_preferences.language,
                "sign_off": context.user_communication_preferences.sign_off,
            },
            "max_body_chars": context.action_policy.max_body_chars,
            "prohibited": list(context.action_policy.prohibited),
            "support_ids": sorted(
                str(i) for i in (state.state_proof.support_ids() if state.state_proof else ())
            ),
        }
    )
    return base


def draft_action(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """Tier R drafting. Grounding is emitted alongside each sentence, by construction."""
    node = "draft_action"
    route = deps.draft_route
    rendered = deps.renderer.render_user(trusted_context=_draft_context(state), blocks=())
    outcome = deps.router.invoke(
        node,
        system=deps.renderer.render_system(route.prompt_version),
        user_text=rendered.user_text,
        schema=DraftAction,
        validate=None,
    )
    if isinstance(outcome, ModelPending):
        return _fail(
            state,
            node,
            code=outcome.reason_code,
            detail="drafting failed inside its budget; no intent is created",
            outcome=AdvocateOutcome.PENDING_HUMAN_REVIEW,
        )
    assert isinstance(outcome, ModelSuccess)
    deps.session.record(run_id=state.agent_run_id, node=node, note="draft returned")
    return _visit(
        state,
        node,
        draft=outcome.value,
        model_routes=(*state.model_routes, outcome.route),
    )


# ---------------------------------------------------------------------------
# validate_draft_claims
# ---------------------------------------------------------------------------


def validate_draft_claims(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """Deterministic verification against the State Proof and the action policy.

    An unsupported claim does not stop the run: section 8 says the intent is
    created as ``NEEDS_REVIEW`` with the warning attached. Refusing outright
    would lose the draft a human might well approve after one edit; sending it
    silently is the failure this whole path exists to prevent. ``NEEDS_REVIEW``
    is the honest third option.
    """
    node = "validate_draft_claims"
    draft = state.draft
    proof = state.state_proof
    context = state.advocacy_context
    if draft is None or proof is None or context is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_DRAFT",
            detail="validation reached without a draft",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    assert proof.case is not None and proof.proof_hash is not None
    failures = validate_draft(
        draft,
        support_ids=frozenset(str(i) for i in proof.support_ids()),
        supported_actions=[str(a) for a in context.action_policy.supported_actions],
        recipient_allowlist_domains=context.action_policy.recipient_allowlist_domains,
        max_body_chars=context.action_policy.max_body_chars,
        open_human_conflict=any(
            c.requires_human or c.status is ConflictStatus.NEEDS_HUMAN for c in proof.conflicts
        ),
        current_case_revision=proof.case.revision,
        current_proof_hash=proof.proof_hash,
    )
    deps.session.record(
        run_id=state.agent_run_id, node=node, note=f"{len(failures)} draft failure(s)"
    )
    return _visit(state, node, draft_failures=failures)


# ---------------------------------------------------------------------------
# create_action_intent
# ---------------------------------------------------------------------------


def create_action_intent(state: AdvocateGraphState, deps: Any) -> AdvocateGraphState:
    """The Advocate's only write tool. The UI owns approval; this never sends.

    ``interrupt()``-style graph pausing is not the approval record and is not
    used here. Approval is a database transition authenticated as the user,
    bound to the case revision and to the draft's SHA-256, and re-checked at
    execution.
    """
    node = "create_action_intent"
    draft = state.draft
    attention = state.attention
    if draft is None or attention is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_DRAFT",
            detail="intent creation reached without a draft",
            outcome=AdvocateOutcome.FAIL_SAFE,
        )
    needs_review = bool(state.draft_failures) or attention.requires_human_decision
    warnings = tuple(f"{f.code}: {f.path}" for f in state.draft_failures)
    receipt = deps.intents.create_action_intent(
        draft,
        rationale=attention.primary_reason,
        warnings=warnings,
        needs_review=needs_review,
    )
    deps.session.record(run_id=state.agent_run_id, node=node, note=str(receipt.status))
    return _visit(
        state,
        node,
        action_intent=receipt,
        outcome=(AdvocateOutcome.NEEDS_REVIEW if needs_review else AdvocateOutcome.INTENT_CREATED),
    )
