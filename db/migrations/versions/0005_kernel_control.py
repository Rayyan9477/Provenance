"""0005 — Kernel control plane, and the two foreign keys 0003 could not create.

Revision ID: 0005_kernel_control
Revises: 0004_obligation_ledger

Creates ``memory_proposals`` (what an agent asked for) and ``kernel_decisions``
(what the deterministic Kernel did about it), from ``specs/10_DATABASE_DDL.md``
section 8, then closes the deferred foreign keys from section 8.3.

Why the deferred foreign keys land here
---------------------------------------
``belief_versions.kernel_decision_id`` and ``state_transitions.kernel_decision_id``
are both ``NOT NULL``, but ``kernel_decisions.proposal_id`` needs
``memory_proposals``, which references nothing in 0003. Declaring the columns in
0003 and their constraints here breaks that knot without a nullable column and
without a circular revision graph.

The constraint is also what fixes statement order inside the Kernel transaction
(DDL section 13): the Kernel re-reads and recomputes the aggregate first, then
inserts the **already-final** ``kernel_decisions`` row before any
``belief_versions`` or ``state_transitions`` row that references it. No transient
decision value is ever persisted. If a later statement fails, the whole
transaction - decision row included - rolls back.

``kernel_decisions`` must be writable for every outcome
-------------------------------------------------------
Rejections and NOOPs get rows too. A decision table that only records
acceptances turns every refusal into an unaudited event, and ``G4.5``'s
"duplicate proposal is a NOOP **with a reason**" has nowhere to write the
reason. ``ck_kernel_decisions_noop_no_bump`` is the other half: a NOOP or a
rejection may not advance the case revision, which is the aggregate-revision
invariant of ``02`` section 10 expressed row-locally.

``transaction_opened`` — a column DDL section 8.2 does not print
----------------------------------------------------------------
``G4.4`` asserts ``kernel_decisions.transaction_opened = false`` when foreign
evidence is refused, and ``quality/23_PHASE_GATES.md`` section 12 lists it among
the three columns a decision row must carry (``reason_code``, ``retry_count``,
``transaction_opened``). DDL section 8.2's column list omits it. The gate is the
only document that says what a decision row must contain, and it outranks the
task plan, so the column is created here. The omission is a spec discrepancy,
reported with ``T2.4`` for the Phase 2 gate ledger; this docstring is the record
that travels with the code.

``DEFAULT false`` is chosen deliberately. A row written by some future path that
never thought about the question claims the *weaker* fact - "no transaction was
opened" - rather than silently asserting the stronger one.
``ck_kernel_decisions_commit_needs_transaction`` then makes the incoherent
combination (an ``ACCEPTED`` decision that never opened a transaction)
unrepresentable, which is what stops the column becoming decorative.

Model ids are the ones this account can actually invoke
--------------------------------------------------------
DDL section 8.1 prints ``anthropic.claude-opus-5`` and
``anthropic.claude-haiku-4-5``. ``CANONICAL_DECISIONS.md`` -> *Bedrock model id
canon* supersedes **every** bare ``anthropic.claude-*`` id in the pack: the
undated ``anthropic.claude-haiku-4-5`` does not exist on Bedrock in any form,
and ``us.anthropic.claude-opus-5`` is denied to this account. A CHECK carrying
the superseded strings would reject every proposal the shipped configuration
produces, so the members are the three verified-invocable ids plus
``deterministic.kernel`` for the Kernel's own derivations.

``memory_proposals.kernel_decision_id`` carries no foreign key
---------------------------------------------------------------
``kernel_decisions.proposal_id`` already points the other way and CockroachDB
cannot defer the resulting cycle. Verification query V6 audits it instead.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``.

Downgrade
---------
Implemented, in reverse creation order, and for **local iteration only**. The
two deferred foreign keys are dropped first, because 0004's and 0003's tables
outlive this revision and would otherwise keep dangling constraints. From
Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0005_kernel_control"
down_revision = "0004_obligation_ledger"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 8, in the order that section prints them.
# ---------------------------------------------------------------------------

MEMORY_PROPOSALS = """
CREATE TABLE memory_proposals (
    id                        UUID        NOT NULL PRIMARY KEY,
    tenant_id                 UUID        NOT NULL,
    user_id                   UUID        NOT NULL,
    trace_id                  UUID        NOT NULL,
    agent_run_id              UUID        NULL,
    schema_version            STRING      NOT NULL,
    proposal_type             STRING      NOT NULL,
    source_artifact_ids       JSONB       NOT NULL,
    evidence_ids              JSONB       NOT NULL,
    candidate_relationship_id UUID        NULL,
    candidate_case_id         UUID        NULL,
    payload                   JSONB       NOT NULL,
    payload_sha256            BYTES       NOT NULL,
    model_id                  STRING      NOT NULL,
    prompt_version            STRING      NOT NULL,
    status                    STRING      NOT NULL DEFAULT 'SUBMITTED',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at                TIMESTAMPTZ NULL,
    kernel_decision_id        UUID        NULL,

    CONSTRAINT uq_memory_proposals_tenant_user_id UNIQUE (tenant_id, user_id, id),
    -- Same payload from the same run is the same proposal. Makes an agent retry a
    -- NOOP_DUPLICATE rather than a second commit.
    CONSTRAINT uq_memory_proposals_payload UNIQUE (tenant_id, user_id, payload_sha256),
    CONSTRAINT ck_memory_proposals_status CHECK (status IN (
        'SUBMITTED', 'ACCEPTED', 'ACCEPTED_WITH_CONFLICT', 'NOOP_DUPLICATE',
        'PENDING_IDENTITY', 'PENDING_HUMAN_REVIEW', 'REJECTED_INVALID_PROVENANCE',
        'REJECTED_INVARIANT', 'REJECTED_SCHEMA'
    )),
    CONSTRAINT ck_memory_proposals_type CHECK (proposal_type IN (
        'INGESTION_INTERPRETATION', 'USER_CORRECTION', 'FULFILLMENT_ADMISSION',
        'TRIGGER_EVALUATION', 'SYSTEM_DERIVATION', 'SEED_FIXTURE'
    )),
    -- Canon models only (CANONICAL_DECISIONS.md -> Bedrock model id canon,
    -- which supersedes the bare ids printed in DDL section 8.1). Rejects a
    -- stale model id at the database boundary.
    CONSTRAINT ck_memory_proposals_model CHECK (model_id IN (
        'us.anthropic.claude-haiku-4-5-20251001-v1:0',
        'us.anthropic.claude-opus-4-6-v1',
        'us.anthropic.claude-sonnet-4-6',
        'deterministic.kernel'
    )),
    CONSTRAINT ck_memory_proposals_schema_version CHECK (schema_version ~ '^[0-9]+\\.[0-9]+$'),
    CONSTRAINT ck_memory_proposals_payload_sha CHECK (length(payload_sha256) = 32),
    CONSTRAINT ck_memory_proposals_decided CHECK (
        status = 'SUBMITTED' OR decided_at IS NOT NULL
    ),
    CONSTRAINT ck_memory_proposals_arrays CHECK (
        jsonb_typeof(source_artifact_ids) = 'array' AND jsonb_typeof(evidence_ids) = 'array'
    ),
    CONSTRAINT fk_memory_proposals_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

KERNEL_DECISIONS = """
CREATE TABLE kernel_decisions (
    id                   UUID        NOT NULL PRIMARY KEY,
    tenant_id            UUID        NOT NULL,
    user_id              UUID        NOT NULL,
    proposal_id          UUID        NOT NULL,
    case_id              UUID        NULL,
    decision             STRING      NOT NULL,
    reason_codes         JSONB       NOT NULL,
    case_revision_before INT8        NULL,
    case_revision_after  INT8        NULL,
    retry_count          INT8        NOT NULL DEFAULT 0,
    transaction_opened   BOOL        NOT NULL DEFAULT false,
    trace_id             UUID        NOT NULL,
    committed_at         TIMESTAMPTZ NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_kernel_decisions_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_kernel_decisions_decision CHECK (decision IN (
        'ACCEPTED', 'ACCEPTED_WITH_CONFLICT', 'NOOP_DUPLICATE',
        'PENDING_IDENTITY', 'PENDING_HUMAN_REVIEW', 'REJECTED_INVALID_PROVENANCE',
        'REJECTED_INVARIANT', 'REJECTED_SCHEMA', 'RETRYABLE_CONCURRENCY'
    )),
    CONSTRAINT ck_kernel_decisions_retry CHECK (retry_count >= 0 AND retry_count <= 5),
    CONSTRAINT ck_kernel_decisions_reason_codes CHECK (jsonb_typeof(reason_codes) = 'array'),
    -- The aggregate-revision invariant from 02 section 10: a canonical commit advances
    -- the case revision by exactly one; a no-op leaves it untouched.
    CONSTRAINT ck_kernel_decisions_revision_step CHECK (
        case_revision_before IS NULL
        OR case_revision_after IS NULL
        OR case_revision_after = case_revision_before
        OR case_revision_after = case_revision_before + 1
    ),
    CONSTRAINT ck_kernel_decisions_noop_no_bump CHECK (
        decision NOT IN ('NOOP_DUPLICATE', 'REJECTED_INVALID_PROVENANCE',
                         'REJECTED_INVARIANT', 'REJECTED_SCHEMA')
        OR case_revision_before IS NULL
        OR case_revision_after IS NULL
        OR case_revision_after = case_revision_before
    ),
    CONSTRAINT ck_kernel_decisions_commit_ts CHECK (
        decision NOT IN ('ACCEPTED', 'ACCEPTED_WITH_CONFLICT') OR committed_at IS NOT NULL
    ),
    -- G4.4's column, given teeth: a decision that changed canonical state must
    -- have opened a transaction to do it. Preflight rejections keep the default
    -- false, which is exactly what the gate asserts.
    CONSTRAINT ck_kernel_decisions_commit_needs_transaction CHECK (
        decision NOT IN ('ACCEPTED', 'ACCEPTED_WITH_CONFLICT') OR transaction_opened
    ),
    CONSTRAINT fk_kernel_decisions_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_kernel_decisions_proposal
        FOREIGN KEY (tenant_id, user_id, proposal_id)
        REFERENCES memory_proposals (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_kernel_decisions_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (MEMORY_PROPOSALS, KERNEL_DECISIONS)


# ---------------------------------------------------------------------------
# Indexes — DDL section 8, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # Memory Trace -- SELECT * FROM memory_proposals WHERE trace_id = $1 ORDER BY created_at.
    """
    CREATE INDEX idx_memory_proposals_trace
        ON memory_proposals (trace_id, created_at)
    """,
    # the pending-review queue -- SELECT * FROM memory_proposals
    # WHERE tenant_id=$1 AND user_id=$2
    #   AND status IN ('PENDING_IDENTITY','PENDING_HUMAN_REVIEW') ORDER BY created_at.
    """
    CREATE INDEX idx_memory_proposals_pending
        ON memory_proposals (tenant_id, user_id, created_at)
        WHERE status IN ('PENDING_IDENTITY', 'PENDING_HUMAN_REVIEW')
    """,
    # agent-run attribution in Judge Mode --
    # SELECT * FROM memory_proposals WHERE agent_run_id = $1.
    """
    CREATE INDEX idx_memory_proposals_run
        ON memory_proposals (agent_run_id)
        WHERE agent_run_id IS NOT NULL
    """,
    # Memory Trace and the retry-count metric --
    # SELECT * FROM kernel_decisions WHERE trace_id = $1 ORDER BY created_at.
    """
    CREATE INDEX idx_kernel_decisions_trace
        ON kernel_decisions (trace_id, created_at)
    """,
    # GET /v1/cases/{case_id}/memory-trace -- SELECT * FROM kernel_decisions
    # WHERE tenant_id=$1 AND user_id=$2 AND case_id=$3 ORDER BY created_at DESC LIMIT 25.
    """
    CREATE INDEX idx_kernel_decisions_case
        ON kernel_decisions (tenant_id, user_id, case_id, created_at DESC)
        STORING (decision, reason_codes, case_revision_before, case_revision_after,
                 retry_count, transaction_opened)
        WHERE case_id IS NOT NULL
    """,
    # one decision per proposal in the happy path; the partial unique index
    # permits a re-decision only after a RETRYABLE_CONCURRENCY outcome.
    """
    CREATE UNIQUE INDEX uq_kernel_decisions_terminal_per_proposal
        ON kernel_decisions (proposal_id)
        WHERE decision <> 'RETRYABLE_CONCURRENCY'
    """,
)


# ---------------------------------------------------------------------------
# The deferred foreign keys — DDL section 8.3.
# ---------------------------------------------------------------------------

DEFERRED_FK_DDL: tuple[str, ...] = (
    """
    ALTER TABLE belief_versions
        ADD CONSTRAINT fk_belief_versions_kernel_decision
        FOREIGN KEY (tenant_id, user_id, kernel_decision_id)
        REFERENCES kernel_decisions (tenant_id, user_id, id) ON DELETE RESTRICT
    """,
    """
    ALTER TABLE state_transitions
        ADD CONSTRAINT fk_state_transitions_kernel_decision
        FOREIGN KEY (tenant_id, user_id, kernel_decision_id)
        REFERENCES kernel_decisions (tenant_id, user_id, id) ON DELETE RESTRICT
    """,
)

DEFERRED_FK_DROP: tuple[str, ...] = (
    "ALTER TABLE state_transitions DROP CONSTRAINT IF EXISTS fk_state_transitions_kernel_decision",
    "ALTER TABLE belief_versions DROP CONSTRAINT IF EXISTS fk_belief_versions_kernel_decision",
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = ("kernel_decisions", "memory_proposals")


def upgrade() -> None:
    """Create the control plane, then close the two foreign keys 0003 deferred."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)
    for statement in DEFERRED_FK_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop the deferred constraints first, then the tables. Local iteration only."""
    for statement in DEFERRED_FK_DROP:
        op.execute(statement)
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
