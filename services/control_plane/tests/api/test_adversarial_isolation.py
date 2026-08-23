"""The adversarial lane: a token for one user must not reach another's memory.

Authority: `specs/15_API_SPEC.md` section 1.7 and `23_PHASE_GATES.md` G8.4.
`EXECUTION/72_DEFECT_PROTOCOL.md` severity rule B2 makes a route that can
return another user's row a BLOCKER, not a bug -- so this file tries every
door: the path parameter, the filter parameter, the cursor, the request body,
the capability id, and the error message itself.

Section 1.7 fixes the shape of the answer: a cross-scope read is
indistinguishable from genuine absence. `404`, never `403`; a `403` would
confirm that another user's object exists.
"""

from __future__ import annotations

import uuid

import pytest
from _support import fakes as fakes_mod
from _support.fixtures import idem

pytestmark = [pytest.mark.adversarial, pytest.mark.isolation]

ALEX, ROB = fakes_mod.ALEX, fakes_mod.ROB
ABSENT = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _assert_indistinguishable_404(response, expected_code: str, foreign: object) -> None:
    """Cross-scope and genuinely-absent must be the same answer, and neither
    may leak the identifier's existence through the body."""
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["error"]["code"] == expected_code
    text = response.text
    for leak in ("tenant", "belongs", "forbidden", "denied", "other user", "not yours"):
        assert leak not in text.lower(), f"the 404 body hints at existence: {leak}"
    assert str(foreign) not in text or body["error"]["details"].get("id") == str(foreign)


# --------------------------------------------------------------------------
# G8.4 -- the path parameter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "attribute", "code"),
    [
        ("/v1/cases/{}", "case_id", "CASE_NOT_FOUND"),
        ("/v1/cases/{}/timeline", "case_id", "CASE_NOT_FOUND"),
        ("/v1/cases/{}/state-proof", "case_id", "CASE_NOT_FOUND"),
        ("/v1/cases/{}/conflicts", "case_id", "CASE_NOT_FOUND"),
        ("/v1/cases/{}/memory-trace", "case_id", "CASE_NOT_FOUND"),
        ("/v1/beliefs/{}", "belief_id", "BELIEF_NOT_FOUND"),
        ("/v1/artifacts/{}", "artifact_id", "ARTIFACT_NOT_FOUND"),
        ("/v1/action-intents/{}", "action_intent_id", "ACTION_INTENT_NOT_FOUND"),
        ("/v1/traces/{}", "trace_id", "TRACE_NOT_FOUND"),
    ],
)
def test_g8_4_a_cross_user_read_is_404_not_403(
    client, auth_alex, template: str, attribute: str, code: str
) -> None:
    foreign = getattr(ROB, attribute)
    response = client.get(template.format(foreign), headers=auth_alex)
    _assert_indistinguishable_404(response, code, foreign)


@pytest.mark.parametrize(
    ("template", "attribute", "code"),
    [
        ("/v1/cases/{}", "case_id", "CASE_NOT_FOUND"),
        ("/v1/beliefs/{}", "belief_id", "BELIEF_NOT_FOUND"),
        ("/v1/artifacts/{}", "artifact_id", "ARTIFACT_NOT_FOUND"),
        ("/v1/action-intents/{}", "action_intent_id", "ACTION_INTENT_NOT_FOUND"),
    ],
)
def test_a_foreign_id_and_an_absent_id_answer_identically(
    client, auth_alex, template: str, attribute: str, code: str
) -> None:
    foreign = client.get(template.format(getattr(ROB, attribute)), headers=auth_alex)
    absent = client.get(template.format(ABSENT), headers=auth_alex)
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"]["code"] == absent.json()["error"]["code"] == code


def test_the_relationship_detail_route_is_scoped_too(client, auth_alex) -> None:
    from _support.fakes import _relationship_id

    response = client.get(f"/v1/relationships/{_relationship_id(ROB)}", headers=auth_alex)
    _assert_indistinguishable_404(response, "RELATIONSHIP_NOT_FOUND", _relationship_id(ROB))


# --------------------------------------------------------------------------
# The filter parameter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "parameter", "attribute"),
    [
        ("/v1/cases", "relationship_id", "case_id"),
        ("/v1/commitments", "case_id", "case_id"),
        ("/v1/triggers", "case_id", "case_id"),
        ("/v1/artifacts", "case_id", "case_id"),
        ("/v1/action-intents", "case_id", "case_id"),
    ],
)
def test_a_foreign_id_in_a_filter_never_widens_the_scope(
    client, auth_alex, fixture, path: str, parameter: str, attribute: str
) -> None:
    response = client.get(f"{path}?{parameter}={getattr(ROB, attribute)}", headers=auth_alex)
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert str(ROB.case_id) not in str(item)
        assert str(ROB.user_id) not in str(item)
    scopes = {scope for _, scope in fixture.read.calls}
    assert scopes == {ALEX.scope}, "every read was issued under the caller's own scope"


def test_the_dashboard_cannot_be_pointed_at_another_users_context(
    client, auth_alex, fixture
) -> None:
    client.get(f"/v1/dashboard?context_id={ROB.case_id}", headers=auth_alex)
    scopes = {scope for _, scope in fixture.read.calls}
    assert scopes == {ALEX.scope}


# --------------------------------------------------------------------------
# The cursor
# --------------------------------------------------------------------------


def test_a_cursor_minted_by_another_user_does_not_carry_their_scope(
    client, auth_alex, auth_rob, fixture
) -> None:
    """A cursor is a sort position, never an authority. Even a valid,
    correctly signed cursor from Rob's session must produce Alex's rows."""
    robs = client.get("/v1/cases?limit=1", headers=auth_rob)
    cursor = robs.json()["page"]["next_cursor"]
    if cursor is None:
        pytest.skip("the seeded fixture returns a single page; nothing to replay")
    fixture.read.calls.clear()
    response = client.get(f"/v1/cases?limit=1&cursor={cursor}", headers=auth_alex)
    assert response.status_code in {200, 400}
    assert {scope for _, scope in fixture.read.calls} <= {ALEX.scope}


def test_a_forged_cursor_is_rejected_rather_than_trusted(client, auth_alex) -> None:
    forged = "eyJ2IjoxLCJrIjpbIngiXSwiaSI6IjAxOGY3YTAwLTAwMDAtNzAwMC04MDAwLTAwMDAwMDAwYjAwMSIsImYiOiJhYWFhYWEifQ.AAAAAAAAAAAAAAAA"
    response = client.get(f"/v1/cases?cursor={forged}", headers=auth_alex)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


# --------------------------------------------------------------------------
# The request body
# --------------------------------------------------------------------------


def test_a_correction_cannot_be_aimed_at_another_users_case(client, auth_alex) -> None:
    response = client.post(
        f"/v1/cases/{ROB.case_id}/corrections",
        headers={**auth_alex, **idem("cross-correction")},
        json={
            "correction_type": "BELIEF_INCORRECT",
            "statement": "This is not mine.",
            "affected_belief_id": str(ROB.belief_id),
            "client_case_revision": 13,
        },
    )
    _assert_indistinguishable_404(response, "CASE_NOT_FOUND", ROB.case_id)


def test_approving_another_users_action_intent_is_404(client, auth_alex) -> None:
    response = client.post(
        f"/v1/action-intents/{ROB.action_intent_id}/approve",
        headers={**auth_alex, **idem("cross-approve")},
        json={
            "approved_draft": {"subject": "s", "body": "b"},
            "client_case_revision": 13,
            "acknowledge_warnings": [],
        },
    )
    _assert_indistinguishable_404(response, "ACTION_INTENT_NOT_FOUND", ROB.action_intent_id)


def test_rejecting_another_users_action_intent_is_404(client, auth_alex) -> None:
    response = client.post(
        f"/v1/action-intents/{ROB.action_intent_id}/reject",
        headers={**auth_alex, **idem("cross-reject")},
        json={"reason_code": "NOT_NOW"},
    )
    _assert_indistinguishable_404(response, "ACTION_INTENT_NOT_FOUND", ROB.action_intent_id)


def test_editing_another_users_draft_is_404(client, auth_alex) -> None:
    response = client.put(
        f"/v1/action-intents/{ROB.action_intent_id}/draft",
        headers={**auth_alex, **idem("cross-draft")},
        json={"subject": "s", "body": "b", "client_case_revision": 13},
    )
    _assert_indistinguishable_404(response, "ACTION_INTENT_NOT_FOUND", ROB.action_intent_id)


def test_completing_another_users_artifact_is_404(client, auth_alex) -> None:
    response = client.post(
        f"/v1/artifacts/{ROB.artifact_id}/complete",
        headers={**auth_alex, **idem("cross-complete")},
        json={},
    )
    _assert_indistinguishable_404(response, "ARTIFACT_NOT_FOUND", ROB.artifact_id)


def test_an_upload_intent_key_is_scoped_to_the_caller(client, auth_alex, fixture) -> None:
    """Section 8.18: the server chooses the key; a client cannot redirect the
    upload into another tenant's prefix."""
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("upload-prefix")},
        json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
    )
    key = response.json()["s3_key"]
    assert key.startswith(f"raw/{ALEX.tenant_id}/{ALEX.user_id}/")
    assert str(ROB.tenant_id) not in key


def test_a_client_supplied_s3_key_is_rejected_by_the_schema(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("upload-key-inject")},
        json={
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "s3_key": f"raw/{ROB.tenant_id}/{ROB.user_id}/x/original",
        },
    )
    assert response.status_code == 422
    reasons = {f["reason"] for f in response.json()["error"]["details"]["fields"]}
    assert "extra_forbidden" in reasons


def test_a_traversal_filename_never_becomes_part_of_the_key(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, **idem("upload-traversal")},
        json={
            "filename": "../../" + str(ROB.tenant_id) + "/evil.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
        },
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------
# The idempotency ledger
# --------------------------------------------------------------------------


def test_one_users_idempotency_key_cannot_collide_with_anothers(
    client, auth_alex, auth_rob
) -> None:
    """Section 6.3: the primary key is prefixed by `tenant_id` precisely so
    that a guessed key string is not a cross-tenant 409 oracle."""
    headers = idem("shared-key-guess")
    body = {"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10}
    first = client.post("/v1/artifacts/upload-intent", headers={**auth_alex, **headers}, json=body)
    second = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_rob, **headers},
        json={**body, "size_bytes": 11},
    )
    assert first.status_code == 201
    assert second.status_code == 201, "a different tenant's key must not conflict"


def test_a_replay_is_never_served_across_users(client, auth_alex, auth_rob) -> None:
    headers = idem("replay-across")
    body = {"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10}
    client.post("/v1/artifacts/upload-intent", headers={**auth_alex, **headers}, json=body)
    second = client.post("/v1/artifacts/upload-intent", headers={**auth_rob, **headers}, json=body)
    assert second.headers["idempotency-replayed"] == "false"
    assert str(ALEX.user_id) not in second.text


# --------------------------------------------------------------------------
# Judge mode grants no cross-user visibility
# --------------------------------------------------------------------------


def test_judge_mode_does_not_unlock_another_users_trace(client, auth_alex) -> None:
    """Section 2.5: judge mode gates sections 8.30-8.31 only; it grants no
    cross-user visibility."""
    response = client.get(f"/v1/traces/{ROB.trace_id}", headers=auth_alex)
    _assert_indistinguishable_404(response, "TRACE_NOT_FOUND", ROB.trace_id)


def test_judge_mode_does_not_unlock_another_users_counterfactual(client, auth_alex) -> None:
    response = client.get(
        f"/v1/judge-mode/counterfactual/{ROB.counterfactual_id}", headers=auth_alex
    )
    _assert_indistinguishable_404(response, "COUNTERFACTUAL_NOT_FOUND", ROB.counterfactual_id)


def test_a_counterfactual_cannot_be_started_on_another_users_artifact(client, auth_alex) -> None:
    response = client.post(
        "/v1/judge-mode/counterfactual",
        headers={**auth_alex, **idem("cross-counterfactual")},
        json={"artifact_id": str(ROB.artifact_id)},
    )
    _assert_indistinguishable_404(response, "ARTIFACT_NOT_FOUND", ROB.artifact_id)


def test_a_non_judge_is_refused_judge_mode_without_leaking_anything(client, auth_rob) -> None:
    response = client.post(
        "/v1/judge-mode/counterfactual",
        headers={**auth_rob, **idem("no-judge-mode")},
        json={"artifact_id": str(ROB.artifact_id)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "JUDGE_MODE_DISABLED"


# --------------------------------------------------------------------------
# The internal surface
# --------------------------------------------------------------------------


def test_an_agent_run_capability_cannot_reach_another_users_case(client, agent_headers) -> None:
    response = client.get(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/state-proof?case_id={ROB.case_id}",
        headers=agent_headers(),
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "CAPABILITY_SCOPE_MISMATCH"
    assert body["error"]["details"]["reason"] == "CASE_OUTSIDE_CAPABILITY"


def test_the_binding_handed_to_an_internal_handler_is_always_the_servers(
    client, agent_headers, fixture
) -> None:
    client.get(f"/internal/v1/agent-runs/{ALEX.agent_run_id}", headers=agent_headers())
    name, binding, _ = fixture.internal.calls[-1]
    assert name == "agent_run"
    assert binding is not None
    assert binding.tenant_id == ALEX.tenant_id
    assert binding.user_id == ALEX.user_id


def test_an_agent_run_id_belonging_to_another_user_still_resolves_to_that_user(
    client, agent_bearer, capability_proof, fixture
) -> None:
    """A leaked capability id is not a cross-user hole: it resolves to *its
    own* owner, so the worst outcome is acting on the correct user's memory
    (section 3.8), never on a user the caller chose."""
    record = fixture.capabilities.records[("AGENT_RUN", str(ROB.agent_run_id))]
    headers = {
        "Authorization": f"Bearer {agent_bearer}",
        "X-Provenance-Capability-Proof": capability_proof(
            "AGENT_RUN", ROB.agent_run_id, record.expires_at
        ),
    }
    client.get(f"/internal/v1/agent-runs/{ROB.agent_run_id}", headers=headers)
    _, binding, _ = fixture.internal.calls[-1]
    assert binding.user_id == ROB.user_id, "resolved from the row, not from the caller"


def test_no_internal_route_accepts_a_user_id_in_the_body(client, agent_headers) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/evidence",
        headers={**agent_headers(), **idem("evidence-user-id")},
        json={
            "schema_version": "1.0",
            "candidates": [],
            "user_id": str(ROB.user_id),
        },
    )
    assert response.status_code == 422
    reasons = {f["reason"] for f in response.json()["error"]["details"]["fields"]}
    assert "extra_forbidden" in reasons


def test_no_internal_route_accepts_a_tenant_id_in_the_body(client, agent_headers) -> None:
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/retrieval",
        headers=agent_headers(),
        json={"schema_version": "1.0", "tenant_id": str(ROB.tenant_id)},
    )
    assert response.status_code == 422
    reasons = {f["reason"] for f in response.json()["error"]["details"]["fields"]}
    assert "extra_forbidden" in reasons


def test_an_ingest_alias_resolves_only_to_its_own_owner(client, worker_headers, fixture) -> None:
    headers = worker_headers("INGEST_JOB", ROB.alias_hash)
    response = client.post(
        "/internal/v1/ingest/artifacts",
        headers={**headers, **idem("ses-alias-rob")},
        json={
            "alias_hash": ROB.alias_hash,
            "s3_bucket": "provenance-inbound-us-east-1",
            "s3_key": "ses/2026/06/05/abc",
            "source_message_id": "<a@b.example>",
            "sender": "billing@northlinebroadband.example",
            "recipient": "x@in.provenance.app",
            "subject": "Invoice",
            "received_at": "2026-06-05T14:19:00Z",
            "size_bytes": 2048,
            "content_sha256": "3f" * 32,
            "ses_verdicts": {
                "spf": "PASS",
                "dkim": "PASS",
                "dmarc": "PASS",
                "spam": "PASS",
                "virus": "PASS",
            },
        },
    )
    assert response.status_code in {200, 201}
    _, binding, _ = fixture.internal.calls[-1]
    assert binding.user_id == ROB.user_id


def test_the_error_body_never_contains_another_tenants_identifier(client, auth_alex) -> None:
    for path in (
        f"/v1/cases/{ROB.case_id}",
        f"/v1/beliefs/{ROB.belief_id}",
        f"/v1/artifacts/{ROB.artifact_id}",
        f"/v1/traces/{ROB.trace_id}",
    ):
        text = client.get(path, headers=auth_alex).text
        assert str(ROB.tenant_id) not in text
        assert str(ROB.user_id) not in text
