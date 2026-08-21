"""Stack 3 -- ``PvDataStack``.

The two S3 buckets, the two ECR repositories, and the four Secrets Manager
secrets. Stateful, so it is deliberately separated from the two stacks that get
redeployed on every build (40_INFRA_IAC.md sections 2.1, 4, 8.1, 8.7).

Secrets are created **empty**. Values are written out of band by
``ops/secrets-populate.sh`` (section 2.4 step 4) so no secret material ever
appears in a CloudFormation template, in ``cdk.out``, or in this repository.
"""

from __future__ import annotations

import json
from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import DataExports, FoundationExports
from provenance_infra.removal import auto_delete_objects, stateful_removal

# The role that ``ops/teardown.sh`` assumes. It is the ONLY principal exempted
# from the append-only deny in section 4.4.
#
# WARNING, and reported rather than silently resolved: 40_INFRA_IAC.md names
# this role in a ``NotPrincipal`` and never creates it in any stack. S3 rejects
# a bucket policy whose principal ARN does not resolve, so ``PvDataStack``
# cannot deploy until the account owner creates ``provenance-teardown-role``.
# Creating a delete-capable role next to an evidence store is an account-owner
# decision, so this build names it and does not mint it. (D-13-006.)
TEARDOWN_ROLE_NAME = "provenance-teardown-role"


class PvDataStack(Stack):
    """Buckets, container repositories, and secrets."""

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
        self.foundation = foundation

        self.artifact_bucket = self._artifact_bucket()
        self.inbound_bucket = self._inbound_bucket()
        self.control_plane_repo, self.agent_repo = self._repositories()
        (
            self.db_secret,
            self.cognito_secret,
            self.crypto_secret,
            self.mcp_secret,
        ) = self._secrets()

        ssm.StringParameter(
            self,
            "ArtifactBucketArnParam",
            parameter_name=cfg.SSM_ARTIFACT_BUCKET_ARN,
            string_value=self.artifact_bucket.bucket_arn,
        )

    # ----------------------------------------------------------------------
    def _artifact_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "ArtifactBucket",
            bucket_name=cfg.ARTIFACT_BUCKET_NAME,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False,
            # ACLs disabled entirely: object ownership is not negotiable when
            # the objects are evidence.
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.foundation.artifact_key,
            bucket_key_enabled=True,
            enforce_ssl=True,
            minimum_tls_version=1.2,
            # Append-only insurance. Versioning plus the deny below is the
            # practical guarantee; S3 Object Lock is deliberately not used
            # (section 15.4).
            versioned=True,
            removal_policy=stateful_removal(self.config),
            auto_delete_objects=auto_delete_objects(self.config),
            lifecycle_rules=[
                s3.LifecycleRule(
                    # Derived parser output is regenerable from raw/. It is the
                    # only prefix that expires.
                    id="expire-normalized-parser-output",
                    enabled=True,
                    prefix="normalized/",
                    expiration=Duration.days(90),
                    noncurrent_version_expiration=Duration.days(7),
                ),
                s3.LifecycleRule(
                    # Raw artifacts are never expired. They only get cheaper.
                    id="cool-raw-artifacts",
                    enabled=True,
                    prefix="raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="cool-inbound-mime",
                    enabled=True,
                    prefix="ses/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    # A pre-signed PUT abandoned mid-multipart leaves billable
                    # parts behind.
                    id="abort-incomplete-multipart",
                    enabled=True,
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                ),
                s3.LifecycleRule(
                    # Versioning is insurance against accidental overwrite, not
                    # an archive.
                    id="expire-noncurrent-raw-versions",
                    enabled=True,
                    prefix="raw/",
                    noncurrent_version_expiration=Duration.days(30),
                ),
            ],
            cors=[
                s3.CorsRule(
                    # The browser PUTs directly to S3
                    # (specs/15_API_SPEC.md section 8.18 step 2).
                    allowed_origins=[self.config.web_base_url, "http://localhost:3000"],
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_headers=[
                        "content-type",
                        "x-amz-server-side-encryption",
                        "x-amz-checksum-sha256",
                    ],
                    exposed_headers=["etag", "x-amz-checksum-sha256"],
                    max_age=300,
                )
            ],
        )

        # 1. Refuse any PUT that is not encrypted with OUR key. Without this a
        #    client can upload with SSE-S3 and the object silently leaves the
        #    KMS audit trail.
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyWrongEncryptionKey",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects("*")],
                conditions={
                    "StringNotEquals": {
                        "s3:x-amz-server-side-encryption-aws-kms-key-id": (
                            self.foundation.artifact_key.key_arn
                        )
                    }
                },
            )
        )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyUnencryptedObjectUploads",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects("*")],
                conditions={"StringNotEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}},
            )
        )

        # 2. Evidence is append-only. Nothing in the running system may delete
        #    an object or a version. The deny is scoped to raw/* and ses/* only,
        #    because ``normalized/*`` must remain deletable: the lifecycle rule
        #    above expires it, and a lifecycle expiration is evaluated against
        #    the bucket policy.
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyDeleteExceptTeardown",
                effect=iam.Effect.DENY,
                not_principals=[
                    iam.ArnPrincipal(
                        f"arn:{self.partition}:iam::{self.account}:role/{TEARDOWN_ROLE_NAME}"
                    )
                ],
                actions=[
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion",
                    "s3:PutBucketVersioning",
                    "s3:PutLifecycleConfiguration",
                ],
                resources=[
                    bucket.bucket_arn,
                    bucket.arn_for_objects("raw/*"),
                    bucket.arn_for_objects("ses/*"),
                ],
            )
        )
        return bucket

    # ----------------------------------------------------------------------
    def _inbound_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "InboundBucket",
            bucket_name=cfg.INBOUND_BUCKET_NAME,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.foundation.artifact_key,
            bucket_key_enabled=True,
            enforce_ssl=True,
            minimum_tls_version=1.2,
            versioned=False,
            removal_policy=stateful_removal(self.config),
            auto_delete_objects=auto_delete_objects(self.config),
            lifecycle_rules=[
                s3.LifecycleRule(
                    # Staging only. ses_ingest copies the canonical object into
                    # the artifact bucket under the immutable dated key; this
                    # copy is a duplicate and may expire.
                    id="expire-ses-staging",
                    enabled=True,
                    prefix="ses/incoming/",
                    expiration=Duration.days(7),
                )
            ],
        )

        # Exactly one writer: the SES service, and only for this account.
        #
        # ``aws:SourceAccount`` plus ``aws:SourceArn`` is not optional. A bucket
        # policy allowing ``ses.amazonaws.com`` without them lets any AWS
        # customer's receipt rule write into this bucket, which for an evidence
        # store is an evidence-injection vulnerability rather than a
        # noisy-neighbour problem (40_INFRA_IAC.md section 757).
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSesInboundPut",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects("ses/incoming/*")],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "StringLike": {
                        "aws:SourceArn": (
                            f"arn:{self.partition}:ses:{cfg.REGION}:{self.account}:"
                            f"receipt-rule-set/{cfg.SES_RULE_SET_NAME}:receipt-rule/*"
                        )
                    },
                },
            )
        )
        return bucket

    # ----------------------------------------------------------------------
    def _repositories(self) -> tuple[ecr.Repository, ecr.Repository]:
        repos: list[ecr.Repository] = []
        for logical, name in (
            ("ControlPlaneRepo", cfg.ECR_CONTROL_PLANE_REPO),
            ("AgentRuntimeRepo", cfg.ECR_AGENT_RUNTIME_REPO),
        ):
            repos.append(
                ecr.Repository(
                    self,
                    logical,
                    repository_name=name,
                    image_scan_on_push=True,
                    # IMMUTABLE is what makes G13.2 meaningful. With mutable
                    # tags ``sha-abc123`` could point at different bytes than
                    # the reviewer read, and the gate would be checking a label
                    # rather than an artifact.
                    image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                    encryption=ecr.RepositoryEncryption.KMS,
                    encryption_key=self.foundation.artifact_key,
                    removal_policy=stateful_removal(self.config),
                    empty_on_delete=auto_delete_objects(self.config),
                    lifecycle_rules=[
                        ecr.LifecycleRule(
                            rule_priority=1,
                            description="keep 10 tagged builds",
                            tag_status=ecr.TagStatus.TAGGED,
                            tag_prefix_list=["sha-"],
                            max_image_count=10,
                        ),
                        ecr.LifecycleRule(
                            rule_priority=2,
                            description="expire untagged after 1 day",
                            tag_status=ecr.TagStatus.UNTAGGED,
                            max_image_age=Duration.days(1),
                        ),
                    ],
                )
            )
        return repos[0], repos[1]

    # ----------------------------------------------------------------------
    def _secrets(self) -> tuple[secretsmanager.Secret, ...]:
        """Four secrets, each a JSON document with a fixed key set.

        The template carries the **key names** and an empty string for each
        value, so a reader of the synthesised template can see the shape without
        the template ever holding material.

        These four are encrypted with the AWS-managed ``aws/secretsmanager`` key
        rather than the artifact CMK, and that is a forced choice rather than a
        preference: ``Secret.grantRead`` on a CMK-encrypted secret grants
        ``kms:Decrypt`` through a ``ViaServicePrincipal``, which CDK can only
        express in the **key's resource policy**. The key lives in
        ``PvFoundationStack`` and the readers live in ``PvComputeStack`` and
        ``PvApiStack``, so that write makes Foundation depend on Compute while
        Compute already depends on Foundation -- a cycle CloudFormation rejects.
        The artifact CMK still protects the bytes it was created for: every S3
        object and both ECR repositories. (D-13-010.)
        """
        made: list[secretsmanager.Secret] = []
        for logical, name in (
            ("DbSecret", cfg.SECRET_DB),
            ("CognitoSecret", cfg.SECRET_COGNITO),
            ("CryptoSecret", cfg.SECRET_CRYPTO),
            ("McpSecret", cfg.SECRET_MCP),
        ):
            template = {key: "" for key in cfg.SECRET_KEYS[name]}
            made.append(
                secretsmanager.Secret(
                    self,
                    logical,
                    secret_name=name,
                    description=(
                        f"{name}: keys {', '.join(cfg.SECRET_KEYS[name])}. "
                        "Created empty; populated out of band by "
                        "ops/secrets-populate.sh. No value is ever in a template."
                    ),
                    removal_policy=stateful_removal(self.config),
                    generate_secret_string=secretsmanager.SecretStringGenerator(
                        secret_string_template=json.dumps(template, separators=(",", ":")),
                        generate_string_key="pending_population",
                        exclude_punctuation=True,
                        password_length=16,
                    ),
                )
            )
        return tuple(made)

    # ----------------------------------------------------------------------
    @property
    def exports(self) -> DataExports:
        return DataExports(
            artifact_bucket=self.artifact_bucket,
            inbound_bucket=self.inbound_bucket,
            control_plane_repo=self.control_plane_repo,
            agent_repo=self.agent_repo,
            db_secret=self.db_secret,
            cognito_secret=self.cognito_secret,
            crypto_secret=self.crypto_secret,
            mcp_secret=self.mcp_secret,
        )
