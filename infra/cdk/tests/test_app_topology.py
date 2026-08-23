"""The shape of the application: ten stacks, four deployment units, one region.

The deployment-unit assertions exist because ``ARCHITECTURE.md`` section 25
specifies a five-service microservice tree that ``CANONICAL_DECISIONS.md``
supersedes. Building the wrong one would put the Memory Kernel in its own
service and break the single-canonical-writer boundary, so "there are four
deployment units and here is each one" is a test rather than a comment.
"""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from provenance_infra import config as cfg
from pv_cdk_testing import STACK_NAMES, all_resources_of, render, resources_of


def test_exactly_ten_stacks_with_the_canonical_names(stacks: dict[str, cdk.Stack]) -> None:
    assert set(stacks) == set(STACK_NAMES)
    assert len(stacks) == 10


def test_every_stack_is_pinned_to_us_east_1(stacks: dict[str, cdk.Stack]) -> None:
    """Single region, no cross-region reference (section 1.1)."""
    for name, stack in stacks.items():
        assert stack.region == cfg.REGION, name


def test_deployment_unit_web_is_one_amplify_app(
    template_json: dict[str, dict[str, Any]],
) -> None:
    apps = all_resources_of(template_json, "AWS::Amplify::App")
    assert len(apps) == 1
    (body,) = apps.values()
    assert body["Properties"]["Name"] == cfg.AMPLIFY_APP_NAME
    assert body["Properties"]["Platform"] == "WEB_COMPUTE"


def test_deployment_unit_control_plane_is_one_app_runner_service(
    template_json: dict[str, dict[str, Any]],
) -> None:
    services = all_resources_of(template_json, "AWS::AppRunner::Service")
    assert len(services) == 1, "five services is the rejected ARCHITECTURE.md section 25 tree"
    (body,) = services.values()
    assert body["Properties"]["ServiceName"] == cfg.APP_RUNNER_SERVICE_NAME


def test_deployment_unit_agent_runtime_is_one_runtime_named_with_underscores(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """One AgentCore runtime, not three agent services.

    The runtime is an ``AwsCustomResource`` because there is no L2 and no
    CloudFormation resource type, so the assertion reads the custom resource's
    ``Create`` payload.
    """
    agent = template_json["PvAgentStack"]
    custom = [
        body for body in agent["Resources"].values() if body["Type"].startswith("Custom::AWS")
    ]
    assert len(custom) == 1
    payload = render(custom[0]["Properties"]["Create"])
    assert cfg.AGENT_RUNTIME_NAME in payload
    assert "-" not in cfg.AGENT_RUNTIME_NAME, "the AgentCore name grammar rejects hyphens"
    assert "CreateAgentRuntime" in payload


def test_deployment_unit_workers_is_nine_lambda_functions(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Nine functions total: eight in Compute plus the Cognito trigger in Identity.

    Section 2.1 credits all nine to ``PvComputeStack`` while section 3.4 builds
    the post-confirmation trigger inside ``PvIdentityStack``. The specific
    statement wins, and it has to: ``UserPool.addTrigger`` needs the function,
    and Compute already depends on Identity for the worker client id. (D-13-002.)
    """
    from provenance_infra import workers as wk

    compute = resources_of(template_json["PvComputeStack"], "AWS::Lambda::Function")
    identity = resources_of(template_json["PvIdentityStack"], "AWS::Lambda::Function")

    names = {
        body["Properties"]["FunctionName"]
        for body in list(compute.values()) + list(identity.values())
        if isinstance(body["Properties"].get("FunctionName"), str)
    }
    assert names == {spec.function_name for spec in wk.ALL_WORKERS}
    assert len(names) == 9
    assert (
        len([b for b in compute.values() if isinstance(b["Properties"].get("FunctionName"), str)])
        == 8
    )


def test_exactly_two_container_repositories(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """One image per containerised deployment unit. Not five, not three."""
    repos = all_resources_of(template_json, "AWS::ECR::Repository")
    assert {b["Properties"]["RepositoryName"] for b in repos.values()} == {
        cfg.ECR_CONTROL_PLANE_REPO,
        cfg.ECR_AGENT_RUNTIME_REPO,
    }


def test_mandatory_tags_reach_every_taggable_resource(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Tags are applied at the App level so nothing escapes them.

    ``Project=Provenance`` is what the teardown verification in section 14.5
    greps for; a resource that escapes the tag is a resource that survives
    teardown unnoticed.
    """
    checked = 0
    for stack_name, template in template_json.items():
        for logical, body in template.get("Resources", {}).items():
            tags = body.get("Properties", {}).get("Tags")
            if not isinstance(tags, list) or not tags:
                continue
            keys = {tag["Key"]: tag.get("Value") for tag in tags if isinstance(tag, dict)}
            if "Project" not in keys:
                continue
            checked += 1
            assert keys["Project"] == "Provenance", f"{stack_name}/{logical}"
            assert keys.get("CostCenter") == "crdb-aws-agentic-memory-hackathon"
            assert keys.get("DeleteAfter") == "2026-10-15"
    assert checked > 20, "the tag aspect reached suspiciously few resources"


def test_stack_dependencies_are_acyclic_and_match_the_documented_order(
    stacks: dict[str, cdk.Stack],
) -> None:
    """Section 2.4's deploy order must be derivable from the graph.

    Two orderings are asserted because both are load-bearing:
    Data -> Compute -> Email is the SES cycle resolution (section 2.2
    exception 1), and Api -> Agent is the half of exception 2 that CDK cannot
    infer from an SSM lookup.
    """
    depends: dict[str, set[str]] = {
        name: {dep.stack_name for dep in stack.dependencies} for name, stack in stacks.items()
    }
    assert "PvDataStack" in depends["PvComputeStack"]
    assert "PvComputeStack" in depends["PvEmailStack"]
    assert "PvApiStack" in depends["PvAgentStack"]
    # No stack depends on a stack that depends on it.
    for name, deps in depends.items():
        for dep in deps:
            assert name not in depends[dep], f"cycle: {name} <-> {dep}"


@pytest.mark.parametrize("stack_name", STACK_NAMES)
def test_each_stack_synthesises_to_a_non_empty_template(
    templates: dict[str, Template], stack_name: str
) -> None:
    """The weakest assertion in the suite, kept only as a smoke check.

    It is deliberately the only test in this file that would pass on an empty
    stack, which is why every other test here names a property.
    """
    assert templates[stack_name].to_json()["Resources"]
