"""The Bedrock model id canon, asserted in IAM and in configuration.

``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*, frozen 2026-08-17 by
live ``Converse`` invocation against this account:

- Anthropic chat models are invocable **only** by inference-profile id. A bare
  id returns ``ValidationException: ... Retry your request with the ID or ARN of
  an inference profile``.
- Every other provider is invocable **only** by bare id and rejects the profile
  form. The rule is the **mirror image**, not an extension, so a client that
  applies one rule uniformly cannot call both families.

Which makes these contract tests rather than style checks: an IAM policy naming
``anthropic.claude-opus-5`` would allow a call that can never succeed, and an
environment default carrying the wrong form would fail at the first invocation
with an error that reads like a permissions problem.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from provenance_infra import config as cfg
from pv_cdk_testing import as_list, flatten, key_values, policy_statements, resources_of

BEDROCK_ROLES = (
    ("PvApiStack", "InstanceRole"),
    ("PvAgentStack", "AgentExecutionRole"),
)


def _bedrock_resources(template: dict[str, Any], role_fragment: str) -> list[str]:
    found: list[str] = []
    for logical, _cfn_type, statement in policy_statements(template):
        if role_fragment not in logical:
            continue
        actions = [a for a in as_list(statement.get("Action")) if isinstance(a, str)]
        if not any(a.startswith("bedrock:Invoke") for a in actions):
            continue
        found.extend(flatten(r) for r in as_list(statement.get("Resource")))
    return found


def _agent_runtime_payload(template: dict[str, Any]) -> str:
    bodies = [
        body for body in template["Resources"].values() if body["Type"].startswith("Custom::AWS")
    ]
    return flatten([body["Properties"].get("Create") for body in bodies])


@pytest.mark.parametrize(("stack_name", "role_fragment"), BEDROCK_ROLES)
def test_bedrock_policy_names_the_three_reachable_model_ids(
    template_json: dict[str, dict[str, Any]], stack_name: str, role_fragment: str
) -> None:
    resources = " ".join(_bedrock_resources(template_json[stack_name], role_fragment))
    assert resources, f"{stack_name}/{role_fragment} has no bedrock:InvokeModel statement"
    for model_id in (
        cfg.BEDROCK_REASONING_MODEL_ID,
        cfg.BEDROCK_EXTRACTION_MODEL_ID,
        cfg.BEDROCK_EMBEDDING_MODEL_ID,
    ):
        assert model_id in resources, f"{stack_name} does not grant {model_id}"


@pytest.mark.parametrize(("stack_name", "role_fragment"), BEDROCK_ROLES)
def test_bedrock_policy_is_enumerated_not_wildcarded(
    template_json: dict[str, dict[str, Any]], stack_name: str, role_fragment: str
) -> None:
    """A code path reaching for a fourth model must fail closed."""
    resources = _bedrock_resources(template_json[stack_name], role_fragment)
    assert resources
    for resource in resources:
        assert resource != "*"
        assert "foundation-model/*" not in resource
        assert "inference-profile/*" not in resource


@pytest.mark.parametrize(("stack_name", "role_fragment"), BEDROCK_ROLES)
def test_anthropic_ids_are_profiles_and_titan_is_a_bare_foundation_model(
    template_json: dict[str, dict[str, Any]], stack_name: str, role_fragment: str
) -> None:
    """The mirror-image rule, expressed as ARN classes.

    An inference-profile invocation is authorised against the profile ARN *and*
    against the foundation-model ARN in every region the profile can route to,
    so both appear for the Anthropic ids. Granting only the profile produces an
    ``AccessDeniedException`` at the first cross-region routing decision, which
    is intermittent and looks like throttling.
    """
    resources = " ".join(_bedrock_resources(template_json[stack_name], role_fragment))

    for model_id in (cfg.BEDROCK_REASONING_MODEL_ID, cfg.BEDROCK_EXTRACTION_MODEL_ID):
        assert cfg.is_inference_profile_id(model_id)
        assert f"bedrock:{cfg.REGION}:<account>:inference-profile/{model_id}" in resources, model_id
        bare = cfg.bare_model_id(model_id)
        for region in cfg.INFERENCE_PROFILE_REGIONS:
            assert f"bedrock:{region}::foundation-model/{bare}" in resources

    titan = cfg.BEDROCK_EMBEDDING_MODEL_ID
    assert not cfg.is_inference_profile_id(titan)
    assert f"bedrock:{cfg.REGION}::foundation-model/{titan}" in resources
    assert f"inference-profile/{titan}" not in resources


def test_no_denied_model_id_appears_anywhere(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Opus 5 and Sonnet 5 are denied on this account.

    The bare ``anthropic.claude-*`` forms the pack carried before the canon was
    frozen are in the same set: a bare Anthropic id is not invocable in any form,
    so a policy naming one grants nothing while looking correct.

    The match is anchored on both sides. ``anthropic.claude-haiku-4-5`` is a
    prefix of the *valid* dated id ``us.anthropic.claude-haiku-4-5-20251001-v1:0``,
    so a naive substring search would fail on a correct template -- which is the
    kind of false alarm that gets a check deleted.
    """
    patterns = {
        denied: re.compile(rf"(?<![\w.-]){re.escape(denied)}(?![\w.:-])")
        for denied in cfg.BEDROCK_DENIED_MODEL_IDS
    }
    for stack_name, template in template_json.items():
        text = flatten(template)
        for denied, pattern in patterns.items():
            assert not pattern.search(text), f"{stack_name} references the denied id {denied}"


def test_control_plane_environment_carries_the_exact_canonical_strings(
    template_json: dict[str, dict[str, Any]],
) -> None:
    service = next(
        iter(resources_of(template_json["PvApiStack"], "AWS::AppRunner::Service").values())
    )
    image_config = service["Properties"]["SourceConfiguration"]["ImageRepository"][
        "ImageConfiguration"
    ]
    variables = key_values(image_config["RuntimeEnvironmentVariables"])
    assert variables["BEDROCK_REASONING_MODEL_ID"] == cfg.BEDROCK_REASONING_MODEL_ID
    assert variables["BEDROCK_EXTRACTION_MODEL_ID"] == cfg.BEDROCK_EXTRACTION_MODEL_ID
    assert variables["BEDROCK_EMBEDDING_MODEL_ID"] == cfg.BEDROCK_EMBEDDING_MODEL_ID
    assert variables["EMBEDDING_DIMENSIONS"] == cfg.EMBEDDING_DIMENSIONS
    assert variables["EMBEDDING_VERSION"] == cfg.EMBEDDING_VERSION


def test_agent_runtime_environment_carries_the_exact_canonical_strings(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The router passes configured ids through unmodified, so the configured
    value is the invoked value and ``agent_runs.model_route`` records it.
    """
    payload = _agent_runtime_payload(template_json["PvAgentStack"])
    for name, value in (
        ("BEDROCK_REASONING_MODEL_ID", cfg.BEDROCK_REASONING_MODEL_ID),
        ("BEDROCK_EXTRACTION_MODEL_ID", cfg.BEDROCK_EXTRACTION_MODEL_ID),
        ("BEDROCK_EMBEDDING_MODEL_ID", cfg.BEDROCK_EMBEDDING_MODEL_ID),
        ("EMBEDDING_DIMENSIONS", cfg.EMBEDDING_DIMENSIONS),
        ("EMBEDDING_VERSION", cfg.EMBEDDING_VERSION),
    ):
        assert name in payload
        assert value in payload, f"{name} should be {value}"


def test_the_embedding_contract_is_frozen() -> None:
    """1024 dimensions, version v1, one model.

    Serving a v1 vector to a v2 index would silently corrupt every ranking, so
    these are contract values rather than tunables.
    """
    assert cfg.BEDROCK_EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"
    assert cfg.EMBEDDING_DIMENSIONS == "1024"
    assert cfg.EMBEDDING_VERSION == "v1"


def test_the_fallback_reasoning_model_is_granted_but_never_configured(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Sonnet 4.6 is the capacity fallback, not the default.

    Granting it in IAM means switching under throttling is a one-line
    environment change rather than a policy edit plus a redeploy. Configuring it
    would silently change which model characterises contradictions, and Tier R
    is documented as never downgrading.
    """
    resources = " ".join(_bedrock_resources(template_json["PvApiStack"], "InstanceRole"))
    assert cfg.BEDROCK_REASONING_FALLBACK_MODEL_ID in resources

    service = next(
        iter(resources_of(template_json["PvApiStack"], "AWS::AppRunner::Service").values())
    )
    image_config = service["Properties"]["SourceConfiguration"]["ImageRepository"][
        "ImageConfiguration"
    ]
    variables = key_values(image_config["RuntimeEnvironmentVariables"])
    assert cfg.BEDROCK_REASONING_FALLBACK_MODEL_ID not in variables.values()
