"""0006 — prospective memory: the trigger that wakes months later.

Revision ID: 0006_prospective_memory
Revises: 0005_kernel_control

Creates ``prospective_triggers`` from ``specs/10_DATABASE_DDL.md`` section 9.
One table, and it is the whole of the landlord-deposit second reveal.

``predicate_ast`` is JSONB because the alternative is arbitrary code
--------------------------------------------------------------------
``16_TRIGGER_DSL.md`` section 3 is an argument against storing an executable
predicate. What is stored is the **serialized safe AST** from
``provenance_contracts.predicates``: ``AND``/``OR``/``NOT``, comparisons,
``FIELD(path)`` over a whitelisted registry, ``CONST(value)``. Never SQL, never
a lambda, never free text a later evaluator would have to parse.

The column type is where that decision becomes irreversible. A ``STRING`` column
makes "the model wrote a little expression and we eval it" a one-line change six
months from now; ``JSONB`` with a parser on the way in does not.

``ck_prospective_triggers_fired`` and its companion
----------------------------------------------------
DDL section 9.1 prints ``(state = 'FIRED') = (fired_at IS NOT NULL)``. The
``T2.5`` sub-task states the rule against ``last_result`` instead. They agree in
the fire transaction (``16_TRIGGER_DSL.md`` section 10.2 step (f) sets ``state``,
``last_result`` and ``fired_at`` together) and the DDL is the higher authority,
so the biconditional is written on ``state`` as printed - and
``ck_prospective_triggers_fired_result`` adds the one-way implication
``state = 'FIRED' -> last_result = 'FIRED'`` that makes the two readings agree
without forbidding a re-arm. The discrepancy is reported with ``T2.5`` for the
Phase 2 gate ledger.

What this buys: ``D8`` asserts ``fired_at IS NULL`` on a **disarmed** trigger.
Without a biconditional that assertion passes on a bug, because a trigger that
fired and then failed to stamp the time is indistinguishable from one that never
fired.

``ck_prospective_triggers_last_reason`` pairs result with reason
----------------------------------------------------------------
``CANONICAL_DECISIONS.md``: "Trigger results ... plus one closed-set reason
code". The CHECK is written per result, so ``DISARMED`` + ``PREDICATE_FALSE`` -
a combination that reads plausibly and means nothing - is refused by the
database rather than by a code review.

``basis_case_revision`` is what makes rule I8 real
---------------------------------------------------
The scheduler says "look now"; the evaluator compares the case's *current*
revision and *current* projection against the predicate before doing anything. A
trigger whose case resolved since arming records ``last_result = 'DISARMED'``
and ``last_reason_code = 'CASE_RESOLVED'`` - which is precisely DDL section 19
test 8.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``: the three indexes below are all partial.

Downgrade
---------
Implemented, and for **local iteration only**. From Phase 13 onward schema rolls
forward and code rolls back (``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0006_prospective_memory"
down_revision = "0005_kernel_control"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Table — DDL section 9.1.
# ---------------------------------------------------------------------------

PROSPECTIVE_TRIGGERS = """
CREATE TABLE prospective_triggers (
    id                  UUID        NOT NULL PRIMARY KEY,
    tenant_id           UUID        NOT NULL,
    user_id             UUID        NOT NULL,
    case_id             UUID        NOT NULL,
    trigger_type        STRING      NOT NULL,
    predicate_ast       JSONB       NOT NULL,
    not_before          TIMESTAMPTZ NULL,
    expires_at          TIMESTAMPTZ NULL,
    state               STRING      NOT NULL DEFAULT 'ARMED',
    evaluation_version  INT8        NOT NULL DEFAULT 0,
    basis_case_revision INT8        NOT NULL,
    schedule_name       STRING      NULL,
    last_evaluated_at   TIMESTAMPTZ NULL,
    last_result         STRING      NULL,
    last_reason_code    STRING      NULL,
    fired_at            TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_prospective_triggers_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_prospective_triggers_schedule UNIQUE (schedule_name),
    CONSTRAINT ck_prospective_triggers_state CHECK (state IN (
        'ARMED', 'FIRED', 'DISARMED', 'EXPIRED'
    )),
    CONSTRAINT ck_prospective_triggers_type CHECK (trigger_type IN (
        'COMMITMENT_DEADLINE', 'RESPONSE_DEADLINE', 'CONFLICT_TIMEOUT',
        'WARRANTY_WINDOW'
    )),
    CONSTRAINT ck_prospective_triggers_last_result CHECK (last_result IS NULL OR last_result IN (
        'FIRED', 'NO_OP', 'DISARMED', 'EXPIRED', 'ERROR'
    )),
    CONSTRAINT ck_prospective_triggers_last_reason CHECK (
        (last_result IS NULL AND last_reason_code IS NULL) OR
        (last_result = 'FIRED' AND last_reason_code IN (
            'COMMITMENT_OVERDUE_UNPAID', 'RESPONSE_DEADLINE_MISSED',
            'CONFLICT_UNRESOLVED_TIMEOUT', 'WARRANTY_WINDOW_CLOSING'
        )) OR
        (last_result = 'NO_OP' AND last_reason_code IN (
            'PREDICATE_FALSE', 'PREDICATE_UNKNOWN', 'WOKE_TOO_EARLY',
            'STALE_SCHEDULE_GENERATION', 'TRIGGER_NOT_ARMED',
            'CONCURRENT_CASE_MUTATION', 'IDEMPOTENT_REPLAY'
        )) OR
        (last_result = 'DISARMED' AND last_reason_code IN (
            'COMMITMENT_SATISFIED', 'COMMITMENT_SUPERSEDED',
            'BINDING_SUPERSEDED', 'CASE_RESOLVED', 'CASE_SUPERSEDED',
            'USER_DISMISSED'
        )) OR
        (last_result = 'EXPIRED' AND last_reason_code IN (
            'TRIGGER_EXPIRED', 'REARM_BUDGET_EXHAUSTED'
        )) OR
        (last_result = 'ERROR' AND last_reason_code IN (
            'BINDING_UNRESOLVED', 'PROJECTION_FAILED', 'KERNEL_UNAVAILABLE'
        ))
    ),
    CONSTRAINT ck_prospective_triggers_versions CHECK (
        evaluation_version >= 0 AND basis_case_revision >= 0
    ),
    CONSTRAINT ck_prospective_triggers_window CHECK (
        expires_at IS NULL OR not_before IS NULL OR expires_at > not_before
    ),
    CONSTRAINT ck_prospective_triggers_fired CHECK (
        (state = 'FIRED') = (fired_at IS NOT NULL)
    ),
    -- Reconciles DDL section 9.1 with the T2.5 sub-task, which states the same
    -- rule against last_result. One-way, so a re-arm after a fire is still
    -- expressible; together with the biconditional above, fired_at IS NOT NULL
    -- implies last_result = 'FIRED' for as long as the trigger stays fired.
    CONSTRAINT ck_prospective_triggers_fired_result CHECK (
        state <> 'FIRED' OR last_result = 'FIRED'
    ),
    CONSTRAINT ck_prospective_triggers_evaluated CHECK (
        last_evaluated_at IS NULL OR last_result IS NOT NULL
    ),
    CONSTRAINT fk_prospective_triggers_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_prospective_triggers_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (PROSPECTIVE_TRIGGERS,)


# ---------------------------------------------------------------------------
# Indexes — DDL section 9.1, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # the sweep that backstops EventBridge Scheduler -- SELECT id FROM
    # prospective_triggers WHERE state='ARMED'
    #   AND (not_before IS NULL OR not_before <= now()) ORDER BY not_before LIMIT 100.
    # This is the query behind the landlord-deposit second reveal.
    """
    CREATE INDEX idx_prospective_triggers_due
        ON prospective_triggers (not_before, tenant_id, user_id)
        STORING (case_id, trigger_type, basis_case_revision, expires_at,
                 last_result, last_reason_code)
        WHERE state = 'ARMED'
    """,
    # POST /internal/v1/triggers/{trigger_id}/evaluate resolving case + user from
    # the trigger alone (never from the caller's claim).
    """
    CREATE INDEX idx_prospective_triggers_case_state
        ON prospective_triggers (tenant_id, user_id, case_id, state)
    """,
    # the expiry sweep -- UPDATE prospective_triggers SET state='EXPIRED'
    # WHERE expires_at < now() AND state='ARMED'.
    """
    CREATE INDEX idx_prospective_triggers_expiry
        ON prospective_triggers (expires_at)
        WHERE state = 'ARMED' AND expires_at IS NOT NULL
    """,
)

DROP_ORDER: tuple[str, ...] = ("prospective_triggers",)


def upgrade() -> None:
    """Create prospective memory. One table, three indexes, all partial or covering."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything 0006 created. Local iteration only."""
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
