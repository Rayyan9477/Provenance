"""Three tenants, three users (``T2.8`` step 3).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.2.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero dataset canon -- **Alex Rivera**,
  ``America/New_York``, ``judge_mode_enabled = true``.

Why the two isolation tenants are not optional
----------------------------------------------
Section 17.2: they exist so DDL section 19 tests 11 and 12 have something real
to fail against. Their corpora are *deliberately near-identical* to the hero's
-- same ISP name, same amounts, same dates. If the vector-index prefix or a
tenant foreign key is ever wrong, those rows leak and the test fails loudly
instead of passing silently on an empty database.

The persona
-----------
"Dana Whitfield" is retired and must not reappear in any example
(``CANONICAL_DECISIONS.md``). ``test_seed_canon.py`` greps this package for the
string, so the retirement is enforced rather than remembered.
"""

from __future__ import annotations

from scripts.seed.ids import sid
from scripts.seed.rows import SeedTenant, SeedUser

__all__ = ["HERO_TENANT", "HERO_USER", "TENANTS", "USERS", "tenant_of", "user_of"]

HERO_TENANT = SeedTenant(id=sid("tenant", "hero"), name="Provenance Demo", slug="hero")
ISO_A_TENANT = SeedTenant(id=sid("tenant", "iso-a"), name="Isolation Tenant A", slug="iso-a")
ISO_B_TENANT = SeedTenant(id=sid("tenant", "iso-b"), name="Isolation Tenant B", slug="iso-b")

TENANTS: tuple[SeedTenant, ...] = (HERO_TENANT, ISO_A_TENANT, ISO_B_TENANT)

HERO_USER = SeedUser(
    id=sid("user", "hero"),
    tenant_id=HERO_TENANT.id,
    slug="hero",
    cognito_sub="seed-hero-alex-rivera",
    email="alex.rivera@example.invalid",
    display_name="Alex Rivera",
    timezone="America/New_York",
    home_region="us-east-1",
    judge_mode_enabled=True,
)

ISO_A_USER = SeedUser(
    id=sid("user", "iso-a"),
    tenant_id=ISO_A_TENANT.id,
    slug="iso-a",
    cognito_sub="seed-iso-a",
    email="iso-a@example.invalid",
    display_name="Isolation A",
    timezone="UTC",
    home_region="us-east-1",
    judge_mode_enabled=False,
)

ISO_B_USER = SeedUser(
    id=sid("user", "iso-b"),
    tenant_id=ISO_B_TENANT.id,
    slug="iso-b",
    cognito_sub="seed-iso-b",
    email="iso-b@example.invalid",
    display_name="Isolation B",
    timezone="UTC",
    home_region="us-east-1",
    judge_mode_enabled=False,
)

USERS: tuple[SeedUser, ...] = (HERO_USER, ISO_A_USER, ISO_B_USER)

_TENANTS_BY_SLUG = {t.slug: t for t in TENANTS}
_USERS_BY_SLUG = {u.slug: u for u in USERS}


def tenant_of(slug: str) -> SeedTenant:
    return _TENANTS_BY_SLUG[slug]


def user_of(slug: str) -> SeedUser:
    return _USERS_BY_SLUG[slug]
