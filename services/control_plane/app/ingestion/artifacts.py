"""The ``source_artifacts`` row, its dedupe, and the run that will read it.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.18, 8.19 and 9.1.
- ``specs/10_DATABASE_DDL.md`` section 12 -- the app holds ``INSERT, UPDATE``
  on ``source_artifacts`` and on ``agent_runs``. Neither table is in
  ``tools/write_path_lint.CANONICAL_TABLES``: the Memory Kernel holds only
  ``SELECT`` on them, so they have no Kernel-only write to protect and these
  statements are outside write rules ``W1``/``W2`` rather than exceptions to
  them.
- ``db/migrations/versions/0002_evidence_plane.py`` -- every CHECK below is
  quoted from the applied DDL.

The key, and the one thing this module will not do
----------------------------------------------------
``ck_source_artifacts_s3_key_shape`` is ``CHECK (s3_key LIKE 'raw/%')`` and
section 8.18 fixes the layout at
``raw/{tenant_id}/{user_id}/{artifact_id}/original``. Section 9.1's request
body carries the key the SES worker wrote -- ``ses/2026/06/05/...`` in the
spec's own example -- and the database refuses it.

So the row can only be written **after** the bytes have been copied into that
prefix, and :func:`~services.control_plane.app.storage.raw_key` is the only
function that mints one. Synthesising a ``raw/`` key without the copy is the
option this module refuses: it satisfies the CHECK and stores a locator for
bytes nobody wrote, and the first symptom is a download that 404s months later
against a row that looks perfect. Every caller here therefore passes a key it
has already put bytes at, and ``ingest_artifact`` verifies the copy by digest
before the INSERT.

Dedupe, in the order section 9.1 gives
---------------------------------------
``source_message_id`` first, then ``content_sha256``. Both have unique
constraints and they catch different things:
``uq_source_artifacts_message_id`` is partial on ``source_message_id IS NOT
NULL`` -- an uploaded ``.eml`` has none -- and
``uq_source_artifacts_content`` is ``(tenant_id, user_id, content_sha256,
source_type)``, which catches the same bytes arriving twice by any route.
Duplicate bytes never create duplicate business state.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg.types.json import Jsonb

__all__ = [
    "AGENT_RUN_INSERT_SQL",
    "ARTIFACT_BY_CONTENT_SQL",
    "ARTIFACT_BY_MESSAGE_ID_SQL",
    "ARTIFACT_FOR_OWNER_SQL",
    "ARTIFACT_INSERT_SQL",
    "ARTIFACT_PARSE_UPDATE_SQL",
    "DEFAULT_MODEL_ROUTE",
    "INGESTION_GRAPH_NAME",
    "INGESTION_GRAPH_VERSION",
    "INTERPRETATION_DISPATCH",
    "ArtifactRow",
    "artifact_insert_params",
    "existing_artifact_id",
    "insert_artifact",
    "load_artifact",
    "mark_parsed",
    "open_agent_run",
    "sender_domain",
]

#: ``ck_agent_runs_graph`` admits exactly four values and this is the one an
#: ingestion run carries. ``agents/runtime/state.GRAPH_NAME_INGESTION`` holds
#: the same string; it is repeated rather than imported because the control
#: plane does not depend on the agent runtime, and
#: ``agents/runtime/tests/test_graph_names_match_the_schema.py`` pins the other
#: copy against the migration.
INGESTION_GRAPH_NAME: Final[str] = "ingestion"
INGESTION_GRAPH_VERSION: Final[str] = "1.0.0"

#: ``agent_runs.model_route`` is ``NOT NULL`` and is the column that makes the
#: submission's model disclosure checkable against persisted state rather than
#: against a README (``CANONICAL_DECISIONS.md`` -> *Disclosure*). The default
#: below is the canon pair; ``build_runtime`` overrides it from ``Settings`` so
#: a deployment that configured other ids records the ids it configured.
DEFAULT_MODEL_ROUTE: Final[Mapping[str, str]] = {
    "tier_e": "gemini-3.5-flash-lite",
    "tier_r": "gemini-3.7-flash",
    "embeddings": "gemini-embedding-2",
}

#: What "QUEUED" means in this build, stated rather than implied.
#:
#: Section 8.19 step 5 and section 9.1 step 5 both say "invoke AgentCore
#: Runtime asynchronously". No interpretation worker is deployed here (Phase 13
#: is not started), and section 9.1 step 5's ``artifact.received.v1`` outbox
#: event is not written either: ``outbox_events`` is in
#: ``tools/write_path_lint.CANONICAL_TABLES`` and write rule ``W1`` makes its
#: INSERT Kernel-only, so the app holds no grant to author one. What genuinely
#: exists is the ``agent_runs`` row -- durable, capability-bearing, and the
#: thing a sweeper would pick up. The response carries this so a client is
#: never told a pipeline is running when none is.
INTERPRETATION_DISPATCH: Final[Mapping[str, Any]] = {
    "queue": "agent_runs",
    "worker_deployed": False,
    "detail": (
        "the agent_runs capability row is written and is what a worker would sweep; "
        "no interpretation worker is deployed in this build, and no artifact.received.v1 "
        "outbox event is authored here because write rule W1 makes outbox_events INSERT "
        "Kernel-only."
    ),
}

#: How long a run's capability lives. Section 3.3's window; short, because the
#: capability is the only thing standing between a leaked run id and another
#: user's artifact.
AGENT_RUN_TTL: Final[timedelta] = timedelta(minutes=15)

ARTIFACT_INSERT_SQL: Final[str] = """
INSERT INTO source_artifacts (
    id, tenant_id, user_id, source_type, s3_bucket, s3_key, content_sha256, size_bytes,
    mime_type, source_message_id, sender, sender_domain, recipient, subject, thread_ref,
    received_at, event_time, parser_status, parser_version, parser_metadata, ses_verdicts,
    created_at, updated_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(source_type)s, %(s3_bucket)s, %(s3_key)s,
    %(content_sha256)s, %(size_bytes)s, %(mime_type)s, %(source_message_id)s, %(sender)s,
    %(sender_domain)s, %(recipient)s, %(subject)s, %(thread_ref)s, %(received_at)s,
    %(event_time)s, %(parser_status)s, %(parser_version)s, %(parser_metadata)s,
    %(ses_verdicts)s, %(created_at)s, %(created_at)s
)
ON CONFLICT DO NOTHING
"""

#: Section 9.1 step 4's second dedupe, and section 8.19 step 4's only one.
ARTIFACT_BY_CONTENT_SQL: Final[str] = """
SELECT id FROM source_artifacts
WHERE tenant_id = %(tenant_id)s
  AND user_id = %(user_id)s
  AND content_sha256 = %(content_sha256)s
  AND source_type = %(source_type)s
"""

#: Section 9.1 step 4's first dedupe. Partial-unique in the schema, so this is
#: only asked when the message actually has an id.
ARTIFACT_BY_MESSAGE_ID_SQL: Final[str] = """
SELECT id FROM source_artifacts
WHERE tenant_id = %(tenant_id)s
  AND user_id = %(user_id)s
  AND source_message_id = %(source_message_id)s
"""

#: Scoped by owner in the statement, not by the caller. The ``None`` this
#: returns means "no such row **for this scope**", which the route maps to a
#: typed 404 and never to a 403 (section 1.7).
ARTIFACT_FOR_OWNER_SQL: Final[str] = """
SELECT id, tenant_id, user_id, source_type, s3_bucket, s3_key,
       encode(content_sha256, 'hex') AS content_sha256, size_bytes, mime_type,
       source_message_id, sender, recipient, subject, received_at,
       parser_status, parser_version, parser_metadata, ses_verdicts
FROM source_artifacts
WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(artifact_id)s
"""

#: The parse result. ``parser_status`` and ``parser_version`` move together
#: because ``ck_source_artifacts_parsed_has_version`` requires it.
ARTIFACT_PARSE_UPDATE_SQL: Final[str] = """
UPDATE source_artifacts
   SET parser_status = %(parser_status)s,
       parser_version = %(parser_version)s,
       parser_metadata = %(parser_metadata)s,
       updated_at = %(updated_at)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(artifact_id)s
"""

#: The capability row a graph presents to reach section 9.2 onwards. Written
#: before the graph is invoked, on purpose: a run that dies must leave a row
#: saying it started, and a row written only on success is a ledger that
#: records only the runs that went well.
AGENT_RUN_INSERT_SQL: Final[str] = """
INSERT INTO agent_runs (
    id, tenant_id, user_id, trace_id, graph_name, graph_version, model_route,
    memory_mode, is_counterfactual, status, started_at, expires_at,
    input_artifact_id, allowed_case_ids
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(trace_id)s, %(graph_name)s, %(graph_version)s,
    %(model_route)s, 'ON', false, 'RUNNING', %(started_at)s, %(expires_at)s,
    %(input_artifact_id)s, NULL
)
"""


def sender_domain(sender: str | None) -> str | None:
    """The domain part of an RFC 5322 address, lowercased.

    ``idx_source_artifacts_sender_domain`` backs retrieval step 1 -- "have we
    heard from this domain before?" -- so the column is derived here rather
    than asked of the caller. A caller-supplied domain is a caller-supplied
    identity signal, and identity signals outrank vector similarity
    (``CANONICAL_DECISIONS.md`` -> *Identity order*).
    """
    if not sender:
        return None
    address = sender.rsplit("<", 1)[-1].rstrip(">").strip()
    _, _, domain = address.rpartition("@")
    return domain.lower() or None


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    """One ``source_artifacts`` row, before it is written.

    ``content_sha256`` is hex here and ``bytes`` on the wire to the database:
    ``ck_source_artifacts_sha_len`` is ``length(content_sha256) = 32``, which is
    32 *bytes*, and passing the 64-character hex string satisfies neither the
    length check nor the column type.
    """

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    source_type: str
    s3_bucket: str
    s3_key: str
    content_sha256_hex: str
    size_bytes: int
    mime_type: str
    received_at: datetime
    created_at: datetime
    parser_status: str
    parser_version: str | None
    parser_metadata: Mapping[str, Any] | None = None
    ses_verdicts: Mapping[str, Any] | None = None
    source_message_id: str | None = None
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    thread_ref: str | None = None
    event_time: datetime | None = None


def artifact_insert_params(row: ArtifactRow) -> dict[str, Any]:
    """Every bind for :data:`ARTIFACT_INSERT_SQL`."""
    return {
        "id": row.artifact_id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "source_type": row.source_type,
        "s3_bucket": row.s3_bucket,
        "s3_key": row.s3_key,
        "content_sha256": bytes.fromhex(row.content_sha256_hex),
        "size_bytes": row.size_bytes,
        "mime_type": row.mime_type,
        "source_message_id": row.source_message_id,
        "sender": row.sender,
        "sender_domain": sender_domain(row.sender),
        "recipient": row.recipient,
        "subject": row.subject,
        "thread_ref": row.thread_ref,
        "received_at": row.received_at,
        "event_time": row.event_time,
        "parser_status": row.parser_status,
        "parser_version": row.parser_version,
        # `parser_metadata IS NULL` is the state that means "no parser output",
        # and `read_normalized_content` reports it as PARSER_OUTPUT_UNAVAILABLE
        # rather than as an empty document. Writing `{}` here would erase that
        # distinction at the moment of creation.
        "parser_metadata": None
        if row.parser_metadata is None
        else Jsonb(dict(row.parser_metadata)),
        "ses_verdicts": None if row.ses_verdicts is None else Jsonb(dict(row.ses_verdicts)),
        "created_at": row.created_at,
    }


async def existing_artifact_id(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    content_sha256_hex: str,
    source_type: str,
    source_message_id: str | None,
) -> uuid.UUID | None:
    """Section 9.1 step 4: ``source_message_id`` first, then ``content_sha256``."""
    if source_message_id:
        found = await _scalar(
            conn,
            ARTIFACT_BY_MESSAGE_ID_SQL,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "source_message_id": source_message_id,
            },
        )
        if found is not None:
            return found
    return await _scalar(
        conn,
        ARTIFACT_BY_CONTENT_SQL,
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "content_sha256": bytes.fromhex(content_sha256_hex),
            "source_type": source_type,
        },
    )


async def insert_artifact(conn: Any, row: ArtifactRow) -> int:
    """Admit the row. Returns how many rows the statement wrote.

    ``0`` means the row was already there -- the retry path -- and is returned
    rather than swallowed, because the caller then knows it is looking at a
    duplicate rather than at a fresh artifact.
    """
    async with conn.cursor() as cur:
        await cur.execute(ARTIFACT_INSERT_SQL, artifact_insert_params(row))
        return int(cur.rowcount)


async def load_artifact(
    conn: Any, *, tenant_id: uuid.UUID, user_id: uuid.UUID, artifact_id: uuid.UUID
) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            ARTIFACT_FOR_OWNER_SQL,
            {"tenant_id": tenant_id, "user_id": user_id, "artifact_id": artifact_id},
        )
        rows = await cur.fetchall()
        if not rows:
            return None
        columns = [column.name for column in cur.description or ()]
    return dict(zip(columns, rows[0], strict=True))


async def mark_parsed(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    parser_status: str,
    parser_version: str | None,
    parser_metadata: Mapping[str, Any] | None,
    updated_at: datetime,
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            ARTIFACT_PARSE_UPDATE_SQL,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "artifact_id": artifact_id,
                "parser_status": parser_status,
                "parser_version": parser_version,
                "parser_metadata": (
                    None if parser_metadata is None else Jsonb(dict(parser_metadata))
                ),
                "updated_at": updated_at,
            },
        )
        return int(cur.rowcount)


async def open_agent_run(
    conn: Any,
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    trace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    model_route: Mapping[str, str],
    started_at: datetime,
    ttl: timedelta = AGENT_RUN_TTL,
) -> dict[str, Any]:
    """Create the capability the interpretation run will present.

    Returns the identifiers sections 8.19 and 9.1 put in their responses. The
    row is the queue: nothing else records that this artifact is waiting to be
    interpreted, and ``idx_source_artifacts_parse_queue`` plus this row are
    what a worker would sweep.
    """
    expires_at = started_at + ttl
    async with conn.cursor() as cur:
        await cur.execute(
            AGENT_RUN_INSERT_SQL,
            {
                "id": run_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "trace_id": trace_id,
                "graph_name": INGESTION_GRAPH_NAME,
                "graph_version": INGESTION_GRAPH_VERSION,
                "model_route": Jsonb(dict(model_route)),
                "started_at": started_at,
                "expires_at": expires_at,
                "input_artifact_id": artifact_id,
            },
        )
    return {
        "agent_run_id": str(run_id),
        "trace_id": str(trace_id),
        "capability_expires_at": expires_at,
    }


async def _scalar(conn: Any, sql: str, params: Mapping[str, Any]) -> uuid.UUID | None:
    async with conn.cursor() as cur:
        await cur.execute(sql, dict(params))
        rows = await cur.fetchall()
    if not rows:
        return None
    value = rows[0][0]
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
