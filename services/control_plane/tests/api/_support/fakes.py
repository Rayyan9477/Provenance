"""In-memory ports for the hermetic API suites.

Two tenants exist here, and that is the point. Every adversarial assertion in
``test_adversarial_isolation.py`` works by handing tenant A's token an
identifier that belongs to tenant B and requiring the API to answer ``404``.
For that to prove anything the fake must *itself* scope by
``(tenant_id, user_id)`` -- a fake that ignored the scope would make the whole
lane vacuous. So every lookup below goes through :func:`_owned`, which takes an
:class:`OwnerScope` and the row's real owner and compares them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from provenance_contracts.identity import CapabilityBinding
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import OwnerScope, UserRecord
from services.control_plane.app.auth.capabilities import CapabilityRecord

__all__ = [
    "ACTORS",
    "ALEX",
    "NOW",
    "ROB",
    "Actor",
    "FakeCapabilityStore",
    "FakeInternalPort",
    "FakeReadPort",
    "FakeUserDirectory",
    "FakeWritePort",
    "Fixture",
    "build_fixture",
]

NOW = datetime(2026, 6, 5, 14, 22, 31, 482000, tzinfo=UTC)


def _u(tail: str) -> uuid.UUID:
    return uuid.UUID(f"018f7a00-0000-7000-8000-{tail:0>12}")


@dataclass(frozen=True, slots=True)
class Actor:
    """One seeded user, with everything they own."""

    name: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    cognito_sub: str
    case_id: uuid.UUID
    belief_id: uuid.UUID
    artifact_id: uuid.UUID
    action_intent_id: uuid.UUID
    trigger_id: uuid.UUID
    agent_run_id: uuid.UUID
    trace_id: uuid.UUID
    counterfactual_id: uuid.UUID
    alias_hash: str
    judge: bool = False

    @property
    def scope(self) -> OwnerScope:
        return OwnerScope(tenant_id=self.tenant_id, user_id=self.user_id)


ALEX = Actor(
    name="Alex Rivera",
    tenant_id=_u("00000000ffff"),
    user_id=_u("00000000abcd"),
    cognito_sub="sub-alex-0001",
    case_id=_u("00000000a001"),
    belief_id=_u("00000000a002"),
    artifact_id=_u("00000000a003"),
    action_intent_id=_u("00000000a004"),
    trigger_id=_u("00000000a005"),
    agent_run_id=_u("00000000a006"),
    trace_id=_u("00000000a007"),
    counterfactual_id=_u("00000000a008"),
    alias_hash="alias-hash-alex-000000000000",
    judge=True,
)

ROB = Actor(
    name="Rob Iso",
    tenant_id=_u("00000000eeee"),
    user_id=_u("00000000bcde"),
    cognito_sub="sub-rob-0001",
    case_id=_u("00000000b001"),
    belief_id=_u("00000000b002"),
    artifact_id=_u("00000000b003"),
    action_intent_id=_u("00000000b004"),
    trigger_id=_u("00000000b005"),
    agent_run_id=_u("00000000b006"),
    trace_id=_u("00000000b007"),
    counterfactual_id=_u("00000000b008"),
    alias_hash="alias-hash-rob-0000000000000",
)

ACTORS: tuple[Actor, ...] = (ALEX, ROB)


def _actor_for(scope: OwnerScope) -> Actor | None:
    for actor in ACTORS:
        if actor.tenant_id == scope.tenant_id and actor.user_id == scope.user_id:
            return actor
    return None


def _owner_of(identifier: uuid.UUID, attribute: str) -> Actor | None:
    for actor in ACTORS:
        if getattr(actor, attribute) == identifier:
            return actor
    return None


def _owned(scope: OwnerScope, owner: Actor | None) -> bool:
    """The single scoping predicate. Everything in this module goes through it."""
    if owner is None:
        return False
    return owner.tenant_id == scope.tenant_id and owner.user_id == scope.user_id


# --------------------------------------------------------------------------
# Directory and capability store
# --------------------------------------------------------------------------


class FakeUserDirectory:
    """``cognito_sub`` -> :class:`UserRecord`. Unknown subs are unprovisioned."""

    def __init__(self, *, provisioned: Sequence[Actor] = ACTORS) -> None:
        self._by_sub = {a.cognito_sub: a for a in provisioned}

    async def by_cognito_sub(self, sub: str) -> UserRecord | None:
        actor = self._by_sub.get(sub)
        if actor is None:
            return None
        return UserRecord(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            cognito_sub=actor.cognito_sub,
            email=f"{actor.name.split()[0].lower()}@example.com",
            display_name=actor.name,
            timezone="America/New_York",
            home_region="US-NY",
            created_at=NOW - timedelta(days=140),
            judge_mode_allowlisted=actor.judge,
        )


class FakeCapabilityStore:
    """Server-side capability rows, exactly as the real tables would hold them."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], CapabilityRecord] = {}
        for actor in ACTORS:
            self.put(
                CapabilityRecord(
                    binding_kind="AGENT_RUN",
                    capability_id=actor.agent_run_id,
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    case_id=None,
                    artifact_id=actor.artifact_id,
                    allowed_case_ids=(actor.case_id,),
                    expires_at=NOW + timedelta(minutes=15),
                    status="ACTIVE",
                    trace_id=actor.trace_id,
                )
            )
            self.put(
                CapabilityRecord(
                    binding_kind="TRIGGER_EVALUATION",
                    capability_id=actor.trigger_id,
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    case_id=actor.case_id,
                    artifact_id=None,
                    allowed_case_ids=(actor.case_id,),
                    expires_at=NOW + timedelta(hours=1),
                    status="ACTIVE",
                    trace_id=None,
                )
            )
            self.put(
                CapabilityRecord(
                    binding_kind="ACTION_INTENT",
                    capability_id=actor.action_intent_id,
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    case_id=actor.case_id,
                    artifact_id=None,
                    allowed_case_ids=(actor.case_id,),
                    expires_at=NOW + timedelta(hours=24),
                    status="ACTIVE",
                    trace_id=None,
                )
            )
            self.put(
                CapabilityRecord(
                    binding_kind="INGEST_JOB",
                    capability_id=None,
                    alias_hash=actor.alias_hash,
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    case_id=None,
                    artifact_id=None,
                    allowed_case_ids=(),
                    expires_at=NOW + timedelta(minutes=5),
                    status="ACTIVE",
                    trace_id=None,
                )
            )

    def put(self, record: CapabilityRecord) -> None:
        self.records[(record.binding_kind, record.lookup_key)] = record

    def retire(self, kind: str, key: str, status: str = "CONSUMED") -> None:
        record = self.records[(kind, key)]
        self.records[(kind, key)] = record.with_status(status)

    async def load(self, kind: str, key: str) -> CapabilityRecord | None:
        return self.records.get((kind, key))


# --------------------------------------------------------------------------
# Read port
# --------------------------------------------------------------------------


def _page(items: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    return items[:limit], len(items) > limit


class FakeReadPort:
    """Deterministic projections for both seeded users."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, OwnerScope]] = []

    def _record(self, name: str, scope: OwnerScope) -> Actor | None:
        self.calls.append((name, scope))
        return _actor_for(scope)

    async def me(self, scope: OwnerScope) -> dict[str, Any] | None:
        actor = self._record("me", scope)
        if actor is None:
            return None
        return {
            "home_region": "US-NY",
            "feature_flags": {
                "ses_inbound_enabled": False,
                "upload_ingest_enabled": True,
                "counterfactual_enabled": True,
                "mcp_trace_visible": True,
            },
            "ingest_alias_status": "ACTIVE",
        }

    async def dashboard(self, scope: OwnerScope, **_: Any) -> dict[str, Any]:
        actor = self._record("dashboard", scope)
        if actor is None:
            return {
                "counts": {},
                "contexts": [],
                "relationships_summary": [],
                "cases_attention": [],
            }
        return {
            "counts": {
                "unresolved_commitments": 2,
                "active_conflicts": 1,
                "action_intents_pending": 1,
                "cases_needing_attention": 3,
                "triggers_armed": 2,
                "triggers_fired_unhandled": 1,
            },
            "contexts": [],
            "relationships_summary": [],
            "cases_attention": [_case_item(actor)],
        }

    async def list_contexts(
        self, scope: OwnerScope, *, limit: int, after: Any = None
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_contexts", scope)
        if actor is None:
            return [], False
        return _page(
            [
                {
                    "context_id": str(_u("00000000c001")),
                    "title": "The Move",
                    "context_type": "RELOCATION",
                    "status": "ACTIVE",
                    "created_at": NOW - timedelta(days=120),
                    "case_count": 5,
                    "open_case_count": 3,
                }
            ],
            limit,
        )

    async def list_relationships(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_relationships", scope)
        if actor is None:
            return [], False
        return _page([_relationship(actor)], limit)

    async def get_relationship(
        self, scope: OwnerScope, relationship_id: uuid.UUID
    ) -> dict[str, Any] | None:
        self._record("get_relationship", scope)
        owner = next((a for a in ACTORS if _relationship_id(a) == relationship_id), None)
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return _relationship(owner) | {
            "normalized_identifiers": {},
            "context": None,
            "cases": [],
            "summary": {
                "total_cases": 1,
                "open_cases": 1,
                "active_conflicts": 1,
                "unresolved_commitments": 0,
                "outstanding": [],
                "first_evidence_at": None,
                "last_evidence_at": None,
            },
        }

    async def list_cases(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_cases", scope)
        if actor is None:
            return [], False
        return _page([_case_item(actor)], limit)

    async def get_case(self, scope: OwnerScope, case_id: uuid.UUID) -> dict[str, Any] | None:
        self._record("get_case", scope)
        owner = _owner_of(case_id, "case_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return _case_item(owner) | {
            "commitments": [],
            "active_conflicts": [],
            "next_trigger": None,
            "latest_action_intent": None,
            "counts": {
                "evidence_items": 14,
                "claims": 9,
                "beliefs": 6,
                "state_transitions": 13,
            },
            "relationship": None,
            "counterparty": None,
            "context": None,
        }

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None:
        self._record("case_revision", scope)
        owner = _owner_of(case_id, "case_id")
        return 13 if _owned(scope, owner) else None

    async def list_timeline(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **_: Any
    ) -> tuple[list[dict[str, Any]], bool] | None:
        if await self.case_revision(scope, case_id) is None:
            return None
        return _page(
            [
                {
                    "id": str(_u("00000000d001")),
                    "kind": "STATE_TRANSITION",
                    "occurred_at": NOW,
                    "case_revision": 13,
                    "trace_id": None,
                    "actor": {"type": "KERNEL", "label": "Memory Kernel"},
                    "headline": "Case reopened by contradicting evidence.",
                    "detail": {},
                }
            ],
            limit,
        )

    async def state_proof(
        self, scope: OwnerScope, case_id: uuid.UUID, **_: Any
    ) -> dict[str, Any] | None:
        revision = await self.case_revision(scope, case_id)
        if revision is None:
            return None
        return {
            "schema_version": "1.0",
            "case_id": str(case_id),
            "case_revision": revision,
            "case_status": "REOPENED",
            "generated_at": NOW,
            "deterministic": True,
            "model_used": None,
            "beliefs": [],
            "conflicts": [],
            "commitments": [],
            "lineage": [],
            "excluded": {"retraction_filter_applied": True, "retracted_evidence_count": 0},
        }

    async def list_conflicts(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **_: Any
    ) -> tuple[list[dict[str, Any]], bool] | None:
        if await self.case_revision(scope, case_id) is None:
            return None
        return _page([], limit)

    async def get_belief(
        self, scope: OwnerScope, belief_id: uuid.UUID, **_: Any
    ) -> dict[str, Any] | None:
        self._record("get_belief", scope)
        owner = _owner_of(belief_id, "belief_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return {
            "belief_id": str(belief_id),
            "case_id": str(owner.case_id),
            "relationship_id": None,
            "predicate": "service_terminated",
            "grounded": True,
            "current_version": None,
            "grounding": [],
            "lineage": [],
        }

    async def list_commitments(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_commitments", scope)
        if actor is None:
            return [], False
        return _page(
            [
                {
                    "commitment_id": str(_u("00000000e001")),
                    "case_id": str(actor.case_id),
                    "relationship_id": None,
                    "counterparty_display_name": "Harborview Property Management",
                    "commitment_type": "MONETARY_REFUND",
                    "description": "Return the deposit within 30 days.",
                    "obligor_type": "COUNTERPARTY",
                    "beneficiary_type": "USER",
                    "status": "ACTIVE",
                    "currency": "USD",
                    "committed_amount": {"currency": "USD", "amount": "1800.0000"},
                    "fulfilled_amount": {"currency": "USD", "amount": "0.0000"},
                    "outstanding_amount": {"currency": "USD", "amount": "1800.0000"},
                    "due_at": NOW - timedelta(days=5),
                    "overdue": True,
                    "days_overdue": 5,
                    "source_claim_id": None,
                    "revision": 2,
                }
            ],
            limit,
        )

    async def list_triggers(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_triggers", scope)
        if actor is None:
            return [], False
        return _page(
            [
                {
                    "trigger_id": str(actor.trigger_id),
                    "case_id": str(actor.case_id),
                    "case_title": "Security deposit return",
                    "trigger_type": "COMMITMENT_DEADLINE",
                    "state": "ARMED",
                    "not_before": NOW + timedelta(days=15),
                    "expires_at": NOW + timedelta(days=90),
                    "basis_case_revision": 13,
                    "evaluation_version": 1,
                    "last_evaluated_at": None,
                    "last_result": None,
                    "last_reason_code": None,
                    "schedule_name": "provenance-trigger-a005",
                    "predicate_summary": "Outstanding deposit is greater than 0.",
                    "predicate_ast": {"op": "CONST", "value": "0"},
                    "last_evaluation": None,
                }
            ],
            limit,
        )

    async def list_artifacts(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_artifacts", scope)
        if actor is None:
            return [], False
        return _page([_artifact(actor)], limit)

    async def get_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, **_: Any
    ) -> dict[str, Any] | None:
        self._record("get_artifact", scope)
        owner = _owner_of(artifact_id, "artifact_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return _artifact(owner)

    async def ingest_alias(self, scope: OwnerScope) -> dict[str, Any] | None:
        actor = self._record("ingest_alias", scope)
        if actor is None:
            return None
        return {
            "alias_display": f"{actor.name.split()[0].lower()}@in.provenance.app",
            "status": "ACTIVE",
            "created_at": NOW - timedelta(days=140),
            "rotated_at": None,
            "artifacts_received": 23,
            "last_received_at": NOW,
        }

    async def list_action_intents(
        self, scope: OwnerScope, *, limit: int, after: Any = None, **_: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        actor = self._record("list_action_intents", scope)
        if actor is None:
            return [], False
        return _page([_action_item(actor)], limit)

    async def get_action_intent(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, **_: Any
    ) -> dict[str, Any] | None:
        self._record("get_action_intent", scope)
        owner = _owner_of(action_intent_id, "action_intent_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return _action_item(owner) | {
            "recipient": "billing@northlinebroadband.example",
            "recipient_allowlisted": True,
            "draft": {
                "subject": "Disputed invoice 88431",
                "body": "Hello,\n\nPlease cancel invoice 88431.\n\nThank you",
                "claims": [],
                "requested_outcome": "CANCEL_INVOICE_AND_CONFIRM_CLOSURE",
                "tone": "FIRM_POLITE",
                "unresolved_risks": [],
            },
            "draft_sha256": "9a" * 32,
            "rationale": "A counterparty claim contradicts a written confirmation.",
            "supporting_belief_versions": [],
            "state_proof_url": f"/v1/cases/{owner.case_id}/state-proof",
            "warnings": [],
            "approval": None,
            "executions": [],
            "trace_id": str(owner.trace_id),
        }

    async def get_trace(
        self, scope: OwnerScope, trace_id: uuid.UUID, *, judge: bool = False
    ) -> dict[str, Any] | None:
        self._record("get_trace", scope)
        owner = _owner_of(trace_id, "trace_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return {
            "trace_id": str(trace_id),
            "started_at": NOW,
            "finished_at": NOW + timedelta(seconds=6),
            "duration_ms": 6000,
            "status": "COMPLETED",
            "case_ids": [str(owner.case_id)],
            "nodes": [
                {
                    "id": "n1",
                    "type": "API_REQUEST",
                    "status": "OK",
                    "parent_id": None,
                    "started_at": NOW,
                    "duration_ms": 61,
                    "summary": "POST /v1/artifacts/{id}/complete",
                    "attributes": {"http_status": 202},
                },
                {
                    "id": "n2",
                    "type": "KERNEL_DECISION",
                    "status": "OK",
                    "parent_id": None,
                    "started_at": NOW,
                    "duration_ms": 178,
                    "summary": "ACCEPTED_WITH_CONFLICT",
                    "attributes": {},
                },
            ],
            "edges": [{"from": "n1", "to": "n2"}],
            "boundary": {
                "deterministic_node_ids": ["n1", "n2"],
                "model_node_ids": [],
                "note": "Model nodes propose. Deterministic nodes decide, commit, and act.",
            },
        }

    async def memory_trace(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **_: Any
    ) -> dict[str, Any] | None:
        revision = await self.case_revision(scope, case_id)
        if revision is None:
            return None
        return {"case_id": str(case_id), "current_revision": revision, "traces": []}

    async def agent_view_names(self) -> list[str]:
        return [
            "agent_active_beliefs_v1",
            "agent_belief_lineage_v1",
            "agent_case_context_v1",
            "agent_evidence_retrieval_v1",
            "agent_open_obligations_v1",
        ]


def _relationship_id(actor: Actor) -> uuid.UUID:
    return uuid.UUID(int=actor.case_id.int ^ 0x11)


def _relationship(actor: Actor) -> dict[str, Any]:
    return {
        "relationship_id": str(_relationship_id(actor)),
        "counterparty": {
            "counterparty_id": str(_u("00000000f001")),
            "display_name": "Northline Fiber",
            "kind": "ISP",
            "canonical_domain": "northlinebroadband.example",
        },
        "label": "Old apartment ISP account",
        "relationship_type": "SERVICE_ACCOUNT",
        "status": "CLOSED",
        "external_account_ref_masked": "••••4417",
        "valid_from": NOW - timedelta(days=1000),
        "valid_to": None,
        "revision": 4,
        "open_case_count": 1,
        "attention_level": "URGENT",
        "last_activity_at": NOW,
        "updated_at": NOW,
    }


def _case_item(actor: Actor) -> dict[str, Any]:
    return {
        "case_id": str(actor.case_id),
        "title": f"{actor.name} case",
        "status": "REOPENED",
        "revision": 13,
        "attention_level": "URGENT",
        "attention_reason_codes": ["CONFLICT_OPEN"],
        "relationship_id": str(_relationship_id(actor)),
        "counterparty_display_name": "Northline Fiber",
        "last_activity_at": NOW,
        "headline": "A new invoice contradicts your recorded cancellation.",
        "case_type": "SERVICE_CANCELLATION",
        "opened_at": NOW - timedelta(days=26),
        "resolved_at": None,
        "reopened_count": 1,
    }


def _artifact(actor: Actor) -> dict[str, Any]:
    return {
        "artifact_id": str(actor.artifact_id),
        "source_type": "UPLOAD_PDF",
        "mime_type": "application/pdf",
        "filename": "northline-invoice-june.pdf",
        "size_bytes": 184213,
        "content_sha256": "3f" * 32,
        "sender_display": "billing@northlinebroadband.example",
        "recipient_display": None,
        "subject": "Invoice 88431",
        "source_message_id": None,
        "received_at": NOW,
        "event_time": None,
        "parser_status": "PARSED",
        "parser_version": "pdf-text-1",
        "parser_metadata": {"pages": 2},
        "content_blocks_summary": [],
        "evidence_item_count": 6,
        "linked_cases": [],
        "agent_run_id": str(actor.agent_run_id),
        "trace_id": str(actor.trace_id),
        "download_url": None,
        "download_url_expires_at": None,
    }


def _action_item(actor: Actor) -> dict[str, Any]:
    return {
        "action_intent_id": str(actor.action_intent_id),
        "case_id": str(actor.case_id),
        "case_title": f"{actor.name} case",
        "counterparty_display_name": "Northline Fiber",
        "action_type": "OUTBOUND_EMAIL_DISPUTE",
        "status": "NEEDS_REVIEW",
        "recipient_masked": "b•••••g@northlinebroadband.example",
        "subject_preview": "Disputed invoice 88431",
        "basis_case_revision": 13,
        "current_case_revision": 13,
        "is_stale": False,
        "warning_count": 0,
        "created_at": NOW,
        "created_by_agent_run_id": str(actor.agent_run_id),
    }


# --------------------------------------------------------------------------
# Write port
# --------------------------------------------------------------------------


@dataclass
class FakeWritePort:
    """Records every call so a test can assert the scope it was handed."""

    calls: list[tuple[str, OwnerScope, Any]] = field(default_factory=list)
    counter: int = 0

    def _next(self) -> uuid.UUID:
        self.counter += 1
        return _u(f"{self.counter:012x}")

    def _seen(self, name: str, scope: OwnerScope, payload: Any) -> Actor | None:
        self.calls.append((name, scope, payload))
        return _actor_for(scope)

    async def create_correction(
        self, scope: OwnerScope, case_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("create_correction", scope, payload)
        owner = _owner_of(case_id, "case_id")
        if not _owned(scope, owner):
            return None
        if payload.client_case_revision != 13:
            raise ApiError(
                ErrorCode.REVISION_CONFLICT,
                details={
                    "expected_revision": payload.client_case_revision,
                    "current_revision": 13,
                },
            )
        return {
            "correction_id": str(self._next()),
            "evidence_id": str(self._next()),
            "claim_id": str(self._next()),
            "kernel_result": {
                "decision": "ACCEPTED",
                "case_revision_before": 13,
                "case_revision_after": 14,
                "reason_codes": ["USER_CORRECTION_ACCEPTED"],
            },
        }

    async def upload_intent(self, scope: OwnerScope, payload: Any) -> dict[str, Any]:
        self._seen("upload_intent", scope, payload)
        artifact_id = _u("00000000aa01")
        key = f"raw/{scope.tenant_id}/{scope.user_id}/{artifact_id}/original"
        return {
            "artifact_id": str(artifact_id),
            "upload_url": f"https://s3.invalid/{key}?X-Amz-Signature=stub",
            "http_method": "PUT",
            "required_headers": {"Content-Type": payload.mime_type},
            "max_size_bytes": 20971520,
            "expires_at": NOW + timedelta(minutes=15),
            "s3_key": key,
        }

    async def complete_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("complete_artifact", scope, payload)
        owner = _owner_of(artifact_id, "artifact_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return {
            "artifact_id": str(artifact_id),
            "status": "QUEUED",
            "duplicate_of": None,
            "agent_run_id": str(owner.agent_run_id),
            "poll": {
                "artifact_url": f"/v1/artifacts/{artifact_id}",
                "trace_url": f"/v1/traces/{owner.trace_id}",
                "suggested_interval_ms": 1500,
            },
        }

    async def rotate_ingest_alias(self, scope: OwnerScope) -> dict[str, Any]:
        self._seen("rotate_ingest_alias", scope, None)
        return {
            "alias_display": "q2m8t5rb7c@in.provenance.app",
            "alias_token": "q2m8t5rb7c",
            "status": "ACTIVE",
            "previous_alias_status": "DISABLED",
            "rotated_at": NOW,
            "notice": "Mail sent to the previous address will be rejected from now on.",
        }

    async def update_draft(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("update_draft", scope, payload)
        owner = _owner_of(action_intent_id, "action_intent_id")
        if not _owned(scope, owner):
            return None
        return {
            "action_intent_id": str(action_intent_id),
            "status": "NEEDS_REVIEW",
            "draft_sha256": "c4" * 32,
            "previous_draft_sha256": "9a" * 32,
            "claims_revalidated": True,
            "warnings": [],
            "current_case_revision": 13,
        }

    async def approve(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("approve", scope, payload)
        owner = _owner_of(action_intent_id, "action_intent_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        if payload.client_case_revision != 13:
            raise ApiError(
                ErrorCode.ACTION_STALE,
                details={
                    "action_intent_id": str(action_intent_id),
                    "case_id": str(owner.case_id),
                    "stale_reason": "CASE_REVISION_ADVANCED",
                    "basis_case_revision": 13,
                    "client_case_revision": payload.client_case_revision,
                    "current_case_revision": 13,
                    "action_intent_status": "NEEDS_REVIEW",
                    "changed_since": [],
                    "superseded_support": [],
                    "draft_hash_matches": True,
                    "refresh": {},
                },
            )
        return {
            "action_intent_id": str(action_intent_id),
            "status": "APPROVED",
            "approval_draft_sha256": "9a" * 32,
            "approved_at": NOW,
            "approved_case_revision": 13,
            "case_revision_after": 14,
            "execution": {"status": "QUEUED", "outbox_event_id": str(self._next())},
        }

    async def reject(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("reject", scope, payload)
        owner = _owner_of(action_intent_id, "action_intent_id")
        if not _owned(scope, owner):
            return None
        return {
            "action_intent_id": str(action_intent_id),
            "status": "REJECTED",
            "rejected_at": NOW,
            "case_revision_after": 14,
        }

    async def start_counterfactual(self, scope: OwnerScope, payload: Any) -> dict[str, Any] | None:
        self._seen("start_counterfactual", scope, payload)
        owner = _owner_of(payload.artifact_id, "artifact_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        return {
            "counterfactual_id": str(owner.counterfactual_id),
            "status": "RUNNING",
            "artifact_id": str(payload.artifact_id),
            "poll_url": f"/v1/judge-mode/counterfactual/{owner.counterfactual_id}",
            "suggested_interval_ms": 1000,
        }

    async def get_counterfactual(
        self, scope: OwnerScope, counterfactual_id: uuid.UUID
    ) -> dict[str, Any] | None:
        self._seen("get_counterfactual", scope, counterfactual_id)
        owner = _owner_of(counterfactual_id, "counterfactual_id")
        if not _owned(scope, owner):
            return None
        assert owner is not None
        equal = {"off": "same", "on": "same", "equal": True}
        return {
            "counterfactual_id": str(counterfactual_id),
            "status": "COMPLETED",
            "artifact_id": str(owner.artifact_id),
            "artifact_summary": "Invoice 88431",
            "completed_at": NOW,
            "parity": {
                "artifact_id": equal,
                "artifact_sha256": equal,
                "model_id": equal,
                "prompt_version": equal,
                "graph_version": equal,
                "decode_params_sha256": equal,
                "all_equal": True,
            },
            "memory_off": {
                "mode": "MEMORY_OFF",
                "retrieval_enabled": False,
                "canonical_memory_enabled": False,
                "corpus_size_visible": 0,
                "model_id": "same",
                "duration_ms": 4120,
                "output": {"headline": "Invoice for $186 due 30 June."},
                "why": "Without retrieval, the artifact is self-describing.",
            },
            "memory_on": {
                "mode": "MEMORY_ON",
                "strategy": "REPLAY_COMMITTED",
                "retrieval_enabled": True,
                "canonical_memory_enabled": True,
                "corpus_size_visible": 16035,
                "model_id": "same",
                "duration_ms": 9420,
                "output": {"headline": "Contradicts your 15 May termination confirmation."},
            },
            "delta": {"verdict": "Memory OFF treated a contradiction as a routine bill."},
            "safety": {
                "memory_off_wrote_canonical_state": False,
                "memory_off_admitted_evidence": False,
                "memory_off_had_proposal_tool": False,
                "case_revision_changed_by_counterfactual": False,
            },
        }

    async def wake_trigger(
        self, scope: OwnerScope, trigger_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | None:
        self._seen("wake_trigger", scope, payload)
        owner = _owner_of(trigger_id, "trigger_id")
        if not _owned(scope, owner):
            return None
        return {
            "trigger_id": str(trigger_id),
            "result": "FIRED",
            "reason_code": "COMMITMENT_OVERDUE_UNPAID",
            "state": "FIRED",
            "evaluated_at": NOW,
        }

    async def run_probe(self, scope: OwnerScope, payload: Any) -> dict[str, Any]:
        self._seen("run_probe", scope, payload)
        return {"probe_id": str(self._next()), "status": "QUEUED", "probe_type": payload.probe_type}


# --------------------------------------------------------------------------
# Internal port
# --------------------------------------------------------------------------


@dataclass
class FakeInternalPort:
    calls: list[tuple[str, CapabilityBinding | None, Any]] = field(default_factory=list)

    def _seen(self, name: str, binding: CapabilityBinding | None, payload: Any) -> None:
        self.calls.append((name, binding, payload))

    async def ingest_artifact(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("ingest_artifact", binding, payload)
        return {
            "artifact_id": str(_u("00000000ab01")),
            "status": "QUEUED",
            "duplicate_of": None,
            "agent_run_id": str(_u("00000000ab02")),
        }

    async def agent_run(self, binding: CapabilityBinding) -> dict[str, Any] | None:
        self._seen("agent_run", binding, None)
        return {
            "agent_run_id": str(binding.binding_id),
            "graph_name": "ingestion_graph",
            "graph_version": "1.3.0",
            "input_artifact_id": str(binding.artifact_id) if binding.artifact_id else None,
            "allowed_case_ids": [str(c) for c in binding.allowed_case_ids] or None,
            "capability_expires_at": binding.expires_at,
            "model_route": {
                "tier_e": "anthropic.claude-haiku-4-5",
                "tier_r": "anthropic.claude-opus-5",
                "embeddings": "amazon.titan-embed-text-v2:0",
            },
            "limits": {"max_model_calls": 8, "max_tool_calls": 50, "max_repair_attempts": 1},
            "user_context": {"timezone": "America/New_York", "home_region": "US-NY"},
        }

    async def artifact_content(self, binding: CapabilityBinding, **_: Any) -> dict[str, Any] | None:
        self._seen("artifact_content", binding, None)
        return {
            "artifact_id": str(binding.artifact_id),
            "mime_type": "application/pdf",
            "parser_version": "pdf-text-1",
            "truncated": False,
            "content_blocks": [],
            "attachments": [],
        }

    async def register_evidence(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("register_evidence", binding, payload)
        return {
            "evidence": [
                {
                    "client_ref": c.client_ref,
                    "evidence_id": str(_u("00000000ac01")),
                    "created": True,
                    "source_authority": "0.4500",
                    "embedding_version": "v1",
                }
                for c in payload.candidates
            ],
            "created_count": len(payload.candidates),
            "deduplicated_count": 0,
        }

    async def retrieve(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("retrieve", binding, payload)
        return {
            "schema_version": "1.0",
            "relationship_candidates": [],
            "case_candidates": [],
            "current_beliefs": [],
            "evidence_snippets": [],
            "active_conflicts": [],
            "active_commitments": [],
            "unresolved_identity_questions": [],
            "retrieval_stats": {
                "corpus_size_user_scoped": 16035,
                "vector_candidates": 20,
                "after_rerank": 7,
                "exact_identifier_hits": 1,
                "retraction_filter_applied": True,
                "retracted_excluded": 2,
                "cross_user_results": 0,
                "vector_index": "evidence_embedding_ann_idx",
                "distance": "cosine",
                "duration_ms": 190,
            },
        }

    async def run_state_proof(
        self, binding: CapabilityBinding, case_id: uuid.UUID
    ) -> dict[str, Any] | None:
        self._seen("run_state_proof", binding, case_id)
        return {
            "state_proof": {
                "schema_version": "1.0",
                "case_id": str(case_id),
                "case_revision": 13,
                "case_status": "REOPENED",
                "generated_at": NOW,
                "deterministic": True,
                "model_used": None,
                "beliefs": [],
            },
            "advocacy_context": {
                "case_id": str(case_id),
                "case_revision": 13,
                "counterparty": {"display_name": "Northline Fiber", "kind": "ISP"},
                "current_case_state": "REOPENED",
                "action_policy": {
                    "supported_actions": ["OUTBOUND_EMAIL_DISPUTE"],
                    "recipient_allowlist_domains": ["northlinebroadband.example"],
                    "requires_human_approval": True,
                    "max_body_chars": 4000,
                    "prohibited": ["LEGAL_THREAT"],
                },
                "user_communication_preferences": {
                    "tone": "FIRM_POLITE",
                    "sign_off": "Alex Rivera",
                },
            },
        }

    async def submit_proposal(
        self, binding: CapabilityBinding, payload: Any, *, idempotency_key: str
    ) -> dict[str, Any]:
        self._seen("submit_proposal", binding, (payload, idempotency_key))
        return {
            "decision": "ACCEPTED_WITH_CONFLICT",
            "proposal_id": str(payload.proposal_id),
            "kernel_decision_id": str(_u("00000000ad01")),
            "case_id": str(binding.allowed_case_ids[0]) if binding.allowed_case_ids else None,
            "case_revision_before": 12,
            "case_revision_after": 13,
            "created_claims": [],
            "created_belief_versions": [],
            "created_or_updated_conflicts": [],
            "commitment_changes": [],
            "trigger_changes": [],
            "state_transitions": [],
            "outbox_event_ids": [],
            "attention_required": True,
            "retry_count": 0,
            "reason_codes": ["MUTUAL_EXCLUSION_DETECTED"],
        }

    async def create_action_intent(
        self, binding: CapabilityBinding, payload: Any
    ) -> dict[str, Any]:
        self._seen("create_action_intent", binding, payload)
        return {
            "action_intent_id": str(_u("00000000ae01")),
            "status": "NEEDS_REVIEW",
            "draft_sha256": "9a" * 32,
            "basis_case_revision": payload.basis_case_revision,
            "claims_validated": len(payload.draft.claims),
            "claims_unsupported": 0,
            "warnings": [],
            "outbox_event_ids": [],
        }

    async def complete_agent_run(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("complete_agent_run", binding, payload)
        return {
            "agent_run_id": str(binding.binding_id),
            "status": payload.status,
            "capability_status": "CONSUMED",
            "finished_at": NOW,
            "duration_ms": 9890,
        }

    async def evaluate_trigger(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("evaluate_trigger", binding, payload)
        return {
            "trigger_id": str(binding.binding_id),
            "result": "FIRED",
            "reason_code": "COMMITMENT_OVERDUE_UNPAID",
            "state": "FIRED",
            "evaluated_at": NOW,
            "case_id": str(binding.case_id),
            "case_revision_before": 5,
            "case_revision_after": 6,
            "basis_case_revision": 5,
            "field_values": {},
            "outbox_event_ids": [],
        }

    async def execute_action(self, binding: CapabilityBinding, payload: Any) -> dict[str, Any]:
        self._seen("execute_action", binding, payload)
        return {
            "action_intent_id": str(binding.binding_id),
            "action_execution_id": str(_u("00000000af01")),
            "attempt_no": 1,
            "status": "EXECUTED",
            "provider": "SES",
            "provider_correlation_id": "0100018f9f2a3b4c",
            "revalidation": {
                "case_revision": payload.expected_case_revision,
                "draft_hash_match": True,
                "support_still_current": True,
                "recipient_allowlisted": True,
            },
            "executed_at": NOW,
            "case_revision_after": 15,
            "outbox_event_ids": [],
        }

    async def sweep_outbox(self, payload: Any) -> dict[str, Any]:
        self._seen("sweep_outbox", None, payload)
        return {
            "claimed": 12,
            "dispatched": 11,
            "failed_retryable": 1,
            "dead": 0,
            "reaped_stale_claims": 0,
            "oldest_pending_age_seconds": 3,
            "duration_ms": 412,
            "worker_id": payload.worker_id,
        }

    async def deliver_event(self, payload: Any) -> dict[str, Any]:
        self._seen("deliver_event", None, payload)
        return {
            "result": "PROCESSED",
            "consumer_name": payload.consumer_name,
            "event_id": str(payload.event["event_id"]),
            "first_processed_at": None,
            "effect": {"kind": "AGENT_RUN_STARTED"},
        }


@dataclass(frozen=True)
class Fixture:
    users: FakeUserDirectory
    capabilities: FakeCapabilityStore
    read: FakeReadPort
    write: FakeWritePort
    internal: FakeInternalPort


def build_fixture() -> Fixture:
    return Fixture(
        users=FakeUserDirectory(),
        capabilities=FakeCapabilityStore(),
        read=FakeReadPort(),
        write=FakeWritePort(),
        internal=FakeInternalPort(),
    )
