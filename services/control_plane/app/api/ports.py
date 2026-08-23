"""The data-access ports the API depends on.

Authority: ``specs/15_API_SPEC.md`` sections 8 and 9;
``implementation/00_IMPLEMENTATION_MAP.md`` on write-path ownership.

Why the routes depend on protocols rather than on SQL
------------------------------------------------------
Two rules make this the only honest shape at Phase 8.

1. **The Memory Kernel is the sole canonical writer.** Routes accept typed
   proposals and call the Kernel; they never write canonical tables. A route
   holding a connection and a ``INSERT`` is exactly what
   ``python -m tools.write_path_lint`` exists to forbid.
2. **Phase 5 owns the read models.** ``provenance_db.repositories`` is
   shape-only today (every body raises ``NotImplementedError`` with "Phase 5
   implements it"), and ``app/state_proof`` and ``app/retrieval`` are being
   built in parallel. Re-implementing those reads here would produce a second
   definition of every scoping predicate, which is how a cross-user leak gets
   in.

So the API states the surface it needs, and binding it to SQL is a wiring
change with no route logic in it.

The scoping mechanism
---------------------
Every method's first parameter is an :class:`OwnerScope` (public surface) or a
``CapabilityBinding`` (internal surface). Neither can be constructed from
request data: ``OwnerScope`` is built only in
``app/api/deps.py::owner_scope`` from a verified :class:`Principal`, and
``CapabilityBinding`` is returned only by ``InternalPrincipal.require_binding``,
which reads a server-side capability row. A query therefore cannot be issued
without ownership, because ownership is a required positional argument.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from provenance_contracts.identity import CapabilityBinding, Principal

__all__ = [
    "InternalPort",
    "OwnerScope",
    "ReadPort",
    "UserDirectory",
    "UserRecord",
    "WritePort",
]

Row = dict[str, Any]
Rows = tuple[list[Row], bool]


@dataclass(frozen=True, slots=True)
class OwnerScope:
    """``(tenant_id, user_id)``, and nothing else.

    Frozen and slotted so it cannot be mutated after construction, and
    deliberately *not* constructible from a request model -- see
    :func:`services.control_plane.app.api.deps.owner_scope`.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID

    @classmethod
    def of(cls, principal: Principal) -> OwnerScope:
        return cls(tenant_id=principal.tenant_id, user_id=principal.user_id)

    @classmethod
    def of_binding(cls, binding: CapabilityBinding) -> OwnerScope:
        return cls(tenant_id=binding.tenant_id, user_id=binding.user_id)


@dataclass(frozen=True, slots=True)
class UserRecord:
    """One ``users`` row, resolved from ``cognito_sub``.

    Section 2.5: a verified token whose ``sub`` has no row returns
    ``403 USER_NOT_PROVISIONED``. Provenance never auto-creates users on an
    API call -- that would let any pool member mint a tenant by hitting
    ``GET /v1/me``.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    cognito_sub: str
    email: str | None
    display_name: str | None
    timezone: str
    home_region: str | None
    created_at: datetime
    judge_mode_allowlisted: bool = False


class UserDirectory(Protocol):
    async def by_cognito_sub(self, sub: str) -> UserRecord | None: ...


class ReadPort(Protocol):
    """The public read surface. Every method is user-scoped by signature."""

    async def me(self, scope: OwnerScope) -> Row | None: ...

    async def dashboard(
        self,
        scope: OwnerScope,
        *,
        context_id: uuid.UUID | None = None,
        attention_only: bool = False,
        statuses: tuple[str, ...] = (),
    ) -> Row: ...

    async def list_contexts(
        self, scope: OwnerScope, *, limit: int, after: tuple[list[str], uuid.UUID] | None = None
    ) -> Rows: ...

    async def list_relationships(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def get_relationship(
        self, scope: OwnerScope, relationship_id: uuid.UUID
    ) -> Row | None: ...

    async def list_cases(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def get_case(self, scope: OwnerScope, case_id: uuid.UUID) -> Row | None: ...

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None: ...

    async def list_timeline(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Rows | None: ...

    async def state_proof(
        self, scope: OwnerScope, case_id: uuid.UUID, **options: Any
    ) -> Row | None: ...

    async def list_conflicts(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Rows | None: ...

    async def get_belief(
        self, scope: OwnerScope, belief_id: uuid.UUID, **options: Any
    ) -> Row | None: ...

    async def list_commitments(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def list_triggers(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def list_artifacts(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def get_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, **options: Any
    ) -> Row | None: ...

    async def ingest_alias(self, scope: OwnerScope) -> Row | None: ...

    async def list_action_intents(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows: ...

    async def get_action_intent(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, **options: Any
    ) -> Row | None: ...

    async def get_trace(
        self, scope: OwnerScope, trace_id: uuid.UUID, *, judge: bool = False
    ) -> Row | None: ...

    async def memory_trace(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Row | None: ...

    async def agent_view_names(self) -> list[str]:
        """Read from ``information_schema.views``.

        T8.7: so `G11.6`'s diff compares the database against itself through
        the API rather than against a hard-coded list.
        """


class WritePort(Protocol):
    """The public mutating surface.

    Nothing here writes canonical tables directly. Each method either submits
    a typed proposal to the Memory Kernel (corrections, approvals) or touches
    a non-canonical resource (an S3 pre-sign, an alias rotation). A ``None``
    return means "no such row **for this scope**" and the route maps it to a
    typed 404 -- never a 403 (section 1.7).
    """

    async def create_correction(
        self, scope: OwnerScope, case_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def upload_intent(self, scope: OwnerScope, payload: Any) -> Row: ...

    async def complete_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def rotate_ingest_alias(self, scope: OwnerScope) -> Row: ...

    async def update_draft(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def approve(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def reject(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def start_counterfactual(self, scope: OwnerScope, payload: Any) -> Row | None: ...

    async def get_counterfactual(
        self, scope: OwnerScope, counterfactual_id: uuid.UUID
    ) -> Row | None: ...

    async def wake_trigger(
        self, scope: OwnerScope, trigger_id: uuid.UUID, payload: Any
    ) -> Row | None: ...

    async def run_probe(self, scope: OwnerScope, payload: Any) -> Row: ...


class InternalPort(Protocol):
    """The thirteen ``/internal/v1`` operations.

    Every method takes the ``CapabilityBinding`` rather than an
    ``OwnerScope``, because the binding also carries ``allowed_case_ids`` and
    the bound artifact -- the two things an internal handler must not take
    from the request.

    ``submit_proposal`` additionally takes ``idempotency_key``, and it is the
    only method here that takes anything off a header. Section 9.7 makes
    ``Idempotency-Key`` required and
    ``provenance_contracts.proposal.MemoryProposal.idempotency_key`` is a
    required field, so the key has to reach the proposal. Passing it rather
    than minting one is the same rule ``execute_action`` learned the hard way:
    a minted key equals the caller's only when the caller supplied none.
    """

    async def ingest_artifact(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def agent_run(self, binding: CapabilityBinding) -> Row | None: ...

    async def artifact_content(self, binding: CapabilityBinding, **options: Any) -> Row | None: ...

    async def register_evidence(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def retrieve(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def run_state_proof(
        self, binding: CapabilityBinding, case_id: uuid.UUID
    ) -> Row | None: ...

    async def submit_proposal(
        self, binding: CapabilityBinding, payload: Any, *, idempotency_key: str
    ) -> Row: ...

    async def create_action_intent(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def complete_agent_run(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def evaluate_trigger(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def execute_action(self, binding: CapabilityBinding, payload: Any) -> Row: ...

    async def sweep_outbox(self, payload: Any) -> Row: ...

    async def deliver_event(self, payload: Any) -> Row: ...
