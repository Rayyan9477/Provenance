"""T8.2 -- RS256 verification and the JWKS cache.

Authority: `specs/15_API_SPEC.md` section 2.3 steps 1-6.

The verifier is standard library only. `pyjwt` and `cryptography` are both
present on this machine and neither is declared in the root `pyproject.toml`,
which is Integrator-owned; a boundary test that passes because of an
undeclared transitive dependency is not a test. So the PKCS#1 v1.5 check is
implemented directly and its padding is verified in full -- a verifier that
only compares the trailing digest is forgeable for small exponents, and that
is precisely the class of bug this file exists to make visible.
"""

from __future__ import annotations

import base64
import hashlib
import time

import pytest
from _support.rsa import RsaKeyPair, b64u

from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth.jwt import (
    JWKS_REFRESH_COOLDOWN_SECONDS,
    JWKS_TTL_SECONDS,
    CachingJwksProvider,
    StaticJwksProvider,
    decode_and_verify,
    verify_rs256,
)

pytestmark = pytest.mark.unit

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pvTEST"


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    base: dict[str, object] = {
        "iss": ISSUER,
        "client_id": "1web000000000000000000000w",
        "token_use": "access",
        "scope": "provenance.memory/read",
        "sub": "sub-alex-0001",
        "iat": now,
        "nbf": now - 60,
        "exp": now + 3600,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The primitive
# --------------------------------------------------------------------------


def test_a_genuine_signature_verifies(signing_key: RsaKeyPair) -> None:
    message = b"header.payload"
    digest = hashlib.sha256(message).digest()
    from services.control_plane.app.auth.jwt import _pkcs1_v15_encode

    size = (signing_key.n.bit_length() + 7) // 8
    em = _pkcs1_v15_encode(digest, size)
    signature = pow(int.from_bytes(em, "big"), signing_key.d, signing_key.n).to_bytes(size, "big")
    assert verify_rs256(message, signature, signing_key.n, signing_key.e) is True


def test_a_forged_signature_does_not_verify(signing_key: RsaKeyPair) -> None:
    size = (signing_key.n.bit_length() + 7) // 8
    assert verify_rs256(b"m", b"\x00" * size, signing_key.n, signing_key.e) is False


def test_a_signature_of_the_wrong_length_is_rejected(signing_key: RsaKeyPair) -> None:
    assert verify_rs256(b"m", b"\x01\x02", signing_key.n, signing_key.e) is False


def test_the_full_padding_block_is_compared_not_just_the_digest(
    signing_key: RsaKeyPair,
) -> None:
    """The Bleichenbacher shape: a DigestInfo placed at the *front* of the
    block, with garbage after it, must not verify."""
    message = b"header.payload"
    digest = hashlib.sha256(message).digest()
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    size = (signing_key.n.bit_length() + 7) // 8
    sloppy = b"\x00\x01\xff\x00" + prefix + digest
    sloppy += b"\x00" * (size - len(sloppy))
    signature = pow(int.from_bytes(sloppy, "big"), signing_key.d, signing_key.n).to_bytes(
        size, "big"
    )
    assert verify_rs256(message, signature, signing_key.n, signing_key.e) is False


# --------------------------------------------------------------------------
# The JWKS cache
# --------------------------------------------------------------------------


async def test_the_static_provider_returns_the_published_key(signing_key: RsaKeyPair) -> None:
    provider = StaticJwksProvider(signing_key.jwks())
    key = await provider.get_key(signing_key.kid)
    assert key is not None
    assert key["kid"] == signing_key.kid


async def test_an_unknown_kid_returns_none(signing_key: RsaKeyPair) -> None:
    provider = StaticJwksProvider(signing_key.jwks())
    assert await provider.get_key("nope") is None


async def test_the_cache_refetches_once_on_an_unknown_kid(
    signing_key: RsaKeyPair, other_key: RsaKeyPair
) -> None:
    calls: list[int] = []
    published = [signing_key.jwks()]

    async def fetch() -> dict[str, object]:
        calls.append(1)
        return published[0]

    clock = [1000.0]
    provider = CachingJwksProvider(fetch, clock=lambda: clock[0])

    assert await provider.get_key(signing_key.kid) is not None
    assert len(calls) == 1

    # Unknown kid -> one forced refresh.
    assert await provider.get_key(other_key.kid) is None
    assert len(calls) == 2

    # Still unknown, still inside the cooldown -> no third fetch.
    assert await provider.get_key(other_key.kid) is None
    assert len(calls) == 2

    # After the cooldown a rotated key is picked up.
    published[0] = other_key.jwks()
    clock[0] += JWKS_REFRESH_COOLDOWN_SECONDS + 1
    assert await provider.get_key(other_key.kid) is not None
    assert len(calls) == 3


async def test_the_cache_expires_after_its_ttl(signing_key: RsaKeyPair) -> None:
    calls: list[int] = []

    async def fetch() -> dict[str, object]:
        calls.append(1)
        return signing_key.jwks()

    clock = [0.0]
    provider = CachingJwksProvider(fetch, clock=lambda: clock[0])
    await provider.get_key(signing_key.kid)
    await provider.get_key(signing_key.kid)
    assert len(calls) == 1
    clock[0] += JWKS_TTL_SECONDS + 1
    await provider.get_key(signing_key.kid)
    assert len(calls) == 2


# --------------------------------------------------------------------------
# decode_and_verify
# --------------------------------------------------------------------------


async def test_a_valid_token_decodes_to_typed_claims(signing_key: RsaKeyPair) -> None:
    token = signing_key.sign_jws(_claims())
    claims = await decode_and_verify(
        token, jwks=StaticJwksProvider(signing_key.jwks()), issuer=ISSUER, now=time.time()
    )
    assert claims.client_id == "1web000000000000000000000w"
    assert claims.scopes == frozenset({"provenance.memory/read"})
    assert claims.sub == "sub-alex-0001"


@pytest.mark.parametrize("token", ["", "a", "a.b", "a.b.c.d", "...", "!!.??.$$"])
async def test_a_structurally_broken_token_is_401(signing_key: RsaKeyPair, token: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        await decode_and_verify(
            token, jwks=StaticJwksProvider(signing_key.jwks()), issuer=ISSUER, now=time.time()
        )
    assert excinfo.value.http_status == 401


async def test_an_unexpected_algorithm_is_refused(signing_key: RsaKeyPair) -> None:
    token = signing_key.sign_jws(_claims(), alg="HS256")
    with pytest.raises(ApiError) as excinfo:
        await decode_and_verify(
            token, jwks=StaticJwksProvider(signing_key.jwks()), issuer=ISSUER, now=time.time()
        )
    assert excinfo.value.code is ErrorCode.TOKEN_INVALID_SIGNATURE
    assert excinfo.value.details["reason"] == "UNEXPECTED_ALGORITHM"


async def test_the_none_algorithm_is_refused(signing_key: RsaKeyPair) -> None:
    header = b64u(b'{"alg":"none","kid":"' + signing_key.kid.encode() + b'"}')
    import json

    payload = b64u(json.dumps(_claims()).encode())
    with pytest.raises(ApiError):
        await decode_and_verify(
            f"{header}.{payload}.",
            jwks=StaticJwksProvider(signing_key.jwks()),
            issuer=ISSUER,
            now=time.time(),
        )


async def test_a_token_with_no_kid_is_refused(signing_key: RsaKeyPair) -> None:
    import json

    header = b64u(json.dumps({"alg": "RS256"}).encode())
    payload = b64u(json.dumps(_claims()).encode())
    signing_input = f"{header}.{payload}".encode()
    size = (signing_key.n.bit_length() + 7) // 8
    from services.control_plane.app.auth.jwt import _pkcs1_v15_encode

    em = _pkcs1_v15_encode(hashlib.sha256(signing_input).digest(), size)
    sig = pow(int.from_bytes(em, "big"), signing_key.d, signing_key.n).to_bytes(size, "big")
    with pytest.raises(ApiError):
        await decode_and_verify(
            f"{header}.{payload}.{b64u(sig)}",
            jwks=StaticJwksProvider(signing_key.jwks()),
            issuer=ISSUER,
            now=time.time(),
        )


async def test_a_payload_that_is_not_an_object_is_refused(signing_key: RsaKeyPair) -> None:
    import json

    header = b64u(json.dumps({"alg": "RS256", "kid": signing_key.kid}).encode())
    payload = b64u(b"[1,2,3]")
    signing_input = f"{header}.{payload}".encode()
    size = (signing_key.n.bit_length() + 7) // 8
    from services.control_plane.app.auth.jwt import _pkcs1_v15_encode

    em = _pkcs1_v15_encode(hashlib.sha256(signing_input).digest(), size)
    sig = pow(int.from_bytes(em, "big"), signing_key.d, signing_key.n).to_bytes(size, "big")
    with pytest.raises(ApiError):
        await decode_and_verify(
            f"{header}.{payload}.{b64u(sig)}",
            jwks=StaticJwksProvider(signing_key.jwks()),
            issuer=ISSUER,
            now=time.time(),
        )


async def test_the_raw_token_never_appears_in_the_error_details(signing_key: RsaKeyPair) -> None:
    token = signing_key.tamper(signing_key.sign_jws(_claims()))
    with pytest.raises(ApiError) as excinfo:
        await decode_and_verify(
            token, jwks=StaticJwksProvider(signing_key.jwks()), issuer=ISSUER, now=time.time()
        )
    rendered = f"{excinfo.value.message}{excinfo.value.details}"
    assert token not in rendered
    assert token.split(".")[2] not in rendered


def test_base64url_decoding_tolerates_missing_padding() -> None:
    raw = b"any bytes at all!"
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    from services.control_plane.app.auth.jwt import b64u_decode

    assert b64u_decode(encoded) == raw
