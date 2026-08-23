"""Traces and Judge Mode.

Authority: ``specs/15_API_SPEC.md`` sections 8.28, 8.30, 8.31, and 2.5 for what
judge mode does **not** grant.

Judge mode unlocks the counterfactual endpoints and a fuller trace rendering.
It grants no cross-user visibility whatsoever: every port call on this router
still takes the caller's own ``OwnerScope``, so a judge who asks for another
user's trace gets the same ``404`` anybody else would. The adversarial lane
asserts that separately, because "the flag only widens the view of your own
data" is exactly the kind of claim that rots quietly.

The render gate
---------------
Section 8.31's counterfactual is only meaningful if both runs were given the
same artifact, the same model, the same prompt version and the same decode
parameters. If any of those differ the comparison proves nothing, so the
outputs are **suppressed** rather than shown with a caveat: a side-by-side
that a reader will screenshot must not be renderable when the parity block
says the two runs were not comparable.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from services.control_plane.app.api.config import Dependencies
from services.control_plane.app.api.deps import (
    HumanContext,
    api_deps,
    request_ids,
    require_principal,
)
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.responses import absent, begin, json_response
from services.control_plane.app.api.schemas.public import CounterfactualRequest

router = APIRouter(tags=["judge"])

Ctx = Annotated[HumanContext, Depends(require_principal)]
Deps = Annotated[Dependencies, Depends(api_deps)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _require_judge(ctx: HumanContext) -> None:
    if not ctx.judge_mode:
        raise ApiError(ErrorCode.JUDGE_MODE_DISABLED)


@router.get("/traces/{trace_id}", summary="The trace DAG, assembled from real rows.")
async def trace(ctx: Ctx, deps: Deps, trace_id: uuid.UUID) -> JSONResponse:
    """Section 8.28.

    ``judge`` widens what is rendered for the caller's own trace. It is not an
    authorisation argument: the scope is still the caller's, and the port
    returns ``None`` for a trace that is not theirs.
    """
    row = await deps.read.get_trace(ctx.scope, trace_id, judge=ctx.judge_mode)
    if row is None:
        raise absent(ErrorCode.TRACE_NOT_FOUND)
    return json_response(row)


@router.post(
    "/judge-mode/counterfactual",
    status_code=202,
    summary="Run the same artifact with memory off and on.",
)
async def start_counterfactual(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    payload: CounterfactualRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.30. Neither mode writes canonical state."""
    _require_judge(ctx)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="judge.counterfactual",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.start_counterfactual(ctx.scope, payload)
        if row is None:
            raise absent(ErrorCode.ARTIFACT_NOT_FOUND)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(202, row)


@router.get(
    "/judge-mode/counterfactual/{counterfactual_id}",
    summary="Both outputs side by side, gated on parity.",
)
async def counterfactual(ctx: Ctx, deps: Deps, counterfactual_id: uuid.UUID) -> JSONResponse:
    """Section 8.31."""
    _require_judge(ctx)
    row = await deps.write.get_counterfactual(ctx.scope, counterfactual_id)
    if row is None:
        raise absent(ErrorCode.COUNTERFACTUAL_NOT_FOUND)
    return json_response(_gate_on_parity(dict(row)))


@router.get("/judge-mode/agent-views", summary="The read-only views the agent may query.")
async def agent_views(ctx: Ctx, deps: Deps) -> JSONResponse:
    """T8.7.

    Read from ``information_schema.views`` through the port rather than from a
    constant, so the answer is what the database actually exposes to
    ``pv_agent_reader``. A hard-coded list would agree with the migration
    right up until somebody added a view and forgot this file.
    """
    _require_judge(ctx)
    return json_response({"views": await deps.read.agent_view_names()})


def _gate_on_parity(body: dict[str, Any]) -> dict[str, Any]:
    """Suppress both outputs unless every parity field is equal."""
    parity = body.get("parity")
    if isinstance(parity, dict) and parity.get("all_equal") is True:
        return body
    for side in ("memory_off", "memory_on"):
        block = body.get(side)
        if isinstance(block, dict):
            body[side] = {**block, "output": None}
    return body
