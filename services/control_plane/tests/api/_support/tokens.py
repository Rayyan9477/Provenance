"""Mint Cognito-shaped access tokens for the hermetic suites."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from _support.rsa import RsaKeyPair

__all__ = [
    "AGENT_CLIENT_ID",
    "ISSUER",
    "WEB_CLIENT_ID",
    "WORKER_CLIENT_ID",
    "agent_token",
    "human_token",
    "worker_token",
]

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pvTEST"
WEB_CLIENT_ID = "1web000000000000000000000w"
AGENT_CLIENT_ID = "2agent00000000000000000000"
WORKER_CLIENT_ID = "3worker0000000000000000000"


def _base(
    key: RsaKeyPair,
    *,
    client_id: str,
    scopes: Iterable[str],
    sub: str | None,
    groups: Sequence[str] = (),
    token_use: str = "access",
    issuer: str | None = None,
    expires_in: int = 3600,
    not_before_offset: int = -60,
    kid: str | None = None,
    alg: str = "RS256",
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer if issuer is not None else ISSUER,
        "client_id": client_id,
        "token_use": token_use,
        "scope": " ".join(scopes),
        "iat": now,
        "nbf": now + not_before_offset,
        "exp": now + expires_in,
        "jti": f"jti-{now}-{client_id}",
    }
    if sub is not None:
        claims["sub"] = sub
    if groups:
        claims["cognito:groups"] = list(groups)
    return key.sign_jws(claims, kid=kid, alg=alg)


def human_token(key: RsaKeyPair, *, sub: str, groups: Sequence[str] = (), **kw: object) -> str:
    return _base(
        key,
        client_id=str(kw.pop("client_id", WEB_CLIENT_ID)),
        scopes=kw.pop("scopes", ("provenance.memory/read",)),  # type: ignore[arg-type]
        sub=sub,
        groups=groups,
        **kw,  # type: ignore[arg-type]
    )


def agent_token(key: RsaKeyPair, **kw: object) -> str:
    return _base(
        key,
        client_id=str(kw.pop("client_id", AGENT_CLIENT_ID)),
        scopes=kw.pop(  # type: ignore[arg-type]
            "scopes",
            ("provenance.memory/read", "provenance.memory/propose", "provenance.action/propose"),
        ),
        sub=None,
        **kw,  # type: ignore[arg-type]
    )


def worker_token(key: RsaKeyPair, **kw: object) -> str:
    return _base(
        key,
        client_id=str(kw.pop("client_id", WORKER_CLIENT_ID)),
        scopes=kw.pop(  # type: ignore[arg-type]
            "scopes",
            (
                "provenance.ingest/write",
                "provenance.trigger/evaluate",
                "provenance.action/execute",
                "provenance.outbox/dispatch",
            ),
        ),
        sub=None,
        **kw,  # type: ignore[arg-type]
    )
