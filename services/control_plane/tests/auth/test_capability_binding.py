"""T8.3 -- capability objects instead of a caller-supplied `user_id`.

Authority: `specs/15_API_SPEC.md` section 3 in full. Feeds `G8.5`.

Section 3.1 names three failures a stricter token check cannot prevent,
because the token is valid in all three. The defence is that there is no field
in which a caller can name a user: `InternalPrincipal` (11_CONTRACTS.md
section 7) has no `tenant_id` and no `user_id`, `extra="forbid"` stops one
being added at runtime, and ownership is reachable only through
`require_binding()`, which fails closed.

Recorded discrepancy, not resolved here
---------------------------------------
`15_API_SPEC.md` section 3.4 names the capability kinds
`AGENT_RUN | TRIGGER | ACTION_INTENT | ARTIFACT | INGEST_ALIAS`;
`11_CONTRACTS.md` section 7 -- which T1.5 implemented and which is green --
names `AGENT_RUN | ACTION_INTENT | TRIGGER_EVALUATION | INGEST_JOB`. The
implemented vocabulary is used throughout. No route in section 9.0 presents an
`ARTIFACT` capability, so nothing here needed the fifth kind.
"""

from __future__ import annotations

import ast
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from _support import fakes as fakes_mod
from pydantic import ValidationError

from provenance_contracts.identity import AuthorizationError, InternalPrincipal
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth.capabilities import (
    CLIENT_CAPABILITY_MATRIX,
    NOT_FOUND_CODE_BY_KIND,
    assert_within_capability,
)
from services.control_plane.app.auth.capability_proof import (
    issue_capability_proof,
    verify_capability_proof,
)

pytestmark = pytest.mark.unit

KEY = b"capability-key-for-tests-only-not-a-secret"


# --------------------------------------------------------------------------
# The type is the control
# --------------------------------------------------------------------------


def test_internal_principal_has_no_tenant_or_user_field_at_all() -> None:
    assert "tenant_id" not in InternalPrincipal.model_fields
    assert "user_id" not in InternalPrincipal.model_fields


def test_internal_principal_forbids_extra_fields_at_runtime() -> None:
    with pytest.raises(ValidationError):
        InternalPrincipal(
            app_client="provenance-agent-runtime",
            workload="AGENT_RUNTIME",
            scopes=frozenset({"provenance.memory/read"}),
            token_issued_at="2026-06-05T14:00:00Z",
            token_expires_at="2026-06-05T15:00:00Z",
            request_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),  # type: ignore[call-arg]
        )


def test_require_binding_fails_closed_when_no_capability_was_presented() -> None:
    principal = InternalPrincipal(
        app_client="provenance-agent-runtime",
        workload="AGENT_RUNTIME",
        scopes=frozenset({"provenance.memory/read"}),
        token_issued_at="2026-06-05T14:00:00Z",
        token_expires_at="2026-06-05T15:00:00Z",
        request_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )
    with pytest.raises(AuthorizationError):
        principal.require_binding()


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------


def test_the_agent_runtime_may_only_ever_present_an_agent_run() -> None:
    assert CLIENT_CAPABILITY_MATRIX["provenance-agent-runtime"] == frozenset({"AGENT_RUN"})


def test_the_workers_client_may_present_the_other_three_kinds() -> None:
    assert CLIENT_CAPABILITY_MATRIX["provenance-workers"] == frozenset(
        {"TRIGGER_EVALUATION", "ACTION_INTENT", "INGEST_JOB"}
    )


def test_every_kind_maps_to_a_typed_not_found_code() -> None:
    assert NOT_FOUND_CODE_BY_KIND == {
        "AGENT_RUN": ErrorCode.AGENT_RUN_NOT_FOUND,
        "ACTION_INTENT": ErrorCode.ACTION_INTENT_NOT_FOUND,
        "TRIGGER_EVALUATION": ErrorCode.TRIGGER_NOT_FOUND,
        "INGEST_JOB": ErrorCode.INGEST_ALIAS_NOT_FOUND,
    }


# --------------------------------------------------------------------------
# The proof header
# --------------------------------------------------------------------------


def test_a_proof_round_trips() -> None:
    expires = fakes_mod.NOW + timedelta(minutes=15)
    proof = issue_capability_proof("AGENT_RUN", fakes_mod.ALEX.agent_run_id, expires, key=KEY)
    verify_capability_proof("AGENT_RUN", fakes_mod.ALEX.agent_run_id, expires, proof, key=KEY)


def test_a_proof_for_another_capability_id_does_not_verify() -> None:
    expires = fakes_mod.NOW + timedelta(minutes=15)
    proof = issue_capability_proof("AGENT_RUN", fakes_mod.ALEX.agent_run_id, expires, key=KEY)
    with pytest.raises(ApiError) as excinfo:
        verify_capability_proof("AGENT_RUN", fakes_mod.ROB.agent_run_id, expires, proof, key=KEY)
    assert excinfo.value.code is ErrorCode.CAPABILITY_PROOF_INVALID


def test_a_missing_proof_is_rejected() -> None:
    expires = fakes_mod.NOW + timedelta(minutes=15)
    with pytest.raises(ApiError):
        verify_capability_proof("AGENT_RUN", fakes_mod.ALEX.agent_run_id, expires, None, key=KEY)


def test_a_proof_is_bound_to_the_kind() -> None:
    expires = fakes_mod.NOW + timedelta(minutes=15)
    proof = issue_capability_proof("AGENT_RUN", fakes_mod.ALEX.agent_run_id, expires, key=KEY)
    with pytest.raises(ApiError):
        verify_capability_proof(
            "ACTION_INTENT", fakes_mod.ALEX.agent_run_id, expires, proof, key=KEY
        )


# --------------------------------------------------------------------------
# G8.5 over HTTP
# --------------------------------------------------------------------------


def _proposal(agent_run_id: uuid.UUID, user_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "agent_run_id": str(agent_run_id),
        "proposal_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "proposal_type": "EVIDENCE_INTERPRETATION",
        "source_artifact_ids": [],
        "evidence_ids": [],
        "identity": {"case_id": str(case_id), "confidence": "0.9600"},
        "claims": [],
        "commitments": [],
        "belief_mutations": [],
        "conflict_hints": [],
        "trigger_mutations": [],
        "unresolved_questions": [],
        # Section 9.7's request body has always printed a `model` block; the
        # schema carries one since `internal.submit_proposal` was bound. The
        # ids are the two the live `agent_runs.model_route` rows record, and
        # `proposals/submission.py::resolve_attribution` compares them against
        # that column -- so a body naming a model the run was not routed on is
        # a refusal rather than a claim the row would then repeat.
        "model": {
            "provider": "gemini",
            "model_id": "gemini-3.5-flash-lite",
            "tier": "E",
            "prompt_version": "pv-extract-1.1.0",
        },
    }


def test_g8_5_a_proposal_naming_another_user_is_403(client, agent_headers) -> None:
    body = _proposal(fakes_mod.ALEX.agent_run_id, fakes_mod.ROB.user_id, fakes_mod.ALEX.case_id)
    response = client.post(
        "/internal/v1/memory/proposals",
        headers={**agent_headers(), "Idempotency-Key": "pv-cap-mismatch-000"},
        json=body,
    )
    assert response.status_code == 403
    detail = response.json()["error"]
    assert detail["code"] == "CAPABILITY_SCOPE_MISMATCH"
    assert detail["details"]["field"] == "user_id"
    assert detail["details"]["reason"] == "PAYLOAD_USER_MISMATCH"
    assert str(fakes_mod.ROB.user_id) not in str(detail), "the rejected id is not echoed back"


def test_g8_5_a_proposal_against_a_completed_run_is_refused(client, agent_headers, fixture) -> None:
    fixture.capabilities.retire("AGENT_RUN", str(fakes_mod.ALEX.agent_run_id), "CONSUMED")
    response = client.post(
        "/internal/v1/memory/proposals",
        headers={**agent_headers(), "Idempotency-Key": "pv-cap-consumed-00"},
        json=_proposal(fakes_mod.ALEX.agent_run_id, fakes_mod.ALEX.user_id, fakes_mod.ALEX.case_id),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_CONSUMED"


def test_a_revoked_capability_is_named_as_revoked(client, agent_headers, fixture) -> None:
    fixture.capabilities.retire("AGENT_RUN", str(fakes_mod.ALEX.agent_run_id), "REVOKED")
    response = client.get(
        f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}", headers=agent_headers()
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_REVOKED"


def test_an_expired_capability_is_refused(client, agent_headers, fixture, capability_proof) -> None:
    key = ("AGENT_RUN", str(fakes_mod.ALEX.agent_run_id))
    record = fixture.capabilities.records[key]
    expired = record.with_expiry(fakes_mod.NOW - timedelta(minutes=1))
    fixture.capabilities.records[key] = expired
    headers = agent_headers() | {
        "X-Provenance-Capability-Proof": capability_proof(
            "AGENT_RUN", fakes_mod.ALEX.agent_run_id, expired.expires_at
        )
    }
    response = client.get(f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_EXPIRED"


def test_an_unknown_capability_id_is_404_not_403(client, agent_bearer, capability_proof) -> None:
    """Section 3.4: indistinguishable from 'belongs to another tenant'. Deliberate."""
    unknown = uuid.uuid4()
    headers = {
        "Authorization": f"Bearer {agent_bearer}",
        "X-Provenance-Capability-Proof": capability_proof(
            "AGENT_RUN", unknown, fakes_mod.NOW + timedelta(minutes=15)
        ),
    }
    response = client.get(f"/internal/v1/agent-runs/{unknown}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_RUN_NOT_FOUND"


def test_the_agent_runtime_cannot_present_an_action_intent(
    client, agent_bearer, capability_proof, fixture
) -> None:
    record = fixture.capabilities.records[("ACTION_INTENT", str(fakes_mod.ALEX.action_intent_id))]
    headers = {
        "Authorization": f"Bearer {agent_bearer}",
        "X-Provenance-Capability-Proof": capability_proof(
            "ACTION_INTENT", fakes_mod.ALEX.action_intent_id, record.expires_at
        ),
        "Idempotency-Key": "pv-matrix-block-000",
    }
    response = client.post(
        f"/internal/v1/actions/{fakes_mod.ALEX.action_intent_id}/execute",
        headers=headers,
        json={"expected_draft_sha256": "9a" * 32, "expected_case_revision": 14},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] in {
        "INSUFFICIENT_SCOPE",
        "CAPABILITY_SCOPE_MISMATCH",
    }


def test_a_case_outside_allowed_case_ids_is_refused(client, agent_headers) -> None:
    body = _proposal(fakes_mod.ALEX.agent_run_id, fakes_mod.ALEX.user_id, fakes_mod.ROB.case_id)
    response = client.post(
        "/internal/v1/memory/proposals",
        headers={**agent_headers(), "Idempotency-Key": "pv-case-outside-00"},
        json=body,
    )
    assert response.status_code == 403
    detail = response.json()["error"]["details"]
    assert detail["field"] == "case_id"
    assert detail["reason"] == "CASE_OUTSIDE_CAPABILITY"


def test_a_wrong_capability_proof_is_refused(client, agent_bearer) -> None:
    response = client.get(
        f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}",
        headers={
            "Authorization": f"Bearer {agent_bearer}",
            "X-Provenance-Capability-Proof": "not-the-right-mac",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_PROOF_INVALID"


# --------------------------------------------------------------------------
# The structural assertion
# --------------------------------------------------------------------------


def test_no_internal_request_model_declares_a_user_id_or_tenant_id_field() -> None:
    """T8.3: "A comment saying 'do not pass user_id' is not a boundary."

    `MemoryProposalRequest.user_id` is the single exception, and it is
    permitted only because section 3.6 defines it as an assertion that is
    compared and then discarded. It is listed by name so that a second one
    cannot appear quietly.
    """
    allowed = {("MemoryProposalRequest", "user_id")}
    schema_root = Path(
        __import__("services.control_plane.app.api.schemas", fromlist=["internal"]).__file__
    ).parent
    source = (schema_root / "internal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id in {"user_id", "tenant_id"}
            ):
                pair = (node.name, stmt.target.id)
                if pair not in allowed:
                    offenders.append(pair)
    assert offenders == []


def test_assert_within_capability_refuses_a_mismatched_user() -> None:
    from provenance_contracts.identity import CapabilityBinding

    binding = CapabilityBinding(
        binding_id=fakes_mod.ALEX.agent_run_id,
        binding_kind="AGENT_RUN",
        tenant_id=fakes_mod.ALEX.tenant_id,
        user_id=fakes_mod.ALEX.user_id,
        allowed_case_ids=(fakes_mod.ALEX.case_id,),
        expires_at=fakes_mod.NOW + timedelta(minutes=15),
        status="ACTIVE",
    )
    assert_within_capability(binding, claimed_user_id=fakes_mod.ALEX.user_id)
    with pytest.raises(ApiError) as excinfo:
        assert_within_capability(binding, claimed_user_id=fakes_mod.ROB.user_id)
    assert excinfo.value.code is ErrorCode.CAPABILITY_SCOPE_MISMATCH
