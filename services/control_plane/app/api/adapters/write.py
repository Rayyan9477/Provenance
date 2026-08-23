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

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

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
from services.control_plane.app.api.adapters import render
from services.control_plane.app.api.adapters.action_errors import raise_as_api_error
from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.adapters.unbound import unbound
from services.control_plane.app.api.ports import OwnerScope, ReadPort
from services.control_plane.app.memory_kernel.trigger_commit import KernelTriggerWriter
from services.control_plane.app.triggers import service as trigger_service
from services.control_plane.app.triggers.store import SqlProjectionReader, SqlTriggerStore

__all__ = ["KernelWritePort"]

Row = dict[str, Any]

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
        "_kernel_pool",
        "_policy",
        "_projection_reader",
        "_read",
        "_recorder",
        "_source",
        "_store",
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
        clock: Callable[[], datetime],
        recorder: Any = None,
        store_factory: StoreFactory = PostgresActionStore,
        trigger_store: Any = None,
        projection_reader: Any = None,
        trigger_kernel: Any = None,
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
        del scope, payload
        unbound("write.upload_intent")

    async def complete_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, payload: Any
    ) -> Row | None:
        del scope, artifact_id, payload
        unbound("write.complete_artifact")

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
        del scope, payload
        unbound("write.start_counterfactual")

    async def get_counterfactual(
        self, scope: OwnerScope, counterfactual_id: uuid.UUID
    ) -> Row | None:
        del scope, counterfactual_id
        unbound("write.get_counterfactual")

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
        del scope, payload
        unbound("write.run_probe")


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
