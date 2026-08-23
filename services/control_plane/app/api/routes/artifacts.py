"""Artifacts and the forwarding alias.

Authority: ``specs/15_API_SPEC.md`` sections 8.17-8.22.

The upload path is where a client would most like to choose a key, so it
cannot. Section 8.18 fixes the layout at
``raw/{tenant_id}/{user_id}/{artifact_id}/original`` and the server builds it
from the resolved principal; the request model has no ``s3_key`` field and
forbids extras, and the user-supplied filename is metadata that never touches
the key. The pre-signed URL is minted for that one key, so a client cannot
redirect the upload into another tenant's prefix even with a valid URL in
hand.

``GET /v1/ingest-alias`` returns the *display* address and never the token or
the hash. The token is shown exactly once, by the rotation endpoint, at the
moment it is minted.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.deps import (
    HumanContext,
    api_config,
    api_deps,
    as_uuid,
    as_vocabulary,
    query_params,
    request_ids,
    require_principal,
)
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.responses import (
    absent,
    begin,
    json_response,
    page_envelope,
    read_page,
)
from services.control_plane.app.api.schemas.public import (
    ArtifactCompleteRequest,
    RotateAliasRequest,
    UploadIntentRequest,
)

router = APIRouter(tags=["artifacts"])

Ctx = Annotated[HumanContext, Depends(require_principal)]
Config = Annotated[ApiConfig, Depends(api_config)]
Deps = Annotated[Dependencies, Depends(api_deps)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]

PARSER_STATUSES = ("PENDING", "PARSING", "PARSED", "FAILED", "UNSUPPORTED")

#: Never returned on a read. ``alias_hash`` is the capability the SES worker
#: presents; ``alias_token`` is the secret behind it.
ALIAS_SECRETS = ("alias_token", "alias_hash")


@router.get("/artifacts", summary="Received and uploaded artifacts, paginated.")
async def artifacts(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "case_id": as_uuid(params, "case_id"),
        "parser_statuses": as_vocabulary(params, "parser_status", PARSER_STATUSES),
        "source_types": tuple(params.get("source_type", ())),
    }
    page = read_page(params, collection="artifacts", filters=filters, config=config)
    rows, has_more = await deps.read.list_artifacts(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="artifact_id", sort_fields=["received_at"], config=config
        )
    )


@router.post(
    "/artifacts/upload-intent",
    status_code=201,
    summary="Pre-signed PUT for one server-chosen key.",
)
async def upload_intent(
    request: Request,
    ctx: Ctx,
    config: Config,
    deps: Deps,
    payload: UploadIntentRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.18.

    The MIME allowlist and the size ceiling are checked here rather than in
    the schema so the refusal carries its own error code: a client that sent
    an executable needs to see ``UNSUPPORTED_MIME_TYPE`` and the allowed set,
    not a generic ``VALIDATION_FAILED`` it has to guess at, and an oversized
    declaration is a ``413`` rather than a ``422``.
    """
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="artifact.upload_intent",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        if payload.mime_type not in config.allowed_upload_mime_types:
            raise ApiError(
                ErrorCode.UNSUPPORTED_MIME_TYPE,
                details={"allowed": list(config.allowed_upload_mime_types)},
            )
        if payload.size_bytes > config.max_artifact_bytes:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                details={"max_size_bytes": config.max_artifact_bytes},
            )
        row = await deps.write.upload_intent(ctx.scope, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)


@router.post(
    "/artifacts/{artifact_id}/complete",
    status_code=202,
    summary="Declare the upload finished and queue interpretation.",
)
async def complete(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    artifact_id: uuid.UUID,
    payload: ArtifactCompleteRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.19. Returns immediately; it does not wait for the graph."""
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="artifact.complete",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.complete_artifact(ctx.scope, artifact_id, payload)
        if row is None:
            raise absent(ErrorCode.ARTIFACT_NOT_FOUND)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(202, row)


@router.get("/artifacts/{artifact_id}", summary="One artifact and its parse result.")
async def artifact(request: Request, ctx: Ctx, deps: Deps, artifact_id: uuid.UUID) -> JSONResponse:
    params = query_params(request)
    row = await deps.read.get_artifact(
        ctx.scope, artifact_id, include_download_url="download" in params
    )
    if row is None:
        raise absent(ErrorCode.ARTIFACT_NOT_FOUND)
    return json_response(row)


@router.get("/ingest-alias", summary="The forwarding address, without its secret.")
async def ingest_alias(ctx: Ctx, deps: Deps) -> JSONResponse:
    """Section 8.21. The token is not here and cannot be recovered here."""
    row = await deps.read.ingest_alias(ctx.scope)
    if row is None:
        raise absent(ErrorCode.INGEST_ALIAS_NOT_FOUND)
    return json_response({k: v for k, v in row.items() if k not in ALIAS_SECRETS})


@router.post(
    "/ingest-alias/rotate",
    status_code=201,
    summary="Mint a new forwarding address. Shows the token once.",
)
async def rotate_alias(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    payload: RotateAliasRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.22. Rotation disables the previous address immediately."""
    del payload  # the body carries no parameters; it exists to forbid extras
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="ingest_alias.rotate",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.write.rotate_ingest_alias(ctx.scope)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)
