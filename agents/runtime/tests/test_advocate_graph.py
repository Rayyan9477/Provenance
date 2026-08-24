"""The Advocate graph — attention classification and grounded drafting (``T7.5``).

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 6, 7, 8, 12.
- ``docs/specs/14_PROMPTS.md`` sections 5 and 6, and section 10 rows A11 and A12.
- ``docs/CANONICAL_DECISIONS.md`` -> *Advocate attention classes*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.5.

The one input
-------------
The Advocate's only business input is a **committed** State Proof. Every test
below builds one, and :class:`FakeProofReader` exposes exactly the two read
tools section 17 permits. There is no method anywhere in this file that could
return an uncommitted proposal, which is the point: an advocate that reads
uncommitted proposals is invariant 4 waiting to happen, and the way to prevent
it is to make the capability absent rather than discouraged.

Grounding is emitted, not reconstructed
---------------------------------------
``claims[].sentence_or_span`` is copied out of ``body`` by the model and checked
by offset. A draft asserting a fact absent from the State Proof therefore fails
the contract test rather than being emitted with a hedge, which is what T7.5
asks for by name.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from agents.runtime.graphs.advocate_graph import (
    ADVOCATE_NODES,
    AdvocateDeps,
    run_advocate,
    select_action_type,
)
from agents.runtime.schemas.advocacy import (
    ActionPolicy,
    AdvocacyContext,
    AttentionAssessment,
    CounterpartyRef,
    UserCommunicationPreferences,
)
from agents.runtime.schemas.validation import ValidationFailure, validate_draft
from agents.runtime.state import (
    GRAPH_NAME_ADVOCATE,
    GRAPH_VERSION_ADVOCATE,
    ActionIntentReceipt,
    AdvocateGraphState,
    AdvocateOutcome,
    ModelPending,
    ModelRoute,
    ModelSuccess,
    RenderedPrompt,
    initial_advocate_state,
)
from provenance_contracts.actions import DraftAction, DraftClaim
from provenance_contracts.base import Money
from provenance_contracts.identity import CapabilityBinding
from provenance_contracts.ingestion import ContentBlock, SourceLocator
from provenance_contracts.proof import (
    BeliefProof,
    BeliefVersionProof,
    CaseSnapshot,
    CommitmentProof,
    ConflictProof,
    DerivationTrace,
    EvidenceProof,
    GroundingEdgeProof,
    StateProof,
)
from provenance_domain.enums import (
    ActionState,
    ActionType,
    AdvocateAttentionClass,
    AttentionLevel,
    CaseStatus,
    CaseType,
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EvidenceType,
    ModelTier,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    ValueType,
)

pytestmark = pytest.mark.unit


def _u(tail: str) -> uuid.UUID:
    return uuid.UUID(f"018f0000-0000-7000-8000-{tail:>012s}".replace(" ", "0"))


TENANT = _u("70a0")
USER = _u("5e40")
CASE = _u("ca50")
RELATIONSHIP = _u("4e10")
TRACE = _u("7ace")
AGENT_RUN = _u("a6e1")
BELIEF = _u("be10")
BELIEF_VERSION = _u("bf10")
SUPPORT_EDGE = _u("50e1")
EVIDENCE = _u("e001")
CONFLICT = _u("c0f1")
COMMITMENT = _u("c001")
INTENT = _u("11e7")

NOW = datetime.fromisoformat("2026-09-18T13:00:00+00:00")
CASE_REVISION = 13

ROUTE_R = ModelRoute(
    provider="gemini",
    model_id="gemini-3.7-flash",
    tier=ModelTier.R,
    prompt_version="pv-draft-1.0.0",
)
ROUTE_ATTENTION = ModelRoute(
    provider="gemini",
    model_id="gemini-3.7-flash",
    tier=ModelTier.R,
    prompt_version="pv-attention-1.1.0",
)

BINDING = CapabilityBinding(
    binding_id=_u("b1d1"),
    binding_kind="AGENT_RUN",
    tenant_id=TENANT,
    user_id=USER,
    case_id=CASE,
    expires_at=NOW + timedelta(hours=1),
    status="ACTIVE",
)

POLICY = ActionPolicy(
    supported_actions=(ActionType.OUTBOUND_EMAIL_DISPUTE, ActionType.OUTBOUND_EMAIL_FOLLOW_UP),
    recipient="billing@northlinefiber.example",
    recipient_allowlist_domains=("northlinefiber.example",),
    max_body_chars=4000,
    prohibited=("LEGAL_THREAT", "PAYMENT_COMMITMENT"),
)

ADVOCACY = AdvocacyContext(
    case_id=CASE,
    case_revision=CASE_REVISION,
    counterparty=CounterpartyRef(display_name="Northline Fiber", kind="ISP"),
    current_case_state=CaseStatus.REOPENED,
    action_policy=POLICY,
    user_communication_preferences=UserCommunicationPreferences(
        tone="FIRM_POLITE", sign_off="Alex Rivera"
    ),
)


# ===========================================================================
# A committed State Proof
# ===========================================================================


def build_proof(*, needs_human: bool = True) -> StateProof:
    """A committed proof of the hero billing dispute, hashed like a real one."""
    evidence = EvidenceProof(
        evidence_id=EVIDENCE,
        artifact_id=_u("a471"),
        evidence_type=EvidenceType.CANCELLATION_NOTICE,
        normalized_text="The provider confirmed the service was terminated on 31 May.",
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id="blk_0002", char_start=0, char_end=58
        ),
        observed_at=NOW - timedelta(days=110),
        artifact_received_at=NOW - timedelta(days=110),
        artifact_sender="support@northlinefiber.example",
    )
    proof = StateProof(
        proof_id=_u("9200"),
        generated_at=NOW,
        tenant_id=TENANT,
        user_id=USER,
        case=CaseSnapshot(
            case_id=CASE,
            case_type=CaseType.BILLING_DISPUTE,
            title="Northline Fiber June invoice",
            status=CaseStatus.REOPENED,
            revision=CASE_REVISION,
            attention_level=AttentionLevel.URGENT,
            counterparty_name="Northline Fiber",
            relationship_id=RELATIONSHIP,
            opened_at=NOW - timedelta(days=120),
            reopened_count=1,
            last_activity_at=NOW,
        ),
        beliefs=(
            BeliefProof(
                belief_id=BELIEF,
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=RELATIONSHIP,
                subject_label="Northline Fiber account NF-4471-8802",
                predicate="service_terminated",
                current_version=BeliefVersionProof(
                    belief_version_id=BELIEF_VERSION,
                    version_no=2,
                    value_type=ValueType.BOOLEAN,
                    value_json=True,
                    epistemic_status=EpistemicStatus.DISPUTED,
                    belief_confidence=Decimal("0.82"),
                    recorded_at=NOW - timedelta(days=95),
                    kernel_decision_id=_u("6ed1"),
                ),
                grounding=(
                    GroundingEdgeProof(
                        support_id=SUPPORT_EDGE,
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=EVIDENCE,
                        relation=SupportRelation.SUPPORTS,
                        weight=Decimal("0.9"),
                        evidence=evidence,
                    ),
                ),
            ),
        ),
        conflicts=(
            ConflictProof(
                conflict_id=CONFLICT,
                conflict_type=ConflictType.VALUE_CONFLICT,
                status=ConflictStatus.NEEDS_HUMAN if needs_human else ConflictStatus.OPEN,
                severity=ConflictSeverity.HIGH,
                predicate="outstanding_amount",
                requires_human=needs_human,
                left_summary="The June invoice states 186.00 outstanding.",
                right_summary="The record holds the service terminated before that period.",
                canonical_belief_version_id=BELIEF_VERSION,
                detected_at=NOW - timedelta(days=1),
            ),
        ),
        commitments=(
            CommitmentProof(
                commitment_id=COMMITMENT,
                description="Return the security deposit within 30 days of inspection.",
                status=CommitmentStatus.ACTIVE,
                committed=Money(amount=Decimal("1800.00"), currency="USD"),
                fulfilled=Money(amount=Decimal("0.00"), currency="USD"),
                outstanding=Money(amount=Decimal("1800.00"), currency="USD"),
                due_at=NOW - timedelta(days=95),
                outstanding_derivation=DerivationTrace(
                    derivation_name="derive_outstanding",
                    function_version="1.0.0",
                    inputs=(("committed", "1800.00"), ("fulfilled", "0.00")),
                    output="1800.00",
                    explanation="outstanding = committed - admitted fulfilments",
                ),
            ),
        ),
    )
    return proof.with_hash()


PROOF = build_proof()
SUPPORT_IDS = PROOF.support_ids()
PROOF_HASH = PROOF.compute_hash()

BODY = (
    "I am writing about the June invoice for account NF-4471-8802. "
    "Your records show an outstanding balance of 186.00 USD. "
    "My records show the service was terminated before that billing period began. "
    "Please review the account and confirm the corrected balance in writing."
)


def sentence_claim(claim_id: str, sentence: str, support: uuid.UUID) -> DraftClaim:
    start = BODY.index(sentence)
    return DraftClaim(
        claim_id=claim_id,
        sentence_or_span=sentence,
        char_start=start,
        char_end=start + len(sentence),
        support_ids=(support,),
        support_kind="BELIEF_VERSION",
    )


def build_draft(
    *,
    claims: Sequence[DraftClaim] | None = None,
    risks: Sequence[str] = ("The record still shows an open contradiction about the balance.",),
    body: str = BODY,
    recipient: str = "billing@northlinefiber.example",
    action_type: ActionType = ActionType.OUTBOUND_EMAIL_DISPUTE,
    basis_case_revision: int = CASE_REVISION,
    basis_proof_hash: str | None = None,
) -> DraftAction:
    if claims is None:
        claims = (
            sentence_claim(
                "dc_1",
                "Your records show an outstanding balance of 186.00 USD.",
                COMMITMENT,
            ),
            sentence_claim(
                "dc_2",
                "My records show the service was terminated before that billing period began.",
                BELIEF_VERSION,
            ),
        )
    return DraftAction(
        draft_id=_u("d4a1"),
        case_id=CASE,
        basis_case_revision=basis_case_revision,
        basis_proof_hash=basis_proof_hash if basis_proof_hash is not None else PROOF_HASH,
        action_type=action_type,
        recipient=recipient,
        subject="June invoice for account NF-4471-8802",
        body=body,
        claims=tuple(claims),
        requested_outcome="A written confirmation of the corrected balance.",
        tone="FIRM",
        unresolved_risks=tuple(risks),
        generated_by=ROUTE_R.attribution(
            graph_name=GRAPH_NAME_ADVOCATE, graph_version=GRAPH_VERSION_ADVOCATE
        ),
        generated_at=NOW,
    )


def build_attention(
    *,
    attention_class: AdvocateAttentionClass = AdvocateAttentionClass.ACTION_REQUIRED,
    recommended: ActionType | None = ActionType.OUTBOUND_EMAIL_DISPUTE,
    urgency: str = "HIGH",
    suppression: Sequence[str] = (),
    supporting_conflicts: Sequence[uuid.UUID] = (CONFLICT,),
    requires_human_decision: bool = False,
) -> AttentionAssessment:
    return AttentionAssessment(
        trace_id=TRACE,
        agent_run_id=AGENT_RUN,
        case_id=CASE,
        case_revision=CASE_REVISION,
        attention_class=attention_class,
        urgency=urgency,  # type: ignore[arg-type]
        time_basis="The deposit commitment passed its due date 95 days ago.",
        primary_reason="An open contradiction about the outstanding balance is unaddressed.",
        rationale_summary="The case holds one open value conflict and one overdue commitment.",
        recommended_action_type=recommended,
        requires_human_decision=requires_human_decision,
        supporting_belief_version_ids=(BELIEF_VERSION,),
        supporting_conflict_ids=tuple(supporting_conflicts),
        supporting_commitment_ids=(COMMITMENT,),
        suppression_reasons=tuple(suppression),
        generated_at=NOW,
        model=ROUTE_ATTENTION.attribution(
            graph_name=GRAPH_NAME_ADVOCATE, graph_version=GRAPH_VERSION_ADVOCATE
        ),
    )


# ===========================================================================
# Fakes
# ===========================================================================


class ScriptedRouter:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.nodes: list[str] = []
        self.systems: list[str] = []
        self.user_texts: list[str] = []

    def invoke(
        self,
        node: str,
        *,
        system: str,
        user_text: str,
        schema: type[Any],
        validate: Callable[[Any], Sequence[ValidationFailure]] | None = None,
    ) -> Any:
        self.nodes.append(node)
        self.systems.append(system)
        self.user_texts.append(user_text)
        if len(self.nodes) > len(self.outcomes):
            raise AssertionError(
                f"the graph made model call {len(self.nodes)} to {node!r}; the script has "
                f"{len(self.outcomes)} outcome(s)."
            )
        outcome = self.outcomes[len(self.nodes) - 1]
        if isinstance(outcome, ModelSuccess) and validate is not None:
            failures = tuple(validate(outcome.value))
            if failures:
                return ModelPending(reason_code=failures[0].code, node=node)
        assert isinstance(schema, type)
        return outcome


class FakeRenderer:
    NONCE = "PROVENANCE_UNTRUSTED_fedcba9876543210"

    def render_system(self, prompt_version: str) -> str:
        return f"# SYSTEM POLICY\n# prompt_version: {prompt_version}\n\n# TASK\n"

    def render_user(
        self, *, trusted_context: Mapping[str, Any], blocks: Sequence[ContentBlock]
    ) -> RenderedPrompt:
        assert list(blocks) == [], "the Advocate is given no artifact blocks"
        return RenderedPrompt(
            user_text="=== TRUSTED STRUCTURED CONTEXT ===\n"
            + repr(sorted(trusted_context))
            + "\n=== UNTRUSTED EVIDENCE ===\n(none)",
            nonce=self.NONCE,
        )


class FakeProofReader:
    """The Advocate's whole world: two reads, both over committed state."""

    def __init__(self, proof: StateProof = PROOF, context: AdvocacyContext = ADVOCACY) -> None:
        self.proof = proof
        self.context = context
        self.calls: list[str] = []

    def get_state_proof(self, case_id: uuid.UUID) -> StateProof:
        self.calls.append("get_state_proof")
        assert case_id == self.proof.case.case_id if self.proof.case else True
        return self.proof

    def get_action_policy(self, case_id: uuid.UUID) -> AdvocacyContext:
        self.calls.append("get_action_policy")
        assert case_id == self.context.case_id
        return self.context


class FakeIntentWriter:
    def __init__(self) -> None:
        self.created: list[tuple[DraftAction, bool, tuple[str, ...]]] = []

    def create_action_intent(
        self,
        draft: DraftAction,
        *,
        rationale: str,
        warnings: Sequence[str],
        needs_review: bool,
    ) -> ActionIntentReceipt:
        assert rationale
        self.created.append((draft, needs_review, tuple(warnings)))
        return ActionIntentReceipt(
            action_intent_id=INTENT,
            status=ActionState.NEEDS_REVIEW if needs_review else ActionState.PROPOSED,
            warnings=tuple(warnings),
        )


class TrapSession:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def record(self, *, run_id: uuid.UUID, node: str, note: str) -> None:
        assert isinstance(run_id, uuid.UUID)
        self.records.append((node, note))

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - only on a defect
        raise AssertionError(
            f"a graph asked the session store for {name!r}; session state is workflow "
            "durability only and is never product state"
        )


def build_deps(
    *,
    router: ScriptedRouter | None = None,
    reader: FakeProofReader | None = None,
    writer: FakeIntentWriter | None = None,
) -> tuple[AdvocateDeps, dict[str, Any]]:
    parts: dict[str, Any] = {
        "router": router
        if router is not None
        else ScriptedRouter(
            ModelSuccess(build_attention(), ROUTE_ATTENTION),
            ModelSuccess(build_draft(), ROUTE_R),
        ),
        "renderer": FakeRenderer(),
        "reader": reader if reader is not None else FakeProofReader(),
        "writer": writer if writer is not None else FakeIntentWriter(),
        "session": TrapSession(),
    }
    deps = AdvocateDeps(
        router=parts["router"],
        renderer=parts["renderer"],
        proofs=parts["reader"],
        intents=parts["writer"],
        session=parts["session"],
        clock=lambda: NOW,
        attention_route=ROUTE_ATTENTION,
        draft_route=ROUTE_R,
    )
    return deps, parts


def start_state() -> AdvocateGraphState:
    return initial_advocate_state(
        trace_id=TRACE, agent_run_id=AGENT_RUN, principal_ref=BINDING, case_id=CASE
    )


def run(**kwargs: Any) -> tuple[AdvocateGraphState, dict[str, Any]]:
    deps, parts = build_deps(**kwargs)
    return run_advocate(start_state(), deps), parts


# ===========================================================================
# 1. Topology
# ===========================================================================


def test_the_node_sequence_is_section_six_in_order() -> None:
    assert ADVOCATE_NODES == (
        "load_state_proof",
        "classify_attention_need",
        "select_action_template",
        "draft_action",
        "validate_draft_claims",
        "create_action_intent",
    )


def test_a_case_needing_action_visits_every_node() -> None:
    state, _ = run()
    print("visit order:", " -> ".join(state.visits))
    assert state.visits == ADVOCATE_NODES
    assert state.outcome is AdvocateOutcome.INTENT_CREATED


def test_attention_none_ends_the_run_before_any_draft() -> None:
    router = ScriptedRouter(
        ModelSuccess(
            build_attention(
                attention_class=AdvocateAttentionClass.NONE,
                recommended=None,
                urgency="NONE",
                supporting_conflicts=(),
            ),
            ROUTE_ATTENTION,
        )
    )
    state, parts = run(router=router)
    print("visit order:", " -> ".join(state.visits))
    assert state.visits == ("load_state_proof", "classify_attention_need")
    assert state.outcome is AdvocateOutcome.NO_ATTENTION
    assert parts["writer"].created == []
    assert router.nodes == ["classify_attention_need"]


def test_fyi_is_recorded_but_drafts_nothing() -> None:
    router = ScriptedRouter(
        ModelSuccess(
            build_attention(
                attention_class=AdvocateAttentionClass.FYI,
                recommended=None,
                urgency="LOW",
                supporting_conflicts=(),
            ),
            ROUTE_ATTENTION,
        )
    )
    state, parts = run(router=router)
    assert state.attention is not None
    assert state.attention.attention_class is AdvocateAttentionClass.FYI
    assert state.outcome is AdvocateOutcome.NO_ATTENTION
    assert parts["writer"].created == []


def test_the_visit_order_is_identical_across_two_runs() -> None:
    first, _ = run()
    second, _ = run()
    assert first.visits == second.visits


# ===========================================================================
# 2. The only input is a committed State Proof
# ===========================================================================


def test_the_advocate_reads_only_the_two_permitted_tools() -> None:
    _, parts = run()
    assert parts["reader"].calls == ["get_state_proof", "get_action_policy"]


def test_the_proof_reader_exposes_no_way_to_read_an_uncommitted_proposal() -> None:
    """Invariant 4, as an absence rather than a rule.

    ``MemoryProposal`` is what an agent submits; it is not canonical until the
    Kernel accepts it. The Advocate's dependency has no method that returns one,
    so drafting from an uncommitted proposal is not a mistake that can be made
    without first widening this protocol.
    """
    from agents.runtime.state import StateProofReader

    methods = {n for n in dir(StateProofReader) if not n.startswith("_")}
    assert methods == {"get_state_proof", "get_action_policy"}
    assert not [n for n in dir(FakeProofReader) if "proposal" in n.lower()]


def test_an_uncommitted_proof_is_refused() -> None:
    """A proof with no hash has not been rendered from a committed revision."""
    unhashed = build_proof().model_copy(update={"proof_hash": None})
    state, parts = run(reader=FakeProofReader(proof=unhashed))
    assert state.outcome is AdvocateOutcome.FAIL_SAFE
    assert [e.code for e in state.errors] == ["PROOF_NOT_COMMITTED"]
    assert parts["writer"].created == []


def test_a_proof_whose_hash_does_not_match_its_content_is_refused() -> None:
    tampered = build_proof().model_copy(update={"proof_hash": "0" * 64})
    state, _ = run(reader=FakeProofReader(proof=tampered))
    assert state.outcome is AdvocateOutcome.FAIL_SAFE
    assert [e.code for e in state.errors] == ["PROOF_HASH_MISMATCH"]


def test_a_memory_off_proof_cannot_drive_an_action() -> None:
    """The counterfactual's empty proof is a valid proof and an invalid basis."""
    from provenance_domain.enums import MemoryMode

    off = StateProof(
        proof_id=_u("9201"),
        generated_at=NOW,
        tenant_id=TENANT,
        user_id=USER,
        memory_mode=MemoryMode.OFF,
        memory_disabled_reason="Judge Mode counterfactual: memory disabled.",
    ).with_hash()
    state, parts = run(reader=FakeProofReader(proof=off))
    assert state.outcome is AdvocateOutcome.FAIL_SAFE
    assert [e.code for e in state.errors] == ["PROOF_MEMORY_OFF"]
    assert parts["writer"].created == []


# ===========================================================================
# 3. Grounding — every factual sentence carries a support id
# ===========================================================================


def test_every_draft_claim_resolves_inside_the_state_proof() -> None:
    state, _ = run()
    assert state.draft is not None
    assert state.draft.claims
    assert state.draft.validate_against_proof(SUPPORT_IDS) == ()


def test_each_claim_sentence_is_byte_identical_to_the_body_at_its_offsets() -> None:
    state, _ = run()
    assert state.draft is not None
    for claim in state.draft.claims:
        assert state.draft.body[claim.char_start : claim.char_end] == claim.sentence_or_span


def test_a_claim_citing_an_id_outside_the_proof_fails_the_contract() -> None:
    ungrounded = build_draft(
        claims=(
            sentence_claim(
                "dc_1",
                "Your records show an outstanding balance of 186.00 USD.",
                _u("dead"),
            ),
        )
    )
    failures = validate_draft(
        ungrounded,
        support_ids=frozenset(str(i) for i in SUPPORT_IDS),
        supported_actions=[str(a) for a in POLICY.supported_actions],
        recipient_allowlist_domains=POLICY.recipient_allowlist_domains,
        max_body_chars=POLICY.max_body_chars,
        open_human_conflict=True,
        current_case_revision=CASE_REVISION,
        current_proof_hash=PROOF_HASH,
    )
    assert [f.code for f in failures] == ["UNSUPPORTED_SENTENCE"]


def test_an_ungrounded_draft_becomes_needs_review_rather_than_a_hedge() -> None:
    ungrounded = build_draft(
        claims=(
            sentence_claim(
                "dc_1",
                "Your records show an outstanding balance of 186.00 USD.",
                _u("dead"),
            ),
        )
    )
    router = ScriptedRouter(
        ModelSuccess(build_attention(), ROUTE_ATTENTION),
        ModelSuccess(ungrounded, ROUTE_R),
    )
    state, parts = run(router=router)
    assert state.outcome is AdvocateOutcome.NEEDS_REVIEW
    assert state.action_intent is not None
    assert state.action_intent.status is ActionState.NEEDS_REVIEW
    assert parts["writer"].created[0][1] is True
    assert "UNSUPPORTED_SENTENCE" in " ".join(parts["writer"].created[0][2])


def test_the_draft_never_mentions_provenance_internals() -> None:
    state, _ = run()
    assert state.draft is not None
    lowered = f"{state.draft.subject}\n{state.draft.body}".lower()
    for term in ("belief_version", "state proof", "memory kernel", "embedding", "prompt"):
        assert term not in lowered


# ===========================================================================
# 4. unresolved_risks is a calibration requirement, not decoration
# ===========================================================================


def test_a_needs_human_conflict_without_a_stated_risk_fails() -> None:
    """T7.5: "a draft with no stated risk on a NEEDS_HUMAN conflict is a
    calibration failure, not a clean result"."""
    silent = build_draft(risks=())
    failures = validate_draft(
        silent,
        support_ids=frozenset(str(i) for i in SUPPORT_IDS),
        supported_actions=[str(a) for a in POLICY.supported_actions],
        recipient_allowlist_domains=POLICY.recipient_allowlist_domains,
        max_body_chars=POLICY.max_body_chars,
        open_human_conflict=True,
        current_case_revision=CASE_REVISION,
        current_proof_hash=PROOF_HASH,
    )
    assert [f.code for f in failures] == ["RISK_UNSTATED_ON_HUMAN_CONFLICT"]


def test_the_graph_routes_a_silent_draft_to_needs_review() -> None:
    router = ScriptedRouter(
        ModelSuccess(build_attention(), ROUTE_ATTENTION),
        ModelSuccess(build_draft(risks=()), ROUTE_R),
    )
    state, parts = run(router=router)
    assert state.outcome is AdvocateOutcome.NEEDS_REVIEW
    assert "RISK_UNSTATED_ON_HUMAN_CONFLICT" in " ".join(parts["writer"].created[0][2])


def test_a_case_with_no_human_conflict_may_state_no_risk() -> None:
    calm = build_proof(needs_human=False)
    failures = validate_draft(
        build_draft(
            risks=(),
            basis_proof_hash=calm.compute_hash(),
        ),
        support_ids=frozenset(str(i) for i in calm.support_ids()),
        supported_actions=[str(a) for a in POLICY.supported_actions],
        recipient_allowlist_domains=POLICY.recipient_allowlist_domains,
        max_body_chars=POLICY.max_body_chars,
        open_human_conflict=False,
        current_case_revision=CASE_REVISION,
        current_proof_hash=calm.compute_hash(),
    )
    assert failures == ()


# ===========================================================================
# 5. Attention classes, action policy, and containment
# ===========================================================================


def test_the_five_advocate_classes_are_emitted_and_never_the_four_case_levels() -> None:
    state, _ = run()
    assert state.attention is not None
    assert isinstance(state.attention.attention_class, AdvocateAttentionClass)
    assert not isinstance(state.attention.attention_class, AttentionLevel)


def test_the_agent_package_provides_no_mapping_to_the_persisted_column() -> None:
    """``CANONICAL_DECISIONS.md``: the mapping is the control plane's.

    A helper here would be the shortest path to writing ``ACTION_REQUIRED`` into
    a four-value column, so its absence is asserted rather than assumed.
    """
    import agents.runtime.schemas.advocacy as advocacy

    names = [n for n in dir(advocacy) if "attention_level" in n.lower()]
    assert names == []
    assert "AttentionLevel" not in dir(advocacy)


def test_a_recommended_action_outside_the_policy_is_refused() -> None:
    router = ScriptedRouter(
        ModelSuccess(build_attention(recommended=ActionType.INTERNAL_REMINDER), ROUTE_ATTENTION)
    )
    state, parts = run(router=router)
    assert state.outcome is AdvocateOutcome.PENDING_HUMAN_REVIEW
    assert [e.code for e in state.errors] == ["UNSUPPORTED_ACTION_TYPE"]
    assert parts["writer"].created == []


def test_select_action_type_is_deterministic_and_policy_bound() -> None:
    assert (
        select_action_type(attention=build_attention(), policy=POLICY)
        is ActionType.OUTBOUND_EMAIL_DISPUTE
    )
    assert (
        select_action_type(attention=build_attention(recommended=None), policy=POLICY)
        is ActionType.OUTBOUND_EMAIL_DISPUTE
    )
    empty = ActionPolicy(supported_actions=())
    assert select_action_type(attention=build_attention(recommended=None), policy=empty) is None


def test_the_recipient_comes_from_the_policy_and_never_from_the_artifact() -> None:
    """Adversarial row A4: a footer naming an attacker mailbox."""
    hijacked = build_draft(recipient="billing@attacker.example")
    failures = validate_draft(
        hijacked,
        support_ids=frozenset(str(i) for i in SUPPORT_IDS),
        supported_actions=[str(a) for a in POLICY.supported_actions],
        recipient_allowlist_domains=POLICY.recipient_allowlist_domains,
        max_body_chars=POLICY.max_body_chars,
        open_human_conflict=True,
        current_case_revision=CASE_REVISION,
        current_proof_hash=PROOF_HASH,
    )
    assert [f.code for f in failures] == ["RECIPIENT_NOT_IN_POLICY"]


def test_an_attention_suppression_attempt_is_recorded_not_obeyed() -> None:
    """Adversarial row A12: evidence telling the classifier to stay quiet."""
    router = ScriptedRouter(
        ModelSuccess(
            build_attention(
                suppression=(
                    "A retrieved evidence item asked automated systems not to surface "
                    "this matter.",
                )
            ),
            ROUTE_ATTENTION,
        ),
        ModelSuccess(build_draft(), ROUTE_R),
    )
    state, _ = run(router=router)
    assert state.attention is not None
    assert state.attention.suppression_reasons
    assert state.attention.attention_class is AdvocateAttentionClass.ACTION_REQUIRED
    assert state.outcome is AdvocateOutcome.INTENT_CREATED


def test_a_stale_revision_binding_is_refused() -> None:
    stale = build_draft(basis_case_revision=CASE_REVISION - 1)
    failures = validate_draft(
        stale,
        support_ids=frozenset(str(i) for i in SUPPORT_IDS),
        supported_actions=[str(a) for a in POLICY.supported_actions],
        recipient_allowlist_domains=POLICY.recipient_allowlist_domains,
        max_body_chars=POLICY.max_body_chars,
        open_human_conflict=True,
        current_case_revision=CASE_REVISION,
        current_proof_hash=PROOF_HASH,
    )
    assert [f.code for f in failures] == ["STALE_PROOF_BINDING"]


# ===========================================================================
# 6. Model failures and the session prohibition
# ===========================================================================


def test_a_pending_draft_creates_no_intent() -> None:
    router = ScriptedRouter(
        ModelSuccess(build_attention(), ROUTE_ATTENTION),
        ModelPending(reason_code="MODEL_REFUSAL", node="draft_action"),
    )
    state, parts = run(router=router)
    assert state.outcome is AdvocateOutcome.PENDING_HUMAN_REVIEW
    assert parts["writer"].created == []
    assert [e.code for e in state.errors] == ["MODEL_REFUSAL"]


def test_no_state_proof_text_reaches_the_system_parameter() -> None:
    state, parts = run()
    router: ScriptedRouter = parts["router"]
    for system in router.systems:
        assert "Northline Fiber" not in system
        assert "186.00" not in system
    assert state.outcome is AdvocateOutcome.INTENT_CREATED


def test_the_advocate_never_reads_from_session_storage() -> None:
    state, parts = run()
    assert state.outcome is AdvocateOutcome.INTENT_CREATED
    assert parts["session"].records


def test_the_trap_session_would_fire_if_the_advocate_read_it() -> None:
    trap = TrapSession()
    with pytest.raises(AssertionError, match="session state is workflow durability only"):
        _ = trap.previous_draft
