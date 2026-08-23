"""``PvAgentStack``: one AgentCore runtime and the absences that bound it.

The authorizer is not the authorization. It answers only "did a Provenance
workload send this request". What an invocation may *do* is bounded entirely by
the ``agent_run_id`` in the payload, which is a server-written row binding
tenant, user, graph, artifact, and allowed cases, expiring in 15 minutes and
unforgeable by the caller.
"""

from __future__ import annotations

import json
from typing import Any

from provenance_infra import config as cfg
from provenance_infra.stacks.agent_stack import AGENTCORE_CONTROL_SERVICE
from pv_cdk_testing import as_list, flatten, policy_statements, resources_of


def _custom_resources(template: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        body["Properties"]
        for body in template["Resources"].values()
        if body["Type"].startswith("Custom::AWS")
    ]


def _create_payload(template: dict[str, Any]) -> str:
    (props,) = _custom_resources(template)
    return flatten(props["Create"])


def test_one_runtime_created_through_the_control_plane_api(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """There is no CDK L2 and no CloudFormation resource type for AgentCore
    Runtime, so it is created with an ``AwsCustomResource`` and the ARN becomes a
    stack output rather than a copied string.
    """
    payload = _create_payload(template_json["PvAgentStack"])
    assert AGENTCORE_CONTROL_SERVICE in payload
    assert "CreateAgentRuntime" in payload
    assert cfg.AGENT_RUNTIME_NAME in payload


def test_the_runtime_is_public_http_and_serves_one_container(
    template_json: dict[str, dict[str, Any]],
) -> None:
    payload = _create_payload(template_json["PvAgentStack"])
    assert '"networkMode"' in payload and "PUBLIC" in payload
    assert '"serverProtocol"' in payload and "HTTP" in payload
    assert "containerConfiguration" in payload
    assert "sha-" + "0" * 40 in payload


def test_the_jwt_authorizer_allows_only_the_worker_client(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``provenance-workers``, not a fourth app client.

    Both invokers -- the control plane and ``advocate_dispatch`` -- are trusted
    control-plane-side workloads. A fourth client would contradict the frozen
    three-client design; reusing ``provenance-agent-runtime`` would mean the
    agent's own callback credential also opens the front door of the runtime it
    runs in, which is strictly worse (section 15.8).
    """
    payload = _create_payload(template_json["PvAgentStack"])
    assert "customJWTAuthorizer" in payload
    assert "/.well-known/openid-configuration" in payload
    assert "allowedClients" in payload
    # A Cognito access token has no ``aud`` claim; allowedClients matches
    # ``client_id``, which is the claim that exists. Configuring an audience
    # produces a runtime that rejects every valid token.
    assert "allowedAudience" not in payload


def test_the_agent_never_learns_a_user_or_tenant_id_from_configuration(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The graph never receives a ``user_id`` or a ``tenant_id``.

    ``GET /internal/v1/agent-runs/{id}`` deliberately omits them, so there is
    nothing for a model to see and repeat -- and nothing in the runtime's
    environment either.
    """
    payload = _create_payload(template_json["PvAgentStack"])
    for forbidden in ("USER_ID", "TENANT_ID", "user_id", "tenant_id"):
        assert forbidden not in payload


def test_the_base_url_is_read_from_ssm_at_deploy_time(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Half of the section 2.2 exception-2 break.

    A CDK reference in this direction would close the cycle; an SSM lookup does
    not, which is why the stack dependency has to be declared by hand.
    """
    template = template_json["PvAgentStack"]
    parameters = template.get("Parameters", {})
    ssm_lookups = [
        name
        for name, body in parameters.items()
        if body.get("Type", "").startswith("AWS::SSM::Parameter::Value")
    ]
    assert ssm_lookups
    assert any(parameters[name].get("Default") == cfg.SSM_API_BASE_URL for name in ssm_lookups)


def test_the_runtime_arn_is_published_for_the_deploy_script(
    template_json: dict[str, dict[str, Any]],
) -> None:
    params = resources_of(template_json["PvAgentStack"], "AWS::SSM::Parameter")
    assert {b["Properties"]["Name"] for b in params.values()} == {cfg.SSM_AGENT_RUNTIME_ARN}


def test_the_custom_resource_is_not_granted_any_resource(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 9.2 prints ``AwsCustomResourcePolicy.fromSdkCalls(ANY_RESOURCE)``.

    That grants the provider Lambda ``bedrock-agentcore:*`` on ``*`` plus an
    unscoped ``PassRole``, and an unscoped ``PassRole`` in the same account as an
    evidence store is not a least-privilege posture. (D-13-011.)
    """
    for _logical, _cfn_type, statement in policy_statements(template_json["PvAgentStack"]):
        actions = [a for a in as_list(statement.get("Action")) if isinstance(a, str)]
        if not any(a.startswith("bedrock-agentcore:") for a in actions):
            continue
        resources = flatten(statement.get("Resource"))
        assert resources != "*"
        assert f"runtime/{'*'}" in resources or cfg.AGENT_RUNTIME_NAME in resources


def test_the_execution_role_trusts_only_agentcore_in_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    roles = resources_of(template_json["PvAgentStack"], "AWS::IAM::Role")
    execution = next(
        body
        for body in roles.values()
        if body["Properties"].get("RoleName") == "provenance-agentcore-execution-role"
    )
    (statement,) = execution["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == {"Ref": "AWS::AccountId"}


def test_the_execution_role_can_pull_its_own_image_and_read_two_secrets(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """No ``provenance/db`` and no ``provenance/crypto``: no SQL credential and no
    capability-proof key.

    The agent's only database reach is the MCP server as ``pv_agent_reader`` on
    five views.
    """
    secret_resources = []
    for logical, _cfn_type, statement in policy_statements(template_json["PvAgentStack"]):
        if "AgentExecutionRole" not in logical:
            continue
        if "secretsmanager:GetSecretValue" not in as_list(statement.get("Action")):
            continue
        secret_resources.append(flatten(statement.get("Resource")))
    joined = " ".join(secret_resources)
    assert "CognitoSecret" in joined
    assert "McpSecret" in joined
    assert "DbSecret" not in joined
    assert "CryptoSecret" not in joined


def test_the_agentcore_name_uses_underscores_only() -> None:
    """The AgentCore name grammar rejects hyphens, and every other physical name
    in this build uses them -- so this is the one place the convention inverts.
    """
    assert "-" not in cfg.AGENT_RUNTIME_NAME
    assert cfg.AGENT_RUNTIME_NAME == "provenance_agents"


def test_the_delete_path_removes_the_runtime(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A runtime left behind after teardown is a resource nothing bills for and
    everyone forgets, until the account is audited.
    """
    (props,) = _custom_resources(template_json["PvAgentStack"])
    assert "Delete" in props
    assert "DeleteAgentRuntime" in flatten(props["Delete"])


def test_mcp_is_enabled_and_degradable(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``PV_MCP_ENABLED=false`` degrades the Interpreter to the control-plane read
    path and the trace renders "MCP UNAVAILABLE -- degraded read path" instead of
    silently succeeding.
    """
    payload = _create_payload(template_json["PvAgentStack"])
    assert "PV_MCP_ENABLED" in payload
    assert "MCP_SERVER_URL" in payload
    assert "MCP_AUTH_SECRET_ARN" in payload


def test_the_agentcore_request_json_is_checked_in_beside_the_stack() -> None:
    """Section 15.7: the AgentCore control-plane API is the most version-sensitive
    surface here.

    ``infra/agentcore/`` holds the exact request JSON so a shape change is a
    one-file edit, and the Phase 0 checklist diffs
    ``--generate-cli-skeleton`` against it.
    """
    from pathlib import Path

    agentcore = Path(__file__).resolve().parents[2] / "agentcore"
    authorizer = json.loads((agentcore / "authorizer.json").read_text(encoding="utf-8"))
    assert "customJWTAuthorizer" in authorizer
    assert "allowedAudience" not in authorizer["customJWTAuthorizer"]

    env = json.loads((agentcore / "env.json").read_text(encoding="utf-8"))
    assert env["BEDROCK_REASONING_MODEL_ID"] == cfg.BEDROCK_REASONING_MODEL_ID
    assert env["BEDROCK_EXTRACTION_MODEL_ID"] == cfg.BEDROCK_EXTRACTION_MODEL_ID
    assert env["BEDROCK_EMBEDDING_MODEL_ID"] == cfg.BEDROCK_EMBEDDING_MODEL_ID

    mcp = json.loads((agentcore / "mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["cockroachdb"]
    assert server["sqlRole"] == "pv_agent_reader"
    assert server["accessMode"] == "READ_ONLY"
    assert len(server["allowedRelations"]) == 5
    assert all(name.endswith("_v1") for name in server["allowedRelations"])
