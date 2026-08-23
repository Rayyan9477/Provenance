"""Response construction: the page envelope, the idempotent replay, the 404.

Authority: ``specs/15_API_SPEC.md`` sections 1.5, 1.7, 5 and 6.

Every helper here exists because the alternative is a rule enforced by
reviewers. Section 1.7's rule -- a cross-scope read is *indistinguishable from
absence*, `404` and never `403` -- is one line of code per route, and one route
that writes `403` instead is a `B2` BLOCKER. So the route never chooses:
:func:`absent` is the only way a handler answers "no such row", and it takes
the typed code as its single argument.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.context import CASE_REVISION_HEADER
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.idempotency import (
    IdemDecision,
    begin_idempotent,
    request_hash,
    require_idempotency_key,
)
from services.control_plane.app.api.pagination import build_page, filter_fingerprint, parse_limit

__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "IDEMPOTENCY_REPLAYED_HEADER",
    "Idempotency",
    "Page",
    "absent",
    "case_revision_headers",
    "json_response",
    "page_envelope",
    "read_page",
]

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"


def json_response(
    payload: Any,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """One encoder for every body.

    ``jsonable_encoder`` is used rather than ``JSONResponse``'s raw
    ``json.dumps`` because the read ports return database projections holding
    ``datetime`` and ``UUID``. Money never passes through as a ``Decimal``:
    section 1.3 puts amounts on the wire as decimal *strings*, and the ports
    hand them over already in that form.
    """
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        headers=dict(headers or {}),
    )


def absent(code: ErrorCode) -> ApiError:
    """Section 1.7. The only way a handler says "no such row".

    Deliberately carries no ``details``: an identifier echoed back into the
    body of a `404` is a confirmation that the identifier is *shaped* like
    something real, and the adversarial lane asserts its absence.
    """
    return ApiError(code)


def case_revision_headers(revision: int | None) -> dict[str, str]:
    return {} if revision is None else {CASE_REVISION_HEADER: str(revision)}


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Page:
    """The decoded page request, plus the fingerprint it was minted under."""

    limit: int
    after: tuple[list[str], uuid.UUID] | None
    fingerprint: str


def read_page(
    params: Mapping[str, list[str]],
    *,
    collection: str,
    filters: Mapping[str, Any],
    config: ApiConfig,
) -> Page:
    """Section 5.1 and 5.3.

    The fingerprint covers the collection **and** the filters, so a cursor
    cannot be replayed against a different query shape or against a different
    endpoint. Both failures are ``400 INVALID_CURSOR`` with a reason, never a
    silent restart of the scan.
    """
    raw_limit = params.get("limit")
    limit = parse_limit(raw_limit[0] if raw_limit else None)
    fingerprint = filter_fingerprint(collection=collection, **dict(filters))
    raw_cursor = params.get("cursor")
    after = None
    if raw_cursor and raw_cursor[0]:
        after = decode(raw_cursor[0], fingerprint, config)
    return Page(limit=limit, after=after, fingerprint=fingerprint)


def decode(cursor: str, fingerprint: str, config: ApiConfig) -> tuple[list[str], uuid.UUID]:
    from services.control_plane.app.api.pagination import decode_cursor

    return decode_cursor(cursor, fingerprint, key=config.cursor_hmac_key)


def page_envelope(
    rows: Sequence[dict[str, Any]],
    has_more: bool,
    page: Page,
    *,
    id_field: str,
    sort_fields: Sequence[str],
    config: ApiConfig,
) -> dict[str, Any]:
    """Section 5.2. ``items`` and ``page``, and nothing else -- there is no
    ``total_count``, because computing one needs a second full scan and it is
    stale by the time it renders."""
    info = build_page(
        list(rows),
        limit=page.limit,
        has_more=has_more,
        id_field=id_field,
        sort_fields=list(sort_fields),
        fingerprint=page.fingerprint,
        key=config.cursor_hmac_key,
    )
    return {"items": list(rows), "page": info.model_dump()}


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


@dataclass
class Idempotency:
    """One request's lease on ``idempotency_records``.

    Section 6.5's decision table lives in
    :func:`~services.control_plane.app.api.idempotency.begin_idempotent`; this
    is the HTTP wrapper around it. The ``Idempotency-Replayed`` response header
    is what makes `G8.6` checkable from the outside: a retry that quietly
    re-executed and a retry that replayed a stored body are otherwise
    identical on the wire.
    """

    deps: Dependencies
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    scope: str
    key: str
    decision: IdemDecision

    @property
    def headers(self) -> dict[str, str]:
        return {
            IDEMPOTENCY_KEY_HEADER: self.key,
            IDEMPOTENCY_REPLAYED_HEADER: "true" if self.decision.replay else "false",
        }

    def replayed(self) -> JSONResponse | None:
        if self.decision.replay is None:
            return None
        status, body = self.decision.replay
        return json_response(body, status, headers=self.headers)

    async def complete(
        self, status: int, body: Mapping[str, Any], resource_id: uuid.UUID | None = None
    ) -> JSONResponse:
        await self.deps.idempotency.complete(
            self.tenant_id, self.scope, self.key, status, dict(body), resource_id
        )
        return json_response(body, status, headers=self.headers)

    async def failed(self) -> None:
        await self.deps.idempotency.fail(self.tenant_id, self.scope, self.key)


async def begin(
    request: Request,
    *,
    deps: Dependencies,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    scope: str,
    presented_key: str | None,
    trace_id: uuid.UUID,
) -> Idempotency:
    """Claim the lease, or hand back the stored response.

    The key is validated before anything else touches the store: section 6.2
    makes an absent key a `400`, and a request that never had a key must not
    take a lease under a fabricated one.
    """
    key = require_idempotency_key(presented_key)
    body = await request.body()
    digest = request_hash(
        request.method,
        request.url.path,
        list(request.query_params.multi_items()),
        body,
    )
    decision = await begin_idempotent(
        deps.idempotency, tenant_id, user_id, scope, key, digest, trace_id
    )
    return Idempotency(
        deps=deps,
        tenant_id=tenant_id,
        user_id=user_id,
        scope=scope,
        key=key,
        decision=decision,
    )
