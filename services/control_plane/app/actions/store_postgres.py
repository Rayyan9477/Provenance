"""``ActionStore`` over ``action_intents`` and ``action_executions``.

Authority
---------
- ``db/migrations/versions/0007_action_plane.py`` -- every column name below is
  read from that file. Nothing here is remembered.
- ``docs/specs/10_DATABASE_DDL.md`` sections 10 and 13 (the executor query).
- ``packages/python/provenance_db/src/provenance_db/repositories/__init__.py``
  -- the enumeration of app-permitted, non-canonical writes.
  ``action_intents`` and ``action_executions`` are both named in it.

Not a canonical writer
-----------------------
Neither table is in ``tools/write_path_lint.CANONICAL_TABLES``, so nothing in
this module counts towards the Kernel's fourteen. That is not an accident of
the linter's configuration: ``10_DATABASE_DDL.md`` section 15 says the Kernel
can neither send anything nor mint an approval, which is precisely why the
action plane owns these two tables and no others.

Staleness is zero rows, not a Python branch
--------------------------------------------
``T9.4``, first sub-task: "Revalidate ``cases.revision == basis_case_revision``
**and** the draft hash inside the executor query itself, so staleness is
expressed as zero rows rather than as a Python branch that can be skipped."
:data:`EXECUTOR_CLAIM_SQL` is that query. It is the database's answer to *may
this be sent*, and ``G9.1`` reads it directly: approve at 13, move the case to
14, run it, get nothing.

The in-process gate in ``executor.py`` is the second of two mechanisms rather
than a replacement for this one. The structural one keeps holding when somebody
adds an indirection an AST scan cannot follow; the in-process one keeps holding
when somebody writes a new query.

Reads are user-scoped by signature
-----------------------------------
Every statement binds ``tenant_id`` and ``user_id`` from an
:class:`~services.control_plane.app.actions.store.ActionScope`, which is a
required positional argument on every method. A query therefore cannot be
issued without ownership.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from psycopg.types.json import Json

from services.control_plane.app.actions.store import (
    ActionExecutionRow,
    ActionIntentRow,
    ActionScope,
    NewActionExecution,
    NewActionIntent,
    as_uuid_tuple,
)
from services.control_plane.app.actions.support_validation import (
    COMMITTED_DECISIONS,
    GroundingSnapshot,
)

__all__ = [
    "EXECUTOR_CLAIM_SQL",
    "FINISH_EXECUTION_SQL",
    "GROUNDING_SNAPSHOT_SQL",
    "INSERT_EXECUTION_SQL",
    "INSERT_INTENT_SQL",
    "LOAD_INTENT_SQL",
    "NEXT_ATTEMPT_SQL",
    "RECORD_APPROVAL_SQL",
    "RECORD_REJECTION_SQL",
    "REPLACE_DRAFT_SQL",
    "SET_STATUS_SQL",
    "SUCCESSFUL_EXECUTION_SQL",
    "PostgresActionStore",
]

_INTENT_COLUMNS: Final[str] = """
    id, tenant_id, user_id, case_id, action_type, recipient, draft_payload,
    draft_sha256, rationale, supporting_belief_versions, basis_case_revision,
    status, risk_tier, created_by_agent_run_id, approved_by_user_id,
    approved_at, approval_draft_sha256, rejected_at, rejection_reason,
    idempotency_key, created_at, updated_at
"""

#: The same projection, aliased for the executor claim, which joins ``cases``.
#: Written out rather than produced by string surgery on the list above: a
#: ``.replace()`` over SQL is a rename waiting to hit the wrong token.
_INTENT_COLUMNS_AI: Final[str] = """
    ai.id, ai.tenant_id, ai.user_id, ai.case_id, ai.action_type, ai.recipient,
    ai.draft_payload, ai.draft_sha256, ai.rationale,
    ai.supporting_belief_versions, ai.basis_case_revision, ai.status,
    ai.risk_tier, ai.created_by_agent_run_id, ai.approved_by_user_id,
    ai.approved_at, ai.approval_draft_sha256, ai.rejected_at,
    ai.rejection_reason, ai.idempotency_key, ai.created_at, ai.updated_at
"""

_EXECUTION_COLUMNS: Final[str] = """
    id, tenant_id, user_id, action_intent_id, attempt_no, provider,
    provider_correlation_id, request_sha256, revalidated_case_revision,
    status, error_code, started_at, finished_at
"""

LOAD_INTENT_SQL: Final[str] = f"""
    SELECT {_INTENT_COLUMNS}
    FROM action_intents
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(intent_id)s
"""

#: The snapshot the whole phase is decided against, in one statement.
#:
#: ``has_committed_kernel_decision`` is an EXISTS over ``kernel_decisions``
#: restricted to the decisions that leave committed state behind, with
#: ``committed_at IS NOT NULL``. Both halves are needed: a decision row exists
#: for every outcome including the rejections, and ``committed_at`` is what
#: separates a settled transaction from one that was opened and abandoned.
#:
#: ``current_belief_version_ids`` is the set an approval's
#: ``supporting_belief_versions`` must still be a subset of. It is collected in
#: the same statement as the revision so the two describe one instant; two
#: statements would describe two, and the window between them is the one an
#: irreversible operation must not have.
GROUNDING_SNAPSHOT_SQL: Final[str] = """
    SELECT c.id AS case_id,
           c.revision AS case_revision,
           EXISTS (
               SELECT 1 FROM kernel_decisions kd
               WHERE kd.tenant_id = c.tenant_id
                 AND kd.user_id = c.user_id
                 AND kd.case_id = c.id
                 AND kd.committed_at IS NOT NULL
                 AND kd.decision = ANY(%(committed_decisions)s::STRING[])
           ) AS has_committed_kernel_decision,
           COALESCE(
               (SELECT array_agg(bv.id)
                FROM belief_versions bv
                JOIN beliefs b
                  ON b.tenant_id = bv.tenant_id AND b.user_id = bv.user_id
                 AND b.id = bv.belief_id
                WHERE bv.tenant_id = c.tenant_id
                  AND bv.user_id = c.user_id
                  AND bv.superseded_at IS NULL),
               ARRAY[]::UUID[]
           ) AS current_belief_version_ids
    FROM cases c
    WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s AND c.id = %(case_id)s
"""

INSERT_INTENT_SQL: Final[str] = f"""
    INSERT INTO action_intents (
        id, tenant_id, user_id, case_id, action_type, recipient, draft_payload,
        draft_sha256, rationale, supporting_belief_versions, basis_case_revision,
        status, risk_tier, created_by_agent_run_id, idempotency_key,
        created_at, updated_at
    ) VALUES (
        %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(action_type)s,
        %(recipient)s, %(draft_payload)s, %(draft_sha256)s, %(rationale)s,
        %(supporting_belief_versions)s, %(basis_case_revision)s, %(status)s,
        %(risk_tier)s, %(created_by_agent_run_id)s, %(idempotency_key)s,
        %(now)s, %(now)s
    )
    RETURNING {_INTENT_COLUMNS}
"""

#: Section 8.25. ``status`` is forced back to ``NEEDS_REVIEW`` and the three
#: approval columns are cleared together -- all three or none, because
#: ``ck_action_intents_approval_complete`` refuses two out of three, which is
#: exactly the shape a half-finished invalidation would leave behind.
REPLACE_DRAFT_SQL: Final[str] = f"""
    UPDATE action_intents
    SET draft_payload = %(draft_payload)s,
        draft_sha256 = %(draft_sha256)s,
        status = %(status)s,
        approved_by_user_id = CASE WHEN %(clear_approval)s THEN NULL ELSE approved_by_user_id END,
        approved_at = CASE WHEN %(clear_approval)s THEN NULL ELSE approved_at END,
        approval_draft_sha256 =
            CASE WHEN %(clear_approval)s THEN NULL ELSE approval_draft_sha256 END,
        updated_at = %(now)s
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(intent_id)s
    RETURNING {_INTENT_COLUMNS}
"""

#: ``T9.2``: freeze both values in ONE statement with the approval. The status,
#: the digest, the approver and the timestamp are set together, so there is no
#: instant at which the row is ``APPROVED`` with a null
#: ``approval_draft_sha256`` -- the state
#: ``ck_action_intents_execution_needs_approval`` exists to forbid.
#:
#: ``basis_case_revision`` is written from the parameter rather than left
#: alone, because section 8.26 advances it to the post-approval revision in the
#: same transaction. ``15_API_SPEC.md`` section 22 flags omitting this as the
#: single easiest way to build a self-invalidating approval.
RECORD_APPROVAL_SQL: Final[str] = f"""
    UPDATE action_intents
    SET draft_payload = %(draft_payload)s,
        draft_sha256 = %(draft_sha256)s,
        approval_draft_sha256 = %(draft_sha256)s,
        approved_by_user_id = %(approved_by_user_id)s,
        approved_at = %(approved_at)s,
        basis_case_revision = %(basis_case_revision)s,
        status = 'APPROVED',
        updated_at = %(approved_at)s
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(intent_id)s
      AND status IN ('PROPOSED', 'NEEDS_REVIEW')
    RETURNING {_INTENT_COLUMNS}
"""

RECORD_REJECTION_SQL: Final[str] = f"""
    UPDATE action_intents
    SET status = 'REJECTED',
        rejected_at = %(rejected_at)s,
        rejection_reason = %(reason_code)s,
        updated_at = %(rejected_at)s
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(intent_id)s
    RETURNING {_INTENT_COLUMNS}
"""

SET_STATUS_SQL: Final[str] = f"""
    UPDATE action_intents
    SET status = %(status)s, updated_at = %(now)s
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(intent_id)s
    RETURNING {_INTENT_COLUMNS}
"""

#: "Has this intent already succeeded?", asked before any provider call. The
#: partial UNIQUE ``uq_action_executions_single_success`` is the hard stop
#: behind it; this read is the cheap one that keeps the hard stop from having
#: to fire.
SUCCESSFUL_EXECUTION_SQL: Final[str] = f"""
    SELECT {_EXECUTION_COLUMNS}
    FROM action_executions
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s
      AND action_intent_id = %(intent_id)s AND status = 'SUCCEEDED'
    LIMIT 1
"""

NEXT_ATTEMPT_SQL: Final[str] = """
    SELECT COALESCE(max(attempt_no), 0) + 1 AS next_attempt_no
    FROM action_executions
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s
      AND action_intent_id = %(intent_id)s
"""

INSERT_EXECUTION_SQL: Final[str] = f"""
    INSERT INTO action_executions (
        id, tenant_id, user_id, action_intent_id, attempt_no, provider,
        provider_correlation_id, request_sha256, revalidated_case_revision,
        status, error_code, started_at, finished_at
    ) VALUES (
        %(id)s, %(tenant_id)s, %(user_id)s, %(action_intent_id)s, %(attempt_no)s,
        %(provider)s, %(provider_correlation_id)s, %(request_sha256)s,
        %(revalidated_case_revision)s, %(status)s, %(error_code)s,
        %(started_at)s, %(finished_at)s
    )
    RETURNING {_EXECUTION_COLUMNS}
"""

FINISH_EXECUTION_SQL: Final[str] = f"""
    UPDATE action_executions
    SET status = %(status)s,
        finished_at = %(finished_at)s,
        provider_correlation_id = %(provider_correlation_id)s,
        error_code = %(error_code)s
    WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(execution_id)s
    RETURNING {_EXECUTION_COLUMNS}
"""

#: DDL section 13's executor query, and ``G9.1``'s "zero rows".
#:
#: Every clause of section 9.11's revalidation gate that a row-local predicate
#: can express is here: the status, the two revisions, the approval digest
#: against the recomputed payload digest, and the absence of a prior success.
#: ``digest(...)`` is CockroachDB's builtin over the canonical JSONB rendering;
#: the application recomputes the same digest over the same canonicalisation in
#: ``drafts.draft_digest``, and the db suite asserts the two agree rather than
#: assuming it.
#:
#: The recipient allowlist is deliberately NOT in this statement: it is
#: configuration, not state, and baking a deployment's environment into a
#: query would make the query wrong the moment the environment changed.
EXECUTOR_CLAIM_SQL: Final[str] = f"""
    SELECT {_INTENT_COLUMNS_AI}, c.revision AS current_case_revision
    FROM action_intents ai
    JOIN cases c
      ON c.tenant_id = ai.tenant_id AND c.user_id = ai.user_id AND c.id = ai.case_id
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND ai.id = %(intent_id)s
      AND ai.status = 'APPROVED'
      AND ai.approval_draft_sha256 IS NOT NULL
      AND ai.approval_draft_sha256 = %(expected_draft_sha256)s
      AND ai.basis_case_revision = c.revision
      AND c.revision = %(expected_case_revision)s
      AND NOT EXISTS (
          SELECT 1 FROM action_executions ae
          WHERE ae.action_intent_id = ai.id AND ae.status = 'SUCCEEDED'
      )
"""


def _scope(scope: ActionScope) -> dict[str, Any]:
    return {"tenant_id": scope.tenant_id, "user_id": scope.user_id}


def _intent_row(row: Mapping[str, Any]) -> ActionIntentRow:
    """One ``action_intents`` mapping as a typed row.

    ``warnings`` is carried inside ``draft_payload`` because ``0007`` has no
    column for it -- reported rather than papered over: section 8.24 renders
    warnings on the intent and the schema does not store them. Adding a column
    would mean a migration, and migrations past ``0008`` are not this task's.
    """
    payload = dict(row["draft_payload"] or {})
    warnings = tuple(str(code) for code in payload.pop("__warnings__", ()))
    return ActionIntentRow(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        case_id=row["case_id"],
        action_type=row["action_type"],
        recipient=row["recipient"],
        draft_payload=payload,
        draft_sha256=bytes(row["draft_sha256"]),
        rationale=row["rationale"],
        supporting_belief_versions=as_uuid_tuple(row["supporting_belief_versions"] or ()),
        basis_case_revision=int(row["basis_case_revision"]),
        status=row["status"],
        risk_tier=int(row["risk_tier"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by_agent_run_id=row["created_by_agent_run_id"],
        approved_by_user_id=row["approved_by_user_id"],
        approved_at=row["approved_at"],
        approval_draft_sha256=(
            bytes(row["approval_draft_sha256"])
            if row["approval_draft_sha256"] is not None
            else None
        ),
        rejected_at=row["rejected_at"],
        rejection_reason=row["rejection_reason"],
        warnings=warnings,
    )


def _execution_row(row: Mapping[str, Any]) -> ActionExecutionRow:
    return ActionExecutionRow(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        action_intent_id=row["action_intent_id"],
        attempt_no=int(row["attempt_no"]),
        provider=row["provider"],
        request_sha256=bytes(row["request_sha256"]),
        revalidated_case_revision=int(row["revalidated_case_revision"]),
        status=row["status"],
        started_at=row["started_at"],
        provider_correlation_id=row["provider_correlation_id"],
        error_code=row["error_code"],
        finished_at=row["finished_at"],
    )


def _stored_payload(payload: Mapping[str, Any], warnings: tuple[str, ...]) -> Json:
    body = dict(payload)
    if warnings:
        body["__warnings__"] = list(warnings)
    return Json(body)


@dataclass
class PostgresActionStore:
    """:class:`ActionStore` over a live ``psycopg.AsyncConnection``.

    The connection is supplied rather than owned. The caller decides the
    transaction boundary, which matters for exactly one reason: section 9.11
    requires the attempt row to be **committed** before the provider is called,
    and a store that opened its own transaction per statement could not be
    asked to hold one open across the two halves -- nor to close it before the
    send.
    """

    conn: Any

    async def _one(self, sql: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
        async with self.conn.cursor() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
            if row is None:
                return None
            columns = [description[0] for description in cur.description]
            return dict(zip(columns, row, strict=True))

    async def grounding_snapshot(
        self, scope: ActionScope, case_id: uuid.UUID
    ) -> GroundingSnapshot | None:
        row = await self._one(
            GROUNDING_SNAPSHOT_SQL,
            {
                **_scope(scope),
                "case_id": case_id,
                "committed_decisions": sorted(COMMITTED_DECISIONS),
            },
        )
        if row is None:
            return None
        return GroundingSnapshot(
            case_id=row["case_id"],
            case_revision=int(row["case_revision"]),
            # NOT frozenset(). The citation set is defined by
            # `StateProof.support_ids()` and spans beliefs, their grounding
            # edges, the evidence behind those edges, conflicts, commitments
            # and fulfillments -- six tables this statement does not read.
            # Rebuilding it here would create a second definition of what
            # "supported" means, and the drift would be invisible until a
            # draft cited something the State Proof happily rendered.
            #
            # So the store says it does not know. `validate_draft_claims`
            # answers SUPPORT_SET_UNAVAILABLE rather than blaming the draft,
            # and a caller that needs grounding validation over this store
            # must build the snapshot with
            # `GroundingSnapshot.from_state_proof(...)` from a proof the
            # State Proof builder assembled. Returning an empty set would have
            # declared every claim in every draft unsupported while looking
            # exactly like a correctly refused draft.
            support_ids=None,
            current_belief_version_ids=frozenset(row["current_belief_version_ids"] or ()),
            has_committed_kernel_decision=bool(row["has_committed_kernel_decision"]),
        )

    async def load_intent(self, scope: ActionScope, intent_id: uuid.UUID) -> ActionIntentRow | None:
        row = await self._one(LOAD_INTENT_SQL, {**_scope(scope), "intent_id": intent_id})
        return None if row is None else _intent_row(row)

    async def insert_intent(
        self, scope: ActionScope, new: NewActionIntent, *, now: datetime
    ) -> ActionIntentRow:
        row = await self._one(
            INSERT_INTENT_SQL,
            {
                **_scope(scope),
                "id": new.id,
                "case_id": new.case_id,
                "action_type": new.action_type,
                "recipient": new.recipient,
                "draft_payload": _stored_payload(new.draft_payload, new.warnings),
                "draft_sha256": new.draft_sha256,
                "rationale": new.rationale,
                "supporting_belief_versions": Json(
                    [str(value) for value in new.supporting_belief_versions]
                ),
                "basis_case_revision": new.basis_case_revision,
                "status": new.status,
                "risk_tier": new.risk_tier,
                "created_by_agent_run_id": new.created_by_agent_run_id,
                "idempotency_key": new.idempotency_key,
                "now": now,
            },
        )
        if row is None:  # pragma: no cover - RETURNING on an INSERT always yields a row
            raise KeyError(new.id)
        return _intent_row(row)

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
        current = await self.load_intent(scope, intent_id)
        warnings = current.warnings if current else ()
        row = await self._one(
            REPLACE_DRAFT_SQL,
            {
                **_scope(scope),
                "intent_id": intent_id,
                "draft_payload": _stored_payload(draft_payload, warnings),
                "draft_sha256": draft_sha256,
                "status": status,
                "clear_approval": clear_approval,
                "now": now,
            },
        )
        if row is None:
            raise KeyError(intent_id)
        return _intent_row(row)

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
        current = await self.load_intent(scope, intent_id)
        if current is None:
            raise KeyError(intent_id)
        row = await self._one(
            RECORD_APPROVAL_SQL,
            {
                **_scope(scope),
                "intent_id": intent_id,
                "draft_payload": _stored_payload(draft_payload, current.warnings),
                "draft_sha256": draft_sha256,
                "approved_by_user_id": approved_by_user_id,
                "approved_at": approved_at,
                "basis_case_revision": current.basis_case_revision,
            },
        )
        if row is None:
            # The optimistic predicate matched nothing: the status moved out of
            # the approvable set under this transaction. Never a lost update.
            raise KeyError(intent_id)
        return _intent_row(row)

    async def record_rejection(
        self,
        scope: ActionScope,
        intent_id: uuid.UUID,
        *,
        reason_code: str,
        rejected_at: datetime,
    ) -> ActionIntentRow:
        row = await self._one(
            RECORD_REJECTION_SQL,
            {
                **_scope(scope),
                "intent_id": intent_id,
                "reason_code": reason_code,
                "rejected_at": rejected_at,
            },
        )
        if row is None:
            raise KeyError(intent_id)
        return _intent_row(row)

    async def set_status(
        self, scope: ActionScope, intent_id: uuid.UUID, *, status: str
    ) -> ActionIntentRow:
        row = await self._one(
            SET_STATUS_SQL,
            {
                **_scope(scope),
                "intent_id": intent_id,
                "status": status,
                "now": datetime.now(tz=UTC),
            },
        )
        if row is None:
            raise KeyError(intent_id)
        return _intent_row(row)

    async def successful_execution(
        self, scope: ActionScope, intent_id: uuid.UUID
    ) -> ActionExecutionRow | None:
        row = await self._one(SUCCESSFUL_EXECUTION_SQL, {**_scope(scope), "intent_id": intent_id})
        return None if row is None else _execution_row(row)

    async def next_attempt_no(self, scope: ActionScope, intent_id: uuid.UUID) -> int:
        row = await self._one(NEXT_ATTEMPT_SQL, {**_scope(scope), "intent_id": intent_id})
        return int(row["next_attempt_no"]) if row else 1

    async def insert_execution(
        self, scope: ActionScope, new: NewActionExecution
    ) -> ActionExecutionRow:
        row = await self._one(
            INSERT_EXECUTION_SQL,
            {
                **_scope(scope),
                "id": new.id,
                "action_intent_id": new.action_intent_id,
                "attempt_no": new.attempt_no,
                "provider": new.provider,
                "provider_correlation_id": new.provider_correlation_id,
                "request_sha256": new.request_sha256,
                "revalidated_case_revision": new.revalidated_case_revision,
                "status": new.status,
                "error_code": new.error_code,
                "started_at": new.started_at,
                "finished_at": new.finished_at,
            },
        )
        if row is None:  # pragma: no cover - RETURNING on an INSERT always yields a row
            raise KeyError(new.id)
        return _execution_row(row)

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
        row = await self._one(
            FINISH_EXECUTION_SQL,
            {
                **_scope(scope),
                "execution_id": execution_id,
                "status": status,
                "finished_at": finished_at,
                "provider_correlation_id": provider_correlation_id,
                "error_code": error_code,
            },
        )
        if row is None:
            raise KeyError(execution_id)
        return _execution_row(row)
