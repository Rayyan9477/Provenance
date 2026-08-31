"""``WritePort`` -- the public mutating surface.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.14, 8.18-8.19, 8.22, 8.25-8.27, 8.30-8.31.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer*: "Only the deterministic
  Memory Kernel using ``pv_kernel_writer``. Agents never receive canonical
  write credentials."
- ``services/control_plane/app/actions/__init__.py`` -- the Phase 9 interface
  the three action methods below are bound to.
- ``tools/write_path_lint.py`` -- the structural check that keeps the first
  line true when somebody is in a hurry.

Why this class contains no SQL at all
--------------------------------------
Not one statement, and that is the point rather than an accident of how far
the work got. Each method either submits a typed proposal to the Memory
Kernel or delegates to a subsystem that owns the table it touches --
``app/actions`` for ``action_intents`` and ``action_executions``,
``app/ingestion`` for the evidence a correction becomes. A route holding a
connection and an ``INSERT`` is exactly what the write-path lint exists to
forbid, and an *adapter* holding one would be the same defect one indirection
further from the route.

``python -m tools.write_path_lint`` reports canonical write statements only
in ``app/memory_kernel`` and in the outbox dispatcher, which rule ``W5``
permits to set ``outbox_events.status`` and nothing else. ``action_intents``
and ``action_executions`` are not canonical -- they are in the app-permitted
enumeration -- so binding Phase 9 does not move that number, which is the
mechanism by which this rule is checkable rather than merely stated.

The connection this port takes, and the transaction it does not open
----------------------------------------------------------------------
The action store is handed one connection from the ``pv_app_reader_writer``
pool, and it does **not** own a transaction. That is deliberate and
``store_postgres.py`` says so: section 9.11 needs the ``action_executions``
attempt row *committed* before the provider call, so that a crash between
send and record leaves evidence that an attempt happened. Pooled connections
are opened ``autocommit=True`` (``provenance_db.pools``), so each statement
commits as it lands, which is the behaviour that requirement asks for.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

from services.control_plane.app.actions import (
    ActionIntentService,
    ActionPolicy,
    ActionRefusedError,
    ActionScope,
    ActionStore,
    ApproveRequest,
    PostgresActionStore,
    RejectRequest,
    UpdateDraftRequest,
)
from services.control_plane.app.api import context
from services.control_plane.app.api.adapters import render
from services.control_plane.app.api.adapters.action_errors import raise_as_api_error
from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.adapters.unbound import unbound
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import OwnerScope, ReadPort
from services.control_plane.app.counterfactual.probe import ModelProbeService
from services.control_plane.app.counterfactual.service import CounterfactualService
from services.control_plane.app.counterfactual.wiring import (
    default_counterfactual_service,
    default_probe_service,
)
from services.control_plane.app.ingestion import artifacts as ingestion_artifacts
from services.control_plane.app.ingestion import blocks as ingestion_blocks
from services.control_plane.app.memory_kernel.trigger_commit import KernelTriggerWriter
from services.control_plane.app.storage import (
    ObjectStore,
    ObjectStoreError,
    UnconfiguredObjectStore,
    raw_key,
)
from services.control_plane.app.triggers import service as trigger_service
from services.control_plane.app.triggers.store import SqlProjectionReader, SqlTriggerStore

__all__ = ["KernelWritePort"]

Row = dict[str, Any]

#: ``ck_source_artifacts_source_type`` admits seven values; section 8.18's MIME
#: allowlist has five entries. This is the mapping between them, and it is the
#: server's, not the caller's: ``UploadIntentRequest`` has no ``source_type``
#: field, because a caller that could name one could claim an uploaded
#: screenshot was inbound provider mail and inherit its authority band.
_SOURCE_TYPE_BY_MIME: Final[Mapping[str, str]] = {
    "message/rfc822": "UPLOAD_EML",
    "application/pdf": "UPLOAD_PDF",
    "image/png": "UPLOAD_IMAGE",
    "image/jpeg": "UPLOAD_IMAGE",
    "text/plain": "UPLOAD_TEXT",
}

StoreFactory = Callable[[Any], ActionStore]


class KernelWritePort:
    """The public mutating surface. Refuses precisely, never vaguely.

    Holds the Kernel pool so the methods that end at ``commit_proposal`` have
    it without a second wiring pass. Holding a pool is not the same as holding
    a statement: the pool is passed to
    ``memory_kernel.transaction.commit_proposal``, which is the only module in
    the repository that issues a canonical write.
    """

    __slots__ = (
        "_clock",
        "_counterfactual",
        "_kernel_pool",
        "_model_route",
        "_objects",
        "_policy",
        "_probe",
        "_projection_reader",
        "_read",
        "_recorder",
        "_source",
        "_store",
        "_trigger_kernel",
        "_trigger_store",
        "_upload_ttl",
    )

    #: Annotated rather than left to inference so `start_counterfactual` and
    #: `run_probe` have a return type mypy can check; the constructor still
    #: takes `Any` so the hermetic suites can inject a double.
    _counterfactual: CounterfactualService
    _probe: ModelProbeService

    def __init__(
        self,
        source: ConnectionSource,
        *,
        kernel_pool: Any,
        read: ReadPort,
        policy: ActionPolicy,
        clock: Callable[[], datetime],
        recorder: Any = None,
        store_factory: StoreFactory = PostgresActionStore,
        trigger_store: Any = None,
        projection_reader: Any = None,
        trigger_kernel: Any = None,
        objects: ObjectStore | None = None,
        model_route: Mapping[str, str] | None = None,
        upload_url_ttl_seconds: int = 900,
        counterfactual: Any = None,
        probe: Any = None,
    ) -> None:
        self._source = source
        self._kernel_pool = kernel_pool
        self._read = read
        self._policy = policy
        self._clock = clock
        self._recorder = recorder
        self._store = store_factory
        # The manual wake's three boundaries, and they are the SAME three the
        # scheduled wake uses. Overridable so the hermetic suites can drive the
        # real adapter with no cluster.
        self._trigger_store = trigger_store or SqlTriggerStore(source)
        self._projection_reader = projection_reader or SqlProjectionReader(source)
        self._trigger_kernel = trigger_kernel or KernelTriggerWriter(kernel_pool)
        # Sections 8.18 and 8.19 both end at bytes. The store is injected for
        # the same reason the three boundaries above are: the hermetic suites
        # drive the real adapter against a real filesystem store and no cluster.
        self._objects = objects if objects is not None else UnconfiguredObjectStore("PV_PLATFORM")
        self._model_route = dict(model_route or ingestion_artifacts.DEFAULT_MODEL_ROUTE)
        self._upload_ttl = upload_url_ttl_seconds
        # Sections 8.30, 8.31 and 8.33. Injected for the same reason as the
        # boundaries above -- the hermetic suites drive the real adapter over
        # the real graph with a scripted router and no cluster -- and defaulted
        # so `build_runtime` needs no extra wiring pass.
        self._counterfactual = (
            counterfactual
            if counterfactual is not None
            else default_counterfactual_service(source, read=read, clock=clock)
        )
        self._probe = probe if probe is not None else default_probe_service(clock=clock)

    def _service(self, conn: Any) -> ActionIntentService:
        """One service per connection, per call.

        Not cached on the instance: the service holds a store, the store holds
        a connection, and a connection belongs to whoever checked it out of
        the pool for the duration of one request. Caching it would hand a
        returned connection to the next caller.
        """
        kwargs: dict[str, Any] = {
            "store": self._store(conn),
            "policy": self._policy,
            "clock": self._clock,
        }
        if self._recorder is not None:
            kwargs["recorder"] = self._recorder
        return ActionIntentService(**kwargs)

    # -- 8.14 -------------------------------------------------------------

    async def create_correction(
        self, scope: OwnerScope, case_id: uuid.UUID, payload: Any
    ) -> Row | None:
        del scope, case_id, payload
        unbound("write.create_correction")

    # -- 8.18 - 8.20 ------------------------------------------------------

    async def upload_intent(self, scope: OwnerScope, payload: Any) -> Row:
        """Section 8.18. The server chooses the key, and only the server can.

        ``UploadIntentRequest`` has no ``s3_key`` field and forbids extras. The
        three components of the key come from the resolved principal and a
        freshly minted artifact id, so a client holding a valid target still
        cannot redirect the upload into another tenant's prefix.

        **``sha256`` is required by this deployment even though section 8.18
        marks it optional, and the reason is a column rather than a
        preference.** The section says "a ``source_artifacts`` row is created
        immediately with ``parser_status = 'PENDING'``", and
        ``content_sha256`` on that table is ``NOT NULL`` with
        ``ck_source_artifacts_sha_len`` requiring 32 bytes. There is no value to
        write before the bytes exist. Deferring the row instead would leave
        ``/complete`` with no artifact to find and turn every completion into a
        404, so the refusal happens here, at the point where it names the
        column a caller can satisfy.

        The upload target carries the transport it actually offers. On a cloud
        store that is a pre-signed ``PUT``; on the filesystem store it is a
        ``file:`` locator, disclosed as ``LOCAL_FILESYSTEM`` rather than
        presented as a URL a browser could use.
        """
        if not payload.sha256:
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                details={
                    "reason": "SHA256_REQUIRED",
                    "field": "sha256",
                    "detail": (
                        "source_artifacts.content_sha256 is NOT NULL and section 8.18 "
                        "creates the row at upload-intent, so the digest has to be "
                        "declared before the bytes are sent"
                    ),
                },
            )
        source_type = _SOURCE_TYPE_BY_MIME.get(payload.mime_type)
        if source_type is None:
            raise ApiError(
                ErrorCode.UNSUPPORTED_MIME_TYPE,
                details={"allowed": sorted(_SOURCE_TYPE_BY_MIME)},
            )

        now = self._clock()
        async with self._source.connection() as conn:
            existing = await ingestion_artifacts.existing_artifact_id(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content_sha256_hex=payload.sha256,
                source_type=source_type,
                source_message_id=None,
            )
        # Section 8.19 step 4's rule, applied one step earlier because
        # `uq_source_artifacts_content` would refuse the INSERT anyway: the same
        # bytes get the same artifact and therefore the same key, so re-offering
        # an upload is idempotent rather than a 409.
        artifact_id = existing if existing is not None else context.new_uuid7()
        key = raw_key(tenant_id=scope.tenant_id, user_id=scope.user_id, artifact_id=artifact_id)
        try:
            target = await self._objects.upload_target(
                key, content_type=payload.mime_type, ttl_seconds=self._upload_ttl
            )
            bucket = self._objects.bucket
        except ObjectStoreError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                details={"dependency": "OBJECT_STORE", "detail": str(exc)},
            ) from exc

        if existing is None:
            row = ingestion_artifacts.ArtifactRow(
                artifact_id=artifact_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                source_type=source_type,
                s3_bucket=bucket,
                s3_key=key,
                content_sha256_hex=payload.sha256,
                size_bytes=payload.size_bytes,
                mime_type=payload.mime_type,
                received_at=now,
                created_at=now,
                parser_status="PENDING",
                # `ck_source_artifacts_parsed_has_version` only requires a
                # version once the status is PARSED, and claiming one now would
                # name a parser that has not run.
                parser_version=None,
                parser_metadata=None,
                subject=payload.filename,
            )
            async with self._source.connection() as conn:
                await ingestion_artifacts.insert_artifact(conn, row)

        return {
            "artifact_id": str(artifact_id),
            "upload_url": target.url,
            "http_method": target.http_method,
            "upload_transport": target.transport,
            "required_headers": dict(target.required_headers),
            "max_size_bytes": target.max_size_bytes,
            "expires_at": target.expires_at,
            "s3_key": key,
        }

    async def complete_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, payload: Any
    ) -> Row | None:
        """Section 8.19, steps 1 to 5. Returns without waiting for a graph.

        Steps 1, 2 and 3 are a ``HeadObject``, a length comparison and a digest
        comparison **against the stored object**, and they are the only reason
        the declared values on the row are worth anything: everything before
        this point is a claim by the client. Step 3 is done by streamed
        recomputation, which section 8.19 admits as the same check for objects
        under 8 MiB and which is what a store with no checksum field leaves.

        Step 4's dedupe already happened at upload-intent, where
        ``uq_source_artifacts_content`` forced it -- the same bytes resolve to
        the same artifact and therefore the same key. A completion of an
        already-completed artifact is ``409 ARTIFACT_ALREADY_COMPLETED`` rather
        than a second run.

        Step 5 is where this build stops short and says so. It parses, stores
        the blocks, and opens the ``agent_runs`` capability row a run would
        present -- all of which are real and durable. It does **not** invoke an
        interpretation worker, because none is deployed, and it does not write
        the ``artifact.received.v1`` outbox event, because ``outbox_events``
        INSERT is Kernel-only under write rule ``W1`` and the app holds no
        grant for it. The response says both rather than implying a pipeline
        that is not running.
        """
        async with self._source.connection() as conn:
            row = await ingestion_artifacts.load_artifact(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                artifact_id=artifact_id,
            )
        if row is None:
            return None
        if str(row["parser_status"]) != "PENDING":
            raise ApiError(
                ErrorCode.ARTIFACT_ALREADY_COMPLETED,
                details={
                    "artifact_id": str(artifact_id),
                    "parser_status": str(row["parser_status"]),
                },
            )

        key = str(row["s3_key"])
        try:
            head = await self._objects.head(key)
        except ObjectStoreError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                details={"dependency": "OBJECT_STORE", "detail": str(exc)},
            ) from exc
        if head is None:
            raise ApiError(
                ErrorCode.ARTIFACT_OBJECT_MISSING,
                details={"artifact_id": str(artifact_id), "s3_key": key},
            )

        declared_size = payload.size_bytes or int(row["size_bytes"])
        if head.size_bytes != declared_size:
            raise ApiError(
                ErrorCode.ARTIFACT_SIZE_MISMATCH,
                details={
                    "declared_size_bytes": declared_size,
                    "stored_size_bytes": head.size_bytes,
                },
            )
        declared_sha = payload.sha256 or str(row["content_sha256"])
        if head.sha256_hex != declared_sha:
            raise ApiError(
                ErrorCode.ARTIFACT_HASH_MISMATCH,
                details={
                    "declared_sha256": declared_sha,
                    "computed_sha256": head.sha256_hex,
                },
            )

        data = await self._objects.get(key)
        # Belt and braces, and it is not redundant: `head` recomputes on a
        # filesystem store but reads a stored checksum on S3, and the object
        # could in principle change between the two calls. The bytes that are
        # parsed are the bytes that are digested here.
        if hashlib.sha256(data).hexdigest() != declared_sha:
            raise ApiError(
                ErrorCode.ARTIFACT_HASH_MISMATCH,
                details={
                    "declared_sha256": declared_sha,
                    "computed_sha256": hashlib.sha256(data).hexdigest(),
                },
            )

        parse = ingestion_blocks.parse_artifact(
            artifact_id=artifact_id, mime_type=str(row["mime_type"]), data=data
        )
        trace_id = context.new_uuid7()
        run_id = context.new_uuid7()
        now = self._clock()
        async with self._source.connection() as conn:
            await ingestion_artifacts.mark_parsed(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                artifact_id=artifact_id,
                parser_status=parse.status.value,
                parser_version=parse.parser_version,
                parser_metadata=(
                    ingestion_blocks.parser_metadata_value(parse)
                    if parse.parser_version is not None
                    else None
                ),
                updated_at=now,
            )
            opened = await ingestion_artifacts.open_agent_run(
                conn,
                run_id=run_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                trace_id=trace_id,
                artifact_id=artifact_id,
                model_route=self._model_route,
                started_at=now,
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
            "poll": {
                "artifact_url": f"/v1/artifacts/{artifact_id}",
                "trace_url": f"/v1/traces/{trace_id}",
                "suggested_interval_ms": 1500,
            },
        }

    # -- 8.22 -------------------------------------------------------------

    async def rotate_ingest_alias(self, scope: OwnerScope) -> Row:
        del scope
        unbound("write.rotate_ingest_alias")

    # -- 8.25 - 8.27, bound to app/actions --------------------------------

    async def update_draft(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None:
        """Section 8.25. Provenance does not refuse a user's own words.

        It records which sentences the committed record no longer supports and
        returns them in ``warnings``, so the approval screen can show exactly
        what is grounded and what is the user speaking for themselves.
        ``recipient`` is not editable and there is no parameter for it here:
        changing the recipient after grounding validation ran against a
        specific counterparty would change the action's blast radius.
        """
        async with self._source.connection() as conn:
            try:
                update = await self._service(conn).update_draft(
                    ActionScope.of(scope),
                    action_intent_id,
                    UpdateDraftRequest(
                        subject=payload.subject,
                        body=payload.body,
                        client_case_revision=payload.client_case_revision,
                    ),
                )
            except ActionRefusedError as refusal:
                raise_as_api_error(refusal)
                return None
        intent = update.intent
        return {
            "action_intent_id": str(intent.id),
            "status": intent.status,
            "draft_sha256": intent.draft_sha256.hex(),
            "previous_draft_sha256": update.previous_draft_sha256.hex(),
            "claims_revalidated": update.claims_revalidated,
            "warnings": list(update.warnings),
            "current_case_revision": await self._read.case_revision(scope, intent.case_id),
        }

    async def approve(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None:
        """Section 8.26, the human authorisation boundary.

        ``approved_by_user_id`` comes from the **scope**, never from the body.
        The request model has no field for it and this adapter would not read
        one if it did: the approver is whoever the token resolved to, and a
        body-supplied approver is an authorisation decision made by the
        caller.

        The hash is computed over the *client-submitted* draft, which is what
        makes a race between an edit and an approval unable to send a
        different message from the one that was on the user's screen.
        """
        approved_draft = payload.approved_draft
        async with self._source.connection() as conn:
            try:
                record = await self._service(conn).approve(
                    ActionScope.of(scope),
                    action_intent_id,
                    ApproveRequest(
                        approved_draft=(
                            approved_draft.model_dump()
                            if hasattr(approved_draft, "model_dump")
                            else dict(approved_draft)
                        ),
                        client_case_revision=payload.client_case_revision,
                        approved_by_user_id=scope.user_id,
                        acknowledge_warnings=tuple(payload.acknowledge_warnings or ()),
                    ),
                )
            except ActionRefusedError as refusal:
                raise_as_api_error(refusal)
                return None
        intent = record.intent
        return {
            "action_intent_id": str(intent.id),
            "status": intent.status,
            "approval_draft_sha256": (
                None if intent.approval_draft_sha256 is None else intent.approval_draft_sha256.hex()
            ),
            "approved_at": intent.approved_at,
            "approved_case_revision": record.approved_case_revision,
            "case_revision_after": record.case_revision_after,
            # Section 8.26 shows `{"status": "QUEUED", "outbox_event_id": ...}`.
            # The outbox event is written by the Kernel-backed
            # `CanonicalRecorder`, and under the `NullRecorder` this deployment
            # currently binds there is no event and nothing is queued. `None`
            # is the truthful answer; reporting `QUEUED` with a null id would
            # claim a dispatch that will never happen.
            "execution": None,
            "trace_id": None,
        }

    async def reject(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None:
        """Section 8.27. A rejection is evidence about the user's own position.

        Recorded, not discarded: it appears in the timeline, and
        ``WRONG_FACTS`` in particular usually means the memory behind the
        draft is wrong rather than the draft.
        """
        async with self._source.connection() as conn:
            try:
                record = await self._service(conn).reject(
                    ActionScope.of(scope),
                    action_intent_id,
                    RejectRequest(
                        reason_code=payload.reason_code,
                        reason_text=payload.reason_text,
                    ),
                )
            except ActionRefusedError as refusal:
                raise_as_api_error(refusal)
                return None
        intent = record.intent
        return {
            "action_intent_id": str(intent.id),
            "status": intent.status,
            "rejected_at": intent.rejected_at,
            "case_revision_after": record.case_revision_after,
        }

    # -- 8.30 - 8.31 ------------------------------------------------------

    async def start_counterfactual(self, scope: OwnerScope, payload: Any) -> Row | None:
        """Section 8.30. Neither mode writes canonical state, and it is measured.

        Delegated whole to ``app/counterfactual``: the two graph walks, the two
        ``agent_runs`` rows and the before/after revision reads belong to the
        module that owns them, and an adapter holding a model call would be the
        transaction-purity failure one indirection from the route.

        ``None`` when the artifact is not this scope's, which the route maps to
        ``404 ARTIFACT_NOT_FOUND`` and never to a ``403`` (section 1.7).
        """
        return await self._counterfactual.start(scope, payload)

    async def get_counterfactual(
        self, scope: OwnerScope, counterfactual_id: uuid.UUID
    ) -> Row | None:
        """Section 8.31. The parity block is computed from the persisted rows.

        The route gates rendering on ``parity.all_equal``; this returns the
        block whatever it says, because a suppressed comparison and a missing
        one are different answers and the judge is entitled to see which.
        """
        return await self._counterfactual.get(scope, counterfactual_id)

    # -- 8.30's manual wake, and the model probe --------------------------

    async def wake_trigger(
        self, scope: OwnerScope, trigger_id: uuid.UUID, payload: Any
    ) -> Row | None:
        """``16_TRIGGER_DSL.md`` section 13.2 -- the judge's button.

        It is **not** a shortcut, a mock, a fixture or a forced fire. There is
        no ``force`` parameter here and adding one is prohibited: this builds an
        ordinary wake envelope, differing from the scheduled one in exactly two
        fields (``wake_source`` and ``wake_id``), and calls the identical
        ``evaluate_trigger`` function. The guards, the projection read, the
        predicate, the Memory Kernel, the serializable transaction, the revision
        guard and the idempotency record are all on the path.

        That is what makes pressing it in front of a judge prove *more* rather
        than less. Press it twice and the second press reaches guard G2 and
        answers ``NO_OP / TRIGGER_NOT_ARMED``; press it on a deposit that was
        actually returned and it no-ops on stage, which is the better demo.

        The generation is read from the trigger row rather than taken from the
        request, and this is the one place the manual path could have gone wrong:
        a client-supplied ``evaluation_version`` would let a caller replay a
        superseded generation, or -- worse -- guess the current one and have the
        wake accepted for a trigger it had never seen.

        Returns ``None`` when the trigger is not this scope's, which the route
        maps to a typed 404 and never to a 403 (section 1.7).
        """
        snapshot = await self._trigger_store.load(
            tenant_id=scope.tenant_id, user_id=scope.user_id, trigger_id=trigger_id
        )
        if snapshot is None:
            return None

        wake = trigger_service.manual_wake(
            trigger_id=trigger_id,
            evaluation_version=int(snapshot.row["evaluation_version"]),
            # The instant the schedule WOULD have used, kept for the trace and
            # never compared against anything. `not_before` is what the arm
            # intended; the database clock is what decides.
            scheduled_for=snapshot.row["not_before"] or snapshot.db_now,
            trace_id=uuid.uuid4(),
            client_idempotency_key=_client_key(payload),
        )
        outcome = await trigger_service.evaluate_trigger(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            wake=wake,
            store=self._trigger_store,
            reader=self._projection_reader,
            kernel=self._trigger_kernel,
            dry_run=bool(getattr(payload, "dry_run", False)),
        )
        return render.trigger_evaluation(outcome)

    async def run_probe(self, scope: OwnerScope, payload: Any) -> Row:
        """Section 8.33. Invokes the configured ids and reports what answered.

        ``scope`` is deliberately unused: a probe is a statement about this
        deployment's model access, not about the caller's data, and there is
        nothing owner-scoped for it to read. The route still requires a human
        token and ``judge_mode_enabled``; what stops one user probing another's
        anything is that there is no such thing.
        """
        del scope
        return await self._probe.run(payload)


def _client_key(payload: Any) -> str:
    """The client's ``Idempotency-Key``, or a fresh one.

    It is the last segment of the wake id, so two presses of the button under
    one key are one logical wake and the second gets the stored result back.
    Minting one when the caller supplied none is correct rather than lax: the
    generation guard and the trigger state already make a second fire
    impossible, and a constant substituted here would make two genuinely
    different manual wakes collide.
    """
    supplied = getattr(payload, "idempotency_key", None)
    return str(supplied) if supplied else uuid.uuid4().hex
