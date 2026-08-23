"""Five counterparties, six relationships (``T2.8`` step 3).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.3 -- the naming and seeding
  authority, table transcribed exactly.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero dataset canon.

The two facts in this file that are load-bearing
------------------------------------------------
**Northline Fiber carries two relationships on one counterparty** -- old
apartment account ``NF-4471-8802`` and new address account ``NF-9913-2250``.
Same counterparty, same sender domain, same brand voice, different account.
An identity gate that matches on counterparty name alone attaches the hero
invoice to the wrong relationship and the whole demo collapses. This pair is
what proves ``idx_relationships_external_ref`` is load-bearing.

**Kestrel Analytics is the EMPLOYER, never the mover.** An earlier draft made
"Kestrel Moving Co." the moving company, which would have attributed the USD
420 damage claim to the user's employer -- a wrong answer that would have read
as a plausible one on screen. ``test_seed_canon.py`` asserts both the kind and
the absence of the retired name.
"""

from __future__ import annotations

from datetime import timedelta

from scripts.seed.ids import CONTEXT_OPENED_AT, DEPOSIT_DUE_AT, sid
from scripts.seed.rows import SeedCounterparty, SeedRelationship
from scripts.seed.tenants import HERO_TENANT, HERO_USER

__all__ = ["COUNTERPARTIES", "RELATIONSHIPS", "counterparty_of", "relationship_of"]


def _cp(slug: str, display_name: str, kind: str, domain: str) -> SeedCounterparty:
    return SeedCounterparty(
        id=sid("counterparty", slug),
        tenant_id=HERO_TENANT.id,
        slug=slug,
        # ``ck_counterparties_normalized`` requires lower-case.
        normalized_name=display_name.lower(),
        display_name=display_name,
        kind=kind,
        canonical_domain=domain,
        known_domains=[domain],
    )


NORTHLINE = _cp("northline-fiber", "Northline Fiber", "ISP", "northlinefiber.example")
HARBORVIEW = _cp(
    "harborview-property-management",
    "Harborview Property Management",
    "LANDLORD",
    "harborviewpm.example",
)
BELTLINE = _cp("beltline-movers", "Beltline Movers", "MOVING_COMPANY", "beltlinemovers.example")
KESTREL = _cp("kestrel-analytics", "Kestrel Analytics", "EMPLOYER", "kestrelanalytics.example")
CASCADE = _cp("cascade-power", "Cascade Power", "UTILITY", "cascadepower.example")

COUNTERPARTIES: tuple[SeedCounterparty, ...] = (
    NORTHLINE,
    HARBORVIEW,
    BELTLINE,
    KESTREL,
    CASCADE,
)


def _rel(
    slug: str,
    counterparty: SeedCounterparty,
    relationship_type: str,
    label: str,
    external_account_ref: str,
    status: str,
    *,
    valid_to_days_before_due: int | None = None,
) -> SeedRelationship:
    return SeedRelationship(
        id=sid("relationship", slug),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        counterparty_id=counterparty.id,
        slug=slug,
        relationship_type=relationship_type,
        label=label,
        external_account_ref=external_account_ref,
        status=status,
        # Two years before "The Move" opened: long enough that the tenancy and
        # the service accounts predate every seeded evidence item.
        valid_from=CONTEXT_OPENED_AT - timedelta(days=730),
        valid_to=(
            None
            if valid_to_days_before_due is None
            else DEPOSIT_DUE_AT - timedelta(days=valid_to_days_before_due)
        ),
    )


#: Old apartment service account. Terminated 31 May 2026 -- fifteen days before
#: the deposit fell due -- which is the fact the June invoice contradicts.
NORTHLINE_OLD = _rel(
    "northline-old",
    NORTHLINE,
    "SERVICE_ACCOUNT",
    "Northline Fiber — 214 Ridgeway Apt 3B",
    "NF-4471-8802",
    "CLOSED",
    valid_to_days_before_due=15,
)

#: New address service account. Still open, still billing, and the reason an
#: identity gate that matches on counterparty name alone gets the wrong answer.
NORTHLINE_NEW = _rel(
    "northline-new",
    NORTHLINE,
    "SERVICE_ACCOUNT",
    "Northline Fiber — 88 Larkin",
    "NF-9913-2250",
    "ACTIVE",
)

HARBORVIEW_TENANCY = _rel(
    "harborview-tenancy",
    HARBORVIEW,
    "TENANCY",
    "Harborview Property Management — 214 Ridgeway Apt 3B",
    "HPM-LEASE-2024-3B",
    "CLOSED",
    valid_to_days_before_due=30,
)

BELTLINE_ENGAGEMENT = _rel(
    "beltline-engagement",
    BELTLINE,
    "VENDOR_ENGAGEMENT",
    "Beltline Movers — job #88214",
    "BM-88214",
    "ACTIVE",
)

KESTREL_EMPLOYMENT = _rel(
    "kestrel-employment",
    KESTREL,
    "EMPLOYMENT",
    "Kestrel Analytics — relocation programme",
    "KA-EMP-3308",
    "ACTIVE",
)

CASCADE_ACCOUNT = _rel(
    "cascade-account",
    CASCADE,
    "SERVICE_ACCOUNT",
    "Cascade Power — 214 Ridgeway Apt 3B",
    "CP-770194",
    "CLOSED",
    valid_to_days_before_due=20,
)

RELATIONSHIPS: tuple[SeedRelationship, ...] = (
    NORTHLINE_OLD,
    NORTHLINE_NEW,
    HARBORVIEW_TENANCY,
    BELTLINE_ENGAGEMENT,
    KESTREL_EMPLOYMENT,
    CASCADE_ACCOUNT,
)

_CP_BY_SLUG = {c.slug: c for c in COUNTERPARTIES}
_REL_BY_SLUG = {r.slug: r for r in RELATIONSHIPS}


def counterparty_of(slug: str) -> SeedCounterparty:
    return _CP_BY_SLUG[slug]


def relationship_of(slug: str) -> SeedRelationship:
    return _REL_BY_SLUG[slug]
