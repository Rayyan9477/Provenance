"""Four commitments, two fulfillments, two triggers (``T2.8`` step 9).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.6 -- the exact figures and
  statuses, transcribed.
- ``docs/quality/22_EVAL_DATASETS.md`` section 2 -- the same four commitments,
  restated as eval ground truth.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero dataset canon -- deposit ``due_at``
  ``2026-06-15T00:00:00Z``, trigger wake ``due_at`` + 60s.

**Nothing in this module is written by the seed program.**
--------------------------------------------------------
``commitments``, ``fulfillments`` and ``prospective_triggers`` are canonical
tables. ``10_DATABASE_DDL.md`` section 12 grants ``INSERT`` on all three to
``pv_kernel_writer``, and ``CANONICAL_DECISIONS.md`` makes the deterministic
Memory Kernel the *only* canonical writer. ``70_TASK_PLAN.md`` T2.8 step 9 is
explicit: "Seeding canonical rows by raw INSERT to unblock Phase 2 would create
a second canonical writer and is forbidden."

So these are **fixtures**: the input to step 9, replayed through
``MemoryKernel.commit()`` when Phase 4 delivers it. Until then the tables are
empty by design, ``db/seeds/MANIFEST.json`` records the expected zero *and*
names the reason, and the arithmetic below is asserted by unit test rather than
by a database CHECK.

The one number the landing screen renders
-----------------------------------------
USD **2,020.00** outstanding = Harborview 1,800.00 + Beltline 220.00.
Northline contributes **0**: its obligation is the non-monetary service
termination, and the June invoice's USD 186 moves ``epistemic_status`` from
``CONFIRMED`` to ``DISPUTED`` without moving an amount. A disputed balance
changes ``status``, never ``amount`` -- if the seed made that total anything
else, the landing screen would contradict the kernel.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from scripts.seed.cases import case_of
from scripts.seed.counterparties import BELTLINE, HARBORVIEW, KESTREL, NORTHLINE
from scripts.seed.ids import DEPOSIT_DUE_AT, TRIGGER_WAKE_AT, sid
from scripts.seed.rows import SeedCommitment, SeedFulfillment, SeedTrigger
from scripts.seed.tenants import HERO_TENANT, HERO_USER

__all__ = [
    "COMMITMENTS",
    "COMMITMENT_TYPES",
    "FULFILLMENTS",
    "TRIGGERS",
    "outstanding_total",
]

#: ``ck_commitments_type``, transcribed from ``10_DATABASE_DDL.md`` section 7.2.
#: Present so a fixture using a plausible-but-absent value -- ``REFUND`` and
#: ``REIMBURSEMENT`` both read perfectly well and neither is in the vocabulary --
#: fails at import rather than three minutes into a bulk load.
COMMITMENT_TYPES: frozenset[str] = frozenset(
    {
        "MONETARY_PAYMENT",
        "MONETARY_REFUND",
        "MONETARY_REIMBURSEMENT",
        "MONETARY_CREDIT",
        "DEPOSIT_RETURN",
        "SERVICE_TERMINATION",
        "SERVICE_DELIVERY",
        "REPAIR",
        "RESPONSE",
        "DOCUMENT_DELIVERY",
        "CORRECTION",
        "OTHER",
    }
)

_ZERO = Decimal("0.00")


def _commitment(
    slug: str,
    case_slug: str,
    obligor_id: str,
    commitment_type: str,
    description: str,
    status: str,
    *,
    committed: str | None = None,
    fulfilled: str | None = None,
    due_days_from_deposit: int | None = None,
    source_claim_slug: str,
) -> SeedCommitment:
    committed_amount = None if committed is None else Decimal(committed)
    fulfilled_amount = None if committed is None else Decimal(fulfilled or "0.00")
    outstanding = (
        None
        if committed_amount is None or fulfilled_amount is None
        else committed_amount - fulfilled_amount
    )
    return SeedCommitment(
        id=sid("commitment", slug),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        case_slug=case_slug,
        slug=slug,
        obligor_type="COUNTERPARTY",
        obligor_id=obligor_id,
        beneficiary_type="USER",
        beneficiary_id=str(HERO_USER.id),
        commitment_type=commitment_type,
        description=description,
        currency=None if committed_amount is None else "USD",
        committed_amount=committed_amount,
        fulfilled_amount=fulfilled_amount,
        outstanding_amount=outstanding,
        due_at=(
            None
            if due_days_from_deposit is None
            else DEPOSIT_DUE_AT + timedelta(days=due_days_from_deposit)
        ),
        status=status,
        source_claim_slug=source_claim_slug,
        revision=case_of(case_slug).revision,
    )


COMMITMENTS: tuple[SeedCommitment, ...] = (
    _commitment(
        "deposit",
        "landlord-deposit",
        str(HARBORVIEW.id),
        "DEPOSIT_RETURN",
        "Return of the USD 1,800.00 security deposit within 30 days of the final inspection.",
        "ACTIVE",
        committed="1800.00",
        fulfilled="0.00",
        due_days_from_deposit=0,
        source_claim_slug="deposit-thirty-day-promise",
    ),
    _commitment(
        "damage",
        "movers-damage",
        str(BELTLINE.id),
        "MONETARY_REIMBURSEMENT",
        "Reimbursement of USD 420.00 for damage caused during the move.",
        "PARTIAL",
        committed="420.00",
        fulfilled="200.00",
        source_claim_slug="damage-reimbursement-promise",
    ),
    _commitment(
        "relocation",
        "employer-relocation",
        str(KESTREL.id),
        "MONETARY_REIMBURSEMENT",
        "Reimbursement of USD 2,350.00 of approved relocation expenses.",
        "FULFILLED",
        committed="2350.00",
        fulfilled="2350.00",
        source_claim_slug="relocation-expense-approved",
    ),
    _commitment(
        "termination",
        "isp-cancellation",
        str(NORTHLINE.id),
        "SERVICE_TERMINATION",
        "Termination of internet service at 214 Ridgeway Apt 3B effective 31 May 2026.",
        "FULFILLED",
        source_claim_slug="isp-termination-effective-31-may",
    ),
)

FULFILLMENTS: tuple[SeedFulfillment, ...] = (
    SeedFulfillment(
        id=sid("fulfillment", "damage-200"),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        commitment_slug="damage",
        evidence_slug="damage-partial-payment",
        slug="damage-200",
        currency="USD",
        amount=Decimal("200.00"),
        fulfilled_at=DEPOSIT_DUE_AT - timedelta(days=21),
        admission_status="ADMITTED",
        confidence=Decimal("0.98"),
    ),
    SeedFulfillment(
        id=sid("fulfillment", "relocation-2350"),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        commitment_slug="relocation",
        evidence_slug="relocation-reimbursement-received",
        slug="relocation-2350",
        currency="USD",
        amount=Decimal("2350.00"),
        fulfilled_at=DEPOSIT_DUE_AT - timedelta(days=18),
        admission_status="ADMITTED",
        confidence=Decimal("0.99"),
    ),
)


def _field(path: str) -> dict[str, Any]:
    return {"op": "FIELD", "path": path}


def _const(value: Any, type_: str) -> dict[str, Any]:
    return {"op": "CONST", "type": type_, "value": value}


def _overdue_predicate(binding: str, commitment_id: uuid.UUID) -> dict[str, Any]:
    """The seven-conjunct overdue predicate, in the envelope the parser reads.

    ``16_TRIGGER_DSL.md`` section 6 specifies ``{ast_version, bindings,
    predicate}``, binary operators taking ``left``/``right``, and operands
    shaped ``{"op": "FIELD", "path": ...}``. These documents previously used a
    bare AST node with an ``args`` list and ``{"node": "FIELD"}`` operands, and
    ``parse_spec`` rejected all of them with ``UNSUPPORTED_AST_VERSION``.

    It was latent rather than harmless. ``prospective_triggers`` is empty until
    the Kernel's arm path runs, so nothing had ever fed these to the parser:
    they are *data* in a seed module, so no import failed, no type checker
    objected, and no test touched them. The first thing that would have parsed
    them is the hero demo arming its own trigger -- which is the second reveal,
    on stage, in a recorded video.

    Seven conjuncts rather than the two ``10_DATABASE_DDL.md`` section 17.6
    prints: the deadline must exist, it must have passed by the *database*
    clock, money must still be outstanding, the commitment must be none of
    FULFILLED / SUPERSEDED / EXPIRED, and the case must not be resolved.
    The last four are what stop a trigger firing a demand for money that has
    already arrived, which is precisely the failure the re-evaluation rule
    exists to prevent.
    """
    money = f"commitments.{binding}"
    return {
        "ast_version": "1.0",
        "bindings": {binding: {"kind": "COMMITMENT", "id": str(commitment_id)}},
        "predicate": {
            "op": "AND",
            "args": [
                {"op": "NOT_NULL", "arg": _field(f"{money}.due_at")},
                {
                    "op": "GTE",
                    "left": _field("clock.now"),
                    "right": _field(f"{money}.due_at"),
                },
                {
                    "op": "GT",
                    "left": _field(f"{money}.outstanding_amount"),
                    "right": _const("0", "DECIMAL"),
                },
                {
                    "op": "NE",
                    "left": _field(f"{money}.status"),
                    "right": _const("FULFILLED", "STRING"),
                },
                {
                    "op": "NE",
                    "left": _field(f"{money}.status"),
                    "right": _const("SUPERSEDED", "STRING"),
                },
                {
                    "op": "NE",
                    "left": _field(f"{money}.status"),
                    "right": _const("EXPIRED", "STRING"),
                },
                # `case.status`, NOT `commitments.<binding>.case_status`. The
                # registry publishes twelve readable commitment fields and that
                # is not one of them -- the case is a separate projection root.
                # The first draft of this invented it, and `registry.resolve_field`
                # refused with UNKNOWN_FIELD, which is exactly why the test that
                # parses these uses the real resolver rather than a permissive
                # stub: a stub would have waved an unreadable path through and
                # the trigger would have failed at arm time instead.
                {
                    "op": "NE",
                    "left": _field("case.status"),
                    "right": _const("RESOLVED", "STRING"),
                },
            ],
        },
    }


_DEPOSIT_PREDICATE: dict[str, Any] = _overdue_predicate("deposit", sid("commitment", "deposit"))

_DAMAGE_PREDICATE: dict[str, Any] = _overdue_predicate("damage", sid("commitment", "damage"))


TRIGGERS: tuple[SeedTrigger, ...] = (
    SeedTrigger(
        id=sid("trigger", "deposit-overdue"),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        case_slug="landlord-deposit",
        slug="deposit-overdue",
        trigger_type="COMMITMENT_DEADLINE",
        predicate_ast=_DEPOSIT_PREDICATE,
        not_before=TRIGGER_WAKE_AT,
        expires_at=DEPOSIT_DUE_AT + timedelta(days=365),
        state="ARMED",
        basis_case_revision=case_of("landlord-deposit").revision,
        schedule_name="pv-trigger-deposit-overdue",
    ),
    SeedTrigger(
        id=sid("trigger", "damage-followup"),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        case_slug="movers-damage",
        slug="damage-followup",
        trigger_type="RESPONSE_DEADLINE",
        predicate_ast=_DAMAGE_PREDICATE,
        not_before=DEPOSIT_DUE_AT + timedelta(days=14),
        expires_at=DEPOSIT_DUE_AT + timedelta(days=365),
        state="ARMED",
        basis_case_revision=case_of("movers-damage").revision,
        schedule_name="pv-trigger-damage-followup",
    ),
)


def outstanding_total() -> Decimal:
    """USD 2,020.00 -- the figure the landing screen renders.

    Summed over monetary commitments only, exactly as the read model will sum
    it. A non-monetary commitment has ``outstanding_amount IS NULL`` and is not
    coerced to zero on the way in, because a NULL that silently becomes 0.00
    hides the difference between "nothing owed" and "not a money obligation".
    """
    return sum(
        (c.outstanding_amount for c in COMMITMENTS if c.outstanding_amount is not None),
        _ZERO,
    )


#: The closed vocabulary, enforced at import. ``10_DATABASE_DDL.md`` section 7.2
#: owns it and the database refuses anything else; catching it here turns a
#: mid-load constraint violation into an ImportError with the offending value.
assert {c.commitment_type for c in COMMITMENTS} <= COMMITMENT_TYPES, sorted(
    {c.commitment_type for c in COMMITMENTS} - COMMITMENT_TYPES
)
