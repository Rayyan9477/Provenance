"""Contradiction detection: the overlap test and the M1-M13 decision table.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.4 owns :func:`bounds` and
  :func:`material_overlap`.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.5 owns :func:`amounts_differ`.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.6 owns the matcher table and
  :func:`match`. Thirteen rows, closed.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.7 owns :func:`payment_key`.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.8 owns retraction filtering.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 9.5 owns severity.
- ``specs/10_DATABASE_DDL.md`` section 7.1 owns the row a finding becomes:
  ``ck_conflicts_side_order`` requires ``left_source_id <= right_source_id``, so
  side ordering is normalised here rather than left to argument order.

Detection never resolves
------------------------
``EXECUTION/70_TASK_PLAN.md`` T4.4: "Never resolve. Detection produces
candidates; disposition is T4.5. Merging them is how a monetary threshold ends
up applied to a non-monetary conflict." Nothing in this module reads gate
``H5``, and :class:`ConflictFinding` carries ``monetary_exposure`` as a number
rather than as a verdict.

Recorded discrepancy
--------------------
Section 9.3's reason-code catalogue names a code for M1-M5 and M7-M12 but
leaves ``M6`` (same payment identity, materially different amounts) and ``M9``
(counterparty says settled, ledger says otherwise) without one. This module
assigns them ``CONFLICT_VALUE_MUTUAL_EXCLUSION`` and
``CONFLICT_PAYMENT_DENIAL`` respectively, both existing members of the closed
catalogue, and the gap is reported rather than closed by inventing codes.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from provenance_domain.enums import (
    CommitmentStatus,
    ConflictSeverity,
    ConflictType,
    EpistemicStatus,
    KernelReasonCode,
    SubjectType,
)
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)
from services.control_plane.app.memory_kernel.families import (
    BalanceValue,
    CommitmentStatusValue,
    Family,
    OutstandingValue,
    PaymentValue,
    ServiceStatusValue,
    canonical_predicate,
    is_monetary,
)
from services.control_plane.app.memory_kernel.propositions import (
    Proposition,
    PropositionSourceKind,
    ValidityBasis,
)

__all__ = [
    "CONFLICT_SIDE_KINDS",
    "EPOCH",
    "MATCHER_RULES",
    "NEG_INF",
    "POS_INF",
    "ConflictFinding",
    "MatchContext",
    "amounts_differ",
    "bounds",
    "detect",
    "match",
    "material_overlap",
    "monetary_exposure",
    "payment_key",
    "severity_of",
]

#: Section 2.4's sentinels for an unbounded interval.
NEG_INF: Final[datetime] = datetime(1, 1, 1, tzinfo=UTC)
POS_INF: Final[datetime] = datetime(9999, 12, 31, tzinfo=UTC)

#: The epoch the ``PAYMENT`` window bucket is measured from (section 2.7).
EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)

#: The matcher rows of section 2.6, in order. ``M13`` is a post-pass.
MATCHER_RULES: Final[tuple[str, ...]] = tuple(f"M{n}" for n in range(1, 14))

#: ``specs/10_DATABASE_DDL.md`` section 7.1 ``ck_conflicts_source_kinds``.
#: Note that this is **not** ``SupportSourceKind``: a conflict side may be a
#: ``COMMITMENT`` and may never be a ``DERIVATION``. Reusing one vocabulary for
#: the other writes a row the database rejects at the last possible moment.
CONFLICT_SIDE_KINDS: Final[frozenset[str]] = frozenset(
    {"EVIDENCE", "CLAIM", "BELIEF_VERSION", "COMMITMENT"}
)

#: Commitment statuses ``M11`` treats as live.
_WITHDRAWABLE: Final[frozenset[CommitmentStatus]] = frozenset(
    {CommitmentStatus.ACTIVE, CommitmentStatus.PARTIAL}
)


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Everything the matcher may look at beyond the two propositions.

    Deliberately small and deliberately explicit. Anything reachable from here
    is something a unit test can construct by hand, which is the falsifiable
    form of "the kernel is testable without a database".
    """

    #: Propositions whose ultimate evidence is not ``ACTIVE``, or whose source
    #: belief version is ``RETRACTED`` (section 2.8). Excluded from matching and
    #: barred from grounding a new version.
    retracted_prop_ids: frozenset[uuid.UUID] = frozenset()
    #: ``commitments.status`` by id, for ``M11``.
    commitment_statuses: Mapping[uuid.UUID, CommitmentStatus] = field(default_factory=dict)
    #: ``[valid_from, valid_to)`` by commitment id, for ``M11``'s containment
    #: test.
    commitment_validity: Mapping[uuid.UUID, tuple[datetime | None, datetime | None]] = field(
        default_factory=dict
    )
    #: Subjects grounding a belief version referenced by an ``action_intents``
    #: row in ``APPROVED`` or ``EXECUTING`` (gate ``H6``, invariant 4).
    action_blocked_subject_ids: frozenset[uuid.UUID] = frozenset()
    #: Absolute amounts of monetary claims admitted in this commit against this
    #: case. Section 3.3's non-monetary ``monetary_exposure`` reads this.
    monetary_amounts_in_commit: tuple[Decimal, ...] = ()

    def is_retrieval_ineligible(self, p: Proposition) -> bool:
        """Section 2.8: ineligible sources are excluded from matching."""
        if p.prop_id in self.retracted_prop_ids:
            return True
        return p.epistemic_status is EpistemicStatus.RETRACTED


@dataclass(frozen=True, slots=True)
class ConflictFinding:
    """One detected contradiction. A candidate, not a decision.

    ``incumbent`` and ``challenger`` keep the epistemic roles the disposition
    rules need; ``left_*`` and ``right_*`` are the same two sides reordered for
    ``ck_conflicts_side_order``. Conflating them is how a UUID ordering
    constraint quietly swaps which side the authority margin was measured on.
    """

    conflict_type: ConflictType
    family: Family
    incumbent: Proposition
    challenger: Proposition
    predicate: str
    subject_type: SubjectType
    subject_id: uuid.UUID
    matcher_rule: str
    overlap: timedelta | None = None
    monetary_exposure: Decimal = Decimal("0.0000")
    severity: ConflictSeverity = ConflictSeverity.LOW
    blocks_approved_action: bool = False
    post_dispute_confidence_below_floor: bool = False
    reason_codes: tuple[KernelReasonCode, ...] = ()

    @property
    def _ordered(self) -> tuple[Proposition, Proposition]:
        if self.incumbent.prop_id <= self.challenger.prop_id:
            return self.incumbent, self.challenger
        return self.challenger, self.incumbent

    @property
    def left_source_id(self) -> uuid.UUID:
        """The lexicographically smaller side id (``ck_conflicts_side_order``)."""
        return self._ordered[0].prop_id

    @property
    def right_source_id(self) -> uuid.UUID:
        """The lexicographically larger side id."""
        return self._ordered[1].prop_id

    @property
    def left_source_kind(self) -> str:
        """The side kind matching :attr:`left_source_id`."""
        return _side_kind(self._ordered[0])

    @property
    def right_source_kind(self) -> str:
        """The side kind matching :attr:`right_source_id`."""
        return _side_kind(self._ordered[1])


def _side_kind(p: Proposition) -> str:
    """A ``conflicts.left_source_kind`` value for *p*.

    ``DERIVATION`` has no cell in ``ck_conflicts_source_kinds``, so a derived
    proposition is recorded as the ``BELIEF_VERSION`` it grounds rather than as
    a kind the constraint refuses.
    """
    if p.source_kind is PropositionSourceKind.CLAIM:
        return "CLAIM"
    return "BELIEF_VERSION"


# --- section 2.4, bounds and the overlap test --------------------------------


def bounds(p: Proposition, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG) -> tuple[datetime, datetime]:
    """The half-open interval to compare *p* on, with instants widened."""
    lo = p.valid_from or NEG_INF
    hi = p.valid_to or POS_INF
    if hi <= lo:  # an instant, or an inverted pair
        if p.family is Family.PAYMENT:
            window = timedelta(days=cfg.payment_match_window_days)
            return lo - window, lo + window
        return lo, lo + timedelta(days=cfg.instant_widen_days)
    return lo, hi


def material_overlap(
    a: Proposition, b: Proposition, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG
) -> timedelta | None:
    """``None`` means not comparable. Otherwise the overlap duration.

    Two service periods that touch for four hours across a timezone-inference
    error are a parsing artifact, not a contradiction, so a brushing overlap
    under ``material_overlap_min_seconds`` is dismissed. Full containment always
    counts regardless of duration, so a one-hour period wholly inside a
    one-year period is never dismissed.
    """
    if ValidityBasis.UNKNOWN in (a.validity_basis, b.validity_basis):
        return None
    a_lo, a_hi = bounds(a, cfg)
    b_lo, b_hi = bounds(b, cfg)
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    if hi <= lo:
        return None
    duration = hi - lo
    contained = (a_lo >= b_lo and a_hi <= b_hi) or (b_lo >= a_lo and b_hi <= a_hi)
    if contained or duration.total_seconds() >= cfg.material_overlap_min_seconds:
        return duration
    return None


def _intervals_touch(a: Proposition, b: Proposition, cfg: KernelConfig) -> bool:
    """Any overlap at all, materiality ignored. ``M2``'s weaker test."""
    if ValidityBasis.UNKNOWN in (a.validity_basis, b.validity_basis):
        return False
    a_lo, a_hi = bounds(a, cfg)
    b_lo, b_hi = bounds(b, cfg)
    return min(a_hi, b_hi) > max(a_lo, b_lo)


# --- section 2.5, amount comparison ------------------------------------------


def amounts_differ(x: Decimal, y: Decimal, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG) -> bool:
    """Section 2.5. Tolerance is ``max(absolute, relative x larger operand)``.

    Tolerance exists because rounding and fee lines produce sub-percent noise
    that is not a dispute. ``1800.00`` against ``1791.00`` is exactly on the
    boundary and is deliberately *not* a difference.
    """
    delta = abs(x - y)
    tolerance = max(cfg.amount_abs_tolerance, cfg.amount_rel_tolerance * max(abs(x), abs(y)))
    return delta > tolerance


# --- section 2.7, payment identity -------------------------------------------


def payment_key(p: Proposition, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG) -> tuple[object, ...]:
    """Section 2.7's payment identity key. Prefers ``external_ref``.

    The amount-and-window fallback is deliberately coarse: it will occasionally
    merge two genuinely distinct same-amount payments three days apart. That
    produces a ``FULFILLMENT_CONFLICT`` routed to a human, which is the safe
    failure direction - a false conflict costs one click, a missed conflict
    costs money.
    """
    value = p.value
    if not isinstance(value, PaymentValue):
        return (p.subject_id, "NONE")
    if value.external_ref:
        return (p.subject_id, "REF", value.external_ref.strip().upper())
    bucket = (value.paid_at - EPOCH) // timedelta(days=cfg.payment_match_window_days)
    return (p.subject_id, "AMT", value.currency, value.amount, bucket)


# --- the individual matcher rows ----------------------------------------------


def _service_status(
    left: Proposition, right: Proposition, _ctx: MatchContext, cfg: KernelConfig
) -> tuple[str, ConflictType, tuple[KernelReasonCode, ...], timedelta | None] | None:
    lv, rv = left.value, right.value
    if not isinstance(lv, ServiceStatusValue) or not isinstance(rv, ServiceStatusValue):
        return None
    if lv.state is not rv.state:  # M1
        overlap = material_overlap(left, right, cfg)
        if overlap is None:
            return None
        return (
            "M1",
            ConflictType.VALUE_CONFLICT,
            (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
            overlap,
        )
    # M2 - same state, but the two sources disagree about when it started.
    if left.valid_from is None or right.valid_from is None:
        return None
    if abs(left.valid_from - right.valid_from) <= timedelta(days=1):
        return None
    if not _intervals_touch(left, right, cfg):
        return None
    return (
        "M2",
        ConflictType.TEMPORAL_CONFLICT,
        (KernelReasonCode.CONFLICT_TEMPORAL_OVERLAP,),
        material_overlap(left, right, cfg),
    )


def _balance(
    left: Proposition, right: Proposition, _ctx: MatchContext, cfg: KernelConfig
) -> tuple[str, ConflictType, tuple[KernelReasonCode, ...], timedelta | None] | None:
    lv, rv = left.value, right.value
    if not isinstance(lv, BalanceValue) or not isinstance(rv, BalanceValue):
        return None
    overlap = material_overlap(left, right, cfg)
    if overlap is None:
        return None
    if lv.currency != rv.currency:  # M4
        return (
            "M4",
            ConflictType.VALUE_CONFLICT,
            (
                KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,
                KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,
            ),
            overlap,
        )
    if amounts_differ(lv.amount, rv.amount, cfg):  # M3
        return (
            "M3",
            ConflictType.VALUE_CONFLICT,
            (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
            overlap,
        )
    return None


def _payment(
    left: Proposition, right: Proposition, _ctx: MatchContext, cfg: KernelConfig
) -> tuple[str, ConflictType, tuple[KernelReasonCode, ...], timedelta | None] | None:
    lv, rv = left.value, right.value
    if not isinstance(lv, PaymentValue) or not isinstance(rv, PaymentValue):
        return None
    # M7 first: a currency clash on a shared reference is decided by the
    # reference alone and does not need the window.
    if lv.external_ref and rv.external_ref:
        same_ref = lv.external_ref.strip().upper() == rv.external_ref.strip().upper()
        if same_ref and lv.currency != rv.currency:
            return (
                "M7",
                ConflictType.FULFILLMENT_CONFLICT,
                (KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,),
                None,
            )
    if payment_key(left, cfg) != payment_key(right, cfg):
        return None
    if lv.asserted != rv.asserted:  # M5
        return (
            "M5",
            ConflictType.FULFILLMENT_CONFLICT,
            (KernelReasonCode.CONFLICT_PAYMENT_DENIAL,),
            None,
        )
    if lv.currency == rv.currency and amounts_differ(lv.amount, rv.amount, cfg):  # M6
        return (
            "M6",
            ConflictType.FULFILLMENT_CONFLICT,
            (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
            None,
        )
    return None


def _outstanding(
    left: Proposition, right: Proposition, _ctx: MatchContext, cfg: KernelConfig
) -> tuple[str, ConflictType, tuple[KernelReasonCode, ...], timedelta | None] | None:
    lv, rv = left.value, right.value
    if not isinstance(lv, OutstandingValue) or not isinstance(rv, OutstandingValue):
        return None
    if lv.commitment_id != rv.commitment_id:
        return None
    overlap = material_overlap(left, right, cfg)
    if overlap is None:
        return None
    if lv.currency != rv.currency:  # M10
        return (
            "M10",
            ConflictType.VALUE_CONFLICT,
            (
                KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,
                KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,
            ),
            overlap,
        )
    if rv.amount == 0 and lv.amount > 0:  # M9 - "settled" against a live ledger
        return (
            "M9",
            ConflictType.FULFILLMENT_CONFLICT,
            (KernelReasonCode.CONFLICT_PAYMENT_DENIAL,),
            overlap,
        )
    if amounts_differ(lv.amount, rv.amount, cfg):  # M8
        return (
            "M8",
            ConflictType.VALUE_CONFLICT,
            (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
            overlap,
        )
    return None


def _commitment_status(
    left: Proposition, right: Proposition, ctx: MatchContext, cfg: KernelConfig
) -> tuple[str, ConflictType, tuple[KernelReasonCode, ...], timedelta | None] | None:
    lv, rv = left.value, right.value
    if not isinstance(lv, CommitmentStatusValue) or not isinstance(rv, CommitmentStatusValue):
        return None
    if lv.commitment_id != rv.commitment_id:
        return None
    if rv.withdrawn and _commitment_is_live(rv.commitment_id, right, ctx):  # M11
        return (
            "M11",
            ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT,
            (KernelReasonCode.CONFLICT_COMMITMENT_WITHDRAWAL,),
            None,
        )
    if lv.withdrawn != rv.withdrawn:  # M12
        overlap = material_overlap(left, right, cfg)
        if overlap is None:
            return None
        return (
            "M12",
            ConflictType.VALUE_CONFLICT,
            (KernelReasonCode.CONFLICT_VALUE_MUTUAL_EXCLUSION,),
            overlap,
        )
    return None


def _commitment_is_live(
    commitment_id: uuid.UUID, challenger: Proposition, ctx: MatchContext
) -> bool:
    """``M11``'s two conditions: a live commitment whose validity covers the
    withdrawal instant."""
    if ctx.commitment_statuses.get(commitment_id) not in _WITHDRAWABLE:
        return False
    window = ctx.commitment_validity.get(commitment_id)
    if window is None:
        return False
    lo, hi = window
    moment = challenger.valid_from or challenger.recorded_at
    if lo is not None and moment < lo:
        return False
    return not (hi is not None and moment >= hi)


# All five rules take the same four parameters so the table can be a plain
# dict rather than a chain of special cases. Four of them do not read the
# context - only `M11` needs a commitment status - and the parameter is named
# `_ctx` there so that "unused" is a statement rather than an oversight.
_FAMILY_RULES: Final[dict[Family, object]] = {
    Family.SERVICE_STATUS: _service_status,
    Family.BALANCE: _balance,
    Family.PAYMENT: _payment,
    Family.OUTSTANDING: _outstanding,
    Family.COMMITMENT_STATUS: _commitment_status,
}


# --- section 3.3's exposure, section 9.5's severity ---------------------------


def _amount_of(p: Proposition) -> Decimal | None:
    """The amount *p* asserts, which for a denial is zero.

    ``payment_not_received`` normalises to ``PaymentValue(asserted=False)``
    carrying the amount that was **not** paid. Read literally, a denial of
    USD 200.00 against an admitted USD 200.00 has a monetary exposure of
    ``abs(200 - 200) = 0``, which puts every payment denial below gate ``H5``
    however large it is - and ``22_EVAL_DATASETS.md`` CX-04/CX-05 exist
    precisely to straddle that threshold at 40.00 and 200.00.

    The disagreement is about the whole payment: one side says 200.00 arrived,
    the other says nothing did. So a denial asserts ``0`` and section 3.3's
    definition - ``abs(incumbent.amount - challenger.amount)`` - yields the
    denied amount without a special case anywhere else.
    """
    value = p.value
    if isinstance(value, PaymentValue) and not value.asserted:
        return Decimal("0.0000")
    if isinstance(value, BalanceValue | PaymentValue | OutstandingValue):
        return value.amount
    return None


def monetary_exposure(
    family: Family,
    incumbent: Proposition | None,
    challenger: Proposition,
    ctx: MatchContext,
) -> Decimal:
    """Section 3.3's precise definition of exposure.

    Monetary families: ``abs(incumbent.amount - challenger.amount)``, or the
    challenger's amount when there is no incumbent amount. Non-monetary
    families: the largest absolute amount among monetary claims admitted in the
    same commit against the same case, else zero.
    """
    if not is_monetary(family):
        if not ctx.monetary_amounts_in_commit:
            return Decimal("0.0000")
        return max(abs(a) for a in ctx.monetary_amounts_in_commit)
    challenger_amount = _amount_of(challenger)
    if challenger_amount is None:
        return Decimal("0.0000")
    incumbent_amount = None if incumbent is None else _amount_of(incumbent)
    if incumbent_amount is None:
        return abs(challenger_amount)
    return abs(incumbent_amount - challenger_amount)


def severity_of(
    finding: ConflictFinding, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG
) -> ConflictSeverity:
    """Section 9.5's severity ladder. First match wins.

    Severity gates the reopen test's Q3a and the UI sort order. It does **not**
    gate auto-resolution; keeping the two independent is what stops a severity
    heuristic from quietly becoming the resolution policy.
    """
    if finding.monetary_exposure >= cfg.critical_amount_threshold or finding.blocks_approved_action:
        return ConflictSeverity.CRITICAL
    high_type = finding.conflict_type in (
        ConflictType.AUTHORITY_CONFLICT,
        ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT,
    )
    if (
        finding.monetary_exposure >= cfg.human_review_amount_threshold
        or finding.incumbent.epistemic_status is EpistemicStatus.CONFIRMED
        or high_type
    ):
        return ConflictSeverity.HIGH
    if max(finding.incumbent.authority, finding.challenger.authority) >= Decimal("0.60"):
        return ConflictSeverity.MEDIUM
    return ConflictSeverity.LOW


# --- section 2.6, the matcher -------------------------------------------------


def _apply_authority_upgrade(finding: ConflictFinding, cfg: KernelConfig) -> ConflictFinding:
    """``M13``: two credible sources, no deterministic winner.

    The only producer of ``AUTHORITY_CONFLICT``, and therefore the only route
    to gate ``H1``. Two sources both at or above the high-authority floor and
    within ``auto_resolve_margin`` of each other cannot auto-resolve, by
    construction rather than by a reviewer noticing.
    """
    left_authority = finding.incumbent.authority
    right_authority = finding.challenger.authority
    both_credible = min(left_authority, right_authority) >= cfg.high_authority_floor
    close = abs(left_authority - right_authority) < cfg.auto_resolve_margin
    if not (both_credible and close):
        return finding
    return ConflictFinding(
        conflict_type=ConflictType.AUTHORITY_CONFLICT,
        family=finding.family,
        incumbent=finding.incumbent,
        challenger=finding.challenger,
        predicate=finding.predicate,
        subject_type=finding.subject_type,
        subject_id=finding.subject_id,
        matcher_rule="M13",
        overlap=finding.overlap,
        monetary_exposure=finding.monetary_exposure,
        severity=finding.severity,
        blocks_approved_action=finding.blocks_approved_action,
        post_dispute_confidence_below_floor=finding.post_dispute_confidence_below_floor,
        reason_codes=(*finding.reason_codes, KernelReasonCode.CONFLICT_AUTHORITY_TIE),
    )


def match(
    left: Proposition,
    right: Proposition,
    ctx: MatchContext,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> ConflictFinding | None:
    """Section 2.6. *left* is the incumbent, *right* the challenger."""
    if left.prop_id == right.prop_id and left.family is right.family:
        # Section 2.10 item 10: supersession is lineage, not contradiction.
        return None
    if left.family is not right.family or left.family is Family.UNMAPPED:
        return None
    if (left.subject_type, left.subject_id) != (right.subject_type, right.subject_id):
        return None
    if ctx.is_retrieval_ineligible(left) or ctx.is_retrieval_ineligible(right):
        return None

    rule = _FAMILY_RULES.get(left.family)
    if rule is None:
        return None
    outcome = rule(left, right, ctx, cfg)  # type: ignore[operator]
    if outcome is None:
        return None
    matcher_rule, conflict_type, reason_codes, overlap = outcome

    blocked = left.subject_id in ctx.action_blocked_subject_ids
    finding = ConflictFinding(
        conflict_type=conflict_type,
        family=left.family,
        incumbent=left,
        challenger=right,
        predicate=canonical_predicate(left.family),
        subject_type=left.subject_type,
        subject_id=left.subject_id,
        matcher_rule=matcher_rule,
        overlap=overlap,
        monetary_exposure=monetary_exposure(left.family, left, right, ctx),
        blocks_approved_action=blocked,
        reason_codes=reason_codes,
    )
    finding = _apply_authority_upgrade(finding, cfg)
    return _with_severity(finding, cfg)


def _with_severity(finding: ConflictFinding, cfg: KernelConfig) -> ConflictFinding:
    """Severity is derived last, because ``M13`` can change the type it reads."""
    return ConflictFinding(
        conflict_type=finding.conflict_type,
        family=finding.family,
        incumbent=finding.incumbent,
        challenger=finding.challenger,
        predicate=finding.predicate,
        subject_type=finding.subject_type,
        subject_id=finding.subject_id,
        matcher_rule=finding.matcher_rule,
        overlap=finding.overlap,
        monetary_exposure=finding.monetary_exposure,
        severity=severity_of(finding, cfg),
        blocks_approved_action=finding.blocks_approved_action,
        post_dispute_confidence_below_floor=finding.post_dispute_confidence_below_floor,
        reason_codes=finding.reason_codes,
    )


def detect(
    incumbents: Sequence[Proposition],
    challengers: Sequence[Proposition],
    ctx: MatchContext,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> tuple[ConflictFinding, ...]:
    """The full cross-product of section 2.6, deduplicated by side pair.

    Snapshots are bounded to one case, so the cross-product is small and no
    index or heuristic pruning is needed. Adding one would create a correctness
    surface with no measured benefit (risk R9).
    """
    findings: list[ConflictFinding] = []
    seen: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for incumbent in incumbents:
        for challenger in challengers:
            finding = match(incumbent, challenger, ctx, cfg)
            if finding is None:
                continue
            key = (finding.left_source_id, finding.right_source_id, finding.predicate)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return tuple(findings)
