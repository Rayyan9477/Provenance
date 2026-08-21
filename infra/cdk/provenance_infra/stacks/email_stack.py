"""Stack 7 -- ``PvEmailStack``.

SES: the two inbound identities, the receipt rule set and its one rule, the
outbound sending identity with MAIL FROM, the verified demo sink, the
configuration set, and the EventBridge event destination
(40_INFRA_IAC.md section 5).

Deploys **after** ``PvComputeStack`` on purpose. The receipt rule needs the
``provenance-ses-ingest`` function ARN and the function needs read on the
inbound bucket, so the cycle is resolved by dependency direction rather than by
an SSM indirection: Data (bucket) -> Compute (function) -> Email (rule).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ses as ses
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import ComputeExports, DataExports, FoundationExports, MessagingExports


class PvEmailStack(Stack):
    """Inbound receiving and outbound sending, both pinned to one account."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        foundation: FoundationExports,
        data: DataExports,
        messaging: MessagingExports,
        compute: ComputeExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # ---- inbound -----------------------------------------------------
        ses.CfnEmailIdentity(
            self,
            "IngestDomainIdentity",
            email_identity=config.ingest_host,
            dkim_attributes=ses.CfnEmailIdentity.DkimAttributesProperty(signing_enabled=True),
        )

        rule_set = ses.CfnReceiptRuleSet(
            self, "InboundRuleSet", rule_set_name=cfg.SES_RULE_SET_NAME
        )

        rule = ses.CfnReceiptRule(
            self,
            "IngestRule",
            rule_set_name=cfg.SES_RULE_SET_NAME,
            rule=ses.CfnReceiptRule.RuleProperty(
                name=cfg.SES_RULE_NAME,
                enabled=True,
                # Without this, spamVerdict and virusVerdict are absent and
                # specs/15_API_SPEC.md section 9.1 step 3's rejection rule has
                # nothing to evaluate.
                scan_enabled=True,
                # ``Require`` would bounce mail from any sending MTA that will
                # not do STARTTLS. That is the right production setting and the
                # wrong demo setting: a bounced hero artifact is unrecoverable
                # in a live demo. The verdicts are captured either way. This is
                # a deliberate demo concession (section 15.5), not an oversight,
                # and it should be Require in production.
                tls_policy="Optional",
                # One rule for the whole domain. Per-address receipt rules would
                # mean a CDK deploy on every user sign-up; the alias is resolved
                # in ingest_aliases by HMAC, so routing lives in the database
                # where it can be rotated and disabled in one statement.
                recipients=[config.ingest_host],
                actions=[
                    # Order is load-bearing. Actions run in order and the Lambda
                    # expects the object to already exist.
                    ses.CfnReceiptRule.ActionProperty(
                        s3_action=ses.CfnReceiptRule.S3ActionProperty(
                            bucket_name=data.inbound_bucket.bucket_name,
                            object_key_prefix="ses/incoming/",
                            kms_key_arn=foundation.artifact_key.key_arn,
                        )
                    ),
                    ses.CfnReceiptRule.ActionProperty(
                        lambda_action=ses.CfnReceiptRule.LambdaActionProperty(
                            function_arn=compute.ses_ingest.function_arn,
                            # Async: SES must not wait on the control plane.
                            invocation_type="Event",
                        )
                    ),
                ],
            ),
        )
        rule.add_resource_dependency(rule_set)

        # SES invokes the ingest worker directly. The permission is scoped to
        # this account so another customer's receipt rule cannot invoke it.
        lambda_.CfnPermission(
            self,
            "AllowSesInvokeIngest",
            action="lambda:InvokeFunction",
            function_name=compute.ses_ingest.function_arn,
            principal="ses.amazonaws.com",
            source_account=self.account,
        )

        # ---- outbound ----------------------------------------------------
        ses.CfnEmailIdentity(
            self,
            "SendingDomainIdentity",
            email_identity=config.root_domain,
            dkim_attributes=ses.CfnEmailIdentity.DkimAttributesProperty(signing_enabled=True),
            dkim_signing_attributes=ses.CfnEmailIdentity.DkimSigningAttributesProperty(
                next_signing_key_length="RSA_2048_BIT"
            ),
            mail_from_attributes=ses.CfnEmailIdentity.MailFromAttributesProperty(
                mail_from_domain=config.mail_from_host,
                behavior_on_mx_failure="USE_DEFAULT_VALUE",
            ),
            feedback_attributes=ses.CfnEmailIdentity.FeedbackAttributesProperty(
                email_forwarding_enabled=False
            ),
        )

        # The demo counterparty sink. Required by the SES sandbox restriction:
        # outbound mail may go only to verified identities, and verifying this
        # one is what lets ACTION_RECIPIENT_MODE=DEMO_SINK work with no
        # production-access request on the critical path (section 5.6).
        ses.CfnEmailIdentity(
            self,
            "DemoSinkIdentity",
            email_identity=config.demo_sink_host,
            dkim_attributes=ses.CfnEmailIdentity.DkimAttributesProperty(signing_enabled=True),
        )

        config_set = ses.CfnConfigurationSet(
            self,
            "OutboundConfigSet",
            name=cfg.SES_CONFIGURATION_SET,
            reputation_options=ses.CfnConfigurationSet.ReputationOptionsProperty(
                reputation_metrics_enabled=True
            ),
            sending_options=ses.CfnConfigurationSet.SendingOptionsProperty(sending_enabled=True),
            suppression_options=ses.CfnConfigurationSet.SuppressionOptionsProperty(
                suppressed_reasons=["BOUNCE", "COMPLAINT"]
            ),
            # No open or click tracking. Rewriting links inside a dispute letter
            # would alter the exact bytes whose SHA-256 the human approved, which
            # would break the approval_draft_sha256 binding. That is a
            # correctness constraint, not a privacy preference.
        )

        destination = ses.CfnConfigurationSetEventDestination(
            self,
            "OutboundEvents",
            configuration_set_name=cfg.SES_CONFIGURATION_SET,
            event_destination=(
                ses.CfnConfigurationSetEventDestination.EventDestinationProperty(
                    name="provenance-outbound-to-eventbridge",
                    enabled=True,
                    matching_event_types=[
                        "SEND",
                        "DELIVERY",
                        "BOUNCE",
                        "COMPLAINT",
                        "REJECT",
                        "RENDERING_FAILURE",
                        "DELIVERY_DELAY",
                    ],
                    event_bridge_destination=(
                        ses.CfnConfigurationSetEventDestination.EventBridgeDestinationProperty(
                            event_bus_arn=messaging.bus.event_bus_arn
                        )
                    ),
                )
            ),
        )
        destination.add_resource_dependency(config_set)

        # A custom bus does not accept PutEvents from a service principal by
        # default. Without this the event destination above is created and
        # silently delivers nothing, so ``action.failed.v1`` with
        # ``error_code: RECIPIENT_BOUNCED`` could never be produced and the
        # action plane could not tell a delivered dispute from a bounced one.
        #
        # The policy is created here rather than on the bus construct so the
        # dependency runs Email -> Messaging only.
        events.CfnEventBusPolicy(
            self,
            "AllowSesPublishToDomainBus",
            event_bus_name=messaging.bus.event_bus_name,
            statement_id="AllowSesEventDestination",
            statement={
                "Effect": "Allow",
                "Principal": {"Service": "ses.amazonaws.com"},
                "Action": "events:PutEvents",
                "Resource": messaging.bus.event_bus_arn,
                "Condition": {"StringEquals": {"aws:SourceAccount": self.account}},
            },
        )
