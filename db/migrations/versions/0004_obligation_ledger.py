"""0004 — conflict, obligation and audit ledger.

Revision ID: 0004_obligation_ledger
Revises: 0003_epistemic_plane

Creates the four tables of ``specs/10_DATABASE_DDL.md`` section 7: ``conflicts``
(two sources that cannot both be right), ``commitments`` (what somebody owes),
``fulfillments`` (evidence that some of it arrived) and ``state_transitions``
(the append-only audit of every canonical aggregate change).

The money invariants are CHECK constraints, not Kernel discipline
-----------------------------------------------------------------
Every arithmetic rule from ``02_DATA_MEMORY_TRANSACTIONS.md`` section 12 that
can be written as a row-local predicate is written as one here, so an impossible
aggregate state cannot be committed **even by a Kernel bug**::

    M1  every amount >= 0
    M2  all three amounts, or none                (without it M3/M4 go vacuous)
    M3  fulfilled <= committed                    (over-payment is a conflict,
                                                   never a silent clamp)
    M4  outstanding = committed - fulfilled       (the identity behind
                                                   "$420 promised, $200 paid,
                                                   $220 outstanding")
    M5  outstanding > 0 forbids FULFILLED         (DDL section 19 test 5, G2.7)
    M6  any amount requires a currency
    M7  a partly-paid commitment cannot claim to be untouched
    M8  FULFILLED requires the money to have arrived

M2 is the one that is easy to leave out and expensive to leave out. With any of
the three columns NULL, M3 and M4 are vacuously true and the whole set stops
meaning anything.

``conflicts.status`` carries both ``OPEN`` and ``NEEDS_HUMAN``
--------------------------------------------------------------
``CANONICAL_DECISIONS.md`` -> *Hero commit canon*: the hero conflict is
``NEEDS_HUMAN``; ``OPEN`` is a legal column value that **no disposition rule
emits**. Both belong in the enum and only one belongs in the hero row. Dropping
``OPEN`` as "unused" would also break ``idx_conflicts_case_status`` and the
partial dedupe index, both of which treat the two as the live pair.

``ck_conflicts_side_order`` is what makes the dedupe index real
---------------------------------------------------------------
``uq_conflicts_live_identity`` is partial over the live statuses, so a genuinely
new contradiction between the same two sources can be raised again after the
previous one was resolved. But an index on ``(left_source_id, right_source_id)``
is trivially defeated by swapping the arguments, so the Kernel normalises the
sides (left = lexicographically smaller UUID) and this CHECK is what makes that
normalisation non-optional. DDL section 19 test 3 asserts "exactly one conflicts
row"; without the CHECK that assertion is false for a reason no reader guesses.

``state_transitions`` has no ``UNIQUE (case_id, case_revision, ...)``
---------------------------------------------------------------------
``02`` section 4.17 lists one. Including the primary key in that tuple makes it
trivially satisfiable, so it is expressed here as ``idx_state_transitions_case_revision``
instead: same access path, no false sense of enforcement. The real guarantee -
one revision per canonical commit - is the aggregate-revision invariant, checked
by verification query V5.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``: SQLAlchemy's CockroachDB dialect emits
  neither ``STORING`` nor partial indexes.

Downgrade
---------
Implemented, in reverse creation order, and for **local iteration only**. From
Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0004_obligation_ledger"
down_revision = "0003_epistemic_plane"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 7, in the order that section prints them.
# ---------------------------------------------------------------------------

CONFLICTS = """
CREATE TABLE conflicts (
    id                          UUID        NOT NULL PRIMARY KEY,
    tenant_id                   UUID        NOT NULL,
    user_id                     UUID        NOT NULL,
    case_id                     UUID        NOT NULL,
    subject_type                STRING      NOT NULL,
    subject_id                  UUID        NOT NULL,
    predicate                   STRING      NOT NULL,
    left_source_kind            STRING      NOT NULL,
    left_source_id              UUID        NOT NULL,
    right_source_kind           STRING      NOT NULL,
    right_source_id             UUID        NOT NULL,
    conflict_type               STRING      NOT NULL,
    status                      STRING      NOT NULL DEFAULT 'OPEN',
    severity                    STRING      NOT NULL,
    requires_human              BOOL        NOT NULL DEFAULT false,
    canonical_belief_version_id UUID        NULL,
    resolution_reason_code      STRING      NULL,
    resolution_notes            STRING      NULL,
    detected_at                 TIMESTAMPTZ NOT NULL,
    resolved_at                 TIMESTAMPTZ NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_conflicts_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_conflicts_status CHECK (status IN (
        'OPEN', 'AUTO_RESOLVED', 'NEEDS_HUMAN', 'RESOLVED', 'SUPERSEDED'
    )),
    CONSTRAINT ck_conflicts_type CHECK (conflict_type IN (
        'VALUE_CONFLICT', 'TEMPORAL_CONFLICT', 'AUTHORITY_CONFLICT',
        'IDENTITY_CONFLICT', 'COMMITMENT_WITHDRAWAL_CONFLICT',
        'FULFILLMENT_CONFLICT', 'POLICY_VERSION_CONFLICT'
    )),
    CONSTRAINT ck_conflicts_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT ck_conflicts_source_kinds CHECK (
        left_source_kind IN ('EVIDENCE', 'CLAIM', 'BELIEF_VERSION', 'COMMITMENT')
        AND right_source_kind IN ('EVIDENCE', 'CLAIM', 'BELIEF_VERSION', 'COMMITMENT')
    ),
    CONSTRAINT ck_conflicts_distinct_sides CHECK (
        left_source_id <> right_source_id OR left_source_kind <> right_source_kind
    ),
    CONSTRAINT ck_conflicts_side_order CHECK (left_source_id <= right_source_id),
    CONSTRAINT ck_conflicts_terminal_needs_resolution CHECK (
        status IN ('OPEN', 'NEEDS_HUMAN')
        OR (resolved_at IS NOT NULL AND resolution_reason_code IS NOT NULL)
    ),
    CONSTRAINT ck_conflicts_open_has_no_resolution CHECK (
        status NOT IN ('OPEN', 'NEEDS_HUMAN') OR resolved_at IS NULL
    ),
    CONSTRAINT ck_conflicts_requires_human_consistent CHECK (
        NOT requires_human OR status <> 'AUTO_RESOLVED'
    ),
    CONSTRAINT ck_conflicts_predicate_shape CHECK (predicate ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT fk_conflicts_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_conflicts_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_conflicts_canonical_version
        FOREIGN KEY (tenant_id, user_id, canonical_belief_version_id)
        REFERENCES belief_versions (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

COMMITMENTS = """
CREATE TABLE commitments (
    id                 UUID           NOT NULL PRIMARY KEY,
    tenant_id          UUID           NOT NULL,
    user_id            UUID           NOT NULL,
    case_id            UUID           NOT NULL,
    obligor_type       STRING         NOT NULL,
    obligor_id         STRING         NULL,
    beneficiary_type   STRING         NOT NULL,
    beneficiary_id     STRING         NULL,
    commitment_type    STRING         NOT NULL,
    description        STRING         NOT NULL,
    currency           STRING         NULL,
    committed_amount   DECIMAL(20,4)  NULL,
    fulfilled_amount   DECIMAL(20,4)  NULL,
    outstanding_amount DECIMAL(20,4)  NULL,
    due_at             TIMESTAMPTZ    NULL,
    condition_ast      JSONB          NULL,
    source_claim_id    UUID           NOT NULL,
    status             STRING         NOT NULL,
    revision           INT8           NOT NULL DEFAULT 0,
    valid_from         TIMESTAMPTZ    NULL,
    valid_to           TIMESTAMPTZ    NULL,
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT uq_commitments_tenant_user_id UNIQUE (tenant_id, user_id, id),

    CONSTRAINT ck_commitments_status CHECK (status IN (
        'PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED', 'FULFILLED', 'EXPIRED', 'SUPERSEDED'
    )),
    CONSTRAINT ck_commitments_type CHECK (commitment_type IN (
        'MONETARY_PAYMENT', 'MONETARY_REFUND', 'MONETARY_REIMBURSEMENT',
        'MONETARY_CREDIT', 'DEPOSIT_RETURN', 'SERVICE_TERMINATION',
        'SERVICE_DELIVERY', 'REPAIR', 'RESPONSE', 'DOCUMENT_DELIVERY',
        'CORRECTION', 'OTHER'
    )),
    CONSTRAINT ck_commitments_obligor CHECK (obligor_type IN ('USER', 'COUNTERPARTY', 'THIRD_PARTY')),
    CONSTRAINT ck_commitments_beneficiary
        CHECK (beneficiary_type IN ('USER', 'COUNTERPARTY', 'THIRD_PARTY')),
    CONSTRAINT ck_commitments_revision CHECK (revision >= 0),
    CONSTRAINT ck_commitments_currency_shape CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_commitments_validity
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),

    -- M1. All money is non-negative.
    CONSTRAINT ck_commitments_amounts_nonneg CHECK (
        (committed_amount   IS NULL OR committed_amount   >= 0)
        AND (fulfilled_amount   IS NULL OR fulfilled_amount   >= 0)
        AND (outstanding_amount IS NULL OR outstanding_amount >= 0)
    ),
    -- M2. A monetary commitment carries all three amounts or none. Without this,
    --     M3 and M4 would be vacuously true whenever one column was left NULL.
    CONSTRAINT ck_commitments_monetary_triple CHECK (
        (committed_amount IS NULL AND fulfilled_amount IS NULL AND outstanding_amount IS NULL)
        OR (committed_amount IS NOT NULL AND fulfilled_amount IS NOT NULL
            AND outstanding_amount IS NOT NULL)
    ),
    -- M3. Fulfilled never exceeds committed. Over-payment is an anomaly the Kernel
    --     must raise as a FULFILLMENT_CONFLICT, never silently clamp.
    CONSTRAINT ck_commitments_fulfilled_le_committed CHECK (
        committed_amount IS NULL OR fulfilled_amount <= committed_amount
    ),
    -- M4. The outstanding identity.
    CONSTRAINT ck_commitments_outstanding_identity CHECK (
        committed_amount IS NULL OR outstanding_amount = committed_amount - fulfilled_amount
    ),
    -- M5. Outstanding money forbids FULFILLED. DDL section 19 test 5 / G2.7.
    CONSTRAINT ck_commitments_outstanding_blocks_fulfilled CHECK (
        outstanding_amount IS NULL OR outstanding_amount = 0 OR status <> 'FULFILLED'
    ),
    -- M6. Any amount requires a currency.
    CONSTRAINT ck_commitments_money_needs_currency CHECK (
        committed_amount IS NULL OR currency IS NOT NULL
    ),
    -- M7. A monetary commitment with partial payment cannot claim to be untouched.
    CONSTRAINT ck_commitments_partial_status CHECK (
        fulfilled_amount IS NULL
        OR fulfilled_amount = 0
        OR outstanding_amount = 0
        OR status IN ('PARTIAL', 'DISPUTED', 'EXPIRED', 'SUPERSEDED')
    ),
    -- M8. FULFILLED requires the money to have actually arrived.
    CONSTRAINT ck_commitments_fulfilled_needs_payment CHECK (
        status <> 'FULFILLED'
        OR committed_amount IS NULL
        OR fulfilled_amount = committed_amount
    ),

    CONSTRAINT fk_commitments_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_commitments_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_commitments_source_claim
        FOREIGN KEY (tenant_id, user_id, source_claim_id)
        REFERENCES claims (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

FULFILLMENTS = """
CREATE TABLE fulfillments (
    id               UUID           NOT NULL PRIMARY KEY,
    tenant_id        UUID           NOT NULL,
    user_id          UUID           NOT NULL,
    commitment_id    UUID           NOT NULL,
    evidence_id      UUID           NOT NULL,
    currency         STRING         NULL,
    amount           DECIMAL(20,4)  NULL,
    quantity         DECIMAL(20,4)  NULL,
    fulfilled_at     TIMESTAMPTZ    NOT NULL,
    admission_status STRING         NOT NULL,
    confidence       DECIMAL(5,4)   NOT NULL,
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),

    -- One evidence item can satisfy a given commitment at most once. This is what
    -- makes replaying the same bank-transfer email a no-op instead of a double credit.
    CONSTRAINT uq_fulfillments_commitment_evidence UNIQUE (commitment_id, evidence_id),
    CONSTRAINT ck_fulfillments_admission CHECK (admission_status IN (
        'ADMITTED', 'CLAIMED_ONLY', 'DISPUTED', 'REJECTED', 'REJECTED_CURRENCY'
    )),
    CONSTRAINT ck_fulfillments_amount_nonneg   CHECK (amount   IS NULL OR amount   >= 0),
    CONSTRAINT ck_fulfillments_quantity_nonneg CHECK (quantity IS NULL OR quantity >= 0),
    CONSTRAINT ck_fulfillments_has_measure     CHECK (amount IS NOT NULL OR quantity IS NOT NULL),
    CONSTRAINT ck_fulfillments_money_currency  CHECK (amount IS NULL OR currency IS NOT NULL),
    CONSTRAINT ck_fulfillments_currency_shape  CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_fulfillments_confidence      CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT fk_fulfillments_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_fulfillments_commitment
        FOREIGN KEY (tenant_id, user_id, commitment_id)
        REFERENCES commitments (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_fulfillments_evidence
        FOREIGN KEY (tenant_id, user_id, evidence_id)
        REFERENCES evidence_items (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

STATE_TRANSITIONS = """
CREATE TABLE state_transitions (
    id                 UUID        NOT NULL PRIMARY KEY,
    tenant_id          UUID        NOT NULL,
    user_id            UUID        NOT NULL,
    case_id            UUID        NOT NULL,
    case_revision      INT8        NOT NULL,
    transition_type    STRING      NOT NULL,
    subject_kind       STRING      NOT NULL,
    subject_id         UUID        NULL,
    from_state         STRING      NULL,
    to_state           STRING      NULL,
    reason_code        STRING      NOT NULL,
    proposal_id        UUID        NULL,
    kernel_decision_id UUID        NOT NULL,
    trace_id           UUID        NOT NULL,
    recorded_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_state_transitions_type CHECK (transition_type IN (
        'CASE_STATUS', 'CASE_ATTENTION', 'COMMITMENT_STATUS', 'CONFLICT_STATUS',
        'COMMITMENT_AMOUNT', 'BELIEF_VERSIONED', 'TRIGGER_STATE', 'ACTION_STATE',
        'RELATIONSHIP_STATUS', 'EVIDENCE_RETRACTION'
    )),
    CONSTRAINT ck_state_transitions_subject_kind CHECK (subject_kind IN (
        'CASE', 'COMMITMENT', 'CONFLICT', 'BELIEF', 'TRIGGER', 'ACTION',
        'RELATIONSHIP', 'EVIDENCE'
    )),
    CONSTRAINT ck_state_transitions_revision CHECK (case_revision >= 0),
    CONSTRAINT ck_state_transitions_moves CHECK (
        from_state IS DISTINCT FROM to_state OR transition_type = 'BELIEF_VERSIONED'
    ),
    CONSTRAINT ck_state_transitions_reason_shape CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    CONSTRAINT fk_state_transitions_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_state_transitions_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (CONFLICTS, COMMITMENTS, FULFILLMENTS, STATE_TRANSITIONS)


# ---------------------------------------------------------------------------
# Indexes — DDL section 7, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # conflict dedupe (02 section 4.14). Partial on the live statuses so a
    # genuinely new contradiction between the same two sources can be raised
    # again after the previous one was resolved.
    """
    CREATE UNIQUE INDEX uq_conflicts_live_identity
        ON conflicts (tenant_id, user_id, subject_type, subject_id, predicate,
                      left_source_kind, left_source_id, right_source_kind, right_source_id)
        WHERE status IN ('OPEN', 'NEEDS_HUMAN')
    """,
    # GET /v1/cases/{case_id}/conflicts and the dashboard's active_conflicts_count --
    # SELECT * FROM conflicts WHERE tenant_id=$1 AND user_id=$2 AND case_id=$3
    #   AND status IN ('OPEN','NEEDS_HUMAN') ORDER BY detected_at DESC.
    """
    CREATE INDEX idx_conflicts_case_status
        ON conflicts (tenant_id, user_id, case_id, status, detected_at DESC)
        STORING (conflict_type, severity, requires_human, predicate,
                 left_source_kind, left_source_id, right_source_kind, right_source_id)
    """,
    # the human-review queue -- SELECT * FROM conflicts
    # WHERE tenant_id=$1 AND user_id=$2 AND requires_human
    # ORDER BY severity DESC, detected_at.
    """
    CREATE INDEX idx_conflicts_needs_human
        ON conflicts (tenant_id, user_id, severity, detected_at)
        WHERE status = 'NEEDS_HUMAN'
    """,
    # GET /v1/cases/{case_id} commitments[] and unresolved_commitments_count --
    # SELECT * FROM commitments WHERE tenant_id=$1 AND user_id=$2 AND case_id=$3
    #   AND status IN ('ACTIVE','PARTIAL','DISPUTED','PROPOSED').
    """
    CREATE INDEX idx_commitments_case_status
        ON commitments (tenant_id, user_id, case_id, status)
        STORING (commitment_type, description, currency, committed_amount,
                 fulfilled_amount, outstanding_amount, due_at, revision)
    """,
    # the overdue sweep behind commitment.overdue.v1 and the landlord-deposit
    # second reveal -- SELECT * FROM commitments WHERE due_at < now()
    #   AND outstanding_amount > 0 AND status IN ('ACTIVE','PARTIAL') ORDER BY due_at.
    """
    CREATE INDEX idx_commitments_overdue
        ON commitments (due_at, tenant_id, user_id)
        STORING (case_id, outstanding_amount, currency, status, commitment_type)
        WHERE due_at IS NOT NULL AND status IN ('ACTIVE', 'PARTIAL')
    """,
    # State Proof grounding join -- SELECT * FROM commitments WHERE source_claim_id = $1.
    """
    CREATE INDEX idx_commitments_source_claim
        ON commitments (tenant_id, user_id, source_claim_id)
    """,
    # the recompute in 02 section 12 -- SELECT coalesce(sum(amount),0) FROM fulfillments
    # WHERE commitment_id = $1 AND admission_status = 'ADMITTED' (recomputed from
    # scratch on every commit rather than incremented, so a retried transaction
    # cannot double-add).
    """
    CREATE INDEX idx_fulfillments_commitment_admitted
        ON fulfillments (commitment_id, admission_status)
        STORING (amount, quantity, currency, fulfilled_at, confidence, evidence_id)
    """,
    # State Proof / timeline -- SELECT * FROM fulfillments
    # WHERE tenant_id=$1 AND user_id=$2 AND evidence_id=$3.
    """
    CREATE INDEX idx_fulfillments_evidence
        ON fulfillments (tenant_id, user_id, evidence_id)
    """,
    # GET /v1/cases/{case_id}/timeline (cursor-paginated) -- SELECT * FROM
    # state_transitions WHERE tenant_id=$1 AND user_id=$2 AND case_id=$3
    # ORDER BY case_revision DESC, recorded_at DESC LIMIT 50.
    """
    CREATE INDEX idx_state_transitions_case_revision
        ON state_transitions (tenant_id, user_id, case_id, case_revision DESC, recorded_at DESC)
        STORING (transition_type, subject_kind, subject_id, from_state, to_state,
                 reason_code, kernel_decision_id, trace_id)
    """,
    # Judge Mode Memory Trace -- SELECT * FROM state_transitions WHERE trace_id = $1
    # ORDER BY recorded_at ("show every canonical change this one artifact caused").
    """
    CREATE INDEX idx_state_transitions_trace
        ON state_transitions (trace_id, recorded_at)
    """,
    # the concurrency test's assertion that one commit produced one coherent
    # revision -- SELECT * FROM state_transitions WHERE kernel_decision_id = $1.
    """
    CREATE INDEX idx_state_transitions_decision
        ON state_transitions (kernel_decision_id)
    """,
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = ("state_transitions", "fulfillments", "commitments", "conflicts")


def upgrade() -> None:
    """Create the obligation ledger. Four tables, eleven indexes."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything 0004 created, in reverse order. Local iteration only."""
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
