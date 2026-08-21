"""Stack 8 -- ``PvAgentStack``.

The Bedrock AgentCore Runtime ``provenance_agents``, its execution role, and its
inbound JWT authorizer (40_INFRA_IAC.md section 9).

One runtime serves both LangGraph graphs. The graph to run is selected by the
invocation payload rather than by a second runtime, because two runtimes would
double the cold-start surface and the container is identical.

There is no CDK L2 for AgentCore Runtime, so it is created through the
control-plane API with an ``AwsCustomResource`` and the ARN becomes a stack
output rather than a copied string. The exact request JSON also lives in
``infra/agentcore/`` so a control-plane API shape change is a one-file edit --
this is the most version-sensitive surface in the whole build (section 15.7).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import DataExports, FoundationExports, IdentityExports

# The AgentCore control-plane service, as the AWS SDK names it.
AGENTCORE_CONTROL_SERVICE = "bedrock-agentcore-control"


class PvAgentStack(Stack):
    """Deployment unit ``agent-runtime``."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        foundation: FoundationExports,
        identity: IdentityExports,
        data: DataExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        execution_role = self._execution_role(foundation, data)

        # The other half of the section 2.2 exception-2 break: read at deploy
        # time, not at synth time, so no CDK reference points from stack 8 back
        # at stack 6.
        api_base_url = ssm.StringParameter.value_for_string_parameter(self, cfg.SSM_API_BASE_URL)

        container_uri = f"{data.agent_repo.repository_uri}:{config.image_tag}"

        parameters: dict[str, Any] = {
            # Underscores only; the AgentCore name grammar rejects hyphens.
            "agentRuntimeName": cfg.AGENT_RUNTIME_NAME,
            "description": "Provenance LangGraph interpreter and advocate graphs",
            "roleArn": execution_role.role_arn,
            "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "HTTP"},
            "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": container_uri}},
            "authorizerConfiguration": {
                "customJWTAuthorizer": {
                    "discoveryUrl": f"{identity.issuer}/.well-known/openid-configuration",
                    # provenance-workers, not a fourth app client. Both invokers
                    # -- the control plane and advocate_dispatch -- are trusted
                    # control-plane-side workloads, and a fourth client would
                    # contradict the frozen three-client design in
                    # specs/15_API_SPEC.md section 2.1. Reusing
                    # provenance-agent-runtime would mean the agent's own
                    # callback credential also opens the front door of the
                    # runtime it runs in, which is strictly worse. (Section 15.8.)
                    #
                    # No allowedAudience is configured: a Cognito access token
                    # has no ``aud`` claim. allowedClients matches ``client_id``,
                    # which is the claim that exists. Configuring an audience
                    # here produces a runtime that rejects every valid token.
                    "allowedClients": [identity.worker_client.user_pool_client_id],
                }
            },
            "environmentVariables": {
                "APP_BASE_URL": api_base_url,
                "COGNITO_TOKEN_ENDPOINT": config.cognito_token_endpoint,
                "COGNITO_AGENT_CLIENT_ID": identity.agent_client.user_pool_client_id,
                "COGNITO_AGENT_CLIENT_SECRET_ARN": data.cognito_secret.secret_arn,
                "MCP_SERVER_URL": config.mcp_server_url,
                "MCP_AUTH_SECRET_ARN": data.mcp_secret.secret_arn,
                # Passed through unmodified by the router. Anthropic ids are
                # inference-profile ids; the embedding id is a bare id. The two
                # forms are mirror images, so a client that normalises either one
                # cannot call both families.
                "BEDROCK_EXTRACTION_MODEL_ID": cfg.BEDROCK_EXTRACTION_MODEL_ID,
                "BEDROCK_REASONING_MODEL_ID": cfg.BEDROCK_REASONING_MODEL_ID,
                "BEDROCK_EMBEDDING_MODEL_ID": cfg.BEDROCK_EMBEDDING_MODEL_ID,
                "EMBEDDING_DIMENSIONS": cfg.EMBEDDING_DIMENSIONS,
                "EMBEDDING_VERSION": cfg.EMBEDDING_VERSION,
                "OTEL_SERVICE_NAME": "provenance-agent-runtime",
                "PV_MCP_ENABLED": "true",
                "PV_AGENT_MODE": "LIVE",
            },
        }

        runtime = cr.AwsCustomResource(
            self,
            "AgentRuntime",
            on_create=cr.AwsSdkCall(
                service=AGENTCORE_CONTROL_SERVICE,
                action="CreateAgentRuntime",
                parameters=parameters,
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_update=cr.AwsSdkCall(
                service=AGENTCORE_CONTROL_SERVICE,
                action="UpdateAgentRuntime",
                parameters={
                    **parameters,
                    "agentRuntimeId": cr.PhysicalResourceIdReference(),
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_delete=cr.AwsSdkCall(
                service=AGENTCORE_CONTROL_SERVICE,
                action="DeleteAgentRuntime",
                parameters={"agentRuntimeId": cr.PhysicalResourceIdReference()},
            ),
            # NOT AwsCustomResourcePolicy.fromSdkCalls(ANY_RESOURCE), which is
            # what section 9.2 prints. That grants the custom-resource Lambda
            # ``bedrock-agentcore:*`` on ``*`` plus an unscoped PassRole, and a
            # PassRole on ``*`` in the same account as an evidence store is not a
            # least-privilege posture. Create cannot name a runtime that does
            # not exist yet, so ``runtime/*`` is the tightest expressible scope;
            # the PassRole is scoped to the one role and the one service.
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        sid="ManageTheProvenanceAgentRuntime",
                        actions=[
                            "bedrock-agentcore:CreateAgentRuntime",
                            "bedrock-agentcore:UpdateAgentRuntime",
                            "bedrock-agentcore:DeleteAgentRuntime",
                            "bedrock-agentcore:GetAgentRuntime",
                            "bedrock-agentcore:ListAgentRuntimes",
                        ],
                        resources=[
                            f"arn:{self.partition}:bedrock-agentcore:{cfg.REGION}:"
                            f"{self.account}:runtime/*"
                        ],
                    ),
                    iam.PolicyStatement(
                        sid="PassAgentExecutionRoleOnly",
                        actions=["iam:PassRole"],
                        resources=[execution_role.role_arn],
                        conditions={
                            "StringEquals": {
                                "iam:PassedToService": "bedrock-agentcore.amazonaws.com"
                            }
                        },
                    ),
                ]
            ),
            install_latest_aws_sdk=True,
            timeout=Duration.minutes(10),
        )

        ssm.StringParameter(
            self,
            "RuntimeArnParam",
            parameter_name=cfg.SSM_AGENT_RUNTIME_ARN,
            string_value=runtime.get_response_field("agentRuntimeArn"),
            description=(
                "Injected into App Runner by ops/apprunner-set-agent-arn.sh "
                "(40_INFRA_IAC.md section 2.4 step 9)"
            ),
        )

    # ------------------------------------------------------------------
    def _execution_role(self, foundation: FoundationExports, data: DataExports) -> iam.Role:
        """What this role *cannot* do is the load-bearing part.

        No ``s3:*``  -- a malicious artifact cannot make the agent read another
        artifact's bytes; content arrives only through the run-scoped
        ``/internal/v1`` endpoint.
        No ``ses:*`` -- the Advocate drafts and cannot send. Invariant 4, in IAM.
        No ``events:PutEvents`` -- the agent cannot manufacture a domain event a
        consumer would treat as committed state.
        No ``provenance/db`` and no ``provenance/crypto`` -- no SQL credential
        and no capability-proof key. The agent's only database reach is the MCP
        server as ``pv_agent_reader`` on five views.
        No ``bedrock-agentcore:*`` -- no recursion path, and no way to escape its
        own capability.
        """
        role = iam.Role(
            self,
            "AgentExecutionRole",
            role_name="provenance-agentcore-execution-role",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:{self.partition}:bedrock-agentcore:{cfg.REGION}:"
                            f"{self.account}:*"
                        )
                    },
                },
            ),
            description="The provenance_agents runtime's identity",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeCanonicalModelsOnly",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=cfg.bedrock_invoke_resources(self.partition, self.account),
            )
        )
        data.agent_repo.grant_pull(role)
        foundation.artifact_key.grant_decrypt(role)
        data.cognito_secret.grant_read(role)
        data.mcp_secret.grant_read(role)
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOwnLogsOnly",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    foundation.agent_runtime_log_group.log_group_arn,
                    f"{foundation.agent_runtime_log_group.log_group_arn}:log-stream:*",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PublishProvenanceMetricsOnly",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "Provenance"}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="XrayHasNoResourceLevelPermission",
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )
        return role
