"""The action plane's persistence boundary: rows, a protocol, and a fake.

Authority
---------
- ``db/migrations/versions/0007_action_plane.py`` -- every column name and
  every constraint referenced here comes from that file, never from memory.
- ``packages/python/provenance_db/src/provenance_db/repositories/__init__.py``
  -- the enumeration of non-canonical writes the app owns. ``action_intents``
  and ``action_executions`` are both in it. Neither is a canonical table, so
  ``python -m tools.write_path_lint`` counts none of these statements and the
  Kernel's fourteen stay fourteen.
- ``docs/specs/10_DATABASE_DDL.md`` section 15: the Kernel can neither send
  anything nor mint an approval, which is why the action plane writes these two
  tables itself and never writes any other.

What this module deliberately does not do
------------------------------------------
It never writes a canonical table. Approving an action is *also* a canonical
state change -- section 8.26 steps 9 and 10 increment ``cases.revision``,
append a ``state_transitions`` row and write an outbox event -- and all three
belong to the Memory Kernel. That work is expressed here as
:class:`CanonicalRecorder`, a protocol the caller binds, so this module can
carry the sequencing without acquiring the credential. :class:`NullRecorder`
is the default and is honest about doing nothing.

Why an in-memory implementation ships in production code
---------------------------------------------------------
:class:`InMemoryActionStore` is not a test fixture that leaked. It is the
reference semantics for :class:`ActionStore`: the Postgres implementation in
``store_postgres.py`` must behave identically, and having both under one
protocol is what makes that checkable. It is also what lets the whole action
sequence -- grounding, approval, revalidation, idempotent execution -- be
decided in the hermetic lane, with no socket, which is where a rule that
governs an irreversible operation ought to be decidable.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from services.control_plane.app.actions.support_validation import GroundingSnapshot

__all__ = [
    "ActionExecutionRow",
    "ActionIntentRow",
    "ActionScope",
    "ActionStore",
    "CanonicalRecorder",
    "InMemoryActionStore",
    "NewActionExecution",
    "NewActionIntent",
    "NullRecorder",
]


@dataclass(frozen=True, slots=True)
class ActionScope:
    """``(tenant_id, user_id)``, and nothing else.

    Mirrors ``app/api/ports.py::OwnerScope`` in shape but is defined here so
    ``app/actions`` does not import ``app/api``: the action plane is called
    *by* the API and by workers, and an import in the other direction would
    make the executor unusable outside a request.

    :meth:`of` is duck-typed on purpose, so an ``OwnerScope``, a ``Principal``
    or a ``CapabilityBinding`` all convert without the caller unpacking two
    fields by hand -- which is the moment somebody unpacks the wrong two.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID

    @classmethod
    def of(cls, source: Any) -> ActionScope:
        return cls(tenant_id=source.tenant_id, user_id=source.user_id)


@dataclass(frozen=True, slots=True)
class ActionIntentRow:
    """One ``action_intents`` row. Field names are the column names."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    action_type: str
    recipient: str | None
    draft_payload: Mapping[str, Any]
    draft_sha256: bytes
    rationale: str
    supporting_belief_versions: tuple[uuid.UUID, ...]
    basis_case_revision: int
    status: str
    risk_tier: int
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    created_by_agent_run_id: uuid.UUID | None = None
    approved_by_user_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    approval_draft_sha256: bytes | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    #: Not a column. Section 8.24 attaches warnings to the intent so the
    #: approval screen can show what is grounded and what is the user speaking
    #: for themselves; ``0007`` has no column for them, so they travel on the
    #: row object and are persisted inside ``draft_payload`` by the store.
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionExecutionRow:
    """One ``action_executions`` row. Field names are the column names."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    action_intent_id: uuid.UUID
    attempt_no: int
    provider: str
    request_sha256: bytes
    revalidated_case_revision: int
    status: str
    started_at: datetime
    provider_correlation_id: str | None = None
    error_code: str | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewActionIntent:
    """The insert. Every field maps to a column ``0007`` declares NOT NULL."""

    id: uuid.UUID
    case_id: uuid.UUID
    action_type: str
    recipient: str | None
    draft_payload: Mapping[str, Any]
    draft_sha256: bytes
    rationale: str
    supporting_belief_versions: tuple[uuid.UUID, ...]
    basis_case_revision: int
    status: str
    risk_tier: int
    idempotency_key: str
    created_by_agent_run_id: uuid.UUID | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewActionExecution:
    """One attempt, at the moment it starts."""

    id: uuid.UUID
    action_intent_id: uuid.UUID
    attempt_no: int
    provider: str
    request_sha256: bytes
    revalidated_case_revision: int
    status: str
    started_at: datetime
    error_code: str | None = None
    finished_at: datetime | None = None
    provider_correlation_id: str | None = None


class CanonicalRecorder(Protocol):
    """The canonical half of an approval and an execution.

    Section 8.26 steps 9 and 10: incrementing ``cases.revision``, appending a
    ``state_transitions`` row and writing the ``action.approved.v1`` outbox
    event are canonical writes, and ``CANONICAL_DECISIONS.md`` -> *Canonical
    writer* gives all three to the Memory Kernel alone. This protocol is where
    the action plane hands them over.

    Both methods return the case revision **after** the write, because section
    8.26 advances ``basis_case_revision`` to it in the same transaction. That
    subtlety is the one ``15_API_SPEC.md`` section 22 flags as the easiest
    place to build a self-invalidating approval: increment the case without
    advancing the basis and every execution returns ``409 ACTION_STALE`` for
    what looks like a concurrency reason.
    """

    async def record_action_approved(self, scope: ActionScope, intent: ActionIntentRow) -> int: ...

    async def record_action_executed(
        self, scope: ActionScope, intent: ActionIntentRow, execution: ActionExecutionRow
    ) -> int: ...


class NullRecorder:
    """No canonical write, and the revision therefore does not move.

    The default, and deliberately not a silent one: with this bound, an
    approval records ``approval_draft_sha256`` and ``basis_case_revision``
    exactly as it must, and the case revision stays where it was. Every
    revalidation still holds, because ``basis == current`` still holds.

    What is missing is the timeline entry and the outbox event, and those are
    missing *visibly* -- a Kernel-backed recorder is a binding, not a rewrite.
    """

    async def record_action_approved(self, scope: ActionScope, intent: ActionIntentRow) -> int:
        del scope
        return intent.basis_case_revision

    async def record_action_executed(
        self, scope: ActionScope, intent: ActionIntentRow, execution: ActionExecutionRow
    ) -> int:
        del scope, intent
        return execution.revalidated_case_revision


class ActionStore(Protocol):
    """Every read and write the action plane needs, and no others.

    Every method takes an :class:`ActionScope` first, so a statement cannot be
    issued without ownership: the predicate lives in the signature rather than
    in the caller's discipline.
    """

    async def grounding_snapshot(
        self, scope: ActionScope, case_id: uuid.UUID
    ) -> GroundingSnapshot | None: ...

    async def load_intent(
        self, scope: ActionScope, intent_id: uuid.UUID
    ) -> ActionIntentRow | None: ...

    async def insert_intent(
        self, scope: ActionScope, new: NewActionIntent, *, now: datetime
    ) -> ActionIntentRow: ...

    async def replace_draft(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        draft_payload: Mapping[str, Any],
        draft_sha256: bytes,
        status: str,
        clear_approval: bool,
        now: datetime,
    ) -> ActionIntentRow: ...

    async def record_approval(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        draft_payload: Mapping[str, Any],
        draft_sha256: bytes,
        approved_by_user_id: uuid.UUID,
        approved_at: datetime,
    ) -> ActionIntentRow: ...

    async def record_rejection(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        reason_code: str,
        rejected_at: datetime,
    ) -> ActionIntentRow: ...

    async def set_status(
        self, scope: ActionScope, intent_id: uuid.UUID, *, status: str
    ) -> ActionIntentRow: ...

    async def successful_execution(
        self, scope: ActionScope, intent_id: uuid.UUID
    ) -> ActionExecutionRow | None: ...

    async def next_attempt_no(self, scope: ActionScope, intent_id: uuid.UUID) -> int: ...

    async def insert_execution(
        self, scope: ActionScope, new: NewActionExecution
    ) -> ActionExecutionRow: ...

    async def finish_execution(
        self,
        scope: ActionScope,
        execution_id: uuid.UUID,
        *,
        status: str,
        finished_at: datetime,
        provider_correlation_id: str | None = None,
        error_code: str | None = None,
    ) -> ActionExecutionRow: ...


@dataclass
class InMemoryActionStore:
    """Reference semantics for :class:`ActionStore`. No socket, no clock.

    The mutators with names like :meth:`tamper_draft_payload` and
    :meth:`withdraw_committed_basis` exist so a test can move exactly **one**
    of the bound facts and leave the others untouched. Proving the revision
    binding and the draft-hash binding separately is the whole difference
    between "a human approved this" and "a human approved something that looked
    like this", and it cannot be done by a fixture that can only rebuild the
    world wholesale.
    """

    _intents: dict[uuid.UUID, ActionIntentRow] = field(default_factory=dict)
    _executions: list[ActionExecutionRow] = field(default_factory=list)
    _snapshots: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], GroundingSnapshot] = field(
        default_factory=dict
    )
    #: How many statements have written an approval. ``T9.2``: freeze both
    #: values "in one statement with the approval, never in a follow-up
    #: update", which is only checkable if somebody counts.
    approval_statements: int = 0

    # -- inspection -------------------------------------------------------

    @property
    def intents(self) -> tuple[ActionIntentRow, ...]:
        return tuple(self._intents.values())

    @property
    def executions(self) -> tuple[ActionExecutionRow, ...]:
        return tuple(self._executions)

    def put_snapshot(self, scope: ActionScope, snapshot: GroundingSnapshot) -> None:
        self._snapshots[(scope.tenant_id, scope.user_id, snapshot.case_id)] = snapshot

    def advance_case_revision(self, scope: ActionScope, case_id: uuid.UUID, *, to: int) -> None:
        """An unrelated Kernel commit moved the case. ``G9.1``'s setup."""
        key = (scope.tenant_id, scope.user_id, case_id)
        self._snapshots[key] = replace(self._snapshots[key], case_revision=to)

    def supersede_belief_versions(
        self, scope: ActionScope, case_id: uuid.UUID, current: frozenset[uuid.UUID]
    ) -> None:
        key = (scope.tenant_id, scope.user_id, case_id)
        self._snapshots[key] = replace(self._snapshots[key], current_belief_version_ids=current)

    def withdraw_committed_basis(self, scope: ActionScope, case_id: uuid.UUID) -> None:
        key = (scope.tenant_id, scope.user_id, case_id)
        self._snapshots[key] = replace(self._snapshots[key], has_committed_kernel_decision=False)

    def tamper_draft_payload(
        self, scope: ActionScope, intent_id: uuid.UUID, payload: Mapping[str, Any]
    ) -> None:
        """Change the stored draft **without** touching ``approval_draft_sha256``.

        The state a draft edit that bypassed the freeze would leave behind, and
        the one the executor's hash re-check exists to catch. No supported code
        path produces it, which is why a test needs a way to construct it.
        """
        del scope
        self._intents[intent_id] = replace(self._intents[intent_id], draft_payload=dict(payload))

    def forget_executions(self) -> None:
        """Drop the ledger, leaving the sink's memory intact.

        Simulates the executor's pre-check losing a race with another instance,
        so the *sink's* idempotency can be asserted rather than the pre-check's.
        """
        self._executions.clear()

    # -- ActionStore ------------------------------------------------------

    async def grounding_snapshot(
        self, scope: ActionScope, case_id: uuid.UUID
    ) -> GroundingSnapshot | None:
        return self._snapshots.get((scope.tenant_id, scope.user_id, case_id))

    async def load_intent(self, scope: ActionScope, intent_id: uuid.UUID) -> ActionIntentRow | None:
        row = self._intents.get(intent_id)
        if row is None or row.tenant_id != scope.tenant_id or row.user_id != scope.user_id:
            return None
        return row

    async def insert_intent(
        self, scope: ActionScope, new: NewActionIntent, *, now: datetime
    ) -> ActionIntentRow:
        if any(row.idempotency_key == new.idempotency_key for row in self._intents.values()):
            raise KeyError("uq_action_intents_idempotency")
        row = ActionIntentRow(
            id=new.id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            case_id=new.case_id,
            action_type=new.action_type,
            recipient=new.recipient,
            draft_payload=dict(new.draft_payload),
            draft_sha256=new.draft_sha256,
            rationale=new.rationale,
            supporting_belief_versions=new.supporting_belief_versions,
            basis_case_revision=new.basis_case_revision,
            status=new.status,
            risk_tier=new.risk_tier,
            idempotency_key=new.idempotency_key,
            created_at=now,
            updated_at=now,
            created_by_agent_run_id=new.created_by_agent_run_id,
            warnings=new.warnings,
        )
        self._intents[row.id] = row
        return row

    async def replace_draft(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        draft_payload: Mapping[str, Any],
        draft_sha256: bytes,
        status: str,
        clear_approval: bool,
        now: datetime,
    ) -> ActionIntentRow:
        del scope
        approval: dict[str, Any] = {}
        if clear_approval:
            approval = {
                "approved_by_user_id": None,
                "approved_at": None,
                "approval_draft_sha256": None,
            }
        row = replace(
            self._intents[intent_id],
            draft_payload=dict(draft_payload),
            draft_sha256=draft_sha256,
            status=status,
            updated_at=now,
            **approval,
        )
        self._intents[intent_id] = row
        return row

    async def record_approval(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        draft_payload: Mapping[str, Any],
        draft_sha256: bytes,
        approved_by_user_id: uuid.UUID,
        approved_at: datetime,
    ) -> ActionIntentRow:
        del scope
        self.approval_statements += 1
        row = replace(
            self._intents[intent_id],
            draft_payload=dict(draft_payload),
            draft_sha256=draft_sha256,
            approval_draft_sha256=draft_sha256,
            approved_by_user_id=approved_by_user_id,
            approved_at=approved_at,
            status="APPROVED",
            updated_at=approved_at,
        )
        self._intents[intent_id] = row
        return row

    async def record_rejection(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        reason_code: str,
        rejected_at: datetime,
    ) -> ActionIntentRow:
        del scope
        row = replace(
            self._intents[intent_id],
            status="REJECTED",
            rejected_at=rejected_at,
            rejection_reason=reason_code,
            updated_at=rejected_at,
        )
        self._intents[intent_id] = row
        return row

    async def set_status(
        self, scope: ActionScope, intent_id: uuid.UUID, *, status: str
    ) -> ActionIntentRow:
        del scope
        row = replace(self._intents[intent_id], status=status)
        self._intents[intent_id] = row
        return row

    async def successful_execution(
        self, scope: ActionScope, intent_id: uuid.UUID
    ) -> ActionExecutionRow | None:
        del scope
        for row in self._executions:
            if row.action_intent_id == intent_id and row.status == "SUCCEEDED":
                return row
        return None

    async def next_attempt_no(self, scope: ActionScope, intent_id: uuid.UUID) -> int:
        del scope
        attempts = [row.attempt_no for row in self._executions if row.action_intent_id == intent_id]
        return max(attempts, default=0) + 1

    async def insert_execution(
        self, scope: ActionScope, new: NewActionExecution
    ) -> ActionExecutionRow:
        if new.status == "SUCCEEDED" and await self.successful_execution(
            scope, new.action_intent_id
        ):
            raise KeyError("uq_action_executions_single_success")
        row = ActionExecutionRow(
            id=new.id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            action_intent_id=new.action_intent_id,
            attempt_no=new.attempt_no,
            provider=new.provider,
            request_sha256=new.request_sha256,
            revalidated_case_revision=new.revalidated_case_revision,
            status=new.status,
            started_at=new.started_at,
            provider_correlation_id=new.provider_correlation_id,
            error_code=new.error_code,
            finished_at=new.finished_at,
        )
        self._executions.append(row)
        return row

    async def finish_execution(
        self,
        scope: ActionScope,
        execution_id: uuid.UUID,
        *,
        status: str,
        finished_at: datetime,
        provider_correlation_id: str | None = None,
        error_code: str | None = None,
    ) -> ActionExecutionRow:
        for index, row in enumerate(self._executions):
            if row.id != execution_id:
                continue
            if status == "SUCCEEDED":
                clash = await self.successful_execution(scope, row.action_intent_id)
                if clash is not None and clash.id != execution_id:
                    raise KeyError("uq_action_executions_single_success")
            updated = replace(
                row,
                status=status,
                finished_at=finished_at,
                provider_correlation_id=provider_correlation_id,
                error_code=error_code,
            )
            self._executions[index] = updated
            return updated
        raise KeyError(execution_id)


def as_uuid_tuple(values: Sequence[Any]) -> tuple[uuid.UUID, ...]:
    """``supporting_belief_versions`` comes back from JSONB as strings."""
    return tuple(
        value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)) for value in values
    )
