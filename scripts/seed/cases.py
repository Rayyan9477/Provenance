"""One context, ten cases (``T2.8`` step 3).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.4 -- the ten-case table.
- ``docs/quality/22_EVAL_DATASETS.md`` section 2 -- the same ten cases with a
  seeded ``revision`` per case, which section 17.4 gives for case 1 only.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero dataset canon -- the context title.

Four of the six relationships are in scope
------------------------------------------
``G12.1`` asserts the dashboard shows **4 relationships** against a seed of 6.
``70_TASK_PLAN.md`` section 24 risk 3 records that no document states why, and
that the plausible reading is a dashboard scoped to "The Move". This module
takes that reading literally: the two out-of-context cases -- the new-address
installation credit and the Cascade Power meter reading -- carry
``context_id = NULL``, so "relationships in the context" is 4 by construction
and the discrepancy is a measurable property of the seed rather than an
argument. Filed; not silently resolved.

Why the revisions are seeded and not counted
--------------------------------------------
Case 1 is seeded at ``revision = 12`` because the demo's reveal is the move to
13. Verification query V5 asserts ``count(DISTINCT state_transitions.case_revision)
<= cases.revision``, which holds for any ledger the Kernel writes in step 9 --
including the empty one this profile leaves behind.
"""

from __future__ import annotations

from datetime import timedelta

from scripts.seed.counterparties import relationship_of
from scripts.seed.ids import CONTEXT_OPENED_AT, DEPOSIT_DUE_AT, FINAL_INSPECTION_AT, sid
from scripts.seed.rows import SeedCase, SeedContext
from scripts.seed.tenants import HERO_TENANT, HERO_USER

__all__ = ["CASES", "CONTEXTS", "THE_MOVE", "case_of"]

THE_MOVE = SeedContext(
    id=sid("context", "the-move"),
    tenant_id=HERO_TENANT.id,
    user_id=HERO_USER.id,
    slug="the-move",
    title="The Move — 214 Ridgeway to 88 Larkin",
    context_type="MOVE",
    status="ACTIVE",
    started_at=CONTEXT_OPENED_AT,
    ended_at=None,
)

CONTEXTS: tuple[SeedContext, ...] = (THE_MOVE,)


def _case(
    slug: str,
    relationship_slug: str,
    case_type: str,
    title: str,
    status: str,
    revision: int,
    *,
    opened_days_before_due: int,
    resolved_days_before_due: int | None,
    last_activity_days_before_due: int,
    in_the_move: bool = True,
    attention_level: str = "NONE",
) -> SeedCase:
    return SeedCase(
        id=sid("case", slug),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        relationship_id=relationship_of(relationship_slug).id,
        context_id=THE_MOVE.id if in_the_move else None,
        slug=slug,
        case_type=case_type,
        title=title,
        status=status,
        revision=revision,
        opened_at=DEPOSIT_DUE_AT - timedelta(days=opened_days_before_due),
        resolved_at=(
            None
            if resolved_days_before_due is None
            else DEPOSIT_DUE_AT - timedelta(days=resolved_days_before_due)
        ),
        last_activity_at=DEPOSIT_DUE_AT - timedelta(days=last_activity_days_before_due),
        attention_level=attention_level,
    )


CASES: tuple[SeedCase, ...] = (
    # 1 -- the hero case. Reopens when the June invoice arrives.
    _case(
        "isp-cancellation",
        "northline-old",
        "SERVICE_CANCELLATION",
        "Old ISP service cancellation",
        "RESOLVED",
        12,
        opened_days_before_due=32,
        resolved_days_before_due=14,
        last_activity_days_before_due=14,
    ),
    # 2 -- near-miss retrieval target.
    _case(
        "isp-final-bill",
        "northline-old",
        "BILLING_DISPUTE",
        "Old ISP final bill reconciliation",
        "RESOLVED",
        6,
        opened_days_before_due=26,
        resolved_days_before_due=10,
        last_activity_days_before_due=10,
    ),
    # 3 -- the second reveal. USD 1,800 overdue, trigger ARMED.
    _case(
        "landlord-deposit",
        "harborview-tenancy",
        "DEPOSIT_RETURN",
        "Landlord deposit return",
        "WAITING",
        9,
        opened_days_before_due=30,
        resolved_days_before_due=None,
        last_activity_days_before_due=-5,
        attention_level="ATTENTION",
    ),
    # 4 -- grounds the 30-day clock.
    _case(
        "landlord-inspection",
        "harborview-tenancy",
        "GENERAL",
        "Landlord final inspection",
        "RESOLVED",
        4,
        opened_days_before_due=40,
        resolved_days_before_due=30,
        last_activity_days_before_due=30,
    ),
    # 5 -- USD 420 committed, USD 200 paid, USD 220 outstanding.
    _case(
        "movers-damage",
        "beltline-engagement",
        "DAMAGE_REIMBURSEMENT",
        "Movers damage reimbursement",
        "WAITING",
        5,
        opened_days_before_due=34,
        resolved_days_before_due=None,
        last_activity_days_before_due=-2,
        attention_level="INFO",
    ),
    # 6 -- timeline texture.
    _case(
        "movers-scheduling",
        "beltline-engagement",
        "GENERAL",
        "Movers scheduling dispute",
        "RESOLVED",
        3,
        opened_days_before_due=52,
        resolved_days_before_due=44,
        last_activity_days_before_due=44,
    ),
    # 7 -- the clean "resolved" row.
    _case(
        "employer-relocation",
        "kestrel-employment",
        "EXPENSE_REIMBURSEMENT",
        "Employer relocation reimbursement",
        "RESOLVED",
        4,
        opened_days_before_due=38,
        resolved_days_before_due=18,
        last_activity_days_before_due=18,
    ),
    # 8 -- timeline texture.
    _case(
        "employer-stipend",
        "kestrel-employment",
        "EXPENSE_REIMBURSEMENT",
        "Employer temporary housing stipend",
        "RESOLVED",
        2,
        opened_days_before_due=46,
        resolved_days_before_due=36,
        last_activity_days_before_due=36,
    ),
    # 9 -- the identity decoy. Out of "The Move": it belongs to the new address.
    _case(
        "new-install-credit",
        "northline-new",
        "SERVICE_INSTALLATION",
        "New address installation credit",
        "OPEN",
        1,
        opened_days_before_due=12,
        resolved_days_before_due=None,
        last_activity_days_before_due=12,
        in_the_move=False,
    ),
    # 10 -- the out-of-context decoy.
    _case(
        "final-meter-reading",
        "cascade-account",
        "ACCOUNT_CLOSURE",
        "Final meter reading",
        "RESOLVED",
        2,
        opened_days_before_due=28,
        resolved_days_before_due=20,
        last_activity_days_before_due=20,
        in_the_move=False,
    ),
)

_CASES_BY_SLUG = {c.slug: c for c in CASES}


def case_of(slug: str) -> SeedCase:
    return _CASES_BY_SLUG[slug]


#: The final inspection is the event the 30-day deposit promise hangs off, and
#: it is the day case 4 resolved. Asserting the identity here keeps the two
#: from drifting: ``FINAL_INSPECTION_AT`` is derived from ``DEPOSIT_DUE_AT``,
#: and case 4's ``resolved_at`` is derived from it independently.
assert case_of("landlord-inspection").resolved_at == FINAL_INSPECTION_AT
