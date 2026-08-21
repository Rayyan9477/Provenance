"""0003 — epistemic plane: claims, beliefs, belief versions, grounding edges.

Revision ID: 0003_epistemic_plane
Revises: 0002_evidence_plane

Creates the four tables of ``specs/10_DATABASE_DDL.md`` section 6:
``claims`` (what a source actor asserted — never truth), ``beliefs`` (stable
proposition identity), ``belief_versions`` (the **lineage** chain) and
``belief_support`` (the **grounding** edges).

The grounding invariant is a database CHECK
-------------------------------------------
``ck_belief_versions_grounded`` refuses any version that claims canonical
standing with no support edges, unless it declares itself a deterministic
derivation::

    derivation_kind = 'DETERMINISTIC_DERIVATION' OR support_edge_count >= 1

That is the schema half of the product's central claim. The behavioural half is
``services/control_plane/tests/db/test_kernel_required.py``
``::test_belief_cannot_be_canonical_without_grounding``, which asserts the
CockroachDB error class rather than a Python guard — ``G2.8``'s reference.

**It is a tripwire, not a proof.** CockroachDB evaluates ``CHECK`` per
statement, so the Kernel cannot insert ``support_edge_count = 0`` and fix it
later in the same transaction; it builds the whole grounding edge set in memory
first, inserts the version with the true count, then inserts exactly that many
``belief_support`` rows, all inside one serializable transaction. The residual
gap is a Kernel bug that writes a count larger than the edges it inserts. This
CHECK cannot see that. Verification query V3 (task ``T2.7``) is what catches it,
and DDL section 19 test 2 asserts V1, V2 and V3 all return zero after a real
commit.

``epistemic_status`` must be able to say DISPUTED
-------------------------------------------------
The hero disposition is ``RETAIN_INCUMBENT_DISPUTED``: the ISP balance belief
moves ``CONFIRMED -> DISPUTED`` **with its value unchanged**. A column that
cannot express that makes the hero commit unrepresentable, so both members are
in ``ck_belief_versions_status`` and both are exercised by the ``T2.3`` tests.

``beliefs.current_version_id`` deliberately has no foreign key
--------------------------------------------------------------
``belief_versions.belief_id`` already references ``beliefs.id``. Adding the
reverse FK creates a cycle, and CockroachDB validates foreign keys at statement
time rather than at commit, so **no insert order satisfies both**. The pointer
is maintained exclusively by the Kernel inside the same serializable transaction
that writes the version, and verification query V2 proves it never dangles.

``belief_support.source_id`` is polymorphic and carries no foreign key
----------------------------------------------------------------------
It points at ``evidence_items``, ``claims`` or ``belief_versions`` depending on
``source_kind``. Accepted as a bounded v1 integrity risk to keep State Proof to
one query instead of three: only ``pv_kernel_writer`` may insert edges, the
Kernel resolves every source before the transaction opens, and V4 audits every
reference. Adding a second writer requires replacing the polymorphic key with
typed nullable foreign keys in a forward migration.

``kernel_decision_id`` is NOT NULL with no FK *yet*
---------------------------------------------------
``kernel_decisions`` does not exist until ``0005``, and
``kernel_decisions.proposal_id`` needs ``memory_proposals``, which references
nothing here. Creating the column in ``0003`` and its foreign key in ``0005``
(DDL section 8.3) breaks that knot without a nullable column and without a
circular revision graph. Task ``T2.4``/``T2.5`` adds the constraint.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``.

Downgrade
---------
Implemented, in reverse creation order, and for **local iteration only**. From
Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0003_epistemic_plane"
down_revision = "0002_evidence_plane"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 6, in the order that section prints them.
# ---------------------------------------------------------------------------

CLAIMS = """
CREATE TABLE claims (
    id                    UUID          NOT NULL PRIMARY KEY,
    tenant_id             UUID          NOT NULL,
    user_id               UUID          NOT NULL,
    case_id               UUID          NULL,
    relationship_id       UUID          NULL,
    subject_type          STRING        NOT NULL,
    subject_id            UUID          NOT NULL,
    predicate             STRING        NOT NULL,
    object_type           STRING        NOT NULL,
    object_json           JSONB         NOT NULL,
    actor_type            STRING        NOT NULL,
    actor_id              STRING        NULL,
    evidence_id           UUID          NOT NULL,
    claim_kind            STRING        NOT NULL,
    valid_from            TIMESTAMPTZ   NULL,
    valid_to              TIMESTAMPTZ   NULL,
    authority_score       DECIMAL(5,4)  NULL,
    extraction_confidence DECIMAL(5,4)  NOT NULL,
    recorded_at           TIMESTAMPTZ   NOT NULL,
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT uq_claims_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_claims_evidence_proposition
        UNIQUE (tenant_id, user_id, evidence_id, subject_type, subject_id, predicate),
    CONSTRAINT ck_claims_kind CHECK (claim_kind IN (
        'OBSERVATION', 'COUNTERPARTY_CLAIM', 'USER_CLAIM', 'COMMITMENT_CLAIM',
        'POLICY_TERM', 'FULFILLMENT_CLAIM', 'CORRECTION', 'INFERENCE'
    )),
    CONSTRAINT ck_claims_subject_type CHECK (subject_type IN (
        'RELATIONSHIP', 'CASE', 'COMMITMENT', 'COUNTERPARTY', 'USER', 'ARTIFACT',
        'SERVICE'
    )),
    CONSTRAINT ck_claims_object_type CHECK (object_type IN (
        'BOOLEAN', 'STRING', 'MONEY', 'QUANTITY', 'TIMESTAMP', 'DATE',
        'INTERVAL', 'ENUM', 'IDENTIFIER', 'ADDRESS', 'STRUCT'
    )),
    CONSTRAINT ck_claims_actor_type CHECK (actor_type IN (
        'COUNTERPARTY', 'USER', 'SYSTEM', 'THIRD_PARTY', 'UNKNOWN'
    )),
    CONSTRAINT ck_claims_inference_authority CHECK (
        claim_kind <> 'INFERENCE' OR authority_score IS NULL OR authority_score <= 0.2000
    ),
    CONSTRAINT ck_claims_authority  CHECK (authority_score IS NULL
                                           OR (authority_score >= 0 AND authority_score <= 1)),
    CONSTRAINT ck_claims_confidence
        CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1),
    CONSTRAINT ck_claims_validity
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_claims_predicate_shape CHECK (predicate ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT fk_claims_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_claims_evidence
        FOREIGN KEY (tenant_id, user_id, evidence_id)
        REFERENCES evidence_items (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_claims_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_claims_relationship
        FOREIGN KEY (tenant_id, user_id, relationship_id)
        REFERENCES relationships (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

BELIEFS = """
CREATE TABLE beliefs (
    id                 UUID        NOT NULL PRIMARY KEY,
    tenant_id          UUID        NOT NULL,
    user_id            UUID        NOT NULL,
    case_id            UUID        NULL,
    subject_type       STRING      NOT NULL,
    subject_id         UUID        NOT NULL,
    predicate          STRING      NOT NULL,
    current_version_id UUID        NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_beliefs_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_beliefs_proposition
        UNIQUE (tenant_id, user_id, subject_type, subject_id, predicate),
    CONSTRAINT ck_beliefs_subject_type CHECK (subject_type IN (
        'RELATIONSHIP', 'CASE', 'COMMITMENT', 'COUNTERPARTY', 'USER', 'ARTIFACT',
        'SERVICE'
    )),
    CONSTRAINT ck_beliefs_predicate_shape CHECK (predicate ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT fk_beliefs_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_beliefs_case
        FOREIGN KEY (tenant_id, user_id, case_id)
        REFERENCES cases (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

BELIEF_VERSIONS = """
CREATE TABLE belief_versions (
    id                     UUID          NOT NULL PRIMARY KEY,
    tenant_id              UUID          NOT NULL,
    user_id                UUID          NOT NULL,
    belief_id              UUID          NOT NULL,
    version_no             INT8          NOT NULL,
    value_type             STRING        NOT NULL,
    value_json             JSONB         NOT NULL,
    epistemic_status       STRING        NOT NULL,
    belief_confidence      DECIMAL(5,4)  NOT NULL,
    derivation_kind        STRING        NOT NULL DEFAULT 'EVIDENCE_GROUNDED',
    support_edge_count     INT8          NOT NULL DEFAULT 0,
    supersedes_version_id  UUID          NULL,
    supersession_reason_code STRING      NULL,
    valid_from             TIMESTAMPTZ   NULL,
    valid_to               TIMESTAMPTZ   NULL,
    recorded_at            TIMESTAMPTZ   NOT NULL,
    superseded_at          TIMESTAMPTZ   NULL,
    kernel_decision_id     UUID          NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT uq_belief_versions_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_belief_versions_chain UNIQUE (belief_id, version_no),
    CONSTRAINT ck_belief_versions_status CHECK (epistemic_status IN (
        'CONFIRMED', 'PROBABLE', 'UNCERTAIN', 'DISPUTED', 'SUPERSEDED', 'RETRACTED'
    )),
    CONSTRAINT ck_belief_versions_value_type CHECK (value_type IN (
        'BOOLEAN', 'STRING', 'MONEY', 'QUANTITY', 'TIMESTAMP', 'DATE',
        'INTERVAL', 'ENUM', 'IDENTIFIER', 'ADDRESS', 'STRUCT'
    )),
    CONSTRAINT ck_belief_versions_derivation CHECK (derivation_kind IN (
        'EVIDENCE_GROUNDED', 'DETERMINISTIC_DERIVATION'
    )),
    CONSTRAINT ck_belief_versions_version_no CHECK (version_no >= 1),
    CONSTRAINT ck_belief_versions_confidence
        CHECK (belief_confidence >= 0 AND belief_confidence <= 1),
    CONSTRAINT ck_belief_versions_support_count CHECK (support_edge_count >= 0),

    CONSTRAINT ck_belief_versions_grounded CHECK (
        derivation_kind = 'DETERMINISTIC_DERIVATION' OR support_edge_count >= 1
    ),

    CONSTRAINT ck_belief_versions_superseded_consistent CHECK (
        (epistemic_status = 'SUPERSEDED' AND superseded_at IS NOT NULL)
        OR (epistemic_status <> 'SUPERSEDED')
    ),
    CONSTRAINT ck_belief_versions_supersession_reason CHECK (
        supersedes_version_id IS NULL OR supersession_reason_code IS NOT NULL
    ),
    CONSTRAINT ck_belief_versions_no_self_supersede CHECK (
        supersedes_version_id IS NULL OR supersedes_version_id <> id
    ),
    CONSTRAINT ck_belief_versions_v1_has_no_parent CHECK (
        version_no > 1 OR supersedes_version_id IS NULL
    ),
    CONSTRAINT ck_belief_versions_validity CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from
    ),
    CONSTRAINT fk_belief_versions_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_belief_versions_belief
        FOREIGN KEY (tenant_id, user_id, belief_id)
        REFERENCES beliefs (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_belief_versions_supersedes
        FOREIGN KEY (tenant_id, user_id, supersedes_version_id)
        REFERENCES belief_versions (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

BELIEF_SUPPORT = """
CREATE TABLE belief_support (
    id                UUID          NOT NULL PRIMARY KEY,
    tenant_id         UUID          NOT NULL,
    user_id           UUID          NOT NULL,
    belief_version_id UUID          NOT NULL,
    source_kind       STRING        NOT NULL,
    source_id         UUID          NOT NULL,
    relation          STRING        NOT NULL,
    weight            DECIMAL(5,4)  NULL,
    reason_code       STRING        NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT uq_belief_support_edge
        UNIQUE (belief_version_id, source_kind, source_id, relation),
    CONSTRAINT ck_belief_support_source_kind CHECK (source_kind IN (
        'EVIDENCE', 'CLAIM', 'BELIEF_VERSION', 'DERIVATION'
    )),
    CONSTRAINT ck_belief_support_relation CHECK (relation IN (
        'SUPPORTS', 'CONTRADICTS', 'QUALIFIES'
    )),
    CONSTRAINT ck_belief_support_weight CHECK (weight IS NULL OR (weight >= 0 AND weight <= 1)),
    CONSTRAINT ck_belief_support_no_self CHECK (
        source_kind <> 'BELIEF_VERSION' OR source_id <> belief_version_id
    ),
    CONSTRAINT fk_belief_support_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_belief_support_version
        FOREIGN KEY (tenant_id, user_id, belief_version_id)
        REFERENCES belief_versions (tenant_id, user_id, id) ON DELETE RESTRICT
)
"""

TABLE_DDL: tuple[str, ...] = (CLAIMS, BELIEFS, BELIEF_VERSIONS, BELIEF_SUPPORT)


# ---------------------------------------------------------------------------
# Indexes — DDL section 6, each with the query it serves.
# ---------------------------------------------------------------------------

INDEX_DDL: tuple[str, ...] = (
    # the Kernel's contradiction candidate scan -- SELECT * FROM claims
    # WHERE tenant_id=$1 AND user_id=$2 AND subject_type=$3 AND subject_id=$4
    #   AND predicate=$5 ORDER BY recorded_at DESC LIMIT 20.
    # Step 11 of the decision pipeline: "compare against current beliefs".
    """
    CREATE INDEX idx_claims_proposition
        ON claims (tenant_id, user_id, subject_type, subject_id, predicate, recorded_at DESC)
        STORING (object_type, object_json, claim_kind, authority_score,
                 valid_from, valid_to, evidence_id)
    """,
    # GET /v1/cases/{case_id}/timeline --
    # SELECT * FROM claims WHERE case_id=$1 ORDER BY recorded_at DESC.
    """
    CREATE INDEX idx_claims_case_recorded
        ON claims (tenant_id, user_id, case_id, recorded_at DESC)
        WHERE case_id IS NOT NULL
    """,
    # State Proof grounding expansion --
    # SELECT * FROM claims WHERE evidence_id = ANY($1).
    """
    CREATE INDEX idx_claims_evidence
        ON claims (tenant_id, user_id, evidence_id)
    """,
    # State Proof step 2 --
    # SELECT * FROM beliefs WHERE tenant_id=$1 AND user_id=$2 AND case_id=$3.
    """
    CREATE INDEX idx_beliefs_case
        ON beliefs (tenant_id, user_id, case_id)
        STORING (subject_type, subject_id, predicate, current_version_id)
        WHERE case_id IS NOT NULL
    """,
    # lineage join from a version back to its head --
    # SELECT * FROM beliefs WHERE current_version_id = $1.
    """
    CREATE INDEX idx_beliefs_current_version
        ON beliefs (current_version_id)
        WHERE current_version_id IS NOT NULL
    """,
    # State Proof lineage panel --
    # SELECT * FROM belief_versions WHERE belief_id = $1 ORDER BY version_no DESC.
    """
    CREATE INDEX idx_belief_versions_chain
        ON belief_versions (belief_id, version_no DESC)
        STORING (value_type, value_json, epistemic_status, belief_confidence,
                 valid_from, valid_to, recorded_at, superseded_at,
                 supersedes_version_id, supersession_reason_code, derivation_kind)
    """,
    # Memory Trace -- SELECT * FROM belief_versions WHERE kernel_decision_id = $1
    # ("what did this one Kernel commit change?").
    """
    CREATE INDEX idx_belief_versions_decision
        ON belief_versions (kernel_decision_id)
    """,
    # bitemporal reconciliation (rule T4) -- SELECT * FROM belief_versions
    # WHERE tenant_id=$1 AND user_id=$2 AND valid_from <= $3
    #   AND (valid_to IS NULL OR valid_to > $3)
    # ("what did we believe was true in the world on date X?").
    """
    CREATE INDEX idx_belief_versions_valid_time
        ON belief_versions (tenant_id, user_id, valid_from, valid_to)
        WHERE valid_from IS NOT NULL
    """,
    # State Proof step 3 --
    # SELECT * FROM belief_support WHERE belief_version_id = ANY($1)
    # (loads grounding for every current belief version on a case in one round trip).
    """
    CREATE INDEX idx_belief_support_version
        ON belief_support (belief_version_id, relation)
        STORING (source_kind, source_id, weight, reason_code)
    """,
    # reverse grounding lookup -- SELECT belief_version_id FROM belief_support
    # WHERE tenant_id=$1 AND user_id=$2 AND source_kind='EVIDENCE' AND source_id=$3
    # ("which beliefs rest on this evidence?" -- required when evidence is
    #  retracted, and by the deletion workflow).
    """
    CREATE INDEX idx_belief_support_source
        ON belief_support (tenant_id, user_id, source_kind, source_id)
    """,
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = ("belief_support", "belief_versions", "beliefs", "claims")


def upgrade() -> None:
    """Create the epistemic plane. Four tables, ten indexes."""
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything 0003 created, in reverse order. Local iteration only."""
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
