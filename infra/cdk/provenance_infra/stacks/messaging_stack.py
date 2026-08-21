"""Stack 4 -- ``PvMessagingStack``.

One custom bus, two work queues, five dead-letter queues, two Scheduler groups,
the Scheduler invocation role, and the three EventBridge rules whose targets are
not Lambda functions (40_INFRA_IAC.md section 6).

**Deviation from section 2.1, reported not hidden.** Section 2.1 places all
"five rules" in this stack. Two of the five -- ``provenance-notification-rule``
and ``provenance-trigger-schedule-rule`` -- target Lambda functions that live in
``PvComputeStack``, and ``PvComputeStack`` already depends on this stack for the
two SQS event sources. Putting those rules here would make the two stacks
mutually dependent, which CloudFormation rejects. They are therefore created in
``PvComputeStack`` beside their targets, with the bus passed in. All five rules
still exist under their canonical names; only the stack boundary moves.
(D-13-003.)
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra import workers as wk
from provenance_infra.config import PvConfig
from provenance_infra.props import FoundationExports, MessagingExports
from provenance_infra.removal import stateful_removal

# specs/15_API_SPEC.md section 11.2. A drift here silently stops a consumer.
ADVOCATE_DETAIL_TYPES = [
    "case.reopened.v1",
    "conflict.detected.v1",
    "commitment.overdue.v1",
    "trigger.fired.v1",
]
NOTIFICATION_DETAIL_TYPES = [
    "case.reopened.v1",
    "case.state_changed.v1",
    "conflict.detected.v1",
    "commitment.overdue.v1",
    "commitment.fulfilled.v1",
    "trigger.fired.v1",
    "action.proposed.v1",
    "action.executed.v1",
    "action.failed.v1",
]
TRIGGER_SCHEDULE_DETAIL_TYPES = ["trigger.armed.v1", "trigger.noop.v1", "trigger.fired.v1"]
EVENT_SOURCE = "provenance.control-plane"


class PvMessagingStack(Stack):
    """Bus, queues, DLQs, Scheduler groups, and the Scheduler invoke role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        foundation: FoundationExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # One bus. Domain events only. The default bus is not used, so an AWS
        # service event can never match a rule written for a Provenance event,
        # and ``source`` is a second filter on every rule rather than the only
        # one.
        self.bus = events.EventBus(self, "DomainBus", event_bus_name=cfg.DOMAIN_BUS_NAME)

        self.worker_dlq = self._dlq("WorkerDlq", cfg.WORKER_DLQ_NAME)
        self.scheduler_dlq = self._dlq("SchedulerDlq", cfg.SCHEDULER_DLQ_NAME)
        self.advocate_dlq = self._dlq("AdvocateDlq", cfg.ADVOCATE_DLQ_NAME)
        self.action_dlq = self._dlq("ActionDlq", cfg.ACTION_DLQ_NAME)
        self.notification_dlq = self._dlq("NotificationDlq", cfg.NOTIFICATION_DLQ_NAME)

        self.advocate_queue = sqs.Queue(
            self,
            "AdvocateQueue",
            queue_name=cfg.ADVOCATE_QUEUE_NAME,
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            enforce_ssl=True,
            # 6x the consumer timeout. advocate_dispatch runs a LangGraph
            # invocation behind the control plane.
            visibility_timeout=Duration.seconds(180),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(queue=self.advocate_dlq, max_receive_count=3),
        )
        self.action_queue = sqs.Queue(
            self,
            "ActionQueue",
            queue_name=cfg.ACTION_QUEUE_NAME,
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            enforce_ssl=True,
            visibility_timeout=Duration.seconds(180),
            retention_period=Duration.days(4),
            # 2, not 3. A queued outbound message is the one place where
            # automatic replay could send a letter the user no longer wants.
            dead_letter_queue=sqs.DeadLetterQueue(queue=self.action_dlq, max_receive_count=2),
        )

        # Only its own source queue may target a DLQ, so a future mis-wired
        # queue cannot dump unrelated messages into a DLQ an alarm is watching.
        #
        # The source ARN is **built from the canonical queue name** rather than
        # read off the construct. ``queue.queue_arn`` renders as a GetAtt, which
        # would make the DLQ depend on the queue while the queue's RedrivePolicy
        # already depends on the DLQ -- an intra-stack resource cycle that
        # ``cdk synth`` emits happily and CloudFormation then refuses to deploy.
        # ``Template.from_stack`` is what caught it. Queue names are contract
        # values, so the string form is exact rather than approximate.
        for dlq, source_name in (
            (self.advocate_dlq, cfg.ADVOCATE_QUEUE_NAME),
            (self.action_dlq, cfg.ACTION_QUEUE_NAME),
        ):
            child = dlq.node.default_child
            assert isinstance(child, sqs.CfnQueue)
            child.redrive_allow_policy = {
                "redrivePermission": "byQueue",
                "sourceQueueArns": [
                    f"arn:{self.partition}:sqs:{cfg.REGION}:{self.account}:{source_name}"
                ],
            }

        self._preauthorise_cross_stack_rule_dlqs()
        self._scheduler(foundation)
        self._rules()

    # ----------------------------------------------------------------------
    def _preauthorise_cross_stack_rule_dlqs(self) -> None:
        """Let EventBridge rules on **our** bus write to the two DLQs they use.

        The two Lambda-targeted rules live in ``PvComputeStack`` (D-13-003).
        CDK's default behaviour is to write a queue policy naming the exact rule
        ARN, but that policy lands in the queue's stack -- here -- and a
        reference from this stack to a rule in ``PvComputeStack`` closes a cycle,
        because ``PvComputeStack`` already imports these queues.

        The statement below is the same grant with a rule-ARN *pattern* instead
        of a single rule ARN: ``events.amazonaws.com``, this account, and only
        rules attached to ``provenance-domain-bus``. It is one step broader than
        naming one rule and far narrower than granting the service. Stated
        rather than buried, because a queue policy is a security boundary.
        """
        pattern = (
            f"arn:{self.partition}:events:{cfg.REGION}:{self.account}:"
            f"rule/{cfg.DOMAIN_BUS_NAME}/*"
        )
        for queue in (self.worker_dlq, self.notification_dlq):
            queue.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="AllowDomainBusRulesToDeadLetterHere",
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ServicePrincipal("events.amazonaws.com")],
                    actions=["sqs:SendMessage"],
                    resources=[queue.queue_arn],
                    conditions={
                        "StringEquals": {"aws:SourceAccount": self.account},
                        "ArnLike": {"aws:SourceArn": pattern},
                    },
                )
            )

    # ----------------------------------------------------------------------
    def _dlq(self, construct_id: str, queue_name: str) -> sqs.Queue:
        return sqs.Queue(
            self,
            construct_id,
            queue_name=queue_name,
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )

    # ----------------------------------------------------------------------
    def _scheduler(self, foundation: FoundationExports) -> None:
        """Two groups, so thousands of one-time trigger schedules never mix with
        the handful of system schedules and a teardown ``delete-schedule-group``
        cannot take out a system sweep by accident.
        """
        del foundation
        scheduler.CfnScheduleGroup(self, "TriggerScheduleGroup", name=cfg.TRIGGER_SCHEDULE_GROUP)
        scheduler.CfnScheduleGroup(self, "SystemScheduleGroup", name=cfg.SYSTEM_SCHEDULE_GROUP)

        # ``aws:SourceAccount`` on the service-principal trust is what stops a
        # confused-deputy attack: without it, another account's Scheduler can
        # ask STS to assume this role (40_INFRA_IAC.md section 757).
        self.scheduler_invoke_role = iam.Role(
            self,
            "SchedulerInvokeRole",
            role_name="provenance-scheduler-invoke-role",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Assumed by EventBridge Scheduler to invoke exactly two functions",
        )
        # Named by ARN string rather than by construct reference. The two
        # functions live in PvComputeStack, which depends on this stack; a
        # construct reference would close a cycle. Function *names* are canon,
        # so the ARN is derivable without an import.
        invokable = []
        for spec in (wk.TRIGGER_WAKEUP, wk.OUTBOX_DISPATCH):
            base = (
                f"arn:{self.partition}:lambda:{cfg.REGION}:{self.account}:"
                f"function:{spec.function_name}"
            )
            invokable.extend([base, f"{base}:*"])
        self.scheduler_invoke_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeTheTwoScheduledWorkers",
                actions=["lambda:InvokeFunction"],
                resources=invokable,
            )
        )
        self.scheduler_invoke_role.add_to_policy(
            iam.PolicyStatement(
                sid="SchedulerDlq",
                actions=["sqs:SendMessage"],
                resources=[self.scheduler_dlq.queue_arn],
            )
        )

    # ----------------------------------------------------------------------
    def _rules(self) -> None:
        # 1. Advocate. The attention_level filter keeps trivial state changes
        #    from waking an LLM.
        #
        #    specs/15_API_SPEC.md section 17.8 flags this filter as fragile:
        #    every detail-type listed here must actually carry attention_level in
        #    its payload, because EventBridge silently does not match when a
        #    required field is absent. The mechanical controls are the spec_lint
        #    check and the contract test named in section 6.3; they are not
        #    replaceable by care.
        events.Rule(
            self,
            "AdvocateRule",
            rule_name="provenance-advocate-rule",
            event_bus=self.bus,
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE],
                detail_type=ADVOCATE_DETAIL_TYPES,
                detail={
                    "schema_version": ["1.0"],
                    "payload": {"attention_level": [{"anything-but": ["NONE"]}]},
                },
            ),
            targets=[targets.SqsQueue(self.advocate_queue, dead_letter_queue=self.worker_dlq)],
        )

        # 2. Action executor. ONLY action.approved.v1 reaches it. There is no
        #    rule that routes action.proposed.v1 here, and adding one would
        #    break invariant 4.
        events.Rule(
            self,
            "ActionExecuteRule",
            rule_name="provenance-action-execute-rule",
            event_bus=self.bus,
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE], detail_type=["action.approved.v1"]
            ),
            targets=[targets.SqsQueue(self.action_queue, dead_letter_queue=self.worker_dlq)],
        )

        # 3. Telemetry: everything, to a log group. Never a second source of truth.
        self.domain_event_log_group = logs.LogGroup(
            self,
            "DomainEventLog",
            log_group_name=cfg.LOG_GROUP_DOMAIN_EVENTS,
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=stateful_removal(self.config),
        )
        events.Rule(
            self,
            "TelemetryRule",
            rule_name="provenance-telemetry-rule",
            event_bus=self.bus,
            event_pattern=events.EventPattern(source=[EVENT_SOURCE]),
            targets=[targets.CloudWatchLogGroup(self.domain_event_log_group)],
        )

    # ----------------------------------------------------------------------
    @property
    def exports(self) -> MessagingExports:
        return MessagingExports(
            bus=self.bus,
            advocate_queue=self.advocate_queue,
            action_queue=self.action_queue,
            worker_dlq=self.worker_dlq,
            scheduler_dlq=self.scheduler_dlq,
            advocate_dlq=self.advocate_dlq,
            action_dlq=self.action_dlq,
            notification_dlq=self.notification_dlq,
            trigger_schedule_group_name=cfg.TRIGGER_SCHEDULE_GROUP,
            system_schedule_group_name=cfg.SYSTEM_SCHEDULE_GROUP,
            scheduler_invoke_role=self.scheduler_invoke_role,
        )
