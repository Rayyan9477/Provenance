"""The `/internal/v1` surface: thirteen routes, no caller-named users.

Authority: ``specs/15_API_SPEC.md`` sections 9.1-9.13, with section 3 as the
rule they all obey.

How ownership arrives
---------------------
Every handler below receives an :class:`InternalPrincipal` and immediately
converts it to a ``CapabilityBinding`` with ``require_binding()``. That binding
came from a server-side row -- ``agent_runs``, ``prospective_triggers``,
``action_intents`` or ``ingest_aliases`` -- selected by an id the caller
presented and populated by columns the caller never touched. The ports below
take the binding, not an ``OwnerScope`` built from anything else, because the
binding also carries ``allowed_case_ids`` and the bound artifact: the two
things an internal handler must not take from the request.

Ten of the thirteen carry the capability id in the path. Three cannot:
section 9.1 has only an alias, and sections 9.7 and 9.8 have only a body. For
those the id is read from the body and used to *select* the row -- which is
not the same as trusting the body, because every ownership field still comes
from the row. Section 3.6's ``user_id`` cross-check runs against the resolved
row, and its failure is a `403`, never a silent narrowing.

The two service-level routes, sections 9.12 and 9.13, hold no capability at
all and touch no user-owned state: the outbox sweep is tenant-agnostic
infrastructure and returns counts, and event delivery dedupes on ``event_id``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from provenance_contracts.identity import (
    AuthorizationError,
    CapabilityBinding,
    InternalPrincipal,
)
from provenance_domain.enums import OAuthScope
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.deps import (
    WorkloadContext,
    api_config,
    api_deps,
    as_uuid,
    query_params,
    request_ids,
    require_capability,
    require_workload_scope,
    resolve_body_capability,
)
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.responses import absent, begin, json_response
from services.control_plane.app.api.schemas.internal import (
    ActionExecuteRequest,
    AdvocacyActionIntentRequest,
    AgentRunCompleteRequest,
    EventDeliveryRequest,
    IngestArtifactRequest,
    MemoryProposalRequest,
    OutboxSweepRequest,
    RegisterEvidenceRequest,
    RetrievalRequest,
    TriggerEvaluateRequest,
)
from services.control_plane.app.auth.capabilities import assert_within_capability

router = APIRouter(tags=["internal"])

Config = Annotated[ApiConfig, Depends(api_config)]
Deps = Annotated[Dependencies, Depends(api_deps)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]

AgentRun = Annotated[
    InternalPrincipal,
    Depends(require_capability("AGENT_RUN", "agent_run_id", OAuthScope.MEMORY_READ)),
]
# Recorded conflict, reported and not resolved here. `15_API_SPEC.md` §9.0
# footnote says §9.4 is "the one endpoint where the agent-runtime client needs
# `provenance.ingest/write`", granted restricted to the `AGENT_RUN` capability
# kind. `11_CONTRACTS.md` §7 -- which T1.5 implemented and which is green --
# hard-codes `_ALLOWED_SCOPES_BY_CLIENT`, and `provenance-agent-runtime` may
# hold only MEMORY_READ, MEMORY_PROPOSE and ACTION_PROPOSE. Constructing an
# `InternalPrincipal` for an agent token carrying INGEST_WRITE raises, so the
# footnote is unimplementable against the contract package as it stands.
# MEMORY_PROPOSE is used instead: registering evidence *is* proposing memory
# content, and the capability kind restriction the footnote actually relies on
# is enforced by `CLIENT_CAPABILITY_MATRIX` either way -- the agent still
# cannot call §9.1, which needs an `INGEST_JOB` capability.
AgentRunWrite = Annotated[
    InternalPrincipal,
    Depends(require_capability("AGENT_RUN", "agent_run_id", OAuthScope.MEMORY_PROPOSE)),
]
TriggerRun = Annotated[
    InternalPrincipal,
    Depends(require_capability("TRIGGER_EVALUATION", "trigger_id", OAuthScope.TRIGGER_EVALUATE)),
]
ActionRun = Annotated[
    InternalPrincipal,
    Depends(require_capability("ACTION_INTENT", "action_intent_id", OAuthScope.ACTION_EXECUTE)),
]

IngestWorkload = Annotated[
    WorkloadContext, Depends(require_workload_scope(OAuthScope.INGEST_WRITE))
]
ProposeWorkload = Annotated[
    WorkloadContext, Depends(require_workload_scope(OAuthScope.MEMORY_PROPOSE))
]
AdvocateWorkload = Annotated[
    WorkloadContext, Depends(require_workload_scope(OAuthScope.ACTION_PROPOSE))
]
DispatchWorkload = Annotated[
    WorkloadContext, Depends(require_workload_scope(OAuthScope.OUTBOX_DISPATCH))
]

#: Which liveness failure each ``require_binding`` refusal maps onto. The
#: capability was already checked during resolution, so reaching one of these
#: means the row changed underneath the request -- which is a `403`, not a 500.
_BINDING_FAILURES = {
    "NO_CAPABILITY_BINDING": ErrorCode.CAPABILITY_SCOPE_MISMATCH,
    "BINDING_NOT_ACTIVE": ErrorCode.CAPABILITY_CONSUMED,
    "BINDING_EXPIRED": ErrorCode.CAPABILITY_EXPIRED,
}


def _binding(principal: InternalPrincipal, deps: Dependencies) -> CapabilityBinding:
    """Fail closed. There is no other accessor for tenant and user here."""
    try:
        return principal.require_binding(now=deps.clock())
    except AuthorizationError as exc:
        raise ApiError(
            _BINDING_FAILURES.get(exc.code, ErrorCode.CAPABILITY_SCOPE_MISMATCH),
            details={"reason": exc.code},
        ) from exc


# --------------------------------------------------------------------------
# 9.1 -- SES ingest
# --------------------------------------------------------------------------


@router.post(
    "/ingest/artifacts",
    status_code=201,
    summary="Inbound email. The worker presents an alias, never a user.",
)
async def ingest_artifact(
    request: Request,
    workload: IngestWorkload,
    config: Config,
    deps: Deps,
    payload: IngestArtifactRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.1.

    A failed ``spf``/``dkim``/``dmarc`` verdict does **not** reject the
    message: a spoofed sender is itself meaningful evidence, and it is
    preserved and used to lower the artifact's source-authority band. A failed
    virus or spam verdict does reject, because that is not evidence, it is a
    payload.
    """
    principal = await resolve_body_capability(
        request,
        workload,
        kind="INGEST_JOB",
        key=payload.alias_hash,
        scope=OAuthScope.INGEST_WRITE,
    )
    binding = _binding(principal, deps)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.ingest.artifact",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        if payload.size_bytes > config.max_artifact_bytes:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                details={"max_size_bytes": config.max_artifact_bytes},
            )
        blocking = payload.ses_verdicts.blocking_failure()
        if blocking is not None:
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                details={"reason": "SES_VERDICT_FAIL", "verdict": blocking},
            )
        row = await deps.internal.ingest_artifact(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)


# --------------------------------------------------------------------------
# 9.2 - 9.6 -- inside one agent run
# --------------------------------------------------------------------------


@router.get(
    "/agent-runs/{agent_run_id}",
    summary="Run bootstrap. Discovers what the run may touch.",
)
async def agent_run(principal: AgentRun, deps: Deps, agent_run_id: uuid.UUID) -> JSONResponse:
    """Section 9.2.

    The response contains no ``user_id`` and no ``tenant_id``. Withholding
    them removes the temptation to pass one back, and the possibility of a
    model seeing and repeating one.
    """
    del agent_run_id  # the capability, not the path, decides what is read
    row = await deps.internal.agent_run(_binding(principal, deps))
    if row is None:
        raise absent(ErrorCode.AGENT_RUN_NOT_FOUND)
    return json_response(row)


@router.get(
    "/agent-runs/{agent_run_id}/artifact-content",
    summary="The parsed bytes bound to this run. No artifact parameter.",
)
async def artifact_content(
    request: Request, principal: AgentRun, deps: Deps, agent_run_id: uuid.UUID
) -> JSONResponse:
    """Section 9.3.

    There is deliberately no ``artifact_id`` parameter. The artifact is the
    one on the capability row, so a run cannot read a second artifact by
    asking for it.
    """
    del agent_run_id
    params = query_params(request)
    row = await deps.internal.artifact_content(
        _binding(principal, deps), block_id=params.get("block_id")
    )
    if row is None:
        raise absent(ErrorCode.ARTIFACT_NOT_FOUND)
    return json_response(row)


@router.post(
    "/agent-runs/{agent_run_id}/evidence",
    status_code=201,
    summary="Register extraction candidates as immutable evidence.",
)
async def register_evidence(
    request: Request,
    principal: AgentRunWrite,
    deps: Deps,
    agent_run_id: uuid.UUID,
    payload: RegisterEvidenceRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.4.

    Admitting evidence means "this text was present in the artifact", never
    "this claim is true". ``source_authority`` is assigned server-side and the
    request schema has no field for it.
    """
    del agent_run_id
    binding = _binding(principal, deps)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.evidence.register",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.internal.register_evidence(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)


@router.post(
    "/agent-runs/{agent_run_id}/retrieval",
    summary="Deterministic, bounded retrieval. Read-only, so no key.",
)
async def retrieval(
    principal: AgentRun, deps: Deps, agent_run_id: uuid.UUID, payload: RetrievalRequest
) -> JSONResponse:
    """Section 9.5. ``POST`` because the query spec is a structured object,
    not because anything mutates."""
    del agent_run_id
    return json_response(await deps.internal.retrieve(_binding(principal, deps), payload))


@router.get(
    "/agent-runs/{agent_run_id}/state-proof",
    summary="The case's deterministic state proof, plus advocacy context.",
)
async def run_state_proof(
    request: Request, principal: AgentRun, deps: Deps, agent_run_id: uuid.UUID
) -> JSONResponse:
    """Section 9.6.

    ``case_id`` is required and is checked against the capability before the
    read is issued: a run bound to one case cannot read the state proof of
    another, even one belonging to the same user.
    """
    del agent_run_id
    case_id = as_uuid(query_params(request), "case_id")
    if case_id is None:
        raise ApiError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            details={"parameter": "case_id", "reason": "REQUIRED"},
        )
    binding = _binding(principal, deps)
    assert_within_capability(binding, case_id=case_id)
    row = await deps.internal.run_state_proof(binding, case_id)
    if row is None:
        raise absent(ErrorCode.CASE_NOT_FOUND)
    return json_response(row)


# --------------------------------------------------------------------------
# 9.7 - 9.9
# --------------------------------------------------------------------------


@router.post(
    "/memory/proposals",
    status_code=201,
    summary="The only path into the Memory Kernel.",
)
async def submit_proposal(
    request: Request,
    workload: ProposeWorkload,
    deps: Deps,
    payload: MemoryProposalRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.7.

    ``payload.user_id`` is section 3.6's tripwire: compared against the
    resolved capability and then discarded. It never selects a row.
    """
    principal = await resolve_body_capability(
        request,
        workload,
        kind="AGENT_RUN",
        key=payload.agent_run_id,
        scope=OAuthScope.MEMORY_PROPOSE,
        payload_user_id=payload.user_id,
    )
    binding = _binding(principal, deps)
    assert_within_capability(
        binding, claimed_user_id=payload.user_id, case_id=payload.declared_case_id()
    )
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.memory.proposal",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        # `guard.key` and not `idempotency_key`: the header may be absent or
        # malformed, and `begin` is what turned it into a validated key (a
        # `400` otherwise). Passing the raw header would hand the proposal a
        # value the idempotency lane already refused.
        row = await deps.internal.submit_proposal(binding, payload, idempotency_key=guard.key)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)


@router.post(
    "/advocacy/action-intents",
    status_code=201,
    summary="Propose a grounded draft. Creating one is not an action.",
)
async def create_action_intent(
    request: Request,
    workload: AdvocateWorkload,
    deps: Deps,
    payload: AdvocacyActionIntentRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.8. Nothing leaves the system until a human approves at 8.26."""
    principal = await resolve_body_capability(
        request,
        workload,
        kind="AGENT_RUN",
        key=payload.agent_run_id,
        scope=OAuthScope.ACTION_PROPOSE,
    )
    binding = _binding(principal, deps)
    assert_within_capability(binding, case_id=payload.case_id)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.advocacy.intent",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.internal.create_action_intent(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(201, row)


@router.post(
    "/agent-runs/{agent_run_id}/complete",
    summary="Close the run and burn the capability.",
)
async def complete_agent_run(
    request: Request,
    principal: AgentRun,
    deps: Deps,
    agent_run_id: uuid.UUID,
    payload: AgentRunCompleteRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.9.

    The request field is ``tool_calls``, matching the ``agent_runs.tool_calls``
    column. Each entry is a closed model, so returned rows and SQL text cannot
    be smuggled into the Memory Trace through a key nobody rejected.
    """
    del agent_run_id
    binding = _binding(principal, deps)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.agent_run.complete",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.internal.complete_agent_run(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)


# --------------------------------------------------------------------------
# 9.10 - 9.13
# --------------------------------------------------------------------------


@router.post(
    "/triggers/{trigger_id}/evaluate",
    summary="Re-evaluate a prospective trigger against committed state.",
)
async def evaluate_trigger(
    request: Request,
    principal: TriggerRun,
    deps: Deps,
    trigger_id: uuid.UUID,
    payload: TriggerEvaluateRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.10.

    The scheduled wakeup is never treated as proof that the condition still
    holds; the predicate is re-evaluated. That is the difference between
    prospective memory and a reminder.
    """
    del trigger_id
    binding = _binding(principal, deps)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.trigger.evaluate",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.internal.evaluate_trigger(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)


@router.post(
    "/actions/{action_intent_id}/execute",
    summary="The only route in the system with an external effect.",
)
async def execute_action(
    request: Request,
    principal: ActionRun,
    deps: Deps,
    action_intent_id: uuid.UUID,
    payload: ActionExecuteRequest,
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    """Section 9.11. Everything is revalidated immediately before the send."""
    del action_intent_id
    binding = _binding(principal, deps)
    trace_id, _ = request_ids(request)
    guard = await begin(
        request,
        deps=deps,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        scope="internal.action.execute",
        presented_key=idempotency_key,
        trace_id=trace_id,
    )
    replay = guard.replayed()
    if replay is not None:
        return replay
    try:
        row = await deps.internal.execute_action(binding, payload)
    except Exception:
        await guard.failed()
        raise
    return await guard.complete(200, row)


@router.post(
    "/events/outbox/sweep",
    summary="Claim, publish and settle a batch. Counts only.",
)
async def sweep_outbox(
    workload: DispatchWorkload, deps: Deps, payload: OutboxSweepRequest
) -> JSONResponse:
    """Section 9.12.

    Service-level authorisation: there is no per-user capability because the
    sweep is tenant-agnostic infrastructure. It returns counts and never an
    event payload, so the absence of a capability costs nothing.
    """
    del workload
    return json_response(await deps.internal.sweep_outbox(payload))


@router.post(
    "/events/deliveries",
    summary="Consumer intake. Deduped on event_id, not on a header.",
)
async def deliver_event(
    workload: DispatchWorkload, deps: Deps, payload: EventDeliveryRequest
) -> JSONResponse:
    """Section 9.13.

    ``DUPLICATE_NOOP`` is a ``200``. Duplicate delivery is normal in an
    at-least-once system; treating it as a failure would make the DLQ
    meaningless.
    """
    del workload
    return json_response(await deps.internal.deliver_event(payload))
