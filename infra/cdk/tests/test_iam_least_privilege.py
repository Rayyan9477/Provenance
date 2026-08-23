"""Least privilege, asserted over every synthesised policy in every stack.

Three properties, each of which has a documented reason to exist:

1. **No wildcard resource on an action that can be scoped.** The allowlist below
   is the complete, reviewed set of actions AWS gives no resource-level
   permission for. It is short on purpose: it is also the answer to "which
   policies could you not scope tightly", so anything added to it is a decision
   somebody has to defend.
2. **No wildcard action anywhere**, except the account-root statement KMS
   requires in a key policy -- without it the key becomes unmanageable, which is
   a documented AWS foot-gun rather than a permission we chose.
3. **Every confusable service-principal trust carries ``aws:SourceAccount`` or
   ``aws:SourceArn``.** 40_INFRA_IAC.md section 757 is explicit that the account
   id is a targeting primitive for a confused-deputy attack: a bucket policy
   that allows ``ses.amazonaws.com`` without these lets any AWS customer's
   receipt rule write into an evidence store.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from pv_cdk_testing import as_list, policy_statements, resources_of, trust_statements

# Actions AWS provides no resource-level permission for. Each entry is a
# permission that genuinely cannot be narrowed by ARN; where a condition key
# exists instead, the corresponding statement applies it.
UNSCOPABLE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        # X-Ray segment ingestion has no resource ARN and no condition key.
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        # PutMetricData has no resource ARN. Narrowed by cloudwatch:namespace,
        # which the assertion below requires.
        "cloudwatch:PutMetricData",
        # Textract job ids are not ARNs; these APIs accept no resource. The
        # compensating control is that the caller can only read S3 objects under
        # prefixes it is separately granted.
        "textract:GetDocumentAnalysis",
        "textract:GetDocumentTextDetection",
        "textract:StartDocumentAnalysis",
        "textract:DetectDocumentText",
        "textract:AnalyzeDocument",
        # The ECR authorization token is account-scoped by definition.
        "ecr:GetAuthorizationToken",
        # A CloudWatch Logs *resource policy* is account-level; there is no ARN
        # for the policy itself. Emitted by CDK's CloudWatchLogGroup event
        # target, not by hand-written code.
        "logs:PutResourcePolicy",
        "logs:DeleteResourcePolicy",
        "logs:DescribeResourcePolicies",
        "logs:DescribeLogGroups",
    }
)

# cloudwatch:PutMetricData must always carry the namespace narrowing.
REQUIRE_CONDITION: Final[dict[str, str]] = {"cloudwatch:PutMetricData": "cloudwatch:namespace"}

# Service principals whose trust policy AWS documents as confused-deputy
# sensitive. ``lambda.amazonaws.com`` is deliberately absent: Lambda assumes an
# execution role for a function in this account only, and neither condition key
# is populated on that call, so requiring one would be cargo-cult rather than a
# control.
CONFUSABLE_SERVICES: Final[frozenset[str]] = frozenset(
    {
        "ses.amazonaws.com",
        "textract.amazonaws.com",
        "scheduler.amazonaws.com",
        "events.amazonaws.com",
        "build.apprunner.amazonaws.com",
        "tasks.apprunner.amazonaws.com",
        "bedrock-agentcore.amazonaws.com",
        "amplify.amazonaws.com",
        "sns.amazonaws.com",
    }
)


def _allow_statements(
    template_json: dict[str, dict[str, Any]],
) -> list[tuple[str, str, str, dict[str, Any]]]:
    found = []
    for stack_name, template in template_json.items():
        for logical, cfn_type, statement in policy_statements(template):
            if statement.get("Effect", "Allow") != "Allow":
                continue
            found.append((stack_name, logical, cfn_type, statement))
    return found


def test_no_allow_statement_uses_a_wildcard_resource_it_could_scope(
    template_json: dict[str, dict[str, Any]],
) -> None:
    offenders: list[str] = []
    for stack_name, logical, cfn_type, statement in _allow_statements(template_json):
        if cfn_type == "AWS::KMS::Key":
            # A KMS key policy is attached to exactly one key and KMS refuses a
            # key policy naming any resource other than "*".
            continue
        if "*" not in as_list(statement.get("Resource")):
            continue
        actions = [a for a in as_list(statement.get("Action")) if isinstance(a, str)]
        unexpected = sorted(set(actions) - UNSCOPABLE_ACTIONS)
        if unexpected:
            offenders.append(f"{stack_name}/{logical}: {unexpected}")
    assert not offenders, "wildcard resource on scopable actions:\n" + "\n".join(offenders)


def test_unscopable_actions_carry_their_available_condition(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Where AWS offers a condition key instead of an ARN, it must be used."""
    for stack_name, logical, cfn_type, statement in _allow_statements(template_json):
        if cfn_type == "AWS::KMS::Key":
            continue
        actions = [a for a in as_list(statement.get("Action")) if isinstance(a, str)]
        for action, condition_key in REQUIRE_CONDITION.items():
            if action not in actions:
                continue
            rendered = str(statement.get("Condition"))
            assert condition_key in rendered, f"{stack_name}/{logical} {action}"


def test_no_statement_grants_a_wildcard_action(
    template_json: dict[str, dict[str, Any]],
) -> None:
    offenders: list[str] = []
    for stack_name, logical, cfn_type, statement in _allow_statements(template_json):
        for action in as_list(statement.get("Action")):
            if not isinstance(action, str) or "*" not in action:
                continue
            if cfn_type == "AWS::KMS::Key" and action == "kms:*":
                # The account-root administration statement. Omitting it makes
                # the key unmanageable and is an AWS-documented lockout.
                continue
            offenders.append(f"{stack_name}/{logical}: {action}")
    assert not offenders, "wildcard actions:\n" + "\n".join(offenders)


def test_pass_role_is_always_narrowed_to_one_service(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """An unconditioned ``iam:PassRole`` is a privilege-escalation primitive."""
    seen = 0
    for stack_name, logical, _cfn_type, statement in _allow_statements(template_json):
        if "iam:PassRole" not in as_list(statement.get("Action")):
            continue
        seen += 1
        rendered = str(statement.get("Condition"))
        assert "iam:PassedToService" in rendered, f"{stack_name}/{logical}"
        assert "*" not in as_list(statement.get("Resource")), f"{stack_name}/{logical}"
    assert seen >= 2, "expected the textract and scheduler PassRole grants"


def test_confusable_service_trusts_are_pinned_to_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    checked = 0
    for stack_name, template in template_json.items():
        for logical, statement in trust_statements(template):
            principal = statement.get("Principal") or {}
            services = as_list(principal.get("Service")) if isinstance(principal, dict) else []
            for service in services:
                if not isinstance(service, str) or service not in CONFUSABLE_SERVICES:
                    continue
                checked += 1
                rendered = str(statement.get("Condition"))
                assert (
                    "aws:SourceAccount" in rendered or "aws:SourceArn" in rendered
                ), f"{stack_name}/{logical} trusts {service} unconditionally"
    assert checked >= 5


def test_service_principals_in_resource_policies_are_pinned_to_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The SES bucket-policy case section 757 calls an evidence-injection hole."""
    checked = 0
    for stack_name, logical, cfn_type, statement in _allow_statements(template_json):
        if cfn_type not in (
            "AWS::S3::BucketPolicy",
            "AWS::SQS::QueuePolicy",
            "AWS::SNS::TopicPolicy",
            "AWS::KMS::Key",
            "AWS::Events::EventBusPolicy",
        ):
            continue
        principal = statement.get("Principal") or {}
        services = as_list(principal.get("Service")) if isinstance(principal, dict) else []
        for service in services:
            if not isinstance(service, str) or service not in CONFUSABLE_SERVICES:
                continue
            checked += 1
            rendered = str(statement.get("Condition"))
            assert (
                "aws:SourceAccount" in rendered or "aws:SourceArn" in rendered
            ), f"{stack_name}/{logical} allows {service} unconditionally"
    assert checked >= 3


@pytest.mark.parametrize(
    ("stack_name", "role_fragment", "forbidden"),
    [
        # Invariant 4 in IAM: the Advocate drafts and cannot send.
        ("PvAgentStack", "AgentExecutionRole", "ses:"),
        # A malicious artifact cannot make the agent read another artifact.
        ("PvAgentStack", "AgentExecutionRole", "s3:"),
        # The agent cannot manufacture a domain event a consumer would treat as
        # committed state.
        ("PvAgentStack", "AgentExecutionRole", "events:PutEvents"),
        # No recursion path and no way to escape its own capability.
        ("PvAgentStack", "AgentExecutionRole", "bedrock-agentcore:"),
        # The control plane reads secrets and never writes them.
        ("PvApiStack", "InstanceRole", "secretsmanager:PutSecretValue"),
        # Evidence is append-only.
        ("PvApiStack", "InstanceRole", "s3:DeleteObject"),
        # The canon decision is that no kernel retry queue exists, so a control
        # plane that could enqueue would contradict a frozen decision.
        ("PvApiStack", "InstanceRole", "sqs:"),
        ("PvApiStack", "InstanceRole", "scheduler:"),
    ],
)
def test_role_does_not_hold_permission(
    template_json: dict[str, dict[str, Any]],
    stack_name: str,
    role_fragment: str,
    forbidden: str,
) -> None:
    template = template_json[stack_name]
    for logical, cfn_type, statement in policy_statements(template):
        if role_fragment not in logical or cfn_type == "AWS::KMS::Key":
            continue
        if statement.get("Effect", "Allow") != "Allow":
            continue
        for action in as_list(statement.get("Action")):
            assert not (
                isinstance(action, str) and action.startswith(forbidden)
            ), f"{stack_name}/{logical} grants {action}"


def test_action_execute_worker_cannot_reach_ses(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The single most important absence in the pack.

    Invariant 4 says no uncommitted proposal produces an external side effect.
    The executor's inability to reach SES means the *only* code path to
    ``SendEmail`` runs after the control plane's revalidation query returns a
    row.
    """
    template = template_json["PvComputeStack"]
    for logical, _cfn_type, statement in policy_statements(template):
        if "ActionExecute" not in logical:
            continue
        for action in as_list(statement.get("Action")):
            assert not (isinstance(action, str) and action.startswith("ses:")), logical


def test_every_worker_role_is_explicit_rather_than_the_managed_basic_policy(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``AWSLambdaBasicExecutionRole`` grants logs on every group in the account.

    The eight compute workers build their roles by hand so each one can write
    only its own log group. The Cognito trigger in ``PvIdentityStack`` is the
    documented exception and is not asserted here.
    """
    roles = resources_of(template_json["PvComputeStack"], "AWS::IAM::Role")
    assert roles, "expected explicit worker roles"
    for logical, body in roles.items():
        managed = str(body.get("Properties", {}).get("ManagedPolicyArns", []))
        assert "AWSLambdaBasicExecutionRole" not in managed, logical
