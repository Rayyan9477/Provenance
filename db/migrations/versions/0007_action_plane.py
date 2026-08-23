"""0007 — action plane: nothing leaves the system without a recorded approval.

Revision ID: 0007_action_plane
Revises: 0006_prospective_memory

Creates ``action_intents`` (a draft, its grounding and its approval) and
``action_executions`` (every attempt to send it) from
``specs/10_DATABASE_DDL.md`` section 10.

Invariant 4 is a CHECK, not a code path
----------------------------------------
``ck_action_intents_execution_needs_approval`` refuses any row in ``EXECUTING``,
``EXECUTED``, ``FAILED_RETRYABLE`` or ``FAILED_FINAL`` that carries no
``approval_draft_sha256``. An unapproved execution is not *prevented* by this
schema - it is unrepresentable in it. That distinction is the whole difference
between a guarantee and a convention: a convention is one refactor away from
being untrue, and nothing about a refactor makes a CHECK stop firing.

``ck_action_intents_approval_complete`` is its partner: approval freezes a hash,
an approver and a timestamp, all three or none. Two out of three is the shape a
half-finished approval flow would leave behind.

What the schema deliberately cannot express
--------------------------------------------
**Approval staleness.** "The case has not moved since approval" is a cross-row
predicate against ``cases.revision`` evaluated at execution time; no row-local
CHECK can see it. It is enforced by the executor's revalidation query in DDL
section 13 and asserted by section 19 test 7. ``basis_case_revision`` is stored
here so that query has something to compare against, and
``idx_action_intents_case_status`` is what makes the join cheap.

``uq_action_executions_single_success`` — many attempts, at most one send
-------------------------------------------------------------------------
Three shapes were available and only one is correct:

- no unique at all: two executor instances race and the recipient gets two
  emails;
- ``UNIQUE (action_intent_id)``: a retry after a genuinely retryable failure is
  refused, so a transient SES error becomes permanent;
- ``UNIQUE (action_intent_id) WHERE status = 'SUCCEEDED'``: attempts are free,
  success is once.

Idempotency is therefore a schema guarantee first and an application guarantee
second, which is what DDL section 19 test 7 leans on.

``idempotency_key`` is globally unique, not per-user
-----------------------------------------------------
Keys are minted as ``sha256(tenant || user || case || action_type ||
draft_sha256)`` rendered hex, so global uniqueness is free and a key stolen from
another tenant cannot collide into a send.

Tier 4 is blocked at the database
----------------------------------
``ck_action_intents_tier4_blocked`` refuses ``risk_tier = 4`` outright.
Consequential or ambiguous actions are never autonomous and are never even
proposed in v1; a schema that could hold one invites a config flag that enables
it.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``.
- ``created_by_agent_run_id`` carries **no** foreign key: ``agent_runs`` is
  created in 0008. It is a trace pointer, not an integrity constraint, and
  adding the FK would drag the entire event plane forward into 0001.

Downgrade
---------
Implemented, in reverse creation order, and for **local iteration only**. From
Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0007_action_plane"
down_revision = "0006_prospective_memory"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 10, in the order that section prints them.
# ---------------------------------------------------------------------------

ACTION_INTENTS = """
CREATE TABLE action_intents (
    id                         UUID        NOT NULL PRIMARY KEY,
    tenant_id                  UUID        NOT NULL,
    user_id                    UUID        NOT NULL,
    case_id                    UUID        NOT NULL,
    action_type                STRING      NOT NULL,
    recipient                  STRING      NULL,
    draft_payload              JSONB       NOT NULL,
    draft_sha256               BYTES       NOT NULL,
    rationale                  STRING      NOT NULL,
    supporting_belief_versions JSONB       NOT NULL,
    basis_case_revision        INT8        NOT NULL,
    status                     STRING      NOT NULL DEFAULT 'PROPOSED',
    risk_tier                  INT8        NOT NULL DEFAULT 3,
    created_by_agent_run_id    UUID        NULL,
    approved_by_user_id        UUID        NULL,
    approved_at                TIMESTAMPTZ NULL,
    approval_draft_sha256      BYTES       NULL,
    rejected_at                TIMESTAMPTZ NULL,
    rejection_reason           STRING      NULL,
    idempotency_key            STRING      NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_action_intents_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_action_intents_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_action_intents_status CHECK (status IN (
        'PROPOSED', 'NEEDS_REVIEW', 'APPROVED', 'REJECTED', 'EXECUTING',
        'EXECUTED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCELLED', 'CANCELLED_STALE'
    )),
    CONSTRAINT ck_action_intents_type CHECK (action_type IN (
        'OUTBOUND_EMAIL_DISPUTE', 'OUTBOUND_EMAIL_FOLLOW_UP',
        'OUTBOUND_EMAIL_CANCELLATION_PROOF', 'OUTBOUND_EMAIL_DEPOSIT_DEMAND',
        'INTERNAL_REMINDER'
    )),
    CONSTRAINT ck_action_intents_risk_tier CHECK (risk_tier BETWEEN 0 AND 4),
    -- Tier 4 (consequential/ambiguous) is never autonomous and never even proposed in v1.
    CONSTRAINT ck_action_intents_tier4_blocked CHECK (risk_tier < 4),
    CONSTRAINT ck_action_intents_draft_sha  CHECK (length(draft_sha256) = 32),
    CONSTRAINT ck_action_intents_approval_sha CHECK (
        approval_draft_sha256 IS NULL OR length(approval_draft_sha256) = 32
    ),
    CONSTRAINT ck_action_intents_revision CHECK (basis_case_revision >= 0),
    CONSTRAINT ck_action_intents_supporting_array CHECK (
        jsonb_typeof(supporting_belief_versions) = 'array'
    ),
    -- Grounding gate: an outbound action must cite at least one canonical belief version.
    CONSTRAINT ck_action_intents_grounded CHECK (
        jsonb_array_length(supporting_belief_versions) >= 1
    ),
    -- Approval freezes an exact draft hash, an approver and a timestamp -- all three or none.
    CONSTRAINT ck_action_intents_approval_complete CHECK (
        (approved_at IS NULL AND approved_by_user_id IS NULL AND approval_draft_sha256 IS NULL)
        OR (approved_at IS NOT NULL AND approved_by_user_id IS NOT NULL
            AND approval_draft_sha256 IS NOT NULL)
    ),
    -- Nothing may reach an execution state without a recorded approval.
    CONSTRAINT ck_action_intents_execution_needs_approval CHECK (
        status NOT IN ('EXECUTING', 'EXECUTED', 'FAILED_RETRYABLE', 'FAILED_FINAL')
        OR approval_draft_sha256 IS NOT NULL
    ),
    CONSTRAINT ck_action_intents_rejection CHECK (
        status <> 'REJECTED' OR rejected_at IS NOT NULL
    ),
    -- An outbound message must have somewhere to go.
    CONSTRAINT ck_action_intents_recipient CHECK (
        action_type = 'INTERNAL_REMINDER' OR recipient IS NOT NULL
    ),
    CONSTRAINT fk_action_intents_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_action_intents_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_action_intents_approver
        FOREIGN KEY (tenant_id, approved_by_user_id)
        REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

ACTION_EXECUTIONS = """
CREATE TABLE action_executions (
    id                        UUID        NOT NULL PRIMARY KEY,
    tenant_id                 UUID        NOT NULL,
    user_id                   UUID        NOT NULL,
    action_intent_id          UUID        NOT NULL,
    attempt_no                INT8        NOT NULL,
    provider                  STRING      NOT NULL,
    provider_correlation_id   STRING      NULL,
    request_sha256            BYTES       NOT NULL,
    revalidated_case_revision INT8        NOT NULL,
    status                    STRING      NOT NULL,
    error_code                STRING      NULL,
    started_at                TIMESTAMPTZ NOT NULL,
    finished_at               TIMESTAMPTZ NULL,

    CONSTRAINT uq_action_executions_attempt UNIQUE (action_intent_id, attempt_no),
    CONSTRAINT ck_action_executions_attempt_no CHECK (attempt_no >= 1 AND attempt_no <= 5),
    CONSTRAINT ck_action_executions_provider CHECK (provider IN ('SES', 'SAFE_SINK', 'SIMULATOR')),
    CONSTRAINT ck_action_executions_status CHECK (status IN (
        'STARTED', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'ABORTED_STALE'
    )),
    CONSTRAINT ck_action_executions_request_sha CHECK (length(request_sha256) = 32),
    CONSTRAINT ck_action_executions_revision CHECK (revalidated_case_revision >= 0),
    CONSTRAINT ck_action_executions_terminal CHECK (
        status = 'STARTED' OR finished_at IS NOT NULL
    ),
    CONSTRAINT ck_action_executions_error CHECK (
        status NOT IN ('FAILED_RETRYABLE', 'FAILED_FINAL', 'ABORTED_STALE')
        OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_action_executions_success_has_correlation CHECK (
        status <> 'SUCCEEDED' OR provider_correlation_id IS NOT NULL
    ),
    CONSTRAINT fk_action_executions_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_action_executions_intent
        FOREIGN KEY (tenant_id, user_id, action_intent_id)
        REFERENCES action_intents (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (ACTION_INTENTS, ACTION_EXECUTIONS)


# ---------------------------------------------------------------------------
# Indexes — DDL section 10, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # GET /v1/action-intents?status=... and action_intents_pending_count --
    # SELECT * FROM action_intents WHERE tenant_id=$1 AND user_id=$2
    #   AND status IN ('PROPOSED','NEEDS_REVIEW') ORDER BY created_at DESC.
    """
    CREATE INDEX idx_action_intents_user_status
        ON action_intents (tenant_id, user_id, status, created_at DESC)
        STORING (case_id, action_type, recipient, basis_case_revision, risk_tier, draft_sha256)
    """,
    # the staleness check in 02 section 18 -- SELECT ai.*, c.revision
    # FROM action_intents ai JOIN cases c ON ...
    # WHERE ai.case_id = $1 AND ai.status = 'APPROVED'.
    """
    CREATE INDEX idx_action_intents_case_status
        ON action_intents (tenant_id, user_id, case_id, status)
    """,
    # the executor claim -- SELECT * FROM action_intents
    # WHERE status='APPROVED' ORDER BY approved_at LIMIT 10.
    """
    CREATE INDEX idx_action_intents_approved_queue
        ON action_intents (approved_at)
        WHERE status = 'APPROVED'
    """,
    # "has this intent already succeeded?" before any provider call --
    # SELECT 1 FROM action_executions WHERE action_intent_id=$1 AND status='SUCCEEDED'.
    # The partial UNIQUE guarantees at most one successful send per intent even
    # if two executor instances race. The hard stop behind section 19 test 7.
    """
    CREATE UNIQUE INDEX uq_action_executions_single_success
        ON action_executions (action_intent_id)
        WHERE status = 'SUCCEEDED'
    """,
    # reconciliation after an outbound timeout --
    # SELECT * FROM action_executions WHERE provider_correlation_id = $1.
    """
    CREATE INDEX idx_action_executions_correlation
        ON action_executions (provider, provider_correlation_id)
        WHERE provider_correlation_id IS NOT NULL
    """,
    # case timeline -- SELECT * FROM action_executions
    # WHERE tenant_id=$1 AND user_id=$2 ORDER BY started_at DESC LIMIT 50.
    """
    CREATE INDEX idx_action_executions_user_time
        ON action_executions (tenant_id, user_id, started_at DESC)
    """,
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = ("action_executions", "action_intents")


def upgrade() -> None:
    """Create the action plane. Two tables, six indexes."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything 0007 created, in reverse order. Local iteration only."""
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
