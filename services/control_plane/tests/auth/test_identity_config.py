"""``ApiConfig.from_settings`` must refuse a configuration it cannot serve.

Authority: ``specs/15_API_SPEC.md`` sections 2.1-2.4;
``CANONICAL_DECISIONS.md`` -> "Gemini model id canon (frozen 2026-08-24)".

The defect this file was opened for
------------------------------------
``PV_PLATFORM`` widened sixteen AWS-specific ``Settings`` fields from ``str``
to ``str | None``, so that a Google or local deployment is not required to
invent Cognito values it will never use. ``ApiConfig.cognito_issuer`` is
declared ``str`` and is assigned ``settings.cognito_issuer`` in
:meth:`ApiConfig.from_settings`.

``mypy --strict`` cannot see it. ``from_settings`` takes ``settings: Any`` on
purpose -- importing the module must never import ``Settings``, because
``Settings`` reads the environment at import time and the hermetic suites must
build an app with no environment at all. ``Any`` is the right call for that
reason and the wrong call for this one: every attribute read off it is
unchecked, and a frozen dataclass does not validate at runtime either.

So on ``PV_PLATFORM=gcp`` the object constructs happily with
``cognito_issuer=None`` and fails later, inside a request, during token
verification. That is exactly the trade the platform work existed to prevent:
a startup failure exchanged for a runtime one.

Two of these tests are the reproduction. They failed before the fix.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from _support.rsa import RsaKeyPair

from services.control_plane.app.api.config import ApiConfig
from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth.jwt import StaticJwksProvider, decode_and_verify

pytestmark = pytest.mark.unit

_SENTINEL = "not-a-real-key-0f3a9c"


class _Secret:
    """Stands in for ``pydantic.SecretStr``, which is all ``from_settings``
    asks of these two fields."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _settings(**overrides: Any) -> Any:
    """A stand-in for ``provenance_contracts.settings.Settings``.

    Shaped, deliberately, exactly like the real object *after* the
    ``PV_PLATFORM`` widening: every AWS field is ``None`` on ``gcp``, because
    the real validator no longer requires them there. A ``SimpleNamespace``
    rather than the real ``Settings`` because constructing that reads the
    developer's environment, and a test whose outcome depends on whether the
    machine exports ``AWS_REGION`` proves nothing.
    """
    base: dict[str, Any] = {
        "pv_platform": "gcp",
        "aws_region": None,
        "cognito_issuer": None,
        "cognito_jwks_url": None,
        "cognito_web_client_id": None,
        "cognito_agent_client_id": None,
        "cognito_worker_client_id": None,
        "cognito_judge_group": None,
        "ses_ingest_domain": None,
        "s3_artifact_bucket": None,
        "google_cloud_project": "provenance-demo",
        "google_cloud_region": "us-east4",
        "gcs_artifact_bucket": "provenance-artifacts",
        "cursor_hmac_key": _Secret(_SENTINEL),
        "provenance_capability_hmac_key": _Secret(_SENTINEL),
        "build_sha": "9c1f2ad",
        "schema_revision": "0008_events_infrastructure",
        "pv_agent_mode": "LIVE",
        "otel_exporter_otlp_endpoint": None,
        "max_artifact_bytes": 20_971_520,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# The reproduction
# --------------------------------------------------------------------------


class TestTheConfigurationCannotBeBuiltHalfPresent:
    def test_a_google_platform_never_yields_a_none_cognito_issuer(self) -> None:
        """The headline defect.

        Before the fix this constructed, and ``config.cognito_issuer`` was
        ``None`` against a field declared ``str``. Now the ``gcp`` branch
        builds a Google issuer, and there is no path that reaches a ``None``.
        """
        config = ApiConfig.from_settings(_settings())
        assert isinstance(config.issuer, str)
        assert config.issuer

    def test_an_aws_platform_missing_its_issuer_refuses_at_startup(self) -> None:
        """The same defect from the other side.

        ``PV_PLATFORM=aws`` with no ``COGNITO_ISSUER`` cannot be served. It
        must fail here, on the way up, and not on the first request that
        happens to carry a token.
        """
        with pytest.raises(ValueError, match="COGNITO_ISSUER"):
            ApiConfig.from_settings(_settings(pv_platform="aws"))

    def test_the_dataclass_itself_refuses_an_empty_issuer(self) -> None:
        """A dataclass does not validate, so this one is made to.

        ``from_settings`` is not the only constructor -- the hermetic suites
        build ``ApiConfig`` directly -- so the invariant belongs on the type,
        not on one factory.
        """
        with pytest.raises(ValueError, match="issuer"):
            ApiConfig(
                cognito_issuer="",
                client_id_names={},
                cursor_hmac_key=b"k",
                capability_hmac_key=b"k",
                ingest_domain="in.provenance.invalid",
            )

    def test_the_three_app_clients_do_not_collapse_into_one_none_key(self) -> None:
        """The quieter half of the same bug.

        ``{None: "provenance-web", None: "provenance-agent-runtime",
        None: "provenance-workers"}`` is a **one**-entry dict. Every logical
        client silently disappeared, and ``logical_client`` answered ``None``
        for all three -- which reads as "unknown app client" and refuses
        every request, on a deployment that thought it was configured.
        """
        config = ApiConfig.from_settings(_settings())
        assert len(config.client_id_names) == 3
        assert None not in config.client_id_names
        assert set(config.client_id_names.values()) == {
            "provenance-web",
            "provenance-agent-runtime",
            "provenance-workers",
        }


class TestEachPlatformBuildsACompleteConfiguration:
    """The other half of "do not do it half way": every branch produces a
    configuration that can actually serve a request, or refuses."""

    def test_gcp_builds_the_google_issuer_from_the_project(self) -> None:
        config = ApiConfig.from_settings(_settings())
        assert config.platform == "gcp"
        assert config.issuer == "https://securetoken.google.com/provenance-demo"
        assert config.identity_provider == "google"
        assert config.logical_client("provenance-demo") == "provenance-web"

    def test_aws_is_unchanged_when_it_is_configured(self) -> None:
        """The pivot must not have moved the AWS path. Same issuer, same three
        clients, same provider."""
        config = ApiConfig.from_settings(
            _settings(
                pv_platform="aws",
                aws_region="us-east-1",
                cognito_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pvTEST",
                cognito_web_client_id="1web000000000000000000000w",
                cognito_agent_client_id="2agent00000000000000000000",
                cognito_worker_client_id="3worker0000000000000000000",
                ses_ingest_domain="in.provenance.app",
            )
        )
        assert config.identity_provider == "cognito"
        assert config.region == "us-east-1"
        assert config.ingest_domain == "in.provenance.app"
        assert config.logical_client("2agent00000000000000000000") == "provenance-agent-runtime"

    def test_local_reads_its_signing_key_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PV_LOCAL_AUTH_SECRET", "a-development-secret")
        config = ApiConfig.from_settings(_settings(pv_platform="local"))
        assert config.identity_provider == "local"
        assert config.issuer == "https://local.provenance.invalid/"

    def test_local_without_a_signing_key_refuses_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No default. A development issuer with a guessable key is a
        production incident waiting for a misconfigured deploy."""
        monkeypatch.delenv("PV_LOCAL_AUTH_SECRET", raising=False)
        with pytest.raises(ValueError, match="PV_LOCAL_AUTH_SECRET"):
            ApiConfig.from_settings(_settings(pv_platform="local"))

    def test_no_platform_ever_leaves_the_issuer_and_provider_disagreeing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PV_LOCAL_AUTH_SECRET", "a-development-secret")
        for overrides in (
            {},
            {"pv_platform": "local"},
            {
                "pv_platform": "aws",
                "aws_region": "us-east-1",
                "cognito_issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pvTEST",
                "cognito_web_client_id": "w",
                "cognito_agent_client_id": "a",
                "cognito_worker_client_id": "k",
            },
        ):
            config = ApiConfig.from_settings(_settings(**overrides))
            assert config.provider.issuer == config.issuer
            assert config.identity_provider == config.provider.name

    def test_the_local_signing_key_is_never_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``D-00-019``. ``ApiConfig`` is printed in pytest failure headers --
        it was printed in this file's own first red run."""
        monkeypatch.setenv("PV_LOCAL_AUTH_SECRET", "a-development-secret")
        config = ApiConfig.from_settings(_settings(pv_platform="local"))
        assert "a-development-secret" not in repr(config)
        assert _SENTINEL not in repr(config)


class TestTheDownstreamConsequence:
    """What the ``None`` actually reached, had it been allowed through."""

    async def test_a_token_with_no_issuer_claim_is_refused_against_a_none_issuer(
        self, signing_key: RsaKeyPair
    ) -> None:
        """``payload.get("iss") != issuer`` is ``None != None`` -> ``False``.

        A misconfigured issuer did not merely break verification; it *passed*
        the issuer check for any token that omitted ``iss`` entirely. The
        verifier now refuses a configured issuer that is not a non-empty
        string, so the comparison can never be vacuous.
        """
        claims = {
            "client_id": "1web000000000000000000000w",
            "token_use": "access",
            "scope": "provenance.memory/read",
            "sub": "sub-alex-0001",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = signing_key.sign_jws(claims)
        with pytest.raises(ApiError) as caught:
            await decode_and_verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                issuer=None,  # type: ignore[arg-type]
                now=time.time(),
            )
        assert caught.value.code is ErrorCode.INTERNAL_ERROR

    async def test_a_token_omitting_iss_is_refused_against_a_real_issuer(
        self, signing_key: RsaKeyPair
    ) -> None:
        """And an absent ``iss`` is still an issuer failure, not a silent pass."""
        claims = {
            "client_id": "1web000000000000000000000w",
            "token_use": "access",
            "sub": "sub-alex-0001",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = signing_key.sign_jws(claims)
        with pytest.raises(ApiError) as caught:
            await decode_and_verify(
                token,
                jwks=StaticJwksProvider(signing_key.jwks()),
                issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pvTEST",
                now=time.time(),
            )
        assert caught.value.code is ErrorCode.TOKEN_WRONG_ISSUER
