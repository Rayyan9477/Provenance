"""The error envelope and the closed error-code catalogue.

Authority: ``specs/15_API_SPEC.md`` section 4.

Two things are deliberate here and both are load-bearing.

**The catalogue is an enum, not a set of string literals.** Section 4.1 says
clients branch on ``error.code`` and never on ``message``; T8.1's sub-task says
"a handler that constructs an error dict inline is how a code drifts from the
catalogue". :class:`ApiError` therefore refuses a code that is not an
:class:`ErrorCode` member, at construction, with a ``ValueError`` -- so drift
is a crash in a test rather than a novel string on a customer's screen.

**The envelope is built in one place.** :func:`install_error_handlers`
registers four handlers that between them cover every non-2xx path FastAPI can
take: a typed :class:`ApiError`, Pydantic's ``RequestValidationError``,
Starlette's ``HTTPException`` (405, 415, and anything the ASGI stack raises),
and the bare ``Exception`` that produces a 500. No route builds a body itself.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "DEFAULT_HTTP_STATUS",
    "DEFAULT_MESSAGE",
    "ApiError",
    "ErrorCode",
    "error_envelope",
    "install_error_handlers",
]

logger = logging.getLogger("provenance.api")


class ErrorCode(StrEnum):
    """Section 4.3, complete and closed."""

    # Request shape -- 4xx
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MALFORMED_JSON = "MALFORMED_JSON"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
    MALFORMED_IDEMPOTENCY_KEY = "MALFORMED_IDEMPOTENCY_KEY"
    INVALID_CURSOR = "INVALID_CURSOR"
    INVALID_PAGE_SIZE = "INVALID_PAGE_SIZE"
    INVALID_QUERY_PARAMETER = "INVALID_QUERY_PARAMETER"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

    # Authentication and authorisation
    UNAUTHENTICATED = "UNAUTHENTICATED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID_SIGNATURE = "TOKEN_INVALID_SIGNATURE"
    TOKEN_WRONG_ISSUER = "TOKEN_WRONG_ISSUER"
    USER_NOT_PROVISIONED = "USER_NOT_PROVISIONED"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    HUMAN_TOKEN_ON_INTERNAL_ROUTE = "HUMAN_TOKEN_ON_INTERNAL_ROUTE"
    WORKLOAD_TOKEN_ON_PUBLIC_ROUTE = "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE"
    CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
    CAPABILITY_CONSUMED = "CAPABILITY_CONSUMED"
    CAPABILITY_REVOKED = "CAPABILITY_REVOKED"
    CAPABILITY_SCOPE_MISMATCH = "CAPABILITY_SCOPE_MISMATCH"
    CAPABILITY_PROOF_INVALID = "CAPABILITY_PROOF_INVALID"
    JUDGE_MODE_DISABLED = "JUDGE_MODE_DISABLED"
    FORBIDDEN = "FORBIDDEN"

    # Not found -- 404
    NOT_FOUND = "NOT_FOUND"
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    RELATIONSHIP_NOT_FOUND = "RELATIONSHIP_NOT_FOUND"
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    BELIEF_NOT_FOUND = "BELIEF_NOT_FOUND"
    COMMITMENT_NOT_FOUND = "COMMITMENT_NOT_FOUND"
    CONFLICT_NOT_FOUND = "CONFLICT_NOT_FOUND"
    ACTION_INTENT_NOT_FOUND = "ACTION_INTENT_NOT_FOUND"
    TRIGGER_NOT_FOUND = "TRIGGER_NOT_FOUND"
    AGENT_RUN_NOT_FOUND = "AGENT_RUN_NOT_FOUND"
    TRACE_NOT_FOUND = "TRACE_NOT_FOUND"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    COUNTERFACTUAL_NOT_FOUND = "COUNTERFACTUAL_NOT_FOUND"
    INGEST_ALIAS_NOT_FOUND = "INGEST_ALIAS_NOT_FOUND"

    # Conflict -- 409
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    ACTION_STALE = "ACTION_STALE"
    ACTION_NOT_APPROVABLE = "ACTION_NOT_APPROVABLE"
    ACTION_ALREADY_EXECUTED = "ACTION_ALREADY_EXECUTED"
    ACTION_DRAFT_FROZEN = "ACTION_DRAFT_FROZEN"
    ARTIFACT_ALREADY_COMPLETED = "ARTIFACT_ALREADY_COMPLETED"
    CASE_TRANSITION_ILLEGAL = "CASE_TRANSITION_ILLEGAL"
    TRIGGER_NOT_ARMED = "TRIGGER_NOT_ARMED"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    INGEST_ALIAS_DISABLED = "INGEST_ALIAS_DISABLED"
    # G9.6. Distinct from ACTION_STALE on purpose: ACTION_STALE means the basis
    # MOVED between approval and execution, NO_COMMITTED_BASIS means there was
    # never a committed one to move. The first is a race a caller can retry
    # after re-reading; the second is invariant 4 -- an external effect
    # attempted from uncommitted state -- and retrying it is exactly wrong.
    NO_COMMITTED_BASIS = "NO_COMMITTED_BASIS"

    # Semantic rejection -- 422
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    ARTIFACT_OBJECT_MISSING = "ARTIFACT_OBJECT_MISSING"
    ARTIFACT_SIZE_MISMATCH = "ARTIFACT_SIZE_MISMATCH"
    UNSUPPORTED_MIME_TYPE = "UNSUPPORTED_MIME_TYPE"
    PROPOSAL_SCHEMA_INVALID = "PROPOSAL_SCHEMA_INVALID"
    PROPOSAL_FOREIGN_PROVENANCE = "PROPOSAL_FOREIGN_PROVENANCE"
    PROPOSAL_INVARIANT_VIOLATION = "PROPOSAL_INVARIANT_VIOLATION"
    PROPOSAL_UNGROUNDED_BELIEF = "PROPOSAL_UNGROUNDED_BELIEF"
    DRAFT_UNSUPPORTED_CLAIM = "DRAFT_UNSUPPORTED_CLAIM"
    RECIPIENT_NOT_ALLOWED = "RECIPIENT_NOT_ALLOWED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CORRECTION_TARGET_INVALID = "CORRECTION_TARGET_INVALID"

    # Throttling and server -- 429/5xx
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    #: The capability does not exist yet -- distinct from a fault. Every
    #: unbound port method raises `NotImplementedError` on purpose, and the
    #: register in `adapters/unbound.py` names the subsystem it waits on.
    #: Mapping that to 500 told a reader the server broke; it had not.
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    RETRYABLE_CONCURRENCY = "RETRYABLE_CONCURRENCY"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"


#: Every code that ends in ``_NOT_FOUND``, plus the bare ``NOT_FOUND``.
_NOT_FOUND_CODES: Final[frozenset[ErrorCode]] = frozenset(
    code for code in ErrorCode if code.value.endswith("NOT_FOUND")
)

DEFAULT_HTTP_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.MALFORMED_JSON: 400,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.MISSING_IDEMPOTENCY_KEY: 400,
    ErrorCode.MALFORMED_IDEMPOTENCY_KEY: 400,
    ErrorCode.INVALID_CURSOR: 400,
    ErrorCode.INVALID_PAGE_SIZE: 400,
    ErrorCode.INVALID_QUERY_PARAMETER: 400,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.TOKEN_INVALID_SIGNATURE: 401,
    ErrorCode.TOKEN_WRONG_ISSUER: 401,
    ErrorCode.USER_NOT_PROVISIONED: 403,
    ErrorCode.INSUFFICIENT_SCOPE: 403,
    ErrorCode.HUMAN_TOKEN_ON_INTERNAL_ROUTE: 403,
    ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE: 403,
    ErrorCode.CAPABILITY_EXPIRED: 403,
    ErrorCode.CAPABILITY_CONSUMED: 403,
    ErrorCode.CAPABILITY_REVOKED: 403,
    ErrorCode.CAPABILITY_SCOPE_MISMATCH: 403,
    ErrorCode.CAPABILITY_PROOF_INVALID: 403,
    ErrorCode.JUDGE_MODE_DISABLED: 403,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: 409,
    ErrorCode.ACTION_STALE: 409,
    ErrorCode.ACTION_NOT_APPROVABLE: 409,
    ErrorCode.ACTION_ALREADY_EXECUTED: 409,
    ErrorCode.ACTION_DRAFT_FROZEN: 409,
    ErrorCode.ARTIFACT_ALREADY_COMPLETED: 409,
    ErrorCode.CASE_TRANSITION_ILLEGAL: 409,
    ErrorCode.TRIGGER_NOT_ARMED: 409,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.NO_COMMITTED_BASIS: 409,
    ErrorCode.INGEST_ALIAS_DISABLED: 409,
    ErrorCode.ARTIFACT_HASH_MISMATCH: 422,
    ErrorCode.ARTIFACT_OBJECT_MISSING: 422,
    ErrorCode.ARTIFACT_SIZE_MISMATCH: 422,
    ErrorCode.UNSUPPORTED_MIME_TYPE: 422,
    ErrorCode.PROPOSAL_SCHEMA_INVALID: 422,
    ErrorCode.PROPOSAL_FOREIGN_PROVENANCE: 422,
    ErrorCode.PROPOSAL_INVARIANT_VIOLATION: 422,
    ErrorCode.PROPOSAL_UNGROUNDED_BELIEF: 422,
    ErrorCode.DRAFT_UNSUPPORTED_CLAIM: 422,
    ErrorCode.RECIPIENT_NOT_ALLOWED: 422,
    ErrorCode.CURRENCY_MISMATCH: 422,
    ErrorCode.CORRECTION_TARGET_INVALID: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.RETRYABLE_CONCURRENCY: 503,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.DEPENDENCY_TIMEOUT: 504,
    **{code: 404 for code in _NOT_FOUND_CODES},
}

#: Safe, user-presentable English, at most 300 characters. Section 4.1 forbids
#: SQL, stack traces, table names, hostnames, prompts and artifact content
#: here, which is why the text is a constant per code rather than an f-string
#: interpolating whatever the caller happened to be holding.
DEFAULT_MESSAGE: Final[dict[ErrorCode, str]] = {
    ErrorCode.VALIDATION_FAILED: "The request body failed validation.",
    ErrorCode.MALFORMED_JSON: "The request body is not valid JSON.",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "This endpoint accepts application/json only.",
    ErrorCode.METHOD_NOT_ALLOWED: "That method is not allowed on this path.",
    ErrorCode.MISSING_IDEMPOTENCY_KEY: "This request requires an Idempotency-Key header.",
    ErrorCode.MALFORMED_IDEMPOTENCY_KEY: "The Idempotency-Key header is malformed.",
    ErrorCode.INVALID_CURSOR: "That page cursor is not valid for this query.",
    ErrorCode.INVALID_PAGE_SIZE: "The page size is outside the permitted range.",
    ErrorCode.INVALID_QUERY_PARAMETER: "A query parameter has an unsupported value.",
    ErrorCode.PAYLOAD_TOO_LARGE: "That upload is larger than the permitted maximum.",
    ErrorCode.UNAUTHENTICATED: "Sign in to continue.",
    ErrorCode.TOKEN_EXPIRED: "Your session expired. Sign in again.",
    ErrorCode.TOKEN_INVALID_SIGNATURE: "That access token could not be verified.",
    ErrorCode.TOKEN_WRONG_ISSUER: "That access token was issued by an unexpected authority.",
    ErrorCode.USER_NOT_PROVISIONED: "This account is not provisioned for Provenance.",
    ErrorCode.INSUFFICIENT_SCOPE: "This credential does not carry the required scope.",
    ErrorCode.HUMAN_TOKEN_ON_INTERNAL_ROUTE: "This route is not available to browser sessions.",
    ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE: "This route is not available to workload clients.",
    ErrorCode.CAPABILITY_EXPIRED: "That authorisation has expired.",
    ErrorCode.CAPABILITY_CONSUMED: "That authorisation has already been used.",
    ErrorCode.CAPABILITY_REVOKED: "That authorisation has been revoked.",
    ErrorCode.CAPABILITY_SCOPE_MISMATCH: "That request falls outside the granted authorisation.",
    ErrorCode.CAPABILITY_PROOF_INVALID: "The capability proof did not verify.",
    ErrorCode.JUDGE_MODE_DISABLED: "Judge Mode is not enabled for this account.",
    ErrorCode.FORBIDDEN: "That operation is not permitted.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.CASE_NOT_FOUND: "No such case.",
    ErrorCode.RELATIONSHIP_NOT_FOUND: "No such relationship.",
    ErrorCode.CONTEXT_NOT_FOUND: "No such context.",
    ErrorCode.ARTIFACT_NOT_FOUND: "No such artifact.",
    ErrorCode.BELIEF_NOT_FOUND: "No such belief.",
    ErrorCode.COMMITMENT_NOT_FOUND: "No such commitment.",
    ErrorCode.CONFLICT_NOT_FOUND: "No such conflict.",
    ErrorCode.ACTION_INTENT_NOT_FOUND: "No such action intent.",
    ErrorCode.TRIGGER_NOT_FOUND: "No such trigger.",
    ErrorCode.AGENT_RUN_NOT_FOUND: "No such agent run.",
    ErrorCode.TRACE_NOT_FOUND: "No such trace.",
    ErrorCode.EVIDENCE_NOT_FOUND: "No such evidence item.",
    ErrorCode.COUNTERFACTUAL_NOT_FOUND: "No such counterfactual run.",
    ErrorCode.INGEST_ALIAS_NOT_FOUND: "No such ingest alias.",
    ErrorCode.IDEMPOTENCY_CONFLICT: (
        "This idempotency key was already used with a different request body."
    ),
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: "An identical request is already in flight.",
    ErrorCode.ACTION_STALE: (
        "This case changed after the draft was prepared. "
        "Review the updated state before approving."
    ),
    ErrorCode.ACTION_NOT_APPROVABLE: "This action is not in a state that can be approved.",
    ErrorCode.ACTION_ALREADY_EXECUTED: "This action has already been executed.",
    ErrorCode.ACTION_DRAFT_FROZEN: "An approved draft cannot be edited.",
    ErrorCode.ARTIFACT_ALREADY_COMPLETED: "This artifact has already been completed.",
    ErrorCode.CASE_TRANSITION_ILLEGAL: "That case transition is not legal from the current state.",
    ErrorCode.TRIGGER_NOT_ARMED: "That trigger is not armed.",
    ErrorCode.REVISION_CONFLICT: "This case changed since you loaded it. Reload and try again.",
    ErrorCode.NO_COMMITTED_BASIS: (
        "This action has no committed state behind it, so it was not sent."
    ),
    ErrorCode.INGEST_ALIAS_DISABLED: "That forwarding address has been disabled.",
    ErrorCode.ARTIFACT_HASH_MISMATCH: "The uploaded bytes do not match the declared hash.",
    ErrorCode.ARTIFACT_OBJECT_MISSING: "The uploaded object was not found. Upload it and retry.",
    ErrorCode.ARTIFACT_SIZE_MISMATCH: "The uploaded bytes do not match the declared size.",
    ErrorCode.UNSUPPORTED_MIME_TYPE: "That file type is not supported.",
    ErrorCode.PROPOSAL_SCHEMA_INVALID: "The proposal does not match the expected schema.",
    ErrorCode.PROPOSAL_FOREIGN_PROVENANCE: "The proposal references records that do not resolve.",
    ErrorCode.PROPOSAL_INVARIANT_VIOLATION: "The proposal would violate a memory invariant.",
    ErrorCode.PROPOSAL_UNGROUNDED_BELIEF: "The proposal would create a belief with no support.",
    ErrorCode.DRAFT_UNSUPPORTED_CLAIM: "The draft asserts something the record does not support.",
    ErrorCode.RECIPIENT_NOT_ALLOWED: "That recipient is not on the allowlist.",
    ErrorCode.CURRENCY_MISMATCH: "Amounts in different currencies cannot be combined.",
    ErrorCode.CORRECTION_TARGET_INVALID: "That correction type does not match the target given.",
    ErrorCode.RATE_LIMITED: "Too many requests. Try again in a few seconds.",
    ErrorCode.QUOTA_EXCEEDED: "You have reached a usage limit for now.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong on our side. Nothing was committed.",
    ErrorCode.NOT_IMPLEMENTED: (
        "This capability is not built yet. Nothing failed and nothing was attempted."
    ),
    ErrorCode.RETRYABLE_CONCURRENCY: "The record was busy. Retry the identical request.",
    ErrorCode.UPSTREAM_UNAVAILABLE: "A dependency is unavailable. Your data is unchanged.",
    ErrorCode.DEPENDENCY_TIMEOUT: "A dependency took too long to answer.",
}

_MESSAGE_MAX = 300


def _fit(message: str, limit: int = _MESSAGE_MAX) -> str:
    """Bring ``message`` under ``limit`` without cutting a word in half.

    Section 4.1 caps a presentable message at 300 characters and that cap is
    right: this envelope is public and an unbounded string is how a stack trace
    escapes. But the cap used to be a bare slice, and a bare slice lands
    wherever it lands. ``read.get_trace``'s message is 335 characters, so Judge
    Mode -- the screen built for the people evaluating this -- rendered
    "... Needs app/ob" and stopped. A sentence severed mid-word reads as a
    broken product, which is the precise opposite of what an honest 501 is for.

    So the cut steps back to the last space and marks the elision. The reader
    sees a truncated sentence and knows it was truncated, rather than seeing a
    mangled one and wondering what else here is half-built.
    """
    if len(message) <= limit:
        return message
    head = message[: limit - 1]
    boundary = head.rfind(" ")
    if boundary <= 0:
        return head + "…"
    return head[:boundary].rstrip(" ,;:.-") + "…"


#: Codes whose 4xx/5xx response sets ``Retry-After`` (sections 1.5 and 4.3).
_DEFAULT_HEADERS: Final[dict[ErrorCode, dict[str, str]]] = {
    ErrorCode.RETRYABLE_CONCURRENCY: {"Retry-After": "1"},
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: {"Retry-After": "2"},
    ErrorCode.UPSTREAM_UNAVAILABLE: {"Retry-After": "5"},
}


class ApiError(Exception):
    """A typed, enveloped API failure.

    Constructing one with a code outside :class:`ErrorCode` raises
    ``ValueError`` immediately: the catalogue is closed, and a handler that
    invented a code would otherwise ship a string no client can branch on.
    """

    __slots__ = ("code", "details", "headers", "http_status", "message")

    def __init__(
        self,
        code: ErrorCode | str,
        http_status: int | None = None,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        try:
            self.code: ErrorCode = ErrorCode(code)
        except ValueError as exc:
            raise ValueError(
                f"{code!r} is not in the error catalogue of specs/15_API_SPEC.md section 4.3; "
                "add it to the spec and to ErrorCode in the same change, or use an existing code"
            ) from exc
        self.http_status: int = http_status or DEFAULT_HTTP_STATUS[self.code]
        self.message: str = _fit(message or DEFAULT_MESSAGE[self.code])
        self.details: dict[str, Any] = dict(details or {})
        self.headers: dict[str, str] = {
            **_DEFAULT_HEADERS.get(self.code, {}),
            **(headers or {}),
        }
        super().__init__(f"{self.code}: {self.message}")


def error_envelope(
    code: ErrorCode | str,
    message: str,
    trace_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The section 4.1 body, and the only place it is constructed."""
    return {
        "error": {
            "code": str(ErrorCode(code)),
            "message": message[:_MESSAGE_MAX],
            "trace_id": str(trace_id),
            "details": details if details is not None else {},
        }
    }


def _trace_of(request: Request) -> uuid.UUID:
    """The request's trace id, or a fresh one.

    The fallback matters: `G8.7` asserts the header on a failure, and a
    failure early enough to precede the context middleware must still carry
    one rather than crash looking for it.
    """
    from services.control_plane.app.api.context import current_trace_id

    candidate = getattr(request.state, "trace_id", None)
    if isinstance(candidate, uuid.UUID):
        return candidate
    return current_trace_id()


def _headers_for(request: Request, extra: dict[str, str] | None = None) -> dict[str, str]:
    from services.control_plane.app.api.context import (
        REQUEST_ID_HEADER,
        TRACE_ID_HEADER,
        current_request_id,
    )

    request_id = getattr(request.state, "request_id", None)
    return {
        TRACE_ID_HEADER: str(_trace_of(request)),
        REQUEST_ID_HEADER: str(request_id if request_id is not None else current_request_id()),
        "Cache-Control": "no-store",
        **(extra or {}),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Replace FastAPI's ``{"detail": ...}`` shape globally."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            headers=_headers_for(request, exc.headers),
            content=error_envelope(exc.code, exc.message, _trace_of(request), exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        raw = exc.errors()
        # A body that is not JSON at all surfaces here as a `json_invalid`
        # error. Section 4.3 gives that its own 400 code, so it is separated
        # rather than folded into the 422.
        if raw and raw[0].get("type") in {"json_invalid", "value_error.jsondecode"}:
            position = raw[0].get("ctx", {}).get("error", "")
            return JSONResponse(
                status_code=400,
                headers=_headers_for(request),
                content=error_envelope(
                    ErrorCode.MALFORMED_JSON,
                    DEFAULT_MESSAGE[ErrorCode.MALFORMED_JSON],
                    _trace_of(request),
                    {"position": str(position)},
                ),
            )
        fields = [
            {
                "loc": ".".join(str(part) for part in item["loc"][1:]),
                "reason": item["type"],
                "message": item["msg"],
            }
            for item in raw[:20]
        ]
        return JSONResponse(
            status_code=422,
            headers=_headers_for(request),
            content=error_envelope(
                ErrorCode.VALIDATION_FAILED,
                DEFAULT_MESSAGE[ErrorCode.VALIDATION_FAILED],
                _trace_of(request),
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHENTICATED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
            413: ErrorCode.PAYLOAD_TOO_LARGE,
            415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return JSONResponse(
            status_code=exc.status_code,
            headers=_headers_for(request, dict(exc.headers or {})),
            content=error_envelope(code, DEFAULT_MESSAGE[code], _trace_of(request)),
        )

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
        """A method that has never been written is not a fault.

        Every unbound port raises this deliberately -- `adapters/unbound.py`
        argues at length that a `None` or `[]` would be worse, because an empty
        list reads as "no conflicts on this case" rather than as "not wired".
        The catch-all then flattened it to `500 INTERNAL_ERROR` with the message
        "Something went wrong on our side", and Judge Mode showed a reader
        `GET /v1/traces/... returned 500 INTERNAL_ERROR`.

        That is `D-00-005` one layer up: `CANNOT RUN` reported as `FAIL`. It is
        also misleading in detail -- "nothing was committed" implies a write was
        attempted and rolled back, and for an unbound read nothing was attempted.

        The exception text already carries the register's sentence, which names
        the subsystem in prose written for a person. It is passed through rather
        than replaced with the generic message: §4.1 forbids SQL, stack traces,
        table names and hostnames in a client-facing message, and this text is a
        curated constant containing none of them.
        """
        trace_id = _trace_of(request)
        logger.info(
            "unbound_capability_requested",
            extra={"trace_id": str(trace_id), "detail": str(exc)},
        )
        return JSONResponse(
            status_code=DEFAULT_HTTP_STATUS[ErrorCode.NOT_IMPLEMENTED],
            headers=_headers_for(request),
            content=error_envelope(
                ErrorCode.NOT_IMPLEMENTED,
                str(exc) or DEFAULT_MESSAGE[ErrorCode.NOT_IMPLEMENTED],
                trace_id,
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        trace_id = _trace_of(request)
        # `exc_info=exc` rather than `logger.exception`'s implicit lookup: the
        # handler is reached through Starlette's error middleware, and on some
        # paths `sys.exc_info()` has already been cleared by the time it runs.
        # Passing the exception the framework handed us cannot go stale.
        logger.error("unhandled_api_error", exc_info=exc, extra={"trace_id": str(trace_id)})
        return JSONResponse(
            status_code=500,
            headers=_headers_for(request),
            content=error_envelope(
                ErrorCode.INTERNAL_ERROR,
                DEFAULT_MESSAGE[ErrorCode.INTERNAL_ERROR],
                trace_id,
            ),
        )
