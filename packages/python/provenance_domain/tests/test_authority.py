"""T1.3 — predicate-aware source authority.

Sources, transcribed by hand rather than imported:

* `docs/specs/12_KERNEL_ALGORITHMS.md` §2.1 — the closed predicate-family
  registry: the five families, each family's **canonical** predicate (the one
  `beliefs.predicate` stores, Rule N1), and the surface predicates accepted
  from proposals. §2.1 is the semantic owner of the surface-predicate set.
  `11_CONTRACTS.md` §5.2 owns this module's *shape* and ships an illustrative
  8-entry map; its own adjacent comment concedes the ownership question
  ("§12.3.2 is the semantic owner"), so where the two disagree §2.1 wins and
  §5.2's map is treated as incomplete rather than as the specification.
  Citing §5.2 here is what previously let eleven of §2.1's sixteen surface
  predicates sit unmapped with a green test.
* `docs/specs/11_CONTRACTS.md` §5.2 — the complete `(source_class, family)`
  score grid, the unknown floor, the `MappingProxyType` shape, and the rule
  that a model recommends a `SourceClass` and never emits an authority score;
  plus the three surface predicates §5.2 carries that §2.1's table omits.
* `docs/specs/12_KERNEL_ALGORITHMS.md` §3.2 — the same grid as a table, which
  §5.2 names as the semantic owner; §2.2 step 6 and the `Proposition.authority`
  property (entailment penalty, floored at zero); §3.3 — the exact
  deterministic condition that decides which of two conflicting sources stays
  incumbent; §3.5 — why a single global trust score is forbidden.
* `docs/specs/12_KERNEL_ALGORITHMS.md` §0 `KernelConfig` — `entailment_penalty
  = 0.30`, `auto_resolve_margin = 0.25`, `auto_resolve_floor = 0.80`,
  `unknown_source_class_authority = 0.10`.
* `docs/specs/12_KERNEL_ALGORITHMS.md` §1.6 step 12 — the hero disposition:
  `0.88 - 0.58 = 0.30 >= 0.25`, winner `0.88 >= 0.80`, incumbent retained.

The grid below is a second, independent transcription. Importing
`provenance_domain.authority`'s own table to check `authority_for` would prove
only that a dictionary lookup works.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from provenance_domain import authority
from provenance_domain.enums import KernelReasonCode, SourceClass

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# The specification, transcribed by hand.
# ---------------------------------------------------------------------------

#: 12_KERNEL_ALGORITHMS.md §2.1, the "Surface predicates accepted from
#: proposals" column, transcribed row by row. Sixteen names, five families.
_SECTION_2_1_SURFACE: dict[str, str] = {
    "service_active": "SERVICE_STATUS",
    "service_terminated": "SERVICE_STATUS",
    "service_cancelled": "SERVICE_STATUS",
    "service_suspended": "SERVICE_STATUS",
    "balance_owed": "BALANCE",
    "amount_due": "BALANCE",
    "invoice_total": "BALANCE",
    "payment_received": "PAYMENT",
    "payment_sent": "PAYMENT",
    "payment_not_received": "PAYMENT",
    "deposit_outstanding": "OUTSTANDING",
    "refund_outstanding": "OUTSTANDING",
    "reimbursement_outstanding": "OUTSTANDING",
    "commitment_withdrawn": "COMMITMENT_STATUS",
    "commitment_revoked": "COMMITMENT_STATUS",
    "promise_retracted": "COMMITMENT_STATUS",
}

#: The three names 11_CONTRACTS.md §5.2 carries that §2.1's table does not
#: list. Retained as aliases rather than deleted: a mapping that resolves
#: correctly today can only lose information by being removed, and Rule N1
#: means an alias never reaches `beliefs.predicate` — the family's canonical
#: predicate is stored either way.
_SECTION_5_2_ONLY: dict[str, str] = {
    "amount_outstanding": "OUTSTANDING",
    "commitment_status": "COMMITMENT_STATUS",
    "billing_period_covered": "SERVICE_STATUS",
}

#: The union. Nineteen names, still exactly five families.
_PREDICATE_FAMILIES: dict[str, str] = {**_SECTION_2_1_SURFACE, **_SECTION_5_2_ONLY}

#: 12_KERNEL_ALGORITHMS.md §2.1, the "Canonical predicate (stored in
#: `beliefs.predicate`)" column. Rule N1: one belief per (subject, family).
_CANONICAL_PREDICATES: dict[str, str] = {
    "SERVICE_STATUS": "service_active",
    "BALANCE": "balance_owed",
    "PAYMENT": "payment_received",
    "OUTSTANDING": "deposit_outstanding",
    "COMMITMENT_STATUS": "commitment_withdrawn",
}

#: 12_KERNEL_ALGORITHMS.md §3.2, column order preserved.
_FAMILIES: tuple[str, ...] = (
    "SERVICE_STATUS",
    "BALANCE",
    "PAYMENT",
    "OUTSTANDING",
    "COMMITMENT_STATUS",
)

_GRID: dict[str, tuple[str, str, str, str, str]] = {
    "BANK_OR_CARD_STATEMENT": ("0.10", "0.55", "0.97", "0.60", "0.10"),
    "PAYMENT_PROCESSOR_RECORD": ("0.10", "0.60", "0.96", "0.60", "0.10"),
    "SIGNED_AGREEMENT": ("0.92", "0.85", "0.30", "0.90", "0.95"),
    "PROVIDER_SYSTEM_NOTICE": ("0.88", "0.90", "0.70", "0.72", "0.55"),
    "PROVIDER_AGENT_WRITTEN": ("0.85", "0.72", "0.55", "0.70", "0.88"),
    "PROVIDER_AGENT_CHAT": ("0.68", "0.55", "0.45", "0.55", "0.70"),
    "OFFICIAL_POLICY_DOC": ("0.60", "0.50", "0.20", "0.45", "0.62"),
    "MARKETING_PAGE": ("0.35", "0.25", "0.05", "0.20", "0.30"),
    "USER_UPLOADED_RECEIPT": ("0.30", "0.45", "0.80", "0.50", "0.25"),
    "USER_STATEMENT": ("0.45", "0.40", "0.50", "0.48", "0.40"),
    "USER_CORRECTION": ("0.75", "0.70", "0.70", "0.72", "0.70"),
    "MODEL_INFERENCE": ("0.05", "0.05", "0.05", "0.05", "0.05"),
}

#: One predicate per family, so the grid can be checked through the public
#: API. §2.1's canonical predicates, not arbitrary surface forms.
_PREDICATE_OF: dict[str, str] = dict(_CANONICAL_PREDICATES)


def _expected(source_class: str, family: str) -> Decimal:
    return Decimal(_GRID[source_class][_FAMILIES.index(family)]).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Predicate families
# ---------------------------------------------------------------------------


def test_predicate_families_map_the_eight_spec_predicates() -> None:
    for predicate, family in _PREDICATE_FAMILIES.items():
        assert authority.predicate_family(predicate) == family
    assert dict(authority.PREDICATE_FAMILIES) == _PREDICATE_FAMILIES
    assert authority.FAMILIES == _FAMILIES


def test_an_unmapped_predicate_stops_rather_than_guessing() -> None:
    """12_KERNEL_ALGORITHMS.md §2.2 step 1 and R1: silence, not invention."""
    assert authority.UNMAPPED_FAMILY == "UNMAPPED"
    for unknown in ("warranty_window_open", "eligibility_denied", "", "SERVICE_STATUS"):
        assert authority.predicate_family(unknown) == "UNMAPPED"
    # An unmapped family has no row in the grid, so it scores the floor.
    assert authority.authority_for("warranty_window_open", SourceClass.SIGNED_AGREEMENT) == Decimal(
        "0.1000"
    )


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_the_grid_covers_every_source_class_and_every_family() -> None:
    assert set(_GRID) == {member.value for member in SourceClass}
    assert set(authority.AUTHORITY_SCORES) == set(SourceClass)
    for source_class in SourceClass:
        assert set(authority.AUTHORITY_SCORES[source_class]) == set(_FAMILIES)


def test_grid_values_match_the_specification_table_at_four_decimal_places() -> None:
    """`claims.authority_score` is `DECIMAL(5,4)`; the shape is part of the value."""
    for source_class in SourceClass:
        for family in _FAMILIES:
            actual = authority.authority_for(_PREDICATE_OF[family], source_class)
            assert actual == _expected(source_class.value, family), f"{source_class.value}/{family}"
            assert str(actual) == str(_expected(source_class.value, family))
    assert str(authority.authority_for("payment_received", SourceClass.BANK_OR_CARD_STATEMENT)) == (
        "0.9700"
    )
    assert str(authority.UNKNOWN_AUTHORITY) == "0.1000"


def test_every_score_is_a_decimal_and_the_table_is_immutable() -> None:
    for source_class in SourceClass:
        row = authority.AUTHORITY_SCORES[source_class]
        for family in _FAMILIES:
            score = row[family]
            assert isinstance(score, Decimal)
            assert not isinstance(score, float)
            assert Decimal("0") <= score <= Decimal("1")
        with pytest.raises(TypeError):
            row["PAYMENT"] = Decimal("1.0000")  # type: ignore[index]
    with pytest.raises(TypeError):
        authority.PREDICATE_FAMILIES["bribe"] = "PAYMENT"  # type: ignore[index]


def test_bank_statement_knows_payments_and_knows_nothing_about_service() -> None:
    """12_KERNEL_ALGORITHMS.md §3.5.1 — one number cannot hold both."""
    bank = SourceClass.BANK_OR_CARD_STATEMENT
    assert authority.authority_for("payment_received", bank) == Decimal("0.9700")
    assert authority.authority_for("service_terminated", bank) == Decimal("0.1000")
    signed = SourceClass.SIGNED_AGREEMENT
    assert authority.authority_for("payment_received", signed) == Decimal("0.3000")
    assert authority.authority_for("service_terminated", signed) == Decimal("0.9200")


def test_model_inference_is_never_authoritative_for_anything() -> None:
    """The machine-readable form of the kernel rule.

    `10_DATABASE_DDL.md` `ck_claims_inference_authority` caps an `INFERENCE`
    claim at `0.2000`; the grid must not be able to breach it.
    """
    for family in _FAMILIES:
        score = authority.authority_for(_PREDICATE_OF[family], SourceClass.MODEL_INFERENCE)
        assert score == Decimal("0.0500")
        assert score <= Decimal("0.2000")


# ---------------------------------------------------------------------------
# Authority is not the model's confidence
# ---------------------------------------------------------------------------


def test_authority_is_not_the_models_confidence() -> None:
    """11_CONTRACTS.md §5.2 and §11 — the model recommends a class, nothing more.

    There is no route from a confidence to an authority. That is asserted on
    the signature, because a keyword nobody passes today is a keyword somebody
    passes in Phase 7.
    """
    assert list(inspect.signature(authority.authority_for).parameters) == [
        "predicate",
        "source_class",
    ]
    assert issubclass(authority.ModelConfidenceIsNotAuthorityError, TypeError)
    with pytest.raises(authority.ModelConfidenceIsNotAuthorityError) as excinfo:
        authority.authority_from_confidence(Decimal("0.99"))
    assert "confidence" in str(excinfo.value)

    # A near-certain model and a hopeless model are equally unauthoritative.
    assert authority.authority_for(
        "balance_owed", SourceClass.MODEL_INFERENCE
    ) == authority.authority_for("balance_owed", SourceClass.MODEL_INFERENCE)
    # And a confident model is still outranked by a marketing page.
    assert authority.authority_for("balance_owed", SourceClass.MODEL_INFERENCE) < (
        authority.authority_for("balance_owed", SourceClass.MARKETING_PAGE)
    )


# ---------------------------------------------------------------------------
# Entailment
# ---------------------------------------------------------------------------


def test_the_entailment_penalty_applies_once_and_floors_at_zero() -> None:
    assert Decimal("0.30") == authority.ENTAILMENT_PENALTY
    direct = authority.SourceAuthority("service_terminated", SourceClass.PROVIDER_SYSTEM_NOTICE)
    entailed = authority.SourceAuthority(
        "service_terminated", SourceClass.PROVIDER_SYSTEM_NOTICE, entailed=True
    )
    assert direct.family == "SERVICE_STATUS"
    assert direct.base_authority == Decimal("0.8800")
    assert direct.authority == Decimal("0.8800")
    assert entailed.base_authority == Decimal("0.8800")
    assert entailed.authority == Decimal("0.5800")

    floored = authority.SourceAuthority(
        "service_terminated", SourceClass.MODEL_INFERENCE, entailed=True
    )
    assert floored.authority == Decimal("0.0000")
    assert floored.authority >= Decimal("0")


# ---------------------------------------------------------------------------
# The ordering that decides which source stays incumbent
# ---------------------------------------------------------------------------


def test_the_ordering_is_family_relative_and_not_a_single_global_rank() -> None:
    """12_KERNEL_ALGORITHMS.md §3.5 — there is no global trustworthiness score."""
    bank = SourceClass.BANK_OR_CARD_STATEMENT
    signed = SourceClass.SIGNED_AGREEMENT
    assert authority.authority_for("payment_received", bank) > (
        authority.authority_for("payment_received", signed)
    )
    assert authority.authority_for("service_terminated", bank) < (
        authority.authority_for("service_terminated", signed)
    )
    # Because the order inverts between families, comparing across families is
    # a category error and is refused rather than answered.
    with pytest.raises(authority.FamilyMismatchError):
        authority.rank(
            authority.SourceAuthority("payment_received", bank),
            authority.SourceAuthority("service_terminated", signed),
        )


def test_authority_ranking_orders_the_source_classes_of_one_predicate() -> None:
    payment = authority.authority_ranking("payment_received")
    assert [source_class for source_class, _ in payment][:3] == [
        SourceClass.BANK_OR_CARD_STATEMENT,
        SourceClass.PAYMENT_PROCESSOR_RECORD,
        SourceClass.USER_UPLOADED_RECEIPT,
    ]
    assert payment[0][1] == Decimal("0.9700")
    assert len(payment) == len(list(SourceClass))
    assert [score for _, score in payment] == sorted((score for _, score in payment), reverse=True)
    assert payment[-1][0] is SourceClass.MODEL_INFERENCE

    service = authority.authority_ranking("service_terminated")
    assert [source_class for source_class, _ in service][:3] == [
        SourceClass.SIGNED_AGREEMENT,
        SourceClass.PROVIDER_SYSTEM_NOTICE,
        SourceClass.PROVIDER_AGENT_WRITTEN,
    ]
    # The same two classes swap ends between the two families.
    assert service[-3][0] is SourceClass.BANK_OR_CARD_STATEMENT


def test_a_tie_retains_the_incumbent_and_still_demands_a_human() -> None:
    """§3.3: `winner = inc if delta >= 0 else chal`, then `abs(delta) >= margin`.

    Both halves matter. The incumbent is the nominal winner of a tie - nothing
    dislodges canonical state by drawing level with it - but a tie can never
    clear the 0.25 margin, so the conflict is `HUMAN_REQUIRED_AUTHORITY_TIE`
    and is never auto-resolved.
    """
    incumbent = authority.SourceAuthority("balance_owed", SourceClass.PROVIDER_SYSTEM_NOTICE)
    challenger = authority.SourceAuthority("balance_owed", SourceClass.PROVIDER_SYSTEM_NOTICE)
    ranking = authority.rank(incumbent, challenger)

    assert ranking.delta == Decimal("0.0000")
    assert ranking.winner is incumbent
    assert ranking.winner_is_incumbent is True
    assert ranking.margin_met is False
    assert ranking.floor_met is True
    assert ranking.auto_resolvable is False
    assert ranking.reason_code is KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE


def test_the_incumbent_is_retained_when_it_outranks_by_the_margin() -> None:
    incumbent = authority.SourceAuthority("balance_owed", SourceClass.PROVIDER_SYSTEM_NOTICE)
    challenger = authority.SourceAuthority("balance_owed", SourceClass.MARKETING_PAGE)
    ranking = authority.rank(incumbent, challenger)

    assert ranking.delta == Decimal("0.6500")
    assert ranking.winner is incumbent
    assert ranking.winner_is_incumbent is True
    assert ranking.margin_met is True
    assert ranking.floor_met is True
    assert ranking.auto_resolvable is True
    assert ranking.reason_code is KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN


def test_the_challenger_is_promoted_when_it_outranks_by_the_margin() -> None:
    incumbent = authority.SourceAuthority("payment_received", SourceClass.MARKETING_PAGE)
    challenger = authority.SourceAuthority("payment_received", SourceClass.BANK_OR_CARD_STATEMENT)
    ranking = authority.rank(incumbent, challenger)

    assert ranking.delta == Decimal("-0.9200")
    assert ranking.winner is challenger
    assert ranking.winner_is_incumbent is False
    assert ranking.margin_met is True
    assert ranking.floor_met is True
    assert ranking.auto_resolvable is True
    assert ranking.reason_code is KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN


def test_a_wide_margin_between_two_weak_sources_still_needs_a_human() -> None:
    """The floor is a second, independent gate. A landslide among the
    unqualified is not a resolution.
    """
    incumbent = authority.SourceAuthority("payment_received", SourceClass.PROVIDER_AGENT_CHAT)
    challenger = authority.SourceAuthority("payment_received", SourceClass.MARKETING_PAGE)
    ranking = authority.rank(incumbent, challenger)

    assert Decimal("0.80") == authority.AUTO_RESOLVE_FLOOR
    assert Decimal("0.25") == authority.AUTO_RESOLVE_MARGIN
    assert ranking.delta == Decimal("0.4000")
    assert ranking.margin_met is True
    assert ranking.floor_met is False
    assert ranking.auto_resolvable is False
    assert ranking.winner is incumbent
    assert ranking.reason_code is KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE


def test_a_narrow_margin_between_two_strong_sources_is_the_authority_tie() -> None:
    """§3.5 corollary: two sources at or above 0.80 within 0.25 of each other
    cannot auto-resolve, by construction. This is the shape of `H1`.
    """
    incumbent = authority.SourceAuthority("service_terminated", SourceClass.SIGNED_AGREEMENT)
    challenger = authority.SourceAuthority("service_terminated", SourceClass.PROVIDER_SYSTEM_NOTICE)
    ranking = authority.rank(incumbent, challenger)

    assert ranking.delta == Decimal("0.0400")
    assert ranking.margin_met is False
    assert ranking.floor_met is True
    assert ranking.auto_resolvable is False
    assert ranking.reason_code is KernelReasonCode.HUMAN_REQUIRED_AUTHORITY_TIE


def test_an_entailed_challenger_loses_and_the_reason_names_the_penalty() -> None:
    """12_KERNEL_ALGORITHMS.md §1.6 step 12 — the hero disposition, exactly.

    The September invoice does not literally say service was active; `EN-1`
    entails it, and the 0.30 penalty is what makes the incumbent termination
    confirmation win by more than the margin.
    """
    incumbent = authority.SourceAuthority("service_terminated", SourceClass.PROVIDER_SYSTEM_NOTICE)
    challenger = authority.SourceAuthority(
        "service_active", SourceClass.PROVIDER_SYSTEM_NOTICE, entailed=True
    )
    ranking = authority.rank(incumbent, challenger)

    assert incumbent.authority == Decimal("0.8800")
    assert challenger.authority == Decimal("0.5800")
    assert ranking.delta == Decimal("0.3000")
    assert ranking.margin_met is True
    assert ranking.floor_met is True
    assert ranking.auto_resolvable is True
    assert ranking.winner_is_incumbent is True
    assert ranking.reason_code is KernelReasonCode.AUTO_RESOLVED_ENTAILMENT_PENALTY


def test_a_ranking_is_deterministic_frozen_and_reports_its_own_inputs() -> None:
    """A disposition that depended on call order would be unauditable."""
    incumbent = authority.SourceAuthority("amount_outstanding", SourceClass.SIGNED_AGREEMENT)
    challenger = authority.SourceAuthority("amount_outstanding", SourceClass.MARKETING_PAGE)
    first = authority.rank(incumbent, challenger)
    second = authority.rank(incumbent, challenger)

    assert first == second
    assert first.delta == Decimal("0.7000")
    assert first.incumbent is incumbent
    assert first.challenger is challenger
    assert first.family == "OUTSTANDING"

    reversed_ranking = authority.rank(challenger, incumbent)
    assert reversed_ranking.winner is incumbent
    assert reversed_ranking.winner_is_incumbent is False

    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError is a subclass
        first.winner_is_incumbent = False  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError is a subclass
        incumbent.entailed = True  # type: ignore[misc]
