"""Reads over the agent run ledger, which is the Memory Trace's spine.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

``agent_runs.tool_calls`` is the column and ``mcp_tool_calls[]`` is the
HTTP field (``CANONICAL_DECISIONS.md`` -> *MCP tool-call naming*).
``agent_runs.model_route`` records the model that actually served the run,
so every run is attributable to the model that produced it. That column is what
makes the submission's model disclosure checkable against persisted state
rather than against a README — ``CANONICAL_DECISIONS.md`` -> *Disclosure*
ships on Opus 4.6 and says so, and this read is how a judge confirms it.

``memory_mode`` and ``is_counterfactual`` are returned together because
``ck_agent_runs_counterfactual_consistent`` makes them the same fact, and the
Judge Mode counterfactual is only provable if both are read from the row rather
than one being inferred by the renderer.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from provenance_contracts.identity import Principal
from provenance_db import retry
from provenance_db.repositories._execute import _fetch_one, _owner, _scope

__all__ = ["AGENT_RUN_SQL", "get_agent_run", "get_agent_run_for"]

AGENT_RUN_SQL = """
    SELECT id, trace_id, graph_name, graph_version, model_route,
           memory_mode, is_counterfactual, status,
           started_at, finished_at, expires_at,
           input_artifact_id, allowed_case_ids, retrieval_candidate_count,
           error_code, tool_calls, model_calls, capability_status
    FROM agent_runs
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND id = %(run_id)s
"""


async def get_agent_run(
    conn: AsyncConnection[Any],
    principal: Principal,
    run_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> dict[str, Any] | None:
    """One agent run, with its model route and its tool calls.

    ``tool_calls``, ``model_calls`` and ``capability_status`` are
    caller-reported by the agent runtime and are disclosed as such
    (``frontend/32_JUDGE_MODE.md`` section 6.4). They are metadata *about* a
    run: nothing in State Proof and no canonical read path consults them, and
    this function returning them does not make them evidence of anything the
    Kernel did.
    """
    return await _fetch_one(
        conn,
        AGENT_RUN_SQL,
        {**_scope(principal), "run_id": run_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def get_agent_run_for(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID, run_id: uuid.UUID
) -> dict[str, Any] | None:
    """:func:`get_agent_run`, for a caller holding an owner pair.

    Section 9.2's bootstrap read. The owner pair comes from the capability
    binding rather than from a ``Principal``: an internal caller *has* no
    principal with a ``cognito_sub``, and the binding is where its ownership
    legitimately comes from. Same statement, so the projection cannot drift
    between the human and the workload paths.
    """
    return await _fetch_one(conn, AGENT_RUN_SQL, {**_owner(tenant_id, user_id), "run_id": run_id})
