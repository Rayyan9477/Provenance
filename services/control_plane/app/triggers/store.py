"""The two reads prospective memory needs, over a live connection — §7.2, §9.5.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §7.2 (the projection SQL, transcribed) and
  §9.5 (authority is resolved from the trigger row, never from the envelope).
- ``db/migrations/versions/0001_identity_aggregates.py``,
  ``0004_obligation_ledger.py`` and ``0006_prospective_memory.py`` — every
  column name below is read from those files.

Reads only. Nothing here writes
--------------------------------
``tools/write_path_lint`` reports canonical write statements only inside
``app/memory_kernel``, and this module does not move that number: it holds three
``SELECT`` statements and no others. The write half of prospective memory is
``memory_kernel/trigger_commit.py``, which is where it has to be.

Why the SQL is here and not in ``provenance_db.repositories``
--------------------------------------------------------------
The projection is §7.2 of the trigger DSL, and it is not a general read model:
it materialises the whitelisted registry paths a stored predicate is allowed to
reference, and its shape is fixed by ``registry.py`` rather than by any API
response. Putting it beside the registry keeps one definition of "what a
predicate may see"; putting it in the shared repository package would separate
the projection from the whitelist it exists to serve and invite a second,
divergent copy the day an endpoint wanted "the same fields, plus one".

The precedent is ``app/actions/store_postgres.py``, which owns the action
plane's statements for the same reason.

One snapshot, one clock
------------------------
:meth:`SqlProjectionReader.read` issues both queries inside **one read-only
transaction**, which is the guarantee ``ProjectionSnapshot`` exists to carry.
``clock.now`` is ``now()`` from that transaction and never the process clock:
§11.5 is explicit that a worker's clock compared against a database-written
deadline is two unsynchronised clocks, and it can produce ``days_overdue = -1``
on a row the database considers overdue.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager
from typing import Any, Final, Protocol

from services.control_plane.app.triggers.projection import (
    ProjectionSnapshot,
    ProjectionUnavailable,
)
from services.control_plane.app.triggers.service import TriggerSnapshot

__all__ = [
    "CASE_PROJECTION_SQL",
    "COMMITMENT_PROJECTION_SQL",
    "TRIGGER_ROW_SQL",
    "ConnectionSource",
    "SqlProjectionReader",
    "SqlTriggerStore",
]


class ConnectionSource(Protocol):
    """Anything that hands out a connection as an async context manager.

    ``provenance_db.pools.RolePool`` satisfies this, and so does a recording
    double in the hermetic suites. Depending on the protocol rather than on
    ``RolePool`` is what makes "did this read bind the owner?" a unit test.
    """

    def connection(self) -> AbstractAsyncContextManager[Any]: ...


#: §9.5. The row **and** the database clock, read together, because the guards
#: judge ``expires_at`` and ``not_before`` against the database's clock and
#: never the caller's. A statement that returned the row alone would force the
#: guard to invent a clock, and inventing a clock is how a trigger fires a
#: minute before its deadline.
#:
#: Scoped by ``(tenant_id, user_id, id)``: §9.5 resolves authority from the row,
#: so "no such trigger" and "not yours" are one answer. Anything else is an
#: existence oracle for other tenants' identifiers.
TRIGGER_ROW_SQL: Final[str] = """
SELECT id, tenant_id, user_id, case_id, trigger_type, predicate_ast, not_before,
       expires_at, state, evaluation_version, basis_case_revision, schedule_name,
       last_evaluated_at, last_result, last_reason_code, fired_at, now() AS db_now
  FROM prospective_triggers
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(trigger_id)s
"""

#: §7.2 query (1), transcribed. The four sub-selects are the derived fields the
#: registry whitelists; ``CANONICAL_DECISIONS.md`` -> *Trigger arithmetic*
#: forbids general arithmetic nodes in the DSL precisely so that every derived
#: number is computed once, here, in reviewed SQL.
#:
#: ``outstanding_currency`` is NULL when the open obligations are in more than
#: one currency. Summing across currencies would produce a number that means
#: nothing, and a single currency name attached to it would make the meaning
#: worse rather than better.
CASE_PROJECTION_SQL: Final[str] = """
SELECT
    c.id                AS case_id,
    c.tenant_id,
    c.user_id,
    c.status            AS case_status,
    c.revision          AS case_revision,
    c.attention_level,
    c.reopened_count,
    c.opened_at,
    c.resolved_at,
    c.last_activity_at,
    now()               AS db_now,
    (SELECT count(*) FROM conflicts f
      WHERE f.tenant_id = c.tenant_id AND f.user_id = c.user_id AND f.case_id = c.id
        AND f.status IN ('OPEN', 'NEEDS_HUMAN'))            AS open_conflict_count,
    (SELECT count(*) FROM conflicts f
      WHERE f.tenant_id = c.tenant_id AND f.user_id = c.user_id AND f.case_id = c.id
        AND f.status = 'NEEDS_HUMAN')                       AS needs_human_conflict_count,
    (SELECT count(*) FROM commitments m
      WHERE m.tenant_id = c.tenant_id AND m.user_id = c.user_id AND m.case_id = c.id
        AND m.status IN ('ACTIVE', 'PARTIAL', 'DISPUTED'))  AS active_commitment_count,
    (SELECT coalesce(sum(m.outstanding_amount), 0) FROM commitments m
      WHERE m.tenant_id = c.tenant_id AND m.user_id = c.user_id AND m.case_id = c.id
        AND m.status IN ('ACTIVE', 'PARTIAL', 'DISPUTED'))  AS total_outstanding_amount,
    (SELECT CASE WHEN count(DISTINCT m.currency) = 1 THEN min(m.currency) ELSE NULL END
       FROM commitments m
      WHERE m.tenant_id = c.tenant_id AND m.user_id = c.user_id AND m.case_id = c.id
        AND m.status IN ('ACTIVE', 'PARTIAL', 'DISPUTED')
        AND m.currency IS NOT NULL)                         AS outstanding_currency
FROM cases c
WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s AND c.id = %(case_id)s
"""

#: §7.2 query (2). ``m.case_id = %(case_id)s`` is a **security control, not an
#: optimisation**: a binding that names a commitment belonging to a different
#: case simply returns no row, which surfaces as ``BINDING_UNRESOLVED`` (§10.4)
#: rather than as a cross-case read.
COMMITMENT_PROJECTION_SQL: Final[str] = """
SELECT
    m.id, m.status, m.commitment_type, m.revision, m.currency,
    m.committed_amount, m.fulfilled_amount, m.outstanding_amount,
    m.due_at, m.valid_from, m.valid_to,
    EXISTS (SELECT 1 FROM fulfillments fu
             WHERE fu.tenant_id = m.tenant_id AND fu.user_id = m.user_id
               AND fu.commitment_id = m.id
               AND fu.admission_status = 'ADMITTED')        AS has_admitted_fulfillment
FROM commitments m
WHERE m.tenant_id = %(tenant_id)s AND m.user_id = %(user_id)s
  AND m.case_id = %(case_id)s
  AND m.id = ANY(%(commitment_ids)s)
"""


async def _rows(conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Every row of *sql*, as dicts keyed by the projected column names.

    Read from ``cursor.description`` rather than from a hand-kept tuple index:
    a column added to the statement and forgotten in an index map is a silent
    off-by-one that type-checks.
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        fetched = await cur.fetchall()
        if not fetched:
            return []
        columns = [description[0] for description in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in fetched]


class SqlTriggerStore:
    """:class:`~...triggers.service.TriggerStore` over the app pool.

    Read-only, and under ``pv_app_reader_writer`` rather than the Kernel's
    credential: loading a trigger is a read, and the Kernel's grant is not
    something to hand out because it happens to be nearby.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    async def load(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> TriggerSnapshot | None:
        """One trigger row and the database clock, or ``None``.

        ``None`` means both "no such trigger" and "not yours", deliberately.
        Distinguishing them would make this an existence oracle for other
        tenants' identifiers.
        """
        async with self._source.connection() as conn:
            rows = await _rows(
                conn,
                TRIGGER_ROW_SQL,
                {"tenant_id": tenant_id, "user_id": user_id, "trigger_id": trigger_id},
            )
        if not rows:
            return None
        row = rows[0]
        return TriggerSnapshot(row=row, db_now=row["db_now"])


class SqlProjectionReader:
    """:class:`~...triggers.projection.ProjectionReader` over the app pool.

    Both queries in one ``READ ONLY`` transaction, so ``cases.revision``, the
    conflict counts, the commitment rows and ``clock.now`` all come from a
    single serializable snapshot. Reading them in separate autocommit
    statements would let the fire transaction's revision guard compare against a
    revision that never coexisted with the values that were evaluated — which
    is the one failure this whole subsystem is arranged to prevent.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    async def read(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        case_id: uuid.UUID,
        commitment_ids: tuple[uuid.UUID, ...],
    ) -> ProjectionSnapshot:
        """The §7.2 snapshot.

        Raises:
            ProjectionUnavailable: the case is not visible to this owner. Not an
                empty
                projection: an absent case and a case with nothing on it lead to
                opposite decisions, and returning the second for the first is
                ``D-00-005`` in its purest form. The evaluator turns it into
                ``ERROR / PROJECTION_FAILED`` and leaves the trigger ``ARMED``.
        """
        scope = {"tenant_id": tenant_id, "user_id": user_id, "case_id": case_id}
        async with self._source.connection() as conn, conn.transaction():
            case_rows = await _rows(conn, CASE_PROJECTION_SQL, scope)
            commitment_rows = await _rows(
                conn,
                COMMITMENT_PROJECTION_SQL,
                {**scope, "commitment_ids": list(commitment_ids)},
            )
        if not case_rows:
            raise ProjectionUnavailable(f"case {case_id} is not visible to this owner")
        return ProjectionSnapshot(
            case_row=case_rows[0],
            commitment_rows={row["id"]: row for row in commitment_rows},
        )
