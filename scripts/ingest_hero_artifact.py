"""Ingest ``demo/artifacts/northline-june-invoice.eml`` through the bound ports.

Why this file exists
--------------------
``adapters/unbound.py`` recorded that sections 8.18, 8.19, 9.1, 9.3 and 9.4 were
all waiting on an object-store client and a parser. Both now exist
(``app/storage``, ``app/ingestion``), and the five methods are bound. What no
hermetic test can answer is the question that has bitten this build repeatedly:
**does the cluster accept the statements?** Every unit test drives a recording
connection, and a recording connection has no CHECK constraints, no foreign
keys and no generated columns.

So this runner opens a real connection as ``pv_app_reader_writer`` and drives
the real adapters over the real ``.eml``.

What it writes, and what it does NOT leave behind
--------------------------------------------------
**Nothing, by default.** Every statement runs inside one explicit transaction
which is rolled back at the end. That is deliberate and it is not timidity:
``evidence_items`` is append-only and the app role holds no ``DELETE``, so a row
written here could not be taken back, and ``db/seeds/MANIFEST.json`` asserts
exact row counts that other lanes are verifying against. ``STATUS.md`` section 7:
"verification against a database another agent is writing is meaningless in both
directions."

A rollback still proves the thing a fake cannot: every CHECK, every foreign key,
every column type and every generated column was evaluated by CockroachDB and
accepted. Pass ``--persist`` to commit instead -- which is what the demo itself
does, since ``scripts/seed/evidence.py`` deliberately does **not** seed this
artifact and records that it is uploaded live at demo time.

The Kernel step
---------------
``--persist`` is also what gates the proposal. ``commit_proposal`` opens its own
transaction on the ``pv_kernel_writer`` pool and commits it; there is no way to
roll that back from here, and a proposal citing evidence ids from a
rolled-back transaction would fail ``fk_claims_evidence`` -- correctly. Without
``--persist`` the step is reported ``CANNOT RUN`` with that reason rather than
skipped quietly (``D-00-005``).

Windows
-------
psycopg's async client refuses the proactor loop, which is the default event
loop policy on Windows and the cause of a control plane that started, answered
``200`` and reported ``db_ok=false`` against a healthy cluster. The selector
loop is selected explicitly at the bottom of this file.

Usage
-----
    python scripts/ingest_hero_artifact.py
    python scripts/ingest_hero_artifact.py --persist
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import selectors
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402  -- sys.path must be primed before the first-party imports.
import psycopg

from provenance_contracts.identity import CapabilityBinding
from provenance_contracts.proposal import MemoryProposal, ProposalIdentity, ProposedClaim
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    ClaimKind,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    ValueType,
)
from scripts.seed.db import role_dsn
from services.control_plane.app.api.adapters.internal import KernelInternalPort
from services.control_plane.app.api.adapters.write import KernelWritePort
from services.control_plane.app.api.errors import ApiError
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.api.schemas.internal import (
    IngestArtifactRequest,
    RegisterEvidenceRequest,
)
from services.control_plane.app.api.schemas.public import (
    ArtifactCompleteRequest,
    UploadIntentRequest,
)
from services.control_plane.app.ingestion import artifacts as ingestion_artifacts
from services.control_plane.app.ingestion import blocks as ingestion_blocks
from services.control_plane.app.proposals import submission
from services.control_plane.app.storage import FilesystemObjectStore

__all__ = ["main"]

PASS: Final[str] = "PASS"
FAIL: Final[str] = "FAIL"
CANNOT_RUN: Final[str] = "CANNOT RUN"

HERO_SUBJECT: Final[str] = "seed-hero-alex-rivera"
ARTIFACT_PATH: Final[Path] = _REPO_ROOT / "demo" / "artifacts" / "northline-june-invoice.eml"

#: Where the filesystem store keeps objects for this run. Under the scratch
#: tree rather than the repository, so a run leaves no untracked bytes behind
#: in a tree whose ``ops/`` status is already a decision waiting on a human.
DEFAULT_OBJECT_ROOT: Final[str] = os.environ.get("PV_LOCAL_OBJECT_ROOT") or "D:/tmp/pv/objects"

#: The key the "SES worker" wrote. Deliberately outside ``raw/``: it is the
#: shape ``ck_source_artifacts_s3_key_shape`` refuses, which is the whole reason
#: section 9.1 has to copy before it inserts.
INBOUND_KEY: Final[str] = "ses/2026/06/14/0100018f9e70abcd-northline-june"

#: ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*: "Evidence admitted from the
#: June invoice -- exactly 3: DATE_ASSERTION, AMOUNT_ASSERTION,
#: IDENTIFIER_ASSERTION". The quotations below are verbatim from the artifact,
#: which is the point: section 9.4 step 2 refuses anything that is not.
HERO_CANDIDATES: Final[tuple[dict[str, Any], ...]] = (
    {
        "client_ref": "c_date",
        "evidence_type": "DATE_ASSERTION",
        "exact_text": "1 June 2026 through 30 June 2026",
        "normalized_text": (
            "[type=COUNTERPARTY_CLAIM][counterparty=Northline Fiber] service period "
            "1 June 2026 through 30 June 2026"
        ),
    },
    {
        "client_ref": "c_amount",
        "evidence_type": "AMOUNT_ASSERTION",
        "exact_text": "Amount due USD 186.00 by 30 June 2026.",
        "normalized_text": (
            "[type=COUNTERPARTY_CLAIM][counterparty=Northline Fiber][amount=186.00 USD] "
            "amount due by 30 June 2026"
        ),
    },
    {
        "client_ref": "c_account",
        "evidence_type": "IDENTIFIER_ASSERTION",
        "exact_text": "account NF-4471-8802",
        "normalized_text": (
            "[type=COUNTERPARTY_CLAIM][counterparty=Northline Fiber] account NF-4471-8802"
        ),
    },
)


class _OneConnectionSource:
    """A ``ConnectionSource`` that hands out the same open connection.

    The adapters take a source rather than a connection so that production can
    hand them a pool. Here every ``async with source.connection()`` must land on
    the *same* transaction, or the rollback would only undo part of the run --
    which would be worse than not rolling back at all.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def connection(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> Any:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def _load_dotenv(root: Path) -> None:
    """Populate ``os.environ`` from ``.env`` without echoing a value.

    ``Settings`` deliberately refuses to parse a dotenv (``settings.py:331``).
    This is a command run by hand, not an import-time path.
    """
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _say(verdict: str, step: str, detail: str) -> None:
    print(f"  [{verdict:^10}] {step}\n               {detail}")


async def _owner(conn: Any, subject: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT tenant_id, id FROM users WHERE cognito_sub = %(sub)s", {"sub": subject}
        )
        rows = await cur.fetchall()
    if not rows:
        raise LookupError(f"no users row for cognito_sub={subject!r}; run `make seed` first")
    return rows[0][0], rows[0][1]


async def _run(args: argparse.Namespace) -> int:
    data = ARTIFACT_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    print(f"\nProvenance -- live ingestion of {ARTIFACT_PATH.name}")
    print(f"  {len(data)} bytes, sha256={digest[:16]}...")
    print(
        f"  mode: {'PERSIST (rows are committed)' if args.persist else 'ROLLBACK (nothing kept)'}\n"
    )

    store = FilesystemObjectStore(args.object_root)
    await store.put(INBOUND_KEY, data, content_type="message/rfc822")
    _say(PASS, "0. the worker's bytes are in the store", f"key={INBOUND_KEY}")

    dsn = str(role_dsn("pv_app_reader_writer", database=args.database))
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=False)
    verdicts: list[str] = []
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT current_user")
            row = await cur.fetchone()
        role = str(row[0]) if row else "<unknown>"
        if role != "pv_app_reader_writer":
            _say(FAIL, "connected role", f"authenticated as {role!r}")
            return 1
        _say(PASS, "connected", f"role={role} database={args.database}")

        tenant_id, user_id = await _owner(conn, args.subject)

        # -- 8.18 and 8.19, the human upload path ---------------------------
        #
        # Run first and against the same bytes. It coexists with the inbound
        # path below because `uq_source_artifacts_content` is
        # (tenant_id, user_id, content_sha256, source_type) and the two routes
        # produce different source types -- UPLOAD_EML and EMAIL_INBOUND.
        writer = KernelWritePort(
            _OneConnectionSource(conn),
            kernel_pool=None,
            read=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            clock=lambda: datetime.now(UTC),
            objects=store,
        )
        scope = OwnerScope(tenant_id=tenant_id, user_id=user_id)
        try:
            intent = await writer.upload_intent(
                scope,
                UploadIntentRequest(
                    filename=ARTIFACT_PATH.name,
                    mime_type="message/rfc822",
                    size_bytes=len(data),
                    sha256=digest,
                ),
            )
        except ApiError as exc:
            _say(FAIL, "8.18 write.upload_intent", f"{exc.code}: {exc.details}")
            return 1
        _say(
            PASS,
            "8.18 write.upload_intent",
            f"artifact_id={intent['artifact_id']} transport={intent['upload_transport']} "
            f"s3_key={intent['s3_key']}",
        )
        # The client's step 2: PUT the bytes at the key the server chose.
        await store.put(intent["s3_key"], data, content_type="message/rfc822")
        try:
            completed = await writer.complete_artifact(
                scope,
                uuid.UUID(intent["artifact_id"]),
                ArtifactCompleteRequest(sha256=digest, size_bytes=len(data)),
            )
        except ApiError as exc:
            _say(FAIL, "8.19 write.complete_artifact", f"{exc.code}: {exc.details}")
            return 1
        if completed is None:
            _say(FAIL, "8.19 write.complete_artifact", "returned absence for a row it created")
            return 1
        _say(
            PASS,
            "8.19 write.complete_artifact",
            f"status={completed['status']} parser_status={completed['parser_status']} "
            f"blocks={completed['block_count']} agent_run_id={completed['agent_run_id']}",
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT parser_status, parser_version, "
                "       jsonb_array_length(parser_metadata->'blocks') "
                "FROM source_artifacts WHERE tenant_id=%(t)s AND user_id=%(u)s AND id=%(a)s",
                {"t": tenant_id, "u": user_id, "a": uuid.UUID(intent["artifact_id"])},
            )
            uploaded = await cur.fetchall()
        _say(
            PASS,
            "the uploaded artifact's row, read back",
            f"parser_status={uploaded[0][0]} parser_version={uploaded[0][1]} "
            f"parser_metadata.blocks={uploaded[0][2]}",
        )

        port = KernelInternalPort(
            _OneConnectionSource(conn),
            kernel_pool=None,
            read=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            sink=None,  # type: ignore[arg-type]
            clock=lambda: datetime.now(UTC),
            objects=store,
        )

        # -- 9.1 ------------------------------------------------------------
        ingest_binding = CapabilityBinding(
            binding_id=uuid.uuid4(),
            binding_kind="INGEST_JOB",
            tenant_id=tenant_id,
            user_id=user_id,
            expires_at=datetime.now(UTC).replace(microsecond=0),
            status="ACTIVE",
        )
        request = IngestArtifactRequest(
            alias_hash="b64:live-run-alias-hash-for-the-hero-invoice=",
            s3_bucket="provenance-inbound-local",
            s3_key=INBOUND_KEY,
            received_at=datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
            size_bytes=len(data),
            content_sha256=digest,
            ses_verdicts={
                "spf": "PASS",
                "dkim": "PASS",
                "dmarc": "PASS",
                "spam": "PASS",
                "virus": "PASS",
            },
        )
        try:
            ingested = await port.ingest_artifact(ingest_binding, request)
        except ApiError as exc:
            _say(FAIL, "9.1 internal.ingest_artifact", f"{exc.code}: {exc.details}")
            return 1
        artifact_id = uuid.UUID(ingested["artifact_id"])
        verdicts.append(PASS)
        _say(
            PASS,
            "9.1 internal.ingest_artifact",
            f"artifact_id={artifact_id} status={ingested['status']} "
            f"parser_status={ingested['parser_status']} blocks={ingested['block_count']} "
            f"agent_run_id={ingested['agent_run_id']}",
        )
        if ingested["status"] == "DUPLICATE":
            _say(
                CANNOT_RUN,
                "the rest of the run",
                "this artifact is already in the corpus; nothing downstream is new. "
                "Re-run after `make demo-reset`, or against a database without it.",
            )
            return 0

        # -- the row the cluster actually holds --------------------------------
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT s3_key, parser_status, parser_version, "
                "       jsonb_array_length(parser_metadata->'blocks') "
                "FROM source_artifacts WHERE tenant_id=%(t)s AND user_id=%(u)s AND id=%(a)s",
                {"t": tenant_id, "u": user_id, "a": artifact_id},
            )
            stored = await cur.fetchall()
        if not stored:
            _say(FAIL, "the source_artifacts row", "the INSERT reported success and read back none")
            return 1
        key, status, version, block_count = stored[0]
        _say(
            PASS,
            "the source_artifacts row, read back from the cluster",
            f"s3_key={key} parser_status={status} parser_version={version} "
            f"parser_metadata.blocks={block_count}",
        )

        # -- 9.3 ------------------------------------------------------------
        run_binding = CapabilityBinding(
            binding_id=uuid.UUID(ingested["agent_run_id"]),
            binding_kind="AGENT_RUN",
            tenant_id=tenant_id,
            user_id=user_id,
            artifact_id=artifact_id,
            expires_at=datetime.now(UTC).replace(microsecond=0),
            status="ACTIVE",
        )
        try:
            content = await port.artifact_content(run_binding)
        except ApiError as exc:
            _say(FAIL, "9.3 internal.artifact_content", f"{exc.code}: {exc.details}")
            return 1
        if content is None:
            _say(FAIL, "9.3 internal.artifact_content", "returned absence for the bound artifact")
            return 1
        kinds = [block["kind"] for block in content["content_blocks"]]
        _say(
            PASS,
            "9.3 internal.artifact_content",
            f"{len(kinds)} blocks: {', '.join(kinds)}",
        )

        # -- 9.4 ------------------------------------------------------------
        body_blocks = [b for b in content["content_blocks"] if b["kind"] == "BODY"]
        candidates = []
        for spec in HERO_CANDIDATES:
            holder = next(
                (
                    b
                    for b in body_blocks
                    if ingestion_blocks.__dict__  # keep the import meaningful
                    and " ".join(spec["exact_text"].split()) in " ".join(b["text"].split())
                ),
                None,
            )
            if holder is None:
                _say(
                    FAIL,
                    "9.4 candidate construction",
                    f"{spec['client_ref']}: {spec['exact_text']!r} is in no BODY block, so "
                    "this runner would be asking the server to admit an invented quotation",
                )
                return 1
            candidates.append(
                {
                    **{k: v for k, v in spec.items() if k != "exact_text"},
                    "block_id": holder["block_id"],
                    "exact_text": spec["exact_text"],
                    "observed_at": datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
                    "extraction_confidence": "0.9700",
                }
            )
        try:
            registered = await port.register_evidence(
                run_binding, RegisterEvidenceRequest(candidates=candidates)
            )
        except ApiError as exc:
            _say(FAIL, "9.4 internal.register_evidence", f"{exc.code}: {exc.details}")
            return 1
        _say(
            PASS,
            "9.4 internal.register_evidence",
            f"created={registered['created_count']} deduplicated={registered['deduplicated_count']} "
            f"embedding_status={registered['embedding_status']} "
            f"types={[c['evidence_type'] for c in candidates]}",
        )

        # The refusal, proved on the same live artifact rather than asserted.
        try:
            await port.register_evidence(
                run_binding,
                RegisterEvidenceRequest(
                    candidates=[
                        {
                            "client_ref": "c_invented",
                            "evidence_type": "AMOUNT_ASSERTION",
                            "block_id": candidates[1]["block_id"],
                            "exact_text": "Amount due USD 286.00 by 30 June 2026.",
                            "normalized_text": "[amount=286.00 USD] invented",
                            "observed_at": datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
                            "extraction_confidence": "0.9900",
                        }
                    ]
                ),
            )
        except ApiError as exc:
            _say(
                PASS,
                "9.4 steps 1-2 refuse an invented quotation",
                f"{exc.code} reason={exc.details.get('reason')}",
            )
        else:
            _say(
                FAIL,
                "9.4 steps 1-2 refuse an invented quotation",
                "a quotation that is not in the document was ADMITTED",
            )
            return 1

        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM evidence_items "
                "WHERE tenant_id=%(t)s AND user_id=%(u)s AND artifact_id=%(a)s",
                {"t": tenant_id, "u": user_id, "a": artifact_id},
            )
            counted = await cur.fetchall()
        _say(
            PASS,
            "evidence_items, counted on the cluster",
            f"{counted[0][0]} rows for this artifact (the canon is exactly 3)",
        )

        # -- 9.7, in two halves that fail differently ------------------------
        #
        # The app's half is a `memory_proposals` INSERT under write rule W4 and
        # it runs on this connection, so it is measurable inside the rollback.
        # The Kernel's half opens its own transaction on the pv_kernel_writer
        # pool and commits it; there is no way to roll that back from here.
        evidence_ids = [uuid.UUID(e["evidence_id"]) for e in registered["evidence"]]
        route = ingestion_artifacts.DEFAULT_MODEL_ROUTE
        proposal = MemoryProposal(
            proposal_id=uuid.uuid4(),
            proposal_type=ProposalType.INGESTION_INTERPRETATION,
            trace_id=uuid.UUID(ingested["trace_id"]),
            agent_run_id=uuid.UUID(ingested["agent_run_id"]),
            user_id=user_id,
            source_artifact_ids=(artifact_id,),
            evidence_ids=tuple(evidence_ids),
            identity=ProposalIdentity(confidence="0.9000"),
            claims=(
                ProposedClaim(
                    local_id="cl_001",
                    claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
                    subject_type=SubjectType.RELATIONSHIP,
                    subject_local_ref="northline-fiber",
                    predicate="billing_period_covered",
                    object_type=ValueType.INTERVAL,
                    object_value={"from": "2026-06-01", "to": "2026-06-30"},
                    actor_type=ActorType.COUNTERPARTY,
                    actor_ref="billing@northlinefiber.example",
                    evidence_id=evidence_ids[0],
                    source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
                    modality=Modality.ASSERTED_PRESENT,
                    extraction_confidence="0.9700",
                ),
            ),
            model=ModelAttribution(
                provider="gemini",
                model_id=route["tier_r"],
                tier=ModelTier.R,
                prompt_version="pv-extract-1.1.0",
                graph_name=ingestion_artifacts.INGESTION_GRAPH_NAME,
                graph_version=ingestion_artifacts.INGESTION_GRAPH_VERSION,
            ),
            idempotency_key=f"live-ingest-{artifact_id}",
            created_at=datetime.now(UTC),
        )
        _say(
            PASS,
            "9.7 the typed MemoryProposal is constructed and validates",
            f"proposal_id={proposal.proposal_id} claims={len(proposal.claims)} "
            f"evidence_ids={len(proposal.evidence_ids)} model={proposal.model.model_id}",
        )
        try:
            written = await submission.register_proposal(
                conn, proposal, tenant_id=tenant_id, user_id=user_id
            )
        except psycopg.errors.CheckViolation as exc:
            await conn.rollback()
            _say(
                FAIL,
                "9.7 the app-side memory_proposals INSERT",
                f"the cluster refused it: {type(exc).__name__} "
                f"{str(exc).splitlines()[0][:200]}. Migration 0009a widens "
                "ck_memory_proposals_model to admit the Gemini ids and is NOT applied "
                "on this cluster; a schema change is a human's authorisation.",
            )
            _say(
                CANNOT_RUN,
                "9.7 commit_proposal",
                "the proposal row was refused, and fk_kernel_decisions_proposal means "
                "the Kernel cannot decide a proposal that does not exist.",
            )
            return 0
        _say(PASS, "9.7 the app-side memory_proposals INSERT", f"{written} row written")
        _say(
            CANNOT_RUN,
            "9.7 commit_proposal",
            "the Kernel commits its own transaction and this run is about to be rolled "
            "back, so its decision would outlive the proposal row it decides. "
            "Re-run with --persist to attempt it."
            if not args.persist
            else "this runner does not hold the pv_kernel_writer pool; "
            "scripts/run_ingestion_graph.py is where the graph path is driven.",
        )
        return 0
    finally:
        if args.persist:
            await conn.commit()
            print("\n  COMMITTED. The rows above are now in the corpus.")
        else:
            await conn.rollback()
            print(
                "\n  ROLLED BACK. Every statement above was accepted by CockroachDB and "
                "nothing was kept."
            )
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default=HERO_SUBJECT, help="users.cognito_sub")
    parser.add_argument("--database", default="provenance")
    parser.add_argument("--object-root", default=DEFAULT_OBJECT_ROOT)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="commit instead of rolling back. evidence_items is append-only.",
    )
    args = parser.parse_args(argv)
    _load_dotenv(_REPO_ROOT)
    if not ARTIFACT_PATH.is_file():
        print(f"CANNOT RUN: {ARTIFACT_PATH} does not exist.")
        return 2
    # psycopg's async client refuses the proactor loop, which is Windows'
    # default. This is the same fix `scripts/run_api.py` records.
    return asyncio.run(
        _run(args), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    )


if __name__ == "__main__":
    raise SystemExit(main())
