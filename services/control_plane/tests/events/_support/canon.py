"""The hero canon, transcribed once, and the row shapes the lane builds on.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero dataset canon*: final inspection
  ``2026-05-16``, deposit ``due_at`` ``2026-06-15T00:00:00Z``, trigger wake at
  ``due_at`` + ``WAKE_MARGIN_SECONDS`` = ``2026-06-15T00:01:00Z``, and 95 days
  overdue against the ``2026-09-18`` demo clock.
- ``docs/specs/16_TRIGGER_DSL.md`` section 7.2 for the two row shapes and
  section 12.1 for the predicate.

Why these are literals and not arithmetic
-----------------------------------------
Every one of them is a canon value that four documents once disagreed about. A
literal fails a test when the canon moves; a ``timedelta`` chain quietly
re-derives whatever the canon now says and the drift becomes invisible. The one
number this module *does* compute is nothing — even ``DAYS_OVERDUE_AT_DEMO`` is
transcribed, and ``test_hero_trigger.py`` proves the projection derives the same
95 independently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "DAYS_OVERDUE_AT_DEMO",
    "DEMO_CLOCK_UTC",
    "DEPOSIT_DUE_AT",
    "DEPOSIT_OUTSTANDING",
    "HERO_CASE_ID",
    "HERO_COMMITMENT_ID",
    "HERO_TENANT_ID",
    "HERO_TRIGGER_ID",
    "HERO_USER_ID",
    "TRIGGER_WAKE_AT",
    "case_row",
    "commitment_row",
    "false_predicate_document",
    "hero_predicate_document",
    "trigger_row",
]

#: ``CANONICAL_DECISIONS.md`` -> Hero dataset canon, "Deposit ``due_at``".
DEPOSIT_DUE_AT = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)

#: The same table's "Trigger wake": ``due_at`` + ``WAKE_MARGIN_SECONDS``.
TRIGGER_WAKE_AT = datetime(2026, 6, 15, 0, 1, 0, tzinfo=UTC)

#: The demo clock as UTC. ``DEMO_ANCHOR`` in the root ``conftest.py`` is the
#: same instant written in ``America/New_York``.
DEMO_CLOCK_UTC = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)

#: "95 days", in the canon's own words.
DAYS_OVERDUE_AT_DEMO = 95

DEPOSIT_OUTSTANDING = Decimal("1800.0000")

HERO_TENANT_ID = uuid.UUID("0f6c1e88-2a94-4b31-8d5c-77e1a0b93f42")
HERO_USER_ID = uuid.UUID("b1d47a03-8e26-4c9f-a0b3-5f2c9d8e1470")
HERO_CASE_ID = uuid.UUID("4d2b8e10-6c3a-4f77-9a51-b8e0d3c7a291")
HERO_TRIGGER_ID = uuid.UUID("a7e3d901-5b48-4c26-9f13-8d0a2e6b4c77")
HERO_COMMITMENT_ID = uuid.UUID("9c1f4b2e-7a55-4d31-b0c7-2f8e6a91d044")


def hero_predicate_document(commitment_id: uuid.UUID = HERO_COMMITMENT_ID) -> dict[str, Any]:
    """``16_TRIGGER_DSL.md`` section 12.1, transcribed.

    Seven conjuncts: the deadline exists, it has passed by the database clock,
    money is still outstanding, and the commitment is neither fulfilled,
    superseded nor expired while the case is not resolved.
    """
    deposit = "commitments.deposit"
    return {
        "ast_version": "1.0",
        "bindings": {"deposit": {"kind": "COMMITMENT", "id": str(commitment_id)}},
        "predicate": {
            "op": "AND",
            "args": [
                {"op": "NOT_NULL", "arg": {"op": "FIELD", "path": f"{deposit}.due_at"}},
                {
                    "op": "GTE",
                    "left": {"op": "FIELD", "path": "clock.now"},
                    "right": {"op": "FIELD", "path": f"{deposit}.due_at"},
                },
                {
                    "op": "GT",
                    "left": {"op": "FIELD", "path": f"{deposit}.outstanding_amount"},
                    "right": {"op": "CONST", "type": "DECIMAL", "value": "0"},
                },
                {
                    "op": "NE",
                    "left": {"op": "FIELD", "path": f"{deposit}.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "FULFILLED"},
                },
                {
                    "op": "NE",
                    "left": {"op": "FIELD", "path": f"{deposit}.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "SUPERSEDED"},
                },
                {
                    "op": "NE",
                    "left": {"op": "FIELD", "path": f"{deposit}.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "EXPIRED"},
                },
                {
                    "op": "NE",
                    "left": {"op": "FIELD", "path": "case.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "RESOLVED"},
                },
            ],
        },
    }


def false_predicate_document(commitment_id: uuid.UUID = HERO_COMMITMENT_ID) -> dict[str, Any]:
    """A genuinely false predicate over the *unmodified* hero state.

    ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration*: "Use the same
    manual-wake entry point for a false-predicate no-op and the landlord fire.
    Do not mutate and secretly revert canonical state for presentation."

    This is that predicate, and the honesty is in the third conjunct: it asks
    whether the commitment already carries an admitted fulfillment. On the hero
    deposit it does not — nothing was ever paid — so the term is ``FALSE``
    against exactly the same rows the landlord trigger reads. No state is
    touched, before or after.
    """
    deposit = "commitments.deposit"
    return {
        "ast_version": "1.0",
        "bindings": {"deposit": {"kind": "COMMITMENT", "id": str(commitment_id)}},
        "predicate": {
            "op": "AND",
            "args": [
                {"op": "NOT_NULL", "arg": {"op": "FIELD", "path": f"{deposit}.due_at"}},
                {
                    "op": "EQ",
                    "left": {"op": "FIELD", "path": f"{deposit}.has_admitted_fulfillment"},
                    "right": {"op": "CONST", "type": "BOOL", "value": True},
                },
            ],
        },
    }


def case_row(
    *,
    db_now: datetime = DEMO_CLOCK_UTC,
    status: str = "WAITING",
    revision: int = 11,
    resolved_at: datetime | None = None,
    total_outstanding: str = "2020.0000",
    outstanding_currency: str | None = "USD",
) -> dict[str, Any]:
    """One row shaped like query (1) of ``16_TRIGGER_DSL.md`` section 7.2."""
    return {
        "case_id": HERO_CASE_ID,
        "tenant_id": HERO_TENANT_ID,
        "user_id": HERO_USER_ID,
        "case_status": status,
        "case_revision": revision,
        "attention_level": "ATTENTION",
        "reopened_count": 0,
        "opened_at": datetime(2026, 5, 16, 14, 22, 0, tzinfo=UTC),
        "resolved_at": resolved_at,
        "last_activity_at": datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC),
        "db_now": db_now,
        "open_conflict_count": 1,
        "needs_human_conflict_count": 1,
        "active_commitment_count": 2,
        "total_outstanding_amount": total_outstanding,
        "outstanding_currency": outstanding_currency,
    }


def commitment_row(
    *,
    status: str = "ACTIVE",
    outstanding: str | None = "1800.0000",
    due_at: datetime | None = DEPOSIT_DUE_AT,
    has_admitted_fulfillment: bool = False,
    commitment_id: uuid.UUID = HERO_COMMITMENT_ID,
) -> dict[str, Any]:
    """One row shaped like query (2) of ``16_TRIGGER_DSL.md`` section 7.2."""
    return {
        "id": commitment_id,
        "status": status,
        "commitment_type": "MONETARY_RETURN",
        "revision": 4,
        "currency": "USD",
        "committed_amount": Decimal("1800.0000"),
        "fulfilled_amount": Decimal("0.0000"),
        "outstanding_amount": None if outstanding is None else Decimal(outstanding),
        "due_at": due_at,
        "valid_from": datetime(2026, 5, 16, 14, 22, 0, tzinfo=UTC),
        "valid_to": None,
        "has_admitted_fulfillment": has_admitted_fulfillment,
    }


def trigger_row(
    *,
    trigger_id: uuid.UUID = HERO_TRIGGER_ID,
    state: str = "ARMED",
    evaluation_version: int = 1,
    not_before: datetime | None = TRIGGER_WAKE_AT,
    expires_at: datetime | None = datetime(2027, 6, 15, 0, 0, 0, tzinfo=UTC),
    basis_case_revision: int = 11,
    trigger_type: str = "COMMITMENT_DEADLINE",
    predicate_ast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One ``prospective_triggers`` row. Column names from migration ``0006``."""
    return {
        "id": trigger_id,
        "tenant_id": HERO_TENANT_ID,
        "user_id": HERO_USER_ID,
        "case_id": HERO_CASE_ID,
        "trigger_type": trigger_type,
        "predicate_ast": predicate_ast if predicate_ast is not None else hero_predicate_document(),
        "not_before": not_before,
        "expires_at": expires_at,
        "state": state,
        "evaluation_version": evaluation_version,
        "basis_case_revision": basis_case_revision,
        "schedule_name": f"pv-trg-{trigger_id.hex}-v{evaluation_version}",
        "last_evaluated_at": None,
        "last_result": None,
        "last_reason_code": None,
        "fired_at": None,
    }
