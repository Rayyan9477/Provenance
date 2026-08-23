"""Cognito access-token verification.

Authority: ``specs/15_API_SPEC.md`` section 2.3, steps 1-6.

Why the RS256 check is written here rather than imported
---------------------------------------------------------
``pyjwt`` and ``cryptography`` are both installed on the current build
machine and **neither is declared** in the root ``pyproject.toml``. That file
is Integrator-owned and outside this task's boundary
(``EXECUTION/71_AGENT_WORKFLOW.md`` section 7), so depending on either would
make the entire auth boundary pass on this machine and fail to import on a
clean clone -- the exact shape of D-00-005, where a suite existed and nothing
ran it.

RSASSA-PKCS1-v1_5 *verification* is a public-key operation over public data:
``sig^e mod n``, then compare the recovered block to the one the signer must
have produced. It is implemented in full below, and the comparison is over the
**entire** encoded message, not just the trailing digest. That distinction is
the difference between a correct verifier and a Bleichenbacher-forgeable one,
and ``tests/auth/test_jwt.py`` asserts it directly.

If the Integrator later adds ``pyjwt[crypto]`` to the ``control-plane``
extra, this module is the single place to swap; nothing else in the tree
touches a signature.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = [
    "JWKS_REFRESH_COOLDOWN_SECONDS",
    "JWKS_TTL_SECONDS",
    "CachingJwksProvider",
    "JwksProvider",
    "StaticJwksProvider",
    "TokenClaims",
    "b64u_decode",
    "decode_and_verify",
    "https_jwks_fetcher",
    "require_configured_issuer",
    "space_delimited_scopes",
    "token_window",
    "unauthenticated",
    "verified_payload",
    "verify_rs256",
]

#: Section 2.3 step 2: JWKS cached for 12 hours, forced refresh on an unknown
#: `kid`, rate-limited to once per 5 minutes.
JWKS_TTL_SECONDS: Final[int] = 12 * 60 * 60
JWKS_REFRESH_COOLDOWN_SECONDS: Final[int] = 5 * 60

#: DER prefix of a SHA-256 DigestInfo inside PKCS#1 v1.5.
_SHA256_DIGEST_INFO: Final[bytes] = bytes.fromhex("3031300d060960864801650304020105000420")

_MIN_PADDING = 8


def b64u_decode(value: str) -> bytes:
    """base64url without padding, as JOSE encodes every segment."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _pkcs1_v15_encode(digest: bytes, size: int) -> bytes:
    """EMSA-PKCS1-v1_5 for SHA-256, per RFC 8017 section 9.2."""
    tail = _SHA256_DIGEST_INFO + digest
    padding_length = size - len(tail) - 3
    if padding_length < _MIN_PADDING:
        raise ValueError("modulus too small for a SHA-256 PKCS#1 v1.5 signature")
    return b"\x00\x01" + b"\xff" * padding_length + b"\x00" + tail


def verify_rs256(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """Strict RSASSA-PKCS1-v1_5 verification with SHA-256.

    The whole encoded message is reconstructed and compared, so a signature
    whose block merely *contains* the right DigestInfo somewhere does not
    verify. ``compare_digest`` is used out of habit rather than necessity:
    both operands here are public.
    """
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= n:
        return False
    recovered = pow(value, e, n).to_bytes(size, "big")
    try:
        expected = _pkcs1_v15_encode(hashlib.sha256(signing_input).digest(), size)
    except ValueError:
        return False
    return hmac.compare_digest(recovered, expected)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The verified claims, and nothing else.

    Section 2.3 step 9: "The raw JWT is discarded here and is never passed
    into business modules, never logged, and never placed into LangGraph
    state." There is therefore no field holding it.
    """

    issuer: str
    client_id: str
    scopes: frozenset[str]
    sub: str | None
    groups: frozenset[str]
    issued_at: int
    not_before: int
    expires_at: int


class JwksProvider(Protocol):
    async def get_key(self, kid: str) -> Mapping[str, Any] | None: ...


class StaticJwksProvider:
    """A fixed key set. Used by the hermetic suites and by nothing else."""

    def __init__(self, jwks: Mapping[str, Any]) -> None:
        self._keys = {str(k.get("kid")): k for k in jwks.get("keys", [])}

    async def get_key(self, kid: str) -> Mapping[str, Any] | None:
        return self._keys.get(kid)


class CachingJwksProvider:
    """Section 2.3 step 2.

    An unknown ``kid`` triggers **one** forced refresh, then is refused until
    the cooldown elapses. Without the cooldown a stream of tokens naming
    random ``kid`` values is a free amplification attack against Cognito's
    JWKS endpoint.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[Mapping[str, Any]]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, Mapping[str, Any]] = {}
        self._fetched_at: float | None = None
        self._last_forced: float | None = None

    async def _refresh(self) -> None:
        jwks = await self._fetch()
        self._keys = {str(k.get("kid")): k for k in jwks.get("keys", [])}
        self._fetched_at = self._clock()

    async def get_key(self, kid: str) -> Mapping[str, Any] | None:
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at > JWKS_TTL_SECONDS:
            await self._refresh()
        key = self._keys.get(kid)
        if key is not None:
            return key
        if (
            self._last_forced is not None
            and now - self._last_forced < JWKS_REFRESH_COOLDOWN_SECONDS
        ):
            return None
        self._last_forced = now
        await self._refresh()
        return self._keys.get(kid)


def https_jwks_fetcher(url: str, *, timeout: float = 5.0) -> Callable[[], Awaitable[Any]]:
    """Fetch a JWKS over HTTPS using the standard library.

    ``urllib`` is blocking, so the call is pushed onto a worker thread. This
    runs at request time on a cold cache, never inside a database transaction
    -- ``tools/txn_purity_lint.py`` forbids a network call in a transaction
    callback, and this module has no transaction to be inside of.
    """

    async def _fetch() -> Any:
        import asyncio
        import urllib.request

        def _blocking() -> Any:
            if not url.startswith("https://"):
                raise ApiError(
                    ErrorCode.INTERNAL_ERROR,
                    message="The identity provider endpoint must be HTTPS.",
                )
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        return await asyncio.to_thread(_blocking)

    return _fetch


def unauthenticated(reason: str) -> ApiError:
    """A ``401`` that names the structural failure and nothing else.

    Public because the Google and local providers raise the same refusals for
    the same structural reasons; a second private copy of this would drift.
    """
    return ApiError(ErrorCode.UNAUTHENTICATED, details={"reason": reason})


#: Retained under its former private name. ``tests/auth`` and the adversarial
#: suite both reach for module internals, and renaming a symbol a test names
#: is a change to the test rather than to the code.
_unauthenticated = unauthenticated


async def verified_payload(
    token: str, *, jwks: JwksProvider
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Steps 1-3 of section 2.3: structure, header, ``kid``, signature.

    Everything here is provider-independent -- RS256 over a JWKS is RS256 over
    a JWKS whether Cognito, Google Identity Platform or the local development
    issuer signed it. Only the *claim* checks differ between providers, and
    those happen in the callers, which is why this returns raw mappings rather
    than :class:`TokenClaims`.

    Returns ``(header, payload)``. The payload is decoded **after** the
    signature verifies, so no unverified JSON reaches a claim check.
    """
    parts = token.split(".")
    if len(parts) != 3 or not all(parts[:2]):
        raise unauthenticated("MALFORMED_TOKEN")
    header_b64, payload_b64, signature_b64 = parts

    try:
        header = json.loads(b64u_decode(header_b64))
    except Exception as exc:
        raise unauthenticated("MALFORMED_HEADER") from exc
    if not isinstance(header, dict):
        raise unauthenticated("MALFORMED_HEADER")

    if header.get("alg") != "RS256":
        raise ApiError(
            ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "UNEXPECTED_ALGORITHM"}
        )
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ApiError(ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "MISSING_KID"})

    key = await jwks.get_key(kid)
    if key is None:
        raise ApiError(ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "UNKNOWN_KID"})

    try:
        n = int.from_bytes(b64u_decode(str(key["n"])), "big")
        e = int.from_bytes(b64u_decode(str(key["e"])), "big")
        signature = b64u_decode(signature_b64)
    except Exception as exc:
        raise ApiError(
            ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "MALFORMED_SIGNATURE"}
        ) from exc

    if not verify_rs256(f"{header_b64}.{payload_b64}".encode("ascii"), signature, n, e):
        raise ApiError(ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "SIGNATURE_MISMATCH"})

    try:
        payload = json.loads(b64u_decode(payload_b64))
    except Exception as exc:
        raise unauthenticated("MALFORMED_PAYLOAD") from exc
    if not isinstance(payload, dict):
        raise unauthenticated("MALFORMED_PAYLOAD")

    return header, payload


def require_configured_issuer(payload: Mapping[str, Any], expected: object) -> None:
    """Step 4, with the misconfiguration case made loud.

    ``payload.get("iss") != expected`` reads as a safe comparison and is not
    one. If *expected* is ``None`` -- which is exactly what ``PV_PLATFORM``
    made reachable the moment ``Settings.cognito_issuer`` widened to
    ``str | None`` -- then a token that simply **omits** ``iss`` compares equal
    and passes. The check silently inverts from "prove who signed this" into
    "say nothing and be believed".

    So both sides are proved to be non-empty strings before they are compared.
    A misconfigured server is a ``500``: it is our fault rather than the
    caller's, and answering ``401`` sends an operator hunting for a bad token.
    """
    if not isinstance(expected, str) or not expected:
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            message="This deployment has no identity provider issuer configured.",
        )
    issuer = payload.get("iss")
    if not isinstance(issuer, str) or not issuer or issuer != expected:
        raise ApiError(ErrorCode.TOKEN_WRONG_ISSUER, details={"expected_issuer": expected})


def token_window(
    payload: Mapping[str, Any], *, now: float, leeway_seconds: int
) -> tuple[int, int, int]:
    """Step 6. Returns ``(issued_at, not_before, expires_at)``.

    ``exp`` is required; ``iat`` and ``nbf`` fall back to it and to each other,
    because an issuer is free to omit either and a missing optional claim is
    not a forgery.
    """
    try:
        expires_at = int(payload["exp"])
        issued_at = int(payload.get("iat", expires_at))
        not_before = int(payload.get("nbf", issued_at))
    except (KeyError, TypeError, ValueError) as exc:
        raise unauthenticated("MALFORMED_CLAIMS") from exc

    if now > expires_at + leeway_seconds:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, details={"expired_at": _iso(expires_at)})
    if now + leeway_seconds < not_before:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, details={"expired_at": _iso(not_before)})
    return issued_at, not_before, expires_at


def space_delimited_scopes(raw: object) -> frozenset[str]:
    """A JOSE ``scope`` claim is one space-delimited string, or absent.

    Absent becomes the empty set rather than an error. Whether the *route*
    needed a scope is a separate question, asked later and answered with
    ``403 INSUFFICIENT_SCOPE``.
    """
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(raw.split())


async def decode_and_verify(
    token: str,
    *,
    jwks: JwksProvider,
    issuer: str,
    now: float,
    leeway_seconds: int = 60,
) -> TokenClaims:
    """Steps 1-6 of section 2.3, in that order. Cognito semantics.

    Order matters and is asserted: signature before issuer, issuer before
    ``token_use``, ``token_use`` before expiry. Checking expiry first would
    let an attacker distinguish "signed by us but stale" from "not signed by
    us" using nothing but the clock.
    """
    _, payload = await verified_payload(token, jwks=jwks)

    require_configured_issuer(payload, issuer)

    if payload.get("token_use") != "access":
        raise ApiError(
            ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "ID_TOKEN_NOT_ACCEPTED"}
        )

    issued_at, not_before, expires_at = token_window(
        payload, now=now, leeway_seconds=leeway_seconds
    )

    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise unauthenticated("MISSING_CLIENT_ID")

    groups = payload.get("cognito:groups") or []

    return TokenClaims(
        issuer=issuer,
        client_id=client_id,
        scopes=space_delimited_scopes(payload.get("scope")),
        sub=str(payload["sub"]) if payload.get("sub") else None,
        groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
    )


def _iso(epoch_seconds: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat().replace("+00:00", "Z")
