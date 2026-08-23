"""``PvFoundationStack``: KMS, log groups, budgets, the billing alarm."""

from __future__ import annotations

from typing import Any

from aws_cdk.assertions import Match, Template
from provenance_infra import config as cfg
from pv_cdk_testing import flatten, policy_statements, resources_of


def test_the_artifact_key_rotates_and_is_retained(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Deleting the CMK makes every object in the artifact bucket unreadable.

    ``RETAIN`` until ``PV_TEARDOWN=1`` is what stops an accidental
    ``cdk destroy`` in week two from doing that.
    """
    keys = resources_of(template_json["PvFoundationStack"], "AWS::KMS::Key")
    assert len(keys) == 1
    (logical, body) = next(iter(keys.items()))
    assert body["Properties"]["EnableKeyRotation"] is True
    assert body["DeletionPolicy"] == "Retain"
    assert body["UpdateReplacePolicy"] == "Retain"
    assert body["Properties"]["PendingWindowInDays"] == 7


def test_the_key_alias_is_the_documented_name(
    template_json: dict[str, dict[str, Any]],
) -> None:
    aliases = resources_of(template_json["PvFoundationStack"], "AWS::KMS::Alias")
    assert {b["Properties"]["AliasName"] for b in aliases.values()} == {cfg.ARTIFACT_KEY_ALIAS}


def test_ses_may_encrypt_under_the_key_only_for_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Without this grant, the receipt rule fails with ``AccessDenied`` and the
    failure surfaces as silently missing mail.

    Without the ``aws:SourceAccount`` condition it would be a grant to every AWS
    customer's SES.
    """
    found = False
    for _logical, cfn_type, statement in policy_statements(template_json["PvFoundationStack"]):
        if cfn_type != "AWS::KMS::Key":
            continue
        principal = statement.get("Principal") or {}
        if principal.get("Service") != "ses.amazonaws.com":
            continue
        found = True
        assert set(statement["Action"]) == {"kms:GenerateDataKey", "kms:Encrypt"}
        assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == {
            "Ref": "AWS::AccountId"
        }
    assert found, "the SES key grant is missing"


def test_log_groups_exist_with_thirty_day_retention(
    template_json: dict[str, dict[str, Any]],
) -> None:
    groups = resources_of(template_json["PvFoundationStack"], "AWS::Logs::LogGroup")
    by_name = {b["Properties"]["LogGroupName"]: b["Properties"] for b in groups.values()}
    assert cfg.LOG_GROUP_CONTROL_PLANE in by_name
    assert cfg.LOG_GROUP_AGENT_RUNTIME in by_name
    for props in by_name.values():
        assert props["RetentionInDays"] == 30


def test_domain_events_log_group_is_not_in_the_foundation(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 6.3 creates it inline in the messaging stack, and that is the
    right home: CDK's ``CloudWatchLogGroup`` event target provisions a custom
    resource in the log group's stack to write its resource policy.
    """
    foundation = resources_of(template_json["PvFoundationStack"], "AWS::Logs::LogGroup")
    messaging = resources_of(template_json["PvMessagingStack"], "AWS::Logs::LogGroup")
    assert cfg.LOG_GROUP_DOMAIN_EVENTS not in {
        b["Properties"]["LogGroupName"] for b in foundation.values()
    }
    assert cfg.LOG_GROUP_DOMAIN_EVENTS in {
        b["Properties"]["LogGroupName"] for b in messaging.values()
    }


def test_two_budgets_with_the_documented_limits_and_filters(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """One project-wide, one for Bedrock alone.

    Bedrock is the only line item that can move fast, which is why it is not
    folded into the monthly budget: a retry storm would be invisible until the
    combined number moved.
    """
    budgets = resources_of(template_json["PvFoundationStack"], "AWS::Budgets::Budget")
    by_name = {b["Properties"]["Budget"]["BudgetName"]: b["Properties"] for b in budgets.values()}
    assert set(by_name) == {"provenance-monthly", "provenance-bedrock-monthly"}

    monthly = by_name["provenance-monthly"]["Budget"]
    assert monthly["BudgetLimit"] == {"Amount": 150, "Unit": "USD"}
    assert monthly["CostFilters"] == {"TagKeyValue": ["user:Project$Provenance"]}

    bedrock = by_name["provenance-bedrock-monthly"]["Budget"]
    assert bedrock["BudgetLimit"] == {"Amount": 60, "Unit": "USD"}
    assert bedrock["CostFilters"] == {"Service": ["Amazon Bedrock"]}


def test_budget_subscribers_never_hardcode_an_email_address(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The owner's address is context, not a literal.

    The test suite supplies no ``pv:owner_email``, so the only subscriber must be
    the SNS topic. An email address baked into a committed template is personal
    data in a template.
    """
    budgets = resources_of(template_json["PvFoundationStack"], "AWS::Budgets::Budget")
    for body in budgets.values():
        for notification in body["Properties"]["NotificationsWithSubscribers"]:
            types = {sub["SubscriptionType"] for sub in notification["Subscribers"]}
            assert types == {"SNS"}
        assert "@" not in flatten(body["Properties"]["NotificationsWithSubscribers"])


def test_the_billing_alarm_exists_and_does_not_page_on_missing_data(
    templates: dict[str, Template],
) -> None:
    templates["PvFoundationStack"].has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "provenance-estimated-charges",
            "Namespace": "AWS/Billing",
            "MetricName": "EstimatedCharges",
            "Threshold": 120,
            "ComparisonOperator": "GreaterThanThreshold",
            "TreatMissingData": "notBreaching",
            "AlarmActions": Match.any_value(),
        },
    )
