"""``PvApiStack``: one App Runner container, two roles, one SSM handshake.

The two roles are asserted separately because conflating them is the most
common App Runner mistake and the one that accidentally hands the running
container ECR write access.
"""

from __future__ import annotations

from typing import Any

from provenance_infra import config as cfg
from pv_cdk_testing import (
    as_list,
    flatten,
    key_values,
    policy_statements,
    resources_of,
    trust_statements,
)


def _service(template: dict[str, Any]) -> dict[str, Any]:
    services = resources_of(template, "AWS::AppRunner::Service")
    assert len(services) == 1
    return next(iter(services.values()))["Properties"]


def _image_config(template: dict[str, Any]) -> dict[str, Any]:
    return _service(template)["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]


def test_the_service_listens_on_8080_and_health_checks_liveness_only(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``/v1/healthz`` is a bare liveness probe and never carries ``fixture_mode``.

    ``GET /v1/version`` is the single authoritative operating-mode channel and is
    deliberately not the health check: a health check that touched the database
    would take the service out of rotation for a database blip.
    """
    props = _service(template_json["PvApiStack"])
    assert props["ServiceName"] == cfg.APP_RUNNER_SERVICE_NAME
    assert _image_config(template_json["PvApiStack"])["Port"] == "8080"
    health = props["HealthCheckConfiguration"]
    assert health["Path"] == "/v1/healthz"
    assert health["Protocol"] == "HTTP"
    assert health["HealthyThreshold"] == 1
    assert health["UnhealthyThreshold"] == 5


def test_autoscaling_is_pinned_one_to_two(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """min 1 keeps one instance always warm, because cold starts are a demo risk
    (``G13.8``). max 2 bounds the in-process rate-limit error at 2x rather than
    leaving it unbounded (section 15.11).
    """
    configs = resources_of(template_json["PvApiStack"], "AWS::AppRunner::AutoScalingConfiguration")
    assert len(configs) == 1
    props = next(iter(configs.values()))["Properties"]
    assert props["MinSize"] == 1
    assert props["MaxSize"] == 2
    assert props["MaxConcurrency"] == 40


def test_deploys_are_explicit_rather_than_triggered_by_a_docker_push(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """An auto-deploy on ECR push means a ``docker push`` during rehearsal
    restarts the service, and it makes ``G13.2``'s sha equality a race rather
    than a statement about a decision.
    """
    source = _service(template_json["PvApiStack"])["SourceConfiguration"]
    assert source["AutoDeploymentsEnabled"] is False


def test_the_image_is_pinned_to_an_immutable_git_sha_tag(
    template_json: dict[str, dict[str, Any]],
) -> None:
    identifier = flatten(
        _service(template_json["PvApiStack"])["SourceConfiguration"]["ImageRepository"][
            "ImageIdentifier"
        ]
    )
    assert identifier.endswith(":sha-" + "0" * 40)
    assert ":latest" not in identifier


def test_the_container_runs_two_uvicorn_workers(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Two workers on one vCPU is deliberate: the workload is I/O-bound
    (CockroachDB round trips, Bedrock waits), so a second worker absorbs a
    blocked event loop.
    """
    command = _image_config(template_json["PvApiStack"])["StartCommand"]
    assert "--workers 2" in command
    assert "--port 8080" in command
    props = _service(template_json["PvApiStack"])["InstanceConfiguration"]
    assert props["Cpu"] == "1 vCPU"
    assert props["Memory"] == "2 GB"


def test_egress_is_public_and_ingress_is_public(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """No VPC connector.

    CockroachDB Cloud Basic offers no IP allowlist and no PrivateLink, so a
    connector would buy no network-layer restriction while adding a NAT gateway
    and cold-start latency to reach the same public TLS endpoint. Claiming a
    private path that does not exist is exactly what section 3 of the gates
    exists to prevent.
    """
    network = _service(template_json["PvApiStack"])["NetworkConfiguration"]
    assert network["EgressConfiguration"]["EgressType"] == "DEFAULT"
    assert network["IngressConfiguration"]["IsPubliclyAccessible"] is True
    assert not resources_of(template_json["PvApiStack"], "AWS::AppRunner::VpcConnector")


def test_xray_observability_is_enabled(template_json: dict[str, dict[str, Any]]) -> None:
    props = _service(template_json["PvApiStack"])
    assert props["ObservabilityConfiguration"]["ObservabilityEnabled"] is True
    configs = resources_of(
        template_json["PvApiStack"], "AWS::AppRunner::ObservabilityConfiguration"
    )
    assert next(iter(configs.values()))["Properties"]["TraceConfiguration"] == {"Vendor": "AWSXRAY"}


def test_the_two_roles_are_distinct_and_trust_different_principals(
    template_json: dict[str, dict[str, Any]],
) -> None:
    principals = {}
    for logical, statement in trust_statements(template_json["PvApiStack"]):
        services = as_list((statement.get("Principal") or {}).get("Service"))
        for service in services:
            if "apprunner" in str(service):
                principals[logical] = service
    assert set(principals.values()) == {
        "build.apprunner.amazonaws.com",
        "tasks.apprunner.amazonaws.com",
    }


def test_the_access_role_can_pull_but_not_push(
    template_json: dict[str, dict[str, Any]],
) -> None:
    actions = set()
    for logical, _cfn_type, statement in policy_statements(template_json["PvApiStack"]):
        if "AccessRole" not in logical:
            continue
        actions.update(a for a in as_list(statement.get("Action")) if isinstance(a, str))
    assert "ecr:BatchGetImage" in actions
    for forbidden in ("ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:CompleteLayerUpload"):
        assert forbidden not in actions


def test_the_instance_role_cannot_reach_a_prefix_it_does_not_own(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Tenant and user are the first two path segments, so every future
    restriction can be a prefix condition rather than an object lookup.
    """
    statement = next(
        s
        for _logical, _cfn_type, s in policy_statements(template_json["PvApiStack"])
        if s.get("Sid") == "PresignAndVerifyArtifacts"
    )
    resources = flatten(statement["Resource"])
    for prefix in ("raw/*", "normalized/*", "ses/*"):
        assert prefix in resources
    assert "s3:DeleteObject" not in as_list(statement["Action"])


def test_the_control_plane_can_send_only_from_the_dispute_address(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The configuration set condition is what makes the bounce events exist.

    Without ``ses:ConfigurationSetName`` pinned, a send could bypass the set and
    ``action.failed.v1`` with ``error_code: RECIPIENT_BOUNCED`` could never be
    produced.
    """
    statement = next(
        s
        for _logical, _cfn_type, s in policy_statements(template_json["PvApiStack"])
        if s.get("Sid") == "SendApprovedActionEmail"
    )
    condition = statement["Condition"]["StringEquals"]
    assert condition["ses:FromAddress"] == "disputes@provenance.app"
    assert condition["ses:ConfigurationSetName"] == cfg.SES_CONFIGURATION_SET


def test_the_control_plane_publishes_only_to_the_domain_bus(
    template_json: dict[str, dict[str, Any]],
) -> None:
    statement = next(
        s
        for _logical, _cfn_type, s in policy_statements(template_json["PvApiStack"])
        if s.get("Sid") == "PublishDomainEvents"
    )
    assert statement["Action"] == "events:PutEvents"
    assert "*" not in as_list(statement["Resource"])


def test_the_agent_runtime_arn_is_a_placeholder_until_the_deploy_script_injects_it(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 2.2 exception 2: the two services genuinely call each other.

    Expressing both directions as CDK references produces a deadly embrace that
    no restructuring removes, so this half is closed at runtime by a one-line
    ``update-service`` in the deploy script.
    """
    variables = key_values(
        _image_config(template_json["PvApiStack"])["RuntimeEnvironmentVariables"]
    )
    assert variables["AGENTCORE_RUNTIME_ARN"] == "pending-agent-stack"
    assert variables["AGENTCORE_QUALIFIER"] == "DEFAULT"


def test_the_api_base_url_is_published_for_the_agent_stack_to_read(
    template_json: dict[str, dict[str, Any]],
) -> None:
    params = resources_of(template_json["PvApiStack"], "AWS::SSM::Parameter")
    names = {b["Properties"]["Name"] for b in params.values()}
    assert cfg.SSM_API_BASE_URL in names


def test_the_action_kill_switch_and_recipient_mode_are_explicit(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``DEMO_SINK`` is never a silent rewrite: the Advocate drafts to the sink
    address and the UI renders the sink address, so the human approves what will
    actually be sent.
    """
    variables = key_values(
        _image_config(template_json["PvApiStack"])["RuntimeEnvironmentVariables"]
    )
    assert variables["ACTION_RECIPIENT_MODE"] == "DEMO_SINK"
    assert variables["PV_ACTION_EXECUTION_MODE"] == "ENABLED"
    assert variables["SES_DEMO_SINK_DOMAIN"] == "demo-sink.provenance.app"


def test_the_cognito_issuer_and_jwks_url_agree_with_the_pool(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``COGNITO_JWKS_URL`` must equal the issuer plus the well-known suffix.

    A settings validator asserts the prefix relationship at container start, so a
    copy-paste error fails at boot rather than at the first request; this asserts
    the same relationship in the template that supplies both.
    """
    variables = key_values(
        _image_config(template_json["PvApiStack"])["RuntimeEnvironmentVariables"]
    )
    issuer = flatten(variables["COGNITO_ISSUER"])
    jwks = flatten(variables["COGNITO_JWKS_URL"])
    assert issuer.startswith(f"https://cognito-idp.{cfg.REGION}.amazonaws.com/")
    assert jwks == f"{issuer}/.well-known/jwks.json"
