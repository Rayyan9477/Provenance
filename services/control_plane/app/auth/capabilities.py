"""Capability resolution: the server-side answer to "acting for whom?".

Authority: ``specs/15_API_SPEC.md`` section 3 in full;
``specs/11_CONTRACTS.md`` section 7 for the type.

The rule (section 3.2)
----------------------
    "Every ``/internal/v1`` endpoint takes a capability object id in the URL
    path. The backend resolves ``tenant_id`` and ``user_id`` from the
    server-side bound record. No internal endpoint reads identity from the
    request body, ever."

Recorded conflict, reported and **not** resolved here
------------------------------------------------------
The two owning documents disagree about the capability-kind vocabulary:

* ``15_API_SPEC.md`` section 3.4: ``AGENT_RUN | TRIGGER | ACTION_INTENT |
  ARTIFACT | INGEST_ALIAS``, on a ``@dataclass`` with flat ``tenant_id`` /
  ``user_id`` fields in ``principal.py``.
* ``11_CONTRACTS.md`` section 7: ``AGENT_RUN | ACTION_INTENT |
  TRIGGER_EVALUATION | INGEST_JOB``, on a pydantic model in ``identity.py``
  with **no** flat ownership fields.

T1.5 implemented section 7 and it is green, so section 7's vocabulary is used
here. Two kinds are the same object under a different name -- ``TRIGGER`` is
``TRIGGER_EVALUATION`` (``prospective_triggers``) and ``INGEST_ALIAS`` is
``INGEST_JOB`` (``ingest_aliases``). The fifth section-3.4 kind, ``ARTIFACT``,
has **no** section-7 counterpart and none was invented: section 9.0's index
shows that no ``/internal/v1`` route presents an ``ARTIFACT`` capability, so
nothing needed it. Whichever vocabulary wins must be the same string in both
documents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Protocol

from provenance_contracts.identity import CapabilityBinding, InternalPrincipal
from provenance_domain.enums import OAuthScope, WorkloadKind
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth.capability_proof import verify_capability_proof

__all__ = [
    "CAPABILITY_KINDS",
    "CLIENT_CAPABILITY_MATRIX",
    "NOT_FOUND_CODE_BY_KIND",
    "CapabilityRecord",
    "CapabilityStore",
    "assert_within_capability",
    "resolve_capability",
    "workload_kind_for",
]

CapabilityKind = str

#: Section 7's four kinds, which are the ones T1.5 implemented.
CAPABILITY_KINDS: Final[tuple[str, ...]] = (
    "AGENT_RUN",
    "ACTION_INTENT",
    "TRIGGER_EVALUATION",
    "INGEST_JOB",
)

#: Section 3.4's second half, and the one that is easy to forget: the agent
#: runtime may only ever present an ``AGENT_RUN``. Even if an
#: ``action_intent_id`` leaked into agent state, the agent's token cannot use
#: it to reach the executor.
CLIENT_CAPABILITY_MATRIX: Final[dict[str, frozenset[str]]] = {
    "provenance-agent-runtime": frozenset({"AGENT_RUN"}),
    "provenance-workers": frozenset({"TRIGGER_EVALUATION", "ACTION_INTENT", "INGEST_JOB"}),
}

#: Section 3.4: an unresolvable capability is ``404``, indistinguishable from
#: "belongs to another tenant". Deliberate.
NOT_FOUND_CODE_BY_KIND: Final[dict[str, ErrorCode]] = {
    "AGENT_RUN": ErrorCode.AGENT_RUN_NOT_FOUND,
    "ACTION_INTENT": ErrorCode.ACTION_INTENT_NOT_FOUND,
    "TRIGGER_EVALUATION": ErrorCode.TRIGGER_NOT_FOUND,
    "INGEST_JOB": ErrorCode.INGEST_ALIAS_NOT_FOUND,
}

#: Which ``WorkloadKind`` a presented capability implies. The enum is
#: ``provenance_domain``'s and is closed.
_WORKLOAD_BY_KIND: Final[dict[str, dict[str, WorkloadKind]]] = {
    "provenance-agent-runtime": {"AGENT_RUN": WorkloadKind.AGENT_RUNTIME},
    "provenance-workers": {
        "INGEST_JOB": WorkloadKind.WORKER_INGEST,
        "TRIGGER_EVALUATION": WorkloadKind.WORKER_TRIGGER,
        "ACTION_INTENT": WorkloadKind.WORKER_ACTION_EXECUTOR,
    },
}


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """One row from ``agent_runs`` / ``prospective_triggers`` /
    ``action_intents`` / ``ingest_aliases``, projected onto one shape.

    ``lookup_key`` exists because ``INGEST_JOB`` is addressed by
    ``alias_hash`` rather than by a UUID -- section 3.3: the SES worker has no
    id yet, only an opaque forwarded-email alias.
    """

    binding_kind: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    expires_at: datetime
    status: str
    capability_id: uuid.UUID | None = None
    alias_hash: str | None = None
    case_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    allowed_case_ids: tuple[uuid.UUID, ...] = ()
    trace_id: uuid.UUID | None = None

    @property
    def lookup_key(self) -> str:
        return str(self.capability_id if self.capability_id is not None else self.alias_hash)

    def with_status(self, status: str) -> CapabilityRecord:
        return replace(self, status=status)

    def with_expiry(self, expires_at: datetime) -> CapabilityRecord:
        return replace(self, expires_at=expires_at)

    def to_binding(self) -> CapabilityBinding:
        return CapabilityBinding(
            binding_id=self.capability_id or uuid.uuid5(uuid.NAMESPACE_OID, self.lookup_key),
            binding_kind=self.binding_kind,  # type: ignore[arg-type]
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_id=self.artifact_id,
            allowed_case_ids=self.allowed_case_ids,
            expires_at=self.expires_at,
            status=self.status,  # type: ignore[arg-type]
        )


class CapabilityStore(Protocol):
    async def load(self, kind: str, key: str) -> CapabilityRecord | None: ...


def workload_kind_for(app_client: str, kind: str) -> WorkloadKind:
    try:
        return _WORKLOAD_BY_KIND[app_client][kind]
    except KeyError as exc:
        raise ApiError(
            ErrorCode.CAPABILITY_SCOPE_MISMATCH,
            details={"capability_kind": kind, "client_id": app_client},
        ) from exc


def _client_may_present(app_client: str, kind: str) -> bool:
    return kind in CLIENT_CAPABILITY_MATRIX.get(app_client, frozenset())


async def resolve_capability(
    store: CapabilityStore,
    *,
    kind: str,
    key: str | uuid.UUID,
    app_client: str,
    scopes: frozenset[str],
    required_scope: OAuthScope,
    proof_header: str | None,
    hmac_key: bytes,
    now: datetime,
    trace_id: uuid.UUID,
    request_id: uuid.UUID,
    token_issued_at: datetime,
    token_expires_at: datetime,
    payload_user_id: uuid.UUID | None = None,
) -> InternalPrincipal:
    """Section 3.4's ``resolve_capability``, in the order that document gives.

    Scope first, because refusing on a scope the caller was never granted
    leaks nothing. Then the row. Then liveness. Then the payload cross-check.
    Then the proof. Then the client/capability matrix.

    ``payload_user_id`` is section 3.6's tripwire, and it is checked here
    rather than in the handler for the three endpoints that carry the
    capability id in the **body** (sections 9.1, 9.7, 9.8). Those endpoints
    have no other way to name the capability, so a proposal aimed at a run the
    caller does not hold would otherwise surface as
    ``CAPABILITY_PROOF_INVALID`` -- true, but it buries the finding. The
    mismatch is what the operator needs to see, and it is what section 3.6
    calls a high-severity alarm. Sitting after liveness and before the proof
    leaks nothing further: an id that resolves to no row has already answered
    ``404`` two checks earlier.
    """
    if str(required_scope) not in scopes:
        raise ApiError(
            ErrorCode.INSUFFICIENT_SCOPE, details={"required_scope": str(required_scope)}
        )

    record = await store.load(kind, str(key))
    if record is None:
        raise ApiError(NOT_FOUND_CODE_BY_KIND[kind], details={"id": str(key)})

    if record.status == "REVOKED":
        raise ApiError(ErrorCode.CAPABILITY_REVOKED, details={"capability_kind": kind})
    if record.status != "ACTIVE":
        raise ApiError(ErrorCode.CAPABILITY_CONSUMED, details={"capability_kind": kind})
    if record.expires_at <= now:
        raise ApiError(
            ErrorCode.CAPABILITY_EXPIRED,
            details={
                "capability_kind": kind,
                "expired_at": record.expires_at.isoformat().replace("+00:00", "Z"),
            },
        )

    if payload_user_id is not None and payload_user_id != record.user_id:
        # The offending value is deliberately not echoed back: a correct
        # system never produces this, and repeating the id would confirm it.
        raise ApiError(
            ErrorCode.CAPABILITY_SCOPE_MISMATCH,
            details={
                "capability_kind": kind,
                "field": "user_id",
                "reason": "PAYLOAD_USER_MISMATCH",
            },
        )

    verify_capability_proof(kind, key, record.expires_at, proof_header, key=hmac_key)

    if not _client_may_present(app_client, kind):
        raise ApiError(
            ErrorCode.CAPABILITY_SCOPE_MISMATCH,
            details={"capability_kind": kind, "client_id": app_client},
        )

    return InternalPrincipal(
        app_client=app_client,  # type: ignore[arg-type]
        workload=workload_kind_for(app_client, kind),
        scopes=frozenset(OAuthScope(s) for s in scopes if s in set(OAuthScope)),
        binding=record.to_binding(),
        agent_run_id=record.capability_id if kind == "AGENT_RUN" else None,
        token_issued_at=token_issued_at,
        token_expires_at=token_expires_at,
        request_id=request_id,
        trace_id=record.trace_id or trace_id,
    )


def assert_within_capability(
    binding: CapabilityBinding,
    *,
    claimed_user_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
) -> None:
    """Section 3.6's server-side predicates.

    ``claimed_user_id`` is the ``MemoryProposal.user_id`` tripwire: it is
    *compared* and then discarded. It never selects a row, scopes a query, or
    widens authority. A mismatch is a high-severity alarm because a correct
    system can never produce one -- so the failure is loud and the offending
    value is deliberately **not** echoed back to the caller.
    """
    if claimed_user_id is not None and claimed_user_id != binding.user_id:
        raise ApiError(
            ErrorCode.CAPABILITY_SCOPE_MISMATCH,
            details={
                "capability_kind": binding.binding_kind,
                "field": "user_id",
                "reason": "PAYLOAD_USER_MISMATCH",
            },
        )
    if case_id is not None:
        permitted = bool(binding.allowed_case_ids) or binding.case_id is not None
        if permitted and not binding.permits_case(case_id):
            raise ApiError(
                ErrorCode.CAPABILITY_SCOPE_MISMATCH,
                details={
                    "capability_kind": binding.binding_kind,
                    "field": "case_id",
                    "reason": "CASE_OUTSIDE_CAPABILITY",
                },
            )
