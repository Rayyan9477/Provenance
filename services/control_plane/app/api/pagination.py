"""Keyset pagination with the cursor bound to the query shape.

Authority: ``specs/15_API_SPEC.md`` section 5.

Offset pagination is prohibited (section 5): ``OFFSET`` produces duplicate or
skipped rows when new evidence arrives mid-scroll, which for a memory product
is a correctness bug. So every cursor carries the sort tuple, the last id, and
a fingerprint of the filters it was minted under. Replaying it against a
different filter is refused with ``400 INVALID_CURSOR`` rather than silently
restarting the scan -- a silent restart is how a user concludes the record is
incomplete.

The HMAC is not confidentiality. The payload is a timestamp and a UUID the
caller already has. It stops hand-crafted cursors, which would freeze the sort
tuple into a de-facto public contract.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = [
    "CURSOR_VERSION",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "PageInfo",
    "build_page",
    "decode_cursor",
    "encode_cursor",
    "filter_fingerprint",
    "parse_limit",
]

CURSOR_VERSION: Final[int] = 1
DEFAULT_LIMIT: Final[int] = 25
MIN_LIMIT: Final[int] = 1
MAX_LIMIT: Final[int] = 100

T = TypeVar("T")


class PageInfo(BaseModel):
    """Section 5.2. There is no ``total_count``: computing it needs a second
    full scan and it is stale by the time it renders."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=MIN_LIMIT, le=MAX_LIMIT)
    has_more: bool
    next_cursor: str | None = None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def filter_fingerprint(**filters: Any) -> str:
    """A short digest of the filters a cursor was minted under.

    ``None``-valued filters are dropped so that "absent" and "explicitly
    null" fingerprint identically -- otherwise a client that stops sending an
    unused optional parameter gets a spurious ``FILTER_CHANGED``.
    """
    canonical = json.dumps(
        {k: v for k, v in sorted(filters.items()) if v is not None},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:6]


def encode_cursor(
    sort_key: Sequence[Any], last_id: uuid.UUID, fingerprint: str, *, key: bytes
) -> str:
    payload = json.dumps(
        {
            "v": CURSOR_VERSION,
            "k": [str(part) for part in sort_key],
            "i": str(last_id),
            "f": fingerprint,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(key, payload, hashlib.sha256).digest()[:12]
    return f"{_b64(payload)}.{_b64(signature)}"


def decode_cursor(cursor: str, fingerprint: str, *, key: bytes) -> tuple[list[str], uuid.UUID]:
    try:
        body, signature = cursor.split(".", 1)
        payload = _unb64(body)
        expected = hmac.new(key, payload, hashlib.sha256).digest()[:12]
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ApiError(ErrorCode.INVALID_CURSOR, details={"reason": "SIGNATURE_INVALID"})
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise TypeError("cursor payload is not an object")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, details={"reason": "MALFORMED"}) from exc
    if data.get("v") != CURSOR_VERSION:
        raise ApiError(ErrorCode.INVALID_CURSOR, details={"reason": "VERSION_UNSUPPORTED"})
    if data.get("f") != fingerprint:
        raise ApiError(ErrorCode.INVALID_CURSOR, details={"reason": "FILTER_CHANGED"})
    try:
        return [str(part) for part in data["k"]], uuid.UUID(str(data["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, details={"reason": "MALFORMED"}) from exc


def parse_limit(raw: int | str | None) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(
            ErrorCode.INVALID_PAGE_SIZE,
            details={"min": MIN_LIMIT, "max": MAX_LIMIT, "received": raw},
        ) from exc
    if value < MIN_LIMIT or value > MAX_LIMIT:
        raise ApiError(
            ErrorCode.INVALID_PAGE_SIZE,
            details={"min": MIN_LIMIT, "max": MAX_LIMIT, "received": value},
        )
    return value


def build_page(
    items: Sequence[dict[str, Any]],
    *,
    limit: int,
    has_more: bool,
    id_field: str,
    sort_fields: Sequence[str],
    fingerprint: str,
    key: bytes,
) -> PageInfo:
    """Build the ``page`` object from the **retained** rows.

    Section 5.4 rule 1: the query asks for ``limit + 1``; the extra row is
    dropped and the cursor is built from the last row actually returned. A
    cursor built from the dropped row skips it on the next page.
    """
    if not has_more or not items:
        return PageInfo(limit=limit, has_more=False, next_cursor=None)
    last = items[-1]
    return PageInfo(
        limit=limit,
        has_more=True,
        next_cursor=encode_cursor(
            [last[field] for field in sort_fields],
            uuid.UUID(str(last[id_field])),
            fingerprint,
            key=key,
        ),
    )
