"""T8.8 -- the thirteen `/internal/v1` routes.

Authority: `specs/15_API_SPEC.md` sections 9.1-9.13.

Two properties are asserted for every route: it is reachable only with a
workload token, and it takes its `tenant_id`/`user_id` from a server-side
capability row rather than from anything the caller sent.
"""

from __future__ import annotations

import uuid

import pytest
from _support import fakes as fakes_mod
from _support.fixtures import idem

pytestmark = pytest.mark.unit

ALEX = fakes_mod.ALEX


def _ses_body() -> dict[str, object]:
    return {
        "alias_hash": ALEX.alias_hash,
        "s3_bucket": "provenance-inbound-us-east-1",
        "s3_key": "ses/2026/06/05/0100018f9e70abcd-3f8a1c9d",
        "source_message_id": "<CAF=88431@mail.northlinebroadband.example>",
        "sender": "billing@northlinebroadband.example",
        "recipient": "n7k4q9wv2x@in.provenance.app",
        "subject": "Invoice 88431",
        "received_at": "2026-06-05T14:19:00Z",
        "size_bytes": 214882,
        "content_sha256": "3f" * 32,
        "ses_verdicts": {
            "spf": "PASS",
            "dkim": "PASS",
            "dmarc": "PASS",
            "spam": "PASS",
            "virus": "PASS",
        },
    }


def _proposal(case_id: uuid.UUID | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "agent_run_id": str(ALEX.agent_run_id),
        "proposal_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "user_id": str(ALEX.user_id),
        "proposal_type": "EVIDENCE_INTERPRETATION",
        "source_artifact_ids": [],
        "evidence_ids": [],
        "identity": {"case_id": str(case_id or ALEX.case_id), "confidence": "0.9600"},
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


# --------------------------------------------------------------------------
# 9.1 ingest
# --------------------------------------------------------------------------


def test_9_1_ses_ingest_resolves_the_user_from_the_alias(client, worker_headers, fixture) -> None:
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-ingest")},
        json=_ses_body(),
    )
    assert response.status_code in {200, 201}
    assert response.json()["status"] == "QUEUED"
    _, binding, _ = fixture.internal.calls[-1]
    assert binding.user_id == ALEX.user_id


def test_9_1_has_no_user_id_field_and_no_way_to_add_one(client, worker_headers) -> None:
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-user-id")},
        json={**_ses_body(), "user_id": str(fakes_mod.ROB.user_id)},
    )
    assert response.status_code == 422


def test_9_1_rejects_an_oversized_message(client, worker_headers) -> None:
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-too-big")},
        json={**_ses_body(), "size_bytes": 20971521},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_9_1_rejects_a_failed_virus_verdict_but_not_a_failed_spf(client, worker_headers) -> None:
    body = _ses_body()
    body["ses_verdicts"] = {**body["ses_verdicts"], "virus": "FAIL"}  # type: ignore[dict-item]
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-virus")},
        json=body,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "SES_VERDICT_FAIL"

    spoofed = _ses_body()
    spoofed["ses_verdicts"] = {**spoofed["ses_verdicts"], "spf": "FAIL"}  # type: ignore[dict-item]
    ok = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-spf")},
        json=spoofed,
    )
    assert ok.status_code in {200, 201}, "a spoofed sender is evidence, not a rejection"


def test_9_1_on_a_disabled_alias_is_409(client, worker_headers, fixture) -> None:
    fixture.capabilities.retire("INGEST_JOB", ALEX.alias_hash, "REVOKED")
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ALEX.alias_hash), **idem("ses-disabled")},
        json=_ses_body(),
    )
    assert response.status_code in {403, 409}


# --------------------------------------------------------------------------
# 9.2-9.6 agent-run scoped
# --------------------------------------------------------------------------


def test_9_2_the_bootstrap_response_contains_no_user_id(client, agent_headers) -> None:
    """Section 9.2: withholding the id removes the temptation to pass it, and
    the possibility of the model seeing and repeating it."""
    response = client.get(f"/internal/v1/agent-runs/{ALEX.agent_run_id}", headers=agent_headers())
    assert response.status_code == 200
    body = response.json()
    assert "user_id" not in body
    assert "tenant_id" not in body
    assert str(ALEX.user_id) not in response.text
    assert body["graph_name"] == "ingestion_graph"
    assert body["limits"]["max_tool_calls"] == 50


def test_9_3_artifact_content_takes_no_artifact_parameter(client, agent_headers, app) -> None:
    response = client.get(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/artifact-content",
        headers=agent_headers(),
    )
    assert response.status_code == 200
    assert response.json()["artifact_id"] == str(ALEX.artifact_id)
    paths = [r.path for r in app.routes if "artifact-content" in getattr(r, "path", "")]
    assert paths == ["/internal/v1/agent-runs/{agent_run_id}/artifact-content"]


def test_9_4_evidence_registration_requires_a_key(client, agent_headers) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/evidence",
        headers=agent_headers(),
        json={"schema_version": "1.0", "candidates": []},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


def test_9_4_registers_candidates(client, agent_headers) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/evidence",
        headers={**agent_headers(), **idem("evidence-ok")},
        json={
            "schema_version": "1.0",
            "candidates": [
                {
                    "client_ref": "c1",
                    "evidence_type": "INVOICE_LINE",
                    "block_id": "b2",
                    "exact_text": "Service period: 01 Jun 2026",
                    "normalized_text": "Invoice for service June.",
                    "observed_at": "2026-06-05T00:00:00Z",
                    "extraction_confidence": "0.9700",
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["created_count"] == 1


def test_9_4_refuses_a_caller_supplied_source_authority(client, agent_headers) -> None:
    """Section 9.4 step 3: 'the field does not exist in the request schema'."""
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/evidence",
        headers={**agent_headers(), **idem("evidence-authority")},
        json={
            "schema_version": "1.0",
            "candidates": [
                {
                    "client_ref": "c1",
                    "evidence_type": "INVOICE_LINE",
                    "block_id": "b2",
                    "exact_text": "x",
                    "normalized_text": "x",
                    "observed_at": "2026-06-05T00:00:00Z",
                    "extraction_confidence": "0.9700",
                    "source_authority": "1.0000",
                }
            ],
        },
    )
    assert response.status_code == 422


def test_9_5_retrieval_needs_no_idempotency_key_and_reports_zero_cross_user(
    client, agent_headers
) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/retrieval",
        headers=agent_headers(),
        json={"schema_version": "1.0", "top_k_vector": 20},
    )
    assert response.status_code == 200
    stats = response.json()["retrieval_stats"]
    assert stats["cross_user_results"] == 0
    assert stats["retraction_filter_applied"] is True


def test_9_6_state_proof_requires_a_case_id(client, agent_headers) -> None:
    response = client.get(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/state-proof", headers=agent_headers()
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY_PARAMETER"


def test_9_6_returns_the_advocacy_wrapper(client, agent_headers) -> None:
    response = client.get(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/state-proof?case_id={ALEX.case_id}",
        headers=agent_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"state_proof", "advocacy_context"}
    assert body["advocacy_context"]["action_policy"]["requires_human_approval"] is True


# --------------------------------------------------------------------------
# 9.7-9.9
# --------------------------------------------------------------------------


def test_9_7_is_the_only_path_into_the_kernel(client, agent_headers) -> None:
    response = client.post(
        "/internal/v1/memory/proposals",
        headers={**agent_headers(), **idem("proposal-ok")},
        json=_proposal(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "ACCEPTED_WITH_CONFLICT"
    assert body["case_revision_before"] == 12
    assert body["case_revision_after"] == 13


def test_9_7_requires_the_propose_scope(client, signing_key, capability_proof, fixture) -> None:
    from _support.tokens import agent_token

    token = agent_token(signing_key, scopes=("provenance.memory/read",))
    record = fixture.capabilities.records[("AGENT_RUN", str(ALEX.agent_run_id))]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Provenance-Capability-Proof": capability_proof(
            "AGENT_RUN", ALEX.agent_run_id, record.expires_at
        ),
        **idem("proposal-scope"),
    }
    response = client.post("/internal/v1/memory/proposals", headers=headers, json=_proposal())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    assert response.json()["error"]["details"]["required_scope"] == "provenance.memory/propose"


def test_9_7_rejects_an_agent_run_id_that_is_not_the_capability(client, agent_headers) -> None:
    body = _proposal()
    body["agent_run_id"] = str(fakes_mod.ROB.agent_run_id)
    response = client.post(
        "/internal/v1/memory/proposals",
        headers={**agent_headers(), **idem("proposal-run-swap")},
        json=body,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_SCOPE_MISMATCH"


def test_9_8_creates_an_intent_that_cannot_act(client, agent_headers) -> None:
    response = client.post(
        "/internal/v1/advocacy/action-intents",
        headers={**agent_headers(), **idem("advocacy-ok")},
        json={
            "schema_version": "1.0",
            "agent_run_id": str(ALEX.agent_run_id),
            "case_id": str(ALEX.case_id),
            "basis_case_revision": 13,
            "action_type": "OUTBOUND_EMAIL_DISPUTE",
            "recipient": "billing@northlinebroadband.example",
            "draft": {
                "subject": "Disputed invoice 88431",
                "body": "Hello,\n\nPlease cancel invoice 88431.",
                "claims": [],
                "requested_outcome": "CANCEL_INVOICE_AND_CONFIRM_CLOSURE",
                "tone": "FIRM_POLITE",
                "unresolved_risks": [],
            },
            "rationale": "A counterparty claim contradicts a written confirmation.",
            "supporting_belief_versions": [],
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] in {"PROPOSED", "NEEDS_REVIEW"}


def test_9_8_requires_the_action_propose_scope(
    client, signing_key, capability_proof, fixture
) -> None:
    from _support.tokens import agent_token

    token = agent_token(signing_key, scopes=("provenance.memory/read",))
    record = fixture.capabilities.records[("AGENT_RUN", str(ALEX.agent_run_id))]
    response = client.post(
        "/internal/v1/advocacy/action-intents",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Provenance-Capability-Proof": capability_proof(
                "AGENT_RUN", ALEX.agent_run_id, record.expires_at
            ),
            **idem("advocacy-scope"),
        },
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


def test_9_9_burns_the_capability(client, agent_headers, fixture) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/complete",
        headers={**agent_headers(), **idem("complete-run")},
        json={"status": "SUCCEEDED", "error_code": None, "tool_calls": [], "model_calls": []},
    )
    assert response.status_code == 200
    assert response.json()["capability_status"] == "CONSUMED"


def test_9_9_uses_tool_calls_not_mcp_tool_calls_as_the_request_field(client, agent_headers) -> None:
    """The column is `agent_runs.tool_calls`; `agent_runs.mcp_tool_calls` is
    not a column name. Section 9.9's request field is `tool_calls`."""
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/complete",
        headers={**agent_headers(), **idem("complete-mcp-name")},
        json={
            "status": "SUCCEEDED",
            "mcp_tool_calls": [],
            "model_calls": [],
        },
    )
    assert response.status_code == 422


def test_9_9_rejects_a_tool_call_key_outside_the_allowlist(client, agent_headers) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/complete",
        headers={**agent_headers(), **idem("complete-smuggle")},
        json={
            "status": "SUCCEEDED",
            "tool_calls": [
                {
                    "sequence": 1,
                    "mcp_server": "cockroachdb-mcp",
                    "tool_name": "query_agent_case_context",
                    "view_name": "agent_case_context_v1",
                    "sql_role": "pv_agent_reader",
                    "access_mode": "READ_ONLY",
                    "filter_summary": "user_id = <run user>",
                    "rows_returned": 1,
                    "duration_ms": 44,
                    "denied": False,
                    "returned_rows": [{"secret": "value"}],
                }
            ],
            "model_calls": [],
        },
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# 9.10-9.13
# --------------------------------------------------------------------------


def test_9_10_evaluates_a_trigger(client, worker_headers) -> None:
    response = client.post(
        f"/internal/v1/triggers/{ALEX.trigger_id}/evaluate",
        headers={
            **worker_headers("TRIGGER_EVALUATION", ALEX.trigger_id),
            **idem("trigger-eval"),
        },
        json={
            "scheduled_for": "2026-05-31T00:00:00Z",
            "schedule_name": "provenance-trigger-a005",
            "evaluation_version": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] in {"FIRED", "NO_OP", "DISARMED", "EXPIRED", "ERROR"}


def test_9_10_requires_the_trigger_scope(client, signing_key, capability_proof, fixture) -> None:
    from _support.tokens import worker_token

    token = worker_token(signing_key, scopes=("provenance.outbox/dispatch",))
    record = fixture.capabilities.records[("TRIGGER_EVALUATION", str(ALEX.trigger_id))]
    response = client.post(
        f"/internal/v1/triggers/{ALEX.trigger_id}/evaluate",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Provenance-Capability-Proof": capability_proof(
                "TRIGGER_EVALUATION", ALEX.trigger_id, record.expires_at
            ),
            **idem("trigger-scope"),
        },
        json={"scheduled_for": "2026-05-31T00:00:00Z", "evaluation_version": 1},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


def test_9_11_execute_is_the_only_route_with_an_external_effect(client, worker_headers) -> None:
    response = client.post(
        f"/internal/v1/actions/{ALEX.action_intent_id}/execute",
        headers={
            **worker_headers("ACTION_INTENT", ALEX.action_intent_id),
            **idem("action-execute"),
        },
        json={"expected_draft_sha256": "9a" * 32, "expected_case_revision": 14},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "EXECUTED"
    assert body["revalidation"]["draft_hash_match"] is True


def test_9_11_is_unreachable_by_the_agent_runtime_scope_set(
    client, agent_bearer, capability_proof, fixture
) -> None:
    """Section 2.1: the graph that drafts an outbound letter is structurally
    incapable of sending it."""
    record = fixture.capabilities.records[("ACTION_INTENT", str(ALEX.action_intent_id))]
    response = client.post(
        f"/internal/v1/actions/{ALEX.action_intent_id}/execute",
        headers={
            "Authorization": f"Bearer {agent_bearer}",
            "X-Provenance-Capability-Proof": capability_proof(
                "ACTION_INTENT", ALEX.action_intent_id, record.expires_at
            ),
            **idem("action-agent-try"),
        },
        json={"expected_draft_sha256": "9a" * 32, "expected_case_revision": 14},
    )
    assert response.status_code == 403


def test_9_12_sweep_is_service_level_and_returns_counts_only(client, worker_bearer) -> None:
    response = client.post(
        "/internal/v1/events/outbox/sweep",
        headers={"Authorization": f"Bearer {worker_bearer}"},
        json={"batch_size": 100, "max_batches": 5, "worker_id": "outbox-dispatch-1a2b3c"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "claimed",
        "dispatched",
        "failed_retryable",
        "dead",
        "reaped_stale_claims",
        "oldest_pending_age_seconds",
        "duration_ms",
        "worker_id",
    }


def test_9_12_rejects_an_out_of_range_batch_size(client, worker_bearer) -> None:
    response = client.post(
        "/internal/v1/events/outbox/sweep",
        headers={"Authorization": f"Bearer {worker_bearer}"},
        json={"batch_size": 501, "max_batches": 5, "worker_id": "w"},
    )
    assert response.status_code == 422


def test_9_13_dedupes_on_event_id_without_an_idempotency_key(client, worker_bearer) -> None:
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "event_type": "case.reopened.v1",
        "aggregate_type": "CASE",
        "aggregate_id": str(ALEX.case_id),
        "aggregate_version": 13,
        "tenant_id": str(ALEX.tenant_id),
        "user_id": str(ALEX.user_id),
        "trace_id": str(ALEX.trace_id),
        "occurred_at": "2026-06-05T14:19:12.470Z",
        "payload": {"case_id": str(ALEX.case_id)},
    }
    response = client.post(
        "/internal/v1/events/deliveries",
        headers={"Authorization": f"Bearer {worker_bearer}"},
        json={"consumer_name": "advocate_dispatch", "event": event},
    )
    assert response.status_code == 200
    assert response.json()["result"] in {
        "PROCESSED",
        "DUPLICATE_NOOP",
        "SKIPPED_STALE",
        "FAILED",
    }


def test_9_13_rejects_an_unknown_consumer(client, worker_bearer) -> None:
    response = client.post(
        "/internal/v1/events/deliveries",
        headers={"Authorization": f"Bearer {worker_bearer}"},
        json={"consumer_name": "not_a_consumer", "event": {}},
    )
    assert response.status_code == 422
