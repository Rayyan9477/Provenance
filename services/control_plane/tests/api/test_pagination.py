"""T8.1 -- cursor pagination bound to the query shape.

Authority: `specs/15_API_SPEC.md` section 5.

The point of the fingerprint is that a cursor cannot be replayed against a
different filter. Silently restarting the scan would be a correctness bug for
a memory product, not a cosmetic one, so the failure is loud: `400
INVALID_CURSOR` with `reason: "FILTER_CHANGED"`.
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.pagination import (
    CURSOR_VERSION,
    MAX_LIMIT,
    decode_cursor,
    encode_cursor,
    filter_fingerprint,
    parse_limit,
)

pytestmark = pytest.mark.unit

KEY = b"cursor-key-for-tests-only-not-a-secret"


def test_cursor_round_trips() -> None:
    last_id = uuid.uuid4()
    fp = filter_fingerprint(status=["REOPENED"], context_id=None)
    cursor = encode_cursor(["2026-06-05T14:22:31.482Z"], last_id, fp, key=KEY)
    sort_key, decoded_id = decode_cursor(cursor, fp, key=KEY)
    assert sort_key == ["2026-06-05T14:22:31.482Z"]
    assert decoded_id == last_id


def test_fingerprint_ignores_none_valued_filters_and_key_order() -> None:
    assert filter_fingerprint(b=2, a=1, c=None) == filter_fingerprint(a=1, b=2)


def test_fingerprint_changes_when_a_filter_changes() -> None:
    assert filter_fingerprint(status=["REOPENED"]) != filter_fingerprint(status=["DISPUTED"])


def test_a_cursor_from_a_different_query_shape_is_rejected() -> None:
    cursor = encode_cursor(["t"], uuid.uuid4(), filter_fingerprint(status=["REOPENED"]), key=KEY)
    with pytest.raises(ApiError) as excinfo:
        decode_cursor(cursor, filter_fingerprint(status=["DISPUTED"]), key=KEY)
    assert excinfo.value.code is ErrorCode.INVALID_CURSOR
    assert excinfo.value.details["reason"] == "FILTER_CHANGED"


def test_a_hand_crafted_cursor_fails_the_signature_check() -> None:
    payload = json.dumps(
        {"v": CURSOR_VERSION, "k": ["t"], "i": str(uuid.uuid4()), "f": "abcdef"},
        separators=(",", ":"),
    ).encode()
    forged = base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".AAAAAAAAAAAAAAAA"
    with pytest.raises(ApiError) as excinfo:
        decode_cursor(forged, "abcdef", key=KEY)
    assert excinfo.value.details["reason"] == "SIGNATURE_INVALID"


def test_a_cursor_signed_with_another_key_is_rejected() -> None:
    fp = filter_fingerprint()
    cursor = encode_cursor(["t"], uuid.uuid4(), fp, key=b"another-key-entirely")
    with pytest.raises(ApiError) as excinfo:
        decode_cursor(cursor, fp, key=KEY)
    assert excinfo.value.details["reason"] == "SIGNATURE_INVALID"


def test_a_garbage_cursor_is_malformed_not_a_500() -> None:
    with pytest.raises(ApiError) as excinfo:
        decode_cursor("not-a-cursor", filter_fingerprint(), key=KEY)
    assert excinfo.value.details["reason"] == "MALFORMED"


def test_an_unsupported_cursor_version_is_named_as_such() -> None:
    import hashlib
    import hmac

    fp = filter_fingerprint()
    payload = json.dumps(
        {"v": CURSOR_VERSION + 1, "k": ["t"], "i": str(uuid.uuid4()), "f": fp},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(KEY, payload, hashlib.sha256).digest()[:12]

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    with pytest.raises(ApiError) as excinfo:
        decode_cursor(f"{b64(payload)}.{b64(sig)}", fp, key=KEY)
    assert excinfo.value.details["reason"] == "VERSION_UNSUPPORTED"


@pytest.mark.parametrize("raw", [0, -1, MAX_LIMIT + 1, 1000])
def test_a_limit_outside_the_range_is_invalid_page_size(raw: int) -> None:
    with pytest.raises(ApiError) as excinfo:
        parse_limit(raw)
    assert excinfo.value.code is ErrorCode.INVALID_PAGE_SIZE
    assert excinfo.value.details == {"min": 1, "max": MAX_LIMIT, "received": raw}


def test_the_default_limit_is_twenty_five() -> None:
    assert parse_limit(None) == 25


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_a_list_response_has_the_section_5_2_page_object(client, auth_alex) -> None:
    body = client.get("/v1/cases", headers=auth_alex).json()
    assert set(body) == {"items", "page"}
    assert set(body["page"]) == {"limit", "has_more", "next_cursor"}
    assert body["page"]["next_cursor"] is None
    assert body["page"]["has_more"] is False
    assert "total_count" not in body["page"], "section 5.2: there is no total_count"


def test_an_out_of_range_limit_is_rejected_over_http(client, auth_alex) -> None:
    response = client.get("/v1/cases?limit=500", headers=auth_alex)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAGE_SIZE"


def test_a_cursor_from_another_filter_is_rejected_over_http(client, auth_alex) -> None:
    cursor = encode_cursor(
        ["2026-06-05T14:22:31.482Z"],
        uuid.uuid4(),
        filter_fingerprint(status=["REOPENED"]),
        key=KEY,
    )
    response = client.get(f"/v1/cases?status=DISPUTED&cursor={cursor}", headers=auth_alex)
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_CURSOR"
    assert body["error"]["details"]["reason"] == "FILTER_CHANGED"


def test_a_cursor_minted_for_one_collection_does_not_work_on_another(client, auth_alex) -> None:
    """The fingerprint includes the collection, so cursors do not cross endpoints."""
    cursor = client.get("/v1/cases?limit=1", headers=auth_alex).json()["page"]["next_cursor"]
    if cursor is None:
        cursor = encode_cursor(
            ["2026-06-05T14:22:31.482Z"],
            uuid.uuid4(),
            filter_fingerprint(collection="cases"),
            key=KEY,
        )
    response = client.get(f"/v1/commitments?cursor={cursor}", headers=auth_alex)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"
