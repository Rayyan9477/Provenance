"""``CounterfactualStore`` over the ``pv_app_reader_writer`` pool.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 12 -- the app role holds
  ``INSERT, UPDATE`` on ``agent_runs`` and ``SELECT`` everywhere this reads.
- ``CANONICAL_DECISIONS.md`` -> *Corpus counts*: "Any surface rendering
  ``corpus_size_user_scoped`` or ``corpus_size_visible`` renders the value
  counted at query time, never a constant."

Reads go through the repositories wherever one exists
------------------------------------------------------
``artifacts.get_artifact``, ``artifacts.list_artifact_cases`` and
``cases.get_case_revision_for`` already carry the owner predicate and are
already scanned by ``tests/db/test_repository_read_only.py``. Restating any of
them here would create a second definition of the same scoping rule, which is
the failure that guard exists to catch. The two statements this module does
own are the ones no repository has: the user-scoped corpus count, and the two
``agent_runs`` writes plus the pair read that
``services/control_plane/app/counterfactual/store.py`` holds.

The State Proof is the read port's, not this module's
-------------------------------------------------------
``read.state_proof`` is bound and is the single definition of "why does
Provenance believe this". Re-deriving one here to hand to the MEMORY ON side
would build a second answer to that question -- the same reason
``internal.run_state_proof`` delegates rather than re-reading.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Final

from provenance_db.repositories import artifacts as artifacts_repo
from provenance_db.repositories import cases as cases_repo
from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.ports import OwnerScope, ReadPort
from services.control_plane.app.counterfactual import store as run_store
from services.control_plane.app.counterfactual.service import ArtifactFacts, CaseFacts

__all__ = ["CASE_EVIDENCE_COUNT_SQL", "CORPUS_SIZE_SQL", "SqlCounterfactualStore"]

#: ``CANONICAL_DECISIONS.md`` -> *Corpus counts*: user-scoped, counted now.
#: ``is_retrieval_eligible`` is the generated column that excludes retracted
#: rows, so this is the corpus the MEMORY ON side could actually have seen --
#: not the table's cardinality.
CORPUS_SIZE_SQL: Final[str] = """
    SELECT count(*) AS corpus_size
    FROM evidence_items
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND is_retrieval_eligible
"""

#: The evidence a counterfactual must not have admitted, counted before and
#: after the runs. Scoped to the case rather than to the user so a concurrent
#: ingestion elsewhere in the corpus does not read as this run writing.
CASE_EVIDENCE_COUNT_SQL: Final[str] = """
    SELECT count(DISTINCT e.id) AS evidence_count
    FROM evidence_items e
    JOIN claims c
      ON c.tenant_id = e.tenant_id
     AND c.user_id = e.user_id
     AND c.evidence_id = e.id
    WHERE e.tenant_id = %(tenant_id)s
      AND e.user_id = %(user_id)s
      AND c.case_id = %(case_id)s
"""

#: A counterfactual whose MEMORY ON side is a *replay* needs something
#: committed to replay. Section 8.30's ``REPLAY_COMMITTED`` reads "the already
#: committed Kernel decision and Advocate draft for this artifact", and the
#: draft is an ``action_intents`` row on a case this artifact's evidence
#: reached.
COMMITTED_DRAFT_SQL: Final[str] = """
    SELECT 1 AS present
    FROM action_intents ai
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND EXISTS (
            SELECT 1
            FROM claims c
            JOIN evidence_items e
              ON e.tenant_id = c.tenant_id
             AND e.user_id = c.user_id
             AND e.id = c.evidence_id
            WHERE c.tenant_id = ai.tenant_id
              AND c.user_id = ai.user_id
              AND c.case_id = ai.case_id
              AND e.artifact_id = %(artifact_id)s)
    LIMIT 1
"""


class SqlCounterfactualStore:
    """Every database touch sections 8.30 and 8.31 make."""

    __slots__ = ("_read", "_source")

    def __init__(self, source: ConnectionSource, *, read: ReadPort) -> None:
        self._source = source
        self._read = read

    async def artifact_facts(
        self, scope: OwnerScope, artifact_id: uuid.UUID
    ) -> ArtifactFacts | None:
        async with self._source.connection() as conn:
            row = await artifacts_repo.get_artifact(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                artifact_id=artifact_id,
            )
        if row is None:
            return None
        subject = row.get("subject")
        return ArtifactFacts(
            artifact_id=artifact_id,
            content_sha256=str(row["content_sha256"]),
            mime_type=str(row["mime_type"]),
            subject=None if subject is None else str(subject),
            summary=_summary(row),
        )

    async def case_facts(self, scope: OwnerScope, artifact_id: uuid.UUID) -> CaseFacts | None:
        async with self._source.connection() as conn:
            rows = await artifacts_repo.list_artifact_cases(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                artifact_id=artifact_id,
            )
            if not rows:
                return None
            case_id = uuid.UUID(str(rows[0]["case_id"]))
            revision = await cases_repo.get_case_revision_for(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id, case_id=case_id
            )
        if revision is None:
            return None
        return CaseFacts(case_id=case_id, title=str(rows[0]["case_title"]), revision=revision)

    async def state_proof(self, scope: OwnerScope, case_id: uuid.UUID) -> Mapping[str, Any] | None:
        return await self._read.state_proof(scope, case_id)

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None:
        """Read on a **fresh connection** each time it is called.

        Before and after the runs are two observations, not one cached value.
        A connection held across the model calls would be holding a snapshot
        the second read could be served from.
        """
        async with self._source.connection() as conn:
            return await cases_repo.get_case_revision_for(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id, case_id=case_id
            )

    async def evidence_count(self, scope: OwnerScope, case_id: uuid.UUID) -> int:
        row = await self._one(
            CASE_EVIDENCE_COUNT_SQL,
            {"tenant_id": scope.tenant_id, "user_id": scope.user_id, "case_id": case_id},
        )
        return int(row["evidence_count"]) if row else 0

    async def corpus_size(self, scope: OwnerScope) -> int:
        row = await self._one(
            CORPUS_SIZE_SQL, {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        )
        return int(row["corpus_size"]) if row else 0

    async def has_committed_draft(self, scope: OwnerScope, artifact_id: uuid.UUID) -> bool:
        row = await self._one(
            COMMITTED_DRAFT_SQL,
            {
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "artifact_id": artifact_id,
            },
        )
        return row is not None

    async def open_run(self, params: Mapping[str, Any]) -> None:
        async with self._source.connection() as conn:
            await run_store.insert_run(conn, params)

    async def settle_run(self, params: Mapping[str, Any]) -> bool:
        async with self._source.connection() as conn:
            return await run_store.settle_run(conn, params)

    async def read_pair(self, scope: OwnerScope, trace_id: uuid.UUID) -> list[dict[str, Any]]:
        from agents.runtime.state import GRAPH_NAME_COUNTERFACTUAL

        async with self._source.connection() as conn:
            return await run_store.read_pair(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                trace_id=trace_id,
                graph_name=GRAPH_NAME_COUNTERFACTUAL,
            )

    async def _one(self, sql: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
        async with self._source.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, dict(params))
            row = await cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in (cur.description or ())]
            return dict(zip(columns, row, strict=True))


def _summary(row: Mapping[str, Any]) -> str:
    """Section 8.31's ``artifact_summary``, from columns rather than from prose.

    The specification's example is a sentence a human wrote. Nothing in this
    build can produce that sentence without a parser, so this reports the
    metadata the row actually carries and no more -- an invented summary of a
    document nobody read is exactly the kind of confident nothing the register
    in ``unbound.py`` exists to prevent.
    """
    parts = [str(row.get("subject") or "(no subject)"), str(row.get("mime_type"))]
    size = row.get("size_bytes")
    if size is not None:
        parts.append(f"{int(size)} bytes")
    return " - ".join(parts)
