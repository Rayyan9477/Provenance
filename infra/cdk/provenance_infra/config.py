"""Frozen names, ids, and environment-derived settings for every Provenance stack.

Every physical resource name in this module is a contract value taken from
``docs/ops/40_INFRA_IAC.md`` or ``docs/CANONICAL_DECISIONS.md``. Changing one
here without changing it there is a defect, not a refactor.

Two rules this module exists to enforce:

1. **No account id and no credential-shaped literal anywhere in the tree.**
   ``G0.3`` scans this repository. Anything that varies per account is either a
   CDK token (``Stack.account``) or a CDK context lookup, never a literal.
2. **Bedrock model ids are provider-dependent and mirror-imaged**
   (``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon*). Anthropic chat
   models are invocable **only** by inference-profile id; every other provider
   is invocable **only** by bare id. The router passes configured ids through
   unmodified, so the IAM policies below must name the exact strings the router
   will send.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Final

from constructs import Construct

# ---------------------------------------------------------------------------
# Region. Single region, no cross-region reference (40_INFRA_IAC.md section 1.1).
# ---------------------------------------------------------------------------
REGION: Final[str] = "us-east-1"

# ---------------------------------------------------------------------------
# Bedrock model id canon (CANONICAL_DECISIONS.md, frozen 2026-08-17).
#
# These strings are what the router sends and therefore what IAM must allow.
# They were established by live ``Converse`` invocation, not by
# ``list-foundation-models`` -- the listing returns ids that are not invocable,
# which is the trap the earlier run fell into.
# ---------------------------------------------------------------------------
BEDROCK_REASONING_MODEL_ID: Final[str] = "us.anthropic.claude-opus-4-6-v1"
BEDROCK_EXTRACTION_MODEL_ID: Final[str] = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_EMBEDDING_MODEL_ID: Final[str] = "amazon.titan-embed-text-v2:0"

# Verified invocable, held as the fallback if Opus 4.6 throttles under eval
# load. It is NOT the default and no environment variable points at it; it is
# granted in IAM so that switching is a one-line environment change under time
# pressure rather than a policy edit plus a redeploy.
BEDROCK_REASONING_FALLBACK_MODEL_ID: Final[str] = "us.anthropic.claude-sonnet-4-6"

# Denied on this account. Named here so a test can assert that no policy and no
# environment default reaches for one of them.
BEDROCK_DENIED_MODEL_IDS: Final[frozenset[str]] = frozenset(
    {
        "us.anthropic.claude-opus-5",
        "us.anthropic.claude-sonnet-5",
        "us.anthropic.claude-opus-4-8",
        "us.anthropic.claude-opus-4-7",
        # The bare forms the pack carried before the canon was frozen. A bare
        # Anthropic id is not invocable in any form on Bedrock, so a policy
        # naming one would allow a call that can never succeed.
        "anthropic.claude-opus-5",
        "anthropic.claude-haiku-4-5",
    }
)

EMBEDDING_DIMENSIONS: Final[str] = "1024"
EMBEDDING_VERSION: Final[str] = "v1"

# The ``us.`` inference-profile region group. An inference-profile invocation is
# authorised against the profile ARN *and* against the foundation-model ARN in
# every region the profile can route to, so both classes appear in the policy.
INFERENCE_PROFILE_REGIONS: Final[tuple[str, ...]] = ("us-east-1", "us-east-2", "us-west-2")

_PROFILE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^(us|global)\.")


def is_inference_profile_id(model_id: str) -> bool:
    """True for an id that carries a ``us.``/``global.`` region-group prefix."""
    return bool(_PROFILE_PREFIX_RE.match(model_id))


def bare_model_id(model_id: str) -> str:
    """Strip the region-group prefix to get the underlying foundation-model id.

    Used only to build the foundation-model half of a Bedrock IAM policy. The
    router never does this: ``CANONICAL_DECISIONS.md`` requires configured ids
    to be passed through unmodified, because the two identifier forms are mirror
    images and a client that rewrites either one cannot call both families.
    """
    return _PROFILE_PREFIX_RE.sub("", model_id)


def bedrock_invoke_resources(partition: str, account: str) -> list[str]:
    """Every ARN a caller needs to invoke exactly the reachable model set.

    An inference-profile invocation is authorised **twice**: once against the
    profile ARN in the calling region, and once against the foundation-model ARN
    in each region the profile may route to. Granting only the profile produces
    ``AccessDeniedException`` at the first cross-region routing decision, which
    is intermittent and looks like throttling.

    A bare-id model (Titan embeddings) has no profile and is granted only as a
    foundation model. The two halves are asymmetric because the identifier forms
    are asymmetric, which is the whole point of the Bedrock model id canon.

    The list is enumerated, never wildcarded: a code path reaching for a fourth
    model fails closed rather than quietly incurring cost on an unreviewed one.
    """
    resources: list[str] = []
    for model_id in (
        BEDROCK_REASONING_MODEL_ID,
        BEDROCK_EXTRACTION_MODEL_ID,
        BEDROCK_REASONING_FALLBACK_MODEL_ID,
    ):
        resources.append(f"arn:{partition}:bedrock:{REGION}:{account}:inference-profile/{model_id}")
        bare = bare_model_id(model_id)
        resources.extend(
            f"arn:{partition}:bedrock:{region}::foundation-model/{bare}"
            for region in INFERENCE_PROFILE_REGIONS
        )
    resources.append(
        f"arn:{partition}:bedrock:{REGION}::foundation-model/{BEDROCK_EMBEDDING_MODEL_ID}"
    )
    return resources


# ---------------------------------------------------------------------------
# Physical resource names (40_INFRA_IAC.md section 1.2).
# ---------------------------------------------------------------------------
ARTIFACT_BUCKET_NAME: Final[str] = f"provenance-artifacts-{REGION}"
INBOUND_BUCKET_NAME: Final[str] = f"provenance-inbound-{REGION}"

ARTIFACT_KEY_ALIAS: Final[str] = "alias/provenance-artifacts-key"

USER_POOL_NAME: Final[str] = "provenance-users"
HOSTED_UI_PREFIX: Final[str] = "provenance-auth"
RESOURCE_SERVER_ID: Final[str] = "provenance"
JUDGES_GROUP_NAME: Final[str] = "provenance-judges"

WEB_CLIENT_NAME: Final[str] = "provenance-web"
AGENT_CLIENT_NAME: Final[str] = "provenance-agent-runtime"
WORKER_CLIENT_NAME: Final[str] = "provenance-workers"

DOMAIN_BUS_NAME: Final[str] = "provenance-domain-bus"
TRIGGER_SCHEDULE_GROUP: Final[str] = "provenance-triggers"
SYSTEM_SCHEDULE_GROUP: Final[str] = "provenance-system"

ADVOCATE_QUEUE_NAME: Final[str] = "provenance-advocate-queue"
ACTION_QUEUE_NAME: Final[str] = "provenance-action-queue"
WORKER_DLQ_NAME: Final[str] = "provenance-worker-dlq"
SCHEDULER_DLQ_NAME: Final[str] = "provenance-scheduler-dlq"
ADVOCATE_DLQ_NAME: Final[str] = "provenance-advocate-dlq"
ACTION_DLQ_NAME: Final[str] = "provenance-action-dlq"
NOTIFICATION_DLQ_NAME: Final[str] = "provenance-notification-dlq"

DLQ_NAMES: Final[tuple[str, ...]] = (
    WORKER_DLQ_NAME,
    SCHEDULER_DLQ_NAME,
    ADVOCATE_DLQ_NAME,
    ACTION_DLQ_NAME,
    NOTIFICATION_DLQ_NAME,
)

TEXTRACT_TOPIC_NAME: Final[str] = "provenance-textract-status"
ALERT_TOPIC_NAME: Final[str] = "provenance-alerts"

APP_RUNNER_SERVICE_NAME: Final[str] = "provenance-control-plane"
APP_RUNNER_SCALING_NAME: Final[str] = "provenance-apprunner-scaling"
AMPLIFY_APP_NAME: Final[str] = "provenance-web"

# AgentCore's name grammar rejects hyphens (40_INFRA_IAC.md section 1.2).
AGENT_RUNTIME_NAME: Final[str] = "provenance_agents"

ECR_CONTROL_PLANE_REPO: Final[str] = "provenance/control-plane"
ECR_AGENT_RUNTIME_REPO: Final[str] = "provenance/agent-runtime"

SES_RULE_SET_NAME: Final[str] = "provenance-inbound-rules"
SES_RULE_NAME: Final[str] = "provenance-ingest-rule"
SES_CONFIGURATION_SET: Final[str] = "provenance-outbound"

# Secrets Manager: four secrets, each a JSON document (section 8.7). The five
# ``provenance/db`` keys are frozen by CANONICAL_DECISIONS.md -- ``ops_reader_url``
# is the fifth and it is not optional.
SECRET_DB: Final[str] = "provenance/db"
SECRET_COGNITO: Final[str] = "provenance/cognito"
SECRET_CRYPTO: Final[str] = "provenance/crypto"
SECRET_MCP: Final[str] = "provenance/mcp"

SECRET_KEYS: Final[dict[str, tuple[str, ...]]] = {
    SECRET_DB: ("app_url", "kernel_url", "agent_url", "migrator_url", "ops_reader_url"),
    SECRET_COGNITO: ("agent_client_secret", "worker_client_secret"),
    SECRET_CRYPTO: (
        "capability_hmac_key",
        "capability_hmac_kid",
        "cursor_hmac_key",
        "alias_hmac_key",
    ),
    SECRET_MCP: ("agent_url", "mcp_endpoint", "mcp_bearer"),
}

# The CockroachDB Cloud Managed MCP Server endpoint. Its hostname embeds the
# cluster name, so it is deployment-specific and is supplied per deployment
# through the ``pv:mcp_server_url`` context key or the ``MCP_SERVER_URL``
# environment variable. The default below is a placeholder of the right shape
# that resolves to nothing: a real cluster hostname committed here would be
# private infrastructure published in a public tree, and it would keep being
# handed to every stack long after the cluster it names was gone.
DEFAULT_MCP_SERVER_URL: Final[str] = "https://mcp.example.invalid"

# The five SQL roles that already exist on the provisioned CockroachDB cluster
# (CockroachDB CCL v26.2.5, plan BASIC). Named here only so a test can assert
# that the ``provenance/db`` key set covers every one of them.
SQL_ROLES: Final[tuple[str, ...]] = (
    "pv_migrator",
    "pv_app_reader_writer",
    "pv_kernel_writer",
    "pv_agent_reader",
    "pv_ops_reader",
)

LOG_GROUP_CONTROL_PLANE: Final[str] = "/provenance/control-plane"
LOG_GROUP_AGENT_RUNTIME: Final[str] = "/provenance/agent-runtime"
LOG_GROUP_DOMAIN_EVENTS: Final[str] = "/provenance/domain-events"

SSM_API_BASE_URL: Final[str] = "/provenance/api/base-url"
SSM_AGENT_RUNTIME_ARN: Final[str] = "/provenance/agent/runtime-arn"
SSM_ARTIFACT_BUCKET_ARN: Final[str] = "/provenance/data/artifact-bucket-arn"

DASHBOARD_NAME: Final[str] = "provenance-ops"

# The seven custom scopes. The list is closed (specs/15_API_SPEC.md section 2.1);
# an eighth requires editing that document first.
SCOPES: Final[dict[str, str]] = {
    "memory/read": "Read canonical memory and read models",
    "memory/propose": "Submit a typed MemoryProposal to the kernel",
    "action/propose": "Create a draft ActionIntent",
    "ingest/write": "Register artifacts and admit evidence",
    "trigger/evaluate": "Evaluate a prospective trigger predicate",
    "action/execute": "Execute an approved external action",
    "outbox/dispatch": "Claim and publish outbox events",
}

# The four deployment units (CANONICAL_DECISIONS.md -> Repository layout canon).
# Not five services and not three agent services: ARCHITECTURE.md section 25's
# microservice tree is the rejected alternative and must not be built from.
DEPLOYMENT_UNITS: Final[tuple[str, ...]] = ("web", "control-plane", "agent-runtime", "workers")


def _lookup(scope: Construct, context_key: str, env_var: str, default: str) -> str:
    """Context first, then environment, then the documented default.

    Context is preferred so a value can be pinned in ``cdk.json`` and reviewed,
    rather than depending on whatever happened to be exported in the shell that
    ran the deploy.
    """
    from_ctx = scope.node.try_get_context(context_key)
    if isinstance(from_ctx, str) and from_ctx:
        return from_ctx
    return os.environ.get(env_var) or default


def _optional(scope: Construct, context_key: str, env_var: str) -> str | None:
    value = scope.node.try_get_context(context_key) or os.environ.get(env_var)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class PvConfig:
    """Everything that varies between one account and another.

    Constructed once in ``app.py`` and threaded through every stack as a prop.
    No stack reads ``os.environ`` on its own.
    """

    root_domain: str
    api_host: str
    web_host: str
    ingest_host: str
    mail_from_host: str
    demo_sink_host: str
    git_sha: str
    owner: str
    owner_email: str | None
    web_repository: str | None
    mcp_server_url: str
    teardown: bool
    # Section 8.4: App Runner returns CNAME validation records that must be
    # added at the registrar. Until they validate, point the API base URL at the
    # generated ``*.awsapprunner.com`` host so the stack is testable before DNS
    # lands. That is one context key (``pv:api_base_url``), not a code change.
    api_base_url_override: str | None = None
    custom_domains_enabled: bool = True
    monthly_budget_usd: int = 150
    bedrock_budget_usd: int = 60
    estimated_charges_threshold_usd: int = 120
    tags: dict[str, str] = field(default_factory=dict)

    # -- derived -----------------------------------------------------------
    @property
    def api_base_url(self) -> str:
        return self.api_base_url_override or f"https://{self.api_host}"

    @property
    def web_base_url(self) -> str:
        return f"https://{self.web_host}"

    @property
    def hosted_ui_domain(self) -> str:
        return f"{HOSTED_UI_PREFIX}.auth.{REGION}.amazoncognito.com"

    @property
    def cognito_token_endpoint(self) -> str:
        return f"https://{self.hosted_ui_domain}/oauth2/token"

    @property
    def ses_from_address(self) -> str:
        return f"disputes@{self.root_domain}"

    @property
    def ses_notification_from_address(self) -> str:
        return f"notifications@{self.root_domain}"

    @property
    def image_tag(self) -> str:
        """``sha-<git sha>``. ECR tags are immutable, so this pins bytes."""
        return f"sha-{self.git_sha}"

    @classmethod
    def from_scope(cls, scope: Construct) -> PvConfig:
        root_domain = _lookup(scope, "pv:root_domain", "PV_ROOT_DOMAIN", "provenance.app")
        owner = _lookup(scope, "pv:owner", "PV_OWNER", "unset")
        # PV_GIT_SHA has no safe default: a placeholder would let a stack
        # synthesise against an image tag that does not exist, and G13.2 asserts
        # string equality between the deployed sha and ``git rev-parse HEAD``.
        # "unset" is loud on purpose.
        git_sha = _lookup(scope, "pv:git_sha", "PV_GIT_SHA", "unset")
        teardown = (
            os.environ.get("PV_TEARDOWN") == "1"
            or scope.node.try_get_context("pv:teardown") is True
        )
        return cls(
            root_domain=root_domain,
            api_host=f"api.{root_domain}",
            web_host=f"app.{root_domain}",
            ingest_host=f"in.{root_domain}",
            mail_from_host=f"mail.{root_domain}",
            demo_sink_host=f"demo-sink.{root_domain}",
            git_sha=git_sha,
            owner=owner,
            owner_email=_optional(scope, "pv:owner_email", "PV_OWNER_EMAIL"),
            web_repository=_optional(scope, "pv:web_repository", "PV_WEB_REPOSITORY"),
            mcp_server_url=_lookup(
                scope,
                "pv:mcp_server_url",
                "MCP_SERVER_URL",
                DEFAULT_MCP_SERVER_URL,
            ),
            teardown=teardown,
            api_base_url_override=_optional(scope, "pv:api_base_url", "PV_API_BASE_URL"),
            custom_domains_enabled=scope.node.try_get_context("pv:custom_domains") is not False,
            tags={
                "Project": "Provenance",
                "Component": "platform",
                "Owner": owner,
                "CostCenter": "provenance-platform",
                "DeleteAfter": "2026-10-15",
            },
        )
