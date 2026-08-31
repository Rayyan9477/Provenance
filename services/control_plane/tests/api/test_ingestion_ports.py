"""The five ingestion ports, driven against a real store and a recording connection.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.18, 8.19, 9.1, 9.3 and 9.4.
- ``db/migrations/versions/0002_evidence_plane.py`` and ``0008`` -- every CHECK
  the statements below have to satisfy.

Why a recording connection rather than a cluster
-------------------------------------------------
Same reason ``test_proposal_submission.py`` and ``test_trigger_evaluation.py``
use one: it makes "did this bind the port?" a unit test instead of an
integration test, and the hermetic lane has no cluster. The **store** is real --
a ``FilesystemObjectStore`` over ``tmp_path`` -- because a store is exactly the
thing a fake would let you get wrong: a no-op ``put`` followed by a working
``head`` is how a row gets written for bytes nobody holds.

What a recording connection cannot prove is that the cluster accepts the
statements. That is stated rather than implied: the assertions here are about
statement *shape* and *ordering*, and a live run is reported separately.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from provenance_contracts.identity import CapabilityBinding
from services.control_plane.app.api.adapters.internal import KernelInternalPort
from services.control_plane.app.api.adapters.write import KernelWritePort
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.api.schemas.internal import (
    IngestArtifactRequest,
    RegisterEvidenceRequest,
)
from services.control_plane.app.api.schemas.public import (
    ArtifactCompleteRequest,
    UploadIntentRequest,
)
from services.control_plane.app.ingestion import blocks as ingestion_blocks
from services.control_plane.app.storage import FilesystemObjectStore, raw_key

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
HERO_EML = REPO_ROOT / "demo" / "artifacts" / "northline-june-invoice.eml"

TENANT = uuid.UUID("018f7a00-0000-7000-8000-000000000001")
USER = uuid.UUID("018f7a01-0000-7000-8000-000000000001")
NOW = datetime(2026, 6, 14, 9, 0, tzinfo=UTC)


def _clock() -> datetime:
    return NOW


def _scope() -> OwnerScope:
    return OwnerScope(tenant_id=TENANT, user_id=USER)


def _binding(artifact_id: uuid.UUID | None, kind: str = "AGENT_RUN") -> CapabilityBinding:
    return CapabilityBinding(
        binding_id=uuid.uuid4(),
        binding_kind=kind,  # type: ignore[arg-type]
        tenant_id=TENANT,
        user_id=USER,
        artifact_id=artifact_id,
        expires_at=datetime(2026, 6, 14, 10, 0, tzinfo=UTC),
        status="ACTIVE",
    )


# ---------------------------------------------------------------------------
# A connection that records statements and answers the two reads
# ---------------------------------------------------------------------------


class _Column:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _Cursor:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn
        self.description: tuple[_Column, ...] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        self._conn.statements.append((" ".join(sql.split()), params))
        columns, rows = self._conn.answer(sql, params)
        self.description = tuple(_Column(name) for name in columns)
        self._rows = rows
        self.rowcount = len(rows) if rows else self._conn.written_rowcount(sql)

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    """Answers ``SELECT``s from a dict of rows; records every statement."""

    def __init__(self, artifact: dict[str, Any] | None = None) -> None:
        self.artifact = artifact
        self.statements: list[tuple[str, Any]] = []
        self.existing_id: uuid.UUID | None = None
        self.existing_evidence: list[tuple[uuid.UUID, str]] = []
        self.insert_rowcount = 1

    @property
    def kinds(self) -> list[str]:
        out = []
        for sql, _ in self.statements:
            if "INSERT INTO source_artifacts" in sql:
                out.append("insert_artifact")
            elif "UPDATE source_artifacts" in sql:
                out.append("update_artifact")
            elif "INSERT INTO agent_runs" in sql:
                out.append("insert_agent_run")
            elif "INSERT INTO evidence_items" in sql:
                out.append("insert_evidence")
            elif "FROM evidence_items" in sql:
                out.append("select_evidence")
            elif "FROM source_artifacts" in sql:
                out.append("select_artifact")
            else:
                out.append(f"other:{sql[:32]}")
        return out

    def answer(self, sql: str, params: Any) -> tuple[list[str], list[tuple[Any, ...]]]:
        flat = " ".join(sql.split())
        if flat.startswith("SELECT id FROM source_artifacts"):
            return (["id"], [(self.existing_id,)] if self.existing_id else [])
        if flat.startswith("SELECT id, tenant_id, user_id, source_type"):
            if self.artifact is None:
                return ([], [])
            columns = list(self.artifact)
            return (columns, [tuple(self.artifact[c] for c in columns)])
        if "FROM evidence_items" in flat:
            return (["id", "text_sha"], list(self.existing_evidence))
        return ([], [])

    def written_rowcount(self, sql: str) -> int:
        return self.insert_rowcount if "INSERT" in sql or "UPDATE" in sql else 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)


class FakeSource:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    def connection(self) -> Any:
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> FakeConnection:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def _internal(conn: FakeConnection, store: FilesystemObjectStore) -> KernelInternalPort:
    return KernelInternalPort(
        FakeSource(conn),
        kernel_pool=object(),
        read=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        sink=object(),  # type: ignore[arg-type]
        clock=_clock,
        objects=store,
    )


def _write(conn: FakeConnection, store: FilesystemObjectStore) -> KernelWritePort:
    return KernelWritePort(
        FakeSource(conn),
        kernel_pool=object(),
        read=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        clock=_clock,
        objects=store,
    )


def _hero() -> bytes:
    assert HERO_EML.is_file(), HERO_EML
    return HERO_EML.read_bytes()


def _ingest_request(key: str, data: bytes, **overrides: Any) -> IngestArtifactRequest:
    body: dict[str, Any] = {
        "alias_hash": "b64:9tKp3f0Zx1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q=",
        "s3_bucket": "provenance-inbound-us-east-1",
        "s3_key": key,
        "received_at": NOW,
        "size_bytes": len(data),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "ses_verdicts": {
            "spf": "PASS",
            "dkim": "PASS",
            "dmarc": "PASS",
            "spam": "PASS",
            "virus": "PASS",
        },
    }
    body.update(overrides)
    return IngestArtifactRequest(**body)


# ---------------------------------------------------------------------------
# 9.1
# ---------------------------------------------------------------------------


async def test_ingest_copies_the_bytes_and_stores_the_key_it_minted(tmp_path: Path) -> None:
    """The whole reason section 9.1 was blocked, exercised end to end.

    The caller's ``ses/...`` key is read and never stored; the row carries the
    key this server minted, and the bytes are at it.
    """
    store = FilesystemObjectStore(tmp_path)
    data = _hero()
    inbound = "ses/2026/06/14/0100018f9e70abcd-3f8a1c9d"
    await store.put(inbound, data, content_type="message/rfc822")

    conn = FakeConnection()
    row = await _internal(conn, store).ingest_artifact(
        _binding(None, kind="INGEST_JOB"), _ingest_request(inbound, data)
    )

    assert row["status"] == "QUEUED"
    assert row["duplicate_of"] is None
    assert row["parser_status"] == "PARSED"
    assert row["block_count"] >= 3
    assert row["interpretation"]["worker_deployed"] is False

    artifact_id = uuid.UUID(row["artifact_id"])
    expected = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id)
    assert await store.get(expected) == data, "the bytes are not under the minted key"

    inserts = [p for sql, p in conn.statements if "INSERT INTO source_artifacts" in sql]
    assert len(inserts) == 1
    written = inserts[0]
    assert written["s3_key"] == expected
    assert written["s3_key"] != inbound
    assert written["s3_key"].startswith("raw/")
    assert written["content_sha256"] == hashlib.sha256(data).digest()
    assert len(written["content_sha256"]) == 32
    assert written["source_type"] == "EMAIL_INBOUND"
    assert written["parser_status"] == "PARSED"
    assert written["parser_version"] is not None, "ck_source_artifacts_parsed_has_version"
    assert written["parser_metadata"].obj["blocks"], "the row carries no parsed blocks"
    # Section 9.1 step 3: SPF/DKIM/DMARC are preserved rather than rejected.
    assert written["parser_metadata"].obj["ses_verdicts"]["spf"] == "PASS"
    # The message is the authority on its own headers.
    assert written["subject"] == "Invoice for June service"
    assert written["sender_domain"] == "northlinefiber.example"

    # Section 9.1 step 4's dedupe order: `source_message_id` first -- the .eml
    # carries one -- then `content_sha256`. Two reads, then the writes.
    assert conn.kinds == [
        "select_artifact",
        "select_artifact",
        "insert_artifact",
        "insert_agent_run",
    ], conn.kinds
    reads = [p for sql, p in conn.statements if sql.startswith("SELECT id FROM source_artifacts")]
    assert "source_message_id" in reads[0]
    assert reads[0]["source_message_id"] == "<northline-june-invoice@northlinefiber.example>"
    assert "content_sha256" in reads[1]


async def test_ingest_refuses_when_the_declared_digest_is_not_the_stored_one(
    tmp_path: Path,
) -> None:
    """A worker's declaration is a claim; the digest is a measurement.

    ``uq_source_artifacts_content`` deduplicates on it, so accepting a wrong
    one lets the same message in twice or collides two different ones.
    """
    store = FilesystemObjectStore(tmp_path)
    data = _hero()
    inbound = "ses/2026/06/14/wrong-digest"
    await store.put(inbound, data, content_type="message/rfc822")
    conn = FakeConnection()

    with pytest.raises(ApiError) as exc:
        await _internal(conn, store).ingest_artifact(
            _binding(None, kind="INGEST_JOB"),
            _ingest_request(inbound, data, content_sha256="0" * 64),
        )
    assert exc.value.code is ErrorCode.ARTIFACT_HASH_MISMATCH
    assert conn.statements == [], "a row was written for bytes that failed their check"


async def test_ingest_refuses_when_the_worker_named_bytes_that_are_not_there(
    tmp_path: Path,
) -> None:
    """No row is written for an object nobody can read.

    Synthesising a ``raw/`` key here would satisfy the CHECK and store a
    locator for bytes nobody wrote; the first symptom is a download that 404s
    months later against a row that looks perfect.
    """
    store = FilesystemObjectStore(tmp_path)
    conn = FakeConnection()
    with pytest.raises(ApiError) as exc:
        await _internal(conn, store).ingest_artifact(
            _binding(None, kind="INGEST_JOB"), _ingest_request("ses/2026/06/14/absent", b"x")
        )
    assert exc.value.code is ErrorCode.ARTIFACT_OBJECT_MISSING
    assert conn.statements == []


async def test_ingest_deduplicates_and_opens_no_second_run(tmp_path: Path) -> None:
    """Duplicate bytes never create duplicate business state.

    And no new ``agent_runs`` row: interpreting one message twice is how one
    artifact becomes two beliefs.
    """
    store = FilesystemObjectStore(tmp_path)
    data = _hero()
    inbound = "ses/2026/06/14/dupe"
    await store.put(inbound, data, content_type="message/rfc822")

    conn = FakeConnection()
    conn.existing_id = uuid.UUID("018f9e80-0000-7000-8000-0000000000ff")
    row = await _internal(conn, store).ingest_artifact(
        _binding(None, kind="INGEST_JOB"), _ingest_request(inbound, data)
    )
    assert row["status"] == "DUPLICATE"
    assert row["duplicate_of"] == str(conn.existing_id)
    assert row["agent_run_id"] is None
    assert "insert_agent_run" not in conn.kinds
    assert "insert_artifact" not in conn.kinds


# ---------------------------------------------------------------------------
# 9.3
# ---------------------------------------------------------------------------


def _parsed_artifact_row(artifact_id: uuid.UUID, data: bytes) -> dict[str, Any]:
    parse = ingestion_blocks.parse_artifact(
        artifact_id=artifact_id, mime_type="message/rfc822", data=data
    )
    return {
        "id": artifact_id,
        "tenant_id": TENANT,
        "user_id": USER,
        "source_type": "EMAIL_INBOUND",
        "s3_bucket": "local-filesystem",
        "s3_key": raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "mime_type": "message/rfc822",
        "source_message_id": None,
        "sender": "billing@northlinefiber.example",
        "recipient": "alex.rivera@example.invalid",
        "subject": "Invoice for June service",
        "received_at": NOW,
        "parser_status": "PARSED",
        "parser_version": ingestion_blocks.PARSER_VERSION,
        "parser_metadata": ingestion_blocks.parser_metadata_value(parse),
        "ses_verdicts": {"spf": "PASS", "dkim": "PASS", "dmarc": "PASS"},
    }


async def test_artifact_content_returns_the_blocks_the_parser_stored(tmp_path: Path) -> None:
    artifact_id = uuid.uuid4()
    data = _hero()
    conn = FakeConnection(_parsed_artifact_row(artifact_id, data))
    row = await _internal(conn, FilesystemObjectStore(tmp_path)).artifact_content(
        _binding(artifact_id)
    )
    assert row is not None
    assert row["artifact_id"] == str(artifact_id)
    assert row["parser_version"] == ingestion_blocks.PARSER_VERSION
    kinds = {block["kind"] for block in row["content_blocks"]}
    assert {"SUBJECT", "HEADER", "BODY"} <= kinds, kinds
    body = " ".join(b["text"] for b in row["content_blocks"] if b["kind"] == "BODY")
    assert "USD 186.00" in body
    for block in row["content_blocks"]:
        assert block["content_sha256"] == hashlib.sha256(block["text"].encode("utf-8")).hexdigest()
        assert block["source_locator"]["block_id"] == block["block_id"]


async def test_artifact_content_refuses_a_row_that_claims_parsed_with_no_output(
    tmp_path: Path,
) -> None:
    """The state every seeded artifact is in, and it must not read as empty.

    An empty ``content_blocks`` would tell the graph the document has no text.
    The graph would then extract nothing and report success, and the artifact
    would look processed forever.
    """
    artifact_id = uuid.uuid4()
    row = _parsed_artifact_row(artifact_id, _hero())
    row["parser_metadata"] = None
    row["parser_version"] = "seed-1.0.0"
    conn = FakeConnection(row)

    with pytest.raises(ApiError) as exc:
        await _internal(conn, FilesystemObjectStore(tmp_path)).artifact_content(
            _binding(artifact_id)
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILED
    assert exc.value.http_status == 409
    assert exc.value.details["reason"] == "PARSER_OUTPUT_UNAVAILABLE"


async def test_artifact_content_can_withhold_quoted_history(tmp_path: Path) -> None:
    """Section 9.3's ``include_quoted_history``, and the tag it filters on."""
    artifact_id = uuid.uuid4()
    raw = (
        b"Subject: Re: cancellation\r\nFrom: alex@example.invalid\r\n"
        b"To: billing@northlinefiber.example\r\n\r\n"
        b"Thanks, that matches my records.\n\n"
        b"> We will refund USD 186.00 within 30 days.\n"
    )
    row = _parsed_artifact_row(artifact_id, raw)
    row["parser_metadata"] = ingestion_blocks.parser_metadata_value(
        ingestion_blocks.parse_artifact(
            artifact_id=artifact_id, mime_type="message/rfc822", data=raw
        )
    )
    conn = FakeConnection(row)
    port = _internal(conn, FilesystemObjectStore(tmp_path))

    with_quoted = await port.artifact_content(_binding(artifact_id))
    without = await port.artifact_content(_binding(artifact_id), include_quoted_history=False)
    assert with_quoted is not None and without is not None
    assert any(b["kind"] == "QUOTED_HISTORY" for b in with_quoted["content_blocks"])
    assert all(b["kind"] != "QUOTED_HISTORY" for b in without["content_blocks"])
    assert len(without["content_blocks"]) < len(with_quoted["content_blocks"])


# ---------------------------------------------------------------------------
# 9.4
# ---------------------------------------------------------------------------


async def test_register_evidence_admits_a_real_quotation_and_discloses_the_absent_embedding(
    tmp_path: Path,
) -> None:
    artifact_id = uuid.uuid4()
    data = _hero()
    conn = FakeConnection(_parsed_artifact_row(artifact_id, data))
    payload = RegisterEvidenceRequest(
        candidates=[
            {
                "client_ref": "c1",
                "evidence_type": "AMOUNT_ASSERTION",
                "block_id": "blk_0003",
                "exact_text": "Amount due USD 186.00",
                "normalized_text": "[amount=186.00 USD] invoice line for June",
                "observed_at": NOW,
                "extraction_confidence": "0.9700",
            }
        ]
    )
    row = await _internal(conn, FilesystemObjectStore(tmp_path)).register_evidence(
        _binding(artifact_id), payload
    )
    assert row["created_count"] == 1
    assert row["deduplicated_count"] == 0
    assert row["evidence"][0]["client_ref"] == "c1"
    assert row["evidence"][0]["created"] is True
    # Steps 3 and 4, disclosed rather than faked.
    assert row["evidence"][0]["source_authority"] is None
    assert row["evidence"][0]["source_class"] == "PROVIDER_SYSTEM_NOTICE"
    assert row["embedding_status"] == "NOT_COMPUTED"
    assert "VECTOR(1024)" in row["embedding_status_reason"]

    written = [p for sql, p in conn.statements if "INSERT INTO evidence_items" in sql]
    assert len(written) == 1
    assert written[0]["embedding"] is None
    assert written[0]["source_locator"].obj["block_id"] == "blk_0003"
    assert written[0]["retraction_status"] == "ACTIVE"


async def test_register_evidence_refuses_an_invented_quotation(tmp_path: Path) -> None:
    """The deterministic defence, and nothing reaches the table."""
    artifact_id = uuid.uuid4()
    conn = FakeConnection(_parsed_artifact_row(artifact_id, _hero()))
    payload = RegisterEvidenceRequest(
        candidates=[
            {
                "client_ref": "c1",
                "evidence_type": "AMOUNT_ASSERTION",
                "block_id": "blk_0003",
                "exact_text": "Amount due USD 286.00",
                "normalized_text": "[amount=286.00 USD]",
                "observed_at": NOW,
                "extraction_confidence": "0.9900",
            }
        ]
    )
    with pytest.raises(ApiError) as exc:
        await _internal(conn, FilesystemObjectStore(tmp_path)).register_evidence(
            _binding(artifact_id), payload
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILED
    # Section 9.4 step 2 names the field a client branches on, and it is the
    # specific reason rather than the outer code.
    assert exc.value.details["reason"] == "SPAN_NOT_IN_BLOCK"
    assert exc.value.details["client_ref"] == "c1"
    assert "insert_evidence" not in conn.kinds


async def test_register_evidence_refuses_a_block_the_artifact_does_not_have(
    tmp_path: Path,
) -> None:
    artifact_id = uuid.uuid4()
    conn = FakeConnection(_parsed_artifact_row(artifact_id, _hero()))
    payload = RegisterEvidenceRequest(
        candidates=[
            {
                "client_ref": "c1",
                "evidence_type": "AMOUNT_ASSERTION",
                "block_id": "b2",
                "exact_text": "Amount due USD 186.00",
                "normalized_text": "[amount=186.00 USD]",
                "observed_at": NOW,
                "extraction_confidence": "0.9700",
            }
        ]
    )
    with pytest.raises(ApiError) as exc:
        await _internal(conn, FilesystemObjectStore(tmp_path)).register_evidence(
            _binding(artifact_id), payload
        )
    assert exc.value.code is ErrorCode.PROPOSAL_FOREIGN_PROVENANCE
    assert exc.value.details["unknown_block_ids"] == ["b2"]
    assert "insert_evidence" not in conn.kinds


async def test_register_evidence_refuses_when_the_artifact_was_never_parsed(
    tmp_path: Path,
) -> None:
    """No blocks, no guard, no row. Every seeded artifact reaches this branch."""
    artifact_id = uuid.uuid4()
    row = _parsed_artifact_row(artifact_id, _hero())
    row["parser_metadata"] = None
    conn = FakeConnection(row)
    payload = RegisterEvidenceRequest(
        candidates=[
            {
                "client_ref": "c1",
                "evidence_type": "AMOUNT_ASSERTION",
                "block_id": "blk_0003",
                "exact_text": "Amount due USD 186.00",
                "normalized_text": "[amount=186.00 USD]",
                "observed_at": NOW,
                "extraction_confidence": "0.9700",
            }
        ]
    )
    with pytest.raises(ApiError) as exc:
        await _internal(conn, FilesystemObjectStore(tmp_path)).register_evidence(
            _binding(artifact_id), payload
        )
    assert exc.value.details["reason"] == "PROVENANCE_UNCHECKABLE"
    assert "insert_evidence" not in conn.kinds


# ---------------------------------------------------------------------------
# 8.18 and 8.19
# ---------------------------------------------------------------------------


async def test_upload_intent_mints_the_key_and_creates_the_pending_row(
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    conn = FakeConnection()
    data = _hero()
    payload = UploadIntentRequest(
        filename="northline-june-invoice.eml",
        mime_type="message/rfc822",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    row = await _write(conn, store).upload_intent(_scope(), payload)

    artifact_id = uuid.UUID(row["artifact_id"])
    assert row["s3_key"] == raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id)
    assert row["upload_transport"] == "LOCAL_FILESYSTEM"
    assert row["http_method"] == "PUT"
    # The filename never becomes part of the key.
    assert "northline-june-invoice.eml" not in row["s3_key"]

    inserts = [p for sql, p in conn.statements if "INSERT INTO source_artifacts" in sql]
    assert len(inserts) == 1
    assert inserts[0]["parser_status"] == "PENDING"
    assert inserts[0]["parser_version"] is None
    assert inserts[0]["parser_metadata"] is None, (
        "an empty parser document at creation erases the difference between "
        "'not parsed' and 'parsed and empty'"
    )
    assert inserts[0]["source_type"] == "UPLOAD_EML"


async def test_upload_intent_refuses_without_a_digest_and_names_the_column(
    tmp_path: Path,
) -> None:
    conn = FakeConnection()
    payload = UploadIntentRequest(
        filename="x.eml", mime_type="message/rfc822", size_bytes=10, sha256=None
    )
    with pytest.raises(ApiError) as exc:
        await _write(conn, FilesystemObjectStore(tmp_path)).upload_intent(_scope(), payload)
    assert exc.value.details["reason"] == "SHA256_REQUIRED"
    assert "content_sha256" in exc.value.details["detail"]
    assert conn.statements == [] or all("INSERT" not in s for s, _ in conn.statements)


async def test_complete_verifies_the_stored_object_then_parses_and_opens_a_run(
    tmp_path: Path,
) -> None:
    """Sections 8.19 steps 1-3 against the store, then step 5's durable half."""
    store = FilesystemObjectStore(tmp_path)
    data = _hero()
    artifact_id = uuid.uuid4()
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id)
    await store.put(key, data, content_type="message/rfc822")

    row_data = _parsed_artifact_row(artifact_id, data)
    row_data["parser_status"] = "PENDING"
    row_data["parser_version"] = None
    row_data["parser_metadata"] = None
    conn = FakeConnection(row_data)

    row = await _write(conn, store).complete_artifact(
        _scope(), artifact_id, ArtifactCompleteRequest(sha256=hashlib.sha256(data).hexdigest())
    )
    assert row is not None
    assert row["status"] == "QUEUED"
    assert row["parser_status"] == "PARSED"
    assert row["block_count"] >= 3
    assert row["interpretation"]["worker_deployed"] is False
    assert row["poll"]["artifact_url"].endswith(str(artifact_id))

    updates = [p for sql, p in conn.statements if "UPDATE source_artifacts" in sql]
    assert len(updates) == 1
    assert updates[0]["parser_status"] == "PARSED"
    assert updates[0]["parser_metadata"].obj["blocks"]
    assert "insert_agent_run" in conn.kinds


async def test_complete_refuses_when_the_bytes_were_never_uploaded(tmp_path: Path) -> None:
    artifact_id = uuid.uuid4()
    row_data = _parsed_artifact_row(artifact_id, _hero())
    row_data["parser_status"] = "PENDING"
    conn = FakeConnection(row_data)
    with pytest.raises(ApiError) as exc:
        await _write(conn, FilesystemObjectStore(tmp_path)).complete_artifact(
            _scope(), artifact_id, ArtifactCompleteRequest()
        )
    assert exc.value.code is ErrorCode.ARTIFACT_OBJECT_MISSING
    assert "update_artifact" not in conn.kinds


async def test_complete_refuses_bytes_that_do_not_match_the_declared_digest(
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    artifact_id = uuid.uuid4()
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id)
    await store.put(key, b"different bytes entirely", content_type="message/rfc822")

    row_data = _parsed_artifact_row(artifact_id, _hero())
    row_data["parser_status"] = "PENDING"
    conn = FakeConnection(row_data)

    with pytest.raises(ApiError) as exc:
        await _write(conn, store).complete_artifact(
            _scope(),
            artifact_id,
            ArtifactCompleteRequest(sha256=hashlib.sha256(_hero()).hexdigest()),
        )
    assert exc.value.code in (
        ErrorCode.ARTIFACT_HASH_MISMATCH,
        ErrorCode.ARTIFACT_SIZE_MISMATCH,
    )
    assert "update_artifact" not in conn.kinds


async def test_complete_of_an_already_completed_artifact_is_a_conflict(
    tmp_path: Path,
) -> None:
    """A second completion must not open a second interpretation run."""
    store = FilesystemObjectStore(tmp_path)
    data = _hero()
    artifact_id = uuid.uuid4()
    await store.put(
        raw_key(tenant_id=TENANT, user_id=USER, artifact_id=artifact_id),
        data,
        content_type="message/rfc822",
    )
    conn = FakeConnection(_parsed_artifact_row(artifact_id, data))  # already PARSED
    with pytest.raises(ApiError) as exc:
        await _write(conn, store).complete_artifact(
            _scope(), artifact_id, ArtifactCompleteRequest()
        )
    assert exc.value.code is ErrorCode.ARTIFACT_ALREADY_COMPLETED
    assert "insert_agent_run" not in conn.kinds


async def test_complete_of_an_artifact_this_scope_does_not_own_is_absence(
    tmp_path: Path,
) -> None:
    """``None`` means "no such row for this scope", which the route maps to 404."""
    conn = FakeConnection(None)
    assert (
        await _write(conn, FilesystemObjectStore(tmp_path)).complete_artifact(
            _scope(), uuid.uuid4(), ArtifactCompleteRequest()
        )
        is None
    )
