"""T8.4 -- idempotency over `idempotency_records`.

Authority: `specs/15_API_SPEC.md` section 6; `23_PHASE_GATES.md` sections 14
(`G8.6`) and 23.10.

Section 23.10 is why the first HTTP assertion below is about the **key**, not
about the effect: a retry test that mints a fresh `uuid4()` per attempt passes
while proving nothing. So the key string is asserted equal across attempts
before anything else is asserted.
"""

from __future__ import annotations

import json
import uuid

import pytest
from _support.fixtures import idem

from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.idempotency import (
    IDEMPOTENCY_KEY_PATTERN,
    IDEMPOTENCY_SCOPES,
    InMemoryIdempotencyStore,
    begin_idempotent,
    jcs_canonicalize,
    request_hash,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID("018f7a00-0000-7000-8000-00000000ffff")
USER = uuid.UUID("018f7a00-0000-7000-8000-00000000abcd")


# --------------------------------------------------------------------------
# The hash
# --------------------------------------------------------------------------


def test_jcs_makes_key_order_and_whitespace_irrelevant() -> None:
    a = b'{"b": 2,  "a": 1}'
    b = b'{"a":1,"b":2}'
    assert jcs_canonicalize(a) == jcs_canonicalize(b)


def test_jcs_is_recursive() -> None:
    a = json.dumps({"outer": {"z": 1, "a": [{"y": 2, "x": 3}]}}).encode()
    b = json.dumps({"outer": {"a": [{"x": 3, "y": 2}], "z": 1}}).encode()
    assert jcs_canonicalize(a) == jcs_canonicalize(b)


def test_request_hash_is_stable_across_reordered_bodies() -> None:
    left = request_hash("POST", "/v1/x", [], b'{"b":2,"a":1}')
    right = request_hash("POST", "/v1/x", [], b'{"a":1,"b":2}')
    assert left == right


def test_request_hash_changes_when_a_value_changes() -> None:
    left = request_hash("POST", "/v1/x", [], b'{"a":1}')
    right = request_hash("POST", "/v1/x", [], b'{"a":2}')
    assert left != right


def test_request_hash_sorts_the_query_string() -> None:
    left = request_hash("POST", "/v1/x", [("b", "2"), ("a", "1")], b"")
    right = request_hash("POST", "/v1/x", [("a", "1"), ("b", "2")], b"")
    assert left == right


def test_request_hash_covers_method_and_path() -> None:
    assert request_hash("POST", "/v1/x", [], b"") != request_hash("PUT", "/v1/x", [], b"")
    assert request_hash("POST", "/v1/x", [], b"") != request_hash("POST", "/v1/y", [], b"")


@pytest.mark.parametrize(
    "key",
    ["short", "has spaces in it here", "has/slash/in/it/aaaaaaaaaa", "a" * 256],
)
def test_malformed_keys_are_rejected_by_the_pattern(key: str) -> None:
    assert IDEMPOTENCY_KEY_PATTERN.fullmatch(key) is None


def test_a_uuid_is_a_well_formed_key() -> None:
    assert IDEMPOTENCY_KEY_PATTERN.fullmatch(str(uuid.uuid4())) is not None


def test_every_section_6_2_endpoint_has_a_declared_scope_string() -> None:
    assert IDEMPOTENCY_SCOPES[("POST", "/v1/artifacts/upload-intent")] == "artifact.upload_intent"
    assert IDEMPOTENCY_SCOPES[("POST", "/v1/cases/{case_id}/corrections")] == "case.correction"
    assert (
        IDEMPOTENCY_SCOPES[("POST", "/internal/v1/memory/proposals")] == "internal.memory.proposal"
    )
    # Section 6.2's table has eighteen rows and exactly fifteen say
    # `Required: yes`. The other three name their own control instead:
    # `/internal/v1/events/deliveries` dedupes on `event_id`,
    # `/internal/v1/events/outbox/sweep` uses the claim/lease state machine,
    # and `/internal/v1/agent-runs/{id}/retrieval` is read-only.
    assert len(IDEMPOTENCY_SCOPES) == 15, "section 6.2 marks fifteen endpoints Required: yes"


# --------------------------------------------------------------------------
# The decision table (section 6.5)
# --------------------------------------------------------------------------


async def test_a_fresh_key_takes_the_lease_and_executes() -> None:
    store = InMemoryIdempotencyStore()
    decision = await begin_idempotent(
        store, TENANT, USER, "artifact.upload_intent", "k" * 20, b"h" * 32, uuid.uuid4()
    )
    assert decision.execute is True


async def test_the_same_key_and_body_replays_the_stored_response() -> None:
    store = InMemoryIdempotencyStore()
    key, digest = "k" * 20, b"h" * 32
    await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    await store.complete(TENANT, "s", key, 201, {"artifact_id": "abc"}, None)
    decision = await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    assert decision.execute is False
    assert decision.replay == (201, {"artifact_id": "abc"})


async def test_the_same_key_with_a_different_body_is_a_conflict() -> None:
    store = InMemoryIdempotencyStore()
    key = "k" * 20
    await begin_idempotent(store, TENANT, USER, "s", key, b"h" * 32, uuid.uuid4())
    await store.complete(TENANT, "s", key, 201, {}, None)
    with pytest.raises(ApiError) as excinfo:
        await begin_idempotent(store, TENANT, USER, "s", key, b"g" * 32, uuid.uuid4())
    assert excinfo.value.code is ErrorCode.IDEMPOTENCY_CONFLICT
    assert excinfo.value.http_status == 409
    assert excinfo.value.details["scope"] == "s"


async def test_a_live_lease_with_a_matching_body_is_in_progress() -> None:
    store = InMemoryIdempotencyStore()
    key, digest = "k" * 20, b"h" * 32
    await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    with pytest.raises(ApiError) as excinfo:
        await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    assert excinfo.value.code is ErrorCode.IDEMPOTENCY_IN_PROGRESS
    assert excinfo.value.headers["Retry-After"] == "2"


async def test_a_live_lease_with_a_different_body_is_a_conflict_not_in_progress() -> None:
    """Section 6.5: the hash check precedes the status check in every row."""
    store = InMemoryIdempotencyStore()
    await begin_idempotent(store, TENANT, USER, "s", "k" * 20, b"h" * 32, uuid.uuid4())
    with pytest.raises(ApiError) as excinfo:
        await begin_idempotent(store, TENANT, USER, "s", "k" * 20, b"g" * 32, uuid.uuid4())
    assert excinfo.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


async def test_a_dead_lease_with_a_matching_body_is_taken_over() -> None:
    store = InMemoryIdempotencyStore()
    key, digest = "k" * 20, b"h" * 32
    await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    store.expire_lease(TENANT, "s", key)
    decision = await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    assert decision.execute is True


async def test_a_dead_lease_with_a_different_body_is_still_a_conflict() -> None:
    store = InMemoryIdempotencyStore()
    key = "k" * 20
    await begin_idempotent(store, TENANT, USER, "s", key, b"h" * 32, uuid.uuid4())
    store.expire_lease(TENANT, "s", key)
    with pytest.raises(ApiError):
        await begin_idempotent(store, TENANT, USER, "s", key, b"g" * 32, uuid.uuid4())


async def test_a_failed_record_with_a_matching_body_re_executes() -> None:
    store = InMemoryIdempotencyStore()
    key, digest = "k" * 20, b"h" * 32
    await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    await store.fail(TENANT, "s", key)
    decision = await begin_idempotent(store, TENANT, USER, "s", key, digest, uuid.uuid4())
    assert decision.execute is True


async def test_the_key_range_is_prefixed_by_tenant() -> None:
    """Section 6.3: PK is (tenant_id, scope, key), so one tenant's key string
    cannot collide with another's and become a cross-tenant 409 oracle."""
    store = InMemoryIdempotencyStore()
    other_tenant = uuid.UUID("018f7a00-0000-7000-8000-00000000eeee")
    key = "shared-key-string-00"
    assert (await begin_idempotent(store, TENANT, USER, "s", key, b"h" * 32, uuid.uuid4())).execute
    assert (
        await begin_idempotent(store, other_tenant, USER, "s", key, b"g" * 32, uuid.uuid4())
    ).execute


# --------------------------------------------------------------------------
# Over HTTP -- G8.6
# --------------------------------------------------------------------------


def test_a_mutating_route_without_a_key_is_400(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers=auth_alex,
        json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
    assert body["error"]["details"]["header"] == "Idempotency-Key"


def test_a_malformed_key_is_400(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, "Idempotency-Key": "too short"},
        json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MALFORMED_IDEMPOTENCY_KEY"


def test_g8_6_replay_is_byte_identical_and_only_the_second_is_flagged(client, auth_alex) -> None:
    headers = {**auth_alex, **idem("g86-replay")}
    body = {"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10}

    # 23.10: assert string equality of the key across attempts FIRST.
    first_key = headers["Idempotency-Key"]
    first = client.post("/v1/artifacts/upload-intent", headers=headers, json=body)
    second = client.post("/v1/artifacts/upload-intent", headers=headers, json=body)
    assert headers["Idempotency-Key"] == first_key, "the same key string on both attempts"

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert first.headers["idempotency-replayed"] == "false"
    assert second.headers["idempotency-replayed"] == "true"
    assert first.headers["idempotency-key"] == first_key


def test_g8_6_the_same_key_with_a_different_body_is_409(client, auth_alex) -> None:
    headers = {**auth_alex, **idem("g86-conflict")}
    client.post(
        "/v1/artifacts/upload-intent",
        headers=headers,
        json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
    )
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers=headers,
        json={"filename": "b.pdf", "mime_type": "application/pdf", "size_bytes": 11},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert body["error"]["details"]["key"] == headers["Idempotency-Key"]


def test_the_effect_runs_exactly_once_under_a_replayed_key(client, auth_alex, fixture) -> None:
    headers = {**auth_alex, **idem("g86-once")}
    body = {"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10}
    client.post("/v1/artifacts/upload-intent", headers=headers, json=body)
    client.post("/v1/artifacts/upload-intent", headers=headers, json=body)
    calls = [c for c in fixture.write.calls if c[0] == "upload_intent"]
    assert len(calls) == 1


def test_a_get_route_does_not_require_a_key(client, auth_alex) -> None:
    assert client.get("/v1/cases", headers=auth_alex).status_code == 200
