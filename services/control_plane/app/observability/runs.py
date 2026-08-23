"""Section 9.9: settling the agent run, which is the Memory Trace's spine.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.9 -- "closes the run and burns the
  capability".
- ``specs/10_DATABASE_DDL.md`` section 11.3 and migration ``0008``, which
  create ``agent_runs`` and its four CHECK constraints.
- ``frontend/32_JUDGE_MODE.md`` section 6.4 -- ``tool_calls``, ``model_calls``
  and ``capability_status`` are disclosed as **caller-reported**.

Why an ``UPDATE`` here is not a second canonical writer
--------------------------------------------------------
``agent_runs`` is deliberately absent from
``tools/write_path_lint.CANONICAL_TABLES``: the Memory Kernel holds only
``SELECT`` on it, so there is no Kernel-only write to protect and this
statement is outside write rule ``W2``'s scope rather than an exception to it.
``tests/api/test_agent_run_completion.py`` asserts that absence, so the day the
table becomes canonical the linter -- and that test -- say so.

What this module refuses to invent
------------------------------------
Three columns and one derivation, and the line between them is the whole point.

* ``tool_calls`` and ``model_calls`` are written **exactly as reported**. Every
  entry is a closed model (``ToolCallRecord``, ``ModelCallRecord``), so a key
  outside the allowlist is a ``422`` and returned rows or SQL text cannot reach
  the Memory Trace. Migration ``0008``'s column comment describes elements
  carrying ``seq``, ``duration_ms`` and ``started_at``; section 9.9's body does
  not carry those for ``model_calls``, and they are left **absent** rather than
  filled with zeros. A default in a measurement column is a measurement nobody
  took.
* An empty array is written as an empty array. A run that bound no MCP tool
  measured zero of them, and ``NULL`` renders as "unknown" -- the ``D-00-005``
  distinction, in the direction that quietly loses a real result.
* ``capability_status.proposal_tool_bound`` is the **server's** statement and
  not the caller's, because ``ck_agent_runs_counterfactual_toolless`` is a
  CHECK on it: a counterfactual run may never claim the proposal tool. It is
  ``not is_counterfactual``, which is the same rule
  ``KernelInternalPort.submit_proposal`` enforces by refusing a counterfactual
  run outright. One fact, one derivation, two places it is visible.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from psycopg.types.json import Jsonb

__all__ = [
    "SETTLE_AGENT_RUN_SQL",
    "RunAlreadySettledError",
    "settle_params",
    "settle_run",
]


#: ``status = 'RUNNING'`` in the predicate is load-bearing. The capability
#: layer refuses a second completion first -- a terminal ``agent_runs.status``
#: resolves as ``CONSUMED`` -- but a guard that depends on another module's
#: correctness is not a guard. Without it a replayed call would overwrite the
#: first call's arrays with the second's, and a Memory Trace that can be
#: rewritten after the fact is not evidence of anything.
SETTLE_AGENT_RUN_SQL: Final[str] = """
UPDATE agent_runs
   SET status = %(status)s,
       finished_at = %(finished_at)s,
       error_code = %(error_code)s,
       tool_calls = %(tool_calls)s,
       model_calls = %(model_calls)s,
       capability_status = %(capability_status)s
 WHERE tenant_id = %(tenant_id)s
   AND user_id = %(user_id)s
   AND id = %(agent_run_id)s
   AND status = 'RUNNING'
"""


class RunAlreadySettledError(RuntimeError):
    """The guarded ``UPDATE`` matched no row, so the run was already terminal.

    A distinct type rather than a boolean return: the caller must not be able
    to treat "nothing was written" as "written", and section 9.9's answer to a
    second call is ``403 CAPABILITY_CONSUMED`` rather than a cheerful ``200``
    describing a ``finished_at`` no row holds.
    """


def settle_params(
    *,
    run: Mapping[str, Any],
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    status: str,
    error_code: str | None,
    tool_calls: Sequence[Mapping[str, Any]],
    model_calls: Sequence[Mapping[str, Any]],
    finished_at: datetime,
) -> dict[str, Any]:
    """Every bind for :data:`SETTLE_AGENT_RUN_SQL`.

    *run* is the row already read for this request; only
    ``is_counterfactual`` is taken from it, and only because
    ``ck_agent_runs_counterfactual_toolless`` is a CHECK on the value derived
    from it.
    """
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "agent_run_id": agent_run_id,
        "status": status,
        "error_code": error_code,
        "finished_at": finished_at,
        "tool_calls": Jsonb([dict(entry) for entry in tool_calls]),
        "model_calls": Jsonb([dict(entry) for entry in model_calls]),
        "capability_status": Jsonb(
            {
                "proposal_tool_bound": not bool(run.get("is_counterfactual")),
                "memory_mode": run.get("memory_mode"),
                "terminal_status": status,
            }
        ),
    }


async def settle_run(conn: Any, params: Mapping[str, Any]) -> None:
    """Run the guarded ``UPDATE``.

    Raises:
        RunAlreadySettledError: the predicate matched nothing, which under this
            statement means the run was no longer ``RUNNING``.
    """
    async with conn.cursor() as cur:
        await cur.execute(SETTLE_AGENT_RUN_SQL, dict(params))
        if int(cur.rowcount) == 0:
            raise RunAlreadySettledError(str(params["agent_run_id"]))
