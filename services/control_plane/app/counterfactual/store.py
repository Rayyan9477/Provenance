"""The two ``agent_runs`` rows a counterfactual leaves, and how they are read back.

Authority
---------
- ``db/migrations/versions/0008_events_infrastructure.py`` -- ``agent_runs``
  and its CHECK constraints, which decide almost everything below.
- ``specs/10_DATABASE_DDL.md`` section 11.3, whose ``idx_agent_runs_counterfactual``
  comment names the exact query the Judge Mode panel is served by:
  ``SELECT * FROM agent_runs WHERE trace_id=$1 AND memory_mode IN ('ON','OFF')``.
  That is why the counterfactual id **is** the shared ``trace_id``: there is no
  ``counterfactuals`` table, and the schema's own index says the pair is joined
  on the trace.
- ``specs/15_API_SPEC.md`` section 8.30 safety properties 1 and 4.

Why an ``INSERT`` here is not a second canonical writer
--------------------------------------------------------
``agent_runs`` is deliberately absent from
``tools/write_path_lint.CANONICAL_TABLES`` -- "the Kernel holds only SELECT on
them, so they have no Kernel-only write to protect" -- and DDL section 12
grants the app ``INSERT, UPDATE`` on it. This module is the counterfactual's
half of the same arrangement ``app/observability/runs.py`` holds for section
9.9's settle, and ``scripts/run_ingestion_graph.py`` holds for the ingestion
runner.

Three things the applied schema decides, and one it cannot
-----------------------------------------------------------
1. ``ck_agent_runs_graph`` admits ``'counterfactual'``, not
   ``'counterfactual_graph'``. Section 8.30's prose says the latter; the
   database is the authority and the constant comes from
   ``agents.runtime.state``, which
   ``agents/runtime/tests/test_graph_names_match_the_schema.py`` pins against
   the migration.
2. ``ck_agent_runs_counterfactual_consistent`` is
   ``is_counterfactual = (memory_mode = 'OFF')``. So the MEMORY **ON** row of a
   counterfactual pair is necessarily ``is_counterfactual = false`` -- it is
   distinguishable from a production run only by ``graph_name``. Every read
   here therefore filters on ``graph_name`` as well as on the trace, and
   section 8.30's "excluded from case timelines" rests on the same column.
3. ``ck_agent_runs_counterfactual_toolless`` forbids a counterfactual run from
   claiming the proposal tool. Both rows are written with
   ``proposal_tool_bound = false``, because neither side had it: no capability
   row is minted for either run at all, so there is no id an external caller
   could present.

The one it cannot decide: **there is no column for the model output.** There is
no ``counterfactual_runs`` table either. ``specs/14_PROMPTS.md`` section 6.4 is
explicit that neither output is stored for replay and neither is cached, so the
rows below carry the run's *attribution and configuration* -- which is what the
``parity`` and ``safety`` blocks are computed from -- and the readings
themselves are returned by the run that produced them and are not persisted.
``get_counterfactual`` says so in the response rather than returning ``null``
and letting a reader guess.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from psycopg.types.json import Jsonb

__all__ = [
    "COUNTERFACTUAL_PAIR_SQL",
    "INSERT_COUNTERFACTUAL_RUN_SQL",
    "SETTLE_COUNTERFACTUAL_RUN_SQL",
    "insert_run",
    "read_pair",
    "settle_run",
]

#: ``status`` starts ``RUNNING`` and ``finished_at`` stays NULL, which
#: ``ck_agent_runs_terminal`` requires of a ``RUNNING`` row. Written **before**
#: the model call, not after: a run that dies mid-call must leave a row saying
#: it started, or the ledger records only the runs that went well.
INSERT_COUNTERFACTUAL_RUN_SQL: Final[str] = """
INSERT INTO agent_runs (
    id, tenant_id, user_id, trace_id, graph_name, graph_version, model_route,
    memory_mode, is_counterfactual, status, started_at, expires_at,
    input_artifact_id, allowed_case_ids, retrieval_candidate_count, capability_status
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(trace_id)s, %(graph_name)s, %(graph_version)s,
    %(model_route)s, %(memory_mode)s, %(is_counterfactual)s, 'RUNNING',
    %(started_at)s, %(expires_at)s, %(input_artifact_id)s, %(allowed_case_ids)s,
    %(retrieval_candidate_count)s, %(capability_status)s
)
"""

#: Guarded on ``status = 'RUNNING'`` for the same reason section 9.9's settle
#: is: a settle that can overwrite a settled row makes the ledger rewritable
#: after the fact, and a rewritable ledger is not evidence of anything.
SETTLE_COUNTERFACTUAL_RUN_SQL: Final[str] = """
UPDATE agent_runs
   SET status = %(status)s,
       finished_at = %(finished_at)s,
       error_code = %(error_code)s,
       model_calls = %(model_calls)s,
       tool_calls = %(tool_calls)s,
       capability_status = %(capability_status)s
 WHERE tenant_id = %(tenant_id)s
   AND user_id = %(user_id)s
   AND id = %(id)s
   AND status = 'RUNNING'
"""

#: The panel query from ``idx_agent_runs_counterfactual``'s own comment, plus
#: the owner predicate every read in this system carries and the ``graph_name``
#: filter point 2 of the module docstring explains.
COUNTERFACTUAL_PAIR_SQL: Final[str] = """
    SELECT id, trace_id, graph_name, graph_version, model_route, memory_mode,
           is_counterfactual, status, started_at, finished_at,
           input_artifact_id, allowed_case_ids, retrieval_candidate_count,
           error_code, model_calls, tool_calls, capability_status
    FROM agent_runs
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND trace_id = %(trace_id)s
      AND graph_name = %(graph_name)s
    ORDER BY memory_mode DESC, started_at
"""


async def insert_run(conn: Any, params: Mapping[str, Any]) -> None:
    """Write the ``RUNNING`` row. See :data:`INSERT_COUNTERFACTUAL_RUN_SQL`."""
    async with conn.cursor() as cur:
        await cur.execute(INSERT_COUNTERFACTUAL_RUN_SQL, dict(params))


async def settle_run(conn: Any, params: Mapping[str, Any]) -> bool:
    """Settle the row. ``False`` means it was no longer ``RUNNING``."""
    async with conn.cursor() as cur:
        await cur.execute(SETTLE_COUNTERFACTUAL_RUN_SQL, dict(params))
        return int(cur.rowcount) == 1


async def read_pair(
    conn: Any, *, tenant_id: uuid.UUID, user_id: uuid.UUID, trace_id: uuid.UUID, graph_name: str
) -> list[dict[str, Any]]:
    """Both runs of one counterfactual, MEMORY_OFF first."""
    async with conn.cursor() as cur:
        await cur.execute(
            COUNTERFACTUAL_PAIR_SQL,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "trace_id": trace_id,
                "graph_name": graph_name,
            },
        )
        columns = [desc[0] for desc in (cur.description or ())]
        return [dict(zip(columns, row, strict=True)) for row in await cur.fetchall()]


def insert_params(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    trace_id: uuid.UUID,
    graph_name: str,
    graph_version: str,
    model_route: Mapping[str, Any],
    memory_mode: str,
    started_at: datetime,
    expires_at: datetime,
    artifact_id: uuid.UUID,
    allowed_case_ids: Sequence[str],
    corpus_size_visible: int,
    capability_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Every bind for :data:`INSERT_COUNTERFACTUAL_RUN_SQL`.

    ``is_counterfactual`` is **derived** from ``memory_mode`` rather than taken
    as an argument, because ``ck_agent_runs_counterfactual_consistent`` says
    they are one fact. A caller able to pass both could pass them disagreeing,
    and would then find out at the end of a paid model call.
    """
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "graph_name": graph_name,
        "graph_version": graph_version,
        "model_route": Jsonb(dict(model_route)),
        "memory_mode": memory_mode,
        "is_counterfactual": memory_mode == "OFF",
        "started_at": started_at,
        "expires_at": expires_at,
        "input_artifact_id": artifact_id,
        "allowed_case_ids": Jsonb(list(allowed_case_ids)),
        "retrieval_candidate_count": corpus_size_visible,
        "capability_status": Jsonb(dict(capability_status)),
    }


def settle_params(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
    error_code: str | None,
    finished_at: datetime,
    model_calls: Sequence[Mapping[str, Any]],
    capability_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Every bind for :data:`SETTLE_COUNTERFACTUAL_RUN_SQL`.

    ``tool_calls`` is written as an empty array rather than left NULL: this run
    bound no MCP tool at all, which is a measurement, and NULL reads as
    "unknown" -- the ``D-00-005`` distinction in the direction that quietly
    loses a real result.
    """
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "status": status,
        "error_code": error_code,
        "finished_at": finished_at,
        "model_calls": Jsonb([dict(call) for call in model_calls]),
        "tool_calls": Jsonb([]),
        "capability_status": Jsonb(dict(capability_status)),
    }
