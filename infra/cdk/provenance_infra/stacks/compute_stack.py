"""Stack 5 -- ``PvComputeStack``.

Eight of the nine Lambda workers, their roles and inline policies, the SNS topic
``provenance-textract-status``, the SQS event source mappings, the async
on-failure destinations, the two Lambda-targeted EventBridge rules, and the
system outbox sweep schedule (40_INFRA_IAC.md sections 6.3, 6.4, 7).

The ninth function, ``provenance-cognito-post-confirmation``, is built in
``PvIdentityStack`` -- see ``props.ComputeExports``.

Nothing in this stack is stateful, which is the point of the split: it is one of
the two stacks most likely to be redeployed during the build.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as destinations
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra import workers as wk
from provenance_infra.config import PvConfig
from provenance_infra.props import (
    ComputeExports,
    DataExports,
    FoundationExports,
    IdentityExports,
    MessagingExports,
)
from provenance_infra.removal import stateful_removal
from provenance_infra.stacks.messaging_stack import (
    EVENT_SOURCE,
    NOTIFICATION_DETAIL_TYPES,
    TRIGGER_SCHEDULE_DETAIL_TYPES,
)

MAX_ARTIFACT_BYTES = str(20 * 1024 * 1024)


class PvComputeStack(Stack):
    """The Lambda workers and everything that invokes them."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        foundation: FoundationExports,
        identity: IdentityExports,
        data: DataExports,
        messaging: MessagingExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.foundation = foundation
        self.identity = identity
        self.data = data
        self.messaging = messaging

        self.textract_topic = sns.Topic(
            self,
            "TextractStatusTopic",
            topic_name=cfg.TEXTRACT_TOPIC_NAME,
            display_name="Textract asynchronous job completion",
            enforce_ssl=True,
        )

        self.ses_ingest = self._ses_ingest()
        self.textract_complete = self._textract_complete()
        self.outbox_dispatch = self._outbox_dispatch()
        self.trigger_wakeup = self._trigger_wakeup()
        self.advocate_dispatch = self._advocate_dispatch()
        self.action_execute = self._action_execute()
        self.notification_dispatch = self._notification_dispatch()
        self.trigger_schedule_manager = self._trigger_schedule_manager()

        self._textract_publish_role()
        self._lambda_rules()
        self._outbox_sweep_schedule()

    # ------------------------------------------------------------------
    # shared construction
    # ------------------------------------------------------------------
    def _common_environment(self) -> dict[str, str]:
        """Section 7.1. Non-secret values only.

        Every credential the workers need is fetched at runtime from Secrets
        Manager by ARN; ``G13.6`` greps environment *variables* for anything that
        looks like material, and this map is what it greps.
        """
        return {
            "APP_ENV": "prod",
            "AWS_REGION_NAME": cfg.REGION,
            "APP_BASE_URL": self.config.api_base_url,
            "COGNITO_TOKEN_ENDPOINT": self.config.cognito_token_endpoint,
            "COGNITO_WORKER_CLIENT_ID": self.identity.worker_client.user_pool_client_id,
            "COGNITO_WORKER_CLIENT_SECRET_ARN": self.data.cognito_secret.secret_arn,
            "POWERTOOLS_SERVICE_NAME": "provenance",
            "POWERTOOLS_LOG_LEVEL": "INFO",
            "LOG_LEVEL": "INFO",
        }

    def _log_group(self, spec: wk.WorkerSpec) -> logs.LogGroup:
        return logs.LogGroup(
            self,
            f"{spec.construct_id}LogGroup",
            log_group_name=f"/provenance/{spec.module.replace('_', '-')}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=stateful_removal(self.config),
        )

    def _role(self, spec: wk.WorkerSpec, log_group: logs.ILogGroup) -> iam.Role:
        """One role per function, built explicitly rather than by the managed
        ``AWSLambdaBasicExecutionRole``.

        The managed policy grants ``logs:*`` on every log group in the account
        and region. Naming the function's own log group instead costs three
        lines and means a compromised worker cannot read or write another
        component's logs.
        """
        role = iam.Role(
            self,
            f"{spec.construct_id}Role",
            role_name=f"{spec.function_name}-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=spec.reason,
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOwnLogsOnly",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[log_group.log_group_arn, f"{log_group.log_group_arn}:log-stream:*"],
            )
        )
        return role

    def _function(
        self,
        spec: wk.WorkerSpec,
        *,
        environment: dict[str, str] | None = None,
        on_failure: sqs.IQueue | None = None,
        retry_attempts: int = 2,
        max_event_age: Duration | None = None,
    ) -> lambda_.Function:
        log_group = self._log_group(spec)
        role = self._role(spec, log_group)
        env = self._common_environment()
        env["OTEL_SERVICE_NAME"] = spec.function_name
        if environment:
            env.update(environment)

        fn = lambda_.Function(
            self,
            spec.construct_id,
            function_name=spec.function_name,
            runtime=wk.RUNTIME,
            architecture=wk.ARCHITECTURE,
            handler=wk.HANDLER,
            code=wk.resolve_code(spec),
            memory_size=spec.memory_mb,
            timeout=spec.timeout,
            reserved_concurrent_executions=spec.reserved_concurrency,
            role=role,
            # X-Ray, joined to the OTEL trace_id.
            tracing=lambda_.Tracing.ACTIVE,
            logging_format=lambda_.LoggingFormat.JSON,
            application_log_level_v2=lambda_.ApplicationLogLevel.INFO,
            system_log_level_v2=lambda_.SystemLogLevel.WARN,
            log_group=log_group,
            environment=env,
            description=spec.reason,
            retry_attempts=retry_attempts,
            max_event_age=max_event_age,
            on_failure=destinations.SqsDestination(on_failure) if on_failure else None,
        )
        # Every worker mints an M2M token; none of them holds a SQL credential.
        self.data.cognito_secret.grant_read(fn)
        return fn

    # ------------------------------------------------------------------
    # the eight workers
    # ------------------------------------------------------------------
    def _ses_ingest(self) -> lambda_.Function:
        spec = wk.SES_INGEST
        fn = self._function(
            spec,
            environment={
                "S3_INBOUND_BUCKET": self.data.inbound_bucket.bucket_name,
                "S3_ARTIFACT_BUCKET": self.data.artifact_bucket.bucket_name,
                "S3_KMS_KEY_ARN": self.foundation.artifact_key.key_arn,
                "SES_INGEST_DOMAIN": self.config.ingest_host,
                "INGEST_ALIAS_HMAC_KEY_ARN": self.data.crypto_secret.secret_arn,
                "MAX_ARTIFACT_BYTES": MAX_ARTIFACT_BYTES,
            },
            on_failure=self.messaging.worker_dlq,
            retry_attempts=2,
            max_event_age=Duration.hours(2),
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadStagedInboundObject",
                actions=["s3:GetObject"],
                resources=[self.data.inbound_bucket.arn_for_objects("ses/incoming/*")],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="WriteCanonicalInboundCopy",
                actions=["s3:PutObject"],
                resources=[self.data.artifact_bucket.arn_for_objects("ses/*")],
                conditions={"StringEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}},
            )
        )
        self._grant_artifact_key_via_s3(fn)
        # Section 15.6, stated rather than buried: inbound mail has no preceding
        # control-plane request, so this worker computes its own capability
        # proof over ("INGEST_ALIAS", alias_hash, expires_at). That widens the
        # blast radius of provenance/crypto, and the open question of whether the
        # ingest path deserves its own narrower HMAC key is recorded upstream.
        self.data.crypto_secret.grant_read(fn)
        #
        # What this function deliberately cannot do: read any object outside
        # ses/incoming/*, write anywhere outside ses/* in the artifact bucket,
        # delete anything, publish to EventBridge, send email, invoke AgentCore,
        # or reach CockroachDB.
        return fn

    def _textract_complete(self) -> lambda_.Function:
        spec = wk.TEXTRACT_COMPLETE
        fn = self._function(
            spec,
            environment={
                "S3_ARTIFACT_BUCKET": self.data.artifact_bucket.bucket_name,
                "S3_KMS_KEY_ARN": self.foundation.artifact_key.key_arn,
            },
            on_failure=self.messaging.worker_dlq,
            retry_attempts=2,
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadTextractResults",
                actions=["textract:GetDocumentAnalysis", "textract:GetDocumentTextDetection"],
                # NOT scopable: Textract job ids are not ARNs and the API
                # supports no resource-level permission. The compensating
                # control is that this function can only be invoked by its SNS
                # topic, only learns job ids from Textract itself, and the
                # JobTag's tenant/user/artifact triple is re-validated
                # server-side by the control plane.
                resources=["*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="WriteNormalizedParserOutput",
                actions=["s3:PutObject"],
                resources=[self.data.artifact_bucket.arn_for_objects("normalized/*")],
                conditions={"StringEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}},
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadRawArtifactForReparse",
                actions=["s3:GetObject"],
                resources=[self.data.artifact_bucket.arn_for_objects("raw/*")],
            )
        )
        self._grant_artifact_key_via_s3(fn)
        self.textract_topic.add_subscription(sns_subs.LambdaSubscription(fn))
        return fn

    def _outbox_dispatch(self) -> lambda_.Function:
        # No events:PutEvents. This function never publishes; the control plane
        # does. That absence is the check that the dispatcher state machine has
        # not leaked out of its owner.
        return self._function(
            wk.OUTBOX_DISPATCH,
            environment={"OUTBOX_SWEEP_BATCH_SIZE": "50"},
            on_failure=self.messaging.scheduler_dlq,
            # 0: the next schedule is the retry, and the claim/lease state
            # machine already handles a dispatcher that died mid-publish.
            retry_attempts=0,
        )

    def _trigger_wakeup(self) -> lambda_.Function:
        fn = self._function(
            wk.TRIGGER_WAKEUP,
            on_failure=self.messaging.scheduler_dlq,
            # Retry accounting lives in exactly one place: the Scheduler target
            # (MaximumRetryAttempts 3, MaximumEventAgeInSeconds 3600).
            retry_attempts=0,
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="SchedulerDlq",
                actions=["sqs:SendMessage"],
                resources=[self.messaging.scheduler_dlq.queue_arn],
            )
        )
        # Notably absent: scheduler:*. trigger_schedule_manager owns schedule
        # lifecycle, so a compromised wakeup path cannot arm a new schedule.
        return fn

    def _advocate_dispatch(self) -> lambda_.Function:
        fn = self._function(wk.ADVOCATE_DISPATCH, retry_attempts=0)
        fn.add_event_source(
            event_sources.SqsEventSource(
                self.messaging.advocate_queue, batch_size=1, report_batch_item_failures=True
            )
        )
        # No bedrock:InvokeModel. It calls the control plane, which starts the
        # agent run, so the queue consumer never holds an agent capability.
        return fn

    def _action_execute(self) -> lambda_.Function:
        fn = self._function(wk.ACTION_EXECUTE, retry_attempts=0)
        fn.add_event_source(
            event_sources.SqsEventSource(
                self.messaging.action_queue, batch_size=1, report_batch_item_failures=True
            )
        )
        # No ses:SendEmail. Invariant 4 in IAM: the executor physically cannot
        # reach SES, so the only code path to a send runs after the control
        # plane re-validates case revision and draft hash inside a transaction.
        return fn

    def _notification_dispatch(self) -> lambda_.Function:
        fn = self._function(
            wk.NOTIFICATION_DISPATCH,
            environment={
                "SES_NOTIFICATION_FROM_ADDRESS": self.config.ses_notification_from_address,
                "SES_CONFIGURATION_SET": cfg.SES_CONFIGURATION_SET,
                "WEB_BASE_URL": self.config.web_base_url,
            },
            on_failure=self.messaging.notification_dlq,
            retry_attempts=2,
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="SendUserNotificationsOnly",
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=[
                    f"arn:{self.partition}:ses:{cfg.REGION}:{self.account}:"
                    f"identity/{self.config.root_domain}"
                ],
                conditions={
                    "StringEquals": {
                        # Pinned to the notification sender. This principal can
                        # never send an ActionIntent: that address is
                        # disputes@, and only the control plane holds it.
                        "ses:FromAddress": self.config.ses_notification_from_address,
                        "ses:ConfigurationSetName": cfg.SES_CONFIGURATION_SET,
                    }
                },
            )
        )
        return fn

    def _trigger_schedule_manager(self) -> lambda_.Function:
        fn = self._function(
            wk.TRIGGER_SCHEDULE_MANAGER,
            environment={
                "EVENTBRIDGE_SCHEDULER_GROUP": self.messaging.trigger_schedule_group_name,
                "SCHEDULER_TARGET_LAMBDA_ARN": self.trigger_wakeup.function_arn,
                "SCHEDULER_ROLE_ARN": self.messaging.scheduler_invoke_role.role_arn,
                "SCHEDULER_DLQ_ARN": self.messaging.scheduler_dlq.queue_arn,
            },
            on_failure=self.messaging.worker_dlq,
            retry_attempts=2,
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ManageOneTimeTriggerSchedulesOnly",
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                    "scheduler:UpdateSchedule",
                ],
                resources=[
                    f"arn:{self.partition}:scheduler:{cfg.REGION}:{self.account}:"
                    f"schedule/{self.messaging.trigger_schedule_group_name}/*"
                ],
            )
        )
        # The one PassRole grant in the whole account, scoped two ways: to the
        # single role, and to the single service that may receive it.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassSchedulerInvokeRoleOnly",
                actions=["iam:PassRole"],
                resources=[self.messaging.scheduler_invoke_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}},
            )
        )
        return fn

    # ------------------------------------------------------------------
    def _grant_artifact_key_via_s3(self, fn: lambda_.Function) -> None:
        """KMS on the artifact key, but only when S3 is the caller.

        ``kms:ViaService`` means a compromised worker cannot use the key to
        decrypt anything that is not an S3 object -- the ECR image layers and
        the Secrets Manager blobs share this CMK.
        """
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="UseArtifactKeyThroughS3Only",
                actions=["kms:Decrypt", "kms:GenerateDataKey"],
                resources=[self.foundation.artifact_key.key_arn],
                conditions={"StringEquals": {"kms:ViaService": f"s3.{cfg.REGION}.amazonaws.com"}},
            )
        )

    def _textract_publish_role(self) -> None:
        """The role Textract assumes to publish completion. Separate and minimal."""
        iam.Role(
            self,
            "TextractPublishRole",
            role_name="provenance-textract-publish-role",
            assumed_by=iam.ServicePrincipal(
                "textract.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Assumed by Textract to publish job completion to one SNS topic",
            inline_policies={
                "publish": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="PublishJobCompletion",
                            actions=["sns:Publish"],
                            resources=[self.textract_topic.topic_arn],
                        )
                    ]
                )
            },
        )

    def _lambda_rules(self) -> None:
        """The two of section 6.3's five rules whose targets are Lambda functions.

        They live here rather than in ``PvMessagingStack`` because a rule and its
        Lambda target reference each other (the rule names the function ARN; the
        function gets a ``Lambda::Permission`` naming the rule ARN), and
        ``PvComputeStack`` already depends on ``PvMessagingStack``. See this
        module's sibling for the full note. (D-13-003.)

        The two target DLQs are referenced as **imported** queues so CDK does
        not try to write a queue policy back into ``PvMessagingStack``, which
        would close the cycle from the other side. The equivalent grant is made
        explicitly by ``PvMessagingStack._preauthorise_cross_stack_rule_dlqs``.
        """
        worker_dlq = sqs.Queue.from_queue_arn(
            self, "WorkerDlqRef", self.messaging.worker_dlq.queue_arn
        )
        notification_dlq = sqs.Queue.from_queue_arn(
            self, "NotificationDlqRef", self.messaging.notification_dlq.queue_arn
        )
        events.Rule(
            self,
            "NotificationRule",
            rule_name="provenance-notification-rule",
            event_bus=self.messaging.bus,
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE], detail_type=NOTIFICATION_DETAIL_TYPES
            ),
            targets=[
                targets.LambdaFunction(
                    self.notification_dispatch,
                    dead_letter_queue=notification_dlq,
                    retry_attempts=2,
                )
            ],
        )
        events.Rule(
            self,
            "TriggerScheduleRule",
            rule_name="provenance-trigger-schedule-rule",
            event_bus=self.messaging.bus,
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE], detail_type=TRIGGER_SCHEDULE_DETAIL_TYPES
            ),
            targets=[
                targets.LambdaFunction(
                    self.trigger_schedule_manager,
                    dead_letter_queue=worker_dlq,
                    retry_attempts=2,
                )
            ],
        )

    def _outbox_sweep_schedule(self) -> None:
        """specs/15_API_SPEC.md section 13.6 wants a sweep every 30 seconds.

        EventBridge Scheduler's minimum ``rate()`` granularity is one minute, so
        a 30-second schedule cannot be expressed. Rather than quietly weaken the
        guarantee, the schedule fires once a minute and the handler performs two
        sweeps 30 seconds apart inside one invocation. The mechanism does not
        match a naive reading of the specification and that is recorded in
        40_INFRA_IAC.md section 15.9.
        """
        scheduler.CfnSchedule(
            self,
            "OutboxSweep",
            name="provenance-outbox-sweep",
            group_name=self.messaging.system_schedule_group_name,
            schedule_expression="rate(1 minute)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            state="ENABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=self.outbox_dispatch.function_arn,
                role_arn=self.messaging.scheduler_invoke_role.role_arn,
                input=('{"source":"SCHEDULER","batch_size":50,"passes":2,"pass_gap_seconds":30}'),
                # 0 is correct: the next schedule fires in one minute, and
                # retrying a sweep would only produce a second concurrent
                # claimer -- safe but pointless.
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=0, maximum_event_age_in_seconds=60
                ),
                dead_letter_config=scheduler.CfnSchedule.DeadLetterConfigProperty(
                    arn=self.messaging.scheduler_dlq.queue_arn
                ),
            ),
        )

    # ------------------------------------------------------------------
    @property
    def exports(self) -> ComputeExports:
        return ComputeExports(
            ses_ingest=self.ses_ingest,
            textract_complete=self.textract_complete,
            outbox_dispatch=self.outbox_dispatch,
            trigger_wakeup=self.trigger_wakeup,
            advocate_dispatch=self.advocate_dispatch,
            action_execute=self.action_execute,
            notification_dispatch=self.notification_dispatch,
            trigger_schedule_manager=self.trigger_schedule_manager,
            textract_topic=self.textract_topic,
        )
