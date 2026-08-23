"""T8.5, T8.6, T8.7 -- the public `/v1` surface.

Authority: `specs/15_API_SPEC.md` sections 8.1-8.31.

`/v1/version` is asserted hardest because it is the operating-mode disclosure
channel: `fixture_mode` lives there and nowhere else on the unauthenticated
surface, and the field is `git_sha` -- `build_sha` is an environment variable
name, not a response field.
"""

from __future__ import annotations

import uuid

import pytest
from _support import fakes as fakes_mod
from _support.fixtures import idem

pytestmark = pytest.mark.unit

ALEX = fakes_mod.ALEX


# --------------------------------------------------------------------------
# 8.1 / 8.2 -- unauthenticated
# --------------------------------------------------------------------------


def test_healthz_is_a_bare_liveness_probe(client) -> None:
    response = client.get("/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_never_carries_fixture_mode(client) -> None:
    """Section 8.2: conflating the two is how an undisclosed fixture-mode demo
    happens."""
    body = client.get("/v1/healthz").json()
    assert "fixture_mode" not in body
    assert "agent_mode" not in body
    assert "db_ok" not in body


def test_healthz_needs_no_token(client) -> None:
    assert client.get("/v1/healthz").status_code == 200


def test_version_is_unauthenticated_so_a_judge_can_curl_it(client) -> None:
    assert client.get("/v1/version").status_code == 200


def test_version_carries_the_five_operating_mode_fields(client) -> None:
    body = client.get("/v1/version").json()
    for field in ("fixture_mode", "agent_mode", "otlp_export", "schema_revision", "db_ok"):
        assert field in body, field
    assert body["fixture_mode"] is False
    assert body["agent_mode"] == "LIVE"
    assert body["otlp_export"] == "ENABLED"
    assert body["db_ok"] is True
    assert body["schema_revision"] == "0008_events_infrastructure"


def test_the_field_is_git_sha_and_build_sha_is_not_a_field_name(client) -> None:
    body = client.get("/v1/version").json()
    assert body["git_sha"] == "9c1f2ad"
    assert "build_sha" not in body


def test_version_names_the_service_and_api_version(client) -> None:
    body = client.get("/v1/version").json()
    assert body["service"] == "provenance-control-plane"
    assert body["api_version"] == "v1"
    assert body["contracts_schema_version"] == "1.0"


# --------------------------------------------------------------------------
# 8.3 -- /v1/me
# --------------------------------------------------------------------------


def test_me_mirrors_fixture_mode_from_the_version_endpoint(client, auth_alex) -> None:
    version = client.get("/v1/version").json()
    me = client.get("/v1/me", headers=auth_alex).json()
    assert me["feature_flags"]["fixture_mode"] == version["fixture_mode"]


def test_me_returns_the_resolved_identity(client, auth_alex) -> None:
    body = client.get("/v1/me", headers=auth_alex).json()
    assert body["user_id"] == str(ALEX.user_id)
    assert body["tenant_id"] == str(ALEX.tenant_id)
    assert body["timezone"] == "America/New_York"
    assert body["judge_mode_enabled"] is True
    assert body["ingest_alias_status"] == "ACTIVE"


def test_me_takes_no_user_id_parameter(client, auth_alex) -> None:
    response = client.get(f"/v1/me?user_id={fakes_mod.ROB.user_id}", headers=auth_alex)
    assert response.status_code in {200, 400}
    if response.status_code == 200:
        assert response.json()["user_id"] == str(ALEX.user_id)


# --------------------------------------------------------------------------
# 8.4 -- dashboard
# --------------------------------------------------------------------------


def test_dashboard_returns_the_six_counts(client, auth_alex) -> None:
    body = client.get("/v1/dashboard", headers=auth_alex).json()
    assert set(body["counts"]) == {
        "unresolved_commitments",
        "active_conflicts",
        "action_intents_pending",
        "cases_needing_attention",
        "triggers_armed",
        "triggers_fired_unhandled",
    }
    assert "generated_at" in body


def test_an_unknown_status_filter_is_a_named_400(client, auth_alex) -> None:
    response = client.get("/v1/dashboard?status=NOT_A_STATUS", headers=auth_alex)
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_QUERY_PARAMETER"
    assert body["error"]["details"]["parameter"] == "status"
    assert "allowed" in body["error"]["details"]


# --------------------------------------------------------------------------
# The read surface, endpoint by endpoint
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/contexts",
        "/v1/relationships",
        "/v1/cases",
        "/v1/commitments",
        "/v1/triggers",
        "/v1/artifacts",
        "/v1/action-intents",
    ],
)
def test_every_collection_is_paginated(client, auth_alex, path: str) -> None:
    body = client.get(path, headers=auth_alex).json()
    assert set(body) == {"items", "page"}


def test_case_detail_carries_the_revision_header_and_field(client, auth_alex) -> None:
    response = client.get(f"/v1/cases/{ALEX.case_id}", headers=auth_alex)
    assert response.status_code == 200
    assert response.headers["x-provenance-case-revision"] == "13"
    assert response.json()["revision"] == 13


def test_the_timeline_envelope_is_the_section_8_10_shape(client, auth_alex) -> None:
    body = client.get(f"/v1/cases/{ALEX.case_id}/timeline", headers=auth_alex).json()
    item = body["items"][0]
    assert set(item) >= {
        "id",
        "kind",
        "occurred_at",
        "case_revision",
        "trace_id",
        "actor",
        "headline",
        "detail",
    }
    assert item["actor"]["type"] in {
        "USER",
        "COUNTERPARTY",
        "KERNEL",
        "AGENT",
        "SCHEDULER",
        "EXECUTOR",
        "SYSTEM",
    }


def test_the_state_proof_is_returned_deterministically(client, auth_alex) -> None:
    response = client.get(f"/v1/cases/{ALEX.case_id}/state-proof", headers=auth_alex)
    assert response.status_code == 200
    body = response.json()
    assert body["deterministic"] is True
    assert body["model_used"] is None
    assert body["schema_version"] == "1.0"
    assert response.headers["x-provenance-case-revision"] == "13"


def test_state_proof_rejects_an_out_of_range_evidence_bound(client, auth_alex) -> None:
    response = client.get(
        f"/v1/cases/{ALEX.case_id}/state-proof?max_evidence_per_belief=99", headers=auth_alex
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY_PARAMETER"


def test_the_belief_detail_route_answers(client, auth_alex) -> None:
    body = client.get(f"/v1/beliefs/{ALEX.belief_id}", headers=auth_alex).json()
    assert body["belief_id"] == str(ALEX.belief_id)
    assert body["case_id"] == str(ALEX.case_id)


def test_commitments_report_overdue_at_read_time(client, auth_alex) -> None:
    item = client.get("/v1/commitments", headers=auth_alex).json()["items"][0]
    assert item["overdue"] is True
    assert item["days_overdue"] == 5
    assert item["outstanding_amount"] == {"currency": "USD", "amount": "1800.0000"}


def test_money_is_never_a_json_number(client, auth_alex) -> None:
    item = client.get("/v1/commitments", headers=auth_alex).json()["items"][0]
    for field in ("committed_amount", "fulfilled_amount", "outstanding_amount"):
        assert isinstance(item[field]["amount"], str), field


def test_triggers_expose_the_predicate_ast(client, auth_alex) -> None:
    item = client.get("/v1/triggers", headers=auth_alex).json()["items"][0]
    assert "predicate_ast" in item
    assert "predicate_summary" in item


# --------------------------------------------------------------------------
# 8.14 -- corrections
# --------------------------------------------------------------------------


def test_a_correction_returns_the_kernel_result(client, auth_alex) -> None:
    response = client.post(
        f"/v1/cases/{ALEX.case_id}/corrections",
        headers={**auth_alex, **idem("correction-ok")},
        json={
            "correction_type": "BELIEF_INCORRECT",
            "statement": "I cancelled on 15 May and they confirmed it.",
            "affected_belief_id": str(ALEX.belief_id),
            "client_case_revision": 13,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kernel_result"]["case_revision_before"] == 13
    assert body["kernel_result"]["case_revision_after"] == 14


def test_a_correction_with_the_wrong_target_is_422(client, auth_alex) -> None:
    response = client.post(
        f"/v1/cases/{ALEX.case_id}/corrections",
        headers={**auth_alex, **idem("correction-target")},
        json={
            "correction_type": "BELIEF_INCORRECT",
            "statement": "no belief named",
            "client_case_revision": 13,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CORRECTION_TARGET_INVALID"


def test_a_stale_client_revision_is_409_revision_conflict(client, auth_alex) -> None:
    response = client.post(
        f"/v1/cases/{ALEX.case_id}/corrections",
        headers={**auth_alex, **idem("correction-stale")},
        json={
            "correction_type": "CONFIRM_BELIEF",
            "statement": "still true",
            "affected_belief_id": str(ALEX.belief_id),
            "client_case_revision": 11,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"


# --------------------------------------------------------------------------
# 8.18-8.22 -- artifacts and the ingest alias
# --------------------------------------------------------------------------


def test_upload_intent_returns_a_server_chosen_key(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("upload-ok")},
        json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 184213},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["http_method"] == "PUT"
    assert body["s3_key"].endswith("/original")
    assert body["max_size_bytes"] == 20971520


@pytest.mark.parametrize(
    "mime",
    ["application/pdf", "image/png", "image/jpeg", "text/plain", "message/rfc822"],
)
def test_the_mime_allowlist_is_exactly_the_five(client, auth_alex, mime: str) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem(f"mime-{mime.replace('/', '-')}")},
        json={"filename": "a.bin", "mime_type": mime, "size_bytes": 10},
    )
    assert response.status_code == 201


def test_an_executable_mime_type_is_refused(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("mime-exe")},
        json={
            "filename": "a.exe",
            "mime_type": "application/x-msdownload",
            "size_bytes": 10,
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED_MIME_TYPE"
    assert "allowed" in body["error"]["details"]


def test_an_oversized_declared_upload_is_413(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("upload-too-big")},
        json={
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 20971521,
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_complete_returns_the_poll_block(client, auth_alex) -> None:
    response = client.post(
        f"/v1/artifacts/{ALEX.artifact_id}/complete",
        headers={**auth_alex, **idem("complete-ok")},
        json={},
    )
    assert response.status_code in {200, 202}
    body = response.json()
    assert body["status"] in {"QUEUED", "PROCESSING", "DUPLICATE"}
    assert body["poll"]["suggested_interval_ms"] == 1500


def test_the_ingest_alias_never_returns_the_token_on_read(client, auth_alex) -> None:
    body = client.get("/v1/ingest-alias", headers=auth_alex).json()
    assert "alias_token" not in body
    assert "alias_hash" not in body
    assert body["status"] == "ACTIVE"


def test_rotation_returns_the_token_exactly_once(client, auth_alex) -> None:
    response = client.post(
        "/v1/ingest-alias/rotate", headers={**auth_alex, **idem("rotate-ok")}, json={}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["alias_token"]
    assert body["previous_alias_status"] == "DISABLED"
    assert "alias_token" not in client.get("/v1/ingest-alias", headers=auth_alex).json()


# --------------------------------------------------------------------------
# 8.23-8.27 -- action intents
# --------------------------------------------------------------------------


def test_the_intent_list_computes_is_stale_per_item(client, auth_alex) -> None:
    item = client.get("/v1/action-intents", headers=auth_alex).json()["items"][0]
    assert item["is_stale"] is False
    assert item["basis_case_revision"] == item["current_case_revision"]


def test_the_intent_detail_carries_the_draft_and_its_hash(client, auth_alex) -> None:
    body = client.get(f"/v1/action-intents/{ALEX.action_intent_id}", headers=auth_alex).json()
    assert body["draft"]["subject"]
    assert len(body["draft_sha256"]) == 64
    assert body["approval"] is None


def test_the_recipient_is_not_editable(client, auth_alex) -> None:
    """Section 8.25: changing the recipient changes the blast radius after
    grounding validation ran. A different recipient requires a new intent."""
    response = client.put(
        f"/v1/action-intents/{ALEX.action_intent_id}/draft",
        headers={**auth_alex, **idem("draft-recipient")},
        json={
            "subject": "s",
            "body": "b",
            "client_case_revision": 13,
            "recipient": "attacker@example.com",
        },
    )
    assert response.status_code == 422
    reasons = {f["reason"] for f in response.json()["error"]["details"]["fields"]}
    assert "extra_forbidden" in reasons


def test_approval_returns_both_revisions(client, auth_alex) -> None:
    response = client.post(
        f"/v1/action-intents/{ALEX.action_intent_id}/approve",
        headers={**auth_alex, **idem("approve-ok")},
        json={
            "approved_draft": {"subject": "s", "body": "b"},
            "client_case_revision": 13,
            "acknowledge_warnings": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approved_case_revision"] == 13
    assert body["case_revision_after"] == 14
    assert len(body["approval_draft_sha256"]) == 64


def test_a_stale_approval_is_409_action_stale_with_the_section_7_3_shape(client, auth_alex) -> None:
    response = client.post(
        f"/v1/action-intents/{ALEX.action_intent_id}/approve",
        headers={**auth_alex, **idem("approve-stale")},
        json={
            "approved_draft": {"subject": "s", "body": "b"},
            "client_case_revision": 11,
            "acknowledge_warnings": [],
        },
    )
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["stale_reason"] in {
        "CASE_REVISION_ADVANCED",
        "CLIENT_REVISION_BEHIND",
        "DRAFT_HASH_MISMATCH",
        "SUPPORT_SUPERSEDED",
        "STATUS_NOT_APPROVABLE",
        "ALREADY_EXECUTED",
    }
    assert "changed_since" in details
    assert "refresh" in details


def test_rejection_requires_reason_text_only_for_other(client, auth_alex) -> None:
    ok = client.post(
        f"/v1/action-intents/{ALEX.action_intent_id}/reject",
        headers={**auth_alex, **idem("reject-not-now")},
        json={"reason_code": "NOT_NOW"},
    )
    assert ok.status_code == 200

    bad = client.post(
        f"/v1/action-intents/{ALEX.action_intent_id}/reject",
        headers={**auth_alex, **idem("reject-other")},
        json={"reason_code": "OTHER"},
    )
    assert bad.status_code == 422


# --------------------------------------------------------------------------
# 8.28-8.31 -- traces and judge mode
# --------------------------------------------------------------------------


CLOSED_NODE_TYPES = {
    "API_REQUEST",
    "ARTIFACT_PARSE",
    "EMBEDDING",
    "AGENT_RUN",
    "MODEL_CALL",
    "MCP_TOOL_CALL",
    "RETRIEVAL",
    "PROPOSAL",
    "KERNEL_DECISION",
    "DB_TRANSACTION",
    "CANONICAL_CHANGE",
    "OUTBOX_EVENT",
    "EVENT_CONSUMER",
    "TRIGGER_EVALUATION",
    "ACTION_INTENT",
    "ACTION_APPROVAL",
    "ACTION_EXECUTION",
}


def test_the_closed_node_type_set_has_exactly_seventeen_members() -> None:
    from services.control_plane.app.api.schemas.public import TRACE_NODE_TYPES

    assert set(TRACE_NODE_TYPES) == CLOSED_NODE_TYPES
    assert len(TRACE_NODE_TYPES) == 17


def test_a_trace_returns_only_types_from_the_closed_set(client, auth_alex) -> None:
    body = client.get(f"/v1/traces/{ALEX.trace_id}", headers=auth_alex).json()
    assert {node["type"] for node in body["nodes"]} <= CLOSED_NODE_TYPES
    assert body["boundary"]["note"].startswith("Model nodes propose")


def test_the_counterfactual_parity_block_has_its_six_fields(client, auth_alex) -> None:
    body = client.get(
        f"/v1/judge-mode/counterfactual/{ALEX.counterfactual_id}", headers=auth_alex
    ).json()
    parity = body["parity"]
    assert set(parity) == {
        "artifact_id",
        "artifact_sha256",
        "model_id",
        "prompt_version",
        "graph_version",
        "decode_params_sha256",
        "all_equal",
    }
    assert parity["all_equal"] is True
    assert body["safety"]["case_revision_changed_by_counterfactual"] is False


def test_the_render_gate_suppresses_both_outputs_when_parity_fails(
    client, auth_alex, fixture
) -> None:
    original = fixture.write.get_counterfactual

    async def unequal(scope: object, counterfactual_id: uuid.UUID) -> object:
        body = await original(scope, counterfactual_id)
        if body is None:
            return None
        body["parity"]["model_id"] = {"off": "a", "on": "b", "equal": False}
        body["parity"]["all_equal"] = False
        return body

    fixture.write.get_counterfactual = unequal  # type: ignore[method-assign]
    body = client.get(
        f"/v1/judge-mode/counterfactual/{ALEX.counterfactual_id}", headers=auth_alex
    ).json()
    assert body["parity"]["all_equal"] is False
    assert body["memory_off"]["output"] is None
    assert body["memory_on"]["output"] is None


def test_starting_a_counterfactual_returns_a_poll_url(client, auth_alex) -> None:
    response = client.post(
        "/v1/judge-mode/counterfactual",
        headers={**auth_alex, **idem("counterfactual-go")},
        json={"artifact_id": str(ALEX.artifact_id)},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "RUNNING"
    assert body["poll_url"].startswith("/v1/judge-mode/counterfactual/")


def test_agent_views_are_read_from_the_database_not_a_constant(client, auth_alex) -> None:
    body = client.get("/v1/judge-mode/agent-views", headers=auth_alex).json()
    assert body["views"] == [
        "agent_active_beliefs_v1",
        "agent_belief_lineage_v1",
        "agent_case_context_v1",
        "agent_evidence_retrieval_v1",
        "agent_open_obligations_v1",
    ]


def test_the_memory_trace_route_answers(client, auth_alex) -> None:
    body = client.get(f"/v1/cases/{ALEX.case_id}/memory-trace", headers=auth_alex).json()
    assert body["current_revision"] == 13
