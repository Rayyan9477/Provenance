"""An ephemeral RSA keypair and a JWS signer, in pure standard library.

Why this exists at all
----------------------
The control plane verifies RS256 Cognito access tokens. Testing that path
needs a private key. Two options were rejected:

* checking a PEM into the repository — ``G0.3`` scans for credential-shaped
  literals and a private key is the archetype;
* depending on ``pyjwt`` / ``cryptography`` — neither is declared in the root
  ``pyproject.toml``, which is Integrator-owned and outside this task's
  boundary. A test that passes only on a machine with an undeclared package
  installed is not a test.

So the keypair is generated in-process, once per session, and thrown away.
Signing is ``pow(m, d, n)``; nothing secret is written anywhere.

Key size is 1024 bits. That is not a production key size and this is not a
production key — it is a throwaway fixture whose only job is to make a
signature verify. Generation of a 2048-bit pair in pure Python costs seconds
on every test session for no additional assurance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any

__all__ = ["RsaKeyPair", "b64u", "generate_keypair"]

# Sieve of small primes, used to discard obvious composites cheaply before the
# expensive Miller-Rabin rounds.
_SMALL_PRIMES: tuple[int, ...] = (
    2,
    3,
    5,
    7,
    11,
    13,
    17,
    19,
    23,
    29,
    31,
    37,
    41,
    43,
    47,
    53,
    59,
    61,
    67,
    71,
    73,
    79,
    83,
    89,
    97,
    101,
    103,
    107,
    109,
    113,
    127,
    131,
    137,
    139,
    149,
    151,
    157,
    163,
    167,
    173,
    179,
    181,
    191,
    193,
    197,
    199,
    211,
    223,
    227,
    229,
)


def b64u(raw: bytes) -> str:
    """URL-safe base64 without padding — the JOSE encoding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _is_probable_prime(n: int, *, rounds: int = 24) -> bool:
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


@dataclass(frozen=True)
class RsaKeyPair:
    """A throwaway RSA keypair plus the JWKS entry that describes it."""

    kid: str
    n: int
    e: int
    d: int

    @property
    def modulus_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def public_jwk(self) -> dict[str, str]:
        size = self.modulus_bytes
        return {
            "kty": "RSA",
            "kid": self.kid,
            "use": "sig",
            "alg": "RS256",
            "n": b64u(self.n.to_bytes(size, "big")),
            "e": b64u(self.e.to_bytes((self.e.bit_length() + 7) // 8, "big")),
        }

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.public_jwk()]}

    def sign_jws(
        self, claims: dict[str, Any], *, kid: str | None = None, alg: str = "RS256"
    ) -> str:
        """Produce a compact JWS over *claims*.

        ``kid`` and ``alg`` are overridable so a suite can present a token
        signed by an unknown key or naming an unexpected algorithm.
        """
        header = {"alg": alg, "kid": kid or self.kid, "typ": "JWT"}
        segments = [
            b64u(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
            b64u(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()),
        ]
        signing_input = ".".join(segments).encode("ascii")
        digest = hashlib.sha256(signing_input).digest()
        em = _pkcs1_v15_encode(digest, self.modulus_bytes)
        signature = pow(int.from_bytes(em, "big"), self.d, self.n)
        segments.append(b64u(signature.to_bytes(self.modulus_bytes, "big")))
        return ".".join(segments)

    def tamper(self, token: str) -> str:
        """Return *token* with a single bit flipped inside the signature."""
        head, payload, sig = token.split(".")
        raw = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
        raw[-1] ^= 0x01
        return f"{head}.{payload}.{b64u(bytes(raw))}"


#: DER prefix for SHA-256 inside a PKCS#1 v1.5 DigestInfo.
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _pkcs1_v15_encode(digest: bytes, size: int) -> bytes:
    t = _SHA256_DIGEST_INFO + digest
    padding = b"\xff" * (size - len(t) - 3)
    return b"\x00\x01" + padding + b"\x00" + t


def generate_keypair(*, bits: int = 1024, kid: str = "pv-test-kid-1") -> RsaKeyPair:
    """Mint a fresh keypair. Called once per pytest session."""
    e = 65537
    while True:
        p = _random_prime(bits // 2)
        q = _random_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        return RsaKeyPair(kid=kid, n=n, e=e, d=pow(e, -1, phi))
