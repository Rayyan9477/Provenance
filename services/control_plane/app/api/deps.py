"""The request-scoped dependencies every route hangs off.

Authority: ``specs/15_API_SPEC.md`` sections 2.3-2.6, 3.2-3.6, 5 and 6.

The one mechanism worth stating plainly
---------------------------------------
**A caller-supplied ``user_id`` cannot reach a query, because no query takes
one.** Every public port method's first parameter is an
:class:`~services.control_plane.app.api.ports.OwnerScope` and every internal
port method's first parameter is a ``CapabilityBinding``. Neither is
constructible from request data:

* :func:`owner_scope` is the only place an ``OwnerScope`` is built on the
  public surface, and it is built from a :class:`Principal` whose
  ``tenant_id``/``user_id`` came from the ``users`` row keyed on the verified
  ``cognito_sub`` -- never from a token claim, never from the body.
* A ``CapabilityBinding`` is produced only by
  :func:`~services.control_plane.app.auth.capabilities.resolve_capability`,
  which reads a server-side row.

So a route physically cannot issue a scoped read without ownership: ownership
is a required positional argument, and the only two factories for it ignore
the request body entirely. ``extra="forbid"`` on every request model is the
second line -- it turns an attempt into a ``422`` rather than a shrug -- but
the type is the first.

Two clocks, deliberately
------------------------
``Dependencies.clock`` is the *domain* clock: it is what capability liveness
and response timestamps are measured against, and the hermetic suites freeze
it. Token expiry is **not** measured against it. A frozen or rewound domain
clock must never make an expired access token acceptable, so the token window
is evaluated against the later of the injected clock and the real one.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request

from provenance_contracts.identity import InternalPrincipal, Principal
from provenance_domain.enums import OAuthScope
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.context import current_request_id, current_trace_id
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import OwnerScope, UserRecord
from services.control_plane.app.auth import route_class as route_class_mod
from services.control_plane.app.auth.capabilities import resolve_capability
from services.control_plane.app.auth.jwt import TokenClaims
from services.control_plane.app.auth.principal import build_human_principal, judge_mode_for
from services.control_plane.app.auth.route_class import RouteClass

__all__ = [
    "HumanContext",
    "WorkloadContext",
    "api_config",
    "api_deps",
    "bearer_token",
    "enforce_json_media_type",
    "owner_scope",
    "request_ids",
    "require_capability",
    "require_principal",
    "require_workload",
    "require_workload_scope",
    "resolve_body_capability",
]

#: The logical name used when a token names a Cognito app client nobody
#: configured. It is a member of no route class, so it reaches nothing.
UNKNOWN_CLIENT = "unknown-app-client"


def api_config(request: Request) -> ApiConfig:
    config: ApiConfig = request.app.state.api_config
    return config


def api_deps(request: Request) -> Dependencies:
    deps: Dependencies = request.app.state.api_deps
    return deps


def request_ids(request: Request) -> tuple[uuid.UUID, uuid.UUID]:
    """``(trace_id, request_id)`` for this request."""
    trace = getattr(request.state, "trace_id", None)
    rid = getattr(request.state, "request_id", None)
    return (
        trace if isinstance(trace, uuid.UUID) else current_trace_id(),
        rid if isinstance(rid, uuid.UUID) else current_request_id(),
    )


def bearer_token(request: Request) -> str:
    """Section 2.3 step 1. Anything that is not a non-empty bearer token is
    ``401 UNAUTHENTICATED`` -- including a well-formed ``Basic`` header, which
    is a client bug rather than a permission problem."""
    raw = request.headers.get("Authorization")
    if not raw:
        raise ApiError(ErrorCode.UNAUTHENTICATED, details={"reason": "MISSING_AUTHORIZATION"})
    scheme, _, rest = raw.partition(" ")
    token = rest.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError(ErrorCode.UNAUTHENTICATED, details={"reason": "MALFORMED_AUTHORIZATION"})
    return token


async def enforce_json_media_type(request: Request) -> None:
    """Section 1.2: ``application/json`` only.

    Mounted as a router dependency rather than as middleware so the refusal
    goes through the one error envelope. A body-bearing request whose content
    type is something else is ``415``, not a validation failure -- the server
    never guessed at the bytes.
    """
    if request.method not in {"POST", "PUT", "PATCH"}:
        return
    raw = request.headers.get("content-type")
    if not raw:
        return
    media = raw.split(";", 1)[0].strip().lower()
    if media and media != "application/json" and not media.endswith("+json"):
        raise ApiError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, details={"accepted": ["application/json"]})


async def _verified_claims(request: Request) -> TokenClaims:
    """Verify through whichever identity provider this deployment configured.

    ``config.provider`` is built once, in ``ApiConfig.__post_init__``, and is
    the same object ``GET /v1/version`` names -- so the provider that verifies
    a token and the provider the deployment discloses cannot be two different
    things.
    """
    config = api_config(request)
    deps = api_deps(request)
    return await config.provider.verify(
        bearer_token(request),
        jwks=deps.jwks,
        now=max(deps.clock().timestamp(), time.time()),
        leeway_seconds=config.clock_skew_seconds,
    )


# --------------------------------------------------------------------------
# The human surface
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HumanContext:
    """Everything a `/v1` handler is allowed to know about its caller."""

    principal: Principal
    record: UserRecord
    judge_mode: bool

    @property
    def scope(self) -> OwnerScope:
        return owner_scope(self.principal)


def owner_scope(principal: Principal) -> OwnerScope:
    """The **only** place a public-surface ``OwnerScope`` is constructed."""
    return OwnerScope.of(principal)


async def require_principal(request: Request) -> HumanContext:
    """Sections 2.3-2.5, in the order those sections give.

    The route-class check runs **before** the ``users`` lookup. A workload
    token has no ``sub`` at all, so resolving first would answer
    ``USER_NOT_PROVISIONED`` and hide the fact that the credential was of the
    wrong kind -- which is the distinction `G8.2` exists to prove.
    """
    config = api_config(request)
    deps = api_deps(request)
    claims = await _verified_claims(request)

    app_client = config.logical_client(claims.client_id) or UNKNOWN_CLIENT
    route_class_mod.route_class_check(RouteClass.PUBLIC, app_client)

    if str(OAuthScope.MEMORY_READ) not in claims.scopes:
        raise ApiError(
            ErrorCode.INSUFFICIENT_SCOPE,
            details={"required_scope": str(OAuthScope.MEMORY_READ)},
        )

    trace_id, request_id = request_ids(request)
    principal = await build_human_principal(
        bearer_token(request),
        config=config,
        jwks=deps.jwks,
        users=deps.users,
        trace_id=trace_id,
        request_id=request_id,
        now=deps.clock(),
    )
    record = await deps.users.by_cognito_sub(principal.cognito_sub)
    if record is None:  # pragma: no cover - build_human_principal already refused
        raise ApiError(ErrorCode.USER_NOT_PROVISIONED)

    return HumanContext(
        principal=principal,
        record=record,
        judge_mode=judge_mode_for(claims, config=config, allowlisted=record.judge_mode_allowlisted),
    )


# --------------------------------------------------------------------------
# The workload surface
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkloadContext:
    """A verified workload token that has passed the route-class check.

    It carries no ownership. Ownership arrives only with a capability.
    """

    claims: TokenClaims
    app_client: str


async def require_workload(request: Request) -> WorkloadContext:
    """Section 2.4 for `/internal/v1`.

    Mounted on the internal router itself, so a route added in a later phase
    inherits the check by construction rather than by the author remembering.
    """
    config = api_config(request)
    claims = await _verified_claims(request)
    app_client = config.logical_client(claims.client_id) or UNKNOWN_CLIENT
    route_class_mod.route_class_check(RouteClass.INTERNAL, app_client)
    return WorkloadContext(claims=claims, app_client=app_client)


def require_workload_scope(
    scope: OAuthScope,
) -> Callable[[WorkloadContext], Awaitable[WorkloadContext]]:
    """Refuse a missing scope **before** the body is validated.

    Section 9.8's `403` must not become a `422` just because the caller also
    sent a malformed body: a credential that was never granted the scope
    learns nothing about the schema behind it.
    """

    async def _dependency(
        workload: Annotated[WorkloadContext, Depends(require_workload)],
    ) -> WorkloadContext:
        if str(scope) not in workload.claims.scopes:
            raise ApiError(ErrorCode.INSUFFICIENT_SCOPE, details={"required_scope": str(scope)})
        return workload

    return _dependency


async def _resolve(
    request: Request,
    workload: WorkloadContext,
    *,
    kind: str,
    key: str | uuid.UUID,
    scope: OAuthScope,
    payload_user_id: uuid.UUID | None = None,
) -> InternalPrincipal:
    config = api_config(request)
    deps = api_deps(request)
    trace_id, request_id = request_ids(request)
    return await resolve_capability(
        deps.capabilities,
        kind=kind,
        key=key,
        app_client=workload.app_client,
        scopes=workload.claims.scopes,
        required_scope=scope,
        proof_header=request.headers.get("X-Provenance-Capability-Proof"),
        hmac_key=config.capability_hmac_key,
        now=deps.clock(),
        trace_id=trace_id,
        request_id=request_id,
        token_issued_at=datetime.fromtimestamp(workload.claims.issued_at, tz=UTC),
        token_expires_at=datetime.fromtimestamp(workload.claims.expires_at, tz=UTC),
        payload_user_id=payload_user_id,
    )


def require_capability(
    kind: str, path_param: str, scope: OAuthScope
) -> Callable[[Request, WorkloadContext], Awaitable[InternalPrincipal]]:
    """Resolve the capability named by a **path** parameter (sections 9.2-9.6,
    9.9-9.11).

    The id in the path is a lookup key, never an authority: the tenant and
    user come from the row it resolves to. Handing over another user's id
    therefore reaches that user's own memory and nothing else -- section 3.8.
    """

    async def _dependency(
        request: Request,
        workload: Annotated[WorkloadContext, Depends(require_workload)],
    ) -> InternalPrincipal:
        return await _resolve(
            request, workload, kind=kind, key=request.path_params[path_param], scope=scope
        )

    return _dependency


async def resolve_body_capability(
    request: Request,
    workload: WorkloadContext,
    *,
    kind: str,
    key: str | uuid.UUID,
    scope: OAuthScope,
    payload_user_id: uuid.UUID | None = None,
) -> InternalPrincipal:
    """Resolve the capability named in the **body** (sections 9.1, 9.7, 9.8).

    Three endpoints have no path parameter to carry the capability, so the id
    travels in the body. That does not make the body authoritative: the id is
    used to *select* the server-side row, and every ownership field still
    comes from that row. ``payload_user_id`` is section 3.6's tripwire and is
    compared here, before the proof, so that a proposal aimed at a run the
    caller does not hold is reported as the scope mismatch it is rather than
    as a proof failure.
    """
    return await _resolve(
        request, workload, kind=kind, key=key, scope=scope, payload_user_id=payload_user_id
    )


def query_params(request: Request) -> dict[str, list[str]]:
    """Query parameters as a repeatable mapping.

    Read from the raw request rather than declared per route because §5's
    failures are typed -- ``INVALID_PAGE_SIZE``, ``INVALID_CURSOR``,
    ``INVALID_QUERY_PARAMETER`` -- and FastAPI's own coercion would answer
    every one of them with ``VALIDATION_FAILED``.
    """
    out: dict[str, list[str]] = {}
    for name, value in request.query_params.multi_items():
        out.setdefault(name, []).append(value)
    return out


def one(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[0] if values else None


def as_uuid(params: dict[str, list[str]], name: str) -> uuid.UUID | None:
    raw = one(params, name)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": name, "reason": "NOT_A_UUID"},
        ) from exc


def as_bool(params: dict[str, list[str]], name: str, *, default: bool = False) -> bool:
    raw = one(params, name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise ApiError(
        ErrorCode.INVALID_QUERY_PARAMETER,
        details={"parameter": name, "allowed": ["true", "false"]},
    )


def as_int(params: dict[str, list[str]], name: str, *, default: int, low: int, high: int) -> int:
    raw = one(params, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": name, "min": low, "max": high},
        ) from exc
    if value < low or value > high:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": name, "min": low, "max": high},
        )
    return value


def as_vocabulary(
    params: dict[str, list[str]], name: str, allowed: tuple[str, ...]
) -> tuple[str, ...]:
    """A repeatable, closed-vocabulary filter.

    An unknown member is ``400 INVALID_QUERY_PARAMETER`` carrying the allowed
    set, rather than an empty result set that reads like "you have nothing".
    """
    values = tuple(params.get(name, ()))
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": name, "allowed": list(allowed), "received": unknown},
        )
    return values


def enum_values(enum: Any) -> tuple[str, ...]:
    return tuple(str(member) for member in enum)
