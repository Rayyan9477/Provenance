"""The capability proof header.

Authority: ``specs/15_API_SPEC.md`` section 3.5.

Capability ids are UUIDv7: unguessable, but not secret. They appear in
``agent_runs`` rows, in traces, and in Judge Mode. The proof binds an id to
the dispatch that created it, so replay of an id observed in a trace fails
closed.

The proof is explicitly **not** the primary control -- the server-side record
is. It narrows the window in which a leaked id is usable. That ordering is why
:func:`verify_capability_proof` takes ``expires_at`` from the loaded row and
never from the request: a proof cannot extend its own validity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import datetime

from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = ["issue_capability_proof", "verify_capability_proof"]

_MAC_BYTES = 16


def _message(kind: str, capability_key: str | uuid.UUID, expires_at: datetime) -> bytes:
    return f"{kind}:{capability_key}:{int(expires_at.timestamp())}".encode()


def issue_capability_proof(
    kind: str, capability_key: str | uuid.UUID, expires_at: datetime, *, key: bytes
) -> str:
    mac = hmac.new(key, _message(kind, capability_key, expires_at), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:_MAC_BYTES]).decode("ascii").rstrip("=")


def verify_capability_proof(
    kind: str,
    capability_key: str | uuid.UUID,
    expires_at: datetime,
    presented: str | None,
    *,
    key: bytes,
) -> None:
    expected = issue_capability_proof(kind, capability_key, expires_at, key=key)
    if presented is None or not hmac.compare_digest(expected, presented):
        raise ApiError(ErrorCode.CAPABILITY_PROOF_INVALID, details={"capability_kind": kind})
