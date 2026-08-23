"""Tests for the typed settings object — written before ``settings.py`` (T0.4).

Authority
---------
- ``quality/23_PHASE_GATES.md`` section 6, gate ``G0.7``: the three test names
  ``test_settings_rejects_missing_required``,
  ``test_settings_rejects_unknown_embedding_dimension`` and
  ``test_settings_never_defaults_a_credential`` are named by the gate and must
  keep those exact names.
- ``EXECUTION/70_TASK_PLAN.md`` T0.4 acceptance criteria.
- ``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon (frozen 2026-08-17)*,
  which is what the inference-profile tests defend (defect ``D-00-002``).

Why the environment is scrubbed in a fixture
--------------------------------------------
``Settings`` reads the process environment, so a developer machine that happens
to export ``AWS_REGION`` would otherwise make ``test_settings_rejects_missing_required``
pass or fail for reasons unrelated to the code. ``_clean_env`` deletes every
variable the model declares before each test, which is the test-suite
equivalent of the ``env -i`` in the ``G0.7`` command. ``monkeypatch`` is the
only sanctioned way to touch ``os.environ`` in this repository, and it is
confined to tests.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from provenance_contracts.settings import (
    CREDENTIAL_NAME_PATTERN,
    ROLE_DSN_BINDINGS,
    Settings,
    SettingsNotReadyError,
    SettingsValidationError,
    env_name_of,
    get_settings,
    reset_settings_cache,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# A complete, valid environment.
#
# Values are shaped like the examples in ops/40_INFRA_IAC.md section 12 and the
# ops/41_RUNBOOK.md section 2.5 template. Nothing here is a real credential:
# the DSN password is a sentinel this file greps for, precisely so a leak is
# visible.
# ---------------------------------------------------------------------------

SECRET_SENTINEL = "n0t-a-real-password-3f8c1d"

_POOL_ID = "us-east-1_A1b2C3d4E"
_ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{_POOL_ID}"

COMPLETE_ENV: dict[str, str] = {
    # --- core
    # This fixture sets COGNITO_*, S3_*, SES_*, EVENTBRIDGE_* and SQS_*, so it
    # IS an AWS environment and now says so. Before PV_PLATFORM existed the
    # platform was implicit in which variables happened to be present, which is
    # how fifteen AWS variables became unconditionally required.
    "PV_PLATFORM": "aws",
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8080",
    "WEB_BASE_URL": "http://localhost:3000",
    "AWS_REGION": "us-east-1",
    "OTEL_SERVICE_NAME": "provenance-control-plane",
    # --- authentication
    "COGNITO_USER_POOL_ID": _POOL_ID,
    "COGNITO_ISSUER": _ISSUER,
    "COGNITO_JWKS_URL": f"{_ISSUER}/.well-known/jwks.json",
    "COGNITO_TOKEN_ENDPOINT": (
        "https://provenance-auth.auth.us-east-1.amazoncognito.com/oauth2/token"
    ),
    "COGNITO_WEB_CLIENT_ID": "1a2b3c4d5e6f7g8h9i0j",
    "COGNITO_AGENT_CLIENT_ID": "2b3c4d5e6f7g8h9i0j1a",
    "COGNITO_AGENT_CLIENT_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:provenance/cognito/agent"
    ),
    "COGNITO_WORKER_CLIENT_ID": "3c4d5e6f7g8h9i0j1a2b",
    "COGNITO_WORKER_CLIENT_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:provenance/cognito/worker"
    ),
    # --- database (only the pv_app_reader_writer DSN is required)
    "COCKROACH_DATABASE_URL": (
        f"postgresql://pv_app_reader_writer:{SECRET_SENTINEL}"
        "@cluster-host:26257/provenance?sslmode=verify-full"
    ),
    # --- cryptographic material
    "PROVENANCE_CAPABILITY_HMAC_KEY": "Y2FwYWJpbGl0eS1obWFjLWtleS0zMi1ieXRlcy0wMDA=",
    "PROVENANCE_CAPABILITY_HMAC_KID": "k1",
    "CURSOR_HMAC_KEY": "Y3Vyc29yLWhtYWMta2V5LTMyLWJ5dGVzLTAwMDAwMDA=",
    "INGEST_ALIAS_HMAC_KEY": "aW5nZXN0LWFsaWFzLWhtYWMta2V5LTMyLWJ5dGVzMDA=",
    # --- storage
    # The two TTLs are required rather than defaulted because their names
    # contain URL and the credential-default rule is mechanical. Values from
    # ops/40_INFRA_IAC.md section 12.5.
    "S3_ARTIFACT_BUCKET": "provenance-artifacts-us-east-1",
    "UPLOAD_URL_TTL_SECONDS": "900",
    "DOWNLOAD_URL_TTL_SECONDS": "300",
    # --- email
    "SES_INGEST_DOMAIN": "in.provenance.app",
    "SES_FROM_ADDRESS": "disputes@provenance.app",
    # --- events and scheduling
    "EVENTBRIDGE_BUS_NAME": "provenance-domain-bus",
    "EVENTBRIDGE_SCHEDULER_GROUP": "provenance-triggers",
    "SQS_DLQ_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/provenance-worker-dlq",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every declared variable, then drop the settings cache."""
    for field_name, field_info in Settings.model_fields.items():
        monkeypatch.delenv(env_name_of(field_name, field_info), raising=False)
    reset_settings_cache()


def _set_complete_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name, value in {**COMPLETE_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


def _complete(**overrides: Any) -> Settings:
    """Construct from keyword arguments, which carry the upper-case alias."""
    return Settings(**{**COMPLETE_ENV, **overrides})


# ---------------------------------------------------------------------------
# G0.7 — the three assertions the gate names
# ---------------------------------------------------------------------------


def test_settings_rejects_missing_required() -> None:
    """An empty environment is a startup failure, not a set of None fields.

    Mirrors the G0.7 command:
        env -i PATH="$PATH" python -c \
            "from provenance_contracts.settings import Settings; Settings()"
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "COCKROACH_DATABASE_URL" in message
    # The gate says "and others" — a single missing name would mean the rest
    # of the manifest had quietly acquired defaults.
    #
    # Since PV_PLATFORM was introduced this assertion has two halves, because
    # the manifest now has two halves. Collapsing them would let a core field
    # acquire a default unnoticed, which is the exact failure the gate names.
    for required_everywhere in (
        "APP_ENV",
        "APP_BASE_URL",
        "PROVENANCE_CAPABILITY_HMAC_KEY",
        "CURSOR_HMAC_KEY",
    ):
        assert required_everywhere in message, required_everywhere

    # The platform-specific half. These are no longer required *unconditionally*
    # — a Google deployment has no Cognito pool — but they are still required by
    # the platform that uses them, and that is what makes the change a move
    # rather than a deletion.
    with pytest.raises(ValidationError) as aws_only:
        Settings(**{**COMPLETE_ENV, "AWS_REGION": "", "S3_ARTIFACT_BUCKET": ""})
    aws_message = str(aws_only.value)
    for required_on_aws in ("AWS_REGION", "S3_ARTIFACT_BUCKET"):
        assert required_on_aws in aws_message, required_on_aws


def test_settings_rejects_unknown_embedding_dimension() -> None:
    """EMBEDDING_DIMENSIONS is frozen at 1024 by the embedding contract."""
    with pytest.raises(ValidationError) as excinfo:
        _complete(EMBEDDING_DIMENSIONS=768)
    assert "EMBEDDING_DIMENSIONS" in str(excinfo.value)

    # And the frozen value still constructs, with the right Python type.
    assert _complete().embedding_dimensions == 1024


def test_settings_never_defaults_a_credential() -> None:
    """No credential-shaped field carries a non-None default.

    T0.4 acceptance: "no field whose name contains SECRET, URL, PASSWORD, or
    TOKEN has a non-None default anywhere in the model". The pattern the module
    enforces is wider (it also covers KEY, DSN, CREDENTIAL); this test asserts
    against the acceptance criterion's own narrower pattern so it cannot be
    satisfied by narrowing the module's pattern.
    """
    acceptance_pattern = re.compile(r"SECRET|URL|PASSWORD|TOKEN")
    offenders: list[str] = []

    for field_name, field_info in Settings.model_fields.items():
        env_name = env_name_of(field_name, field_info)
        if not acceptance_pattern.search(env_name):
            continue
        if field_info.is_required():
            continue
        if field_info.get_default() is not None:
            offenders.append(env_name)

    assert offenders == []
    # The module's own pattern must be at least as wide as the gate's.
    for probe in ("A_SECRET_X", "A_URL_X", "A_PASSWORD_X", "A_TOKEN_X"):
        assert CREDENTIAL_NAME_PATTERN.search(probe), probe


# ---------------------------------------------------------------------------
# Secrets never render
# ---------------------------------------------------------------------------


def test_secret_values_are_secretstr() -> None:
    settings = _complete()
    assert isinstance(settings.cockroach_database_url, SecretStr)
    assert settings.cockroach_database_url.get_secret_value().endswith("sslmode=verify-full")
    assert SECRET_SENTINEL in settings.cockroach_database_url.get_secret_value()


def test_secretstr_never_appears_in_repr_or_str() -> None:
    """repr(), str(), format() and JSON must all mask the credential.

    A settings object reaches a log line, an exception traceback and a crash
    report; each of those calls one of these four.
    """
    settings = _complete()

    renderings = {
        "repr": repr(settings),
        "str": str(settings),
        "format": f"{settings}",
        "json": settings.model_dump_json(),
        "exception": repr(RuntimeError(settings)),
    }
    for how, text in renderings.items():
        assert SECRET_SENTINEL not in text, f"credential leaked through {how}()"

    # The masked marker is present, so the field is rendered but redacted —
    # this distinguishes "masked" from "field silently absent".
    assert "**********" in renderings["repr"]
    # Serialisation is by field name: the aliases are input names, read from the
    # environment, and a dump is not an environment.
    assert json.loads(renderings["json"])["cockroach_database_url"] == "**********"


def test_secret_fields_do_not_leak_through_model_dump() -> None:
    settings = _complete()
    dumped = settings.model_dump()
    assert isinstance(dumped["cockroach_database_url"], SecretStr)
    assert SECRET_SENTINEL not in str(dumped)


def test_a_validation_error_carries_the_raw_environment_and_get_settings_drops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``D-00-005``: ``SecretStr`` does not mask ``ValidationError.errors()``.

    An ``after`` model validator failing attaches ``input`` — the whole
    environment mapping, credentials in plaintext — to every error entry.
    ``str(exc)`` masks it, which is why the leak survived a test suite that
    checked ``repr``, ``str``, ``format`` and ``model_dump_json``. Both halves
    are asserted here: that pydantic really does leak on that path (so this
    test fails if the premise stops holding), and that ``get_settings`` does
    not.
    """
    # POOL_MIN > POOL_MAX trips a cross-field `model_validator(mode="after")`,
    # which is the shape that attaches the whole input mapping.
    _set_complete_env(monkeypatch, COCKROACH_POOL_MIN="9", COCKROACH_POOL_MAX="1")

    with pytest.raises(ValidationError) as raw:
        Settings()  # type: ignore[call-arg]
    # `str(errors())` rather than `json.dumps(errors())`: the `ctx` of an
    # after-validator failure holds the ValueError object itself, which is not
    # JSON-serialisable. The `input` field is what matters and it is a plain
    # mapping in both renderings.
    leaky = str(raw.value.errors()) + raw.value.json()
    assert SECRET_SENTINEL in leaky, (
        "pydantic no longer attaches the raw input to an after-validator error. "
        "If that is genuinely true on this version, this test and the wrapper in "
        "get_settings can both go — but verify it, do not assume it."
    )

    reset_settings_cache()
    with pytest.raises(SettingsValidationError) as wrapped:
        get_settings()
    rendered = "\n".join(
        (str(wrapped.value), repr(wrapped.value), "".join(map(str, wrapped.value.args)))
    )
    assert SECRET_SENTINEL not in rendered
    # The message must still be useful: it names the failing location.
    assert "cockroach_pool" in rendered.lower() or "pool" in rendered.lower()


# ---------------------------------------------------------------------------
# Defect D-00-002 — Bedrock chat models need an inference-profile id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["BEDROCK_REASONING_MODEL_ID", "BEDROCK_EXTRACTION_MODEL_ID"],
)
@pytest.mark.parametrize(
    "bare_id",
    [
        "anthropic.claude-opus-5",
        "anthropic.claude-haiku-4-5",
        "anthropic.claude-opus-4-6-v1",
    ],
)
def test_bare_anthropic_chat_model_id_is_rejected(field: str, bare_id: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _complete(**{field: bare_id})

    message = str(excinfo.value)
    assert "inference profile" in message
    assert "us." in message
    assert "D-00-002" in message


@pytest.mark.parametrize(
    "profile_id",
    [
        "us.anthropic.claude-opus-5",
        "us.anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "global.anthropic.claude-opus-5",
    ],
)
def test_inference_profile_chat_model_id_is_accepted(profile_id: str) -> None:
    settings = _complete(BEDROCK_REASONING_MODEL_ID=profile_id)
    assert settings.bedrock_reasoning_model_id == profile_id


def test_chat_model_defaults_are_the_verified_invocable_ids() -> None:
    """The defaults are what Phase 0 proved invocable, not the superseded ids."""
    settings = _complete()
    assert settings.bedrock_extraction_model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert settings.bedrock_reasoning_model_id == "us.anthropic.claude-opus-4-6-v1"
    assert settings.bedrock_embedding_model_id == "amazon.titan-embed-text-v2:0"


def test_embedding_model_id_is_a_bare_id_and_is_not_rejected() -> None:
    """The inference-profile rule applies to Anthropic chat models only."""
    settings = _complete()
    assert not settings.bedrock_embedding_model_id.startswith("us.")

    with pytest.raises(ValidationError):
        _complete(BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v1")


# ---------------------------------------------------------------------------
# Phase-gated optional DSNs
# ---------------------------------------------------------------------------


def test_absent_role_dsns_do_not_raise_at_construction() -> None:
    """The pv_* DSNs do not exist until Phase 2, so Phase 0 must still start."""
    settings = _complete()
    assert settings.cockroach_kernel_url is None
    assert settings.cockroach_migrator_url is None
    assert settings.provenance_test_db_url is None


@pytest.mark.parametrize("role", ["pv_kernel_writer", "pv_migrator"])
def test_require_role_dsns_raises_at_the_point_of_use(role: str) -> None:
    settings = _complete()
    binding = ROLE_DSN_BINDINGS[role]

    with pytest.raises(SettingsNotReadyError) as excinfo:
        settings.require_role_dsns(role)

    message = str(excinfo.value)
    assert role in message
    assert binding.env_var is not None
    assert binding.env_var in message
    assert binding.secret_key in message
    # The failure must name the phase that creates the value, or the reader
    # has no way to tell "misconfigured" from "not built yet".
    assert "Phase 2" in message


def test_present_role_dsn_is_returned_for_its_role(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel_dsn = (
        f"postgresql://pv_kernel_writer:{SECRET_SENTINEL}"
        "@cluster-host:26257/provenance?sslmode=verify-full"
    )
    _set_complete_env(monkeypatch, COCKROACH_KERNEL_URL=kernel_dsn)

    settings = Settings()
    settings.require_role_dsns("pv_kernel_writer", "pv_app_reader_writer")
    assert settings.dsn_for_role("pv_kernel_writer").get_secret_value() == kernel_dsn
    assert SECRET_SENTINEL not in repr(settings)


def test_role_map_covers_the_five_sql_roles_and_five_secret_keys() -> None:
    assert set(ROLE_DSN_BINDINGS) == {
        "pv_migrator",
        "pv_app_reader_writer",
        "pv_kernel_writer",
        "pv_agent_reader",
        "pv_ops_reader",
    }
    assert {b.secret_key for b in ROLE_DSN_BINDINGS.values()} == {
        "migrator_url",
        "app_url",
        "kernel_url",
        "agent_url",
        "ops_reader_url",
    }


def test_dsn_for_an_unknown_role_names_the_known_roles() -> None:
    settings = _complete()
    with pytest.raises(ValueError, match="pv_kernel_writer"):
        settings.dsn_for_role("pv_root")


def test_agent_reader_dsn_is_not_an_environment_variable() -> None:
    """pv_agent_reader reaches the database through MCP, not through a DSN."""
    settings = _complete()
    assert ROLE_DSN_BINDINGS["pv_agent_reader"].env_var is None
    with pytest.raises(SettingsNotReadyError, match="MCP_AUTH_SECRET_ARN"):
        settings.dsn_for_role("pv_agent_reader")


def test_require_agent_runtime_targets_names_its_phase() -> None:
    settings = _complete()
    with pytest.raises(SettingsNotReadyError) as excinfo:
        settings.require_agent_runtime_targets()
    message = str(excinfo.value)
    # AGENTCORE_RUNTIME_ARN is injected at deploy (Phase 13, 40_INFRA_IAC
    # section 2.4 step 9); the MCP endpoint arrives in Phase 11.
    assert "AGENTCORE_RUNTIME_ARN" in message
    assert "Phase 13" in message
    assert "MCP_SERVER_URL" in message
    assert "Phase 11" in message


def test_require_agent_runtime_targets_is_quiet_when_mcp_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PV_MCP_ENABLED=false is the documented degradation, not a misconfiguration."""
    _set_complete_env(
        monkeypatch,
        PV_MCP_ENABLED="false",
        AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/x",
    )
    Settings().require_agent_runtime_targets()


# ---------------------------------------------------------------------------
# The remaining frozen contracts
# ---------------------------------------------------------------------------


def test_issuer_must_match_region_and_pool() -> None:
    with pytest.raises(ValidationError, match="COGNITO_ISSUER"):
        _complete(COGNITO_ISSUER="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_ZZZZZ")


def test_jwks_url_must_be_under_the_issuer() -> None:
    with pytest.raises(ValidationError, match="COGNITO_JWKS_URL"):
        _complete(COGNITO_JWKS_URL="https://example.com/.well-known/jwks.json")


def test_embedding_version_is_frozen_at_v1() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_VERSION"):
        _complete(EMBEDDING_VERSION="v2")


def test_unknown_variable_passed_in_is_refused() -> None:
    """extra='forbid': a stale variable is a startup failure, not a no-op."""
    with pytest.raises(ValidationError, match="COCKROACH_DATABSE_URL"):
        _complete(COCKROACH_DATABSE_URL="typo")


def test_settings_are_immutable() -> None:
    settings = _complete()
    with pytest.raises(ValidationError):
        settings.app_env = "prod"  # type: ignore[misc]


def test_settings_read_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_env(monkeypatch, APP_ENV="demo")
    assert Settings().app_env == "demo"


def test_get_settings_is_cached_and_resettable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_env(monkeypatch)
    first = get_settings()
    assert get_settings() is first

    monkeypatch.setenv("APP_ENV", "demo")
    assert get_settings().app_env == "local", "cache must not re-read the environment"

    reset_settings_cache()
    assert get_settings().app_env == "demo"


def test_action_allowlist_is_parsed_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """41_RUNBOOK section 2.5 writes a bare address, not a JSON array."""
    _set_complete_env(monkeypatch, PV_ACTION_ALLOWLIST="demo-inbox@example.com, a@b.test")
    settings = Settings()
    assert settings.action_allowlist_addresses == ("demo-inbox@example.com", "a@b.test")


def test_action_allowlist_defaults_closed() -> None:
    assert _complete().action_allowlist_addresses == ()


def test_fixture_mode_is_off_unless_declared() -> None:
    assert _complete().fixture_mode is False
    assert _complete(PV_AGENT_MODE="FIXTURE").fixture_mode is True
