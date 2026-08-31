"""The read surface over canonical memory, plus user corrections.

Authority: ``specs/15_API_SPEC.md`` sections 8.3-8.17 and 8.29.

Every handler in this file takes ``ctx.scope`` -- an
:class:`~services.control_plane.app.api.ports.OwnerScope` built from the
resolved principal -- as the first argument to its port call. There is no
overload that omits it, so "forgot to scope the query" is not a mistake this
file can make; the closest a route can come is passing a *filter* that names
another user's row, and a filter is applied inside the caller's own scope, so
it narrows and never widens.

A `POST /v1/cases/{case_id}/corrections` is the only mutation here, and it is
not an edit. Section 8.14: a correction is first-class evidence. The route
therefore hands a typed payload to the write port and the Memory Kernel
decides; it writes no canonical table itself.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from provenance_domain.enums import CaseStatus, CommitmentStatus, TriggerState
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.deps import (
    HumanContext,
    api_config,
    api_deps,
    as_bool,
    as_int,
    as_uuid,
    as_vocabulary,
    enum_values,
    query_params,
    request_ids,
    require_principal,
)
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.responses import (
    absent,
    begin,
    case_revision_headers,
    json_response,
    page_envelope,
    read_page,
)
from services.control_plane.app.api.schemas.public import CorrectionRequest, TriggerWakeRequest

router = APIRouter(tags=["memory"])

Ctx = Annotated[HumanContext, Depends(require_principal)]
Config = Annotated[ApiConfig, Depends(api_config)]
Deps = Annotated[Dependencies, Depends(api_deps)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]

CASE_STATUSES = enum_values(CaseStatus)
COMMITMENT_STATUSES = enum_values(CommitmentStatus)
TRIGGER_STATES = enum_values(TriggerState)


# --------------------------------------------------------------------------
# 8.3 / 8.4
# --------------------------------------------------------------------------


@router.get("/me", summary="Session bootstrap: identity, flags, judge mode.")
async def me(ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    """Section 8.3.

    ``user_id`` and ``tenant_id`` are read off the principal, which was
    resolved from the ``users`` row keyed on the verified ``cognito_sub``. A
    token carrying a forged ``custom:tenant_id`` therefore changes nothing
    here, and ``tests/auth/test_auth_principal.py`` presents exactly such a
    token to prove it.

    ``feature_flags.fixture_mode`` mirrors ``GET /v1/version`` so the UI has
    one thing to bind to; the two are read from the same
    :class:`ApiConfig`, so they cannot disagree.
    """
    row = await deps.read.me(ctx.scope)
    if row is None:  # pragma: no cover - the principal resolved from this row
        raise absent(ErrorCode.NOT_FOUND)
    flags = dict(row.get("feature_flags") or {})
    flags["fixture_mode"] = config.fixture_mode
    return json_response(
        {
            "user_id": str(ctx.principal.user_id),
            "tenant_id": str(ctx.principal.tenant_id),
            "display_name": ctx.principal.display_name,
            "email": ctx.principal.email,
            "timezone": ctx.principal.timezone,
            "home_region": row.get("home_region") or ctx.record.home_region,
            "created_at": ctx.record.created_at,
            "feature_flags": flags,
            "judge_mode_enabled": ctx.judge_mode,
            "ingest_alias_status": row.get("ingest_alias_status"),
        }
    )


@router.get("/dashboard", summary="The deterministic home read model.")
async def dashboard(request: Request, ctx: Ctx, deps: Deps) -> JSONResponse:
    """Section 8.4. No model call, and not a raw table dump."""
    params = query_params(request)
    row = await deps.read.dashboard(
        ctx.scope,
        context_id=as_uuid(params, "context_id"),
        attention_only=as_bool(params, "attention_only"),
        statuses=as_vocabulary(params, "status", CASE_STATUSES),
    )
    return json_response({**row, "generated_at": deps.clock()})


# --------------------------------------------------------------------------
# 8.5 - 8.7
# --------------------------------------------------------------------------


@router.get("/contexts", summary="Life contexts, paginated.")
async def contexts(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    page = read_page(params, collection="contexts", filters={}, config=config)
    rows, has_more = await deps.read.list_contexts(ctx.scope, limit=page.limit, after=page.after)
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="context_id", sort_fields=["created_at"], config=config
        )
    )


@router.get("/relationships", summary="Counterparty relationships, paginated.")
async def relationships(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "context_id": as_uuid(params, "context_id"),
        "counterparty_id": as_uuid(params, "counterparty_id"),
        "attention_only": as_bool(params, "attention_only"),
    }
    page = read_page(params, collection="relationships", filters=filters, config=config)
    rows, has_more = await deps.read.list_relationships(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows,
            has_more,
            page,
            id_field="relationship_id",
            # `updated_at`, because that is what the statement orders and
            # keysets on: `ORDER BY r.updated_at DESC, r.id DESC` with the
            # predicate `(r.updated_at, r.id) < (%(after_updated_at)s, ...)`.
            # This said `last_activity_at`, so the cursor was minted from one
            # column and compared against another. With six relationships and
            # limit=2, page one returned two rows and has_more=true, and
            # following the cursor returned ZERO -- four rows silently
            # unreachable. A cursor that loses rows is worse than one that is
            # ignored: the client believes it has seen everything.
            sort_fields=["updated_at"],
            config=config,
        )
    )


@router.get("/relationships/{relationship_id}", summary="One relationship in full.")
async def relationship(ctx: Ctx, deps: Deps, relationship_id: uuid.UUID) -> JSONResponse:
    row = await deps.read.get_relationship(ctx.scope, relationship_id)
    if row is None:
        raise absent(ErrorCode.RELATIONSHIP_NOT_FOUND)
    return json_response(row)


# --------------------------------------------------------------------------
# 8.8 - 8.13
# --------------------------------------------------------------------------


@router.get("/cases", summary="Cases, paginated and filterable.")
async def cases(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "statuses": as_vocabulary(params, "status", CASE_STATUSES),
        "relationship_id": as_uuid(params, "relationship_id"),
        "context_id": as_uuid(params, "context_id"),
        "attention_only": as_bool(params, "attention_only"),
    }
    page = read_page(params, collection="cases", filters=filters, config=config)
    rows, has_more = await deps.read.list_cases(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows,
            has_more,
            page,
            id_field="case_id",
            sort_fields=["last_activity_at"],
            config=config,
        )
    )


@router.get("/cases/{case_id}", summary="One case, with its revision header.")
async def case_detail(ctx: Ctx, deps: Deps, case_id: uuid.UUID) -> JSONResponse:
    row = await deps.read.get_case(ctx.scope, case_id)
    if row is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    return json_response(row, headers=case_revision_headers(row.get("revision")))


@router.get("/cases/{case_id}/timeline", summary="Everything that happened, in order.")
async def timeline(
    request: Request, ctx: Ctx, config: Config, deps: Deps, case_id: uuid.UUID
) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {"kinds": tuple(params.get("kind", ()))}
    page = read_page(params, collection="timeline", filters=filters, config=config)
    # `after` is threaded explicitly. It used to be parsed into `page` and then
    # dropped here, so a correctly-signed next_cursor -- one this endpoint had
    # itself just issued -- was accepted with a 200 and ignored, and a client
    # following it read page one forever. The adapter and the SQL had supported
    # keyset paging the whole time (read.list_timeline reads filters["after"]
    # and binds after_occurred_at/after_id); only the argument was missing, so
    # nothing failed and nothing was logged.
    result = await deps.read.list_timeline(
        ctx.scope, case_id, limit=page.limit, after=page.after, **filters
    )
    if result is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    rows, has_more = result
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="id", sort_fields=["occurred_at"], config=config
        )
    )


@router.get("/cases/{case_id}/state-proof", summary="Why Provenance believes this. No model.")
async def state_proof(request: Request, ctx: Ctx, deps: Deps, case_id: uuid.UUID) -> JSONResponse:
    """Section 8.11. Deterministic, and correct with Bedrock unavailable."""
    params = query_params(request)
    row = await deps.read.state_proof(
        ctx.scope,
        case_id,
        include_retracted=as_bool(params, "include_retracted"),
        belief_ids=tuple(_uuids(params.get("belief_id", ()), "belief_id")),
        max_evidence_per_belief=as_int(
            params, "max_evidence_per_belief", default=10, low=1, high=50
        ),
    )
    if row is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    return json_response(row, headers=case_revision_headers(row.get("case_revision")))


@router.get("/cases/{case_id}/conflicts", summary="Open and resolved conflicts on a case.")
async def conflicts(
    request: Request, ctx: Ctx, config: Config, deps: Deps, case_id: uuid.UUID
) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {"statuses": tuple(params.get("status", ()))}
    page = read_page(params, collection="conflicts", filters=filters, config=config)
    # As in `timeline` above: parsed, then dropped. Same silent no-op.
    result = await deps.read.list_conflicts(
        ctx.scope, case_id, limit=page.limit, after=page.after, **filters
    )
    if result is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    rows, has_more = result
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="conflict_id", sort_fields=["detected_at"], config=config
        )
    )


@router.get("/beliefs/{belief_id}", summary="One belief, its grounding and its lineage.")
async def belief(request: Request, ctx: Ctx, deps: Deps, belief_id: uuid.UUID) -> JSONResponse:
    params = query_params(request)
    row = await deps.read.get_belief(
        ctx.scope, belief_id, include_retracted=as_bool(params, "include_retracted")
    )
    if row is None:
        raise absent(ErrorCode.BELIEF_NOT_FOUND)
    return json_response(row)


# --------------------------------------------------------------------------
# 8.14 -- the one mutation on this router
# --------------------------------------------------------------------------


@router.post(
    "/cases/{case_id}/corrections",
    status_code=201,
    summary="Record a user correction as evidence.",
)
async def correction(
    request: Request,
    ctx: Ctx,
    deps: Deps,
    case_id: uuid.UUID,
    payload: CorrectionRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 8.14.

    The route does not write ``evidence_items``, ``claims`` or ``beliefs``. It
    builds a typed proposal and submits it through the write port, which is
    the Memory Kernel's door. ``tools/write_path_lint.py`` exists to keep that
    true when somebody is in a hurry.
    """
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        scope="case.correction",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        missing = payload.missing_target()
        if missing is not None:
            raise ApiError(
                ErrorCode.CORRECTION_TARGET_INVALID,
                details={
                    "correction_type": payload.correction_type,
                    "required_field": missing,
                },
            )
        row = await deps.write.create_correction(ctx.scope, case_id, payload)
        if row is None:
            raise absent(ErrorCode.CASE_NOT_FOUND)
    except BaseException:
        await guard.failed()
        raise
    return await guard.complete(201, row)


# --------------------------------------------------------------------------
# 8.15 - 8.16, 8.29
# --------------------------------------------------------------------------


@router.get("/commitments", summary="Obligations, next one first.")
async def commitments(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "case_id": as_uuid(params, "case_id"),
        "relationship_id": as_uuid(params, "relationship_id"),
        "statuses": as_vocabulary(params, "status", COMMITMENT_STATUSES),
        "overdue_only": as_bool(params, "overdue_only"),
    }
    page = read_page(params, collection="commitments", filters=filters, config=config)
    rows, has_more = await deps.read.list_commitments(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="commitment_id", sort_fields=["due_at"], config=config
        )
    )


@router.get("/triggers", summary="Prospective memory: what is armed and why.")
async def triggers(request: Request, ctx: Ctx, config: Config, deps: Deps) -> JSONResponse:
    params = query_params(request)
    filters: dict[str, Any] = {
        "case_id": as_uuid(params, "case_id"),
        "states": as_vocabulary(params, "state", TRIGGER_STATES),
    }
    page = read_page(params, collection="triggers", filters=filters, config=config)
    rows, has_more = await deps.read.list_triggers(
        ctx.scope, limit=page.limit, after=page.after, **filters
    )
    return json_response(
        page_envelope(
            rows, has_more, page, id_field="trigger_id", sort_fields=["not_before"], config=config
        )
    )


@router.post(
    "/triggers/{trigger_id}/wake",
    summary="Prospective memory: evaluate an armed trigger now.",
)
async def wake_trigger(
    ctx: Ctx,
    deps: Deps,
    trigger_id: uuid.UUID,
    payload: TriggerWakeRequest | None = None,
) -> JSONResponse:
    """``16_TRIGGER_DSL.md`` section 13.2 -- the manual wake entry point.

    This is the door onto ``write.wake_trigger``, which has been implemented
    and bound since 2026-08-24 and had nothing that could reach it.
    ``tools/demo_readiness`` reported the demo's second reveal NOT READY for
    exactly that reason: the capability existed and had no handle.

    **It is not a shortcut and must never become one.** The port builds an
    ordinary wake envelope differing from the scheduled one in two fields
    (``wake_source``, ``wake_id``) and calls the identical ``evaluate_trigger``,
    so the guards, the projection read, the predicate, the Memory Kernel, the
    serializable transaction, the revision guard and the idempotency claim are
    all on the path. That is what makes pressing it in front of a judge prove
    *more* rather than less: press it twice and the second press reaches guard
    G2 and answers ``NO_OP / TRIGGER_NOT_ARMED``; press it on a deposit that was
    actually returned and it no-ops on stage, which is the better demo.

    The body carries no verdict and cannot. ``TriggerWakeRequest`` forbids
    unknown fields, so ``{"force": true}`` is a 422 rather than a silently
    ignored key -- ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration*
    forbids mutating and secretly reverting state for presentation, and a
    forcing flag is the same dishonesty with better ergonomics.

    ``evaluation_version`` is read from the trigger row inside the port and is
    never taken from the request: a client-supplied generation would let a
    caller replay a superseded one, or guess the current one and have a wake
    accepted for a trigger it had never seen.

    A trigger outside this scope returns ``None`` from the port and becomes a
    typed 404 here, never a 403 (section 1.7): a 403 confirms the row exists to
    someone who may not read it.

    **There is deliberately no request-level idempotency key here**, and this is
    the one place in the write surface where that is the correct choice rather
    than an omission.

    Every other side-effecting route takes an ``Idempotency-Key`` and replays
    the stored response on a repeat. Doing that here would break the property
    the manual wake exists to demonstrate: pressing the button a second time
    must reach guard G2 and answer ``NO_OP / TRIGGER_NOT_ARMED``, because the
    first press disarmed the trigger. A replayed response would return the
    first press's ``FIRED`` again -- a cached verdict presented as a fresh
    evaluation, on stage, which is precisely the scripted animation
    ``CANONICAL_DECISIONS.md`` -> *Judge Mode* forbids.

    The wake is not unprotected. Its dedupe is the idempotency claim the Kernel
    makes as the FIRST statement of the fire transaction, keyed on the
    trigger's ``evaluation_version`` -- migration ``0009b`` exists to grant
    ``pv_kernel_writer`` the ``SELECT, INSERT`` that claim needs. That key is
    derived from canonical state rather than supplied by the caller, so two
    presses of the same generation collapse the way they should while the
    second press still *reports* what it found.
    """
    row = await deps.write.wake_trigger(ctx.scope, trigger_id, payload)
    if row is None:
        raise absent(ErrorCode.TRIGGER_NOT_FOUND)
    return json_response(row)


@router.get("/cases/{case_id}/memory-trace", summary="What memory did on this case, and when.")
async def memory_trace(request: Request, ctx: Ctx, deps: Deps, case_id: uuid.UUID) -> JSONResponse:
    params = query_params(request)
    row = await deps.read.memory_trace(
        ctx.scope,
        case_id,
        limit=as_int(params, "limit", default=25, low=1, high=100),
    )
    if row is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    return json_response(row, headers=case_revision_headers(row.get("current_revision")))


def _uuids(values: list[str] | tuple[str, ...], parameter: str) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(value) for value in values]
    except ValueError as exc:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": parameter, "reason": "NOT_A_UUID"},
        ) from exc
