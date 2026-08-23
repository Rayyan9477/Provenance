"""One view of the whole seeded world (``T2.8``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17 in full.

Why this module exists
----------------------
Two consumers need the seed as a *whole* rather than plane by plane: the loader,
which must insert in foreign-key order, and the determinism test, which hashes
every minted id in a second interpreter. Giving both one assembled object keeps
the FK order in one place -- ``10_DATABASE_DDL.md`` section 13 -- instead of
implied by the order of calls in ``__main__``.

The decoy corpus is deliberately **not** materialised here. It is 18,000 rows
and both of its consumers stream it; assembling it into this object would make
importing ``world`` cost a third of a second and 40 MB for callers that only
wanted the hero ids.
"""

from __future__ import annotations

from uuid import UUID

from scripts.seed.cases import CASES, CONTEXTS
from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS
from scripts.seed.evidence import CURATED_ARTIFACTS, CURATED_EVIDENCE
from scripts.seed.obligations import COMMITMENTS, FULFILLMENTS, TRIGGERS
from scripts.seed.retractions import RETRACTION_ARTIFACTS, RETRACTION_FIXTURES
from scripts.seed.tenants import TENANTS, USERS

__all__ = ["all_seeded_ids", "curated_artifacts", "curated_evidence"]


def curated_artifacts() -> tuple[object, ...]:
    """Curated plus retraction artifacts, in insert order."""
    return CURATED_ARTIFACTS + RETRACTION_ARTIFACTS


def curated_evidence() -> tuple[object, ...]:
    """Curated plus retraction evidence, in insert order.

    The retraction fixtures come last because ``isp-wrong-term-date`` is
    superseded *by* a curated item, and step 10's ``UPDATE`` sets
    ``retracted_by_evidence_id`` -- which ``fk_evidence_retracted_by`` will only
    accept once the target row exists.
    """
    return CURATED_EVIDENCE + RETRACTION_FIXTURES


def all_seeded_ids() -> dict[str, UUID]:
    """Every stable id the hero world mints, keyed by ``kind:slug``.

    Excludes the decoy corpus: 18,000 more entries would make the cross-process
    comparison in ``test_seed_determinism.py`` a 40 MB subprocess round trip to
    prove the same property that ``corpus_fingerprint()`` proves in 64 bytes.
    """
    ids: dict[str, UUID] = {}
    for tenant in TENANTS:
        ids[f"tenant:{tenant.slug}"] = tenant.id
    for user in USERS:
        ids[f"user:{user.slug}"] = user.id
    for counterparty in COUNTERPARTIES:
        ids[f"counterparty:{counterparty.slug}"] = counterparty.id
    for relationship in RELATIONSHIPS:
        ids[f"relationship:{relationship.slug}"] = relationship.id
    for context in CONTEXTS:
        ids[f"context:{context.slug}"] = context.id
    for case in CASES:
        ids[f"case:{case.slug}"] = case.id
    for artifact in CURATED_ARTIFACTS + RETRACTION_ARTIFACTS:
        ids[f"artifact:{artifact.slug}"] = artifact.id
    for evidence in CURATED_EVIDENCE + RETRACTION_FIXTURES:
        ids[f"evidence:{evidence.slug}"] = evidence.id
    for commitment in COMMITMENTS:
        ids[f"commitment:{commitment.slug}"] = commitment.id
    for fulfillment in FULFILLMENTS:
        ids[f"fulfillment:{fulfillment.slug}"] = fulfillment.id
    for trigger in TRIGGERS:
        ids[f"trigger:{trigger.slug}"] = trigger.id
    return ids
