"""0008 — events, infrastructure, the five agent-safe views, and every grant.

Revision ID: 0008_events_infrastructure
Revises: 0007_action_plane

The last revision of the Phase 2 chain. Creates the final four tables of
``specs/10_DATABASE_DDL.md`` section 11 - ``outbox_events``,
``processed_events``, ``agent_runs``, ``idempotency_records`` - which brings the
canonical set to **26**; then the five ``agent_*_v1`` views of section 14; then
the grants of section 15.

Why the views are last
----------------------
``agent_open_obligations_v1`` unions ``commitments`` with ``conflicts`` (0004)
and ``agent_belief_lineage_v1`` joins ``kernel_decisions`` (0005). The views
reach across every plane, so they cannot exist before the planes do.

The grants are the security boundary, not a hardening pass
-----------------------------------------------------------
``pv_agent_reader`` receives ``SELECT`` on five views and **nothing else** - no
table grant of any kind. It reaches data only through views owned by
``pv_migrator``, which execute with the owner's table privileges, so revoking a
view is the whole revocation. Verification query V9 asserts the absence;
``services/control_plane/tests/db/test_mcp_boundary.py`` asserts both the
absence *and* the refusal observed from a connection as that role, because DDL
section 20 risk 11 is explicit that the catalogue check is the weaker of the two.

``agent_evidence_retrieval_v1`` applies ``retraction_status = 'ACTIVE'``
**inside the view**. That is canon item C moved from a caller's discipline to
the boundary itself: it is why V10 returns zero while V11 returns three, and an
agent using MCP cannot forget it because there is nothing to forget.

Roles are not created here, and that is deliberate
---------------------------------------------------
``pv_migrator`` holds no ``CREATEROLE`` privilege on this cluster - probed, not
assumed: ``CREATE ROLE IF NOT EXISTS pv_ops_reader`` returns "user pv_migrator
does not have CREATEROLE privilege". Roles are cluster-scoped identities with
passwords, provisioned once by ``ops/40_INFRA_IAC.md`` section 11 and stored in
the ``provenance/db`` secret; a migration that recreated them would either fail
or rotate a live credential out from under a running service. So this revision
provisions **authorisation** and leaves **identity** to the cluster. All five
roles are asserted to exist below, so a missing one fails loudly here rather
than silently producing a database with no boundary.

``ALTER TABLE ... OWNER TO`` is issued one table at a time
-----------------------------------------------------------
DDL section 15 prints a single comma-separated ``ALTER TABLE a, b, c OWNER TO``.
CockroachDB v26.2 rejects that form with a syntax error at the first comma, so
the statement is issued per table. It is a no-op in the normal case - the
migrator created every one of these tables and therefore already owns them - and
it costs about a quarter-second each. It is kept because the ownership of these
tables is what the view-executes-as-owner argument rests on, and an invariant
that matters that much is worth re-establishing rather than assuming.

``idx_agent_runs_user_active`` uses ``status = 'RUNNING'``
-----------------------------------------------------------
DDL section 11.3 prints ``WHERE status = 'STARTED'`` while
``ck_agent_runs_status`` permits only ``RUNNING``, ``SUCCEEDED``, ``FAILED`` and
``ABANDONED``. As printed, the partial index can never contain a row, and the
comment directly above it names the query it serves as
``... AND status='RUNNING'``. The internal contradiction is resolved toward the
comment; reported with ``T2.6`` for the Phase 2 gate ledger.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here. ``GRANT`` and
  ``CREATE VIEW`` are schema changes, not writes.
- Literal SQL through ``op.execute()``.

Downgrade
---------
Implemented: views first, then the tables, then the grants that named them are
gone with the objects. For **local iteration only** - from Phase 13 onward
schema rolls forward and code rolls back (``quality/23_PHASE_GATES.md``
section 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Connection

revision = "0008_events_infrastructure"
down_revision = "0007_action_plane"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 11, in the order that section prints them.
# ---------------------------------------------------------------------------

OUTBOX_EVENTS = """
CREATE TABLE outbox_events (
    id                UUID        NOT NULL PRIMARY KEY,
    tenant_id         UUID        NOT NULL,
    user_id           UUID        NOT NULL,
    aggregate_type    STRING      NOT NULL,
    aggregate_id      UUID        NOT NULL,
    aggregate_version INT8        NOT NULL,
    event_type        STRING      NOT NULL,
    payload_version   STRING      NOT NULL,
    payload           JSONB       NOT NULL,
    trace_id          UUID        NOT NULL,
    causation_id      UUID        NULL,
    correlation_id    UUID        NULL,
    status            STRING      NOT NULL DEFAULT 'PENDING',
    attempt_count     INT8        NOT NULL DEFAULT 0,
    next_attempt_at   TIMESTAMPTZ NOT NULL,
    last_error        STRING      NULL,
    occurred_at       TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at     TIMESTAMPTZ NULL,

    -- One event per (aggregate, version, type). A Kernel transaction that retries
    -- after a 40001 cannot emit the same domain event twice, because the retry
    -- recomputes the same aggregate_version and collides here.
    CONSTRAINT uq_outbox_events_aggregate_event
        UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type),
    CONSTRAINT ck_outbox_events_status CHECK (status IN (
        'PENDING', 'DISPATCHING', 'DISPATCHED', 'FAILED_RETRYABLE', 'DEAD'
    )),
    CONSTRAINT ck_outbox_events_aggregate_type CHECK (aggregate_type IN (
        'CASE', 'RELATIONSHIP', 'ACTION', 'TRIGGER', 'ARTIFACT'
    )),
    -- Closed vocabulary from 04_API_EVENTS_SECURITY.md section 14. "Do not invent
    -- event names ad hoc in consumers" is enforced here rather than only in review.
    CONSTRAINT ck_outbox_events_event_type CHECK (event_type IN (
        'artifact.received.v1', 'artifact.parsed.v1', 'artifact.rejected.v1',
        'evidence.admitted.v1', 'evidence.retracted.v1',
        'memory.proposal.accepted.v1', 'memory.proposal.rejected.v1',
        'belief.changed.v1', 'conflict.detected.v1', 'conflict.resolved.v1',
        'case.reopened.v1', 'case.state_changed.v1',
        'commitment.created.v1', 'commitment.partially_fulfilled.v1',
        'commitment.fulfilled.v1', 'commitment.overdue.v1',
        'trigger.armed.v1', 'trigger.fired.v1', 'trigger.noop.v1',
        'action.proposed.v1', 'action.approved.v1', 'action.rejected.v1',
        'action.executed.v1', 'action.failed.v1',
        'relationship.state_changed.v1'
    )),
    CONSTRAINT ck_outbox_events_version CHECK (aggregate_version >= 0),
    CONSTRAINT ck_outbox_events_attempts CHECK (attempt_count >= 0 AND attempt_count <= 5),
    CONSTRAINT ck_outbox_events_payload_version CHECK (payload_version ~ '^[0-9]+\\.[0-9]+$'),
    CONSTRAINT ck_outbox_events_dispatched CHECK (
        (status = 'DISPATCHED') = (dispatched_at IS NOT NULL)
    ),
    CONSTRAINT ck_outbox_events_dead_has_error CHECK (
        status NOT IN ('DEAD', 'FAILED_RETRYABLE') OR last_error IS NOT NULL
    ),
    CONSTRAINT fk_outbox_events_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

PROCESSED_EVENTS = """
CREATE TABLE processed_events (
    consumer_name STRING      NOT NULL,
    event_id      UUID        NOT NULL,
    tenant_id     UUID        NULL,
    user_id       UUID        NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    result_hash   BYTES       NULL,

    CONSTRAINT pk_processed_events PRIMARY KEY (consumer_name, event_id),
    CONSTRAINT ck_processed_events_consumer_shape
        CHECK (consumer_name ~ '^[a-z][a-z0-9_.-]{2,63}$'),
    CONSTRAINT ck_processed_events_result_hash
        CHECK (result_hash IS NULL OR length(result_hash) = 32)
)
"""

AGENT_RUNS = """
CREATE TABLE agent_runs (
    id                UUID        NOT NULL PRIMARY KEY,
    tenant_id         UUID        NOT NULL,
    user_id           UUID        NOT NULL,
    trace_id          UUID        NOT NULL,
    graph_name        STRING      NOT NULL,
    graph_version     STRING      NOT NULL,
    model_route       JSONB       NOT NULL,
    memory_mode       STRING      NOT NULL DEFAULT 'ON',
    is_counterfactual BOOL        NOT NULL DEFAULT false,
    status            STRING      NOT NULL DEFAULT 'RUNNING',
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ NULL,
    expires_at        TIMESTAMPTZ NOT NULL,
    input_artifact_id UUID        NULL,
    allowed_case_ids  JSONB       NULL,
    retrieval_candidate_count INT8 NULL,
    error_code        STRING      NULL,

    -- Memory Trace and Judge Mode source columns. Caller-reported by the agent
    -- runtime over POST /internal/v1/agent-runs/{id}/complete, which is why
    -- frontend/32_JUDGE_MODE.md section 6.4 discloses them as caller-reported
    -- rather than server-observed. They are metadata about a run; nothing in
    -- State Proof or any canonical read path consults them.
    --
    -- tool_calls: ordered array of MCP tool invocations. Each element carries
    --   seq (int), mcp_server (str), tool_name (str), view_name (str),
    --   sql_role (str), access_mode (always READ_ONLY), rows_returned (int),
    --   duration_ms (int), filter_summary (str), started_at (RFC3339).
    --   Written as a prose list rather than a JSON sketch on purpose: a literal
    --   {"seq"<colon>int} in this comment is parsed by SQLAlchemy as a bind
    --   parameter and the CREATE TABLE fails before it reaches the cluster.
    -- This is the column the MCP visibility requirement rests on. The COLUMN
    -- is always tool_calls; agent_runs.mcp_tool_calls is not a column name.
    -- The JSON FIELD that carries this array over HTTP is mcp_tool_calls[].
    -- Column tool_calls, field mcp_tool_calls -- that pairing is fixed.
    tool_calls        JSONB       NULL,
    -- model_calls: ordered array of Bedrock invocations. Each element carries
    --   seq (int), node (str), model_id (str), prompt_version (str),
    --   input_tokens (int), output_tokens (int), repair_attempts (int),
    --   duration_ms (int), started_at (RFC3339).
    model_calls       JSONB       NULL,
    -- capability_status: the run's capability lifecycle, so a judge can see
    -- what this run was and was not allowed to do.
    capability_status JSONB       NULL,

    CONSTRAINT uq_agent_runs_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_agent_runs_tool_calls CHECK (
        tool_calls IS NULL OR jsonb_typeof(tool_calls) = 'array'
    ),
    CONSTRAINT ck_agent_runs_model_calls CHECK (
        model_calls IS NULL OR jsonb_typeof(model_calls) = 'array'
    ),
    CONSTRAINT ck_agent_runs_capability_status CHECK (
        capability_status IS NULL OR jsonb_typeof(capability_status) = 'object'
    ),
    -- A counterfactual run may never have been given the proposal tool.
    CONSTRAINT ck_agent_runs_counterfactual_toolless CHECK (
        is_counterfactual = false
        OR capability_status IS NULL
        OR (capability_status->>'proposal_tool_bound')::BOOL = false
    ),
    CONSTRAINT ck_agent_runs_graph CHECK (graph_name IN (
        'ingestion', 'advocate', 'resolver', 'counterfactual'
    )),
    CONSTRAINT ck_agent_runs_status CHECK (status IN (
        'RUNNING', 'SUCCEEDED', 'FAILED', 'ABANDONED'
    )),
    -- Canon item A: the Judge Mode memory ON/OFF toggle is a first-class,
    -- auditable property of a run, not a UI trick.
    CONSTRAINT ck_agent_runs_memory_mode CHECK (memory_mode IN ('ON', 'OFF')),
    CONSTRAINT ck_agent_runs_counterfactual_consistent CHECK (
        is_counterfactual = (memory_mode = 'OFF')
    ),
    CONSTRAINT ck_agent_runs_terminal CHECK (status = 'RUNNING' OR finished_at IS NOT NULL),
    CONSTRAINT ck_agent_runs_error CHECK (
        status NOT IN ('FAILED', 'ABANDONED') OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_agent_runs_expiry CHECK (expires_at > started_at),
    CONSTRAINT ck_agent_runs_allowed_cases CHECK (
        allowed_case_ids IS NULL OR jsonb_typeof(allowed_case_ids) = 'array'
    ),
    CONSTRAINT fk_agent_runs_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_agent_runs_artifact
        FOREIGN KEY (tenant_id, user_id, input_artifact_id)
        REFERENCES source_artifacts (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

IDEMPOTENCY_RECORDS = """
CREATE TABLE idempotency_records (
    scope              STRING      NOT NULL,
    key                STRING      NOT NULL,
    tenant_id          UUID        NULL,
    user_id            UUID        NULL,
    request_hash       BYTES       NOT NULL,
    -- The trace this idempotency record was created under. Memory Trace joins
    -- eleven sources on trace_id; without this column the idempotency row is the
    -- one source reachable only by a second round trip. NULL is permitted
    -- because the record may be created before a trace context exists.
    trace_id           UUID        NULL,
    status             STRING      NOT NULL DEFAULT 'IN_PROGRESS',
    response_code      INT8        NULL,
    response_body_hash BYTES       NULL,
    response_body      JSONB       NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ NULL,
    expires_at         TIMESTAMPTZ NOT NULL,

    CONSTRAINT pk_idempotency_records PRIMARY KEY (scope, key),
    CONSTRAINT ck_idempotency_status CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_idempotency_scope_shape CHECK (scope ~ '^[a-z][a-z0-9_.]{2,63}$'),
    CONSTRAINT ck_idempotency_request_hash CHECK (length(request_hash) = 32),
    CONSTRAINT ck_idempotency_response_hash CHECK (
        response_body_hash IS NULL OR length(response_body_hash) = 32
    ),
    CONSTRAINT ck_idempotency_completed CHECK (
        status = 'IN_PROGRESS' OR (completed_at IS NOT NULL AND response_code IS NOT NULL)
    ),
    CONSTRAINT ck_idempotency_expiry CHECK (expires_at > created_at)
) WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@hourly')
"""

TABLE_DDL: tuple[str, ...] = (OUTBOX_EVENTS, PROCESSED_EVENTS, AGENT_RUNS, IDEMPOTENCY_RECORDS)


# ---------------------------------------------------------------------------
# Indexes — DDL section 11, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # the dispatcher claim -- SELECT id FROM outbox_events
    # WHERE status IN ('PENDING','FAILED_RETRYABLE') AND next_attempt_at <= now()
    # ORDER BY next_attempt_at, created_at LIMIT 50 FOR UPDATE.
    # Partial so the index stays tiny once events are dispatched: the dispatcher
    # never pays for the history of everything already delivered.
    """
    CREATE INDEX idx_outbox_events_dispatch_queue
        ON outbox_events (next_attempt_at, created_at)
        STORING (tenant_id, user_id, event_type, aggregate_type, aggregate_id,
                 aggregate_version, payload, payload_version, trace_id, attempt_count)
        WHERE status IN ('PENDING', 'FAILED_RETRYABLE')
    """,
    # the DEAD-letter alarm and manual replay --
    # SELECT * FROM outbox_events WHERE status='DEAD' ORDER BY created_at DESC.
    """
    CREATE INDEX idx_outbox_events_dead
        ON outbox_events (created_at DESC)
        WHERE status = 'DEAD'
    """,
    # Memory Trace ("which events did this commit emit?") --
    # SELECT * FROM outbox_events WHERE trace_id = $1 ORDER BY created_at.
    """
    CREATE INDEX idx_outbox_events_trace
        ON outbox_events (trace_id, created_at)
    """,
    # the outbox-pending-age metric --
    # SELECT min(created_at) FROM outbox_events WHERE status <> 'DISPATCHED'.
    """
    CREATE INDEX idx_outbox_events_pending_age
        ON outbox_events (created_at)
        WHERE status <> 'DISPATCHED'
    """,
    # Judge Mode's "inject duplicate event" demo -- SELECT * FROM processed_events
    # WHERE event_id = $1 (shows first delivery PROCESSED, duplicate NOOP).
    """
    CREATE INDEX idx_processed_events_event
        ON processed_events (event_id)
    """,
    # consumer-lag observability --
    # SELECT consumer_name, max(processed_at) FROM processed_events GROUP BY 1.
    """
    CREATE INDEX idx_processed_events_recent
        ON processed_events (consumer_name, processed_at DESC)
    """,
    # the workload-authentication resolution in 04 section 2.2 --
    # SELECT tenant_id, user_id, allowed_case_ids, memory_mode FROM agent_runs
    # WHERE id=$1 AND status='RUNNING' AND expires_at > now(). The agent sends
    # agent_run_id; the backend derives identity from this row, so a machine
    # client can never assert a user_id of its own choosing.
    """
    CREATE INDEX idx_agent_runs_active
        ON agent_runs (id)
        STORING (tenant_id, user_id, memory_mode, allowed_case_ids, expires_at)
        WHERE status = 'RUNNING'
    """,
    # Memory Trace -- SELECT * FROM agent_runs WHERE trace_id=$1 ORDER BY started_at.
    """
    CREATE INDEX idx_agent_runs_trace
        ON agent_runs (trace_id, started_at)
    """,
    # the Judge Mode side-by-side panel -- SELECT * FROM agent_runs
    # WHERE trace_id=$1 AND memory_mode IN ('ON','OFF') (renders the memory-OFF
    # and memory-ON runs of the same artifact together).
    """
    CREATE INDEX idx_agent_runs_counterfactual
        ON agent_runs (tenant_id, user_id, input_artifact_id, memory_mode)
        WHERE input_artifact_id IS NOT NULL
    """,
    # the per-user concurrent-run limit in 04 section 24 -- SELECT count(*)
    # FROM agent_runs WHERE tenant_id=$1 AND user_id=$2 AND status='RUNNING'.
    # DDL section 11.3 prints WHERE status = 'STARTED', which ck_agent_runs_status
    # forbids; the predicate follows the query the comment names.
    """
    CREATE INDEX idx_agent_runs_user_active
        ON agent_runs (tenant_id, user_id)
        WHERE status = 'RUNNING'
    """,
    # operational cleanup if probe P9 failed and row-level TTL is unavailable --
    # DELETE FROM idempotency_records WHERE expires_at < now() LIMIT 1000.
    """
    CREATE INDEX idx_idempotency_expiry
        ON idempotency_records (expires_at)
    """,
    # Memory Trace assembly -- SELECT * FROM idempotency_records WHERE trace_id = $1.
    """
    CREATE INDEX idx_idempotency_trace
        ON idempotency_records (trace_id)
        WHERE trace_id IS NOT NULL
    """,
)


# ---------------------------------------------------------------------------
# The five agent-safe views — DDL section 14. Canon names, verbatim.
# ---------------------------------------------------------------------------

#: Order matters only for the drop; creation order is free because no view
#: references another.
AGENT_VIEWS: tuple[str, ...] = (
    "agent_case_context_v1",
    "agent_active_beliefs_v1",
    "agent_belief_lineage_v1",
    "agent_evidence_retrieval_v1",
    "agent_open_obligations_v1",
)

VIEW_DDL: tuple[str, ...] = (
    # V1. Case context. What the agent is allowed to know about a case.
    """
    CREATE VIEW agent_case_context_v1 AS
    SELECT
        c.tenant_id,
        c.user_id,
        c.id                AS case_id,
        c.title,
        c.case_type,
        c.status,
        c.revision,
        c.attention_level,
        c.opened_at,
        c.resolved_at,
        c.last_activity_at,
        c.reopened_count,
        r.id                AS relationship_id,
        r.relationship_type,
        r.external_account_ref,
        cp.display_name     AS counterparty_name,
        cp.kind             AS counterparty_kind,
        cp.canonical_domain AS counterparty_domain,
        ctx.title           AS context_title
    FROM cases c
    JOIN relationships r
      ON r.tenant_id = c.tenant_id AND r.user_id = c.user_id AND r.id = c.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = r.tenant_id AND cp.id = r.counterparty_id
    LEFT JOIN contexts ctx
      ON ctx.tenant_id = c.tenant_id AND ctx.user_id = c.user_id AND ctx.id = c.context_id
    WHERE c.status <> 'SUPERSEDED'
    """,
    # V2. Active canonical beliefs with their grounding, flattened. Every row is
    #     one belief version paired with one support edge and its relation.
    """
    CREATE VIEW agent_active_beliefs_v1 AS
    SELECT
        b.tenant_id,
        b.user_id,
        b.case_id,
        b.id               AS belief_id,
        b.subject_type,
        b.subject_id,
        b.predicate,
        bv.id              AS belief_version_id,
        bv.version_no,
        bv.value_type,
        bv.value_json,
        bv.epistemic_status,
        bv.belief_confidence,
        bv.derivation_kind,
        bv.valid_from,
        bv.valid_to,
        bv.recorded_at,
        bs.relation        AS grounding_relation,
        bs.source_kind     AS grounding_source_kind,
        bs.source_id       AS grounding_source_id,
        bs.weight          AS grounding_weight,
        bs.reason_code     AS grounding_reason_code
    FROM beliefs b
    JOIN belief_versions bv
      ON bv.tenant_id = b.tenant_id AND bv.user_id = b.user_id AND bv.id = b.current_version_id
    LEFT JOIN belief_support bs
      ON bs.belief_version_id = bv.id
    WHERE bv.epistemic_status <> 'RETRACTED'
    """,
    # V3. Belief lineage. Why the current version replaced the previous one.
    """
    CREATE VIEW agent_belief_lineage_v1 AS
    SELECT
        bv.tenant_id,
        bv.user_id,
        bv.belief_id,
        bv.id                       AS belief_version_id,
        bv.version_no,
        bv.value_json,
        bv.epistemic_status,
        bv.recorded_at,
        bv.superseded_at,
        bv.supersedes_version_id,
        bv.supersession_reason_code,
        kd.decision                 AS kernel_decision,
        kd.reason_codes             AS kernel_reason_codes,
        kd.trace_id
    FROM belief_versions bv
    JOIN kernel_decisions kd
      ON kd.tenant_id = bv.tenant_id AND kd.user_id = bv.user_id AND kd.id = bv.kernel_decision_id
    """,
    # V4. Evidence snippets safe to show an agent, WITH the retraction filter
    #     baked in. Raw exact_text and source_locator are withheld; the agent
    #     gets the normalised semantic string and the identifiers it needs to cite.
    """
    CREATE VIEW agent_evidence_retrieval_v1 AS
    SELECT
        e.tenant_id,
        e.user_id,
        e.id           AS evidence_id,
        e.artifact_id,
        e.evidence_type,
        e.normalized_text,
        e.actor_ref,
        e.valid_from,
        e.valid_to,
        e.observed_at,
        e.extraction_confidence,
        e.source_authority,
        e.embedding_version,
        a.source_type,
        a.sender_domain,
        a.subject      AS artifact_subject,
        a.received_at  AS artifact_received_at
    FROM evidence_items e
    JOIN source_artifacts a
      ON a.tenant_id = e.tenant_id AND a.user_id = e.user_id AND a.id = e.artifact_id
    WHERE e.retraction_status = 'ACTIVE'
    """,
    # V5. Open conflicts and open commitments, the two things an Advocate must
    #     never miss.
    """
    CREATE VIEW agent_open_obligations_v1 AS
    SELECT
        cm.tenant_id,
        cm.user_id,
        cm.case_id,
        'COMMITMENT'          AS row_kind,
        cm.id                 AS row_id,
        cm.commitment_type    AS subtype,
        cm.status,
        cm.description        AS summary,
        cm.currency,
        cm.committed_amount,
        cm.fulfilled_amount,
        cm.outstanding_amount,
        cm.due_at,
        NULL::STRING          AS severity
    FROM commitments cm
    WHERE cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
    UNION ALL
    SELECT
        cf.tenant_id,
        cf.user_id,
        cf.case_id,
        'CONFLICT'            AS row_kind,
        cf.id                 AS row_id,
        cf.conflict_type      AS subtype,
        cf.status,
        cf.predicate          AS summary,
        NULL::STRING          AS currency,
        NULL::DECIMAL(20,4)   AS committed_amount,
        NULL::DECIMAL(20,4)   AS fulfilled_amount,
        NULL::DECIMAL(20,4)   AS outstanding_amount,
        NULL::TIMESTAMPTZ     AS due_at,
        cf.severity
    FROM conflicts cf
    WHERE cf.status IN ('OPEN', 'NEEDS_HUMAN')
    """,
)


# ---------------------------------------------------------------------------
# Grants — DDL section 15 and ops/40_INFRA_IAC.md section 11.
# ---------------------------------------------------------------------------

#: The 26 canonical tables, in the order DDL section 15 lists them.
CANONICAL_TABLES: tuple[str, ...] = (
    "tenants",
    "users",
    "ingest_aliases",
    "counterparties",
    "relationships",
    "contexts",
    "cases",
    "source_artifacts",
    "evidence_items",
    "claims",
    "beliefs",
    "belief_versions",
    "belief_support",
    "conflicts",
    "commitments",
    "fulfillments",
    "state_transitions",
    "memory_proposals",
    "kernel_decisions",
    "prospective_triggers",
    "action_intents",
    "action_executions",
    "outbox_events",
    "processed_events",
    "agent_runs",
    "idempotency_records",
)

#: Every runtime identity. Provisioned outside the migration; asserted here.
RUNTIME_ROLES: tuple[str, ...] = (
    "pv_app_reader_writer",
    "pv_kernel_writer",
    "pv_agent_reader",
    "pv_ops_reader",
)

_ALL = ", ".join(CANONICAL_TABLES)
_VIEWS = ",\n                ".join(AGENT_VIEWS)

GRANT_DDL: tuple[str, ...] = (
    # ---- pv_app_reader_writer ---------------------------------------------
    f"GRANT SELECT ON TABLE {_ALL} TO pv_app_reader_writer",
    "GRANT INSERT, UPDATE ON TABLE tenants, users, ingest_aliases, source_artifacts,"
    " action_intents, action_executions, agent_runs, idempotency_records"
    " TO pv_app_reader_writer",
    "GRANT INSERT ON TABLE evidence_items, memory_proposals, processed_events"
    " TO pv_app_reader_writer",
    # dispatcher status only -- the single exception to "no repository writes a
    # canonical table" (DDL section 12, Phase 10).
    "GRANT UPDATE ON TABLE outbox_events TO pv_app_reader_writer",
    # ---- pv_kernel_writer --------------------------------------------------
    "GRANT SELECT ON TABLE tenants, users, counterparties, relationships, contexts, cases,"
    " source_artifacts, evidence_items, claims, beliefs, belief_versions, belief_support,"
    " conflicts, commitments, fulfillments, state_transitions, memory_proposals,"
    " kernel_decisions, prospective_triggers, action_intents, outbox_events, agent_runs"
    " TO pv_kernel_writer",
    "GRANT INSERT, UPDATE ON TABLE counterparties, relationships, contexts, cases, beliefs,"
    " belief_versions, conflicts, commitments, prospective_triggers, kernel_decisions,"
    " evidence_items TO pv_kernel_writer",
    "GRANT INSERT ON TABLE claims, belief_support, fulfillments, state_transitions,"
    " outbox_events TO pv_kernel_writer",
    "GRANT UPDATE ON TABLE memory_proposals TO pv_kernel_writer",
    # The Kernel can never send anything, and can never mint an approval.
    "REVOKE ALL ON TABLE action_executions, ingest_aliases, idempotency_records,"
    " processed_events FROM pv_kernel_writer",
    "REVOKE INSERT, UPDATE ON TABLE action_intents FROM pv_kernel_writer",
    # ---- pv_agent_reader ---------------------------------------------------
    # Views only. Views execute with the owner's table privileges, so no
    # base-table grant is needed -- and none is given.
    f"GRANT SELECT ON {_VIEWS} TO pv_agent_reader",
    # Belt and braces: prove there is nothing else to reach.
    f"REVOKE ALL ON TABLE {_ALL} FROM pv_agent_reader",
    # ---- pv_ops_reader -----------------------------------------------------
    # Read-only operations and verification. This role exists so that trace
    # verification is performed by a principal that provably could not have
    # authored what it verifies.
    f"GRANT SELECT ON {_VIEWS} TO pv_ops_reader",
    # The eleven operational tables the trace assembly query and the row census
    # read. Nothing else: no evidence_items, no claims, no belief_versions.
    "GRANT SELECT ON TABLE source_artifacts, agent_runs, memory_proposals, kernel_decisions,"
    " state_transitions, outbox_events, processed_events, prospective_triggers,"
    " action_intents, action_executions, idempotency_records TO pv_ops_reader",
    # Provable read-only.
    "REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM pv_ops_reader",
    # ---- defaults ----------------------------------------------------------
    # Nothing new is ever granted implicitly to a runtime role.
    "ALTER DEFAULT PRIVILEGES FOR ROLE pv_migrator IN SCHEMA public"
    " REVOKE ALL ON TABLES FROM pv_app_reader_writer, pv_kernel_writer, pv_agent_reader,"
    " pv_ops_reader",
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = (
    "idempotency_records",
    "agent_runs",
    "processed_events",
    "outbox_events",
)


def _assert_runtime_roles_exist(bind: Connection) -> None:
    """Fail loudly if an identity this revision grants to does not exist.

    ``GRANT ... TO <missing role>`` is an error, so a missing role would fail
    anyway - but it would fail two hundred lines into a grant block with a
    message about one statement. This names every absentee at once, before a
    single privilege is issued.
    """
    present = {
        str(row[0]) for row in bind.exec_driver_sql("SELECT rolname FROM pg_roles").fetchall()
    }
    missing = [role for role in ("pv_migrator", *RUNTIME_ROLES) if role not in present]
    if missing:
        raise RuntimeError(
            "migration 0008 grants privileges to SQL roles that do not exist on this "
            f"cluster: {missing}. Roles are cluster-scoped identities with passwords, "
            "provisioned once by ops/40_INFRA_IAC.md section 11 and stored in the "
            "provenance/db secret; pv_migrator holds no CREATEROLE privilege and this "
            "migration deliberately does not mint credentials."
        )


def _take_ownership_of_every_table() -> None:
    """``ALTER TABLE <t> OWNER TO pv_migrator``, one statement per table.

    DDL section 15 prints these as a single comma-separated ``ALTER TABLE``;
    CockroachDB v26.2 rejects that form with a syntax error at the first comma.
    In the normal case each statement is a no-op, because the migrator created
    every one of these tables. It is issued anyway: the whole grant model rests
    on the views executing with an owner that holds the base-table privileges,
    and that is worth re-establishing rather than assuming.
    """
    for table in CANONICAL_TABLES:
        op.execute(f"ALTER TABLE {table} OWNER TO pv_migrator")


def upgrade() -> None:
    """Four tables, twelve indexes, five views, then every grant.

    Both catalogue reads happen first, before any schema change: CockroachDB is
    strict about statement kinds within one transaction, and a precondition that
    can only be checked halfway through a migration is not a precondition.
    """
    bind = op.get_bind()
    _assert_runtime_roles_exist(bind)
    database = str(bind.exec_driver_sql("SELECT current_database()").scalar())

    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)
    for statement in VIEW_DDL:
        op.execute(statement)

    _take_ownership_of_every_table()

    for role in RUNTIME_ROLES:
        op.execute(f'GRANT CONNECT ON DATABASE "{database}" TO {role}')

    for statement in GRANT_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop the views, then the four tables. Local iteration only.

    Grants naming a dropped object go with it, so there is no separate revoke
    step; what survives is the ``ALTER DEFAULT PRIVILEGES`` revocation, which is
    the *default* state anyway and is left in place on purpose.
    """
    for view in reversed(AGENT_VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
