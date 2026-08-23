"""``T9.4``-``T9.6`` -- the only code in this system that can cause an external effect.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*: ... **bind
  approval to case revision and draft SHA-256, revalidate, execute
  idempotently.** The last three clauses are this module.
- ``docs/specs/15_API_SPEC.md`` section 9.11 -- the revalidation gate, verbatim.
- ``docs/specs/10_DATABASE_DDL.md`` sections 10 and 13, and section 19 test 7.
- ``docs/quality/23_PHASE_GATES.md`` ``G9.1``, ``G9.4``, ``G9.5``, ``G9.6``,
  ``G9.7``.

Revalidation happens **at execution time**, not only at approval time
----------------------------------------------------------------------
An approval is a statement about a world that was true when the human read it.
Between the click and the send, contradicting evidence can arrive, a belief can
be superseded, the case can move, an operator can narrow the allowlist. Each of
those makes the approved message a message about a world that no longer exists,
and each of them is re-checked here, immediately before the send, with the
intent row loaded fresh.

Why the network call is outside the transaction
------------------------------------------------
Section 9.11: "set ``status = 'EXECUTING'``, insert ``action_executions`` with
the next ``attempt_no`` and ``request_sha256``, commit, **then** call the
provider outside the transaction". Two reasons and both are load-bearing. A
retryable transaction callback runs once per attempt, so a send inside it is a
send per retry; and a transaction that rolls back cannot un-send an e-mail.
``tools/txn_purity_lint.py`` enforces the shape structurally --
:meth:`ActionExecutor.execute` is not a transaction callback and reaches no
banned root.

The ``PV_SABOTAGE`` hook, and why it is a predicate
----------------------------------------------------
``T9.4``, fourth sub-task: add the hook on ``actions.executor.revalidate_revision``
so ``G9.7`` can neuter it and DDL section 19 test 7 must go red. ``PV_SABOTAGE``
replaces the symbol with the identity function, and an identity function
returns its (truthy) argument -- so the symbol must be shaped such that
"truthy" is the **unsafe** answer. A predicate returning "the revision still
matches" degrades to *yes, still valid*, the stale send proceeds, and ``G9.1``
goes red on both of its assertions. A function returning reason codes would
degrade to a ``TypeError``, which is also red but red for a reason that has
nothing to do with revision binding.

``tests/sabotage_matrix.yaml`` is outside this task's file ownership; the entry
is reported in the handover rather than added here. The hook itself is live and
:func:`revalidate` reaches it through the module global, never a ``from``-import
-- a ``from``-import copies the reference before the rebind is visible and the
sabotage silently never arrives.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from provenance_domain import money
from services.control_plane.app.actions import drafts, intents
from services.control_plane.app.actions import support_validation as sv
from services.control_plane.app.actions.policy import (
    ACTION_EXECUTION_DISABLED,
    RECIPIENT_NOT_ALLOWLISTED,
    ActionPolicy,
)
from services.control_plane.app.actions.sink import ActionSink, SinkMessage
from services.control_plane.app.actions.store import (
    ActionScope,
    ActionStore,
    CanonicalRecorder,
    NewActionExecution,
    NullRecorder,
)

__all__ = [
    "ACTION_EXECUTION_DISABLED",
    "ALREADY_EXECUTED",
    "CASE_REVISION_MOVED",
    "DRAFT_HASH_CHANGED",
    "MAX_ATTEMPTS",
    "NOT_APPROVED",
    "NO_COMMITTED_BASIS",
    "RECIPIENT_NOT_ALLOWLISTED",
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "SUPPORT_BELIEF_SUPERSEDED",
    "WRITTEN_EXECUTION_STATUSES",
    "ActionExecutor",
    "ExecutionOutcome",
    "ProviderFinalError",
    "ProviderTransientError",
    "RevalidationInput",
    "RevisionCheck",
    "revalidate",
    "revalidate_revision",
]

# --- blocking reason codes --------------------------------------------------
#
# Written to ``action_executions.error_code``. ``CASE_REVISION_MOVED`` is
# ``G9.1``'s literal string and wins here over
# ``ActionIntentView.executability``'s ``CASE_REVISION_CHANGED``: the gate reads
# the ledger row, so the ledger row carries the gate's word. The divergence is
# recorded rather than reconciled by editing a contract this task does not own.
#
# THIS LIST IS EXACTLY WHAT ``revalidate`` CAN RETURN -- not this package's
# whole refusal vocabulary. That is a boundary, not an omission, and one an
# adapter's status table is entitled to trust: a code bound here is a code that
# can reach ``action_executions.error_code``, and a code that is not is one no
# execution attempt can ever carry.
#
# ``NO_COMMITTED_BASIS`` is re-exported from ``support_validation`` because
# ``revalidate`` genuinely returns it -- the basis can be withdrawn between the
# approval and the send. ``SUPPORT_SET_UNAVAILABLE`` is deliberately NOT here,
# and adding it "for completeness" would be wrong twice over: it is raised by
# ``ActionIntentService.create`` before any intent exists, so no execution can
# report it, and it is a server-side omission rather than a reason a send was
# refused. Reach it as ``sv.SUPPORT_SET_UNAVAILABLE`` if you need the string.

NOT_APPROVED: Final[str] = "NOT_APPROVED"
CASE_REVISION_MOVED: Final[str] = "CASE_REVISION_MOVED"
DRAFT_HASH_CHANGED: Final[str] = "DRAFT_HASH_CHANGED"
SUPPORT_BELIEF_SUPERSEDED: Final[str] = "SUPPORT_BELIEF_SUPERSEDED"
ALREADY_EXECUTED: Final[str] = "ALREADY_EXECUTED"
NO_COMMITTED_BASIS: Final[str] = sv.NO_COMMITTED_BASIS

#: ``ck_action_executions_attempt_no`` is ``attempt_no >= 1 AND <= 5``.
MAX_ATTEMPTS: Final[int] = 5

#: Every value this module writes to ``action_executions.status``, declared so
#: it can be driven through the real table by
#: ``tests/db/test_action_plane.py::test_every_ledger_status_the_executor_writes_is_accepted_by_the_table``.
#:
#: It exists because of an asymmetry that reads like a defect and is not. A
#: refusal for ``NO_COMMITTED_BASIS`` -- or for an unallowlisted recipient, or
#: for a superseded belief -- is recorded as ``ABORTED_STALE`` even though
#: nothing about the intent was stale, because ``ck_action_executions_status``
#: admits exactly five values and ``ABORTED_STALE`` is the only terminal
#: refusal among them. ``error_code`` carries which of the seven blocking
#: reasons fired; ``status`` answers the coarser question the schema has room
#: for. So the HTTP body can legitimately say ``409 NO_COMMITTED_BASIS`` while
#: the ledger row says ``ABORTED_STALE``, and the two are reconciled by
#: ``error_code`` rather than in contradiction.
#:
#: The obvious repair is a sixth status naming the reason. It would be refused
#: by the database at runtime -- a ``23514`` mid-demo, on the one operation
#: that cannot be undone -- and schema past ``0008`` is not this phase's to
#: write. Extending this tuple without extending the CHECK fails in the db
#: lane instead.
WRITTEN_EXECUTION_STATUSES: Final[tuple[str, ...]] = (
    "STARTED",
    "SUCCEEDED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
    "ABORTED_STALE",
)


class ProviderTransientError(RuntimeError):
    """The provider refused in a way another attempt could survive."""


class ProviderFinalError(RuntimeError):
    """The provider refused in a way another attempt cannot survive."""


@dataclass(frozen=True, slots=True)
class RevisionCheck:
    """The three revisions a send is judged against.

    ``expected_case_revision`` is the caller's stated belief about the world
    (section 9.11's ``request.expected_case_revision``). ``None`` means the
    caller offered no belief, which is permitted; offering a wrong one is not.
    """

    basis_case_revision: int
    current_case_revision: int
    expected_case_revision: int | None


def revalidate_revision(check: RevisionCheck) -> bool:
    """Is the approval still bound to the revision the case is actually at?

    THE ``PV_SABOTAGE`` SYMBOL. See the module docstring for why it is a
    predicate rather than a reason-code builder.
    """
    if (
        check.expected_case_revision is not None
        and check.expected_case_revision != check.current_case_revision
    ):
        return False
    return check.basis_case_revision == check.current_case_revision


@dataclass(frozen=True, slots=True)
class RevalidationInput:
    """Everything section 9.11's gate reads, as one value object.

    Assembled from a single fresh read of the intent and its case, so the whole
    gate is decided against one point-in-time view. Assembling it from several
    reads is the worked BLOCKER ``ops/gates/PHASE_09.md`` names: "the executor
    revalidating on one connection and sending on another".
    """

    status: str
    basis_case_revision: int
    current_case_revision: int
    expected_case_revision: int | None
    approval_draft_sha256: bytes | None
    draft_payload_sha256: bytes
    expected_draft_sha256: bytes | None
    supporting_belief_versions: frozenset[uuid.UUID]
    current_belief_version_ids: frozenset[uuid.UUID]
    has_successful_execution: bool
    recipient_allowlisted: bool
    has_committed_basis: bool


def revalidate(inp: RevalidationInput) -> tuple[str, ...]:
    """Every blocking reason, in the order a reviewer should read them.

    Fails closed by construction: the function returns *reasons*, and the
    caller sends only on an empty tuple. There is no branch anywhere that
    sends because a check was skipped -- a skipped check contributes no reason
    and an unreached check contributes none either, which is why the checks are
    a flat list rather than a chain of early returns.
    """
    reasons: list[str] = []
    if not inp.has_committed_basis:
        reasons.append(NO_COMMITTED_BASIS)
    if inp.status != "APPROVED":
        reasons.append(NOT_APPROVED)
    # Reached through the module global: PV_SABOTAGE rebinds this name.
    if not revalidate_revision(
        RevisionCheck(
            basis_case_revision=inp.basis_case_revision,
            current_case_revision=inp.current_case_revision,
            expected_case_revision=inp.expected_case_revision,
        )
    ):
        reasons.append(CASE_REVISION_MOVED)
    if (
        inp.approval_draft_sha256 is None
        or inp.approval_draft_sha256 != inp.draft_payload_sha256
        or (
            inp.expected_draft_sha256 is not None
            and inp.expected_draft_sha256 != inp.draft_payload_sha256
        )
    ):
        reasons.append(DRAFT_HASH_CHANGED)
    if not inp.supporting_belief_versions.issubset(inp.current_belief_version_ids):
        reasons.append(SUPPORT_BELIEF_SUPERSEDED)
    if inp.has_successful_execution:
        reasons.append(ALREADY_EXECUTED)
    if not inp.recipient_allowlisted:
        reasons.append(RECIPIENT_NOT_ALLOWLISTED)
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one call to :meth:`ActionExecutor.execute` did, and to what.

    ``status`` is an ``action_executions`` status, ``EXECUTED`` (the intent's
    word for a ``SUCCEEDED`` attempt), or ``NOT_EXECUTED`` when the kill switch
    is down and no attempt was recorded at all. The last one is deliberately
    not ``ABORTED_STALE``: nothing about the intent was stale, the system was
    simply not permitted to send, and recording staleness would be a lie about
    the state of the case.
    """

    action_intent_id: uuid.UUID
    idempotency_key: str
    status: str
    provider: str
    case_revision: int
    action_execution_id: uuid.UUID | None = None
    attempt_no: int | None = None
    provider_correlation_id: str | None = None
    error_code: str | None = None
    blocking_reasons: tuple[str, ...] = ()
    replayed: bool = False
    case_revision_after: int | None = None
    #: ``action_executions.finished_at``, read back off the row the store
    #: wrote -- section 9.11's ``executed_at``. Never a timestamp taken by
    #: whoever renders the response: that would report when an adapter
    #: observed the outcome rather than when the attempt was recorded, and
    #: only the second is defensible as an audit record. ``None`` on the kill
    #: switch path, where no attempt exists to have finished.
    finished_at: datetime | None = None


@dataclass
class ActionExecutor:
    """Revalidate, record the attempt, send outside the transaction, record the outcome."""

    store: ActionStore
    sink: ActionSink
    policy: ActionPolicy
    clock: Callable[[], datetime]
    recorder: CanonicalRecorder = field(default_factory=NullRecorder)

    async def execute(
        self,
        scope: ActionScope,
        action_intent_id: uuid.UUID,
        *,
        idempotency_key: str,
        expected_draft_sha256: bytes | None = None,
        expected_case_revision: int | None = None,
    ) -> ExecutionOutcome:
        """Section 9.11, in order, with every refusal recorded and nothing sent.

        Raises:
            ActionRefusedError: the intent does not exist for this scope, or the
                supplied key is not the intent's key. Both are conditions under
                which there is nothing to record an attempt *against* -- a
                ledger row keyed to the wrong intent would be worse than none.
        """
        intent = await self.store.load_intent(scope, action_intent_id)
        if intent is None:
            raise intents.ActionRefusedError(
                intents.ACTION_INTENT_NOT_FOUND, action_intent_id=str(action_intent_id)
            )
        if idempotency_key != intent.idempotency_key:
            # Section 9.11: "The key **must** equal
            # ``action_intents.idempotency_key``." Tolerating a mismatch would
            # make the key advisory, and an advisory idempotency key is a
            # second send waiting for a retry.
            raise intents.ActionRefusedError(
                intents.IDEMPOTENCY_CONFLICT, action_intent_id=str(action_intent_id)
            )

        already = await self.store.successful_execution(scope, action_intent_id)
        if already is not None:
            # ``G9.4``: the second execute returns the first's outcome. No new
            # attempt, no provider call, the original correlation id.
            return ExecutionOutcome(
                action_intent_id=action_intent_id,
                idempotency_key=idempotency_key,
                status="EXECUTED",
                provider=already.provider,
                case_revision=already.revalidated_case_revision,
                action_execution_id=already.id,
                attempt_no=already.attempt_no,
                provider_correlation_id=already.provider_correlation_id,
                replayed=True,
                finished_at=already.finished_at,
            )

        snapshot = await self.store.grounding_snapshot(scope, intent.case_id)
        current_revision = (
            snapshot.case_revision if snapshot is not None else intent.basis_case_revision
        )

        if not self.policy.execution_enabled:
            # ``G9.6``: the kill switch records approvals and sends nothing.
            # Checked before the attempt is written, so a disabled deployment
            # leaves no ledger residue to explain later, and the intent stays
            # ``APPROVED`` so flipping the switch back needs no re-approval.
            return ExecutionOutcome(
                action_intent_id=action_intent_id,
                idempotency_key=idempotency_key,
                status="NOT_EXECUTED",
                provider=self.sink.provider,
                case_revision=current_revision,
                error_code=ACTION_EXECUTION_DISABLED,
                blocking_reasons=(ACTION_EXECUTION_DISABLED,),
            )

        payload_digest = drafts.draft_digest(intent.draft_payload)
        blocking = revalidate(
            RevalidationInput(
                status=intent.status,
                basis_case_revision=intent.basis_case_revision,
                current_case_revision=current_revision,
                expected_case_revision=expected_case_revision,
                approval_draft_sha256=intent.approval_draft_sha256,
                draft_payload_sha256=payload_digest,
                expected_draft_sha256=expected_draft_sha256,
                supporting_belief_versions=frozenset(intent.supporting_belief_versions),
                current_belief_version_ids=(
                    snapshot.current_belief_version_ids if snapshot else frozenset()
                ),
                # Always False, and not an oversight: the replay branch
                # above already returned if a SUCCEEDED row existed, so
                # this call cannot be reached with one. The field stays on
                # `RevalidationInput` because `revalidate` is also the pure
                # statement of section 9.11's gate, and the gate has that
                # clause. `uq_action_executions_single_success` is the
                # backstop for the race this branch cannot see.
                has_successful_execution=False,
                recipient_allowlisted=self.policy.recipient_allowlisted(intent.recipient),
                has_committed_basis=bool(snapshot and snapshot.has_committed_kernel_decision),
            )
        )

        attempt_no = await self.store.next_attempt_no(scope, action_intent_id)
        request = self._request(intent, idempotency_key)
        now = self.clock()

        if blocking:
            return await self._abort(
                scope,
                intent_id=action_intent_id,
                attempt_no=attempt_no,
                request_sha256=request.request_sha256,
                revision=current_revision,
                blocking=blocking,
                now=now,
                idempotency_key=idempotency_key,
            )

        # --- the transactional half ends here -----------------------------
        # The attempt is on the ledger and the intent is EXECUTING before any
        # socket is opened, so a crash mid-send leaves a row that says so.
        await self.store.set_status(scope, action_intent_id, status="EXECUTING")
        execution = await self.store.insert_execution(
            scope,
            NewActionExecution(
                id=uuid.uuid4(),
                action_intent_id=action_intent_id,
                attempt_no=attempt_no,
                provider=self.sink.provider,
                request_sha256=request.request_sha256,
                revalidated_case_revision=current_revision,
                status="STARTED",
                started_at=now,
            ),
        )

        # --- outside the transaction --------------------------------------
        try:
            receipt = await self.sink.send(request)
        except ProviderTransientError as exc:
            return await self._record_failure(
                scope,
                execution.id,
                intent_id=action_intent_id,
                idempotency_key=idempotency_key,
                status="FAILED_RETRYABLE",
                error_code=str(exc),
                revision=current_revision,
                attempt_no=attempt_no,
            )
        except ProviderFinalError as exc:
            return await self._record_failure(
                scope,
                execution.id,
                intent_id=action_intent_id,
                idempotency_key=idempotency_key,
                status="FAILED_FINAL",
                error_code=str(exc),
                revision=current_revision,
                attempt_no=attempt_no,
            )

        finished = await self.store.finish_execution(
            scope,
            execution.id,
            status="SUCCEEDED",
            finished_at=self.clock(),
            provider_correlation_id=receipt.provider_correlation_id,
        )
        approved_intent = await self.store.set_status(scope, action_intent_id, status="EXECUTED")
        after = await self.recorder.record_action_executed(scope, approved_intent, finished)
        return ExecutionOutcome(
            action_intent_id=action_intent_id,
            idempotency_key=idempotency_key,
            status="EXECUTED",
            provider=finished.provider,
            case_revision=current_revision,
            action_execution_id=finished.id,
            attempt_no=finished.attempt_no,
            provider_correlation_id=finished.provider_correlation_id,
            case_revision_after=after,
            finished_at=finished.finished_at,
        )

    # -- helpers ----------------------------------------------------------

    def _request(self, intent: Any, idempotency_key: str) -> SinkMessage:
        """The exact bytes that would leave, and their digest.

        ``request_sha256`` covers the delivery address rather than the intent's
        recipient, because the delivery address is what a provider record would
        show. Under ``DEMO_SINK`` those differ, and the ledger should describe
        what happened rather than what was intended.
        """
        subject = str(intent.draft_payload.get("subject", ""))
        body = str(intent.draft_payload.get("body", ""))
        recipient = self.policy.delivery_address(intent.recipient or "")
        material = drafts.canonical_json_bytes(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "idempotency_key": idempotency_key,
            }
        )
        return SinkMessage(
            action_intent_id=intent.id,
            idempotency_key=idempotency_key,
            recipient=recipient,
            subject=subject,
            body=body,
            request_sha256=hashlib.sha256(material).digest(),
        )

    async def _abort(
        self,
        scope: ActionScope,
        *,
        intent_id: uuid.UUID,
        attempt_no: int,
        request_sha256: bytes,
        revision: int,
        blocking: tuple[str, ...],
        now: datetime,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        """Record ``ABORTED_STALE`` and send nothing.

        The attempt is written even though nothing was sent, because "we
        refused to send this, at this revision, for this reason" is exactly the
        record a person asking *why didn't it go out* needs. ``G9.1`` reads it.
        """
        execution = await self.store.insert_execution(
            scope,
            NewActionExecution(
                id=uuid.uuid4(),
                action_intent_id=intent_id,
                attempt_no=attempt_no,
                provider=self.sink.provider,
                request_sha256=request_sha256,
                revalidated_case_revision=revision,
                status="ABORTED_STALE",
                started_at=now,
                finished_at=now,
                error_code=blocking[0],
            ),
        )
        await self.store.set_status(scope, intent_id, status="NEEDS_REVIEW")
        return ExecutionOutcome(
            action_intent_id=intent_id,
            idempotency_key=idempotency_key,
            status="ABORTED_STALE",
            provider=execution.provider,
            case_revision=revision,
            action_execution_id=execution.id,
            attempt_no=execution.attempt_no,
            error_code=blocking[0],
            blocking_reasons=blocking,
            finished_at=execution.finished_at,
        )

    async def _record_failure(
        self,
        scope: ActionScope,
        execution_id: uuid.UUID,
        *,
        intent_id: uuid.UUID,
        idempotency_key: str,
        status: str,
        error_code: str,
        revision: int,
        attempt_no: int,
    ) -> ExecutionOutcome:
        """A provider-declared failure is canonical state, not an HTTP problem.

        Section 9.11: ``502`` is not used. The attempt is recorded with its
        error code and the caller reads the status, because a recorded failed
        attempt is the thing a retry needs to know about.
        """
        finished = await self.store.finish_execution(
            scope, execution_id, status=status, finished_at=self.clock(), error_code=error_code
        )
        await self.store.set_status(scope, intent_id, status=status)
        return ExecutionOutcome(
            action_intent_id=intent_id,
            idempotency_key=idempotency_key,
            status=status,
            provider=finished.provider,
            case_revision=revision,
            action_execution_id=finished.id,
            attempt_no=attempt_no,
            error_code=error_code,
            finished_at=finished.finished_at,
        )


# --- the PV_SABOTAGE hook ----------------------------------------------------

#: The label ``tests/sabotage_matrix.yaml`` and ``G9.7`` address this module by.
SABOTAGE_MODULE: Final[str] = "actions.executor"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("revalidate_revision",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
