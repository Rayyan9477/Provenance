"""Fixtures for the hermetic Memory Kernel suite.

Every test in this directory carries ``pytest.mark.unit``, which the repository
root ``conftest.py`` turns into mechanism E3 from
``quality/20_TDD_STRATEGY.md`` section 2.3: the five credential environment
variables are unset and every outbound socket reach raises. If anything in this
suite needs a network call, a database or a model, the boundary is wrong and
section 2.4 names the diagnosis, none of which is "mock the model".

Nothing here mutates :data:`DEFAULT_KERNEL_CONFIG`. A test that wants a
different threshold builds its own :class:`KernelConfig`, per the section 4.1
fixture contract.

Why the constants arrive as a fixture rather than as module-level names
-----------------------------------------------------------------------
``pyproject.toml`` sets ``--import-mode=importlib`` and ``.ruff.toml``'s
``ban-relative-imports = "all"``, so a test module cannot ``from .conftest
import``. The shared identities therefore travel on one frozen
:class:`Hero` object handed over by the :func:`hero` fixture, which is the
supported route and keeps the seeded vocabulary in exactly one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from types import EllipsisType
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from provenance_domain.enums import ClaimKind, EpistemicStatus, SourceClass, SubjectType
from services.control_plane.app.memory_kernel import families as fam
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)

__all__ = ["Hero", "PropositionFactory"]

PropositionFactory = Callable[..., prop.Proposition]


# The seeded identities, hoisted to module level so the frozen dataclass
# below has plain names as defaults. A dataclass default that is a *call*
# is evaluated once at class-definition time, which is fine for a UUID and
# a trap for anything mutable; ruff's RUF009 refuses to distinguish the two
# cases, and it is right not to.
_TZ: Final = ZoneInfo("America/New_York")
_TENANT: Final = uuid.UUID(int=0x8001)
_USER: Final = uuid.UUID(int=0x8002)
_OTHER_USER: Final = uuid.UUID(int=0x8003)
_REL_ISP: Final = uuid.UUID(int=0x1001)
_REL_OTHER: Final = uuid.UUID(int=0x1002)
_CASE_ISP: Final = uuid.UUID(int=0x2001)
_CM_DEPOSIT: Final = uuid.UUID(int=0x3001)
_CM_MOVING: Final = uuid.UUID(int=0x3002)
_CL_TERMINATED: Final = uuid.UUID(int=0x4001)
_CL_INVOICE: Final = uuid.UUID(int=0x4002)
_CL_OTHER: Final = uuid.UUID(int=0x4003)
_BV_SERVICE_V1: Final = uuid.UUID(int=0x5001)
_EV_ONE: Final = uuid.UUID(int=0x6001)
_EV_TWO: Final = uuid.UUID(int=0x6002)
_EV_FOREIGN: Final = uuid.UUID(int=0x6003)
_ART_ONE: Final = uuid.UUID(int=0x7001)
_ART_FOREIGN: Final = uuid.UUID(int=0x7002)
_JUN_1: Final = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
_JUL_1: Final = datetime(2026, 7, 1, 4, 0, tzinfo=UTC)
_MAY_1: Final = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)
_CONFIRMATION_RECORDED_AT: Final = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)
_INVOICE_RECORDED_AT: Final = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)
_FORWARD_RECORDED_AT: Final = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)
_TX_NOW: Final = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Hero:
    """The seeded identities and instants of the hero universe.

    Fixed UUIDs, not ``uuid4()``: ``quality/20_TDD_STRATEGY.md`` section 4.2
    rule 1 bans fixture-generated randomness, because a suite whose failures
    cannot be reproduced is a suite that cannot be debugged. The integers are
    chosen so the side-ordering assertions in ``test_contradiction.py`` have a
    known answer rather than a lucky one.

    The instants are written out rather than derived from the day-boundary
    functions, so a bug in that convention cannot make the fixture agree with
    itself.
    """

    tz: tzinfo = _TZ

    tenant: uuid.UUID = _TENANT
    user: uuid.UUID = _USER
    other_user: uuid.UUID = _OTHER_USER

    rel_isp: uuid.UUID = _REL_ISP
    rel_other: uuid.UUID = _REL_OTHER
    case_isp: uuid.UUID = _CASE_ISP
    cm_deposit: uuid.UUID = _CM_DEPOSIT
    cm_moving: uuid.UUID = _CM_MOVING

    #: The 15 May cancellation confirmation.
    cl_terminated: uuid.UUID = _CL_TERMINATED
    #: The September invoice: ``balance_owed`` USD 186.00.
    cl_invoice: uuid.UUID = _CL_INVOICE
    cl_other: uuid.UUID = _CL_OTHER
    bv_service_v1: uuid.UUID = _BV_SERVICE_V1

    ev_one: uuid.UUID = _EV_ONE
    ev_two: uuid.UUID = _EV_TWO
    ev_foreign: uuid.UUID = _EV_FOREIGN
    art_one: uuid.UUID = _ART_ONE
    art_foreign: uuid.UUID = _ART_FOREIGN

    #: `2026-06-01T04:00:00Z` - where the TERMINATED state begins.
    jun_1: datetime = _JUN_1
    #: `2026-07-01T04:00:00Z` - the exclusive end of the billed June period.
    jul_1: datetime = _JUL_1
    #: `2026-05-01T04:00:00Z` - the start of the late-arriving May letter (L-3).
    may_1: datetime = _MAY_1

    confirmation_recorded_at: datetime = _CONFIRMATION_RECORDED_AT
    invoice_recorded_at: datetime = _INVOICE_RECORDED_AT
    forward_recorded_at: datetime = _FORWARD_RECORDED_AT
    tx_now: datetime = _TX_NOW

    #: `CANONICAL_DECISIONS.md` -> *Hero conflict*: the exposure that turns H5.
    invoice_amount: Decimal = Decimal("186.0000")
    #: `CANONICAL_DECISIONS.md` -> *Hero dataset canon*: the Harborview deposit.
    deposit_amount: Decimal = Decimal("1800.0000")
    #: The Beltline Movers damage claim, and what was paid against it.
    moving_committed: Decimal = Decimal("420.0000")
    moving_paid: Decimal = Decimal("200.0000")


@pytest.fixture
def hero() -> Hero:
    """The seeded hero universe, frozen."""
    return Hero()


@pytest.fixture
def cfg() -> KernelConfig:
    """The shipped configuration. Never mutated."""
    return DEFAULT_KERNEL_CONFIG


@pytest.fixture
def make_proposition(hero: Hero) -> PropositionFactory:
    """Build a :class:`Proposition` with hero-shaped defaults.

    Deliberately a factory rather than a set of frozen instances: almost every
    matcher test differs from the hero by exactly one field, and naming that
    field at the call site is what makes the test read as a claim about
    behaviour rather than as a fixture lookup.
    """

    def _make(
        *,
        prop_id: uuid.UUID | EllipsisType = ...,
        family: fam.Family = fam.Family.SERVICE_STATUS,
        predicate: str = "service_active",
        value: fam.FamilyValue | None = None,
        subject_type: SubjectType = SubjectType.RELATIONSHIP,
        subject_id: uuid.UUID | EllipsisType = ...,
        valid_from: datetime | None | EllipsisType = ...,
        valid_to: datetime | None | EllipsisType = ...,
        validity_basis: prop.ValidityBasis = prop.ValidityBasis.EXPLICIT,
        base_authority: Decimal = Decimal("0.8800"),
        recorded_at: datetime | EllipsisType = ...,
        source_class: str | None = SourceClass.PROVIDER_SYSTEM_NOTICE.value,
        source_claim_kind: ClaimKind | None = ClaimKind.COUNTERPARTY_CLAIM,
        entailed_from: uuid.UUID | None = None,
        entailment_rule: str | None = None,
        actor_ref: str | None = "northline-fiber",
        is_incumbent: bool = False,
        epistemic_status: EpistemicStatus | None = None,
        belief_confidence: Decimal | None = None,
        source_kind: prop.PropositionSourceKind = prop.PropositionSourceKind.CLAIM,
        service_period: tuple[datetime, datetime] | None = None,
    ) -> prop.Proposition:
        return prop.Proposition(
            prop_id=hero.cl_invoice if prop_id is ... else prop_id,
            source_kind=source_kind,
            subject_type=subject_type,
            subject_id=hero.rel_isp if subject_id is ... else subject_id,
            family=family,
            predicate=predicate,
            value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE) if value is None else value,
            valid_from=hero.jun_1 if valid_from is ... else valid_from,
            valid_to=hero.jul_1 if valid_to is ... else valid_to,
            validity_basis=validity_basis,
            base_authority=base_authority,
            recorded_at=hero.invoice_recorded_at if recorded_at is ... else recorded_at,
            source_class=source_class,
            source_claim_kind=source_claim_kind,
            entailed_from=entailed_from,
            entailment_rule=entailment_rule,
            actor_ref=actor_ref,
            is_incumbent=is_incumbent,
            epistemic_status=epistemic_status,
            belief_confidence=belief_confidence,
            service_period=service_period,
        )

    return _make


@pytest.fixture
def incumbent_terminated(hero: Hero, make_proposition: PropositionFactory) -> prop.Proposition:
    """``bv_isp_service_v1``: TERMINATED from 1 June 04:00Z, open-ended, 0.88.

    The direct cancellation confirmation. ``EXPLICIT_OPEN`` because the source
    stated a lower bound and no upper one, which is the commonest shape a
    cancellation has.
    """
    return make_proposition(
        prop_id=hero.bv_service_v1,
        source_kind=prop.PropositionSourceKind.BELIEF_VERSION,
        predicate="service_terminated",
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        valid_from=hero.jun_1,
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        base_authority=Decimal("0.8800"),
        recorded_at=hero.confirmation_recorded_at,
        is_incumbent=True,
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.9400"),
    )


@pytest.fixture
def entailed_active(hero: Hero, make_proposition: PropositionFactory) -> prop.Proposition:
    """The ``EN-1`` product: SERVICE_STATUS ACTIVE over June, authority 0.58."""
    return make_proposition(
        prop_id=hero.cl_invoice,
        predicate="service_active",
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        valid_from=hero.jun_1,
        valid_to=hero.jul_1,
        validity_basis=prop.ValidityBasis.EXPLICIT,
        base_authority=Decimal("0.8800"),
        entailed_from=hero.cl_invoice,
        entailment_rule=prop.EN_1,
    )
