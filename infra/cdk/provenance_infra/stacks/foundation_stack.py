"""Stack 1 -- ``PvFoundationStack``.

KMS CMK, the three long-lived CloudWatch log groups, the alert topic, AWS
Budgets, and the billing alarm. Nothing depends on anything, so it deploys
first (40_INFRA_IAC.md section 2.4 step 1).

Stateful: the KMS key. Deleting it would make every object in the artifact
bucket unreadable, which is why it carries ``RETAIN`` until teardown.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import FoundationExports
from provenance_infra.removal import stateful_removal


class PvFoundationStack(Stack):
    """KMS, log groups, budgets, and the billing alarm."""

    def __init__(self, scope: Construct, construct_id: str, *, config: PvConfig, **kwargs: Any):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # ------------------------------------------------------------------
        # Alert topic. Budget notifications, the billing alarm, and every
        # PvObservabilityStack alarm publish here.
        # ------------------------------------------------------------------
        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=cfg.ALERT_TOPIC_NAME,
            display_name="Provenance alerts",
            enforce_ssl=True,
        )

        # ------------------------------------------------------------------
        # The customer-managed key for every artifact object and both ECR
        # repositories.
        # ------------------------------------------------------------------
        self.artifact_key = kms.Key(
            self,
            "ArtifactKey",
            alias=cfg.ARTIFACT_KEY_ALIAS,
            description="Provenance artifact bytes, parser output, and container images",
            enable_key_rotation=True,
            removal_policy=stateful_removal(config),
            # A key in PendingDeletion does not bill. Seven days is the minimum
            # and is long enough to notice an accidental teardown.
            pending_window=Duration.days(7),
        )

        # SES writes the raw inbound MIME object itself, so it needs to be able
        # to generate a data key under our CMK. Without this the receipt rule
        # fails with AccessDenied and the failure surfaces as silently missing
        # mail (40_INFRA_IAC.md section 4.5).
        #
        # ``resources: ['*']`` is unavoidable and correct in a KMS *key* policy:
        # the policy is already attached to exactly one key, and KMS rejects a
        # key policy that names a resource other than ``*``. The scoping that
        # matters is the ``aws:SourceAccount`` condition, which is what stops
        # any other AWS customer's SES receipt rule from encrypting into this
        # key (40_INFRA_IAC.md section 757).
        self.artifact_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSesEncryptForThisAccountOnly",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["kms:GenerateDataKey", "kms:Encrypt"],
                resources=["*"],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            )
        )

        # ------------------------------------------------------------------
        # Log groups. Created here rather than implicitly by each service so
        # retention is a decision and teardown has a list to delete.
        # ------------------------------------------------------------------
        self.control_plane_log_group = logs.LogGroup(
            self,
            "ControlPlaneLogGroup",
            log_group_name=cfg.LOG_GROUP_CONTROL_PLANE,
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=stateful_removal(config),
        )
        self.agent_runtime_log_group = logs.LogGroup(
            self,
            "AgentRuntimeLogGroup",
            log_group_name=cfg.LOG_GROUP_AGENT_RUNTIME,
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=stateful_removal(config),
        )
        # /provenance/domain-events is NOT created here. 40_INFRA_IAC.md
        # section 6.3 creates it inline in the messaging stack, and that is the
        # right home: CDK's CloudWatchLogGroup event target provisions a custom
        # resource to write the log group's resource policy, and it provisions it
        # in the log group's stack. Keeping the group beside the rule keeps that
        # machinery out of the foundation.

        self._budgets()
        self._billing_alarm()

    # ----------------------------------------------------------------------
    def _subscribers(self) -> list[budgets.CfnBudget.SubscriberProperty]:
        """SNS always; email only when an address was supplied as context.

        The owner's email address is not committed. ``PV_OWNER_EMAIL`` or the
        ``pv:owner_email`` context key supplies it at synth time; without one the
        budget still notifies through the topic.
        """
        subs: list[budgets.CfnBudget.SubscriberProperty] = [
            budgets.CfnBudget.SubscriberProperty(
                subscription_type="SNS", address=self.alert_topic.topic_arn
            )
        ]
        if self.config.owner_email:
            subs.append(
                budgets.CfnBudget.SubscriberProperty(
                    subscription_type="EMAIL", address=self.config.owner_email
                )
            )
        return subs

    def _notification(
        self, threshold: int, notification_type: str
    ) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
        return budgets.CfnBudget.NotificationWithSubscribersProperty(
            notification=budgets.CfnBudget.NotificationProperty(
                notification_type=notification_type,
                comparison_operator="GREATER_THAN",
                threshold=threshold,
                threshold_type="PERCENTAGE",
            ),
            subscribers=self._subscribers(),
        )

    def _budgets(self) -> None:
        """Two budgets. Budgets alert; they do not stop anything.

        The tag filter is what makes the monthly budget mean "this project"
        rather than "this account", and it is the same tag the teardown
        verification in section 14.5 greps for.
        """
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="provenance-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=self.config.monthly_budget_usd, unit="USD"
                ),
                cost_filters={"TagKeyValue": ["user:Project$Provenance"]},
            ),
            notifications_with_subscribers=[
                self._notification(50, "ACTUAL"),
                self._notification(80, "ACTUAL"),
                self._notification(100, "ACTUAL"),
                self._notification(100, "FORECASTED"),
            ],
        )

        # Bedrock is the only line item that can move fast: a retry storm
        # against Tier R is the realistic way this account produces a surprising
        # bill, so it gets its own tighter budget.
        budgets.CfnBudget(
            self,
            "BedrockBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="provenance-bedrock-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=self.config.bedrock_budget_usd, unit="USD"
                ),
                cost_filters={"Service": ["Amazon Bedrock"]},
            ),
            notifications_with_subscribers=[
                self._notification(50, "ACTUAL"),
                self._notification(80, "ACTUAL"),
                self._notification(100, "FORECASTED"),
            ],
        )

    def _billing_alarm(self) -> None:
        """``AWS/Billing EstimatedCharges`` only publishes in us-east-1.

        Which is where this stack lives anyway, so the alarm is co-located with
        the metric rather than needing a cross-region reference.
        """
        alarm = cloudwatch.Alarm(
            self,
            "EstimatedChargesAlarm",
            alarm_name="provenance-estimated-charges",
            alarm_description=(
                "P2 - estimated account charges crossed the review threshold. "
                "Budgets alert on a percentage of a forecast; this alarms on the "
                "number itself."
            ),
            metric=cloudwatch.Metric(
                namespace="AWS/Billing",
                metric_name="EstimatedCharges",
                dimensions_map={"Currency": "USD"},
                statistic="Maximum",
                period=Duration.hours(6),
            ),
            threshold=self.config.estimated_charges_threshold_usd,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
        alarm.add_ok_action(cw_actions.SnsAction(self.alert_topic))

    # ----------------------------------------------------------------------
    @property
    def exports(self) -> FoundationExports:
        return FoundationExports(
            artifact_key=self.artifact_key,
            alert_topic=self.alert_topic,
            control_plane_log_group=self.control_plane_log_group,
            agent_runtime_log_group=self.agent_runtime_log_group,
        )
