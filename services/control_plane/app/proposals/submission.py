"""Section 9.7's app-side half: the proposal row, and the door to the Kernel.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.7 -- "the only write path an agent has".
- ``specs/10_DATABASE_DDL.md`` section 12 -- the app holds ``INSERT`` on
  ``memory_proposals``; the Memory Kernel holds only ``UPDATE``.
- ``specs/11_CONTRACTS.md`` section 12 --
  :class:`~provenance_contracts.proposal.MemoryProposal`, which is the object
  this module builds and the Kernel decides.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer* and -> *Disclosure*.

Why the row has to exist before the Kernel is called
-----------------------------------------------------
``memory_kernel.transaction.commit_proposal`` settles a proposal; it never
creates one. ``fk_kernel_decisions_proposal`` is
``FOREIGN KEY (tenant_id, user_id, proposal_id) REFERENCES memory_proposals``,
so calling the Kernel first fails at the database on the one path the whole
product rests on. The order in :func:`register_proposal` then
``KernelProposalWriter.commit`` is that foreign key, not a preference.

``ON CONFLICT DO NOTHING`` is the same reason ``scripts/seed/loader.py`` uses
it: a retry re-offers the row it already wrote, and idempotency is the Kernel's
(rule ``R6`` returns the recorded decision for a proposal id that already has
one). A unique violation here would refuse the retry before the Kernel could
recognise it.

Where the model attribution comes from, and why it is split
-------------------------------------------------------------
``MemoryProposal.model`` is a required
:class:`~provenance_contracts.resolution.ModelAttribution` with six fields, and
they do not all have the same trustworthiness:

* ``graph_name`` and ``graph_version`` are ``NOT NULL`` columns on the
  ``agent_runs`` row the capability already resolved. Read, never asserted.
* ``model_id`` is *claimed* by the caller and **compared** against
  ``agent_runs.model_route``. ``CANONICAL_DECISIONS.md`` -> *Disclosure* makes
  the shipped model checkable against persisted state rather than against a
  README, and that property survives a caller-supplied id only if the id has to
  match one the run row already records. A mismatch is a refusal.
* ``tier`` is claimed and checked in the same statement: the claimed tier
  selects *which* route entry must hold the claimed id. It is not derived by
  searching the route, because a configuration that points both tiers at one id
  -- the documented response to a Tier R capacity failure
  (``CANONICAL_DECISIONS.md`` -> *Tier R fallback*) -- would make a derived
  tier ambiguous, and a search that silently picked one would record a tier the
  call may not have used.
* ``provider`` is claimed and checked by ``ModelAttribution``'s own validator,
  which dispatches the id-shape rule on it (``D-00-040``: the Bedrock and
  Gemini rules are mirror images, so a client applying one uniformly cannot
  call both families).
* ``prompt_version`` is claimed and **cannot be checked here**. It reaches
  persisted state on ``agent_runs.model_calls[]``, which only section 9.9's
  completion body writes -- *after* section 9.7 has run. So at proposal time
  the caller is its only holder. This is the one field on the block that is an
  assertion rather than a cross-check, and it is recorded as such rather than
  quietly presented as verified.

What this module refuses to invent
-----------------------------------
``scripts/seed/proposals.py`` writes ``deterministic.kernel`` into
``memory_proposals.model_id`` because no model produced the curated fixtures,
and says so. A model *did* produce anything arriving through section 9.7, so
this module writes the id that served the run. Filling the column with the
seed's value -- or with any id the run was not routed on -- would put a false
provenance record inside the provenance system.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from psycopg.types.json import Jsonb

from provenance_contracts.kernel import KernelCommitResult
from provenance_contracts.proposal import MemoryProposal
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ModelTier

__all__ = [
    "PROPOSAL_INSERT_SQL",
    "SUBMITTED_STATUS",
    "KernelProposalWriter",
    "ProposalRefusedError",
    "build_proposal",
    "insert_params",
    "payload_sha256",
    "proposal_payload",
    "register_proposal",
    "resolve_attribution",
]

#: ``memory_proposals.status`` at submission. ``ck_memory_proposals_decided``
#: is ``status = 'SUBMITTED' OR decided_at IS NOT NULL``, so this is the only
#: value the app may write: every other status names a decision, and only the
#: Kernel takes one.
SUBMITTED_STATUS: Final[str] = "SUBMITTED"

#: Write rule ``W4``. The app admits a proposal; the Kernel settles it.
PROPOSAL_INSERT_SQL: Final[str] = """
INSERT INTO memory_proposals (
    id, tenant_id, user_id, trace_id, agent_run_id, schema_version, proposal_type,
    source_artifact_ids, evidence_ids, candidate_relationship_id, candidate_case_id,
    payload, payload_sha256, model_id, prompt_version, status, created_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(trace_id)s, %(agent_run_id)s,
    %(schema_version)s, %(proposal_type)s, %(source_artifact_ids)s, %(evidence_ids)s,
    %(candidate_relationship_id)s, %(candidate_case_id)s, %(payload)s,
    %(payload_sha256)s, %(model_id)s, %(prompt_version)s, %(status)s, %(created_at)s
)
ON CONFLICT DO NOTHING
"""

#: ``ModelTier`` -> the ``agent_runs.model_route`` key that must hold the id.
#: ``EMBEDDING`` is absent on purpose: an embedding model produces no claim, so
#: there is no route entry it could legitimately be attributed through, and a
#: lookup that missed would be indistinguishable from a typo.
_ROUTE_KEY_BY_TIER: Final[Mapping[ModelTier, str]] = {
    ModelTier.E: "tier_e",
    ModelTier.R: "tier_r",
}


class ProposalRefusedError(Exception):
    """A typed refusal carrying the reason code and the body's ``details``.

    Shaped like :class:`~services.control_plane.app.actions.ActionRefusedError`
    so the API layer maps a code to an ``ErrorCode`` and a status in one place,
    and this package holds no dependency on the API layer.
    """

    def __init__(self, reason_code: str, **details: Any) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details: dict[str, Any] = details


# ---------------------------------------------------------------------------
# The payload and its digest
# ---------------------------------------------------------------------------


def proposal_payload(proposal: MemoryProposal) -> dict[str, Any]:
    """The ``memory_proposals.payload`` value: the proposal itself.

    Anything smaller would make the persisted row a summary of a proposal
    rather than the proposal, and the Memory Trace reads this column.
    """
    import json

    loaded: dict[str, Any] = json.loads(proposal.model_dump_json())
    return loaded


def payload_sha256(proposal: MemoryProposal) -> bytes:
    """``memory_proposals.payload_sha256`` -- 32 bytes, and deterministic.

    ``uq_memory_proposals_payload`` is ``(tenant_id, user_id, payload_sha256)``,
    so the digest is what makes a re-offered proposal the row that is already
    there rather than a second logical proposal. ``scripts/seed/proposals.py``
    computes the same digest the same way, and
    ``tests/api/test_proposal_submission.py`` asserts the two agree rather than
    trusting that they do.
    """
    return hashlib.sha256(proposal.model_dump_json().encode("utf-8")).digest()


def insert_params(
    proposal: MemoryProposal, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """Every bind for :data:`PROPOSAL_INSERT_SQL`, from one proposal.

    ``tenant_id`` and ``user_id`` are arguments rather than fields on the
    proposal: ``MemoryProposal`` carries no ``tenant_id`` at all, by design, and
    its ``user_id`` is section 3.6's tripwire -- compared against the resolved
    capability and then discarded. The row is scoped by the capability.
    """
    identity = proposal.identity
    return {
        "id": proposal.proposal_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": proposal.trace_id,
        "agent_run_id": proposal.agent_run_id,
        "schema_version": proposal.schema_version,
        "proposal_type": str(proposal.proposal_type),
        "source_artifact_ids": Jsonb([str(a) for a in proposal.source_artifact_ids]),
        "evidence_ids": Jsonb([str(e) for e in proposal.evidence_ids]),
        "candidate_relationship_id": identity.relationship_id,
        "candidate_case_id": identity.case_id,
        "payload": Jsonb(proposal_payload(proposal)),
        "payload_sha256": payload_sha256(proposal),
        "model_id": proposal.model.model_id,
        "prompt_version": proposal.model.prompt_version,
        "status": SUBMITTED_STATUS,
        "created_at": proposal.created_at,
    }


async def register_proposal(
    conn: Any, proposal: MemoryProposal, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """Admit the proposal row. Returns how many rows the statement wrote.

    ``0`` is a legitimate answer and means "this proposal was already
    submitted" -- the retry path. It is returned rather than swallowed because
    the caller then knows the Kernel is about to take the recorded-decision
    branch rather than a fresh one.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            PROPOSAL_INSERT_SQL, insert_params(proposal, tenant_id=tenant_id, user_id=user_id)
        )
        return int(cur.rowcount)


# ---------------------------------------------------------------------------
# The model attribution
# ---------------------------------------------------------------------------


def resolve_attribution(
    run: Mapping[str, Any], *, provider: str, model_id: str, tier: str, prompt_version: str
) -> ModelAttribution:
    """The six fields, three read off the run row and three checked against it.

    See the module docstring for which is which and why the split is not
    uniform. The one thing this function will not do is accept a ``model_id``
    the run was not routed on: that comparison is what keeps
    ``agent_runs.model_route`` the authority on which model served a run.

    Raises:
        ProposalRefusedError: the route is unreadable, the tier names no route
            entry, or the claimed id is not the one that entry holds.
    """
    route = run.get("model_route")
    if not isinstance(route, Mapping) or not route:
        raise ProposalRefusedError(
            "MODEL_ROUTE_UNREADABLE",
            agent_run_id=str(run.get("id")),
            reason="agent_runs.model_route is NOT NULL and did not read as an object",
        )

    try:
        model_tier = ModelTier(tier)
    except ValueError as exc:
        raise ProposalRefusedError("MODEL_TIER_UNKNOWN", tier=tier) from exc

    key = _ROUTE_KEY_BY_TIER.get(model_tier)
    if key is None:
        raise ProposalRefusedError(
            "MODEL_TIER_CANNOT_PRODUCE_A_PROPOSAL",
            tier=str(model_tier),
            reason="an embedding model produces no claim, so it attributes none",
        )

    routed = route.get(key)
    if routed != model_id:
        # The routed id is echoed: it is the run's own record and the caller
        # already holds the run. Telling it which id the row carries is what
        # turns a refusal into something the runtime can act on.
        raise ProposalRefusedError(
            "MODEL_NOT_ON_RUN_ROUTE",
            tier=str(model_tier),
            claimed_model_id=model_id,
            routed_model_id=None if routed is None else str(routed),
        )

    graph_name = run.get("graph_name")
    graph_version = run.get("graph_version")
    if not graph_name or not graph_version:
        raise ProposalRefusedError(
            "RUN_GRAPH_UNREADABLE",
            agent_run_id=str(run.get("id")),
            reason="agent_runs.graph_name and graph_version are NOT NULL columns",
        )

    try:
        return ModelAttribution(
            provider=provider,  # type: ignore[arg-type]
            model_id=model_id,
            tier=model_tier,
            prompt_version=prompt_version,
            graph_name=str(graph_name),
            graph_version=str(graph_version),
        )
    except ValueError as exc:
        raise ProposalRefusedError("MODEL_ATTRIBUTION_INVALID", reason=str(exc)) from exc


# ---------------------------------------------------------------------------
# The typed proposal
# ---------------------------------------------------------------------------


def build_proposal(
    payload: Any,
    *,
    run: Mapping[str, Any],
    idempotency_key: str,
    created_at: datetime,
) -> MemoryProposal:
    """Section 9.7's body plus the run row, as the object the Kernel decides.

    Every nested list is handed to pydantic as-is. ``MemoryProposal``'s
    validators are the schema rules section 9.7 lists -- local ids resolve,
    every referenced evidence id is declared, a transition carries a reason
    code -- and re-implementing any of them here would create a second,
    weaker copy that drifts.

    Raises:
        ProposalRefusedError: the body does not make a proposal the contract
            admits, or the model block does not agree with the run row.
    """
    from pydantic import ValidationError

    attribution = resolve_attribution(
        run,
        provider=payload.model.provider,
        model_id=payload.model.model_id,
        tier=payload.model.tier,
        prompt_version=payload.model.prompt_version,
    )
    try:
        return MemoryProposal(
            proposal_id=payload.proposal_id,
            proposal_type=payload.proposal_type,
            trace_id=payload.trace_id,
            agent_run_id=payload.agent_run_id,
            user_id=payload.user_id,
            source_artifact_ids=tuple(payload.source_artifact_ids),
            evidence_ids=tuple(payload.evidence_ids),
            identity=payload.identity,
            claims=tuple(payload.claims),
            commitments=tuple(payload.commitments),
            belief_mutations=tuple(payload.belief_mutations),
            conflict_hints=tuple(payload.conflict_hints),
            trigger_mutations=tuple(payload.trigger_mutations),
            requested_case_transition=payload.requested_case_transition,
            requested_transition_reason_code=payload.requested_transition_reason_code,
            unresolved_questions=tuple(payload.unresolved_questions),
            blocks_state_change=payload.blocks_state_change,
            model=attribution,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
    except ValidationError as exc:
        raise ProposalRefusedError(
            "PROPOSAL_CONTRACT_INVALID",
            errors=[
                {"field": ".".join(str(p) for p in error["loc"]), "reason": error["msg"]}
                for error in exc.errors()
            ][:20],
        ) from exc


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


class KernelProposalWriter:
    """``commit_proposal`` behind a one-method object, so it can be replaced.

    Same shape and same reason as
    :class:`~services.control_plane.app.memory_kernel.trigger_commit.KernelTriggerWriter`:
    the hermetic suites drive the real adapter with no cluster, which is what
    makes "did this bind the Kernel?" a unit test rather than an integration
    test. The pool it holds is ``pv_kernel_writer``'s and belongs to nothing
    else.
    """

    __slots__ = ("_pool",)

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def commit(self, proposal: MemoryProposal, *, principal: Any) -> KernelCommitResult:
        # Imported here rather than at module scope so that importing this
        # package -- which the app does to reach the W4 statement -- does not
        # drag the whole Kernel in behind it.
        from services.control_plane.app.memory_kernel import transaction

        return await transaction.commit_proposal(self._pool, proposal, principal=principal)
