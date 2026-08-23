"""T4.4/T4.5 - gates H1-H8, the four dispositions, and the hero's H5.

`specs/12_KERNEL_ALGORITHMS.md` section 3, and `CANONICAL_DECISIONS.md` ->
*Hero conflict*, which fixes the outcome these tests defend: `VALUE_CONFLICT`,
family `BALANCE`, `status = 'NEEDS_HUMAN'`, `severity = 'HIGH'`,
`requires_human = true`, disposition `RETAIN_INCUMBENT_DISPUTED`, produced by
gate H5 short-circuiting **before** the authority-margin test.

The ordering is the assertion. Every predicate in `decide()` could be right and
the hero could still be wrong if H5 ran after the margin, because the hero's
margin would auto-resolve it.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from provenance_domain.enums import (
    AdvocateAttentionClass,
    AttentionLevel,
    ClaimKind,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    KernelReasonCode,
)
from services.control_plane.app.memory_kernel import contradiction as cx
from services.control_plane.app.memory_kernel import disposition as disp
from services.control_plane.app.memory_kernel import families as fam
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG

pytestmark = pytest.mark.unit

Make = Callable[..., prop.Proposition]


def _finding(
    incumbent: prop.Proposition,
    challenger: prop.Proposition,
    *,
    conflict_type: ConflictType = ConflictType.VALUE_CONFLICT,
    family: fam.Family = fam.Family.SERVICE_STATUS,
    exposure: Decimal = Decimal("0.0000"),
    severity: ConflictSeverity = ConflictSeverity.MEDIUM,
    blocks_approved_action: bool = False,
    post_dispute_confidence_below_floor: bool = False,
    matcher_rule: str = "M1",
) -> cx.ConflictFinding:
    """A finding built by hand, so a disposition test never depends on the
    matcher being right about something unrelated."""
    return cx.ConflictFinding(
        conflict_type=conflict_type,
        family=family,
        incumbent=incumbent,
        challenger=challenger,
        predicate=fam.canonical_predicate(family) or "service_active",
        subject_type=incumbent.subject_type,
        subject_id=incumbent.subject_id,
        matcher_rule=matcher_rule,
        monetary_exposure=exposure,
        severity=severity,
        blocks_approved_action=blocks_approved_action,
        post_dispute_confidence_below_floor=post_dispute_confidence_below_floor,
    )


# --- the hero -----------------------------------------------------------------


def test_the_hero_conflict_needs_a_human_because_of_the_amount(
    hero: Any, make_proposition: Make
) -> None:
    """`CANONICAL_DECISIONS.md` -> *Hero conflict*, in full.

    The incumbent (no balance recorded, 0.90) outranks the challenger by
    nothing at all here - the point is that the arithmetic is never reached.
    186.00 is at or above the 100.00 review threshold, the family is monetary,
    and H5 short-circuits: `NEEDS_HUMAN`, `requires_human = true`, disposition
    `RETAIN_INCUMBENT_DISPUTED`, value unchanged, `CONFIRMED -> DISPUTED`.
    """
    incumbent = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        base_authority=Decimal("0.9000"),
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.9400"),
        is_incumbent=True,
    )
    challenger = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
        base_authority=Decimal("0.5000"),
    )
    d = disp.decide(
        _finding(
            incumbent,
            challenger,
            family=fam.Family.BALANCE,
            exposure=hero.invoice_amount,
            severity=ConflictSeverity.HIGH,
            matcher_rule="M3",
        )
    )
    assert d.gate == "H5"
    assert d.kind is disp.DispositionKind.RETAIN_INCUMBENT_DISPUTED
    assert d.conflict_status is ConflictStatus.NEEDS_HUMAN
    assert d.requires_human is True
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_MONETARY_THRESHOLD
    assert d.epistemic_status_after is EpistemicStatus.DISPUTED
    assert d.value_changes is False


def test_h5_short_circuits_before_the_authority_margin(hero: Any, make_proposition: Make) -> None:
    """T4.5 acceptance, stated as its own test because it is an *ordering*
    property. Give the incumbent an overwhelming margin - 0.97 against 0.05,
    which would auto-resolve instantly - and H5 must still win because the
    money is over the threshold."""
    incumbent = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        base_authority=Decimal("0.9700"),
        is_incumbent=True,
    )
    challenger = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
        base_authority=Decimal("0.0500"),
    )
    d = disp.decide(
        _finding(incumbent, challenger, family=fam.Family.BALANCE, exposure=Decimal("186.0000"))
    )
    assert d.gate == "H5"
    assert d.requires_human is True


def test_monetary_exposure_below_the_threshold_reaches_the_margin(
    hero: Any, make_proposition: Make
) -> None:
    """The complement, without which the previous test could pass on a rule
    that simply always demands a human for money."""
    incumbent = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        base_authority=Decimal("0.9000"),
        is_incumbent=True,
    )
    challenger = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("40.0000")),
        base_authority=Decimal("0.4500"),
    )
    d = disp.decide(
        _finding(incumbent, challenger, family=fam.Family.BALANCE, exposure=Decimal("40.0000"))
    )
    assert d.gate is None
    assert d.kind is disp.DispositionKind.RETAIN_INCUMBENT_AUTO
    assert d.conflict_status is ConflictStatus.AUTO_RESOLVED


def test_the_threshold_is_inclusive_at_exactly_one_hundred(
    hero: Any, make_proposition: Make
) -> None:
    """`monetary_exposure >= 100.00`. An exclusive comparison would let a
    round-number dispute through on the boundary."""
    incumbent = make_proposition(
        family=fam.Family.BALANCE,
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        base_authority=Decimal("0.9000"),
        is_incumbent=True,
    )
    challenger = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        value=fam.BalanceValue("USD", Decimal("100.0000")),
        base_authority=Decimal("0.4500"),
    )
    d = disp.decide(
        _finding(incumbent, challenger, family=fam.Family.BALANCE, exposure=Decimal("100.0000"))
    )
    assert d.gate == "H5"


def test_a_non_monetary_family_never_reaches_h5(hero: Any, make_proposition: Make) -> None:
    """Section 3.3 gates H5 on `f.family in MONETARY_FAMILIES`. Applying a money
    threshold to a service-status disagreement is the exact confusion T4.4's
    sub-tasks warn about."""
    incumbent = make_proposition(
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.8800"),
        is_incumbent=True,
    )
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.5800"))
    d = disp.decide(_finding(incumbent, challenger, exposure=Decimal("5000.0000")))
    assert d.gate is None
    assert d.requires_human is False


# --- the other mandatory gates -------------------------------------------------


def test_h1_an_authority_conflict_always_needs_a_human(hero: Any, make_proposition: Make) -> None:
    """Two credible sources disagree and no deterministic winner exists.
    `00_IMPLEMENTATION_MAP.md` section 12's rule - do not silently resolve two
    high-authority conflicting sources - is enforced *here*, by construction,
    rather than by a reviewer noticing."""
    incumbent = make_proposition(base_authority=Decimal("0.8800"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.8800"))
    d = disp.decide(_finding(incumbent, challenger, conflict_type=ConflictType.AUTHORITY_CONFLICT))
    assert d.gate == "H1"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE
    assert d.conflict_status is ConflictStatus.NEEDS_HUMAN


def test_h2_a_commitment_withdrawal_always_needs_a_human(hero: Any, make_proposition: Make) -> None:
    """The obligor is retracting their own promise. Authority is symmetric by
    definition, so a margin test is meaningless. Always human, always."""
    incumbent = make_proposition(base_authority=Decimal("0.9500"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.1000"))
    d = disp.decide(
        _finding(
            incumbent,
            challenger,
            conflict_type=ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT,
            family=fam.Family.COMMITMENT_STATUS,
        )
    )
    assert d.gate == "H2"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_WITHDRAWAL


@pytest.mark.parametrize("kind", [ClaimKind.USER_CLAIM, ClaimKind.CORRECTION])
def test_h4_a_user_dispute_never_auto_resolves_in_either_direction(
    hero: Any, make_proposition: Make, kind: ClaimKind
) -> None:
    """The user disputing their own record is the strongest possible signal
    that the model got something wrong. Auto-resolving *in the user's favour*
    is just as wrong as auto-resolving against them: both decide on the user's
    behalf about the one thing they explicitly weighed in on."""
    incumbent = make_proposition(base_authority=Decimal("0.9500"), is_incumbent=True)
    challenger = make_proposition(
        prop_id=hero.cl_invoice, base_authority=Decimal("0.0500"), source_claim_kind=kind
    )
    d = disp.decide(_finding(incumbent, challenger))
    assert d.gate == "H4"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_USER_DISPUTE


def test_h6_a_conflict_blocking_an_approved_action_needs_a_human(
    hero: Any, make_proposition: Make
) -> None:
    """Invariant 4. A pending external side effect must not have its basis
    silently rewritten while a human approval is outstanding."""
    incumbent = make_proposition(base_authority=Decimal("0.9500"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.0500"))
    d = disp.decide(_finding(incumbent, challenger, blocks_approved_action=True))
    assert d.gate == "H6"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_ACTION_BLOCKING


def test_h7_a_decayed_belief_under_a_pending_action_needs_a_human(
    hero: Any, make_proposition: Make
) -> None:
    """Section 3.4 H7: grounded advocacy requires a belief worth advocating.
    Section 3.3's printed function omits this gate; it is implemented behind an
    explicit flag that defaults to false, so the default path is byte-for-byte
    the printed one."""
    incumbent = make_proposition(base_authority=Decimal("0.9500"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.0500"))
    d = disp.decide(_finding(incumbent, challenger, post_dispute_confidence_below_floor=True))
    assert d.gate == "H7"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_ACTION_BLOCKING


def test_h8_an_unresolvable_conflict_type_fails_closed(hero: Any, make_proposition: Make) -> None:
    """Anything outside `AUTO_RESOLVABLE_TYPES` routes to a person. Failing
    closed on a type the table does not cover is what keeps a future
    `conflict_type` from silently inheriting auto-resolution."""
    incumbent = make_proposition(base_authority=Decimal("0.9500"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.0500"))
    d = disp.decide(
        _finding(incumbent, challenger, conflict_type=ConflictType.POLICY_VERSION_CONFLICT)
    )
    assert d.gate == "H8"
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE


def test_the_auto_resolvable_types_are_exactly_three() -> None:
    """Section 3.3. `AUTHORITY_CONFLICT` and `COMMITMENT_WITHDRAWAL_CONFLICT`
    are deliberately absent; adding either would make H1 and H2 unreachable."""
    assert {
        ConflictType.VALUE_CONFLICT,
        ConflictType.TEMPORAL_CONFLICT,
        ConflictType.FULFILLMENT_CONFLICT,
    } == disp.AUTO_RESOLVABLE_TYPES


# --- deterministic auto-resolution --------------------------------------------


def test_the_entailment_penalty_retains_the_incumbent_automatically(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """Section 1.6 step 12, the forward hero. 0.88 against 0.58 is a 0.30
    margin, the winner clears the 0.80 floor, the family is not monetary, and
    one side is entailed, so the reason code names the penalty rather than the
    margin. That sentence is what State Proof renders."""
    d = disp.decide(_finding(incumbent_terminated, entailed_active))
    assert d.kind is disp.DispositionKind.RETAIN_INCUMBENT_AUTO
    assert d.conflict_status is ConflictStatus.AUTO_RESOLVED
    assert d.requires_human is False
    assert d.reason_code is KernelReasonCode.AUTO_RESOLVED_ENTAILMENT_PENALTY
    assert d.value_changes is False


def test_the_reversed_hero_promotes_the_challenger(hero: Any, make_proposition: Make) -> None:
    """Section 8.7 L-2. The entailed ACTIVE belief is incumbent, the direct
    cancellation confirmation arrives later, and the margin points the other
    way: `PROMOTE_CHALLENGER_AUTO`, v1 superseded, v2 current."""
    incumbent = make_proposition(
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        base_authority=Decimal("0.8800"),
        entailed_from=hero.cl_invoice,
        entailment_rule="EN-1",
        is_incumbent=True,
    )
    challenger = make_proposition(
        prop_id=hero.cl_terminated,
        predicate="service_terminated",
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.8800"),
        recorded_at=hero.forward_recorded_at,
    )
    d = disp.decide(_finding(incumbent, challenger))
    assert d.kind is disp.DispositionKind.PROMOTE_CHALLENGER_AUTO
    assert d.conflict_status is ConflictStatus.AUTO_RESOLVED
    assert d.reason_code is KernelReasonCode.AUTO_RESOLVED_ENTAILMENT_PENALTY
    assert d.value_changes is True


def test_a_clean_margin_between_direct_sources_names_the_margin(
    hero: Any, make_proposition: Make
) -> None:
    """When neither side is entailed the reason code must say so, because
    "a direct statement beat an entailed one" would be a false explanation."""
    incumbent = make_proposition(base_authority=Decimal("0.9200"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.6000"))
    d = disp.decide(_finding(incumbent, challenger))
    assert d.reason_code is KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN


def test_a_wide_margin_below_the_floor_still_needs_a_human(
    hero: Any, make_proposition: Make
) -> None:
    """Both conditions are required: `abs(delta) >= margin` **and**
    `winner.authority >= floor`. A 0.60-to-0.05 gap is wide, and 0.60 is still
    not authoritative enough to rewrite canonical state on its own."""
    incumbent = make_proposition(base_authority=Decimal("0.6000"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.0500"))
    d = disp.decide(_finding(incumbent, challenger))
    assert d.requires_human is True
    assert d.reason_code is KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE


def test_an_exact_tie_goes_to_the_incumbent_but_never_auto_resolves(
    hero: Any, make_proposition: Make
) -> None:
    """`winner = inc if delta >= 0 else chal`, but a tie cannot clear the
    margin. Nothing dislodges canonical state by drawing level with it, and
    nothing resolves a genuine disagreement by coin flip."""
    incumbent = make_proposition(base_authority=Decimal("0.7000"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.7000"))
    d = disp.decide(_finding(incumbent, challenger))
    assert d.kind is disp.DispositionKind.RETAIN_INCUMBENT_DISPUTED
    assert d.requires_human is True


def test_temporal_precedence_promotes_only_the_same_actor(
    hero: Any, make_proposition: Make
) -> None:
    """Section 3.3's narrow tie-break. An actor may amend their own statements;
    a third party may not silently rewrite the validity of somebody else's."""
    incumbent = make_proposition(
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.8800"),
        actor_ref="northline-fiber",
        valid_from=hero.may_1,
        recorded_at=hero.confirmation_recorded_at,
        is_incumbent=True,
    )
    same_actor = make_proposition(
        prop_id=hero.cl_terminated,
        base_authority=Decimal("0.8800"),
        actor_ref="northline-fiber",
        valid_from=hero.jun_1,
        recorded_at=hero.forward_recorded_at,
    )
    other_actor = make_proposition(
        prop_id=hero.cl_other,
        base_authority=Decimal("0.8800"),
        actor_ref="someone-else",
        valid_from=hero.jun_1,
        recorded_at=hero.forward_recorded_at,
    )
    promoted = disp.decide(_finding(incumbent, same_actor))
    refused = disp.decide(_finding(incumbent, other_actor))
    assert promoted.kind is disp.DispositionKind.PROMOTE_CHALLENGER_AUTO
    assert promoted.reason_code is KernelReasonCode.AUTO_RESOLVED_TEMPORAL_PRECEDENCE
    assert refused.requires_human is True


# --- the negative that keeps a future rule honest ------------------------------


def test_no_disposition_rule_ever_emits_status_open(hero: Any, make_proposition: Make) -> None:
    """T4.5 acceptance and `CANONICAL_DECISIONS.md`: `OPEN` is a legal
    `conflicts.status` value that **no** disposition rule emits, and only a
    negative test keeps a future rule from quietly emitting it. Swept over the
    cross-product of every conflict type, family, claim kind, gate flag and
    both sides of every threshold."""
    authorities = (Decimal("0.0500"), Decimal("0.5800"), Decimal("0.8800"), Decimal("0.9700"))
    exposures = (Decimal("0.0000"), Decimal("99.9900"), Decimal("186.0000"), Decimal("5000.00"))
    kinds = (ClaimKind.COUNTERPARTY_CLAIM, ClaimKind.USER_CLAIM, ClaimKind.CORRECTION)
    seen: set[ConflictStatus | None] = set()
    for ctype, family, inc_a, chal_a, exposure, kind, blocked in itertools.product(
        ConflictType, fam.Family, authorities, authorities, exposures, kinds, (False, True)
    ):
        incumbent = make_proposition(base_authority=inc_a, is_incumbent=True)
        challenger = make_proposition(
            prop_id=hero.cl_invoice, base_authority=chal_a, source_claim_kind=kind
        )
        d = disp.decide(
            _finding(
                incumbent,
                challenger,
                conflict_type=ctype,
                family=family,
                exposure=exposure,
                blocks_approved_action=blocked,
            )
        )
        seen.add(d.conflict_status)
    assert ConflictStatus.OPEN not in seen
    assert seen <= {ConflictStatus.AUTO_RESOLVED, ConflictStatus.NEEDS_HUMAN}


def test_open_is_nonetheless_a_legal_column_value() -> None:
    """The negative above is about the *rules*, not about the schema.
    `ck_conflicts_status` admits `OPEN`, and a test that removed it from the
    enum would be asserting the wrong thing."""
    assert ConflictStatus.OPEN in set(ConflictStatus)


def test_a_needs_human_disposition_never_claims_auto_resolution(
    hero: Any, make_proposition: Make
) -> None:
    """`ck_conflicts_requires_human_consistent`: `NOT requires_human OR status
    <> 'AUTO_RESOLVED'`. A disposition that violated this would be rejected by
    the database at the last possible moment, inside the transaction."""
    incumbent = make_proposition(base_authority=Decimal("0.8800"), is_incumbent=True)
    challenger = make_proposition(prop_id=hero.cl_invoice, base_authority=Decimal("0.8800"))
    for ctype in ConflictType:
        d = disp.decide(_finding(incumbent, challenger, conflict_type=ctype))
        assert not (d.requires_human and d.conflict_status is ConflictStatus.AUTO_RESOLVED)


# --- the belief effects --------------------------------------------------------


def test_a_disputed_retention_decays_confidence_but_never_below_the_floor() -> None:
    """Section 3.1's formula, and its floor. A belief that has been contradicted
    is worth less; a belief worth nothing at all would drop out of every read
    model and look deleted."""
    assert disp.disputed_confidence(Decimal("0.9400"), Decimal("0.5000")) == Decimal("0.7520")
    assert disp.disputed_confidence(Decimal("0.0600"), Decimal("1.0000")) == Decimal("0.0500")


def test_an_auto_retained_incumbent_keeps_its_confidence_unchanged(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """Section 3.1: the incumbent won on the merits, so decaying it would be
    arbitrary. Only `RETAIN_INCUMBENT_DISPUTED` decays."""
    d = disp.decide(_finding(incumbent_terminated, entailed_active))
    assert d.kind is disp.DispositionKind.RETAIN_INCUMBENT_AUTO
    assert d.epistemic_status_after is None


@pytest.mark.parametrize(
    ("authority", "status"),
    [
        (Decimal("0.9000"), EpistemicStatus.CONFIRMED),
        (Decimal("0.8999"), EpistemicStatus.PROBABLE),
        (Decimal("0.5800"), EpistemicStatus.PROBABLE),
    ],
)
def test_a_first_belief_version_is_confirmed_only_above_the_floor(
    authority: Decimal, status: EpistemicStatus
) -> None:
    """Section 3.1 row 1 and section 8.7 L-1: the invoice-first universe writes
    `ACTIVE` at 0.58, which is `PROBABLE`, not `CONFIRMED`. Calling an inferred
    belief confirmed is how a memory system starts lying confidently."""
    d = disp.decide_no_incumbent(authority)
    assert d.kind is disp.DispositionKind.NO_INCUMBENT
    assert d.conflict_status is None
    assert d.epistemic_status_after is status
    assert d.reason_code is KernelReasonCode.BELIEF_CREATED


def test_a_no_incumbent_disposition_writes_no_conflict_row() -> None:
    """Section 3.1's table: the `NO_INCUMBENT` row's `conflicts.status` column
    is "(no conflict row)". Nothing was contradicted."""
    assert disp.decide_no_incumbent(Decimal("0.9500")).conflict_status is None


# --- the advocate attention mapping --------------------------------------------


@pytest.mark.parametrize(
    ("advocate", "case"),
    [
        (AdvocateAttentionClass.NONE, AttentionLevel.NONE),
        (AdvocateAttentionClass.FYI, AttentionLevel.INFO),
        (AdvocateAttentionClass.ACTION_SUGGESTED, AttentionLevel.ATTENTION),
        (AdvocateAttentionClass.ACTION_REQUIRED, AttentionLevel.URGENT),
        (AdvocateAttentionClass.HUMAN_DECISION, AttentionLevel.URGENT),
    ],
)
def test_advocate_classes_map_deterministically_onto_case_attention(
    advocate: AdvocateAttentionClass, case: AttentionLevel
) -> None:
    """`specs/14_PROMPTS.md` section 4. The advocate's classes are a model
    output and `cases.attention_level` accepts four values, none of which are
    those five. Writing `ACTION_REQUIRED` straight into the column is the
    defect `EXECUTION/72_DEFECT_PROTOCOL.md` section 8 exists to name, and it
    passes any Pydantic model that types the column as `str`."""
    assert disp.case_attention_for(advocate) is case


def test_human_decision_is_distinguishable_from_action_required() -> None:
    """Both map to `URGENT`, so the mapping alone loses the distinction. The
    second output is what carries `requires_human_decision = true` into the
    action policy."""
    assert disp.requires_human_decision(AdvocateAttentionClass.HUMAN_DECISION) is True
    assert disp.requires_human_decision(AdvocateAttentionClass.ACTION_REQUIRED) is False


def test_the_mapping_is_total_over_the_closed_advocate_set() -> None:
    """A class with no mapping would be a silent `KeyError` on the one path
    that decides how loudly the user is told."""
    assert set(disp.ADVOCATE_TO_CASE_ATTENTION) == set(AdvocateAttentionClass)
    assert set(disp.ADVOCATE_TO_CASE_ATTENTION.values()) <= set(AttentionLevel)


def test_the_config_thresholds_actually_gate_the_decision(
    hero: Any, make_proposition: Make
) -> None:
    """A bespoke config must change the verdict, or `human_review_amount_
    threshold` is decoration rather than configuration."""
    from services.control_plane.app.memory_kernel.config import KernelConfig

    incumbent = make_proposition(
        family=fam.Family.BALANCE, base_authority=Decimal("0.9000"), is_incumbent=True
    )
    challenger = make_proposition(
        prop_id=hero.cl_invoice, family=fam.Family.BALANCE, base_authority=Decimal("0.4500")
    )
    finding = _finding(
        incumbent, challenger, family=fam.Family.BALANCE, exposure=Decimal("40.0000")
    )
    strict = KernelConfig(human_review_amount_threshold=Decimal("10.00"))
    assert disp.decide(finding).gate is None
    assert disp.decide(finding, strict).gate == "H5"
    assert DEFAULT_KERNEL_CONFIG.human_review_amount_threshold == Decimal("100.00")
