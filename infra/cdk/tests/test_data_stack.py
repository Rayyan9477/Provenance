"""``PvDataStack``: the append-only evidence store and the two image repositories.

The bucket assertions are the ones that would be expensive to discover later.
Invariant 1 is *evidence is append-only*; a lifecycle rule that expired an
artifact would silently make a State Proof unciteable four months after the
demo, and nothing would fail loudly when it happened.
"""

from __future__ import annotations

from typing import Any

from provenance_infra import config as cfg
from provenance_infra.stacks.data_stack import TEARDOWN_ROLE_NAME
from pv_cdk_testing import as_list, flatten, policy_statements, resources_of


def _bucket(template: dict[str, Any], name: str) -> dict[str, Any]:
    for body in resources_of(template, "AWS::S3::Bucket").values():
        if body["Properties"].get("BucketName") == name:
            return body
    raise AssertionError(f"no bucket named {name}")


def test_both_buckets_exist_with_the_api_contract_spelling(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``00_PRODUCT.md`` section 2.3 writes ``provenance-artifacts-use1`` inside a
    worked example; ``specs/15_API_SPEC.md`` sections 8.18 and 9.1 write
    ``provenance-artifacts-us-east-1`` inside a pre-signed URL and a request
    body. The API contract is the thing code parses, so it wins. (Section 15.1.)
    """
    buckets = resources_of(template_json["PvDataStack"], "AWS::S3::Bucket")
    assert {b["Properties"]["BucketName"] for b in buckets.values()} == {
        cfg.ARTIFACT_BUCKET_NAME,
        cfg.INBOUND_BUCKET_NAME,
    }
    assert cfg.ARTIFACT_BUCKET_NAME.endswith("-us-east-1")


def test_both_buckets_block_public_access_completely(
    template_json: dict[str, dict[str, Any]],
) -> None:
    for name in (cfg.ARTIFACT_BUCKET_NAME, cfg.INBOUND_BUCKET_NAME):
        props = _bucket(template_json["PvDataStack"], name)["Properties"]
        assert props["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        assert props["OwnershipControls"]["Rules"] == [{"ObjectOwnership": "BucketOwnerEnforced"}]


def test_both_buckets_use_the_customer_managed_key(
    template_json: dict[str, dict[str, Any]],
) -> None:
    for name in (cfg.ARTIFACT_BUCKET_NAME, cfg.INBOUND_BUCKET_NAME):
        props = _bucket(template_json["PvDataStack"], name)["Properties"]
        rule = props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
        assert "KMSMasterKeyID" in rule["ServerSideEncryptionByDefault"]
        assert rule["BucketKeyEnabled"] is True


def test_the_artifact_bucket_is_versioned_and_retained(
    template_json: dict[str, dict[str, Any]],
) -> None:
    body = _bucket(template_json["PvDataStack"], cfg.ARTIFACT_BUCKET_NAME)
    assert body["Properties"]["VersioningConfiguration"] == {"Status": "Enabled"}
    assert body["DeletionPolicy"] == "Retain"


def test_only_normalized_output_expires(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``raw/`` and ``ses/`` are never overwritten and never expired.

    Parser output is regenerable from ``raw/``; an artifact is not.
    """
    props = _bucket(template_json["PvDataStack"], cfg.ARTIFACT_BUCKET_NAME)["Properties"]
    rules = {rule["Id"]: rule for rule in props["LifecycleConfiguration"]["Rules"]}

    assert rules["expire-normalized-parser-output"]["Prefix"] == "normalized/"
    assert rules["expire-normalized-parser-output"]["ExpirationInDays"] == 90

    for rule_id in ("cool-raw-artifacts", "cool-inbound-mime"):
        assert "ExpirationInDays" not in rules[rule_id], rule_id
        assert rules[rule_id]["Transitions"][0]["StorageClass"] == "INTELLIGENT_TIERING"

    # A pre-signed PUT abandoned mid-multipart leaves billable parts behind.
    assert rules["abort-incomplete-multipart"]["AbortIncompleteMultipartUpload"] == {
        "DaysAfterInitiation": 1
    }


def test_the_bucket_policy_refuses_uploads_that_bypass_the_kms_audit_trail(
    template_json: dict[str, dict[str, Any]],
) -> None:
    sids = set()
    for _logical, cfn_type, statement in policy_statements(template_json["PvDataStack"]):
        if cfn_type != "AWS::S3::BucketPolicy" or statement.get("Effect") != "Deny":
            continue
        sids.add(statement.get("Sid"))
    assert {"DenyWrongEncryptionKey", "DenyUnencryptedObjectUploads"} <= sids


def test_deletes_are_denied_on_raw_and_ses_but_not_on_normalized(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``normalized/*`` must stay deletable: the lifecycle rule expires it and a
    lifecycle expiration is evaluated against the bucket policy.
    """
    statement = None
    for _logical, cfn_type, candidate in policy_statements(template_json["PvDataStack"]):
        if cfn_type == "AWS::S3::BucketPolicy" and candidate.get("Sid") == (
            "DenyDeleteExceptTeardown"
        ):
            statement = candidate
    assert statement is not None

    assert statement["Effect"] == "Deny"
    assert set(statement["Action"]) == {
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
    }
    resources = flatten(as_list(statement["Resource"]))
    assert "raw/*" in resources
    assert "ses/*" in resources
    assert "normalized/*" not in resources
    assert TEARDOWN_ROLE_NAME in flatten(statement["NotPrincipal"])


def test_the_inbound_bucket_accepts_writes_only_from_this_account_s_ses(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A bucket policy allowing ``ses.amazonaws.com`` without ``aws:SourceAccount``
    and ``aws:SourceArn`` lets any AWS customer's receipt rule write into this
    bucket. For an evidence store that is an evidence-injection vulnerability,
    not a noisy-neighbour problem (section 757).
    """
    statement = None
    for _logical, cfn_type, candidate in policy_statements(template_json["PvDataStack"]):
        if cfn_type == "AWS::S3::BucketPolicy" and candidate.get("Sid") == "AllowSesInboundPut":
            statement = candidate
    assert statement is not None
    assert statement["Principal"] == {"Service": "ses.amazonaws.com"}
    assert statement["Action"] == "s3:PutObject"
    assert "ses/incoming/*" in flatten(statement["Resource"])
    condition = statement["Condition"]
    assert condition["StringEquals"]["aws:SourceAccount"] == {"Ref": "AWS::AccountId"}
    source_arn = flatten(condition["StringLike"]["aws:SourceArn"])
    assert f"receipt-rule-set/{cfg.SES_RULE_SET_NAME}:receipt-rule/*" in source_arn


def test_the_inbound_staging_copy_expires_but_the_canonical_copy_does_not(
    template_json: dict[str, dict[str, Any]],
) -> None:
    props = _bucket(template_json["PvDataStack"], cfg.INBOUND_BUCKET_NAME)["Properties"]
    (rule,) = props["LifecycleConfiguration"]["Rules"]
    assert rule["Id"] == "expire-ses-staging"
    assert rule["Prefix"] == "ses/incoming/"
    assert rule["ExpirationInDays"] == 7


def test_both_ecr_repositories_have_immutable_tags(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Immutable tags are what make ``G13.2`` meaningful.

    With mutable tags, ``sha-abc123`` could point at different bytes than the
    reviewer read, and the gate would be checking a label rather than an
    artifact.
    """
    repos = resources_of(template_json["PvDataStack"], "AWS::ECR::Repository")
    assert len(repos) == 2
    for body in repos.values():
        assert body["Properties"]["ImageTagMutability"] == "IMMUTABLE"
        assert body["Properties"]["ImageScanningConfiguration"] == {"ScanOnPush": True}
        assert body["Properties"]["EncryptionConfiguration"]["EncryptionType"] == "KMS"


def test_four_secrets_with_the_documented_names(
    template_json: dict[str, dict[str, Any]],
) -> None:
    secrets = resources_of(template_json["PvDataStack"], "AWS::SecretsManager::Secret")
    assert {b["Properties"]["Name"] for b in secrets.values()} == {
        cfg.SECRET_DB,
        cfg.SECRET_COGNITO,
        cfg.SECRET_CRYPTO,
        cfg.SECRET_MCP,
    }


def test_the_artifact_bucket_arn_is_published_to_ssm(
    template_json: dict[str, dict[str, Any]],
) -> None:
    params = resources_of(template_json["PvDataStack"], "AWS::SSM::Parameter")
    assert {b["Properties"]["Name"] for b in params.values()} == {cfg.SSM_ARTIFACT_BUCKET_ARN}


def test_the_browser_may_only_put_and_only_from_the_two_known_origins(
    template_json: dict[str, dict[str, Any]],
) -> None:
    props = _bucket(template_json["PvDataStack"], cfg.ARTIFACT_BUCKET_NAME)["Properties"]
    (rule,) = props["CorsConfiguration"]["CorsRules"]
    assert rule["AllowedMethods"] == ["PUT"]
    assert set(rule["AllowedOrigins"]) == {
        "https://app.provenance.app",
        "http://localhost:3000",
    }
    assert "x-amz-checksum-sha256" in rule["AllowedHeaders"]
