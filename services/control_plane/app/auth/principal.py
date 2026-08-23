"""Token -> :class:`Principal`.

Authority: ``specs/15_API_SPEC.md`` sections 2.3 (step 9) and 2.5.

On the column named ``cognito_sub``
------------------------------------
The column, the ``users`` row field and ``Principal.cognito_sub`` keep their
name while now holding **an opaque subject identifier from whichever identity
provider is configured** -- a Cognito ``sub``, a Google Identity Platform /
Firebase ``uid``, or a local development subject. Nothing about the lookup
changes: it is still one indexed equality on a value the issuer minted and the
caller cannot choose.

The name was left alone deliberately, and the reasoning is recorded rather
than smoothed over. There are 139 references across 37 non-infra files, and
they sit in ``packages/python/provenance_db``, ``scripts/seed``,
``app/retrieval``, ``provenance_contracts.identity`` and eight documents --
every one of which belongs to a different agent working concurrently. A rename
that reached only the files this task owns would leave the tree in the one
state worse than either option: half renamed, with two names for one column
and a migration that half the code does not know about. So the column keeps
its name and this docstring carries the meaning.

The resolution is a database lookup on ``cognito_sub``, never a token claim.
Section 2.5 gives the query; the reason is section 3.1's second failure mode:
a custom claim is attacker-influencable in a way a ``users`` row is not. A
token carrying ``custom:tenant_id`` therefore changes nothing, and
``tests/auth/test_auth_principal.py`` presents exactly such a token to prove
it.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from provenance_contracts.identity import Principal
from provenance_domain.enums import OAuthScope
from services.control_plane.app.api.config import ApiConfig
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.api.ports import UserDirectory
from services.control_plane.app.auth.jwt import JwksProvider, TokenClaims

__all__ = ["build_human_principal", "judge_mode_for", "known_scopes"]


def known_scopes(raw: frozenset[str]) -> frozenset[OAuthScope]:
    """Keep only the seven canon scopes.

    An unrecognised scope string is dropped rather than rejected: Cognito can
    legitimately mint a token carrying a scope this build does not know about
    yet, and section 1.3 requires graceful degradation on unknown enum
    members. Dropping is safe because every authorisation decision asks
    whether a *required* scope is present, never whether an unknown one is
    absent.
    """
    valid = {str(s) for s in OAuthScope}
    return frozenset(OAuthScope(s) for s in raw if s in valid)


def judge_mode_for(claims: TokenClaims, *, config: ApiConfig, allowlisted: bool) -> bool:
    """Section 2.5. It gates sections 8.30-8.31 only, and grants no
    cross-user visibility -- the isolation suite asserts that separately."""
    if config.judge_group and config.judge_group in claims.groups:
        return True
    return config.judge_allowlist_enabled and allowlisted


async def build_human_principal(
    token: str,
    *,
    config: ApiConfig,
    jwks: JwksProvider,
    users: UserDirectory,
    trace_id: uuid.UUID,
    request_id: uuid.UUID,
    now: datetime,
) -> Principal:
    # The token window is evaluated against the **later** of the injected
    # clock and the real one. `now` is the domain clock, which the hermetic
    # suites freeze so that timestamps in responses are deterministic; token
    # expiry is not a domain fact, and a frozen or rewound clock must never be
    # able to resurrect an expired access token. Taking the maximum makes the
    # injected clock able to move the check *forward* (a test may assert on an
    # expiry) but never backward.
    claims = await config.provider.verify(
        token,
        jwks=jwks,
        now=max(now.timestamp(), time.time()),
        leeway_seconds=config.clock_skew_seconds,
    )
    if claims.sub is None:
        # A client-credentials token has no `sub` by design (section 2.2). It
        # is not a human and must not be resolved as one.
        raise ApiError(ErrorCode.USER_NOT_PROVISIONED)

    record = await users.by_cognito_sub(claims.sub)
    if record is None:
        raise ApiError(ErrorCode.USER_NOT_PROVISIONED)

    return Principal(
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        cognito_sub=record.cognito_sub,
        email=record.email,
        display_name=record.display_name,
        timezone=record.timezone,
        scopes=known_scopes(claims.scopes),
        # The window opens at the earlier of `iat` and `nbf`. A token is
        # presentable from `nbf`, and Cognito is free to emit one before
        # `iat`; taking `iat` alone would describe a window the token was
        # already usable outside of. It also keeps `Principal`'s
        # "expires after issued" invariant satisfiable for a token that is
        # inside the clock-skew tolerance but whose `exp` has just passed --
        # which is a real token, accepted three lines above, and must not
        # become a 500 on the way into the type.
        token_issued_at=datetime.fromtimestamp(min(claims.issued_at, claims.not_before), tz=UTC),
        token_expires_at=datetime.fromtimestamp(claims.expires_at, tz=UTC),
        request_id=request_id,
        trace_id=trace_id,
    )
