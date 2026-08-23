"""Action intents: read, edit, approve, reject.

Authority: ``specs/15_API_SPEC.md`` sections 8.23-8.27.

This router is the human authorisation boundary. Nothing here sends anything;
approval writes canonical state and an outbox event, and the executor
(section 9.11, on the internal surface) is the only thing in the system that
can produce an external effect. That separation is enforced at the Cognito
app-client boundary as well as here: the agent runtime's token can never hold
``provenance.action/execute``.

Two failure shapes are worth naming.

``409 ACTION_STALE`` carries section 7.3's whole block -- ``stale_reason``,
``changed_since``, ``refresh`` -- because "the case moved" is useless to a user
who cannot see *what* moved. The write port raises it; the route does not
reshape it.

``404 ACTION_INTENT_NOT_FOUND`` is what another user's intent looks like, on
every one of these five routes. Section 1.7: a `403` would confirm the object
exists.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from provenance_domain.enums import ActionState
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.deps import (
    HumanContext,
    api_config,
    api_deps,
    as_uuid,
    as_vocabulary,
    enum_values,
    query_params,
    request_ids,
    require_principal,
)
from services.control_plane.app.api.errors import ErrorCode
from services.control_plane.app.api.responses import (
    absent,
    begin,
    json_response,
    page_envelope,
    read_page,
)
from services.control_plane.app.api.schemas.public import (
    ApproveRequest,
    DraftUpdateRequest,
    RejectRequest,
)

router = APIRouter(tags=["actions"])

Ctx = Annotated[HumanContext, Depends(require_principal)]
Config = Annotated[ApiConfig, Depends(api_config)]
Deps = Annotated[Dependencies, Depends(api_deps)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]

ACTION_STATES = enum_values(ActionState)


@router.get("/action-intents", summary="Drafted actions awaiting a human.")
async def action_intents(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "case_id": as_uuid(params, "case_id"),
        "statuses": as_vocabulary(params, "status", ACTION_STATES),
    }
    page = read_page(params, collection="action_intents", filters=filters, config=config)
    rows, has_more = await deps.read.list_action_intents(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows,
            has_more,
            page,
            id_field="action_intent_id",
            sort_fields=["created_at"],
            config=config,
        )
    )


@router.get(
    "/action-intents/{action_intent_id}",
    summary="One intent: the draft, its hash, and what supports it.",
)
async def action_intent(ctx: Ctx, deps: Deps, action_intent_id: uuid.UUID) -> JSONResponse:
    row = await deps.read.get_action_intent(ctx.scope, action_intent_id)
    if row is None:
        raise absent(ErrorCode.ACTION_INTENT_NOT_FOUND)
    return json_response(row)


@router.put(
    "/action-intents/{action_intent_id}/draft",
    summary="Edit the drafted message. The recipient is not editable.",
)
async def update_draft(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    action_intent_id: uuid.UUID,
    payload: DraftUpdateRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.25. Provenance does not refuse a user's own words -- it
    records which sentences the record no longer supports and shows them."""
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="action.draft_update",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.update_draft(ctx.scope, action_intent_id, payload)
        if row is None:
            raise absent(ErrorCode.ACTION_INTENT_NOT_FOUND)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)


@router.post(
    "/action-intents/{action_intent_id}/approve",
    summary="Authorise the send. Nothing leaves the system before this.",
)
async def approve(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    action_intent_id: uuid.UUID,
    payload: ApproveRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.26.

    The hash is computed over the **client-submitted** draft, so a race
    between an edit and an approval cannot send a different message from the
    one that was on the user's screen. That hash becomes
    ``approval_draft_sha256``, which the executor re-checks in section 9.11.
    """
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="action.approve",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.approve(ctx.scope, action_intent_id, payload)
        if row is None:
            raise absent(ErrorCode.ACTION_INTENT_NOT_FOUND)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)


@router.post(
    "/action-intents/{action_intent_id}/reject",
    summary="Decline the draft. Recorded, not discarded.",
)
async def reject(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    action_intent_id: uuid.UUID,
    payload: RejectRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.27. A rejection is evidence about the user's own position."""
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="action.reject",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.reject(ctx.scope, action_intent_id, payload)
        if row is None:
            raise absent(ErrorCode.ACTION_INTENT_NOT_FOUND)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)
