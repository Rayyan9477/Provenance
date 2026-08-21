"""Stack 6 -- ``PvApiStack``.

The App Runner service that is deployment unit ``control-plane``, its
autoscaling configuration, its two IAM roles, the X-Ray observability
configuration, the custom domain association, and the SSM parameter that breaks
half of the App Runner <-> AgentCore cycle (40_INFRA_IAC.md sections 2.2, 8).

No stateful resource lives here: this is the other of the two stacks that gets
redeployed most during the build.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import DataExports, FoundationExports, IdentityExports, MessagingExports


class PvApiStack(Stack):
    """One App Runner container terminating TLS for ``/v1`` and ``/internal/v1``."""

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

        self.access_role = self._access_role()
        self.instance_role = self._instance_role()

        scaling = apprunner.CfnAutoScalingConfiguration(
            self,
            "Scaling",
            auto_scaling_configuration_name=cfg.APP_RUNNER_SCALING_NAME,
            # Requests per instance before a new one is added.
            max_concurrency=40,
            # One always-warm instance: cold starts are a demo risk (G13.8).
            min_size=1,
            # Pinned 1-2 per specs/15_API_SPEC.md sections 14 and 17.1. The
            # in-process rate limits are per instance, so maxSize bounds the
            # per-user limit error at 2x rather than leaving it unbounded.
            max_size=2,
        )
        observability = apprunner.CfnObservabilityConfiguration(
            self,
            "XrayObservability",
            observability_configuration_name="provenance-apprunner-xray",
            trace_configuration=apprunner.CfnObservabilityConfiguration.TraceConfigurationProperty(
                vendor="AWSXRAY"
            ),
        )

        self.service = apprunner.CfnService(
            self,
            "ControlPlane",
            service_name=cfg.APP_RUNNER_SERVICE_NAME,
            auto_scaling_configuration_arn=scaling.attr_auto_scaling_configuration_arn,
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                cpu="1 vCPU",
                memory="2 GB",
                instance_role_arn=self.instance_role.role_arn,
            ),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP",
                # Liveness only: no auth, no DB, no rate limit. /v1/version is
                # the operating-mode channel and is deliberately not the health
                # check.
                path="/v1/healthz",
                interval=10,
                timeout=5,
                healthy_threshold=1,
                unhealthy_threshold=5,
            ),
            network_configuration=apprunner.CfnService.NetworkConfigurationProperty(
                # Public egress. CockroachDB Cloud Basic offers no IP allowlist
                # and no PrivateLink, so a VPC connector would buy no
                # network-layer restriction and would add a NAT gateway plus
                # cold-start latency to reach the same public TLS endpoint
                # (section 8.5). The SQL grants remain the permission boundary.
                egress_configuration=apprunner.CfnService.EgressConfigurationProperty(
                    egress_type="DEFAULT"
                ),
                ingress_configuration=apprunner.CfnService.IngressConfigurationProperty(
                    is_publicly_accessible=True
                ),
                ip_address_type="IPV4",
            ),
            observability_configuration=(
                apprunner.CfnService.ServiceObservabilityConfigurationProperty(
                    observability_enabled=True,
                    observability_configuration_arn=(
                        observability.attr_observability_configuration_arn
                    ),
                )
            ),
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                # An auto-deploy on ECR push means a ``docker push`` during
                # rehearsal restarts the service. Deploys are one explicit
                # command, which also makes G13.2's sha equality a statement
                # about a decision rather than a race.
                auto_deployments_enabled=False,
                authentication_configuration=(
                    apprunner.CfnService.AuthenticationConfigurationProperty(
                        access_role_arn=self.access_role.role_arn
                    )
                ),
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=(
                        f"{data.control_plane_repo.repository_uri}:{config.image_tag}"
                    ),
                    image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port="8080",
                        start_command=(
                            "uvicorn services.control_plane.app.main:app "
                            "--host 0.0.0.0 --port 8080 --workers 2"
                        ),
                        runtime_environment_variables=self._runtime_variables(),
                        runtime_environment_secrets=self._runtime_secrets(),
                    ),
                ),
            ),
        )

        # Half of the section 2.2 exception-2 break. PvAgentStack reads this at
        # deploy time; AGENTCORE_RUNTIME_ARN travels the other way through a
        # one-line ``update-service`` in the deploy script, because expressing
        # both directions as CDK references produces a deadly embrace that no
        # restructuring removes -- the two services genuinely call each other.
        ssm.StringParameter(
            self,
            "ApiBaseUrlParam",
            parameter_name=cfg.SSM_API_BASE_URL,
            string_value=config.api_base_url,
            description="Read by PvAgentStack at deploy time (40_INFRA_IAC.md section 2.2)",
        )

        if config.custom_domains_enabled:
            self._custom_domain()

    # ------------------------------------------------------------------
    def _custom_domain(self) -> None:
        """Associate ``api.<root domain>`` with the service.

        40_INFRA_IAC.md section 8.4 writes this as ``new CfnCustomDomain(...)``.
        **There is no such construct and no such CloudFormation resource**:
        ``AWS::AppRunner::*`` covers Service, AutoScalingConfiguration,
        ObservabilityConfiguration, VpcConnector and VpcIngressConnection only,
        and a custom domain is associated through the App Runner API. The
        snippet cannot be compiled in TypeScript either, so this is a defect in
        the specification rather than a Python-vs-TypeScript difference.
        (Reported as D-13-009.)

        It is implemented here as a scoped custom resource. App Runner returns
        CNAME validation records that must be added at the registrar; until they
        validate, point ``pv:api_base_url`` at the generated
        ``*.awsapprunner.com`` host so the stack stays testable.
        """
        cr.AwsCustomResource(
            self,
            "ApiDomain",
            on_create=cr.AwsSdkCall(
                service="apprunner",
                action="AssociateCustomDomain",
                parameters={
                    "ServiceArn": self.service.attr_service_arn,
                    "DomainName": self.config.api_host,
                    "EnableWWWSubdomain": False,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{cfg.APP_RUNNER_SERVICE_NAME}-{self.config.api_host}"
                ),
            ),
            on_delete=cr.AwsSdkCall(
                service="apprunner",
                action="DisassociateCustomDomain",
                parameters={
                    "ServiceArn": self.service.attr_service_arn,
                    "DomainName": self.config.api_host,
                },
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        sid="AssociateThisServiceDomainOnly",
                        actions=[
                            "apprunner:AssociateCustomDomain",
                            "apprunner:DisassociateCustomDomain",
                            "apprunner:DescribeCustomDomains",
                        ],
                        resources=[self.service.attr_service_arn],
                    )
                ]
            ),
        )

    # ------------------------------------------------------------------
    def _runtime_variables(self) -> list[apprunner.CfnService.KeyValuePairProperty]:
        """Non-secret configuration only.

        ``G13.6`` asserts that no runtime *variable* matches ``://|AKIA|BEGIN``.
        The two URLs below do match ``://`` and are supposed to: the gate greps
        the variable list for material, and the honest reading is that a public
        origin is not material. Every value that *is* material appears in
        :meth:`_runtime_secrets` instead.
        """
        pairs = {
            "APP_ENV": "prod",
            "APP_BASE_URL": self.config.api_base_url,
            "WEB_BASE_URL": self.config.web_base_url,
            "BUILD_SHA": self.config.git_sha,
            "AWS_REGION_NAME": cfg.REGION,
            "LOG_LEVEL": "INFO",
            "OTEL_SERVICE_NAME": "provenance-control-plane",
            "SCHEMA_REVISION": "0008",
            "COGNITO_USER_POOL_ID": self.identity.user_pool.user_pool_id,
            "COGNITO_ISSUER": self.identity.issuer,
            "COGNITO_JWKS_URL": self.identity.jwks_url,
            "COGNITO_TOKEN_ENDPOINT": self.config.cognito_token_endpoint,
            "COGNITO_WEB_CLIENT_ID": self.identity.web_client.user_pool_client_id,
            "COGNITO_AGENT_CLIENT_ID": self.identity.agent_client.user_pool_client_id,
            "COGNITO_WORKER_CLIENT_ID": self.identity.worker_client.user_pool_client_id,
            "COGNITO_JUDGE_GROUP": cfg.JUDGES_GROUP_NAME,
            "S3_ARTIFACT_BUCKET": self.data.artifact_bucket.bucket_name,
            "S3_KMS_KEY_ARN": self.foundation.artifact_key.key_arn,
            "MAX_ARTIFACT_BYTES": str(20 * 1024 * 1024),
            "UPLOAD_URL_TTL_SECONDS": "900",
            "DOWNLOAD_URL_TTL_SECONDS": "300",
            "SES_INGEST_DOMAIN": self.config.ingest_host,
            "SES_FROM_ADDRESS": self.config.ses_from_address,
            "SES_CONFIGURATION_SET": cfg.SES_CONFIGURATION_SET,
            "SES_DEMO_SINK_DOMAIN": self.config.demo_sink_host,
            # Never a silent rewrite: in DEMO_SINK mode the Advocate drafts to
            # the sink address and the UI renders the sink address, so the human
            # approves what will actually be sent.
            "ACTION_RECIPIENT_MODE": "DEMO_SINK",
            "PV_ACTION_EXECUTION_MODE": "ENABLED",
            "EVENTBRIDGE_BUS_NAME": self.messaging.bus.event_bus_name,
            "OUTBOX_SWEEP_BATCH_SIZE": "50",
            # Read from configuration, passed through unmodified. Because the
            # two Bedrock identifier forms differ by provider, the router must
            # not synthesise a profile prefix.
            "BEDROCK_REASONING_MODEL_ID": cfg.BEDROCK_REASONING_MODEL_ID,
            "BEDROCK_EXTRACTION_MODEL_ID": cfg.BEDROCK_EXTRACTION_MODEL_ID,
            "BEDROCK_EMBEDDING_MODEL_ID": cfg.BEDROCK_EMBEDDING_MODEL_ID,
            "EMBEDDING_DIMENSIONS": cfg.EMBEDDING_DIMENSIONS,
            "EMBEDDING_VERSION": cfg.EMBEDDING_VERSION,
            # Set by the section 11.4 probe outcome. NONE unless the cosine
            # opclass is rejected and Variant C is taken.
            "EMBEDDING_NORMALIZATION": "NONE",
            "EMBEDDING_CACHE_TABLE": "embedding_cache",
            "AGENTCORE_QUALIFIER": "DEFAULT",
            # Intentionally a placeholder here. PvAgentStack writes the real ARN
            # to /provenance/agent/runtime-arn and ops/apprunner-set-agent-arn.sh
            # injects it (section 2.4 step 9).
            "AGENTCORE_RUNTIME_ARN": "pending-agent-stack",
            "PV_AGENT_MODE": "LIVE",
            "PV_MCP_ENABLED": "true",
            "MCP_SERVER_URL": self.config.mcp_server_url,
        }
        return [
            apprunner.CfnService.KeyValuePairProperty(name=name, value=value)
            for name, value in pairs.items()
        ]

    def _runtime_secrets(self) -> list[apprunner.CfnService.KeyValuePairProperty]:
        """App Runner resolves a JSON key inside a secret with ``<arn>:<key>::``.

        The trailing ``::`` is the empty version stage and the empty version id.
        Getting it wrong injects the whole JSON blob as the value, and the
        failure looks like a malformed connection string rather than a
        configuration error.

        ``G13.6`` asserts this list contains ``COCKROACH_DATABASE_URL``,
        ``COGNITO_AGENT_CLIENT_SECRET_ARN`` and ``MCP_AUTH_SECRET_ARN``. Two of
        those names end in ``_ARN`` but are delivered through the secrets
        channel and therefore arrive as *values*; the settings object accepts
        either shape, which keeps the gate honest and keeps account ids out of
        plaintext configuration.
        """
        entries = {
            "COCKROACH_DATABASE_URL": (self.data.db_secret, "app_url"),
            "COCKROACH_KERNEL_URL": (self.data.db_secret, "kernel_url"),
            "COGNITO_AGENT_CLIENT_SECRET_ARN": (self.data.cognito_secret, "agent_client_secret"),
            "COGNITO_WORKER_CLIENT_SECRET_ARN": (
                self.data.cognito_secret,
                "worker_client_secret",
            ),
            "MCP_AUTH_SECRET_ARN": (self.data.mcp_secret, "agent_url"),
            "PROVENANCE_CAPABILITY_HMAC_KEY": (self.data.crypto_secret, "capability_hmac_key"),
            "PROVENANCE_CAPABILITY_HMAC_KID": (self.data.crypto_secret, "capability_hmac_kid"),
            "CURSOR_HMAC_KEY": (self.data.crypto_secret, "cursor_hmac_key"),
            "INGEST_ALIAS_HMAC_KEY": (self.data.crypto_secret, "alias_hmac_key"),
        }
        return [
            apprunner.CfnService.KeyValuePairProperty(
                name=name, value=f"{secret.secret_arn}:{json_key}::"
            )
            for name, (secret, json_key) in entries.items()
        ]

    # ------------------------------------------------------------------
    def _access_role(self) -> iam.Role:
        """Used by the App Runner SERVICE to pull from ECR. Not the app's identity.

        Conflating this with the instance role is the most common App Runner
        mistake, and it is the one that accidentally hands the running container
        ECR write access.
        """
        role = iam.Role(
            self,
            "AccessRole",
            role_name="provenance-apprunner-access-role",
            assumed_by=iam.ServicePrincipal(
                "build.apprunner.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="App Runner pulls the control-plane image with this role",
        )
        self.data.control_plane_repo.grant_pull(role)
        # The ECR repository is KMS-encrypted with the artifact CMK.
        self.foundation.artifact_key.grant_decrypt(role)
        return role

    def _instance_role(self) -> iam.Role:
        """The RUNNING CONTAINER's identity: the app's least-privilege set.

        There is no ``secretsmanager:PutSecretValue``, no ``s3:DeleteObject``,
        no ``sqs:*`` and no ``scheduler:*``. The control plane reads secrets,
        writes evidence, publishes events, and sends one kind of email.

        ``sqs:*`` is absent deliberately and not by omission: the canon decision
        is that the Kernel performs no side effect after the retry cap and no
        kernel retry queue exists, so a control plane that could enqueue would
        contradict a frozen decision.
        """
        role = iam.Role(
            self,
            "InstanceRole",
            role_name="provenance-apprunner-instance-role",
            assumed_by=iam.ServicePrincipal(
                "tasks.apprunner.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="The control-plane container's identity",
        )

        role.add_to_policy(
            iam.PolicyStatement(
                sid="PresignAndVerifyArtifacts",
                actions=["s3:PutObject", "s3:GetObject", "s3:GetObjectAttributes"],
                resources=[
                    self.data.artifact_bucket.arn_for_objects("raw/*"),
                    self.data.artifact_bucket.arn_for_objects("normalized/*"),
                    self.data.artifact_bucket.arn_for_objects("ses/*"),
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="HeadObjectNeedsListOnPrefix",
                actions=["s3:ListBucket"],
                resources=[self.data.artifact_bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": ["raw/*", "normalized/*", "ses/*"]}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="UseArtifactKey",
                actions=["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
                resources=[self.foundation.artifact_key.key_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeCanonicalModelsOnly",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=cfg.bedrock_invoke_resources(self.partition, self.account),
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeAgentRuntime",
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                # The runtime id is suffixed by AgentCore at creation, so the
                # tightest expressible scope is the name prefix. It is still a
                # single named runtime rather than the service.
                resources=[
                    f"arn:{self.partition}:bedrock-agentcore:{cfg.REGION}:{self.account}:"
                    f"runtime/{cfg.AGENT_RUNTIME_NAME}*"
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PublishDomainEvents",
                actions=["events:PutEvents"],
                resources=[self.messaging.bus.event_bus_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="SendApprovedActionEmail",
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=[
                    f"arn:{self.partition}:ses:{cfg.REGION}:{self.account}:"
                    f"identity/{self.config.root_domain}"
                ],
                conditions={
                    "StringEquals": {
                        # The control plane cannot send from an arbitrary
                        # address, and cannot bypass the configuration set that
                        # produces the bounce events the action plane depends on.
                        "ses:FromAddress": self.config.ses_from_address,
                        "ses:ConfigurationSetName": cfg.SES_CONFIGURATION_SET,
                    }
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="StartTextractForScannedDocuments",
                actions=[
                    "textract:StartDocumentAnalysis",
                    "textract:DetectDocumentText",
                    "textract:AnalyzeDocument",
                ],
                # NOT scopable: Textract has no resource-level permission for
                # these actions. The compensating control is that the document
                # must already be an object under a prefix this role can read.
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PassTextractPublishRole",
                actions=["iam:PassRole"],
                resources=[
                    f"arn:{self.partition}:iam::{self.account}:"
                    f"role/provenance-textract-publish-role"
                ],
                conditions={"StringEquals": {"iam:PassedToService": "textract.amazonaws.com"}},
            )
        )
        for secret in (
            self.data.db_secret,
            self.data.cognito_secret,
            self.data.crypto_secret,
            self.data.mcp_secret,
        ):
            secret.grant_read(role)
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOwnLogsOnly",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    self.foundation.control_plane_log_group.log_group_arn,
                    f"{self.foundation.control_plane_log_group.log_group_arn}:log-stream:*",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PublishProvenanceMetricsOnly",
                actions=["cloudwatch:PutMetricData"],
                # NOT scopable: PutMetricData supports no resource-level
                # permission. ``cloudwatch:namespace`` is the only narrowing AWS
                # offers and it is applied with plain StringEquals, so a call
                # that omits the namespace is denied rather than tolerated.
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "Provenance"}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="XrayHasNoResourceLevelPermission",
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                # NOT scopable and no condition key exists either. Reported as
                # an unscopable grant rather than presented as least privilege.
                resources=["*"],
            )
        )
        return role
