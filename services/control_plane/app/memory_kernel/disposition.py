"""Auto-resolution versus human review: gates H1-H8 and the four dispositions.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.1 owns the four dispositions and
  the confidence formula.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.3 owns :func:`decide`, including
  the order of the gates.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.4 owns the gate table ``H1``-``H8``.
- ``CANONICAL_DECISIONS.md`` -> *Hero conflict*: the hero resolves on ``H5``,
  which short-circuits **before** the authority-margin test. ``status = 'OPEN'``
  is a legal column value that no disposition rule emits.
- ``specs/14_PROMPTS.md`` section 4 owns the advocate-class mapping.

Disposition is not canonical retention
--------------------------------------
Two questions the surrounding documents conflate. *Which value stays canonical?*
is always answered deterministically. *Does a human have to look at it?* is a
separate answer, and this module keeps them separate: every gate that routes to
a human still says what stayed canonical, on
:attr:`Disposition.epistemic_status_after` and :attr:`Disposition.value_changes`.

Rule G3 and why even a retained incumbent writes a new version
--------------------------------------------------------------
``belief_support`` rows are append-only and are written only in the transaction
that creates the version they attach to. Any change in *grounding* therefore
requires a new belief version, so ``RETAIN_INCUMBENT_AUTO`` writes v(n+1) with
the same value and a new ``CONTRADICTS`` edge. That version is what State Proof
renders as "confirmed, contradicted, and retained - here is why", which is the
product's most persuasive single screen.

Recorded discrepancy
--------------------
Section 3.4's table lists gate ``H7`` (a post-dispute confidence below the
action floor, under a pending action intent) but section 3.3's printed
``decide()`` omits it. It is implemented here behind
:attr:`ConflictFinding.post_dispute_confidence_below_floor`, which defaults to
``False``, so the default control flow is byte-for-byte the printed one and the
gate is reachable only when a caller has actually established the condition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from provenance_domain.enums import (
    AdvocateAttentionClass,
    AttentionLevel,
    ClaimKind,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    KernelReasonCode,
)
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)
from services.control_plane.app.memory_kernel.contradiction import ConflictFinding
from services.control_plane.app.memory_kernel.families import Family, is_monetary

__all__ = [
    "ADVOCATE_TO_CASE_ATTENTION",
    "AUTO_RESOLVABLE_TYPES",
    "HUMAN_DISPUTE_CLAIM_KINDS",
    "Disposition",
    "DispositionKind",
    "case_attention_for",
    "decide",
    "decide_no_incumbent",
    "disputed_confidence",
    "requires_human_decision",
]

#: Section 3.3. Everything else fails closed onto ``H8``. ``AUTHORITY_CONFLICT``
#: and ``COMMITMENT_WITHDRAWAL_CONFLICT`` are deliberately absent: adding either
#: would make ``H1`` and ``H2`` unreachable.
AUTO_RESOLVABLE_TYPES: Final[frozenset[ConflictType]] = frozenset(
    {
        ConflictType.VALUE_CONFLICT,
        ConflictType.TEMPORAL_CONFLICT,
        ConflictType.FULFILLMENT_CONFLICT,
    }
)

#: Gate ``H4``. The user disputing their own record is the strongest possible
#: signal that the model got something wrong, so neither direction auto-resolves.
HUMAN_DISPUTE_CLAIM_KINDS: Final[frozenset[ClaimKind]] = frozenset(
    {ClaimKind.USER_CLAIM, ClaimKind.CORRECTION}
)

#: ``specs/14_PROMPTS.md`` section 4. The advocate's five classes are a model
#: output; ``cases.attention_level`` accepts four values and none of these.
#: Writing ``ACTION_REQUIRED`` straight into the column is the defect
#: ``EXECUTION/72_DEFECT_PROTOCOL.md`` section 8 exists to name, and it passes
#: any Pydantic model that types the column as ``str``.
ADVOCATE_TO_CASE_ATTENTION: Final[Mapping[AdvocateAttentionClass, AttentionLevel]] = (
    MappingProxyType(
        {
            AdvocateAttentionClass.NONE: AttentionLevel.NONE,
            AdvocateAttentionClass.FYI: AttentionLevel.INFO,
            AdvocateAttentionClass.ACTION_SUGGESTED: AttentionLevel.ATTENTION,
            AdvocateAttentionClass.ACTION_REQUIRED: AttentionLevel.URGENT,
            AdvocateAttentionClass.HUMAN_DECISION: AttentionLevel.URGENT,
        }
    )
)

#: Section 3.1's floor on a decayed confidence. A belief worth nothing at all
#: would drop out of every read model and look deleted.
_CONFIDENCE_FLOOR: Final[Decimal] = Decimal("0.05")
_CONFIDENCE_EXPONENT: Final[Decimal] = Decimal("0.0001")


class DispositionKind(StrEnum):
    """Section 3.1's four dispositions, and no others."""

    NO_INCUMBENT = "NO_INCUMBENT"
    RETAIN_INCUMBENT_AUTO = "RETAIN_INCUMBENT_AUTO"
    PROMOTE_CHALLENGER_AUTO = "PROMOTE_CHALLENGER_AUTO"
    RETAIN_INCUMBENT_DISPUTED = "RETAIN_INCUMBENT_DISPUTED"


@dataclass(frozen=True, slots=True)
class Disposition:
    """What stays canonical, and whether a person has to look.

    The belief effect and the case effect are reported separately because
    ``EXECUTION/70_TASK_PLAN.md`` T4.5 requires it: a disposition that returned
    one verdict for both makes it impossible to say "the value did not change
    but the case now needs attention", which is exactly the hero's shape.
    """

    kind: DispositionKind
    conflict_status: ConflictStatus | None
    requires_human: bool
    reason_code: KernelReasonCode
    gate: str | None = None
    epistemic_status_after: EpistemicStatus | None = None
    value_changes: bool = False
    case_attention: AttentionLevel = AttentionLevel.NONE


def _needs_human(gate: str | None, reason_code: KernelReasonCode) -> Disposition:
    """The shape every mandatory gate returns.

    The incumbent stays canonical with its value unchanged and its status
    ``DISPUTED``: ``NEEDS_HUMAN`` conflicts never block ingestion. Evidence and
    claims are still admitted (invariant 1), the incumbent belief is still
    canonical, and the case is raised to ``ATTENTION`` (section 3.4).
    """
    return Disposition(
        kind=DispositionKind.RETAIN_INCUMBENT_DISPUTED,
        conflict_status=ConflictStatus.NEEDS_HUMAN,
        requires_human=True,
        reason_code=reason_code,
        gate=gate,
        epistemic_status_after=EpistemicStatus.DISPUTED,
        value_changes=False,
        case_attention=AttentionLevel.ATTENTION,
    )


def _status_for(authority: Decimal, cfg: KernelConfig) -> EpistemicStatus:
    """``CONFIRMED`` above the confirmed floor, ``PROBABLE`` below it.

    Section 8.7 L-1's invoice-first universe writes ``ACTIVE`` at 0.58, which is
    ``PROBABLE``. Calling an inferred belief confirmed is how a memory system
    starts lying confidently.
    """
    if authority >= cfg.confirmed_status_floor:
        return EpistemicStatus.CONFIRMED
    return EpistemicStatus.PROBABLE


def decide(f: ConflictFinding, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG) -> Disposition:
    """Section 3.3, gate order preserved.

    ``H1``, ``H2``, ``H4``, ``H5``, ``H6`` and ``H7`` short-circuit before any
    authority arithmetic runs. That ordering is not decoration: the hero's
    conflict has an authority margin that would auto-resolve, and it must not,
    because 186.00 is at or above the human-review threshold.

    ``H3`` is absent by construction. Identity ambiguity is resolved at pipeline
    step 7 and yields ``PENDING_IDENTITY``; no ``conflicts`` row is written, so
    there is no finding for this function to receive.
    """
    incumbent, challenger = f.incumbent, f.challenger

    # ---- mandatory human-review gates, evaluated first, short-circuiting ----
    if f.conflict_type is ConflictType.AUTHORITY_CONFLICT:  # H1
        return _needs_human("H1", KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE)
    if f.conflict_type is ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT:  # H2
        return _needs_human("H2", KernelReasonCode.HUMAN_REQUIRED_WITHDRAWAL)
    if challenger.source_claim_kind in HUMAN_DISPUTE_CLAIM_KINDS:  # H4
        return _needs_human("H4", KernelReasonCode.HUMAN_REQUIRED_USER_DISPUTE)
    if is_monetary(f.family) and f.monetary_exposure >= cfg.human_review_amount_threshold:  # H5
        return _needs_human("H5", KernelReasonCode.HUMAN_REQUIRED_MONETARY_THRESHOLD)
    if f.blocks_approved_action:  # H6
        return _needs_human("H6", KernelReasonCode.HUMAN_REQUIRED_ACTION_BLOCKING)
    if f.post_dispute_confidence_below_floor:  # H7
        return _needs_human("H7", KernelReasonCode.HUMAN_REQUIRED_ACTION_BLOCKING)

    # ---- fail closed on anything the table does not cover ----
    if f.conflict_type not in AUTO_RESOLVABLE_TYPES:  # H8
        return _needs_human("H8", KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE)

    # ---- deterministic auto-resolution ----
    delta = incumbent.authority - challenger.authority
    winner_is_incumbent = delta >= 0
    winner = incumbent if winner_is_incumbent else challenger
    if abs(delta) >= cfg.auto_resolve_margin and winner.authority >= cfg.auto_resolve_floor:
        entailed = incumbent.entailed_from is not None or challenger.entailed_from is not None
        code = (
            KernelReasonCode.AUTO_RESOLVED_ENTAILMENT_PENALTY
            if entailed
            else KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN
        )
        if winner_is_incumbent:
            return Disposition(
                kind=DispositionKind.RETAIN_INCUMBENT_AUTO,
                conflict_status=ConflictStatus.AUTO_RESOLVED,
                requires_human=False,
                reason_code=code,
                epistemic_status_after=None,  # section 3.1: unchanged from v_n
                value_changes=False,
                case_attention=AttentionLevel.INFO,
            )
        return Disposition(
            kind=DispositionKind.PROMOTE_CHALLENGER_AUTO,
            conflict_status=ConflictStatus.AUTO_RESOLVED,
            requires_human=False,
            reason_code=code,
            epistemic_status_after=_status_for(challenger.authority, cfg),
            value_changes=True,
            case_attention=AttentionLevel.INFO,
        )

    # ---- temporal precedence tie-break (narrow, same-actor only) ----
    # An actor may amend their own statements; a third party may not silently
    # rewrite the validity of somebody else's.
    if (
        f.family in (Family.SERVICE_STATUS, Family.BALANCE)
        and incumbent.actor_ref is not None
        and incumbent.actor_ref == challenger.actor_ref
        and challenger.valid_from is not None
        and incumbent.valid_from is not None
        and challenger.valid_from > incumbent.valid_from
        and challenger.recorded_at > incumbent.recorded_at
        and challenger.authority >= cfg.supersession_authority_floor
    ):
        return Disposition(
            kind=DispositionKind.PROMOTE_CHALLENGER_AUTO,
            conflict_status=ConflictStatus.AUTO_RESOLVED,
            requires_human=False,
            reason_code=KernelReasonCode.AUTO_RESOLVED_TEMPORAL_PRECEDENCE,
            epistemic_status_after=_status_for(challenger.authority, cfg),
            value_changes=True,
            case_attention=AttentionLevel.INFO,
        )

    # The residual: no mandatory gate fired, no margin cleared, no same-actor
    # precedence. `gate` stays None because nothing short-circuited - the
    # arithmetic simply found no deterministic winner, which is the honest
    # answer and the one a person can act on.
    return _needs_human(None, KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE)


def decide_no_incumbent(
    challenger_authority: Decimal, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG
) -> Disposition:
    """Section 3.1 row 1: no conflict row, a first belief version.

    ``CONFIRMED`` above the confirmed floor, ``PROBABLE`` below it. A first
    version cannot be disputed, because there is nothing to dispute it with.
    """
    return Disposition(
        kind=DispositionKind.NO_INCUMBENT,
        conflict_status=None,
        requires_human=False,
        reason_code=KernelReasonCode.BELIEF_CREATED,
        epistemic_status_after=_status_for(challenger_authority, cfg),
        value_changes=True,
        case_attention=AttentionLevel.INFO,
    )


def disputed_confidence(
    confidence_before: Decimal,
    challenger_authority: Decimal,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> Decimal:
    """Section 3.1's decay, floored at 0.05.

    Applies to ``RETAIN_INCUMBENT_DISPUTED`` only. For
    ``RETAIN_INCUMBENT_AUTO`` the confidence is unchanged: the incumbent won on
    the merits, so decaying it would be arbitrary.
    """
    decayed = confidence_before * (Decimal(1) - cfg.dispute_decay * challenger_authority)
    return max(_CONFIDENCE_FLOOR, decayed.quantize(_CONFIDENCE_EXPONENT))


def case_attention_for(advocate_class: AdvocateAttentionClass) -> AttentionLevel:
    """The deterministic advocate-class -> case-attention map."""
    return ADVOCATE_TO_CASE_ATTENTION[advocate_class]


def requires_human_decision(advocate_class: AdvocateAttentionClass) -> bool:
    """True only for ``HUMAN_DECISION``.

    It shares ``URGENT`` with ``ACTION_REQUIRED``, so the attention level alone
    loses the distinction; this is the second output that carries it into the
    action policy.
    """
    return advocate_class is AdvocateAttentionClass.HUMAN_DECISION
