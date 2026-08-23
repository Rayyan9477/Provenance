"""Deterministic identity and time for the seed (``T2.8``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.1 -- ``PROVENANCE_SEED_NS``,
  ``sid()``, ``DEMO_ANCHOR``, ``random.Random(20260817)``.
- ``docs/CANONICAL_DECISIONS.md`` -> **Hero dataset canon** -- final inspection
  ``2026-05-16``, deposit ``due_at`` ``2026-06-15T00:00:00Z``, demo clock
  ``2026-09-18`` (95 days overdue), trigger wake ``due_at`` +
  ``WAKE_MARGIN_SECONDS``.
- ``docs/quality/20_TDD_STRATEGY.md`` section 4.2 rule 2 -- seeded timestamps
  are offsets from a seed epoch, never absolute literals.

This module is the **only** place in ``scripts/seed`` allowed to write an
absolute instant, and ``test_seed_determinism.py`` enforces that with an AST
scan. Everything else derives from :data:`DEMO_ANCHOR`.

Why that rule is worth an AST scan
----------------------------------
``23_PHASE_GATES.md`` describes the failure exactly: a seed that stores
``datetime(2026, 6, 15)`` is correct on the day it is written and quietly wrong
forever after, because "four months ago" becomes "fourteen months ago" without
one test changing colour. The trigger suite runs at two frozen clocks (``G10.6``)
and must produce identical pass/fail, which is only possible if every seeded
instant moves with the anchor.

Why ``uuid5`` and not ``uuid4``
-------------------------------
Re-running the seed must be idempotent, and every fixture, test and demo script
must be able to hard-code an id. ``sid('case', 'isp-cancellation')`` is the same
UUID forever, on every machine, in every process -- which is what makes
``INSERT ... ON CONFLICT DO NOTHING`` a *reseed* rather than a duplicate load.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

__all__ = [
    "CONTEXT_OPENED_AT",
    "DEMO_ANCHOR",
    "DEMO_ANCHOR_UTC",
    "DEPOSIT_DUE_AT",
    "DEPOSIT_OVERDUE_DAYS",
    "FINAL_INSPECTION_AT",
    "LOOKBACK_DAYS",
    "PROVENANCE_SEED_NS",
    "TRIGGER_WAKE_AT",
    "WAKE_MARGIN_SECONDS",
    "days_before_anchor",
    "midnight_before_anchor",
    "sid",
]

#: ``10_DATABASE_DDL.md`` section 17.1. Never regenerate this value: every
#: committed fixture id in the repository is a ``uuid5`` under it.
PROVENANCE_SEED_NS = uuid.UUID("6f2b1c40-0000-4000-8000-70726f76656e")


def sid(*parts: str) -> uuid.UUID:
    """Stable seed id. ``sid('case', 'isp-cancellation')`` is the same UUID forever."""
    return uuid.uuid5(PROVENANCE_SEED_NS, ":".join(parts))


# ---------------------------------------------------------------------------
# The demo clock
# ---------------------------------------------------------------------------

#: ``2026-09-18T09:00:00-04:00``. The single frozen anchor. The root
#: ``conftest.py`` pins the identical instant for ``frozen_clock``, and
#: ``db/seeds/MANIFEST.json`` records it so a reader can check the two agree.
DEMO_ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4)))

#: The same instant as UTC, which is how every row stores it.
DEMO_ANCHOR_UTC = DEMO_ANCHOR.astimezone(UTC)


def days_before_anchor(days: float, *, hours: float = 0.0) -> datetime:
    """An instant *days* (and *hours*) before :data:`DEMO_ANCHOR`, in UTC."""
    return DEMO_ANCHOR_UTC - timedelta(days=days, hours=hours)


def midnight_before_anchor(days: int) -> datetime:
    """Midnight UTC on the day *days* before :data:`DEMO_ANCHOR`."""
    return days_before_anchor(days).replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# The four canon dates, every one of them an offset from the anchor
# ---------------------------------------------------------------------------

#: Every "days overdue" figure the product renders derives from this against
#: the demo clock. It is not stored anywhere as a number.
DEPOSIT_OVERDUE_DAYS = 95

#: ``2026-06-15T00:00:00Z``.
DEPOSIT_DUE_AT = midnight_before_anchor(DEPOSIT_OVERDUE_DAYS)

#: ``2026-05-16``. The promise was written "within 30 days of inspection", so
#: the inspection is the due date minus exactly thirty days -- expressed that
#: way round because the *promise* is the thing the evidence contains.
FINAL_INSPECTION_AT = DEPOSIT_DUE_AT - timedelta(days=30)

#: ``16_TRIGGER_DSL.md``: a trigger wakes at ``due_at`` plus a margin, so a
#: scheduler firing a hair early cannot evaluate a predicate that is not yet
#: true. ``2026-06-15T00:01:00Z``.
WAKE_MARGIN_SECONDS = 60
TRIGGER_WAKE_AT = DEPOSIT_DUE_AT + timedelta(seconds=WAKE_MARGIN_SECONDS)

#: ``2026-04-02``. "The Move" opened 74 days before the deposit fell due.
CONTEXT_OPENED_AT = DEPOSIT_DUE_AT - timedelta(days=74)

#: ``13_RETRIEVAL_SPEC.md`` ``DEFAULT_LOOKBACK_DAYS``. The decoy corpus is
#: spread over this window so Stage C's temporal filter has something to
#: exclude.
LOOKBACK_DAYS = 540
