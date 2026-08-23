"""T8.2 -- Cognito verification and the `Principal` mapping.

Authority: `specs/15_API_SPEC.md` sections 2.3, 2.5, 2.6. Feeds `G8.2`,
`G8.3`, `G8.4`, `G8.8`.

Step 9 of section 2.3 is the one that matters most here: the raw JWT is
discarded at the boundary and never enters a business module. Every assertion
below is written against that boundary rather than against a handler.
"""

from __future__ import annotations

import time
import uuid

import pytest
from _support import fakes as fakes_mod
from _support.rsa import RsaKeyPair
from _support.tokens import ISSUER, WEB_CLIENT_ID, human_token

from provenance_contracts.identity import Principal

pytestmark = pytest.mark.unit


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 1
def test_a_missing_authorization_header_is_401_unauthenticated(client) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# 2
@pytest.mark.parametrize("value", ["", "Bearer", "Basic abc", "Bearer  ", "Bearerabc"])
def test_a_malformed_authorization_header_is_401(client, value: str) -> None:
    response = client.get("/v1/me", headers={"Authorization": value})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# 3
def test_an_unknown_kid_is_a_signature_failure(client, other_key: RsaKeyPair) -> None:
    token = human_token(other_key, sub=fakes_mod.ALEX.cognito_sub)
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID_SIGNATURE"


# 4
def test_a_tampered_signature_is_rejected(client, signing_key: RsaKeyPair) -> None:
    token = signing_key.tamper(human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub))
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID_SIGNATURE"


# 5
def test_a_wrong_issuer_is_named_as_such(client, signing_key: RsaKeyPair) -> None:
    token = human_token(
        signing_key, sub=fakes_mod.ALEX.cognito_sub, issuer="https://evil.example/pool"
    )
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "TOKEN_WRONG_ISSUER"
    assert body["error"]["details"]["expected_issuer"] == ISSUER


# 6
def test_an_id_token_is_never_accepted_for_api_authorisation(
    client, signing_key: RsaKeyPair
) -> None:
    token = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub, token_use="id")
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "TOKEN_INVALID_SIGNATURE"
    assert body["error"]["details"]["reason"] == "ID_TOKEN_NOT_ACCEPTED"


# 7
def test_an_expired_token_is_401_token_expired(client, signing_key: RsaKeyPair) -> None:
    token = human_token(
        signing_key, sub=fakes_mod.ALEX.cognito_sub, expires_in=-3600, not_before_offset=-7200
    )
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "TOKEN_EXPIRED"
    assert "expired_at" in body["error"]["details"]


# 8
def test_sixty_seconds_of_clock_skew_is_tolerated(client, signing_key: RsaKeyPair) -> None:
    token = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub, expires_in=-30)
    assert client.get("/v1/me", headers=_headers(token)).status_code == 200


# 9
def test_a_not_yet_valid_token_is_rejected(client, signing_key: RsaKeyPair) -> None:
    token = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub, not_before_offset=600)
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


# 10
def test_a_verified_sub_with_no_users_row_is_403_user_not_provisioned(
    client, signing_key: RsaKeyPair
) -> None:
    token = human_token(signing_key, sub="sub-nobody-here")
    response = client.get("/v1/me", headers=_headers(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_NOT_PROVISIONED"


# 11
def test_tenant_and_user_come_from_the_users_row_not_the_token(
    client, signing_key: RsaKeyPair
) -> None:
    """A forged `custom:tenant_id` claim changes nothing: the resolution is a
    database lookup keyed on `sub`, and the claim is never read."""
    now = int(time.time())
    forged = signing_key.sign_jws(
        {
            "iss": ISSUER,
            "client_id": WEB_CLIENT_ID,
            "token_use": "access",
            "scope": "provenance.memory/read",
            "sub": fakes_mod.ALEX.cognito_sub,
            "iat": now,
            "nbf": now - 60,
            "exp": now + 3600,
            "custom:tenant_id": str(fakes_mod.ROB.tenant_id),
            "custom:user_id": str(fakes_mod.ROB.user_id),
            "tenant_id": str(fakes_mod.ROB.tenant_id),
            "user_id": str(fakes_mod.ROB.user_id),
        }
    )
    body = client.get("/v1/me", headers=_headers(forged)).json()
    assert body["user_id"] == str(fakes_mod.ALEX.user_id)
    assert body["tenant_id"] == str(fakes_mod.ALEX.tenant_id)


# 12
def test_a_missing_required_scope_is_403_insufficient_scope(
    client, signing_key: RsaKeyPair
) -> None:
    token = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub, scopes=())
    response = client.get("/v1/cases", headers=_headers(token))
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "INSUFFICIENT_SCOPE"
    assert body["error"]["details"]["required_scope"] == "provenance.memory/read"


# 13
def test_judge_mode_comes_from_the_cognito_group_or_the_seeded_allowlist(
    client, signing_key: RsaKeyPair
) -> None:
    in_group = human_token(
        signing_key, sub=fakes_mod.ROB.cognito_sub, groups=("provenance-judges",)
    )
    assert client.get("/v1/me", headers=_headers(in_group)).json()["judge_mode_enabled"] is True

    not_in_group = human_token(signing_key, sub=fakes_mod.ROB.cognito_sub)
    assert (
        client.get("/v1/me", headers=_headers(not_in_group)).json()["judge_mode_enabled"] is False
    )

    allowlisted = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub)
    assert client.get("/v1/me", headers=_headers(allowlisted)).json()["judge_mode_enabled"] is True


# --------------------------------------------------------------------------
# The principal object itself
# --------------------------------------------------------------------------


def test_the_built_principal_is_the_contracts_type_and_carries_no_token(
    api_config, deps, signing_key: RsaKeyPair
) -> None:
    import asyncio

    from services.control_plane.app.auth.principal import build_human_principal

    token = human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub)
    principal = asyncio.run(
        build_human_principal(
            token,
            config=api_config,
            jwks=deps.jwks,
            users=deps.users,
            trace_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            now=deps.clock(),
        )
    )
    assert isinstance(principal, Principal)
    assert principal.tenant_id == fakes_mod.ALEX.tenant_id
    serialised = principal.model_dump_json()
    assert token not in serialised
    assert "Bearer" not in serialised
    assert not any(
        "token" in f and "expires" not in f and "issued" not in f for f in Principal.model_fields
    ), "no raw-token field exists at all"


def test_assert_owns_refuses_another_users_aggregate(
    api_config, deps, signing_key: RsaKeyPair
) -> None:
    import asyncio

    from provenance_contracts.identity import AuthorizationError
    from services.control_plane.app.auth.principal import build_human_principal

    principal = asyncio.run(
        build_human_principal(
            human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub),
            config=api_config,
            jwks=deps.jwks,
            users=deps.users,
            trace_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            now=deps.clock(),
        )
    )
    with pytest.raises(AuthorizationError):
        principal.assert_owns(tenant_id=fakes_mod.ROB.tenant_id, user_id=fakes_mod.ROB.user_id)
