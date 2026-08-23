"""The row shapes the seed writes (``T2.8``).

Authority
---------
- ``db/migrations/versions/0001_identity_aggregates.py`` through
  ``0008_events_infrastructure.py`` -- the ground truth for column names,
  nullability and CHECK vocabularies. Every field below was read off the built
  schema, not off prose.
- ``docs/specs/10_DATABASE_DDL.md`` sections 3, 4, 7, 9.

Why frozen dataclasses and not dicts
------------------------------------
A dict typo is a runtime error at insert time, three minutes into an 18,035-row
load, in a batch of 500. A dataclass typo is a ``TypeError`` at import, before a
connection is opened. The seed is the one program in this repository whose
failures are expensive to reproduce, so its data is typed.

Money is ``Decimal`` throughout. ``float`` money is banned repository-wide and
``ck_commitments_outstanding_identity`` would catch the resulting drift at
insert time -- but only after the round trip, which is exactly the expensive
failure this module exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

__all__ = [
    "SeedArtifact",
    "SeedCase",
    "SeedCommitment",
    "SeedContext",
    "SeedCounterparty",
    "SeedEvidence",
    "SeedFulfillment",
    "SeedRelationship",
    "SeedTenant",
    "SeedTrigger",
    "SeedUser",
]


@dataclass(frozen=True, slots=True)
class SeedTenant:
    id: UUID
    name: str
    slug: str
    status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class SeedUser:
    id: UUID
    tenant_id: UUID
    slug: str
    cognito_sub: str
    email: str
    display_name: str
    timezone: str
    home_region: str
    judge_mode_enabled: bool
    app_role: str = "USER"
    status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class SeedCounterparty:
    id: UUID
    tenant_id: UUID
    slug: str
    normalized_name: str
    display_name: str
    kind: str
    canonical_domain: str
    known_domains: list[str]


@dataclass(frozen=True, slots=True)
class SeedRelationship:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    counterparty_id: UUID
    slug: str
    relationship_type: str
    label: str
    external_account_ref: str
    status: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    revision: int = 0


@dataclass(frozen=True, slots=True)
class SeedContext:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    slug: str
    title: str
    context_type: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class SeedCase:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    relationship_id: UUID
    context_id: UUID | None
    slug: str
    case_type: str
    title: str
    status: str
    revision: int
    opened_at: datetime
    resolved_at: datetime | None
    last_activity_at: datetime
    reopened_count: int = 0
    attention_level: str = "NONE"


@dataclass(frozen=True, slots=True)
class SeedArtifact:
    """A ``source_artifacts`` row.

    ``content_sha256`` and ``size_bytes`` are computed from real bytes on disk
    for the curated set (``10_DATABASE_DDL.md`` section 17.5: "so the hashes,
    ``source_locator`` spans, and S3 keys are genuine rather than fabricated"),
    and from the generated text for the synthetic set.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    slug: str
    source_type: str
    s3_bucket: str
    s3_key: str
    content_sha256: bytes
    size_bytes: int
    mime_type: str
    source_message_id: str | None
    sender: str | None
    sender_domain: str | None
    recipient: str | None
    subject: str | None
    received_at: datetime
    event_time: datetime | None
    parser_status: str = "PARSED"
    parser_version: str | None = "seed-1.0.0"
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    """An ``evidence_items`` row, plus the fields the embedding template needs.

    ``counterparty_name``, ``predicate``, ``currency`` and ``amount`` are not
    columns; they are the header inputs to ``13_RETRIEVAL_SPEC.md`` section
    12.1. Carrying them here keeps one object responsible for both the row and
    the vector, which is what stops the two drifting apart.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    artifact_id: UUID
    slug: str
    evidence_type: str
    normalized_text: str
    exact_text: str | None
    source_locator: dict[str, Any] | None
    actor_ref: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime
    extraction_confidence: Decimal
    source_authority: Decimal | None
    counterparty_name: str | None = None
    predicate: str | None = None
    currency: str | None = None
    amount: Decimal | None = None
    has_identifier: bool = False
    case_slug: str | None = None
    retraction_status: str = "ACTIVE"
    retraction_reason_code: str | None = None
    retracted_by_slug: str | None = None
    embed: bool = True


@dataclass(frozen=True, slots=True)
class SeedCommitment:
    """A ``commitments`` row fixture.

    **Not written by this program.** ``commitments`` is a canonical table and
    ``10_DATABASE_DDL.md`` section 12 grants ``INSERT`` on it to
    ``pv_kernel_writer`` alone. These fixtures are the input to step 9, which
    replays them through ``MemoryKernel.commit()`` once Phase 4 exists.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    case_slug: str
    slug: str
    obligor_type: str
    obligor_id: str | None
    beneficiary_type: str
    beneficiary_id: str | None
    commitment_type: str
    description: str
    currency: str | None
    committed_amount: Decimal | None
    fulfilled_amount: Decimal | None
    outstanding_amount: Decimal | None
    due_at: datetime | None
    status: str
    source_claim_slug: str
    revision: int = 0


@dataclass(frozen=True, slots=True)
class SeedFulfillment:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    commitment_slug: str
    evidence_slug: str
    slug: str
    currency: str | None
    amount: Decimal | None
    fulfilled_at: datetime
    admission_status: str
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class SeedTrigger:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    case_slug: str
    slug: str
    trigger_type: str
    predicate_ast: dict[str, Any]
    not_before: datetime | None
    expires_at: datetime | None
    state: str
    basis_case_revision: int
    schedule_name: str | None = None
    fired_at: datetime | None = None
    evaluation_version: int = 0
    tags: list[str] = field(default_factory=list)
