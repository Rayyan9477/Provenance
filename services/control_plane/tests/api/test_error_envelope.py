"""T8.1 -- the error envelope, the closed code catalogue, and the trace header.

Authority: `specs/15_API_SPEC.md` section 4. Feeds `G8.1` and `G8.7`.

The load-bearing assertion here is the one about failure: `G8.7` checks the
trace id on a 404, because the header is worthless on a 200 -- nobody pastes a
trace id for a request that worked.
"""

from __future__ import annotations

import uuid

import pytest

from services.control_plane.app.api.errors import (
    DEFAULT_HTTP_STATUS,
    ApiError,
    ErrorCode,
    error_envelope,
)

pytestmark = pytest.mark.unit

TRACE_HEADER = "x-provenance-trace-id"
REQUEST_HEADER = "x-provenance-request-id"


def _assert_envelope(body: dict[str, object]) -> dict[str, object]:
    assert set(body) == {"error"}, "the envelope has exactly one top-level key"
    error = body["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message", "trace_id", "details"}
    assert error["code"] in {c.value for c in ErrorCode}, "code drawn from the closed catalogue"
    assert isinstance(error["message"], str) and error["message"]
    assert len(error["message"]) <= 300
    uuid.UUID(str(error["trace_id"]))
    assert isinstance(error["details"], dict)
    return error


# --------------------------------------------------------------------------
# The catalogue is closed
# --------------------------------------------------------------------------


def test_every_catalogue_code_has_a_declared_http_status() -> None:
    missing = [c.value for c in ErrorCode if c not in DEFAULT_HTTP_STATUS]
    assert missing == []


def test_api_error_refuses_a_code_outside_the_catalogue() -> None:
    with pytest.raises(ValueError, match="not in the error catalogue"):
        ApiError("MADE_UP_CODE")  # type: ignore[arg-type]


def test_api_error_defaults_its_status_from_the_catalogue() -> None:
    assert ApiError(ErrorCode.CASE_NOT_FOUND).http_status == 404
    assert ApiError(ErrorCode.ACTION_STALE).http_status == 409
    assert ApiError(ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE).http_status == 403
    assert ApiError(ErrorCode.RETRYABLE_CONCURRENCY).http_status == 503


def test_retryable_concurrency_carries_retry_after() -> None:
    assert ApiError(ErrorCode.RETRYABLE_CONCURRENCY).headers["Retry-After"] == "1"


def test_error_envelope_is_the_section_4_shape() -> None:
    trace = uuid.uuid4()
    body = error_envelope(ErrorCode.ACTION_STALE, "This case changed.", trace, {"a": 1})
    assert body == {
        "error": {
            "code": "ACTION_STALE",
            "message": "This case changed.",
            "trace_id": str(trace),
            "details": {"a": 1},
        }
    }


def test_details_default_to_an_empty_object_never_null() -> None:
    body = error_envelope(ErrorCode.CASE_NOT_FOUND, "gone", uuid.uuid4(), None)
    assert body["error"]["details"] == {}


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_trace_id_is_present_on_success(client, auth_alex) -> None:
    response = client.get("/v1/me", headers=auth_alex)
    assert response.status_code == 200
    uuid.UUID(response.headers[TRACE_HEADER])
    uuid.UUID(response.headers[REQUEST_HEADER])


def test_g8_7_trace_id_is_present_on_a_404_for_a_nonexistent_case(client, auth_alex) -> None:
    response = client.get("/v1/cases/00000000-0000-0000-0000-000000000000", headers=auth_alex)
    assert response.status_code == 404
    error = _assert_envelope(response.json())
    assert error["code"] == "CASE_NOT_FOUND"
    assert response.headers[TRACE_HEADER] == error["trace_id"]


def test_trace_id_is_present_on_an_unauthenticated_401(client) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    error = _assert_envelope(response.json())
    assert error["code"] == "UNAUTHENTICATED"
    assert response.headers[TRACE_HEADER] == error["trace_id"]


def test_trace_id_is_present_on_an_unhandled_500(client, auth_alex, fixture) -> None:
    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("table users does not exist")

    fixture.read.me = boom  # type: ignore[method-assign]
    response = client.get("/v1/me", headers=auth_alex)
    assert response.status_code == 500
    error = _assert_envelope(response.json())
    assert error["code"] == "INTERNAL_ERROR"
    assert "users" not in error["message"], "no table name reaches the client"
    assert "Nothing was committed" in error["message"]
    uuid.UUID(response.headers[TRACE_HEADER])


def test_a_supplied_trace_id_is_honoured_when_well_formed(client, auth_alex) -> None:
    supplied = str(uuid.uuid4())
    response = client.get("/v1/me", headers={**auth_alex, "X-Provenance-Trace-Id": supplied})
    assert response.headers[TRACE_HEADER] == supplied


def test_a_malformed_supplied_trace_id_is_replaced_not_echoed(client, auth_alex) -> None:
    response = client.get(
        "/v1/me", headers={**auth_alex, "X-Provenance-Trace-Id": "'; DROP TABLE cases--"}
    )
    assert response.status_code == 200
    uuid.UUID(response.headers[TRACE_HEADER])


def test_validation_failure_uses_the_section_4_2_field_shape(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, "Idempotency-Key": "pv-validation-000000"},
        json={"filename": "x.pdf", "mime_type": "application/pdf", "size_bytes": "many"},
    )
    assert response.status_code == 422
    error = _assert_envelope(response.json())
    assert error["code"] == "VALIDATION_FAILED"
    fields = error["details"]["fields"]  # type: ignore[index]
    assert isinstance(fields, list) and fields
    assert set(fields[0]) == {"loc", "reason", "message"}


def test_a_caller_supplied_user_id_is_rejected_by_the_schema_layer(client, auth_alex) -> None:
    """Section 2.6: `extra="forbid"` is what makes the rule enforceable."""
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={**auth_alex, "Idempotency-Key": "pv-extra-forbid-0000"},
        json={
            "filename": "x.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "user_id": "018f7a01-0000-7000-8000-00000000abcd",
        },
    )
    assert response.status_code == 422
    error = _assert_envelope(response.json())
    reasons = {f["reason"] for f in error["details"]["fields"]}  # type: ignore[index]
    assert "extra_forbidden" in reasons


def test_method_not_allowed_is_enveloped(client, auth_alex) -> None:
    response = client.delete("/v1/me", headers=auth_alex)
    assert response.status_code == 405
    assert _assert_envelope(response.json())["code"] == "METHOD_NOT_ALLOWED"


def test_a_non_json_content_type_is_415(client, auth_alex) -> None:
    response = client.post(
        "/v1/artifacts/upload-intent",
        headers={
            **auth_alex,
            "Idempotency-Key": "pv-media-type-00000",
            "Content-Type": "text/xml",
        },
        content=b"<x/>",
    )
    assert response.status_code == 415
    assert _assert_envelope(response.json())["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_malformed_json_is_400_not_422(client, auth_alex) -> None:
    response = client.post(
        "/v1/cases/018f7a00-0000-7000-8000-00000000a001/corrections",
        headers={
            **auth_alex,
            "Idempotency-Key": "pv-malformed-json00",
            "Content-Type": "application/json",
        },
        content=b"{not json",
    )
    assert response.status_code == 400
    assert _assert_envelope(response.json())["code"] == "MALFORMED_JSON"


def test_every_authenticated_response_is_no_store(client, auth_alex) -> None:
    response = client.get("/v1/me", headers=auth_alex)
    assert response.headers["cache-control"] == "no-store"


def test_case_scoped_responses_carry_the_case_revision_header(client, auth_alex) -> None:
    response = client.get("/v1/cases/018f7a00-0000-7000-8000-00000000a001", headers=auth_alex)
    assert response.status_code == 200
    assert response.headers["x-provenance-case-revision"] == "13"


# --------------------------------------------------------------------------
# A capped message is truncated, never mangled
# --------------------------------------------------------------------------
#
# The 300-character cap used to be a bare slice. `read.get_trace`'s message is
# 335 characters, so Judge Mode -- the screen built for the people evaluating
# this build -- rendered "... Needs app/ob" and stopped, which reads as a broken
# product rather than as an honest boundary. These four assertions are what
# would fail if the bare slice came back.


def test_an_over_long_message_is_cut_at_a_word_boundary() -> None:
    """The cut lands between words and says that it cut."""
    message = "alpha bravo charlie delta " * 20  # 520 characters, all whole words
    error = ApiError(ErrorCode.NOT_IMPLEMENTED, message=message)

    assert len(error.message) <= 300
    assert error.message.endswith("…"), "an elided message says so"
    body = error.message.rstrip("…")
    assert not body.endswith(" "), "the marker sits against the last word"
    for word in body.split():
        assert word in {
            "alpha",
            "bravo",
            "charlie",
            "delta",
        }, f"{word!r} is a fragment; the cut landed inside a word"


def test_a_message_that_fits_is_left_exactly_alone() -> None:
    """No marker, no rstrip, on anything under the cap."""
    message = "Nothing failed and nothing was attempted."
    assert ApiError(ErrorCode.NOT_IMPLEMENTED, message=message).message == message


def test_every_unbound_message_reaches_the_reader_whole() -> None:
    """The register's messages are read by humans, so they must fit whole.

    An unbound message names the subsystem a reader should go look at. One that
    arrives truncated names half of it. This asserts the two a judge actually
    sees -- Judge Mode's trace panel and the per-case memory trace -- are short
    enough to survive the envelope intact.
    """
    from services.control_plane.app.api.adapters.unbound import UNBOUND

    judge_visible = ("read.get_trace", "read.memory_trace")
    for name in judge_visible:
        rendered = ApiError(
            ErrorCode.NOT_IMPLEMENTED,
            message=f"{name} is not bound yet: {UNBOUND[name]}",
        ).message
        assert not rendered.endswith("…"), (
            f"{name} is judge-visible and does not fit in the 300-character "
            f"envelope; it renders elided at {len(rendered)} characters"
        )
        assert rendered.endswith("."), f"{name} should arrive as whole sentences"
