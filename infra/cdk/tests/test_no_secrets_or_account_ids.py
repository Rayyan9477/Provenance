"""``G0.3`` and ``G13.6``, asserted at synth time instead of after a deploy.

``G0.3`` scans this repository for credential-shaped literals and account ids.
``G13.6`` asserts that no secret is a plaintext App Runner environment
*variable* and that the three named keys arrive through the secrets channel.

Both are checked here against the synthesised templates, which is where a
mistake would actually land: a secret typed into a construct becomes a literal
in ``cdk.out`` and in every CloudFormation event, long before anyone runs the
gate command.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from provenance_infra import config as cfg
from pv_cdk_testing import (
    ACCOUNT_ID_RE,
    CREDENTIAL_SHAPES,
    all_resources_of,
    key_values,
    render,
    resources_of,
)

# G13.6's jq filter, transcribed.
G13_6_VARIABLE_FILTER = re.compile(r"://|AKIA|BEGIN ")

G13_6_REQUIRED_SECRET_KEYS = (
    "COCKROACH_DATABASE_URL",
    "COGNITO_AGENT_CLIENT_SECRET_ARN",
    "MCP_AUTH_SECRET_ARN",
)


def test_no_template_contains_a_literal_account_id(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Every account reference must be ``{"Ref": "AWS::AccountId"}``.

    The account id is a targeting primitive for a confused-deputy attack
    (40_INFRA_IAC.md section 757), and it is the single value most likely to be
    pasted into a policy by hand.
    """
    for stack_name, template in template_json.items():
        text = render(template)
        matches = sorted(set(ACCOUNT_ID_RE.findall(text)))
        assert not matches, f"{stack_name} contains account-id-shaped literals: {matches}"


@pytest.mark.parametrize(("label", "pattern"), CREDENTIAL_SHAPES, ids=lambda v: str(v)[:24])
def test_no_template_contains_a_credential_shaped_literal(
    template_json: dict[str, dict[str, Any]],
    label: str,
    pattern: re.Pattern[str],
) -> None:
    for stack_name, template in template_json.items():
        text = render(template)
        assert not pattern.search(text), f"{stack_name} contains a {label}"


def test_secrets_are_created_empty_with_only_their_key_names(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Four secrets, each carrying a JSON *shape* and no value.

    ``ops/secrets-populate.sh`` writes the values out of band, so no secret
    material ever appears in a CDK template (section 2.4 step 3).
    """
    secrets = all_resources_of(template_json, "AWS::SecretsManager::Secret")
    by_name = {body["Properties"]["Name"]: body["Properties"] for body in secrets.values()}
    assert set(by_name) == set(cfg.SECRET_KEYS)

    for name, keys in cfg.SECRET_KEYS.items():
        props = by_name[name]
        assert "SecretString" not in props, f"{name} has an inline value"
        template = json.loads(props["GenerateSecretString"]["SecretStringTemplate"])
        assert set(template) == set(keys)
        assert all(value == "" for value in template.values())


def test_the_db_secret_carries_one_url_per_sql_role(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Five keys, because there are five SQL roles.

    ``pv_ops_reader`` is created in migration 0008 because ``tools/trace_verify.py``
    has a real consumer, and CANONICAL_DECISIONS.md makes its URL the fifth key
    rather than an optional extra.
    """
    keys = cfg.SECRET_KEYS[cfg.SECRET_DB]
    assert len(keys) == 5
    assert len(cfg.SQL_ROLES) == 5
    assert "ops_reader_url" in keys


def test_app_runner_delivers_every_credential_through_the_secrets_channel(
    template_json: dict[str, dict[str, Any]],
) -> None:
    service = next(
        iter(resources_of(template_json["PvApiStack"], "AWS::AppRunner::Service").values())
    )
    image_config = service["Properties"]["SourceConfiguration"]["ImageRepository"][
        "ImageConfiguration"
    ]
    secrets = key_values(image_config["RuntimeEnvironmentSecrets"])
    for required in G13_6_REQUIRED_SECRET_KEYS:
        assert required in secrets, required

    # ``<arn>:<json-key>::`` -- the trailing pair is the empty version stage and
    # the empty version id. Getting it wrong injects the whole JSON blob and the
    # failure looks like a malformed connection string.
    for name, value in secrets.items():
        assert render(value).count("::") >= 1, name


def test_no_app_runner_runtime_variable_looks_like_material(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``G13.6``'s second command, run against the template rather than the
    deployed service.

    The two public origins legitimately contain ``://``; every other match would
    be a credential. They are enumerated so a third one cannot appear silently.
    """
    allowed_url_variables = {
        "APP_BASE_URL",
        "WEB_BASE_URL",
        "COGNITO_ISSUER",
        "COGNITO_JWKS_URL",
        "COGNITO_TOKEN_ENDPOINT",
        "MCP_SERVER_URL",
    }
    service = next(
        iter(resources_of(template_json["PvApiStack"], "AWS::AppRunner::Service").values())
    )
    image_config = service["Properties"]["SourceConfiguration"]["ImageRepository"][
        "ImageConfiguration"
    ]
    variables = key_values(image_config["RuntimeEnvironmentVariables"])
    for name, value in variables.items():
        if not isinstance(value, str):
            continue
        if not G13_6_VARIABLE_FILTER.search(value):
            continue
        assert name in allowed_url_variables, f"{name}={value!r} looks like material"


def test_no_lambda_environment_variable_looks_like_material(
    template_json: dict[str, dict[str, Any]],
) -> None:
    allowed = {
        "APP_BASE_URL",
        "COGNITO_TOKEN_ENDPOINT",
        "WEB_BASE_URL",
    }
    functions = all_resources_of(template_json, "AWS::Lambda::Function")
    for logical, body in functions.items():
        variables = body["Properties"].get("Environment", {}).get("Variables", {})
        for name, value in variables.items():
            if not isinstance(value, str) or not G13_6_VARIABLE_FILTER.search(value):
                continue
            assert name in allowed, f"{logical} {name}={value!r}"


def test_only_the_cognito_trigger_receives_a_database_credential(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """ "No worker holds a SQL credential" has exactly one documented exception.

    ``provenance-cognito-post-confirmation`` writes tenants, users, and
    ingest_aliases in one transaction as ``pv_app_reader_writer`` because there
    is no authenticated principal yet for it to call the API with. Every other
    worker's effect on canonical state goes through ``/internal/v1``.
    """
    functions = all_resources_of(template_json, "AWS::Lambda::Function")
    holders = set()
    for logical, body in functions.items():
        variables = body["Properties"].get("Environment", {}).get("Variables", {})
        if any(name.startswith("COCKROACH_") for name in variables):
            holders.add(logical)
    assert len(holders) == 1, holders
    assert "PostConfirmation" in next(iter(holders))


def test_the_amplify_app_carries_no_access_token(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A repository connection token in a template is a token in every event."""
    app = next(iter(resources_of(template_json["PvWebStack"], "AWS::Amplify::App").values()))
    assert "AccessToken" not in app["Properties"]
    assert "OauthToken" not in app["Properties"]
    assert "BasicAuthConfig" not in app["Properties"]
