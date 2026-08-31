"""``InternalPort`` -- the thirteen ``/internal/v1`` operations.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 9.1-9.13.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer* and -> *Kernel retry
  exhaustion*.
- ``services/control_plane/app/memory_kernel/transaction.py::commit_proposal``
  -- the Kernel's single entry point.

Every method takes a ``CapabilityBinding``
--------------------------------------------
Not an ``OwnerScope``, because the binding also carries ``allowed_case_ids``
and the bound artifact: the two things an internal handler must not take from
the request. The binding was selected by an id the caller presented and
populated by columns the caller never touched, which is contract law L10 --
a machine client never asserts its own ``user_id``.

Where the Kernel is
--------------------
``commit_proposal(pool, proposal, principal=...)`` is the door, and this
module holds the ``pv_kernel_writer`` pool so that door can be opened. Nothing
here issues SQL of its own; ``python -m tools.write_path_lint`` reports
canonical write statements only inside ``app/memory_kernel`` and
``app/proposals``, and this adapter does not move that number.

The proposal path, and what it took to bind it
-----------------------------------------------
:meth:`KernelInternalPort.submit_proposal` is bound. Section 9.7's path is two
steps and the split is a grant: the app ``INSERT``s the ``memory_proposals``
row under write rule ``W4``, then the Kernel decides it -- ``commit_proposal``
only ever ``UPDATE``s that row, and ``fk_kernel_decisions_proposal`` refuses
the decision row when the proposal row is absent. The app's half now lives in
``app/proposals/submission.py``; before it existed an agent had no way to reach
the Kernel at all, which is the path the entire product rests on.

Three fields ``MemoryProposal`` requires were not on the wire, and each was
closed differently rather than uniformly:

* ``model: ModelAttribution``. Section 9.7's request body carries a ``model``
  block and section 9.8's deliberately does not, and that asymmetry is in the
  two specs. It is safe here only because the block is **checked**:
  ``graph_name`` and ``graph_version`` are read off the ``agent_runs`` row, and
  the claimed ``model_id`` must be the one that row's ``model_route`` records
  for the claimed tier. ``CANONICAL_DECISIONS.md`` -> *Disclosure* makes the
  shipped model checkable against persisted state rather than against a README,
  and that property survives a caller-supplied id only under that comparison.
  ``prompt_version`` is the one field that cannot be checked here -- it reaches
  persisted state on ``agent_runs.model_calls[]``, which only section 9.9
  writes, *after* 9.7 has run -- and ``proposals/submission.py`` says so rather
  than presenting it as verified.
* ``idempotency_key`` -- now a keyword argument on the port. Section 9.7 makes
  the header required and the route already validates it; minting one here
  would match the caller's only when the caller supplied none, which is the
  defect ``execute_action`` records.
* ``local_id`` on every proposed item. ``MemoryProposalRequest`` types the five
  item lists as ``dict``, so the caller supplies it and pydantic validates the
  prefix. Section 9.7's printed body spells the field ``client_ref``; the
  contract package is the authority and the mismatch is a typed ``422``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

import psycopg.errors as pgerr

from provenance_contracts.identity import CapabilityBinding
from provenance_db.repositories import agent_runs
from services.control_plane.app.actions import (
    ActionExecutor,
    ActionPolicy,
    ActionRefusedError,
    ActionScope,
    ActionSink,
    ActionStore,
    PostgresActionStore,
)
from services.control_plane.app.actions import (
    executor as action_executor,
)
from services.control_plane.app.api import context
from services.control_plane.app.api.adapters import action_errors, render
from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.adapters.unbound import unbound
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import OwnerScope, ReadPort
from services.control_plane.app.events import catalogue
from services.control_plane.app.events.consumer import ConsumptionOutcome, IdempotentConsumer
from services.control_plane.app.events.dispatcher import OutboxDispatcher
from services.control_plane.app.events.store import SqlConsumerUnitOfWork, SqlOutboxStore
from services.control_plane.app.events.transport import InProcessTransport, PublishedEvent
from services.control_plane.app.ingestion import artifacts as ingestion_artifacts
from services.control_plane.app.ingestion import blocks as ingestion_blocks
from services.control_plane.app.ingestion import evidence as ingestion_evidence
from services.control_plane.app.memory_kernel import preflight
from services.control_plane.app.memory_kernel.trigger_commit import KernelTriggerWriter
from services.control_plane.app.observability import runs as observability_runs
from services.control_plane.app.proposals import submission
from services.control_plane.app.storage import (
    ObjectStore,
    ObjectStoreError,
    UnconfiguredObjectStore,
    raw_key,
)
from services.control_plane.app.triggers import service as trigger_service
from services.control_plane.app.triggers.store import SqlProjectionReader, SqlTriggerStore

__all__ = ["KernelInternalPort"]

Row = dict[str, Any]

#: ``ck_source_artifacts_source_type`` admits seven values and this is the one
#: an inbound message carries. Section 9.1 has no field for it: the source type
#: is what the *route* was, and a caller that could name it could claim to be
#: the provider's mail server.
INBOUND_SOURCE_TYPE: Final[str] = "EMAIL_INBOUND"

#: ``ck_source_artifacts_mime`` admits five values; inbound mail is RFC 822.
INBOUND_MIME_TYPE: Final[str] = "message/rfc822"


class KernelInternalPort:
    """The workload surface, over the read pool and the Kernel's pool.

    Two pools, two roles. Reads go through ``pv_app_reader_writer`` and the
    Kernel holds ``pv_kernel_writer``; one pool with a role argument would
    turn "only the Kernel writes canonical tables" from a grant back into a
    convention, because the property would then depend on call order rather
    than on credentials.
    """

    __slots__ = (
        "_clock",
        "_consumer_unit_of_work",
        "_kernel_pool",
        "_model_route",
        "_objects",
        "_outbox_store",
        "_policy",
        "_projection_reader",
        "_proposal_kernel",
        "_read",
        "_recorder",
        "_sink",
        "_source",
        "_store",
        "_transport",
        "_trigger_kernel",
        "_trigger_store",
    )

    def __init__(
        self,
        source: ConnectionSource,
        *,
        kernel_pool: Any,
        read: ReadPort,
        policy: ActionPolicy,
        sink: ActionSink,
        clock: Callable[[], datetime],
        recorder: Any = None,
        store_factory: Callable[[Any], ActionStore] = PostgresActionStore,
        trigger_store: Any = None,
        projection_reader: Any = None,
        trigger_kernel: Any = None,
        proposal_kernel: Any = None,
        outbox_store: Any = None,
        transport: Any = None,
        consumer_unit_of_work: Any = None,
        objects: ObjectStore | None = None,
        model_route: Mapping[str, str] | None = None,
    ) -> None:
        self._source = source
        self._kernel_pool = kernel_pool
        self._read = read
        self._policy = policy
        self._sink = sink
        self._clock = clock
        self._recorder = recorder
        self._store = store_factory
        # The four Phase 10 boundaries. Constructed here rather than per call so
        # the transport keeps whatever state it has across a sweep, and
        # overridable so the hermetic suites can drive the real adapter with no
        # cluster -- which is what makes "did this bind the evaluator?" a unit
        # test rather than an integration test.
        self._trigger_store = trigger_store or SqlTriggerStore(source)
        self._projection_reader = projection_reader or SqlProjectionReader(source)
        self._trigger_kernel = trigger_kernel or KernelTriggerWriter(kernel_pool)
        self._proposal_kernel = proposal_kernel or submission.KernelProposalWriter(kernel_pool)
        self._outbox_store = outbox_store or SqlOutboxStore(source)
        self._transport = transport or InProcessTransport()
        self._consumer_unit_of_work = consumer_unit_of_work or SqlConsumerUnitOfWork(source)
        # Section 9.1 needs an object store to copy the worker's bytes into the
        # `raw/` prefix `ck_source_artifacts_s3_key_shape` requires. It is
        # injected rather than constructed here for the same reason the four
        # Phase 10 boundaries above are: the hermetic suites drive the real
        # adapter with a real filesystem store and no cluster.
        self._objects = objects if objects is not None else UnconfiguredObjectStore("PV_PLATFORM")
        self._model_route = dict(model_route or ingestion_artifacts.DEFAULT_MODEL_ROUTE)

    def _executor(self, conn: Any) -> ActionExecutor:
        """One executor per connection, per call.

        Not cached on the instance: the executor holds a store, the store
        holds a connection, and a connection belongs to whoever checked it out
        of the pool for the duration of one request. Caching it would hand a
        returned connection to the next caller.
        """
        kwargs: dict[str, Any] = {
            "store": self._store(conn),
            "sink": self._sink,
            "policy": self._policy,
            "clock": self._clock,
        }
        if self._recorder is not None:
            kwargs["recorder"] = self._recorder
        return ActionExecutor(**kwargs)

    # -- 9.1 - 9.3 --------------------------------------------------------

    async def ingest_artifact(self, binding: CapabilityBinding, payload: Any) -> Row:
        """Section 9.1. The inbound key is read; the stored key is minted.

        Steps 1-3 are the route's: it resolved the alias into this binding,
        rejected an oversized message and refused a failed virus or spam
        verdict. What is left is the part that needed an object store.

        **The bytes are copied before the row is written, and that ordering is
        a constraint rather than a preference.** The applied
        ``ck_source_artifacts_s3_key_shape`` admits only a key under the
        ``raw/`` prefix, and section 9.1's body carries the key the SES worker
        wrote -- ``ses/2026/06/05/...`` in the spec's own example. So the row
        can only name a key this server minted, and the only honest way to have
        one is to have put the bytes there. Synthesising a ``raw/`` key without
        the copy satisfies the CHECK and stores a locator for bytes nobody
        wrote; the first symptom is a download that 404s months later against a
        row that looks perfect.

        The digest is **recomputed from the copied bytes** and compared with the
        declared ``content_sha256``. The declaration is a claim by a worker; the
        digest is a measurement, and ``uq_source_artifacts_content`` deduplicates
        on it -- so a wrong one lets the same message in twice, or collides two
        different ones.

        The store I/O happens before any connection is taken. Object-store I/O
        is a network call in every implementation but the local one, and
        ``CANONICAL_DECISIONS.md`` -> *Transaction isolation* forbids one inside
        a transaction; holding a pooled connection across it is the same mistake
        one step earlier.
        """
        try:
            data = await self._objects.get(payload.s3_key)
        except ObjectStoreError as exc:
            raise ApiError(
                ErrorCode.ARTIFACT_OBJECT_MISSING,
                details={"s3_key": payload.s3_key, "detail": str(exc)},
            ) from exc

        digest = hashlib.sha256(data).hexdigest()
        if digest != payload.content_sha256:
            raise ApiError(
                ErrorCode.ARTIFACT_HASH_MISMATCH,
                details={
                    "declared_sha256": payload.content_sha256,
                    "computed_sha256": digest,
                },
            )
        if payload.size_bytes and len(data) != payload.size_bytes:
            raise ApiError(
                ErrorCode.ARTIFACT_SIZE_MISMATCH,
                details={
                    "declared_size_bytes": payload.size_bytes,
                    "stored_size_bytes": len(data),
                },
            )

        # The message is the authority on its own headers. Section 9.1 lets the
        # worker declare them; a declared subject that disagrees with the
        # message is exactly the unverified assertion this path removes.
        headers = ingestion_blocks.artifact_headers(data)
        message_id = headers["source_message_id"] or payload.source_message_id

        async with self._source.connection() as conn:
            duplicate = await ingestion_artifacts.existing_artifact_id(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                content_sha256_hex=digest,
                source_type=INBOUND_SOURCE_TYPE,
                source_message_id=message_id,
            )
        if duplicate is not None:
            # Duplicate bytes never create duplicate business state, and no new
            # run is opened: interpreting one message twice is how one artifact
            # becomes two beliefs.
            return {
                "artifact_id": str(duplicate),
                "status": "DUPLICATE",
                "duplicate_of": str(duplicate),
                "agent_run_id": None,
                "trace_id": None,
            }

        artifact_id = context.new_uuid7()
        key = raw_key(tenant_id=binding.tenant_id, user_id=binding.user_id, artifact_id=artifact_id)
        try:
            await self._objects.put(key, data, content_type=INBOUND_MIME_TYPE)
        except ObjectStoreError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                details={"dependency": "OBJECT_STORE", "detail": str(exc)},
            ) from exc

        parse = ingestion_blocks.parse_artifact(
            artifact_id=artifact_id, mime_type=INBOUND_MIME_TYPE, data=data
        )
        verdicts = payload.ses_verdicts.model_dump()
        row = ingestion_artifacts.ArtifactRow(
            artifact_id=artifact_id,
            tenant_id=binding.tenant_id,
            user_id=binding.user_id,
            source_type=INBOUND_SOURCE_TYPE,
            s3_bucket=self._objects.bucket,
            s3_key=key,
            content_sha256_hex=digest,
            size_bytes=len(data),
            mime_type=INBOUND_MIME_TYPE,
            received_at=payload.received_at,
            created_at=self._clock(),
            parser_status=parse.status.value,
            parser_version=parse.parser_version,
            parser_metadata=(
                ingestion_blocks.parser_metadata_value(parse, extra={"ses_verdicts": verdicts})
                if parse.blocks or parse.parser_version is not None
                else None
            ),
            ses_verdicts=verdicts,
            source_message_id=message_id,
            sender=headers["sender"] or payload.sender,
            recipient=headers["recipient"] or payload.recipient,
            subject=headers["subject"] or payload.subject,
        )
        trace_id = context.new_uuid7()
        run_id = context.new_uuid7()
        async with self._source.connection() as conn:
            written = await ingestion_artifacts.insert_artifact(conn, row)
            if written == 0:
                existing = await ingestion_artifacts.existing_artifact_id(
                    conn,
                    tenant_id=binding.tenant_id,
                    user_id=binding.user_id,
                    content_sha256_hex=digest,
                    source_type=INBOUND_SOURCE_TYPE,
                    source_message_id=message_id,
                )
                return {
                    "artifact_id": str(existing or artifact_id),
                    "status": "DUPLICATE",
                    "duplicate_of": str(existing) if existing is not None else None,
                    "agent_run_id": None,
                    "trace_id": None,
                }
            opened = await ingestion_artifacts.open_agent_run(
                conn,
                run_id=run_id,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                trace_id=trace_id,
                artifact_id=artifact_id,
                model_route=self._model_route,
                started_at=self._clock(),
            )
        return {
            "artifact_id": str(artifact_id),
            "status": "QUEUED",
            "duplicate_of": None,
            "agent_run_id": opened["agent_run_id"],
            "trace_id": opened["trace_id"],
            "parser_status": parse.status.value,
            "block_count": len(parse.blocks),
            "interpretation": dict(ingestion_artifacts.INTERPRETATION_DISPATCH),
        }

    async def agent_run(self, binding: CapabilityBinding) -> Row | None:
        """Section 9.2. Run bootstrap: what this run is allowed to touch.

        The response carries **no** ``user_id`` and no ``tenant_id``. Section
        9.2 is explicit about why: the graph never needs them, every
        subsequent call re-resolves the binding server-side, and withholding
        the id removes both the temptation to pass one back and the
        possibility of a model seeing and repeating one. The projection below
        therefore drops both columns even though the row has them.
        """
        async with self._source.connection() as conn:
            row = await agent_runs.get_agent_run_for(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                run_id=binding.binding_id,
            )
        if row is None:
            return None
        return {
            "agent_run_id": str(row["id"]),
            "graph_name": row.get("graph_name"),
            "graph_version": row.get("graph_version"),
            "trace_id": None if row.get("trace_id") is None else str(row["trace_id"]),
            "input_artifact_id": (
                None if row.get("input_artifact_id") is None else str(row["input_artifact_id"])
            ),
            "allowed_case_ids": row.get("allowed_case_ids"),
            "capability_expires_at": row.get("expires_at"),
            "model_route": row.get("model_route"),
            "memory_mode": row.get("memory_mode"),
            "is_counterfactual": row.get("is_counterfactual"),
            "status": row.get("status"),
        }

    async def artifact_content(self, binding: CapabilityBinding, **options: Any) -> Row | None:
        """Section 9.3. The blocks of the artifact this run is bound to.

        There is no ``artifact_id`` parameter anywhere on this path. The
        artifact is the one on the capability row, so a run cannot read a
        second artifact by asking for it.

        **A row that says ``PARSED`` is not proof that anything can be read
        back.** ``read_normalized_content`` returns either real content or a
        named reason, and this method surfaces the reason rather than an empty
        ``content_blocks``: an empty list tells the graph the document has no
        text, and the graph then extracts nothing and reports success. Every
        artifact the seed wrote is in exactly that state --
        ``parser_status='PARSED'``, ``parser_version='seed-1.0.0'``, no stored
        parser output -- so the distinction is the ordinary case.

        ``409 VALIDATION_FAILED`` is section 9.3's own error for
        ``parser_status <> 'PARSED'``, and it is reused for the two other
        unreadable states because the caller's recourse is identical: there is
        nothing to interpret yet.
        """
        artifact_id = binding.artifact_id
        if artifact_id is None:
            return None
        async with self._source.connection() as conn:
            row = await ingestion_artifacts.load_artifact(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                artifact_id=artifact_id,
            )
        if row is None:
            return None

        content = ingestion_blocks.read_normalized_content(
            artifact_id=artifact_id,
            parser_status=str(row["parser_status"]),
            parser_metadata=row.get("parser_metadata"),
        )
        if isinstance(content, ingestion_blocks.ParserOutputUnavailable):
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                http_status=409,
                details={
                    "reason": content.reason_code,
                    "parser_status": content.parser_status,
                    "detail": content.detail,
                },
            )

        include_quoted = options.get("include_quoted_history", True)
        max_chars = int(options.get("max_chars") or 60_000)
        blocks = [
            block for block in content.blocks if include_quoted or not block.is_quoted_history
        ]
        rendered: list[Row] = []
        budget = max_chars
        truncated = content.truncated
        for block in blocks:
            if budget <= 0:
                truncated = True
                break
            text = block.text[:budget]
            truncated = truncated or len(text) != len(block.text)
            budget -= len(text)
            rendered.append(
                {
                    "block_id": block.block_id,
                    "kind": block.kind.value,
                    "text": text,
                    "content_sha256": block.content_sha256,
                    "source_locator": block.source_locator.model_dump(
                        mode="json", exclude_none=True
                    ),
                }
            )
        requested = options.get("block_id")
        if requested is not None:
            rendered = [entry for entry in rendered if entry["block_id"] == requested]
            if not rendered:
                return None
        return {
            "artifact_id": str(artifact_id),
            "mime_type": row["mime_type"],
            "parser_version": content.parser_version,
            "truncated": truncated,
            "content_blocks": rendered,
            # Section 9.3 prints the key and this build produces none: the
            # parser reads the message body and does not walk attachments, so
            # an empty list here is a measured zero rather than an unknown.
            "attachments": [],
        }

    # -- 9.4 - 9.6 --------------------------------------------------------

    async def register_evidence(self, binding: CapabilityBinding, payload: Any) -> Row:
        """Section 9.4. Steps 1 and 2 run *before* anything is written.

        They are the deterministic defence against a model inventing a
        quotation, and ``evidence_items`` is append-only -- so a row admitted
        without them could never afterwards be told apart from one that passed.
        ``app/ingestion/evidence.admissions`` holds both checks and refuses when
        it has nothing to check against.

        Steps 3 and 4 are where this build declines to invent two numbers, and
        each declination is disclosed in the response rather than hidden:

        * ``source_authority`` is NULL and the derived **source class** is
          recorded on the locator. The authority table is a
          ``(source class x predicate family)`` grid and an evidence item has
          no predicate; the claim does.
        * ``embedding`` is NULL. The applied column is ``VECTOR(1024)`` and
          admits only Titan; the shipping profile is 1536-wide Gemini. The
          response carries ``embedding_status`` so a caller learns the row will
          not retrieve at the moment it is created rather than months later
          from a silent absence.
        """
        artifact_id = binding.artifact_id
        if artifact_id is None:
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                details={
                    "reason": "RUN_HAS_NO_ARTIFACT",
                    "detail": (
                        "section 9.4 admits evidence from the artifact the run is bound "
                        "to, and this capability names none"
                    ),
                },
            )
        async with self._source.connection() as conn:
            row = await ingestion_artifacts.load_artifact(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                artifact_id=artifact_id,
            )
        if row is None:
            raise ApiError(ErrorCode.ARTIFACT_NOT_FOUND, details={"artifact_id": str(artifact_id)})

        content = ingestion_blocks.read_normalized_content(
            artifact_id=artifact_id,
            parser_status=str(row["parser_status"]),
            parser_metadata=row.get("parser_metadata"),
        )
        try:
            admitted = ingestion_evidence.admissions(
                candidates=list(payload.candidates), content=content
            )
        except ingestion_evidence.EvidenceRefusedError as exc:
            raise _evidence_refusal(exc) from exc

        source_class = ingestion_evidence.source_class_for(
            str(row["source_type"]), ses_verdicts=row.get("ses_verdicts")
        )
        async with self._source.connection() as conn:
            registered = await ingestion_evidence.register_admissions(
                conn,
                admitted,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                artifact_id=artifact_id,
                source_class=source_class,
                created_at=self._clock(),
                new_id=context.new_uuid7,
            )
        created = sum(1 for entry in registered if entry["created"])
        return {
            "evidence": registered,
            "created_count": created,
            "deduplicated_count": len(registered) - created,
            "embedding_status": "NOT_COMPUTED",
            "embedding_status_reason": ingestion_evidence.EMBEDDING_NOT_COMPUTED_REASON,
        }

    async def retrieve(self, binding: CapabilityBinding, payload: Any) -> Row:
        del binding, payload
        unbound("internal.retrieve")

    async def run_state_proof(self, binding: CapabilityBinding, case_id: uuid.UUID) -> Row | None:
        """Section 9.6. The same proof the human surface renders.

        Delegated to :class:`~...adapters.read.SqlReadPort` rather than
        reimplemented, and that is the whole point of the delegation: the
        agent and the user must be looking at the same state. Two
        implementations of "why does Provenance believe this" would eventually
        differ, and the difference would surface as a drafted letter citing
        grounding the user's screen never showed.

        The route has already run ``assert_within_capability`` against
        *case_id*, so a run bound to one case cannot reach another's proof --
        including one belonging to the same user.
        """
        return await self._read.state_proof(OwnerScope.of_binding(binding), case_id)

    # -- 9.7 - 9.9 --------------------------------------------------------

    async def submit_proposal(
        self, binding: CapabilityBinding, payload: Any, *, idempotency_key: str
    ) -> Row:
        """Section 9.7. The only write path an agent has, and it is two steps.

        **Step one is the app's write and step two is the Kernel's.** Write
        rule ``W4`` grants the app ``INSERT`` on ``memory_proposals`` and the
        Kernel only ``UPDATE``; ``commit_proposal`` settles a proposal and
        never creates one, and ``fk_kernel_decisions_proposal`` refuses the
        decision row when the proposal row is absent. So the order is a foreign
        key rather than a preference, and
        ``tests/api/test_proposal_submission.py`` asserts it as one.

        **The run row is read first, and not only for the attribution.** Three
        things come off it that the request must not assert: ``graph_name`` and
        ``graph_version``, which are ``NOT NULL`` columns; the ``model_route``
        that the claimed model id is checked against
        (``CANONICAL_DECISIONS.md`` -> *Disclosure*: the shipped model is
        checkable against persisted state rather than against a README); and
        ``is_counterfactual``, which nothing else on this path reads. The
        capability record carries no counterfactual flag, so without the check
        below the MEMORY OFF column of the Judge Mode comparison could commit
        canonical memory -- and ``ck_agent_runs_counterfactual_toolless``
        records that such a run was never given this tool at all.

        **Nothing is written when the proposal is refused.** Every refusal
        below happens before :func:`~...proposals.submission.register_proposal`,
        because a ``SUBMITTED`` row nobody will ever decide is worse than no
        row: it sits in the ledger looking like work in progress.
        """
        async with self._source.connection() as conn:
            run = await agent_runs.get_agent_run_for(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                run_id=binding.binding_id,
            )
        if run is None:
            # A capability that resolved and a run row that did not is a
            # cluster that changed underneath the request. Absent, not empty.
            raise ApiError(
                ErrorCode.AGENT_RUN_NOT_FOUND, details={"agent_run_id": str(binding.binding_id)}
            )
        if run.get("is_counterfactual"):
            raise ApiError(
                ErrorCode.CAPABILITY_SCOPE_MISMATCH,
                details={
                    "capability_kind": binding.binding_kind,
                    "reason": "COUNTERFACTUAL_RUN_HOLDS_NO_PROPOSAL_TOOL",
                    "memory_mode": run.get("memory_mode"),
                },
            )

        try:
            proposal = submission.build_proposal(
                payload, run=run, idempotency_key=idempotency_key, created_at=self._clock()
            )
        except submission.ProposalRefusedError as refusal:
            raise ApiError(
                ErrorCode.PROPOSAL_SCHEMA_INVALID,
                details={"reason": refusal.reason_code, **refusal.details},
            ) from refusal

        try:
            async with self._source.connection() as conn:
                await submission.register_proposal(
                    conn, proposal, tenant_id=binding.tenant_id, user_id=binding.user_id
                )
        except pgerr.CheckViolation as violation:
            # The database owns the admitted sets, and this reads its answer
            # rather than holding a second copy of them. Measured, not
            # hypothetical: on 2026-08-24 this exact statement was run against
            # `provenance` inside a rolled-back transaction with the model
            # route the two live `agent_runs` rows carry, and both shipping
            # ids -- `gemini-3.5-flash-lite` and `gemini-3.7-flash` -- came
            # back `constraint=ck_memory_proposals_model`. `0005` admits four
            # Bedrock-era ids and `deterministic.kernel`. `0009a` widens that
            # CHECK to the Gemini set and WAS applied to the live cluster on
            # 2026-08-28, so this path no longer fires there. (`0009`, the
            # embedding rewrite, is the revision that stays unapplied; they are
            # different and this comment used to conflate them.) The handler is
            # kept because a database still at `0008` refuses, and because the
            # refusal is worth naming rather than becoming a 500.
            #
            # An uncaught CheckViolation is a `500`: "something went wrong on
            # our side", which is untrue -- the database refused a specific
            # value for a specific, nameable reason -- and unactionable. This
            # says which constraint and which id, and the Kernel is never
            # called, so no decision row can reference a proposal that is not
            # there.
            raise ApiError(
                ErrorCode.PROPOSAL_SCHEMA_INVALID,
                details={
                    "reason": "PROPOSAL_ROW_REFUSED_BY_SCHEMA",
                    "constraint": violation.diag.constraint_name,
                    "model_id": proposal.model.model_id,
                    "proposal_type": str(proposal.proposal_type),
                },
            ) from violation

        result = await self._proposal_kernel.commit(
            proposal, principal=preflight.Principal(binding.tenant_id, binding.user_id)
        )
        return render.kernel_commit_result(result)

    async def create_action_intent(self, binding: CapabilityBinding, payload: Any) -> Row:
        del binding, payload
        unbound("internal.create_action_intent")

    async def complete_agent_run(self, binding: CapabilityBinding, payload: Any) -> Row:
        """Section 9.9. Settle the run, and burn the capability by doing it.

        "Any subsequent call with this id returns ``403 CAPABILITY_CONSUMED``"
        is section 9.9's closing sentence, and it is a *consequence* rather
        than a separate step: the capability read derives liveness from
        ``agent_runs.status`` (``adapters/directory.py``), so moving that
        column off ``RUNNING`` is what burns the token. While this method was
        unbound nothing moved it, and a run's credential stayed live until
        ``expires_at`` whatever the run had done.

        ``tool_calls``, ``model_calls`` and ``capability_status`` are the three
        columns Judge Mode reads, and they are **caller-reported by
        construction** -- migration ``0008``'s own column comment says so, and
        ``frontend/32_JUDGE_MODE.md`` section 6.4 discloses them as such.
        Persisting what the caller reported is not fabricating a trace;
        inventing the arrays would be, which is why every entry is a closed
        model and nothing here fills a missing measurement with a default.

        The one field this method decides rather than records is
        ``capability_status.proposal_tool_bound``, and only because
        ``ck_agent_runs_counterfactual_toolless`` is a CHECK on it: a
        counterfactual run may never claim the proposal tool, which is the same
        fact :meth:`submit_proposal` enforces by refusing one outright.
        """
        async with self._source.connection() as conn:
            run = await agent_runs.get_agent_run_for(
                conn,
                tenant_id=binding.tenant_id,
                user_id=binding.user_id,
                run_id=binding.binding_id,
            )
        if run is None:
            raise ApiError(
                ErrorCode.AGENT_RUN_NOT_FOUND, details={"agent_run_id": str(binding.binding_id)}
            )

        finished_at = self._clock()
        params = observability_runs.settle_params(
            run=run,
            tenant_id=binding.tenant_id,
            user_id=binding.user_id,
            agent_run_id=binding.binding_id,
            status=payload.status,
            error_code=payload.error_code,
            tool_calls=[call.model_dump(mode="json") for call in payload.tool_calls],
            model_calls=[call.model_dump(mode="json") for call in payload.model_calls],
            finished_at=finished_at,
        )
        try:
            async with self._source.connection() as conn:
                await observability_runs.settle_run(conn, params)
        except observability_runs.RunAlreadySettledError as settled:
            # Nothing was written, so the honest answer is the one section 9.9
            # gives a second call. A `200` here would tell the runtime its
            # trace had been recorded while the row still holds the first
            # call's arrays.
            raise ApiError(
                ErrorCode.CAPABILITY_CONSUMED,
                details={
                    "capability_kind": binding.binding_kind,
                    "agent_run_id": str(binding.binding_id),
                    "reason": "AGENT_RUN_ALREADY_SETTLED",
                },
            ) from settled

        started_at = run.get("started_at")
        return {
            "agent_run_id": str(binding.binding_id),
            "status": payload.status,
            # The capability lifecycle, not the JSONB column of the same name.
            # Section 9.9's `200` body prints the string; the column holds the
            # trace object. Two different facts that share a word.
            "capability_status": "CONSUMED",
            "finished_at": finished_at,
            "duration_ms": (
                None
                if started_at is None
                else int((finished_at - started_at).total_seconds() * 1000)
            ),
        }

    # -- 9.10 - 9.11 ------------------------------------------------------

    async def evaluate_trigger(self, binding: CapabilityBinding, payload: Any) -> Row:
        """Section 9.10. The wakeup is never proof that the condition holds.

        Everything below the envelope is re-read: the trigger row and the
        database clock together, then the case and its bound obligations in one
        read-only snapshot, then the predicate against those values. There is no
        ``force`` parameter on the way in and no branch here that could
        manufacture one.

        The capability supplies the identity and **nothing else**. Whether the
        trigger is this caller's is settled by the row (section 9.5), which is
        why a wake for another tenant's trigger comes back as ``ERROR /
        PROJECTION_FAILED`` with a 404 rather than as a refusal that confirms
        the id exists.
        """
        wake = trigger_service.scheduler_wake(
            trigger_id=binding.binding_id,
            evaluation_version=int(payload.evaluation_version),
            scheduled_for=payload.scheduled_for,
            trace_id=uuid.uuid4(),
        )
        return await self._evaluate(binding.tenant_id, binding.user_id, wake)

    async def _evaluate(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, wake: Any, *, dry_run: bool = False
    ) -> Row:
        """The one evaluation path, reached from the one place that holds it.

        Scheduler wakes and manual demo wakes differ in the envelope handed in
        and in nothing else. Two call sites that each assembled their own store,
        reader and kernel would be two paths wearing one function's name.
        """
        outcome = await trigger_service.evaluate_trigger(
            tenant_id=tenant_id,
            user_id=user_id,
            wake=wake,
            store=self._trigger_store,
            reader=self._projection_reader,
            kernel=self._trigger_kernel,
            dry_run=dry_run,
        )
        return render.trigger_evaluation(outcome)

    async def execute_action(self, binding: CapabilityBinding, payload: Any) -> Row:
        """Section 9.11. The only endpoint in the system with an external effect.

        The intent id comes from the **capability**, not from the path or the
        body: the executor's token is bound to one ``ACTION_INTENT`` row, so
        there is no argument through which it could name a different intent.

        The idempotency key is read off the intent row. Section 9.11: "The key
        **must** equal ``action_intents.idempotency_key``", and the executor
        refuses anything else with ``IDEMPOTENCY_CONFLICT`` rather than
        tolerating a mismatch -- an advisory idempotency key is a second send
        waiting for a retry.

        Minting one here looked reasonable and was wrong in two independent
        ways, either of which fails *every* legitimate execution with a `409`.
        Section 9.8 step 7 stores the Advocate's **request** key when one was
        supplied, so a minted key matches only when none was; and approving
        with even a one-word edit moves ``approval_draft_sha256`` off the
        creation digest, so a mint over that digest misses too. The row is the
        only place the true key lives, and this method already holds the row.

        Two outcomes come back as a ``200`` body rather than as an error, and
        section 9.11 is explicit about both: a provider-declared
        ``FAILED_RETRYABLE`` / ``FAILED_FINAL`` is canonical state rather than
        an HTTP-layer problem, and the operator kill switch produces a
        ``NOT_EXECUTED`` outcome in which nothing was attempted. A failed
        revalidation is the one that raises, carrying section 7.3's body and
        the whole blocking-reason list, and nothing is sent.

        Which code it raises is decided by
        :data:`~...adapters.action_errors.BLOCKING_STATUS` rather than being
        ``ACTION_STALE`` unconditionally. Section 9.11's own error list
        carries ``409 ACTION_NOT_APPROVABLE``, ``409 ACTION_ALREADY_EXECUTED``
        and ``422 RECIPIENT_NOT_ALLOWED`` beside ``409 ACTION_STALE``, and
        ``G9.6`` asks for ``409 NO_COMMITTED_BASIS`` specifically. The
        difference is not cosmetic: ``ACTION_STALE`` tells a client the world
        moved and to reload, and three of those four say the world did not
        move at all -- so answering ``ACTION_STALE`` to a de-allowlisted
        recipient or to an uncommitted basis sends the caller into a reload
        loop against a case that will read exactly the same next time.
        """
        scope = ActionScope.of(binding)
        async with self._source.connection() as conn:
            store = self._store(conn)
            intent = await store.load_intent(scope, binding.binding_id)
            if intent is None:
                # Section 9.11 lists `404 ACTION_INTENT_NOT_FOUND`, and
                # `InternalPort.execute_action` returns `Row` rather than
                # `Row | None`: the internal route has no `if row is None`
                # branch, so returning one would serialise a `200 null` body
                # for an intent that does not exist for this capability.
                raise ApiError(
                    ErrorCode.ACTION_INTENT_NOT_FOUND,
                    details={"action_intent_id": str(binding.binding_id)},
                )
            try:
                outcome = await self._executor(conn).execute(
                    scope,
                    binding.binding_id,
                    idempotency_key=intent.idempotency_key,
                    expected_draft_sha256=_expected_digest(payload),
                    expected_case_revision=getattr(payload, "expected_case_revision", None),
                )
            except ActionRefusedError as refusal:
                # Always raises here, unlike the public port: the only absent
                # code is ACTION_INTENT_NOT_FOUND, which `as_api_error` maps
                # to its own 404, and this route has no None branch to fall
                # into.
                raise action_errors.as_api_error(refusal) from refusal

        if outcome.status == "ABORTED_STALE":
            # Nothing was sent, an `ABORTED_STALE` attempt is on the ledger
            # carrying `blocking_reasons[0]` as its `error_code`, and the
            # status below is derived from that same element -- so the code
            # the client reads and the code the row records are one fact
            # rather than two that can drift apart in an incident review.
            raise action_errors.blocking_error(
                outcome.blocking_reasons,
                current_case_revision=outcome.case_revision,
                action_execution_id=(
                    None
                    if outcome.action_execution_id is None
                    else str(outcome.action_execution_id)
                ),
                attempt_no=outcome.attempt_no,
            )

        reasons = frozenset(outcome.blocking_reasons)
        # The kill switch short-circuits ahead of `revalidate`, so
        # `NOT_EXECUTED` is exactly the outcome in which no check ran.
        revalidated = outcome.status != "NOT_EXECUTED"
        return {
            "action_intent_id": str(outcome.action_intent_id),
            "action_execution_id": (
                None if outcome.action_execution_id is None else str(outcome.action_execution_id)
            ),
            "attempt_no": outcome.attempt_no,
            "status": outcome.status,
            "provider": outcome.provider,
            "provider_correlation_id": outcome.provider_correlation_id,
            "error_code": outcome.error_code,
            "blocking_reasons": list(outcome.blocking_reasons),
            "replayed": outcome.replayed,
            # Each flag is derived from the reason that would have set it,
            # never from "was anything blocking". Under the kill switch the
            # executor returns before revalidating at all, so every flag is
            # `None`: reporting `draft_hash_match: false` there would assert
            # the result of a comparison nothing performed, and `true` would
            # assert the other one just as falsely.
            "revalidation": {
                "case_revision": outcome.case_revision,
                "draft_hash_match": _flag(
                    revalidated, action_executor.DRAFT_HASH_CHANGED not in reasons
                ),
                "support_still_current": _flag(
                    revalidated, action_executor.SUPPORT_BELIEF_SUPERSEDED not in reasons
                ),
                "recipient_allowlisted": _flag(
                    revalidated, action_executor.RECIPIENT_NOT_ALLOWLISTED not in reasons
                ),
            },
            # The `action_executions.finished_at` the store wrote, read back
            # off the row -- never a clock this adapter takes. Stamping our own
            # would report when the adapter observed the return rather than
            # when the attempt was recorded, and only the second is auditable.
            # `None` under the kill switch, where no attempt exists to have
            # finished.
            "executed_at": outcome.finished_at,
            "case_revision_after": outcome.case_revision_after,
            "outbox_event_ids": [],
        }

    # -- 9.12 - 9.13 ------------------------------------------------------

    async def sweep_outbox(self, payload: Any) -> Row:
        """Section 9.12. Counts, never payloads.

        The lease is reclaimed **before** the sweep rather than after: a sweeper
        that died between claim and mark left its rows in ``DISPATCHING``, which
        the claim query deliberately cannot see, and reclaiming afterwards would
        strand them for one whole interval longer than necessary. A stranded
        ``trigger.fired.v1`` is a silently forgotten obligation.

        ``max_batches`` is a bound and not a target: the loop stops the moment a
        claim comes back empty, so a quiet outbox costs one query rather than
        twenty.
        """
        started = self._clock()
        dispatcher = OutboxDispatcher(
            store=self._outbox_store,
            transport=self._transport,
            batch_size=int(payload.batch_size),
        )
        reclaimed = await dispatcher.reclaim(now=started)
        claimed = dispatched = failed = dead = 0
        for _ in range(int(payload.max_batches)):
            result = await dispatcher.sweep(now=self._clock())
            claimed += result.claimed
            dispatched += result.published
            failed += result.failed
            dead += result.dead
            if result.claimed == 0:
                break

        finished = self._clock()
        return {
            "claimed": claimed,
            "dispatched": dispatched,
            "failed_retryable": failed,
            "dead": dead,
            "reaped_stale_claims": reclaimed,
            # `None` when nothing is pending. Not `0`: 'nothing is waiting' and
            # 'something is waiting and it is fresh' are opposite facts, and an
            # operator watching this number would read the first as the second.
            "oldest_pending_age_seconds": await self._oldest_pending(finished),
            "duration_ms": max(int((finished - started).total_seconds() * 1000), 0),
            "worker_id": payload.worker_id,
        }

    async def _oldest_pending(self, now: datetime) -> float | None:
        """The age of the oldest undispatched row, or ``None`` when there is none.

        Read through ``getattr`` because ``OutboxStore`` is a Protocol owned by
        the dispatcher and this is a reporting field rather than part of the
        state machine: widening the Protocol for it would make every
        implementation carry a method the dispatcher never calls.
        """
        probe = getattr(self._outbox_store, "oldest_pending_age_seconds", None)
        if probe is None:  # pragma: no cover - every shipped store has it
            return None
        return await probe(now=now)

    async def deliver_event(self, payload: Any) -> Row:
        """Section 9.13. ``event_id`` is the idempotency key, so there is no header.

        What this endpoint owns is section 12's dedupe transaction: the
        ``processed_events`` row and the consumer's local effect commit or roll
        back together, and a redelivery is a ``200 DUPLICATE_NOOP`` rather than
        an error, because a duplicate is the expected shape of an at-least-once
        contract and pushing benign redeliveries into a dead-letter queue would
        make that queue meaningless.

        **What it does not own, and does not pretend to.** Section 9.13's example
        ``effect`` is ``AGENT_RUN_STARTED``, produced by the ``advocate_dispatch``
        EventBridge target Lambda -- which left the design with the pivot to
        Cloud Run. No consumer's local effect is registered in this deployment,
        so the response reports the one thing that did happen: the delivery was
        recorded and deduped. Returning ``AGENT_RUN_STARTED`` with a null id, or
        a bare ``null`` beside ``PROCESSED``, would each read as work that
        occurred.

        ``SKIPPED_STALE`` is likewise never returned, and that too is a statement
        rather than an omission: it is the answer for a consumer whose projection
        is behind the event, and no consumer here maintains one.
        ``consumer.is_out_of_order`` is offered rather than assumed for exactly
        that reason, and a ``last_applied_version`` invented here to produce the
        value would silently drop late deliveries.
        """
        event = _published_event(payload.event)
        consumer = IdempotentConsumer(
            consumer_name=payload.consumer_name, unit_of_work=self._consumer_unit_of_work
        )
        result = await consumer.consume(event, _record_only)
        duplicate = result.outcome is ConsumptionOutcome.NOOP
        return {
            "result": "DUPLICATE_NOOP" if duplicate else "PROCESSED",
            "consumer_name": result.consumer_name,
            "event_id": str(result.event_id),
            # The ledger row's timestamp is the consumer's, not this adapter's,
            # and this adapter did not read it back. `None` says 'not read'
            # rather than inventing a plausible instant.
            "first_processed_at": None,
            "effect": (
                None if duplicate else {"kind": _DEDUPE_ONLY, "consumer_name": result.consumer_name}
            ),
        }


#: The only ``effect.kind`` this deployment can truthfully report. See
#: :meth:`KernelInternalPort.deliver_event` for why it is not
#: ``AGENT_RUN_STARTED`` and not ``None``.
_DEDUPE_ONLY: Final[str] = "DEDUPE_RECORDED"


async def _record_only(event: PublishedEvent) -> None:
    """The handler for a consumer with no local effect wired.

    Returns ``None`` so ``result_hash`` stays ``None`` and the ledger row
    records no digest: hashing 'no result' would write a fixed, meaningless 32
    bytes into every row and make 'these two runs produced the same result'
    true of runs that produced nothing at all.
    """
    del event
    return None


def _published_event(raw: Mapping[str, Any]) -> PublishedEvent:
    """Section 10.1's envelope as the dispatcher's own value object.

    The event type is checked against the closed catalogue here rather than
    trusted: an unknown type cannot have been written by the Kernel, so a dedupe
    row for it would be a permanent record of a delivery nothing can have sent.
    ``PublishedEvent`` re-checks the payload for document text and for the size
    cap on construction, which is the second half of the same rule.
    """
    event_type = str(raw["event_type"])
    if not catalogue.is_known_event_type(event_type):
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            details={"field": "event.event_type", "value": event_type},
        )
    try:
        return PublishedEvent(
            event_id=uuid.UUID(str(raw["event_id"])),
            event_type=event_type,
            aggregate_type=str(raw["aggregate_type"]),
            aggregate_id=uuid.UUID(str(raw["aggregate_id"])),
            aggregate_version=int(raw["aggregate_version"]),
            tenant_id=uuid.UUID(str(raw["tenant_id"])),
            user_id=uuid.UUID(str(raw["user_id"])),
            trace_id=uuid.UUID(str(raw["trace_id"])),
            occurred_at=raw["occurred_at"],
            payload_version=str(raw.get("payload_version", "1.0")),
            payload=raw.get("payload") or {},
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, details={"field": "event", "reason": str(error)}
        ) from error


def _expected_digest(payload: Any) -> bytes | None:
    """``expected_draft_sha256`` as bytes, or ``None`` when absent.

    Section 9.11 carries it as hex on the wire and the executor compares
    bytes. Converting here rather than inside the executor keeps the wire
    format a property of the HTTP boundary, which is where it belongs.
    """
    raw = getattr(payload, "expected_draft_sha256", None)
    if raw is None:
        return None
    if isinstance(raw, bytes | bytearray):
        return bytes(raw)
    return bytes.fromhex(str(raw))


def _flag(checked: bool, value: bool) -> bool | None:
    """*value* when the check ran, ``None`` when it did not.

    Section 9.11's ``200`` example shows three booleans because that example
    is the executed case. ``None`` is the only honest third state: a
    revalidation flag is a claim about a comparison, and no boolean
    truthfully describes a comparison that was never made.
    """
    return value if checked else None


#: Section 9.4's refusal codes, mapped to the API's own vocabulary in one place
#: so ``app/ingestion`` holds no dependency on the API layer -- the same split
#: ``action_errors`` and ``ProposalRefusedError`` already use.
_EVIDENCE_ERRORS: Final[Mapping[str, ErrorCode]] = {
    "PROPOSAL_FOREIGN_PROVENANCE": ErrorCode.PROPOSAL_FOREIGN_PROVENANCE,
    "VALIDATION_FAILED": ErrorCode.VALIDATION_FAILED,
    "PROVENANCE_UNCHECKABLE": ErrorCode.VALIDATION_FAILED,
}


def _evidence_refusal(exc: ingestion_evidence.EvidenceRefusedError) -> ApiError:
    """One refusal, one typed error, with the reason code kept in ``details``.

    ``PROVENANCE_UNCHECKABLE`` has no ``ErrorCode`` of its own and is reported
    as ``VALIDATION_FAILED`` carrying its own name, rather than being widened
    into ``PROPOSAL_FOREIGN_PROVENANCE``: the provenance is not foreign, it is
    unverifiable, and those need different fixes.
    """
    code = _EVIDENCE_ERRORS.get(exc.reason_code, ErrorCode.VALIDATION_FAILED)
    # ``setdefault`` and not a merge that overwrites: section 9.4 step 2 names
    # ``reason: "SPAN_NOT_IN_BLOCK"`` as the field a client branches on, and the
    # refusal already set it. Prefixing the outer code would replace the
    # specific answer with the generic one.
    details = dict(exc.details)
    details.setdefault("reason", exc.reason_code)
    return ApiError(code, details=details)
