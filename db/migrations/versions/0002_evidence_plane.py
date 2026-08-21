"""0002 — evidence plane, including the ANN vector index.

Revision ID: 0002_evidence_plane
Revises: 0001_identity_aggregates

Creates ``source_artifacts`` and ``evidence_items``
(``specs/10_DATABASE_DDL.md`` section 4), then the vector index from section
5.1 and the six non-vector indexes that section 4.2 names.

READ THIS IF YOU ARE HERE BECAUSE THE SEED FAILED
--------------------------------------------------
**``IMPORT INTO`` is unsupported on a table once a vector index exists.** This
revision creates ``evidence_embedding_ann_idx`` as part of the schema, so from
the moment ``0002`` lands, ``evidence_items`` cannot be bulk-loaded with
``IMPORT INTO``. Large batch inserts into a vector-indexed table also degrade
badly: every insert does partition maintenance on the ANN structure, so an
18,000-row load with the index live is dramatically slower than the same load
followed by one index build.

The seed therefore **drops this index, loads, and rebuilds it** — task ``T2.8``
steps 4, 6 and 7 (``ops/41_RUNBOOK.md`` section 4.2). The end state is
byte-identical to what this revision produces; only the procedure differs. Do
not "fix" a seed failure by removing the index from this migration: the schema
is supposed to carry it, and ``G2.4`` asserts it exists.

The vector index — variant and prefix
-------------------------------------
``VECTOR_INDEX_DDL`` below is **Variant A**, the first form in DDL section 5.1,
decided by probe PB-2 against this cluster and recorded in
``ops/decisions/VECTOR_INDEX_VARIANT.md``. It is a module-level constant so a
fallback to variant B or C is a one-line edit rather than a rewrite.

``user_id`` is the **prefix column and that is not negotiable**. It is not a
performance hint: it is the mechanism by which approximate nearest-neighbour
search physically cannot return another user's evidence, so invariant I7 does
not rest on a ``WHERE`` clause a future refactor can drop. Filter acceleration
in CockroachDB works only through prefix columns — an index built without the
prefix passes ``SHOW INDEXES`` and then fails ``EXPLAIN`` at ``G6.2``.

``D-06-001``, proven on this cluster: an ANN query vector supplied as a
**correlated subquery silently produces a full scan** — correct results, no
error, no warning. Phase 6 must embed first and pass the vector as a literal or
a bound parameter, never computing it inside the ranking statement.

Retraction, and why the embedding is never deleted
--------------------------------------------------
Evidence is append-only. Retraction is a status transition plus a pointer, never
a ``DELETE`` and never ``SET embedding = NULL``: grounding edges point at the
row, a withdrawn invoice is often the strongest support for the user's dispute
position, and the Judge Mode counterfactual has to stay re-runnable against the
same vector population.

The consequence is the thing to remember: **a retracted item's vector stays in
the ANN index and will be returned by nearest-neighbour search.** Filtering on
``retraction_status = 'ACTIVE'`` is a hard requirement of every retrieval query
(DDL section 5.5), not an optional refinement.

Rules this revision obeys (DDL section 16)
------------------------------------------
- **No DDL/DML mixing.** Not one row is written here.
- Literal SQL through ``op.execute()``: Alembic has no vector-index operation,
  and SQLAlchemy's dialect emits neither ``VECTOR`` nor ``FAMILY``.

Downgrade
---------
Implemented, in reverse creation order, and for **local iteration only**. From
Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from alembic import op

revision = "0002_evidence_plane"
down_revision = "0001_identity_aggregates"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables — DDL section 4.
# ---------------------------------------------------------------------------

SOURCE_ARTIFACTS = """
CREATE TABLE source_artifacts (
    id                UUID        NOT NULL PRIMARY KEY,
    tenant_id         UUID        NOT NULL,
    user_id           UUID        NOT NULL,
    source_type       STRING      NOT NULL,
    s3_bucket         STRING      NOT NULL,
    s3_key            STRING      NOT NULL,
    content_sha256    BYTES       NOT NULL,
    size_bytes        INT8        NOT NULL,
    mime_type         STRING      NOT NULL,
    source_message_id STRING      NULL,
    sender            STRING      NULL,
    sender_domain     STRING      NULL,
    recipient         STRING      NULL,
    subject           STRING      NULL,
    thread_ref        STRING      NULL,
    received_at       TIMESTAMPTZ NOT NULL,
    event_time        TIMESTAMPTZ NULL,
    parser_status     STRING      NOT NULL DEFAULT 'PENDING',
    parser_version    STRING      NULL,
    parser_metadata   JSONB       NULL,
    ses_verdicts      JSONB       NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_source_artifacts_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT uq_source_artifacts_content
        UNIQUE (tenant_id, user_id, content_sha256, source_type),
    CONSTRAINT ck_source_artifacts_source_type CHECK (source_type IN (
        'EMAIL_INBOUND', 'UPLOAD_EML', 'UPLOAD_PDF', 'UPLOAD_IMAGE',
        'UPLOAD_TEXT', 'USER_CORRECTION', 'SEED_FIXTURE'
    )),
    CONSTRAINT ck_source_artifacts_parser_status CHECK (parser_status IN (
        'PENDING', 'PARSING', 'PARSED', 'PARTIAL', 'FAILED', 'UNSUPPORTED_MIME'
    )),
    CONSTRAINT ck_source_artifacts_mime CHECK (mime_type IN (
        'message/rfc822', 'application/pdf', 'image/png', 'image/jpeg',
        'text/plain'
    )),
    CONSTRAINT ck_source_artifacts_sha_len   CHECK (length(content_sha256) = 32),
    CONSTRAINT ck_source_artifacts_size      CHECK (size_bytes > 0 AND size_bytes <= 20971520),
    CONSTRAINT ck_source_artifacts_parsed_has_version CHECK (
        parser_status <> 'PARSED' OR parser_version IS NOT NULL
    ),
    CONSTRAINT ck_source_artifacts_s3_key_shape CHECK (s3_key LIKE 'raw/%'),
    CONSTRAINT fk_source_artifacts_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT
)
"""

EVIDENCE_ITEMS = """
CREATE TABLE evidence_items (
    id                     UUID          NOT NULL PRIMARY KEY,
    tenant_id              UUID          NOT NULL,
    user_id                UUID          NOT NULL,
    artifact_id            UUID          NOT NULL,
    evidence_type          STRING        NOT NULL,
    normalized_text        STRING        NOT NULL,
    exact_text             STRING        NULL,
    source_locator         JSONB         NULL,
    actor_ref              STRING        NULL,
    valid_from             TIMESTAMPTZ   NULL,
    valid_to               TIMESTAMPTZ   NULL,
    observed_at            TIMESTAMPTZ   NOT NULL,
    extraction_confidence  DECIMAL(5,4)  NOT NULL,
    source_authority       DECIMAL(5,4)  NULL,

    retraction_status        STRING      NOT NULL DEFAULT 'ACTIVE',
    retracted_at             TIMESTAMPTZ NULL,
    retracted_by_evidence_id UUID        NULL,
    retraction_reason_code   STRING      NULL,
    is_retrieval_eligible    BOOL        NOT NULL AS (retraction_status = 'ACTIVE') STORED,

    embedding              VECTOR(1024)  NULL,
    embedding_model        STRING        NULL,
    embedding_version      STRING        NULL,
    embedding_generated_at TIMESTAMPTZ   NULL,
    normalized_text_sha256 BYTES         NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT uq_evidence_tenant_user_id UNIQUE (tenant_id, user_id, id),
    CONSTRAINT ck_evidence_type CHECK (evidence_type IN (
        'STATEMENT', 'CONFIRMATION', 'CANCELLATION_NOTICE',
        'SERVICE_STATUS_ASSERTION', 'INVOICE_LINE', 'PAYMENT_RECORD', 'RECEIPT',
        'COMMITMENT_STATEMENT', 'POLICY_TERM_TEXT', 'DATE_ASSERTION',
        'AMOUNT_ASSERTION', 'IDENTIFIER_ASSERTION', 'ADDRESS_ASSERTION',
        'CORRECTION_NOTICE', 'ATTACHMENT_REFERENCE', 'QUOTED_HISTORY_EXCERPT'
    )),
    CONSTRAINT ck_evidence_retraction_status CHECK (retraction_status IN (
        'ACTIVE', 'RETRACTED', 'SUPERSEDED', 'QUARANTINED'
    )),
    CONSTRAINT ck_evidence_retraction_consistent CHECK (
        (retraction_status = 'ACTIVE'
         AND retracted_at IS NULL
         AND retracted_by_evidence_id IS NULL
         AND retraction_reason_code IS NULL)
        OR (retraction_status <> 'ACTIVE'
         AND retracted_at IS NOT NULL
         AND retraction_reason_code IS NOT NULL)
    ),
    CONSTRAINT ck_evidence_retraction_reason CHECK (
        retraction_reason_code IS NULL OR retraction_reason_code IN (
            'USER_CORRECTION', 'EXTRACTION_ERROR', 'SOURCE_WITHDRAWN',
            'DUPLICATE_OF_OTHER', 'PARSER_DEFECT', 'ADVERSARIAL_CONTENT'
        )
    ),
    CONSTRAINT ck_evidence_no_self_retract CHECK (
        retracted_by_evidence_id IS NULL OR retracted_by_evidence_id <> id
    ),
    CONSTRAINT ck_evidence_confidence
        CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1),
    CONSTRAINT ck_evidence_authority  CHECK (source_authority IS NULL
                                             OR (source_authority >= 0 AND source_authority <= 1)),
    CONSTRAINT ck_evidence_validity
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_evidence_text_sha_len  CHECK (length(normalized_text_sha256) = 32),
    CONSTRAINT ck_evidence_text_nonempty CHECK (length(normalized_text) > 0),
    CONSTRAINT ck_evidence_embedding_provenance CHECK (
        embedding IS NULL
        OR (embedding_model IS NOT NULL
            AND embedding_version IS NOT NULL
            AND embedding_generated_at IS NOT NULL)
    ),
    CONSTRAINT ck_evidence_embedding_model CHECK (
        embedding_model IS NULL OR embedding_model = 'amazon.titan-embed-text-v2:0'
    ),
    CONSTRAINT fk_evidence_user
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_evidence_artifact
        FOREIGN KEY (tenant_id, user_id, artifact_id)
        REFERENCES source_artifacts (tenant_id, user_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_evidence_retracted_by
        FOREIGN KEY (tenant_id, user_id, retracted_by_evidence_id)
        REFERENCES evidence_items (tenant_id, user_id, id) ON DELETE RESTRICT,

    FAMILY f_meta (id, tenant_id, user_id, artifact_id, evidence_type, actor_ref,
                   valid_from, valid_to, observed_at, extraction_confidence,
                   source_authority, retraction_status, retracted_at,
                   retracted_by_evidence_id, retraction_reason_code,
                   is_retrieval_eligible, embedding_model, embedding_version,
                   embedding_generated_at, normalized_text_sha256, created_at),
    FAMILY f_text (normalized_text, exact_text, source_locator),
    FAMILY f_vec  (embedding)
)
"""

TABLE_DDL: tuple[str, ...] = (SOURCE_ARTIFACTS, EVIDENCE_ITEMS)


# ---------------------------------------------------------------------------
# The ANN vector index — DDL section 5.1, Variant A.
#
# One constant, so switching to variant B (`USING cspann`) or variant C
# (default L2 opclass, which then requires EMBEDDING_NORMALIZATION=L2_UNIT) is
# a one-line edit. `ops/decisions/VECTOR_INDEX_VARIANT.md` records why A.
# ---------------------------------------------------------------------------

VECTOR_INDEX_DDL = (
    "CREATE VECTOR INDEX evidence_embedding_ann_idx "
    "ON evidence_items (user_id, embedding vector_cosine_ops)"
)

# ---------------------------------------------------------------------------
# OPTIONAL index variant R — NOT CREATED. Deliberately.
#
#   CREATE VECTOR INDEX evidence_embedding_ann_active_idx
#       ON evidence_items (user_id, is_retrieval_eligible, embedding vector_cosine_ops);
#
# Probe P8 proved a two-column prefix works on this cluster, so the syntax is
# available. It stays unbuilt because building it now would pre-empt a
# measurement nobody has taken. DDL section 5.3 permits it "only if probe P8
# succeeded AND retrieval evaluation shows the over-fetch in section 5.5 is
# costing recall" — that is a recall evaluation, in Phase 6, against the real
# corpus. Until then the default over-fetch-then-filter path
# (k_raw = greatest(40, 4 * k_final), k_final = 20) is the sanctioned one.
#
# The trade it makes, for whoever runs that evaluation: flipping
# `retraction_status` moves a row between index partitions, which is write
# amplification we would accept because retraction is rare. The benefit is that
# ANN never spends beam budget on retracted vectors, so `LIMIT k` returns k
# usable rows instead of k candidates that then shrink.
# ---------------------------------------------------------------------------


EVIDENCE_INDEX_DDL: tuple[str, ...] = (
    # SELECT * FROM evidence_items
    # WHERE tenant_id=$1 AND user_id=$2 AND artifact_id=$3 ORDER BY observed_at
    # (Memory Trace + State Proof: "what did we extract from this artifact?").
    """
    CREATE INDEX idx_evidence_artifact
        ON evidence_items (tenant_id, user_id, artifact_id, observed_at)
        STORING (evidence_type, retraction_status, extraction_confidence, source_authority)
    """,
    # SELECT * FROM evidence_items
    # WHERE tenant_id=$1 AND user_id=$2 AND evidence_type=$3
    # ORDER BY observed_at DESC LIMIT 50
    # (retrieval fallback when the vector path misses).
    """
    CREATE INDEX idx_evidence_type_observed
        ON evidence_items (tenant_id, user_id, evidence_type, observed_at DESC)
    """,
    # the embedding backfill worker -- SELECT id, normalized_text FROM evidence_items
    # WHERE embedding IS NULL ORDER BY created_at LIMIT 200.
    """
    CREATE INDEX idx_evidence_embedding_backlog
        ON evidence_items (created_at)
        WHERE embedding IS NULL
    """,
    # the embedding cache -- SELECT embedding FROM evidence_items
    # WHERE normalized_text_sha256 = $1 AND embedding_version = $2 LIMIT 1
    # (avoids re-billing Titan for identical normalized text, which is common
    #  across the 18k synthetic decoys and across duplicate forwards).
    """
    CREATE INDEX idx_evidence_text_hash
        ON evidence_items (normalized_text_sha256, embedding_version)
        WHERE embedding IS NOT NULL
    """,
    # the retraction audit view and DDL section 19 test 12's negative control --
    # SELECT * FROM evidence_items
    # WHERE tenant_id=$1 AND user_id=$2 AND retraction_status <> 'ACTIVE'.
    """
    CREATE INDEX idx_evidence_retracted
        ON evidence_items (tenant_id, user_id, retracted_at DESC)
        WHERE retraction_status <> 'ACTIVE'
    """,
    # valid-time overlap checks during conflict candidate detection --
    # SELECT * FROM evidence_items WHERE tenant_id=$1 AND user_id=$2
    #   AND valid_from < $4 AND (valid_to IS NULL OR valid_to > $3).
    """
    CREATE INDEX idx_evidence_valid_time
        ON evidence_items (tenant_id, user_id, valid_from, valid_to)
        WHERE valid_from IS NOT NULL
    """,
)

ARTIFACT_INDEX_DDL: tuple[str, ...] = (
    # SELECT id FROM source_artifacts
    # WHERE tenant_id=$1 AND user_id=$2 AND source_message_id=$3
    # (email-path dedupe when the RFC822 Message-ID is present; partial because
    #  uploads and screenshots have none -- which is precisely why
    #  uq_source_artifacts_content exists as well. DDL section 19 test 1 needs
    #  both: dedupe keyed only on source_message_id deduplicates nothing for an
    #  uploaded .eml, because that column is NULL for every one of them.)
    """
    CREATE UNIQUE INDEX uq_source_artifacts_message_id
        ON source_artifacts (tenant_id, user_id, source_message_id)
        WHERE source_message_id IS NOT NULL
    """,
    # SELECT * FROM source_artifacts
    # WHERE tenant_id=$1 AND user_id=$2 AND sender_domain=$3
    # ORDER BY received_at DESC LIMIT 20
    # (retrieval step 1: "have we heard from this domain before?").
    """
    CREATE INDEX idx_source_artifacts_sender_domain
        ON source_artifacts (tenant_id, user_id, sender_domain, received_at DESC)
        WHERE sender_domain IS NOT NULL
    """,
    # SELECT * FROM source_artifacts
    # WHERE tenant_id=$1 AND user_id=$2 AND thread_ref=$3
    # (forwarded-thread stitching: the hero invoice arrives inside a forward of
    #  the original cancellation thread).
    """
    CREATE INDEX idx_source_artifacts_thread
        ON source_artifacts (tenant_id, user_id, thread_ref)
        WHERE thread_ref IS NOT NULL
    """,
    # the ingestion worker sweep -- SELECT id FROM source_artifacts
    # WHERE parser_status IN ('PENDING','PARSING') ORDER BY created_at LIMIT 25.
    """
    CREATE INDEX idx_source_artifacts_parse_queue
        ON source_artifacts (parser_status, created_at)
        WHERE parser_status IN ('PENDING', 'PARSING')
    """,
)

#: Reverse creation order.
DROP_ORDER: tuple[str, ...] = ("evidence_items", "source_artifacts")


def upgrade() -> None:
    """Create the evidence plane, then the ANN index, then the scalar indexes."""
    for statement in TABLE_DDL:
        op.execute(statement)
    op.execute(VECTOR_INDEX_DDL)
    for statement in ARTIFACT_INDEX_DDL:
        op.execute(statement)
    for statement in EVIDENCE_INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop the ANN index and both tables. Local iteration only."""
    op.execute("DROP INDEX IF EXISTS evidence_embedding_ann_idx CASCADE")
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
