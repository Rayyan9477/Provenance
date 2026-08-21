"""Typed cross-stack props (40_INFRA_IAC.md section 2.2).

Rule: **props in, no hand-written ``Fn::ImportValue``.** Each stack takes a
typed props object and receives concrete constructs from ``app.py``. CDK creates
whatever exports it needs.

Two documented exceptions, both preserved here:

1. The SES receipt rule to Lambda cycle is resolved by dependency direction,
   not by SSM indirection: ``PvDataStack`` (bucket) -> ``PvComputeStack``
   (function, granted read on the bucket) -> ``PvEmailStack`` (rule referencing
   the function). No stack needs anything from a stack that depends on it.
2. App Runner needs the AgentCore runtime ARN and AgentCore needs the App Runner
   URL. That is a genuine cycle -- the two services really do call each other --
   and it is broken at *runtime* rather than at synth time. ``PvApiStack``
   publishes ``/provenance/api/base-url``; ``PvAgentStack`` reads it with an SSM
   lookup at deploy time; ``AGENTCORE_RUNTIME_ARN`` is injected into App Runner
   by a one-line ``update-service`` in the deploy script. Neither direction
   appears as a CDK reference, which is why neither stack appears in the other's
   props below.
"""

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs


@dataclass(frozen=True)
class FoundationExports:
    """Stack 1. KMS CMK, log groups, alert topic."""

    artifact_key: kms.IKey
    alert_topic: sns.ITopic
    control_plane_log_group: logs.ILogGroup
    agent_runtime_log_group: logs.ILogGroup


@dataclass(frozen=True)
class IdentityExports:
    """Stack 2. Cognito user pool, three app clients, hosted UI."""

    user_pool: cognito.IUserPool
    web_client: cognito.IUserPoolClient
    agent_client: cognito.IUserPoolClient
    worker_client: cognito.IUserPoolClient
    issuer: str
    jwks_url: str
    hosted_ui_domain: str


@dataclass(frozen=True)
class DataExports:
    """Stack 3. Buckets, ECR repositories, Secrets Manager secrets."""

    artifact_bucket: s3.IBucket
    inbound_bucket: s3.IBucket
    control_plane_repo: ecr.IRepository
    agent_repo: ecr.IRepository
    db_secret: secretsmanager.ISecret
    cognito_secret: secretsmanager.ISecret
    crypto_secret: secretsmanager.ISecret
    mcp_secret: secretsmanager.ISecret


@dataclass(frozen=True)
class MessagingExports:
    """Stack 4. Bus, queues, DLQs, Scheduler groups, Scheduler invoke role."""

    bus: events.IEventBus
    advocate_queue: sqs.IQueue
    action_queue: sqs.IQueue
    worker_dlq: sqs.IQueue
    scheduler_dlq: sqs.IQueue
    advocate_dlq: sqs.IQueue
    action_dlq: sqs.IQueue
    notification_dlq: sqs.IQueue
    trigger_schedule_group_name: str
    system_schedule_group_name: str
    scheduler_invoke_role: iam.IRole


@dataclass(frozen=True)
class ComputeExports:
    """Stack 5. The eight Lambda workers that are not the Cognito trigger.

    ``provenance-cognito-post-confirmation`` is the ninth function and lives in
    ``PvIdentityStack``: ``UserPool.add_trigger`` needs the function, and putting
    it here would make Identity depend on Compute while Compute already depends
    on Identity for the worker client id. See ``docs`` discrepancy note D-13-002.
    """

    ses_ingest: lambda_.IFunction
    textract_complete: lambda_.IFunction
    outbox_dispatch: lambda_.IFunction
    trigger_wakeup: lambda_.IFunction
    advocate_dispatch: lambda_.IFunction
    action_execute: lambda_.IFunction
    notification_dispatch: lambda_.IFunction
    trigger_schedule_manager: lambda_.IFunction
    textract_topic: sns.ITopic
