"""The deployment platform, and what each one actually requires.

The problem this solves
------------------------
Before the pivot, ``Settings`` had **27 required environment variables and 15 of
them were AWS-specific** -- eight Cognito, two SES, two EventBridge, one SQS,
one S3, plus ``AWS_REGION``. None of those exist in a Google deployment, and
none can be given a plausible value by someone who is not us. A judge following
the README could therefore not construct settings at all, which makes the
hackathon's mandatory "spin-up instructions" unsatisfiable no matter how well
the README is written.

Why this is not a weakening of the fail-fast contract
------------------------------------------------------
The temptation is to give every AWS field a default and move on. That trades a
startup failure for a runtime one: ``S3_ARTIFACT_BUCKET=""`` constructs
happily and fails at the first upload, in a request, in production.

Instead the requirement becomes *conditional on the declared platform*. A
missing variable for the **active** platform is still a startup failure, with
the same message it had before. What changes is that AWS variables stop being
required by a deployment that does not use AWS. The set of ways to start a
broken system does not grow; it shrinks, because ``local`` can no longer be
started with half an AWS configuration.

``T0.4``'s acceptance -- "raises on missing" -- is preserved exactly. The three
gate-named tests in ``test_settings.py`` keep their names and their meaning.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from provenance_contracts.settings import Settings

pytestmark = pytest.mark.unit


SENTINEL = "n0t-a-real-password-3f8c1d"

#: Everything every platform needs, and nothing platform-specific.
_CORE: dict[str, str] = {
    "APP_ENV": "local",
    "APP_BASE_URL": "https://api.provenance.invalid",
    "WEB_BASE_URL": "https://app.provenance.invalid",
    "OTEL_SERVICE_NAME": "provenance-control-plane",
    "COCKROACH_DATABASE_URL": f"postgresql://u:{SENTINEL}@h.invalid:26257/provenance",
    "PROVENANCE_CAPABILITY_HMAC_KEY": "a" * 44,
    "PROVENANCE_CAPABILITY_HMAC_KID": "k1",
    "CURSOR_HMAC_KEY": "b" * 44,
    "INGEST_ALIAS_HMAC_KEY": "c" * 44,
    "UPLOAD_URL_TTL_SECONDS": "900",
    "DOWNLOAD_URL_TTL_SECONDS": "900",
}

_GCP: dict[str, str] = {
    "PV_PLATFORM": "gcp",
    "GOOGLE_API_KEY": "d" * 39,
    "GCS_ARTIFACT_BUCKET": "provenance-artifacts",
}


def _env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    """An environment containing exactly *values* and nothing else.

    A developer machine that exports ``AWS_REGION`` would otherwise decide the
    outcome of these tests, which is the reason ``test_settings.py`` scrubs too.
    """
    for name in list(Settings.model_fields):
        alias = Settings.model_fields[name].validation_alias
        if isinstance(alias, str):
            monkeypatch.delenv(alias, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


class TestAGoogleDeploymentNeedsNoAwsVariables:
    """The whole point. This is the configuration a judge will actually use."""

    def test_settings_construct_with_core_plus_google_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _env(monkeypatch, {**_CORE, **_GCP})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.pv_platform == "gcp"

    def test_no_cognito_variable_is_required_on_gcp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, {**_CORE, **_GCP})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cognito_user_pool_id is None

    def test_no_ses_or_eventbridge_or_sqs_variable_is_required_on_gcp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _env(monkeypatch, {**_CORE, **_GCP})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.ses_ingest_domain is None
        assert settings.eventbridge_bus_name is None
        assert settings.sqs_dlq_url is None


class TestFailFastIsPreservedNotTraded:
    """A missing variable for the ACTIVE platform is still a startup failure.

    If these pass while the tests above also pass, the requirement genuinely
    moved rather than being deleted.
    """

    def test_gcp_without_a_google_api_key_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = {**_CORE, **_GCP}
        del env["GOOGLE_API_KEY"]
        _env(monkeypatch, env)
        with pytest.raises(ValidationError, match="GOOGLE_API_KEY"):
            Settings()  # type: ignore[call-arg]

    def test_gcp_without_an_artifact_bucket_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = {**_CORE, **_GCP}
        del env["GCS_ARTIFACT_BUCKET"]
        _env(monkeypatch, env)
        with pytest.raises(ValidationError, match="GCS_ARTIFACT_BUCKET"):
            Settings()  # type: ignore[call-arg]

    def test_aws_without_cognito_refuses_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The old contract, unchanged, for anyone still deploying on AWS."""
        _env(monkeypatch, {**_CORE, "PV_PLATFORM": "aws", "AWS_REGION": "us-east-1"})
        with pytest.raises(ValidationError, match="COGNITO_"):
            Settings()  # type: ignore[call-arg]

    def test_aws_without_a_region_refuses_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, {**_CORE, "PV_PLATFORM": "aws"})
        with pytest.raises(ValidationError, match="AWS_REGION"):
            Settings()  # type: ignore[call-arg]

    def test_a_core_variable_is_still_required_on_every_platform(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = {**_CORE, **_GCP}
        del env["COCKROACH_DATABASE_URL"]
        _env(monkeypatch, env)
        with pytest.raises(ValidationError, match="COCKROACH_DATABASE_URL"):
            Settings()  # type: ignore[call-arg]


class TestTheLocalPlatform:
    """The mode a reviewer runs on a laptop with no cloud account at all."""

    def test_local_needs_neither_aws_nor_google_infrastructure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _env(monkeypatch, {**_CORE, "PV_PLATFORM": "local"})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.pv_platform == "local"

    def test_local_still_requires_the_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """There is no version of this product without canonical memory."""
        env = {**_CORE, "PV_PLATFORM": "local"}
        del env["COCKROACH_DATABASE_URL"]
        _env(monkeypatch, env)
        with pytest.raises(ValidationError, match="COCKROACH_DATABASE_URL"):
            Settings()  # type: ignore[call-arg]


class TestThePlatformValueItself:
    def test_an_unknown_platform_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo must not silently select a platform with laxer requirements."""
        _env(monkeypatch, {**_CORE, **_GCP, "PV_PLATFORM": "azure"})
        with pytest.raises(ValidationError, match="PV_PLATFORM"):
            Settings()  # type: ignore[call-arg]

    def test_the_default_platform_is_gcp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pivot is done; the default should be where the product ships."""
        env = {**_CORE, "GOOGLE_API_KEY": "d" * 39, "GCS_ARTIFACT_BUCKET": "b"}
        _env(monkeypatch, env)
        settings = Settings()  # type: ignore[call-arg]
        assert settings.pv_platform == "gcp"


class TestTheGoogleApiKeyIsTreatedAsACredential:
    def test_it_is_a_secret_and_does_not_render_in_repr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``D-00-019``: ``ValidationError.errors()`` once rendered the raw
        environment in plaintext. A key that stringifies is a key that reaches
        a log."""
        _env(monkeypatch, {**_CORE, **_GCP})
        settings = Settings()  # type: ignore[call-arg]
        assert "d" * 39 not in repr(settings)

    def test_it_carries_no_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T0.4: no credential-shaped field carries a non-None default."""
        field = Settings.model_fields["google_api_key"]
        assert field.default in (None, ...), field.default
