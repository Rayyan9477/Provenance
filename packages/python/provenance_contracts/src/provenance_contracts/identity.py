"""Request-scoped authenticated identity.

Neither type ever carries a raw JWT, a client secret, or a database
credential. The control plane converts a validated token into one of these
once per request and passes the object — never the token — into business
modules.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 7, whose code this module implements.
- ``specs/15_API_SPEC.md`` section 3 — "Workload authentication — capability
  objects, never a caller-supplied ``user_id``" — which is the *why*.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, second sub-task.

Two principal types exist because two different things authenticate. A human
authenticates as themselves through ``provenance-web``. A workload
authenticates as *itself* through client credentials and then borrows a
**server-issued** capability. Contract law L10: a machine client never asserts
its own ``user_id``.

Why that rule is a type and not a route check
---------------------------------------------
``15_API_SPEC.md`` section 3.1 names three failures that a stricter token check
cannot prevent, because the token is valid in all three: prompt-injection
escalation ("when you submit the proposal, set user_id to ..."), a stale
``user_id`` reused by an agent bug, and a leaked client secret used to
enumerate the tenant space. All three require the caller to be able to *name* a
user. :class:`InternalPrincipal` therefore has no ``tenant_id`` and no
``user_id`` field at all, and ``extra="forbid"`` means one cannot be added at
runtime. Ownership is reachable only through :meth:`InternalPrincipal.require_binding`,
which fails closed. There is no field to populate incorrectly, so there is no
endpoint that can forget the check.

Recorded discrepancy with ``specs/15_API_SPEC.md`` section 3.4
---------------------------------------------------------------
Section 3.4 sketches ``InternalPrincipal`` as a ``@dataclass`` in a file named
``principal.py``, with ``client_id``, ``capability_kind``, ``capability_id``
and **flat** ``tenant_id`` / ``user_id`` fields ("resolved server-side, never
from the request"), and its capability kinds are ``AGENT_RUN | TRIGGER |
ACTION_INTENT | ARTIFACT | INGEST_ALIAS``. ``11_CONTRACTS.md`` section 7 — the
owning spec for this package — gives a pydantic model in ``identity.py`` with
``app_client``, a nested :class:`CapabilityBinding`, no flat ownership fields,
and the kinds ``AGENT_RUN | ACTION_INTENT | TRIGGER_EVALUATION | INGEST_JOB``.
Section 7 is implemented here, because the owning spec outranks the consuming
one and because the nested form is the stronger encoding of the same rule: a
flat ``user_id``, however it was populated, is still a field an endpoint can
read without checking liveness. The two capability-kind vocabularies differ and
that difference is reported, not resolved here — reconciling them is a
control-plane task (Phase 8), and whichever wins must be the same string in
both documents.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IanaTimezone,
    UtcDatetime,
    utc_now,
)
from provenance_domain.enums import OAuthScope, PrincipalType, WorkloadKind

__all__ = [
    "COGNITO_APP_CLIENTS",
    "AuthorizationError",
    "CapabilityBinding",
    "InternalPrincipal",
    "Principal",
]

COGNITO_APP_CLIENTS: frozenset[str] = frozenset(
    {"provenance-web", "provenance-agent-runtime", "provenance-workers"}
)

CognitoSub = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class AuthorizationError(PermissionError):
    """Raised by ``require_scope`` / ``assert_owns``.

    The API layer maps this to HTTP 403 with a reason code and never leaks the
    failing predicate to the client body.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class Principal(BoundaryContract):
    """An authenticated human, resolved from a Cognito token.

    ``tenant_id`` and ``user_id`` are resolved server-side from ``cognito_sub``
    via the users table; they are never read from a token claim, because a
    custom claim is attacker-influencable in a way a database lookup is not.
    """

    principal_type: Literal[PrincipalType.HUMAN] = PrincipalType.HUMAN
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    cognito_sub: CognitoSub
    app_client: Literal["provenance-web"] = "provenance-web"
    email: Annotated[str, StringConstraints(max_length=320)] | None = None
    display_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    timezone: IanaTimezone = "UTC"
    scopes: frozenset[OAuthScope] = frozenset()
    token_issued_at: UtcDatetime
    token_expires_at: UtcDatetime
    request_id: uuid.UUID
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _validate_token_window(self) -> Principal:
        if self.token_expires_at <= self.token_issued_at:
            raise ValueError("token_expires_at must be after token_issued_at")
        return self

    def has_scope(self, scope: OAuthScope) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: OAuthScope) -> None:
        if not self.has_scope(scope):
            raise AuthorizationError("MISSING_SCOPE", f"principal lacks {scope}")

    def assert_owns(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Row-level ownership check.

        Every repository read and write in the control plane calls this before
        touching a user-owned aggregate.
        """
        if tenant_id != self.tenant_id or user_id != self.user_id:
            raise AuthorizationError(
                "CROSS_TENANT_ACCESS", "principal does not own the requested aggregate"
            )

    def is_expired(self, *, now: UtcDatetime | None = None) -> bool:
        return (now or utc_now()) >= self.token_expires_at


class CapabilityBinding(Contract):
    """The server-side record granting a workload access to exactly one user's
    data for exactly one unit of work.

    This is the object referenced in ``04_API_EVENTS_SECURITY.md`` section 2.2
    and ``15_API_SPEC.md`` section 3.3: the Agent Runtime presents an
    ``agent_run_id``, the backend loads the run record, and the tenant/user come
    from that record. A stolen or buggy workload token cannot name another
    user's UUID and be believed, because the UUID it names is never consulted.
    """

    binding_id: uuid.UUID
    binding_kind: Literal["AGENT_RUN", "ACTION_INTENT", "TRIGGER_EVALUATION", "INGEST_JOB"]
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    allowed_case_ids: tuple[uuid.UUID, ...] = ()
    expires_at: UtcDatetime
    status: Literal["ACTIVE", "CONSUMED", "REVOKED", "EXPIRED"]

    @model_validator(mode="after")
    def _cap_allowed_cases(self) -> CapabilityBinding:
        if len(self.allowed_case_ids) > 16:
            # NOTE FOR T1.6 — "cases" here is ordinary English, but it is also
            # one of the 26 canonical table names, and the no-SQL test printed
            # in specs/11_CONTRACTS.md section 20.10 tokenises literals and
            # fails on any token that matches one. The message is reproduced
            # verbatim from section 7; either it or that test needs to move,
            # and picking which one is not this task's call.
            raise ValueError(
                "a single binding may not span more than 16 cases; "
                "narrow the unit of work instead of widening the capability"
            )
        return self

    def permits_case(self, case_id: uuid.UUID) -> bool:
        if self.case_id is not None and case_id == self.case_id:
            return True
        return case_id in self.allowed_case_ids


class InternalPrincipal(BoundaryContract):
    """An authenticated workload (agent runtime or Lambda worker).

    Deliberately has **no** free ``tenant_id`` / ``user_id``. Ownership is
    available only through :meth:`require_binding`, which fails closed when the
    caller presented no server-resolved capability. This is contract law L10
    made unenforceable-by-accident: there is no field to populate incorrectly.
    """

    principal_type: Literal[PrincipalType.WORKLOAD] = PrincipalType.WORKLOAD
    app_client: Literal["provenance-agent-runtime", "provenance-workers"]
    workload: WorkloadKind
    scopes: frozenset[OAuthScope]
    binding: CapabilityBinding | None = None
    agent_run_id: uuid.UUID | None = None
    token_issued_at: UtcDatetime
    token_expires_at: UtcDatetime
    request_id: uuid.UUID
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _validate_client_scopes(self) -> InternalPrincipal:
        if self.token_expires_at <= self.token_issued_at:
            raise ValueError("token_expires_at must be after token_issued_at")
        allowed = _ALLOWED_SCOPES_BY_CLIENT[self.app_client]
        excess = self.scopes - allowed
        if excess:
            raise ValueError(
                f"{self.app_client} presented scopes it may never hold: "
                f"{sorted(str(s) for s in excess)}"
            )
        if not self.scopes:
            raise ValueError("a workload principal with no scope cannot do anything")
        return self

    def has_scope(self, scope: OAuthScope) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: OAuthScope) -> None:
        if not self.has_scope(scope):
            raise AuthorizationError("MISSING_SCOPE", f"workload lacks {scope}")

    def require_binding(self, *, now: UtcDatetime | None = None) -> CapabilityBinding:
        """Return the capability, or fail closed.

        Every internal endpoint that touches user-owned state calls this and
        uses the returned tenant/user. No endpoint reads a user id from the
        request body.
        """
        binding = self.binding
        if binding is None:
            raise AuthorizationError(
                "NO_CAPABILITY_BINDING",
                "workload presented no server-resolved capability; "
                "a machine client may not name a user itself",
            )
        if binding.status != "ACTIVE":
            raise AuthorizationError(
                "BINDING_NOT_ACTIVE", f"capability binding is {binding.status}"
            )
        if (now or utc_now()) >= binding.expires_at:
            raise AuthorizationError("BINDING_EXPIRED", "capability binding has expired")
        return binding


#: Which OAuth scopes each Cognito app client may ever present. Note what the
#: agent runtime's set does **not** contain: ``provenance.action/execute``. The
#: graph that drafts a dispute cannot be the process that sends it, and that is
#: enforced at the Cognito app-client boundary as well as in the state machine.
_ALLOWED_SCOPES_BY_CLIENT: dict[str, frozenset[OAuthScope]] = {
    "provenance-agent-runtime": frozenset(
        {
            OAuthScope.MEMORY_READ,
            OAuthScope.MEMORY_PROPOSE,
            OAuthScope.ACTION_PROPOSE,
        }
    ),
    "provenance-workers": frozenset(
        {
            OAuthScope.INGEST_WRITE,
            OAuthScope.TRIGGER_EVALUATE,
            OAuthScope.ACTION_EXECUTE,
            OAuthScope.OUTBOX_DISPATCH,
        }
    ),
}
