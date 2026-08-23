"""Predicate-aware source authority, and the ordering that decides an incumbent.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 5.2 owns this module's shape. There is no
  single global trustworthiness score. The model recommends a ``SourceClass``;
  this table maps ``(predicate family, class)`` to a band; the Kernel picks the
  value. Contracts never carry an authority score produced by a model.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.2 is the semantic owner of the
  grid and prints it as a table. The two must agree value for value.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.2 step 6 and the
  ``Proposition.authority`` property: an entailed proposition pays a fixed
  penalty, floored at zero.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.3: the exact deterministic
  condition. ``winner = inc if delta >= 0 else chal``; auto-resolution requires
  ``abs(delta) >= auto_resolve_margin`` **and** ``winner.authority >=
  auto_resolve_floor``.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.5: why a single global trust
  score is forbidden, in four independent ways.

Stdlib only. No ``pydantic``, no ``provenance_db``, no ``boto3``, no
``httpx``, no ``asyncio``.

Scope
-----
:func:`rank` implements the **authority half** of section 3.3 - the ordering
that decides which of two conflicting sources stays incumbent. It deliberately
does not implement the mandatory human-review gates ``H1``-``H6``, which
short-circuit *before* the authority test and need a conflict type, a claim
kind, a monetary exposure and an action-intent lookup that this package has no
business knowing about. The hero conflict is resolved by ``H5``, not by the
margin (``CANONICAL_DECISIONS.md``, *Hero conflict*). Reading a
:class:`AuthorityRanking` as a disposition would therefore be wrong; it is one
input to one.

Authority is not the model's confidence
---------------------------------------
A confidence says how sure an extractor is that it read a document correctly.
An authority says how much that *kind of document* is worth on that *kind of
question*. A bank statement read at 0.55 confidence is still near-decisive
about whether a charge cleared; a model's own 0.99 inference is worth 0.05
about anything. There is no function here from one to the other, and
:func:`authority_from_confidence` exists solely so that the mistake has a named
place to fail loudly rather than quietly becoming a keyword argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final, NoReturn

from provenance_domain.enums import KernelReasonCode, SourceClass

__all__ = [
    "AUTHORITY_EXPONENT",
    "AUTHORITY_SCORES",
    "CANONICAL_PREDICATES",
    "AUTO_RESOLVE_FLOOR",
    "AUTO_RESOLVE_MARGIN",
    "AuthorityRanking",
    "ENTAILMENT_PENALTY",
    "FAMILIES",
    "FamilyMismatchError",
    "MODEL_INFERENCE_CEILING",
    "ModelConfidenceIsNotAuthorityError",
    "PREDICATE_FAMILIES",
    "SourceAuthority",
    "UNKNOWN_AUTHORITY",
    "UNMAPPED_FAMILY",
    "authority_for",
    "authority_from_confidence",
    "authority_ranking",
    "predicate_family",
    "rank",
]

#: ``claims.authority_score`` is ``DECIMAL(5,4)``; the shape is part of the
#: value, so every score this module returns is quantised to it.
AUTHORITY_EXPONENT: Final[Decimal] = Decimal("0.0001")

#: The five predicate families of v1. Anything outside them produces zero
#: conflicts by design (``specs/12_KERNEL_ALGORITHMS.md`` R1): honest silence
#: beats invented contradictions.
FAMILIES: Final[tuple[str, ...]] = (
    "SERVICE_STATUS",
    "BALANCE",
    "PAYMENT",
    "OUTSTANDING",
    "COMMITMENT_STATUS",
)

#: The family of a predicate the registry does not know.
UNMAPPED_FAMILY: Final[str] = "UNMAPPED"

#: ``KernelConfig.unknown_source_class_authority``. Fail low, never high.
UNKNOWN_AUTHORITY: Final[Decimal] = Decimal("0.10").quantize(AUTHORITY_EXPONENT)

#: ``KernelConfig.entailment_penalty`` - section 2.3.
ENTAILMENT_PENALTY: Final[Decimal] = Decimal("0.30")
#: ``KernelConfig.auto_resolve_margin`` - section 3.3.
AUTO_RESOLVE_MARGIN: Final[Decimal] = Decimal("0.25")
#: ``KernelConfig.auto_resolve_floor`` - section 3.3.
AUTO_RESOLVE_FLOOR: Final[Decimal] = Decimal("0.80")

#: ``10_DATABASE_DDL.md`` ``ck_claims_inference_authority``: an ``INFERENCE``
#: claim may never carry an authority above this. The grid must not be able to
#: breach the constraint the database will enforce anyway.
MODEL_INFERENCE_CEILING: Final[Decimal] = Decimal("0.2000")

#: Surface predicate -> family. The registry is CLOSED: a predicate absent from
#: it is `UNMAPPED`, produces no `conflicts` row, and scores the unmapped floor.
#:
#: RECONCILED 2026-08-18, and the reason matters more than the list.
#:
#: Two specifications disagreed. `11_CONTRACTS.md` §5.2 shipped eight names --
#: which this module implemented faithfully -- while `12_KERNEL_ALGORITHMS.md`
#: §2.1 defines SIXTEEN across the same five families. Neither set contained the
#: other, and the gap was not cosmetic:
#:
#:     authority_for("amount_due",    PROVIDER_SYSTEM_NOTICE) -> 0.1000
#:     authority_for("invoice_total", PROVIDER_SYSTEM_NOTICE) -> 0.1000
#:
#: `amount_due` and `invoice_total` are named BY §2.1 as accepted `BALANCE`
#: surface predicates. Every provider invoice using either spelling was silently
#: demoted to the unmapped floor -- below any auto-resolve threshold -- so the
#: disposition changed and nothing errored. Correct-looking output, no warning.
#:
#: §2.1 wins on the surface-predicate set. §5.2's own adjacent comment concedes
#: it ("§12.3.2 is the semantic owner"): `11_CONTRACTS.md` owns this module's
#: SHAPE, `12_KERNEL_ALGORITHMS.md` owns its SEMANTICS.
#:
#: The three §5.2-only names are KEPT as aliases rather than deleted. A mapping
#: that resolves correctly today can only lose information by being removed, and
#: Rule N1 means an alias never reaches `beliefs.predicate` anyway -- the
#: family's canonical predicate is what gets stored. `billing_period_covered` is
#: the weakest of the three: it has no §2.1 home and arguably encodes a billing
#: period rather than a service state. It is retained pending a ruling, and
#: flagged here so the ruling is not made by accident.
PREDICATE_FAMILIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        # --- SERVICE_STATUS, canonical `service_active` -- §2.1 row 1
        "service_active": "SERVICE_STATUS",
        "service_terminated": "SERVICE_STATUS",
        "service_cancelled": "SERVICE_STATUS",
        "service_suspended": "SERVICE_STATUS",
        # --- BALANCE, canonical `balance_owed` -- §2.1 row 2
        "balance_owed": "BALANCE",
        "amount_due": "BALANCE",
        "invoice_total": "BALANCE",
        # --- PAYMENT, canonical `payment_received` -- §2.1 row 3
        "payment_received": "PAYMENT",
        "payment_sent": "PAYMENT",
        "payment_not_received": "PAYMENT",
        # --- OUTSTANDING, canonical `deposit_outstanding` -- §2.1 row 4
        "deposit_outstanding": "OUTSTANDING",
        "refund_outstanding": "OUTSTANDING",
        "reimbursement_outstanding": "OUTSTANDING",
        # --- COMMITMENT_STATUS, canonical `commitment_withdrawn` -- §2.1 row 5
        "commitment_withdrawn": "COMMITMENT_STATUS",
        "commitment_revoked": "COMMITMENT_STATUS",
        "promise_retracted": "COMMITMENT_STATUS",
        # --- retained from 11_CONTRACTS.md §5.2, absent from §2.1's table
        "amount_outstanding": "OUTSTANDING",
        "commitment_status": "COMMITMENT_STATUS",
        "billing_period_covered": "SERVICE_STATUS",
    }
)

#: Family -> the canonical predicate stored in `beliefs.predicate`.
#:
#: §2.1 Rule N1: one belief per (subject, family). `service_terminated` and
#: `service_active` are two SURFACE forms of one belief, and the canonical form
#: is what persists. Without this the
#: `UNIQUE (tenant_id, user_id, subject_type, subject_id, predicate)` constraint
#: would happily hold two mutually exclusive beliefs as separate rows, and the
#: contradiction model would silently no-op instead of detecting anything.
CANONICAL_PREDICATES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "SERVICE_STATUS": "service_active",
        "BALANCE": "balance_owed",
        "PAYMENT": "payment_received",
        "OUTSTANDING": "deposit_outstanding",
        "COMMITMENT_STATUS": "commitment_withdrawn",
    }
)


def _row(*values: str) -> Mapping[str, Decimal]:
    """One grid row, in the column order of :data:`FAMILIES`."""
    return MappingProxyType(
        dict(
            zip(
                FAMILIES,
                (Decimal(value).quantize(AUTHORITY_EXPONENT) for value in values),
                strict=True,
            )
        )
    )


# Exact scores, not fitted probabilities. `specs/12_KERNEL_ALGORITHMS.md`
# section 3.2 is the semantic owner; the documentation lint compares this
# complete matrix with that table.
#
# Read a row horizontally to see why it must be a grid rather than a column.
# A bank knows a charge cleared (PAYMENT 0.97) and knows nothing about whether
# an ISP honoured a cancellation (SERVICE_STATUS 0.10). One number cannot hold
# both, and averaging them produces a source that is mediocre at everything and
# correct at nothing.
AUTHORITY_SCORES: Final[Mapping[SourceClass, Mapping[str, Decimal]]] = MappingProxyType(
    {
        SourceClass.BANK_OR_CARD_STATEMENT: _row("0.10", "0.55", "0.97", "0.60", "0.10"),
        SourceClass.PAYMENT_PROCESSOR_RECORD: _row("0.10", "0.60", "0.96", "0.60", "0.10"),
        SourceClass.SIGNED_AGREEMENT: _row("0.92", "0.85", "0.30", "0.90", "0.95"),
        SourceClass.PROVIDER_SYSTEM_NOTICE: _row("0.88", "0.90", "0.70", "0.72", "0.55"),
        SourceClass.PROVIDER_AGENT_WRITTEN: _row("0.85", "0.72", "0.55", "0.70", "0.88"),
        SourceClass.PROVIDER_AGENT_CHAT: _row("0.68", "0.55", "0.45", "0.55", "0.70"),
        SourceClass.OFFICIAL_POLICY_DOC: _row("0.60", "0.50", "0.20", "0.45", "0.62"),
        SourceClass.MARKETING_PAGE: _row("0.35", "0.25", "0.05", "0.20", "0.30"),
        SourceClass.USER_UPLOADED_RECEIPT: _row("0.30", "0.45", "0.80", "0.50", "0.25"),
        SourceClass.USER_STATEMENT: _row("0.45", "0.40", "0.50", "0.48", "0.40"),
        SourceClass.USER_CORRECTION: _row("0.75", "0.70", "0.70", "0.72", "0.70"),
        SourceClass.MODEL_INFERENCE: _row("0.05", "0.05", "0.05", "0.05", "0.05"),
    }
)


class ModelConfidenceIsNotAuthorityError(TypeError):
    """Someone tried to turn an extraction confidence into an authority."""


class FamilyMismatchError(ValueError):
    """Two propositions from different predicate families were compared.

    The order of source classes inverts between families - a bank statement
    outranks a signed agreement on ``PAYMENT`` and is outranked by it on
    ``SERVICE_STATUS``. Comparing across families is therefore not a close
    call, it is a category error, and answering it would produce a confidently
    wrong disposition.
    """


def predicate_family(predicate: str) -> str:
    """The family of *predicate*, or :data:`UNMAPPED_FAMILY`."""
    return PREDICATE_FAMILIES.get(predicate, UNMAPPED_FAMILY)


def authority_for(predicate: str, source_class: SourceClass) -> Decimal:
    """The frozen ``(predicate family, source class)`` score.

    An unmapped predicate or an unknown class falls to
    :data:`UNKNOWN_AUTHORITY`. Failing low is deliberate: an unrecognised
    source must not be able to displace canonical state.
    """
    family = predicate_family(predicate)
    row = AUTHORITY_SCORES.get(source_class)
    if row is None:
        return UNKNOWN_AUTHORITY
    return row.get(family, UNKNOWN_AUTHORITY).quantize(AUTHORITY_EXPONENT)


def authority_from_confidence(confidence: Decimal) -> NoReturn:
    """Always raises. Authority is not the model's confidence.

    Kept as a named refusal rather than omitted, so that the mistake has
    somewhere to land loudly. A confidence measures how sure an extractor is
    that it read a document; an authority measures what that kind of document
    is worth on that kind of question. They are different quantities and no
    monotone map exists between them.
    """
    raise ModelConfidenceIsNotAuthorityError(
        f"refusing to derive an authority from a model confidence of {confidence}; "
        "the model may recommend a SourceClass and nothing more "
        "(11_CONTRACTS.md section 5.2). Call authority_for(predicate, source_class)."
    )


@dataclass(frozen=True, slots=True)
class SourceAuthority:
    """One side of a conflict, reduced to what the ordering may look at.

    Note what is absent: no confidence, no recency, no volume, no per-source
    reputation. Section 3.5's fourth argument is that a score updated from
    outcomes creates a recency ratchet in which the institutions that send you
    the most mail become the most authoritative about your own life. Nothing
    here can accumulate.
    """

    predicate: str
    source_class: SourceClass
    entailed: bool = False

    @property
    def family(self) -> str:
        return predicate_family(self.predicate)

    @property
    def base_authority(self) -> Decimal:
        return authority_for(self.predicate, self.source_class)

    @property
    def authority(self) -> Decimal:
        """The grid score, less the entailment penalty, floored at zero.

        An invoice for June does not literally say service was active in June;
        section 2.3's rules ``EN-1`` and ``EN-2`` entail it, and the penalty is
        what keeps an inference from carrying the weight of a statement.
        """
        if not self.entailed:
            return self.base_authority
        penalised = self.base_authority - ENTAILMENT_PENALTY
        return max(Decimal("0"), penalised).quantize(AUTHORITY_EXPONENT)


@dataclass(frozen=True, slots=True)
class AuthorityRanking:
    """The authority comparison of section 3.3, with its working shown.

    Every field is reported rather than collapsed into a verdict, because
    State Proof has to answer "why did you keep the old belief?" in numbers a
    user can argue with. "The ISP scores 0.71" is not an answer.
    """

    incumbent: SourceAuthority
    challenger: SourceAuthority
    family: str
    delta: Decimal
    winner: SourceAuthority
    winner_is_incumbent: bool
    margin_met: bool
    floor_met: bool
    auto_resolvable: bool
    reason_code: KernelReasonCode


def rank(incumbent: SourceAuthority, challenger: SourceAuthority) -> AuthorityRanking:
    """Decide which of two conflicting sources stays incumbent.

    Ties go to the incumbent - ``winner = inc if delta >= 0 else chal`` - but a
    tie can never clear :data:`AUTO_RESOLVE_MARGIN`, so it is reported as
    ``HUMAN_REQUIRED_AUTHORITY_TIE`` and is never auto-resolved. Nothing
    dislodges canonical state by drawing level with it, and nothing resolves a
    genuine disagreement by coin flip.

    Two sources both at or above :data:`AUTO_RESOLVE_FLOOR` and within
    :data:`AUTO_RESOLVE_MARGIN` of each other therefore cannot auto-resolve, by
    construction. That is ``00_IMPLEMENTATION_MAP.md`` section 12's rule - "do
    not silently resolve two high-authority conflicting sources" - falling out
    of the arithmetic instead of being bolted on.

    Raises:
        FamilyMismatchError: the two sides are in different families, or in no
            family at all.
    """
    family = incumbent.family
    if family != challenger.family:
        raise FamilyMismatchError(
            f"refusing to compare authority across families: "
            f"{incumbent.predicate!r} is {family}, "
            f"{challenger.predicate!r} is {challenger.family}"
        )
    if family == UNMAPPED_FAMILY:
        raise FamilyMismatchError(
            f"{incumbent.predicate!r} and {challenger.predicate!r} are UNMAPPED; "
            "an unmapped predicate produces no contradiction and no ranking "
            "(12_KERNEL_ALGORITHMS.md section 2.2 step 1)"
        )

    delta = (incumbent.authority - challenger.authority).quantize(AUTHORITY_EXPONENT)
    winner_is_incumbent = delta >= 0
    winner = incumbent if winner_is_incumbent else challenger
    margin_met = abs(delta) >= AUTO_RESOLVE_MARGIN
    floor_met = winner.authority >= AUTO_RESOLVE_FLOOR
    auto_resolvable = margin_met and floor_met

    if not auto_resolvable:
        reason_code = KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE
    elif incumbent.entailed or challenger.entailed:
        reason_code = KernelReasonCode.AUTO_RESOLVED_ENTAILMENT_PENALTY
    else:
        reason_code = KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN

    return AuthorityRanking(
        incumbent=incumbent,
        challenger=challenger,
        family=family,
        delta=delta,
        winner=winner,
        winner_is_incumbent=winner_is_incumbent,
        margin_met=margin_met,
        floor_met=floor_met,
        auto_resolvable=auto_resolvable,
        reason_code=reason_code,
    )


#: ``SourceClass`` in declaration order, used only as a stable tiebreak.
_DECLARATION_ORDER: Final[tuple[SourceClass, ...]] = tuple(SourceClass)


def _ranking_key(entry: tuple[SourceClass, Decimal]) -> tuple[Decimal, int]:
    """Descending by score; ties broken by ``SourceClass`` declaration order.

    The tiebreak is presentational only. Two classes on the same score are
    genuinely equally authoritative for that family, and :func:`rank` treats
    the pair as a tie regardless of which one this ordering happens to print
    first.
    """
    source_class, score = entry
    return (-score, _DECLARATION_ORDER.index(source_class))


def authority_ranking(predicate: str) -> tuple[tuple[SourceClass, Decimal], ...]:
    """Every source class for *predicate*, most authoritative first.

    The ordering is **family-relative and nothing else**. There is no global
    version of this list, and building one by averaging these columns would
    reintroduce exactly the single trust score section 3.5 forbids.

    The order is a total preorder rather than a total order: ties are ordinary
    here (``BANK_OR_CARD_STATEMENT`` and ``PAYMENT_PROCESSOR_RECORD`` are both
    0.10 on ``SERVICE_STATUS``), and a tie between two conflicting sources is
    resolved by a human, not by this ordering.
    """
    scored = [
        (source_class, authority_for(predicate, source_class)) for source_class in SourceClass
    ]
    scored.sort(key=_ranking_key)
    return tuple(scored)
