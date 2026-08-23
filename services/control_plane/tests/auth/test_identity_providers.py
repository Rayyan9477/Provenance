"""The three identity providers, and the properties none of them may relax.

Authority: ``specs/15_API_SPEC.md`` sections 2.1-2.5;
``CANONICAL_DECISIONS.md`` -> "Gemini model id canon (frozen 2026-08-24)".

The Google claim vocabulary asserted here was read on 2026-08-24 from
https://firebase.google.com/docs/auth/admin/verify-id-tokens and cross-checked
against ``firebase-admin-node``'s ``src/auth/token-verifier.ts``. It is not
recalled from memory, because the last time this pack froze provider
identifiers from memory every one of them was wrong (``D-00-002``).

The file is organised by *property* rather than by provider. Section 2's
guarantees are the strongest part of the submission and the interesting
question is not "does Google work" but "does Google still refuse everything
Cognito refused, for the same reasons".
"""

from __future__ import annotations

import pathlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from _support import fakes as fakes_mod
from _support.rsa import RsaKeyPair
from _support.tokens import AGENT_CLIENT_ID, ISSUER, WEB_CLIENT_ID, WORKER_CLIENT_ID

from provenance_domain.enums import OAuthScope
from services.control_plane.app.api.config import (
    DEFAULT_SCOPE_GRANTS,
    LOCAL_CLIENT_ID_NAMES,
    ApiConfig,
)
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth import route_class as route_class_mod
from services.control_plane.app.auth.identity import (
    GOOGLE_JWKS_URL,
    GOOGLE_SECURETOKEN_ISSUER_PREFIX,
    LOCAL_ISSUER,
    CognitoIdentityProvider,
    GoogleIdentityProvider,
    LocalIdentityProvider,
    identity_provider_for,
    issue_local_token,
)
from services.control_plane.app.auth.jwt import StaticJwksProvider
from services.control_plane.app.auth.principal import build_human_principal, known_scopes
from services.control_plane.app.auth.route_class import RouteClass

pytestmark = pytest.mark.unit

PROJECT = "provenance-demo"
GOOGLE_ISSUER = f"{GOOGLE_SECURETOKEN_ISSUER_PREFIX}{PROJECT}"
LOCAL_SECRET = b"local-dev-secret-not-a-production-key"

CURSOR_KEY = b"cursor-key-for-tests-only-not-a-secret"
CAPABILITY_KEY = b"capability-key-for-tests-only-not-a-secret"


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def google_claims(**overrides: Any) -> dict[str, Any]:
    """A Google Identity Platform ID token payload.

    ``iss`` is ``https://securetoken.google.com/<projectId>`` and ``aud`` is
    that same ``<projectId>``; ``sub`` is the Firebase uid. There is no
    ``token_use`` claim -- GIP does not mint one -- which is exactly why the
    provider replaces that check with the audience binding rather than
    dropping it.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": GOOGLE_ISSUER,
        "aud": PROJECT,
        "sub": fakes_mod.ALEX.cognito_sub,
        "auth_time": now - 120,
        "iat": now,
        "exp": now + 3600,
        "email": "alex@provenance.invalid",
        "email_verified": True,
        "firebase": {"sign_in_provider": "password", "identities": {}},
        "scope": "provenance.memory/read",
    }
    claims.update(overrides)
    return claims


def local_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": LOCAL_ISSUER,
        "token_use": "access",
        "client_id": "provenance-web",
        "scope": "provenance.memory/read",
        "sub": fakes_mod.ALEX.cognito_sub,
        "iat": now,
        "nbf": now - 60,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def google_config(**overrides: Any) -> ApiConfig:
    base: dict[str, Any] = {
        "platform": "gcp",
        "cognito_issuer": GOOGLE_ISSUER,
        "google_project_id": PROJECT,
        "client_id_names": {
            PROJECT: "provenance-web",
            "provenance-agent-runtime": "provenance-agent-runtime",
            "provenance-workers": "provenance-workers",
        },
        "default_scopes_by_client": DEFAULT_SCOPE_GRANTS,
        "cursor_hmac_key": CURSOR_KEY,
        "capability_hmac_key": CAPABILITY_KEY,
        "ingest_domain": "in.provenance.invalid",
    }
    base.update(overrides)
    return ApiConfig(**base)


def local_config(**overrides: Any) -> ApiConfig:
    base: dict[str, Any] = {
        "platform": "local",
        "cognito_issuer": LOCAL_ISSUER,
        "local_signing_key": LOCAL_SECRET,
        "client_id_names": dict(LOCAL_CLIENT_ID_NAMES),
        "default_scopes_by_client": DEFAULT_SCOPE_GRANTS,
        "cursor_hmac_key": CURSOR_KEY,
        "capability_hmac_key": CAPABILITY_KEY,
        "ingest_domain": "in.provenance.invalid",
    }
    base.update(overrides)
    return ApiConfig(**base)


def cognito_config(**overrides: Any) -> ApiConfig:
    base: dict[str, Any] = {
        "platform": "aws",
        "cognito_issuer": ISSUER,
        "client_id_names": {
            WEB_CLIENT_ID: "provenance-web",
            AGENT_CLIENT_ID: "provenance-agent-runtime",
            WORKER_CLIENT_ID: "provenance-workers",
        },
        "cursor_hmac_key": CURSOR_KEY,
        "capability_hmac_key": CAPABILITY_KEY,
        "ingest_domain": "in.provenance.invalid",
    }
    base.update(overrides)
    return ApiConfig(**base)


def google_provider() -> GoogleIdentityProvider:
    """Built through the config rather than by hand, so the scope grant is
    re-keyed onto the opaque client ids a provider actually sees -- which is
    the wiring under test, not a detail to bypass."""
    provider = identity_provider_for(google_config())
    assert isinstance(provider, GoogleIdentityProvider)
    return provider


def local_provider() -> LocalIdentityProvider:
    provider = identity_provider_for(local_config())
    assert isinstance(provider, LocalIdentityProvider)
    return provider


# --------------------------------------------------------------------------
# Selection: which provider a platform gets, and which it can never get
# --------------------------------------------------------------------------


class TestTheProviderIsChosenByThePlatformAlone:
    def test_aws_selects_cognito(self) -> None:
        provider = identity_provider_for(cognito_config())
        assert isinstance(provider, CognitoIdentityProvider)
        assert provider.name == "cognito"

    def test_gcp_selects_google(self) -> None:
        provider = identity_provider_for(google_config())
        assert isinstance(provider, GoogleIdentityProvider)
        assert provider.name == "google"

    def test_local_selects_the_local_issuer(self) -> None:
        provider = identity_provider_for(local_config())
        assert isinstance(provider, LocalIdentityProvider)
        assert provider.name == "local"

    @pytest.mark.parametrize("platform", ["aws", "gcp"])
    def test_the_local_provider_cannot_be_reached_from_another_platform(
        self, platform: str
    ) -> None:
        """No flag turns it on. The only lever is ``PV_PLATFORM``.

        A signing key on a non-local platform is not a disclosable state --
        it is a refusal. Disclosure is for legitimate configurations, and a
        development issuer inside a cloud deployment is not one.
        """
        builder = cognito_config if platform == "aws" else google_config
        config = builder(local_signing_key=LOCAL_SECRET)
        assert config.provider.name != "local"
        assert config.identity_provider != "local"

    def test_a_local_platform_without_a_signing_key_refuses_to_start(self) -> None:
        with pytest.raises(ValueError, match="PV_LOCAL_AUTH_SECRET"):
            local_config(local_signing_key=None)

    def test_a_google_platform_without_a_project_refuses_to_start(self) -> None:
        with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
            google_config(google_project_id=None)

    @pytest.mark.parametrize("builder", [cognito_config, google_config, local_config])
    def test_the_disclosed_name_is_read_off_the_verifier_in_force(self, builder: Any) -> None:
        """``GET /v1/version`` must not be able to say one thing while another
        object verifies the tokens."""
        config = builder()
        assert config.identity_provider == config.provider.name

    def test_an_issuer_that_disagrees_with_the_provider_refuses_to_start(self) -> None:
        with pytest.raises(ValueError, match="issuer"):
            google_config(cognito_issuer="https://securetoken.google.com/some-other-project")


# --------------------------------------------------------------------------
# Google: the claim vocabulary, verified against current documentation
# --------------------------------------------------------------------------


class TestGoogleIdentityPlatform:
    def test_the_issuer_and_audience_come_from_one_configured_value(self) -> None:
        """Firebase: ``iss`` is ``https://securetoken.google.com/<projectId>``
        and ``aud`` "must be your Firebase project ID", matching that same
        ``<projectId>``. Storing them separately would let them disagree."""
        provider = google_provider()
        assert provider.issuer == f"https://securetoken.google.com/{PROJECT}"
        assert provider.audience == PROJECT
        assert provider.issuer.rsplit("/", 1)[-1] == provider.audience

    def test_the_jwks_url_is_the_securetoken_jwk_endpoint(self) -> None:
        """The documentation prints the x509 PEM URL; the OIDC discovery
        document's ``jwks_uri`` is the JWK one, and this verifier consumes
        ``n``/``e`` rather than certificates."""
        assert google_provider().jwks_url == GOOGLE_JWKS_URL
        assert GOOGLE_JWKS_URL.startswith("https://")
        assert "securetoken@system.gserviceaccount.com" in GOOGLE_JWKS_URL

    async def test_a_well_formed_token_verifies(self, signing_key: RsaKeyPair) -> None:
        token = signing_key.sign_jws(google_claims())
        claims = await google_provider().verify(
            token,
            jwks=StaticJwksProvider(signing_key.jwks()),
            now=time.time(),
            leeway_seconds=60,
        )
        assert claims.sub == fakes_mod.ALEX.cognito_sub
        assert claims.issuer == GOOGLE_ISSUER
        assert claims.scopes == frozenset({"provenance.memory/read"})

    async def test_a_token_for_another_google_project_is_refused(
        self, signing_key: RsaKeyPair
    ) -> None:
        """The replacement for Cognito's ``token_use`` check, and the reason it
        is a replacement rather than a deletion: what step 5 buys is a refusal
        to accept a token some other relying party was the audience of."""
        token = signing_key.sign_jws(google_claims(aud="someone-elses-project"))
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.code is ErrorCode.TOKEN_INVALID_SIGNATURE
        assert caught.value.details["reason"] == "WRONG_AUDIENCE"

    async def test_a_token_from_another_issuer_is_refused(self, signing_key: RsaKeyPair) -> None:
        token = signing_key.sign_jws(google_claims(iss=ISSUER))
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.code is ErrorCode.TOKEN_WRONG_ISSUER

    async def test_a_token_with_no_subject_is_refused(self, signing_key: RsaKeyPair) -> None:
        """Firebase: ``sub`` "must be a non-empty string ... the uid of the
        user". Without one there is nothing to resolve against ``users``."""
        claims = google_claims()
        del claims["sub"]
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                signing_key.sign_jws(claims),
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.details["reason"] == "MISSING_SUBJECT"

    async def test_a_tampered_signature_is_refused(self, signing_key: RsaKeyPair) -> None:
        token = signing_key.tamper(signing_key.sign_jws(google_claims()))
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.code is ErrorCode.TOKEN_INVALID_SIGNATURE

    async def test_an_unpublished_kid_is_refused(
        self, signing_key: RsaKeyPair, other_key: RsaKeyPair
    ) -> None:
        token = other_key.sign_jws(google_claims())
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.details["reason"] == "UNKNOWN_KID"

    async def test_an_expired_token_is_refused(self, signing_key: RsaKeyPair) -> None:
        past = int(time.time()) - 7200
        token = signing_key.sign_jws(google_claims(iat=past - 3600, exp=past))
        with pytest.raises(ApiError) as caught:
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.code is ErrorCode.TOKEN_EXPIRED

    async def test_an_auth_time_in_the_future_is_refused(self, signing_key: RsaKeyPair) -> None:
        token = signing_key.sign_jws(google_claims(auth_time=int(time.time()) + 9000))
        with pytest.raises(ApiError):
            await google_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )

    async def test_an_ordinary_user_without_a_custom_claim_is_the_web_client(
        self, signing_key: RsaKeyPair
    ) -> None:
        """No provisioning ritual for a signed-in human: absent the
        ``provenance_client`` claim, ``aud`` -- the project id -- is the
        client, and the config maps it to ``provenance-web``."""
        token = signing_key.sign_jws(google_claims())
        claims = await google_provider().verify(
            token,
            jwks=StaticJwksProvider(signing_key.jwks()),
            now=time.time(),
            leeway_seconds=60,
        )
        assert claims.client_id == PROJECT
        assert google_config().logical_client(claims.client_id) == "provenance-web"

    async def test_a_workload_custom_claim_names_the_workload_client(
        self, signing_key: RsaKeyPair
    ) -> None:
        token = signing_key.sign_jws(google_claims(provenance_client="provenance-agent-runtime"))
        claims = await google_provider().verify(
            token,
            jwks=StaticJwksProvider(signing_key.jwks()),
            now=time.time(),
            leeway_seconds=60,
        )
        assert google_config().logical_client(claims.client_id) == "provenance-agent-runtime"

    async def test_the_configured_grant_applies_only_when_the_token_carries_none(
        self, signing_key: RsaKeyPair
    ) -> None:
        """GIP has no resource server, so section 2.1's allocation table is
        applied server-side. It is a floor supplied in the absence of a claim,
        never an override of one."""
        claims_no_scope = google_claims()
        del claims_no_scope["scope"]
        granted = await google_provider().verify(
            signing_key.sign_jws(claims_no_scope),
            jwks=StaticJwksProvider(signing_key.jwks()),
            now=time.time(),
            leeway_seconds=60,
        )
        assert granted.scopes == DEFAULT_SCOPE_GRANTS["provenance-web"]

    async def test_the_agent_grant_still_excludes_action_execute(self) -> None:
        """Section 2.1's fourth invariant, expressed in configuration rather
        than in IAM but expressed all the same: the graph that drafts an
        outbound letter cannot send it."""
        agent = DEFAULT_SCOPE_GRANTS["provenance-agent-runtime"]
        assert "provenance.action/execute" not in agent
        assert "provenance.ingest/write" not in agent


# --------------------------------------------------------------------------
# Local
# --------------------------------------------------------------------------


class TestTheLocalDevelopmentIssuer:
    async def test_a_minted_token_verifies(self) -> None:
        token = issue_local_token(local_claims(), key=LOCAL_SECRET)
        claims = await local_provider().verify(
            token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
        )
        assert claims.sub == fakes_mod.ALEX.cognito_sub
        assert claims.issuer == LOCAL_ISSUER

    async def test_a_token_signed_with_another_key_is_refused(self) -> None:
        """This is the property that makes it an issuer rather than a bypass.

        There is no unsigned mode and no header that names a user; a forged
        token fails here exactly as it fails on the other two platforms.
        """
        token = issue_local_token(local_claims(), key=b"a-different-secret-entirely")
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.code is ErrorCode.TOKEN_INVALID_SIGNATURE
        assert caught.value.details["reason"] == "SIGNATURE_MISMATCH"

    async def test_an_expired_token_is_refused(self) -> None:
        past = int(time.time()) - 7200
        token = issue_local_token(
            local_claims(iat=past - 3600, nbf=past - 3600, exp=past), key=LOCAL_SECRET
        )
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.code is ErrorCode.TOKEN_EXPIRED

    async def test_a_token_from_another_issuer_is_refused(self) -> None:
        token = issue_local_token(local_claims(iss=ISSUER), key=LOCAL_SECRET)
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.code is ErrorCode.TOKEN_WRONG_ISSUER

    async def test_an_id_token_is_still_not_accepted(self) -> None:
        token = issue_local_token(local_claims(token_use="id"), key=LOCAL_SECRET)
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.details["reason"] == "ID_TOKEN_NOT_ACCEPTED"

    async def test_a_token_naming_no_client_is_refused(self) -> None:
        claims = local_claims()
        del claims["client_id"]
        token = issue_local_token(claims, key=LOCAL_SECRET)
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.details["reason"] == "MISSING_CLIENT_ID"

    async def test_a_provider_with_no_key_verifies_nothing(self) -> None:
        """Belt and braces for the case ``__post_init__`` already refuses: an
        empty key must never make ``compare_digest`` the only thing standing
        between a forged token and a session."""
        token = issue_local_token(local_claims(), key=b"")
        with pytest.raises(ApiError) as caught:
            await LocalIdentityProvider(signing_key=b"").verify(
                token, jwks=StaticJwksProvider({}), now=time.time(), leeway_seconds=60
            )
        assert caught.value.code is ErrorCode.INTERNAL_ERROR


class TestAlgorithmConfusionIsClosedInBothDirections:
    """The one risk an HMAC-signed local issuer introduces, closed structurally.

    Neither verifier can be reached on the other's platform, but that is a
    configuration argument. These two assert the code itself refuses.
    """

    async def test_the_rs256_verifiers_refuse_an_hs256_token(self, signing_key: RsaKeyPair) -> None:
        token = issue_local_token(local_claims(), key=LOCAL_SECRET)
        for provider in (CognitoIdentityProvider(issuer=ISSUER), google_provider()):
            with pytest.raises(ApiError) as caught:
                await provider.verify(
                    token,
                    jwks=StaticJwksProvider(signing_key.jwks()),
                    now=time.time(),
                    leeway_seconds=60,
                )
            assert caught.value.details["reason"] == "UNEXPECTED_ALGORITHM"

    async def test_the_local_verifier_refuses_an_rs256_token(self, signing_key: RsaKeyPair) -> None:
        token = signing_key.sign_jws(local_claims())
        with pytest.raises(ApiError) as caught:
            await local_provider().verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                now=time.time(),
                leeway_seconds=60,
            )
        assert caught.value.details["reason"] == "UNEXPECTED_ALGORITHM"

    async def test_the_local_verifier_refuses_the_none_algorithm(self) -> None:
        import base64
        import json

        def seg(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        header = seg(json.dumps({"alg": "none"}).encode())
        payload = seg(json.dumps(local_claims()).encode())
        with pytest.raises(ApiError):
            await local_provider().verify(
                f"{header}.{payload}.",
                jwks=StaticJwksProvider({}),
                now=time.time(),
                leeway_seconds=60,
            )


# --------------------------------------------------------------------------
# The section 2 properties, asserted against the NEW providers
# --------------------------------------------------------------------------


class TestEverySectionTwoPropertySurvivesTheAbstraction:
    async def test_the_principal_is_still_a_database_row_under_google(
        self, signing_key: RsaKeyPair, fixture: fakes_mod.Fixture
    ) -> None:
        """Section 2.5. A GIP token carrying a forged tenant claim changes
        nothing, because nothing downstream reads one."""
        token = signing_key.sign_jws(
            google_claims(
                tenant_id=str(uuid.uuid4()),
                user_id=str(fakes_mod.ROB.user_id),
                **{"custom:tenant_id": str(uuid.uuid4())},
            )
        )
        principal = await build_human_principal(
            token,
            config=google_config(),
            jwks=StaticJwksProvider(signing_key.jwks()),
            users=fixture.users,
            trace_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            now=datetime.now(UTC),
        )
        assert principal.tenant_id == fakes_mod.ALEX.tenant_id
        assert principal.user_id == fakes_mod.ALEX.user_id

    async def test_an_unprovisioned_google_subject_is_refused(
        self, signing_key: RsaKeyPair, fixture: fakes_mod.Fixture
    ) -> None:
        """Section 2.5: Provenance does not auto-create a user on first call,
        on any platform."""
        token = signing_key.sign_jws(google_claims(sub="uid-nobody-has-ever-seen"))
        with pytest.raises(ApiError) as caught:
            await build_human_principal(
                token,
                config=google_config(),
                jwks=StaticJwksProvider(signing_key.jwks()),
                users=fixture.users,
                trace_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                now=datetime.now(UTC),
            )
        assert caught.value.code is ErrorCode.USER_NOT_PROVISIONED

    async def test_an_unrecognised_scope_is_dropped_rather_than_rejected(
        self, signing_key: RsaKeyPair
    ) -> None:
        """Section 1.3, on the Google path. A provider this build does not
        know every scope of must not become a build that refuses every
        token."""
        token = signing_key.sign_jws(
            google_claims(scope="provenance.memory/read provenance.future/unknown")
        )
        claims = await google_provider().verify(
            token,
            jwks=StaticJwksProvider(signing_key.jwks()),
            now=time.time(),
            leeway_seconds=60,
        )
        assert known_scopes(claims.scopes) == frozenset({OAuthScope.MEMORY_READ})

    @pytest.mark.parametrize("builder", [cognito_config, google_config, local_config])
    def test_a_fourth_client_reaches_no_route_class(self, builder: Any) -> None:
        """Section 2.4, on all three platforms. An identity nobody configured
        maps to no logical name, and no logical name is in either set."""
        config = builder()
        assert config.logical_client("an-identity-created-by-hand") is None
        for route_class in (RouteClass.PUBLIC, RouteClass.INTERNAL):
            with pytest.raises(ApiError):
                route_class_mod.route_class_check(route_class, "unknown-app-client")

    @pytest.mark.parametrize("builder", [cognito_config, google_config, local_config])
    def test_the_two_surfaces_still_cannot_leak_into_each_other(self, builder: Any) -> None:
        config = builder()
        web = next(k for k, v in config.client_id_names.items() if v == "provenance-web")
        agent = next(
            k for k, v in config.client_id_names.items() if v == "provenance-agent-runtime"
        )
        with pytest.raises(ApiError) as internal:
            route_class_mod.route_class_check(RouteClass.INTERNAL, config.logical_client(web) or "")
        assert internal.value.code is ErrorCode.HUMAN_TOKEN_ON_INTERNAL_ROUTE
        with pytest.raises(ApiError) as public:
            route_class_mod.route_class_check(RouteClass.PUBLIC, config.logical_client(agent) or "")
        assert public.value.code is ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE

    async def test_the_kid_amplification_mitigation_is_the_shared_one(self) -> None:
        """``jwt.py``'s cooldown is not reimplemented per provider, so it
        cannot be forgotten by one of them. Every provider that reads a JWKS
        reads it through the same cache; the local one publishes no keys at
        all and so cannot be used to force a fetch."""
        from services.control_plane.app.auth import identity as identity_mod

        source = identity_mod.__file__ or ""
        assert source
        body = pathlib.Path(source).read_text(encoding="utf-8")
        assert "get_key" not in body, "a provider grew its own JWKS lookup"
        assert google_provider().jwks_url == GOOGLE_JWKS_URL
        assert local_provider().jwks_url == ""

    @pytest.mark.parametrize("builder", [google_config, local_config])
    def test_no_credential_is_rendered_by_the_config(self, builder: Any) -> None:
        """``D-00-019``: a live credential once reached a pytest failure
        header. ``ApiConfig`` is printed in exactly that position."""
        rendered = repr(builder())
        assert LOCAL_SECRET.decode() not in rendered
        assert "cursor-key-for-tests-only" not in rendered


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------


class TestTheActiveProviderIsAnnounced:
    def _client(self, config: ApiConfig, deps: Any) -> Any:
        from fastapi.testclient import TestClient

        from services.control_plane.app.api.app import create_app

        return TestClient(create_app(config=config, deps=deps), raise_server_exceptions=False)

    def test_version_names_the_provider_in_force(self, deps: Any) -> None:
        """Section 8.2 is the single authoritative operating-mode channel, and
        it is unauthenticated so a judge can ``curl`` it with nothing but the
        URL. An authentication path that does not announce itself is the same
        class of quiet dishonesty as an undisclosed fixture-mode demo."""
        with self._client(local_config(), deps) as client:
            body = client.get("/v1/version").json()
        assert body["identity_provider"] == "local"

    @pytest.mark.parametrize(
        ("builder", "expected"),
        [(cognito_config, "cognito"), (google_config, "google"), (local_config, "local")],
    )
    def test_every_platform_discloses_its_own_provider(
        self, builder: Any, expected: str, deps: Any
    ) -> None:
        with self._client(builder(), deps) as client:
            body = client.get("/v1/version").json()
        assert body["identity_provider"] == expected

    def test_version_stays_unauthenticated(self, deps: Any) -> None:
        with self._client(local_config(), deps) as client:
            assert client.get("/v1/version").status_code == 200

    def test_healthz_does_not_carry_the_provider(self, deps: Any) -> None:
        """Section 8.1 stays a bare liveness probe. It does not carry
        ``fixture_mode`` and it does not grow this either -- a load balancer
        polling it would put the operating mode into every access log."""
        with self._client(local_config(), deps) as client:
            body = client.get("/v1/healthz").json()
        assert body == {"status": "ok"}
