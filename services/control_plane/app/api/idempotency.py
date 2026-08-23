"""Idempotency over ``idempotency_records``.

Authority: ``specs/15_API_SPEC.md`` section 6; ``23_PHASE_GATES.md`` sections
14 and 23.10.

Section 6.5's decision table is implemented literally, including the ordering
rule that is easy to get wrong: **the hash check precedes the status check in
every row.** A mismatched body is always a `409`, even against a dead lease.
The key identifies the *intent*; a different body under the same key is never
a legitimate continuation of it.

Section 6.3's primary key is ``(tenant_id, scope, key)``, not ``(scope, key)``.
With a global unique constraint, a client that guessed another tenant's key
string would cause a cross-tenant `409` -- both an availability problem and a
one-bit oracle for key existence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol
from urllib.parse import urlencode

from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = [
    "IDEMPOTENCY_KEY_PATTERN",
    "IDEMPOTENCY_SCOPES",
    "IdemDecision",
    "IdemStatus",
    "IdempotencyRecord",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "begin_idempotent",
    "jcs_canonicalize",
    "request_hash",
    "require_idempotency_key",
]

IDEMPOTENCY_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._~-]{16,255}$")

LEASE_SECONDS: Final[int] = 30
RECORD_TTL_HOURS: Final[int] = 24

#: Section 6.2, verbatim: the endpoints that require a key, and the scope
#: string each one records under. The keys are ``(method, path_template)`` so
#: the table can be diffed against the router rather than trusted.
IDEMPOTENCY_SCOPES: Final[dict[tuple[str, str], str]] = {
    ("POST", "/v1/artifacts/upload-intent"): "artifact.upload_intent",
    ("POST", "/v1/artifacts/{artifact_id}/complete"): "artifact.complete",
    ("POST", "/v1/cases/{case_id}/corrections"): "case.correction",
    ("PUT", "/v1/action-intents/{action_intent_id}/draft"): "action.draft_update",
    ("POST", "/v1/action-intents/{action_intent_id}/approve"): "action.approve",
    ("POST", "/v1/action-intents/{action_intent_id}/reject"): "action.reject",
    ("POST", "/v1/ingest-alias/rotate"): "ingest_alias.rotate",
    ("POST", "/v1/judge-mode/counterfactual"): "judge.counterfactual",
    ("POST", "/internal/v1/ingest/artifacts"): "internal.ingest.artifact",
    ("POST", "/internal/v1/agent-runs/{agent_run_id}/evidence"): "internal.evidence.register",
    ("POST", "/internal/v1/memory/proposals"): "internal.memory.proposal",
    ("POST", "/internal/v1/advocacy/action-intents"): "internal.advocacy.intent",
    ("POST", "/internal/v1/triggers/{trigger_id}/evaluate"): "internal.trigger.evaluate",
    ("POST", "/internal/v1/actions/{action_intent_id}/execute"): "internal.action.execute",
    ("POST", "/internal/v1/agent-runs/{agent_run_id}/complete"): "internal.agent_run.complete",
}
# Section 6.2's table lists eighteen rows, of which **fifteen** are
# `Required: yes`; the other three -- `/internal/v1/events/deliveries`,
# `/internal/v1/events/outbox/sweep` and `/internal/v1/agent-runs/{id}/retrieval`
# -- say no, and say why (`event_id` is the ledger; the claim/lease state
# machine is the control; retrieval is read-only). This map is those fifteen.
#
# An earlier draft of this file carried two more entries,
# `POST /v1/judge/triggers/{trigger_id}/wake` and `POST /v1/judge-mode/probes`,
# reasoning that judge-mode probes are state-changing in the same sense as a
# counterfactual. Neither path exists in section 8.0's index of 31 public
# routes, and `tests/api/test_openapi_surface.py` fails a route that is
# implemented but undocumented as loudly as one that is documented and
# missing. Extrapolating a keyed endpoint into the map created a scope string
# for a route that can never be built, so the two were removed rather than
# given routes the spec does not have.


class IdemStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def jcs_canonicalize(body: bytes) -> bytes:
    """RFC 8785 canonicalisation, to the depth this API needs.

    Key ordering and insignificant whitespace between a first attempt and a
    retry must not manufacture a false ``IDEMPOTENCY_CONFLICT``. A body that
    is not JSON is hashed as-is: canonicalising something with no canonical
    form would be worse than hashing the bytes.
    """
    if not body:
        return b""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    return json.dumps(
        parsed, separators=(",", ":"), sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def request_hash(method: str, path: str, query: list[tuple[str, str]], body: bytes) -> bytes:
    """Section 6.4.

    ``Authorization``, ``X-Provenance-Trace-Id`` and ``X-Provenance-Request-Id``
    are deliberately excluded: a retry after a token refresh is the same
    logical request.
    """
    material = b"\n".join(
        [
            method.upper().encode(),
            path.encode(),
            urlencode(sorted(query)).encode(),
            jcs_canonicalize(body),
        ]
    )
    return hashlib.sha256(material).digest()


def require_idempotency_key(raw: str | None) -> str:
    if raw is None or raw == "":
        raise ApiError(ErrorCode.MISSING_IDEMPOTENCY_KEY, details={"header": "Idempotency-Key"})
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(raw) is None:
        raise ApiError(
            ErrorCode.MALFORMED_IDEMPOTENCY_KEY,
            details={"pattern": IDEMPOTENCY_KEY_PATTERN.pattern},
        )
    return raw


@dataclass(frozen=True, slots=True)
class IdemDecision:
    """Either "you hold the lease, execute" or "here is the stored result"."""

    execute: bool
    replay: tuple[int, dict[str, Any]] | None = None

    @classmethod
    def replay_of(cls, status: int, body: dict[str, Any]) -> IdemDecision:
        return cls(execute=False, replay=(status, body))


@dataclass
class IdempotencyRecord:
    tenant_id: uuid.UUID
    scope: str
    key: str
    user_id: uuid.UUID
    request_sha256: bytes
    status: IdemStatus
    trace_id: uuid.UUID
    lease_expires_at: datetime
    created_at: datetime
    expires_at: datetime
    response_status: int | None = None
    response_body: dict[str, Any] | None = None
    resource_id: uuid.UUID | None = None
    completed_at: datetime | None = None


class IdempotencyStore(Protocol):
    """The ledger. The Postgres implementation is a thin translation of the
    same four operations onto section 6.6's SQL."""

    async def get(self, tenant_id: uuid.UUID, scope: str, key: str) -> IdempotencyRecord | None: ...

    async def claim(self, record: IdempotencyRecord) -> bool:
        """Insert or take over the lease. ``False`` means somebody else holds it."""

    async def complete(
        self,
        tenant_id: uuid.UUID,
        scope: str,
        key: str,
        status: int,
        body: dict[str, Any],
        resource_id: uuid.UUID | None,
    ) -> None: ...

    async def fail(self, tenant_id: uuid.UUID, scope: str, key: str) -> None: ...


@dataclass
class InMemoryIdempotencyStore:
    """Process-local ledger.

    Used by the hermetic suites, and by a single-instance local ``make
    run-api``. It is **not** the production store: section 6.3's table is,
    because a lease that lives in one process cannot serialise two App Runner
    instances.
    """

    rows: dict[tuple[uuid.UUID, str, str], IdempotencyRecord] = field(default_factory=dict)
    now: Any = _utcnow

    async def get(self, tenant_id: uuid.UUID, scope: str, key: str) -> IdempotencyRecord | None:
        return self.rows.get((tenant_id, scope, key))

    async def claim(self, record: IdempotencyRecord) -> bool:
        pk = (record.tenant_id, record.scope, record.key)
        existing = self.rows.get(pk)
        if existing is None:
            self.rows[pk] = record
            return True
        if not hmac.compare_digest(existing.request_sha256, record.request_sha256):
            return False
        takeable = existing.status is IdemStatus.FAILED or (
            existing.status is IdemStatus.IN_PROGRESS and existing.lease_expires_at < self.now()
        )
        if not takeable:
            return False
        existing.status = IdemStatus.IN_PROGRESS
        existing.trace_id = record.trace_id
        existing.lease_expires_at = record.lease_expires_at
        return True

    async def complete(
        self,
        tenant_id: uuid.UUID,
        scope: str,
        key: str,
        status: int,
        body: dict[str, Any],
        resource_id: uuid.UUID | None,
    ) -> None:
        row = self.rows[(tenant_id, scope, key)]
        row.status = IdemStatus.COMPLETED
        row.response_status = status
        row.response_body = body
        row.resource_id = resource_id
        row.completed_at = self.now()

    async def fail(self, tenant_id: uuid.UUID, scope: str, key: str) -> None:
        self.rows[(tenant_id, scope, key)].status = IdemStatus.FAILED

    # -- test affordance, not part of the protocol -------------------------
    def expire_lease(self, tenant_id: uuid.UUID, scope: str, key: str) -> None:
        self.rows[(tenant_id, scope, key)].lease_expires_at = self.now() - timedelta(seconds=1)


async def begin_idempotent(
    store: IdempotencyStore,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    scope: str,
    key: str,
    req_hash: bytes,
    trace_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> IdemDecision:
    """Section 6.6, with 6.5's decision table as the contract."""
    moment = now or _utcnow()
    claimed = await store.claim(
        IdempotencyRecord(
            tenant_id=tenant_id,
            scope=scope,
            key=key,
            user_id=user_id,
            request_sha256=req_hash,
            status=IdemStatus.IN_PROGRESS,
            trace_id=trace_id,
            lease_expires_at=moment + timedelta(seconds=LEASE_SECONDS),
            created_at=moment,
            expires_at=moment + timedelta(hours=RECORD_TTL_HOURS),
        )
    )
    if claimed:
        return IdemDecision(execute=True)

    existing = await store.get(tenant_id, scope, key)
    if existing is None:
        # Raced with the GC job. Retrying the identical request is correct.
        raise ApiError(ErrorCode.RETRYABLE_CONCURRENCY)

    # The hash check precedes the status check. Always.
    if not hmac.compare_digest(bytes(existing.request_sha256), req_hash):
        raise ApiError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            details={
                "scope": scope,
                "key": key,
                "first_seen_at": existing.created_at.isoformat(),
            },
        )

    if existing.status is IdemStatus.COMPLETED:
        return IdemDecision.replay_of(existing.response_status or 200, existing.response_body or {})

    raise ApiError(
        ErrorCode.IDEMPOTENCY_IN_PROGRESS,
        details={"scope": scope, "key": key, "retry_after_seconds": 2},
    )
