"""0001 — identity and aggregates.

Revision ID: 0001_identity_aggregates
Revises: (none — this is the root of the chain)

Creates the seven tables of ``specs/10_DATABASE_DDL.md`` section 3, in the order
that section prints them: ``tenants``, ``users``, ``ingest_aliases``,
``counterparties``, ``relationships``, ``contexts``, ``cases``.

The tenancy spine
-----------------
``users`` declares ``UNIQUE (tenant_id, id)``, and every user-owned table below
carries ``FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)``.
Child rows in the evidence, epistemic and obligation chains then reference their
parents **composite-ly** on ``(tenant_id, user_id, id)``, which is why most
tables also declare ``UNIQUE (tenant_id, user_id, id)``.

That composite key is the point. It makes cross-user stitching impossible at the
storage layer rather than merely unlikely at the application layer: DDL section
19 test 11 bypasses the Memory Kernel entirely and attempts the raw ``INSERT``,
and the foreign key has to be what refuses it. It costs an extra index per table
and an extra FK check per insert; at this system's scale that cost is invisible and
the guarantee is not.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here. CockroachDB rejects a
  schema change that follows a data write in the same transaction, and the seed
  (``scripts/seed/``, task ``T2.8``) is a separate program for exactly that
  reason.
- **Literal SQL through** ``op.execute()``. SQLAlchemy's CockroachDB dialect
  emits neither ``STORING`` nor partial indexes, both of which section 3 uses on
  nearly every index.
- **No** ``DEFAULT gen_random_uuid()`` **anywhere.** Ids are generated
  application-side so the Memory Kernel knows every id — including
  ``kernel_decision_id`` and the outbox ``event_id`` — before the transaction
  opens, and can build the whole write plan without a round trip.

Downgrade
---------
``downgrade()`` is implemented and drops in reverse creation order, but it is
for **local iteration only**. From Phase 13 onward the deployment rule is that
schema rolls forward and code rolls back (``quality/23_PHASE_GATES.md`` section
5, forward-only rule). Nobody should be reading that sentence for the first time
during an incident, which is why it is here rather than only in the runbook.
"""

from __future__ import annotations

from alembic import op

revision = "0001_identity_aggregates"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 3, in the order that section prints them.
# ---------------------------------------------------------------------------

TENANTS = """
CREATE TABLE tenants (
    id          UUID        NOT NULL PRIMARY KEY,
    name        STRING      NOT NULL,
    slug        STRING      NOT NULL,
    status      STRING      NOT NULL DEFAULT 'ACTIVE',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_tenants_slug UNIQUE (slug),
    CONSTRAINT ck_tenants_status CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')),
    CONSTRAINT ck_tenants_slug_shape CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')
)
"""

USERS = """
CREATE TABLE users (
    id                 UUID        NOT NULL PRIMARY KEY,
    tenant_id          UUID        NOT NULL,
    cognito_sub        STRING      NOT NULL,
    email              STRING      NULL,
    display_name       STRING      NULL,
    timezone           STRING      NOT NULL DEFAULT 'UTC',
    home_region        STRING      NULL,
    app_role           STRING      NOT NULL DEFAULT 'USER',
    judge_mode_enabled BOOL        NOT NULL DEFAULT false,
    status             STRING      NOT NULL DEFAULT 'ACTIVE',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_users_tenant_id     UNIQUE (tenant_id, id),
    CONSTRAINT uq_users_cognito_sub   UNIQUE (cognito_sub),
    CONSTRAINT ck_users_app_role      CHECK (app_role IN ('USER', 'DEMO_JUDGE', 'ADMIN')),
    CONSTRAINT ck_users_status        CHECK (status IN ('ACTIVE', 'DISABLED')),
    CONSTRAINT ck_users_email_shape   CHECK (email IS NULL OR email LIKE '%@%'),
    CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
"""

INGEST_ALIASES = """
CREATE TABLE ingest_aliases (
    id          UUID        NOT NULL PRIMARY KEY,
    tenant_id   UUID        NOT NULL,
    user_id     UUID        NOT NULL,
    alias_hash  BYTES       NOT NULL,
    alias_label STRING      NULL,
    status      STRING      NOT NULL DEFAULT 'ACTIVE',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ NULL,

    CONSTRAINT uq_ingest_aliases_hash UNIQUE (alias_hash),
    CONSTRAINT ck_ingest_aliases_status     CHECK (status IN ('ACTIVE', 'DISABLED')),
    CONSTRAINT ck_ingest_aliases_hash_len   CHECK (length(alias_hash) = 32),
    CONSTRAINT fk_ingest_aliases_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

COUNTERPARTIES = """
CREATE TABLE counterparties (
    id               UUID        NOT NULL PRIMARY KEY,
    tenant_id        UUID        NOT NULL,
    normalized_name  STRING      NOT NULL,
    display_name     STRING      NOT NULL,
    kind             STRING      NOT NULL,
    canonical_domain STRING      NULL,
    known_domains    JSONB       NULL,
    metadata         JSONB       NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_counterparties_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_counterparties_identity  UNIQUE (tenant_id, normalized_name, kind),
    CONSTRAINT ck_counterparties_kind CHECK (kind IN (
        'ISP', 'LANDLORD', 'MOVING_COMPANY', 'EMPLOYER', 'BANK', 'RETAILER',
        'AIRLINE', 'UTILITY', 'INSURER', 'HEALTHCARE_PROVIDER', 'GOVERNMENT',
        'TELECOM', 'OTHER'
    )),
    CONSTRAINT ck_counterparties_normalized CHECK (normalized_name = lower(normalized_name)),
    CONSTRAINT fk_counterparties_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
"""

RELATIONSHIPS = """
CREATE TABLE relationships (
    id                     UUID        NOT NULL PRIMARY KEY,
    tenant_id              UUID        NOT NULL,
    user_id                UUID        NOT NULL,
    counterparty_id        UUID        NOT NULL,
    relationship_type      STRING      NOT NULL,
    label                  STRING      NULL,
    external_account_ref   STRING      NULL,
    normalized_identifiers JSONB       NULL,
    status                 STRING      NOT NULL DEFAULT 'ACTIVE',
    valid_from             TIMESTAMPTZ NULL,
    valid_to               TIMESTAMPTZ NULL,
    revision               INT8        NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_relationships_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_relationships_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'CLOSED')),
    CONSTRAINT ck_relationships_type CHECK (relationship_type IN (
        'SERVICE_ACCOUNT', 'TENANCY', 'EMPLOYMENT', 'VENDOR_ENGAGEMENT',
        'FINANCIAL_ACCOUNT', 'LOYALTY', 'INSURANCE_POLICY', 'OTHER'
    )),
    CONSTRAINT ck_relationships_revision  CHECK (revision >= 0),
    CONSTRAINT ck_relationships_validity
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    CONSTRAINT fk_relationships_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_relationships_counterparty
        FOREIGN KEY (tenant_id, counterparty_id)
        REFERENCES counterparties (tenant_id, id) ON DELETE RESTRICT
)
"""

CONTEXTS = """
CREATE TABLE contexts (
    id           UUID        NOT NULL PRIMARY KEY,
    tenant_id    UUID        NOT NULL,
    user_id      UUID        NOT NULL,
    title        STRING      NOT NULL,
    context_type STRING      NOT NULL,
    status       STRING      NOT NULL DEFAULT 'ACTIVE',
    started_at   TIMESTAMPTZ NULL,
    ended_at     TIMESTAMPTZ NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_contexts_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_contexts_status CHECK (status IN ('ACTIVE', 'DORMANT', 'CLOSED')),
    CONSTRAINT ck_contexts_type CHECK (context_type IN (
        'MOVE', 'TRAVEL', 'PURCHASE', 'EMPLOYMENT_CHANGE', 'HEALTHCARE_EPISODE',
        'FINANCIAL_EVENT', 'LEGAL_MATTER', 'OTHER'
    )),
    CONSTRAINT fk_contexts_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

# `cases` is the primary consistency aggregate. `revision` is the
# optimistic-concurrency token that action approval (`basis_case_revision`),
# trigger evaluation and the outbox `aggregate_version` all key off, so it is
# NOT NULL with a CHECK rather than a nullable counter.
CASES = """
CREATE TABLE cases (
    id               UUID        NOT NULL PRIMARY KEY,
    tenant_id        UUID        NOT NULL,
    user_id          UUID        NOT NULL,
    relationship_id  UUID        NOT NULL,
    context_id       UUID        NULL,
    case_type        STRING      NOT NULL,
    title            STRING      NOT NULL,
    status           STRING      NOT NULL,
    revision         INT8        NOT NULL DEFAULT 0,
    opened_at        TIMESTAMPTZ NOT NULL,
    resolved_at      TIMESTAMPTZ NULL,
    last_activity_at TIMESTAMPTZ NOT NULL,
    reopened_count   INT8        NOT NULL DEFAULT 0,
    attention_level  STRING      NOT NULL DEFAULT 'NONE',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_cases_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_cases_status CHECK (status IN (
        'OPEN', 'WAITING', 'ACTIONABLE', 'IN_PROGRESS', 'DISPUTED', 'BLOCKED',
        'AWAITING_USER', 'RESOLVED', 'REOPENED', 'SUPERSEDED'
    )),
    CONSTRAINT ck_cases_type CHECK (case_type IN (
        'SERVICE_CANCELLATION', 'DEPOSIT_RETURN', 'DAMAGE_REIMBURSEMENT',
        'EXPENSE_REIMBURSEMENT', 'BILLING_DISPUTE', 'WARRANTY_CLAIM', 'REFUND',
        'ACCOUNT_CLOSURE', 'SERVICE_INSTALLATION', 'GENERAL'
    )),
    CONSTRAINT ck_cases_attention CHECK (attention_level IN ('NONE', 'INFO', 'ATTENTION', 'URGENT')),
    CONSTRAINT ck_cases_revision       CHECK (revision >= 0),
    CONSTRAINT ck_cases_reopened_count  CHECK (reopened_count >= 0),
    CONSTRAINT ck_cases_resolved_at_consistent CHECK (
        (status = 'RESOLVED' AND resolved_at IS NOT NULL)
        OR (status <> 'RESOLVED')
    ),
    CONSTRAINT ck_cases_reopen_implies_history CHECK (
        reopened_count = 0 OR status IN ('REOPENED', 'WAITING', 'ACTIONABLE', 'IN_PROGRESS',
                                          'DISPUTED', 'BLOCKED', 'AWAITING_USER', 'RESOLVED',
                                          'SUPERSEDED')
    ),
    CONSTRAINT fk_cases_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_cases_relationship
        FOREIGN KEY (tenant_id, user_id, relationship_id)
        REFERENCES relationships (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_cases_context
        FOREIGN KEY (tenant_id, user_id, context_id)
        REFERENCES contexts (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (
    TENANTS,
    USERS,
    INGEST_ALIASES,
    COUNTERPARTIES,
    RELATIONSHIPS,
    CONTEXTS,
    CASES,
)


# ---------------------------------------------------------------------------
# Indexes. Every one of these serves a named query; the comment above each is
# that query, copied from DDL section 3 so the two cannot drift silently.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # SELECT id, tenant_id, app_role FROM users WHERE cognito_sub = $1
    # (cognito_sub -> Principal resolution on every authenticated request).
    """
    CREATE INDEX idx_users_cognito_lookup
        ON users (cognito_sub)
        STORING (tenant_id, app_role, judge_mode_enabled, timezone, status)
    """,
    # SELECT tenant_id, user_id FROM ingest_aliases
    # WHERE alias_hash = $1 AND status = 'ACTIVE'
    # (SES inbound Lambda resolving a forwarded-mail alias to its owner).
    """
    CREATE INDEX idx_ingest_aliases_active_lookup
        ON ingest_aliases (alias_hash)
        STORING (tenant_id, user_id, status)
        WHERE status = 'ACTIVE'
    """,
    # SELECT * FROM ingest_aliases WHERE tenant_id = $1 AND user_id = $2
    # (settings screen listing a user's aliases).
    """
    CREATE INDEX idx_ingest_aliases_by_user
        ON ingest_aliases (tenant_id, user_id, created_at DESC)
    """,
    # SELECT id FROM counterparties WHERE tenant_id = $1 AND canonical_domain = $2
    # (retrieval step 1 - deterministic identity hint from the sender domain,
    #  before any vector search runs).
    """
    CREATE INDEX idx_counterparties_domain
        ON counterparties (tenant_id, canonical_domain)
        STORING (normalized_name, display_name, kind)
        WHERE canonical_domain IS NOT NULL
    """,
    # SELECT * FROM relationships
    # WHERE tenant_id=$1 AND user_id=$2 AND counterparty_id=$3 AND status='ACTIVE'
    # (the Northline-Fiber-old-account vs Northline-Fiber-new-address
    #  disambiguation - the sharpest decoy in the hero corpus).
    """
    CREATE INDEX idx_relationships_user_counterparty_status
        ON relationships (tenant_id, user_id, counterparty_id, status)
        STORING (label, external_account_ref, relationship_type, valid_from, valid_to)
    """,
    # SELECT id FROM relationships
    # WHERE tenant_id=$1 AND user_id=$2 AND external_account_ref=$3
    # (exact-identifier lookup: the highest-precision identity signal there is.
    #  Partial because most relationships have no account number.)
    """
    CREATE INDEX idx_relationships_external_ref
        ON relationships (tenant_id, user_id, external_account_ref)
        WHERE external_account_ref IS NOT NULL
    """,
    # SELECT * FROM contexts WHERE tenant_id=$1 AND user_id=$2 AND status='ACTIVE'
    # (dashboard: render "THE MOVE" as the top-level grouping).
    """
    CREATE INDEX idx_contexts_user_status
        ON contexts (tenant_id, user_id, status, created_at DESC)
    """,
    # GET /v1/dashboard - SELECT ... FROM cases
    # WHERE tenant_id=$1 AND user_id=$2 AND status = ANY($3)
    # ORDER BY last_activity_at DESC LIMIT 50.
    # STORING makes it index-only: no PK round trip for the dashboard read model.
    """
    CREATE INDEX idx_cases_user_status_activity
        ON cases (tenant_id, user_id, status, last_activity_at DESC)
        STORING (title, case_type, attention_level, revision, relationship_id, context_id,
                 resolved_at, reopened_count)
    """,
    # SELECT * FROM cases WHERE relationship_id = $1 AND status = $2
    # (relationship detail screen, and the Kernel's identity gate asking "does
    #  this relationship already have an open/resolved case of this type?").
    """
    CREATE INDEX idx_cases_relationship_status
        ON cases (tenant_id, user_id, relationship_id, status)
    """,
    # SELECT * FROM cases WHERE context_id = $1 AND status <> 'SUPERSEDED'
    # (the "THE MOVE - 4 relationships" panel).
    """
    CREATE INDEX idx_cases_context_status
        ON cases (tenant_id, user_id, context_id, status)
        WHERE context_id IS NOT NULL
    """,
    # SELECT * FROM cases
    # WHERE tenant_id=$1 AND user_id=$2 AND attention_level <> 'NONE'
    # ORDER BY last_activity_at DESC   (the attention_only=true dashboard filter).
    """
    CREATE INDEX idx_cases_attention
        ON cases (tenant_id, user_id, attention_level, last_activity_at DESC)
        WHERE attention_level <> 'NONE'
    """,
)

#: Reverse creation order. Foreign keys are ``ON DELETE RESTRICT`` on the
#: append-only chain, so the order is not optional even with ``CASCADE``.
DROP_ORDER: tuple[str, ...] = (
    "cases",
    "contexts",
    "relationships",
    "counterparties",
    "ingest_aliases",
    "users",
    "tenants",
)


def upgrade() -> None:
    """Create the identity and aggregate plane. Seven tables, eleven indexes."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything 0001 created, in reverse order. Local iteration only."""
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
