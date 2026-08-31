"""The one typed settings object — T0.4.

Authority
---------
- ``implementation/06_CODING_AGENT_HANDOFF.md`` section 17 — the environment
  manifest this module must model, field for field.
- ``ops/40_INFRA_IAC.md`` section 12 — the same manifest with purposes,
  examples and consumers, plus section 12.11, whose ``Settings`` sketch this
  module implements rather than paraphrases.
- ``ops/40_INFRA_IAC.md`` section 11.3 — the ``provenance/db`` secret and its
  five role URL keys, which ``ROLE_DSN_BINDINGS`` mirrors.
- ``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon (frozen 2026-08-17)* —
  the model ids, and the reason a bare ``anthropic.*`` id is rejected here.
- ``quality/23_PHASE_GATES.md`` section 6, gate ``G0.7``.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
**No code outside this module calls ``os.environ``, ``os.getenv``,
``dotenv``, or any other environment reader.** Domain code, service code,
agent code, worker code and script code take a ``Settings`` instance (or the
values off it) as an argument. That rule is what makes the environment
auditable: one file lists every variable the system reads, so a variable that
is set-but-unread and a variable that is read-but-unset are both visible by
inspection. ``ops/40_INFRA_IAC.md`` section 12 states the same rule from the
operations side; ``tools/`` and ``ops/`` shell scripts are outside the Python
process and are governed by ``quality/23_PHASE_GATES.md`` section 2.2 instead.

Three properties, and why each one is here
------------------------------------------
1. **It raises rather than defaults.** A required value that is missing
   produces a ``pydantic.ValidationError`` naming the variable, at import of
   the settings object — that is, at container start. A settings object that
   silently defaults is how a service starts against the wrong database and
   nobody finds out until a write lands there.

2. **Values that do not exist yet are ``Optional``, and are checked at the
   point of use.** The ``pv_*`` role DSNs are created by ``T0.5``/``T2.6``;
   the AgentCore and MCP endpoints do not exist until Phases 13 and 11. Making
   them required would mean Phase 0 could not construct ``Settings`` at all,
   and making them silently default would mean a Phase 6 process pointing at
   nothing. They are ``None`` until their phase, and
   ``require_role_dsns`` / ``require_agent_runtime_targets`` raise a
   ``SettingsNotReadyError`` that names the phase, the task, the environment
   variable and the ``provenance/db`` secret key that supplies it.

3. **Credentials are ``SecretStr`` and never render.** ``repr``, ``str``,
   ``format``, ``model_dump``, ``model_dump_json`` and ``logging`` all mask
   them, which is what keeps a credential out of a log line.
   ``tests/test_settings.py::test_secretstr_never_appears_in_repr_or_str``
   proves it.

   **What ``SecretStr`` does not cover, stated rather than implied.** A
   ``pydantic.ValidationError`` raised by a ``model_validator(mode="after")``
   attaches the *raw input* to every error entry, and ``errors()`` and
   ``json()`` render it in plaintext even though ``str()`` masks it. So the
   guarantee is not "a credential cannot reach a traceback" — it is that
   ``get_settings`` never lets such an exception out: it re-raises
   :class:`SettingsValidationError`, rebuilt from ``(type, loc, msg)`` with
   ``input`` dropped. ``Settings()`` constructed directly still raises
   pydantic's exception, and a caller doing that owns the handling. Found by
   ``D-00-005``; ``pickle.dumps`` on a ``Settings`` instance leaks the same way
   and nothing in this build pickles one.

Deliberate deviations from ``EXECUTION/70_TASK_PLAN.md`` T0.4, recorded
-----------------------------------------------------------------------
- T0.4 says to pin the two chat model ids as ``Literal`` types on the bare ids
  ``anthropic.claude-haiku-4-5`` and ``anthropic.claude-opus-5``. That text
  predates the live Phase 0 probe. ``CANONICAL_DECISIONS.md`` -> *Bedrock model
  id canon* supersedes it: bare ids are **not invocable**, and the Tier R
  target is denied to this account today while ``us.anthropic.claude-opus-4-6-v1``
  is verified. A ``Literal`` pinned to one id would also break the canon's
  *Router obligation* — "when the Opus 5 grant lands it is a one-line
  environment change, never a code change". So the chat ids are plain
  configuration with verified defaults plus a *shape* validator (defect
  ``D-00-002``). The frozen embedding id and dimension keep their ``Literal``
  types, because those are genuinely frozen by contract.
- ``NEXT_PUBLIC_*`` (section 12.9) is not modelled here: those are Amplify
  build-time variables consumed by the Next.js build, never by a Python
  process, and declaring them under ``extra="forbid"`` would invite a
  contributor to set them on the API.
- The gate-harness variables of section 12.10 (``PV_REPO_ROOT``, ``PV_GIT_SHA``,
  ...) are shell-level and owned by ``quality/23_PHASE_GATES.md`` section 2.2.
  The three that a Python process genuinely reads — ``PV_SABOTAGE``,
  ``PV_FORBID_MOCKS``, ``PV_BEDROCK_CLIENT`` — are modelled, as T0.4 requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "CREDENTIAL_NAME_PATTERN",
    "ROLE_DSN_BINDINGS",
    "RoleDsnBinding",
    "Settings",
    "SettingsNotReadyError",
    "SettingsValidationError",
    "env_name_of",
    "get_settings",
    "reset_settings_cache",
]


# ---------------------------------------------------------------------------
# Canon constants
# ---------------------------------------------------------------------------

#: Tier E, verified invocable 2026-08-17. Note the dated suffix: the undated
#: ``anthropic.claude-haiku-4-5`` does not exist in any form on Bedrock.
DEFAULT_EXTRACTION_MODEL_ID: Final = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

#: Tier R *in force*. ``us.anthropic.claude-opus-5`` is the target and its
#: inference profile is ACTIVE, but this account is denied; the grant is
#: pending. Defaulting to a denied model would make every reasoning call fail
#: at run time, so the default is the most capable model this account can
#: actually call. When the grant lands, set ``BEDROCK_REASONING_MODEL_ID`` in
#: the environment — that is the whole change.
DEFAULT_REASONING_MODEL_ID: Final = "us.anthropic.claude-opus-4-6-v1"

#: Frozen embedding model. Invoked by **bare** id: the inference-profile
#: requirement applies to the Anthropic chat models only.
FROZEN_EMBEDDING_MODEL_ID: Final = "amazon.titan-embed-text-v2:0"

# ---------------------------------------------------------------------------
# Gemini defaults. CANONICAL_DECISIONS.md -> Gemini model id canon.
#
# PROBE REQUIRED. Every id below is transcribed from
# https://ai.google.dev/gemini-api/docs/models and NONE has been invoked. The
# last time this pack froze model ids from documentation, all four were wrong:
# `list-foundation-models` returns ids that are not invocable, which is the
# trap D-00-002 fell into. `ops/probes/gemini_probe.py` is what settles these.
#
# There is deliberately no Pro tier: `gemini-3.1-pro-preview` is version 3.1,
# BELOW the hackathon's "3.5 or newer" floor, so both tiers are Flash-class.
# ---------------------------------------------------------------------------
DEFAULT_GEMINI_REASONING_MODEL_ID: Final = "gemini-3.7-flash"
DEFAULT_GEMINI_EXTRACTION_MODEL_ID: Final = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_REASONING_FALLBACK_MODEL_ID: Final = "gemini-3.6-flash"

#: Region-group prefixes that make a Bedrock id an inference-profile id.
INFERENCE_PROFILE_PREFIXES: Final = ("us.", "global.")

#: Field names matching this are credential-shaped and may never carry a
#: non-``None`` default. Wider than the T0.4 acceptance pattern
#: (``SECRET|URL|PASSWORD|TOKEN``) on purpose: ``KEY``, ``DSN`` and
#: ``CREDENTIAL`` are the names the next contributor will reach for.
CREDENTIAL_NAME_PATTERN: Final = re.compile(r"SECRET|URL|PASSWORD|TOKEN|KEY|DSN|CREDENTIAL")


class SettingsValidationError(RuntimeError):
    """A ``pydantic.ValidationError``, re-raised with the offending input dropped.

    ``SecretStr`` masks a credential in ``repr``, ``str``, ``format``,
    ``model_dump``, ``model_dump_json``, ``logging`` and ``deepcopy`` — but a
    ``ValidationError`` raised by a ``model_validator(mode="after")`` attaches
    the **raw input** to each error entry, and that input is the environment
    mapping in plaintext. ``str(exc)`` masks it, so the leak is invisible on
    the paths anyone checks; ``exc.errors()`` and ``exc.json()`` do not, and
    both are what a structured logger and most traceback formatters reach for.

    ``get_settings`` therefore never lets a ``ValidationError`` escape. It
    rebuilds the message from ``(type, loc, msg)`` only — the three fields that
    tell an operator which variable is wrong and why — and drops ``input``,
    ``url`` and ``ctx``. Filed as ``D-00-005``.

    Construction failures on ``Settings()`` called directly still raise
    ``ValidationError``: `G0.7` asserts on that path and on that message, and
    a caller that constructs the object by hand has already chosen to handle
    pydantic's exception type.
    """


class SettingsNotReadyError(RuntimeError):
    """A configured-but-not-yet-existing value was used before its phase.

    Distinct from ``pydantic.ValidationError`` on purpose: a ``ValidationError``
    means the environment is *wrong*, this means the environment is *early*.
    Conflating the two produces the worst possible operator message — "invalid
    configuration" for a value that nothing has created yet.
    """


@dataclass(frozen=True, slots=True)
class RoleDsnBinding:
    """How one SQL role's connection string reaches the process.

    ``ops/40_INFRA_IAC.md`` section 11.3 and ``EXECUTION/70_TASK_PLAN.md`` T0.5:
    the five login users are created in Phase 0 and their URLs are written to
    the ``provenance/db`` secret under five keys. The GRANTs that actually
    differentiate the roles arrive in ``T2.6``.
    """

    role: str
    #: Key inside the ``provenance/db`` Secrets Manager secret.
    secret_key: str
    #: Environment variable that carries it, where the manifest declares one.
    #: ``None`` means the manifest deliberately declares no variable — see
    #: ``pv_agent_reader`` and ``pv_ops_reader`` below.
    env_var: str | None
    #: Attribute on ``Settings``; ``None`` wherever ``env_var`` is ``None``.
    attribute: str | None
    #: Where the value comes from, in words an operator can act on.
    provenance: str
    #: Other environment variable names this role answers to.
    #:
    #: The canonical spelling above is what `ops/40_INFRA_IAC.md` section 11.3
    #: writes into the `provenance/db` secret. The `PV_DB_*` spellings are what
    #: the build machine's `.env` and every runbook actually carry. Both must
    #: resolve, and they must be declared HERE rather than in a second table:
    #: `scripts/seed/db.py` once kept its own, and with both keys present the
    #: seed split across two databases and reported "26 tables checked, 26
    #: match" against a database holding zero evidence rows.
    aliases: tuple[str, ...] = ()


ROLE_DSN_BINDINGS: Final[dict[str, RoleDsnBinding]] = {
    "pv_migrator": RoleDsnBinding(
        role="pv_migrator",
        secret_key="migrator_url",
        env_var="COCKROACH_MIGRATOR_URL",
        attribute="cockroach_migrator_url",
        aliases=("PV_DB_MIGRATOR_CI", "PV_DB_MIGRATOR"),
        provenance=(
            "DDL only; read by CI and ops/migrate.sh, never by the running service. "
            "Login user created in Phase 0 (T0.5); privileges granted in Phase 2 (T2.6)."
        ),
    ),
    "pv_app_reader_writer": RoleDsnBinding(
        role="pv_app_reader_writer",
        secret_key="app_url",
        env_var="COCKROACH_DATABASE_URL",
        attribute="cockroach_database_url",
        aliases=("PV_DB_APP",),
        provenance=(
            "The application read/write pool. Required at construction: a control "
            "plane without it has nothing to serve. Login user created in Phase 0 "
            "(T0.5); privileges granted in Phase 2 (T2.6)."
        ),
    ),
    "pv_kernel_writer": RoleDsnBinding(
        role="pv_kernel_writer",
        secret_key="kernel_url",
        env_var="COCKROACH_KERNEL_URL",
        attribute="cockroach_kernel_url",
        aliases=("PV_DB_KERNEL",),
        provenance=(
            "The only canonical write pool. Login user created in Phase 0 (T0.5); "
            "privileges granted in Phase 2 (T2.6)."
        ),
    ),
    "pv_agent_reader": RoleDsnBinding(
        role="pv_agent_reader",
        secret_key="agent_url",
        env_var=None,
        attribute=None,
        provenance=(
            "Deliberately not an environment variable. The agent runtime reaches "
            "the five _v1 views through the CockroachDB Cloud Managed MCP Server, "
            "whose credential is resolved from MCP_AUTH_SECRET_ARN at run time "
            "(Phase 11). Handing the agent a DSN would put a direct base-table "
            "connection inside the process the view boundary exists to constrain."
        ),
    ),
    "pv_ops_reader": RoleDsnBinding(
        role="pv_ops_reader",
        secret_key="ops_reader_url",
        env_var=None,
        attribute=None,
        provenance=(
            "Human operator role. Read interactively from the provenance/db "
            "secret at the moment of use (Phase 2, T2.6); never injected into a "
            "running process, so no environment variable carries it."
        ),
    ),
}


def env_name_of(field_name: str, field_info: FieldInfo) -> str:
    """The environment variable name a field is read from.

    Every field declares an explicit upper-case ``validation_alias``; the
    fallback exists so a future field that forgets one still reports a name
    rather than raising inside error handling.
    """
    alias = field_info.validation_alias
    return alias if isinstance(alias, str) else field_name.upper()


AppEnv = Literal["local", "demo", "eval", "prod"]
AgentMode = Literal["LIVE", "FIXTURE"]
ActionExecutionMode = Literal["ENABLED", "DISABLED"]
RecipientMode = Literal["DEMO_SINK", "COUNTERPARTY"]
EmbeddingNormalization = Literal["NONE", "L2_UNIT"]

#: Which cloud a deployment actually runs on. This decides which of the
#: provider-specific variables below are REQUIRED -- see
#: ``_platform_requirements``. Before the pivot the object demanded fifteen
#: AWS variables unconditionally, which made the hackathon's mandatory
#: "spin-up instructions" unsatisfiable for anyone without our AWS account.
PlatformName = Literal["aws", "gcp", "local"]


class Settings(BaseSettings):
    """Every environment value the Python side of Provenance reads.

    Construction order of failure, deliberately: unknown variable, missing
    required variable, wrong-shaped value, then the cross-field contracts
    (issuer/pool, frozen embedding contract). An operator reading the traceback
    top-down sees the cheapest fix first.
    """

    model_config = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        case_sensitive=True,
        # No `env_file`. Reading a dotenv here would mean a repository-root
        # .env — which holds a live database credential and is gitignored —
        # being parsed by every test run that happens to have the repository
        # root as its working directory. The shell exports the environment
        # (ops/41_RUNBOOK.md section 2.5); this object only reads it.
        env_file=None,
    )

    # -- 12.1 Core ---------------------------------------------------------
    app_env: AppEnv = Field(validation_alias="APP_ENV")
    #: Defaults to ``gcp``: the pivot is done and that is where the product
    #: ships. ``local`` is the mode a reviewer runs on a laptop with no cloud
    #: account at all.
    pv_platform: PlatformName = Field(default="gcp", validation_alias="PV_PLATFORM")
    app_base_url: str = Field(validation_alias="APP_BASE_URL")
    web_base_url: str = Field(validation_alias="WEB_BASE_URL")
    aws_region: str | None = Field(default=None, validation_alias="AWS_REGION")
    otel_service_name: str = Field(validation_alias="OTEL_SERVICE_NAME")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    # Injected by the image build (Phase 13). Absent on a developer machine,
    # where `git rev-parse HEAD` is available to the caller instead; the
    # version endpoint renders null rather than inventing a sha.
    build_sha: str | None = Field(default=None, validation_alias="BUILD_SHA")
    schema_revision: str | None = Field(default=None, validation_alias="SCHEMA_REVISION")

    # -- 12.2 Authentication ----------------------------------------------
    cognito_user_pool_id: str | None = Field(default=None, validation_alias="COGNITO_USER_POOL_ID")
    cognito_issuer: str | None = Field(default=None, validation_alias="COGNITO_ISSUER")
    cognito_jwks_url: str | None = Field(default=None, validation_alias="COGNITO_JWKS_URL")
    cognito_token_endpoint: str | None = Field(
        default=None, validation_alias="COGNITO_TOKEN_ENDPOINT"
    )
    cognito_web_client_id: str | None = Field(
        default=None, validation_alias="COGNITO_WEB_CLIENT_ID"
    )
    cognito_agent_client_id: str | None = Field(
        default=None, validation_alias="COGNITO_AGENT_CLIENT_ID"
    )
    # An ARN is a reference, not a credential: it is safe to log, and the value
    # behind it is resolved by the App Runner secrets channel (section 8.2).
    cognito_agent_client_secret_arn: str | None = Field(
        default=None, validation_alias="COGNITO_AGENT_CLIENT_SECRET_ARN"
    )
    cognito_worker_client_id: str | None = Field(
        default=None, validation_alias="COGNITO_WORKER_CLIENT_ID"
    )
    cognito_worker_client_secret_arn: str | None = Field(
        default=None, validation_alias="COGNITO_WORKER_CLIENT_SECRET_ARN"
    )
    cognito_judge_group: str | None = Field(default=None, validation_alias="COGNITO_JUDGE_GROUP")

    # -- 12.3 Database -----------------------------------------------------
    cockroach_database_url: SecretStr = Field(validation_alias="COCKROACH_DATABASE_URL")
    # Phase 2 (T2.6). See ROLE_DSN_BINDINGS and require_role_dsns().
    # AliasChoices, not a single string: the canonical spelling is what the
    # secret carries and the `PV_DB_*` spelling is what every `.env` and runbook
    # carries. Resolving only the first meant the kernel pool silently failed to
    # open while the app pool opened fine, and the process started anyway.
    cockroach_kernel_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("COCKROACH_KERNEL_URL", "PV_DB_KERNEL"),
    )
    cockroach_migrator_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "COCKROACH_MIGRATOR_URL", "PV_DB_MIGRATOR_CI", "PV_DB_MIGRATOR"
        ),
    )
    provenance_test_db_url: SecretStr | None = Field(
        default=None, validation_alias="PROVENANCE_TEST_DB_URL"
    )
    cockroach_pool_min: int = Field(default=2, ge=0, validation_alias="COCKROACH_POOL_MIN")
    cockroach_pool_max: int = Field(default=10, ge=1, validation_alias="COCKROACH_POOL_MAX")
    cockroach_statement_timeout_ms: int = Field(
        default=15000, ge=1, validation_alias="COCKROACH_STATEMENT_TIMEOUT_MS"
    )
    provenance_keep_test_dbs: bool = Field(
        default=False, validation_alias="PROVENANCE_KEEP_TEST_DBS"
    )

    # -- 12.4 Cryptographic material --------------------------------------
    # All four are required with no default. A signing key that defaults is a
    # signing key that verifies forged proofs on a misconfigured deploy.
    provenance_capability_hmac_key: SecretStr = Field(
        validation_alias="PROVENANCE_CAPABILITY_HMAC_KEY"
    )
    provenance_capability_hmac_kid: str = Field(validation_alias="PROVENANCE_CAPABILITY_HMAC_KID")
    cursor_hmac_key: SecretStr = Field(validation_alias="CURSOR_HMAC_KEY")
    ingest_alias_hmac_key: SecretStr = Field(validation_alias="INGEST_ALIAS_HMAC_KEY")

    # -- 12.5 Storage ------------------------------------------------------
    s3_artifact_bucket: str | None = Field(default=None, validation_alias="S3_ARTIFACT_BUCKET")
    s3_inbound_bucket: str | None = Field(default=None, validation_alias="S3_INBOUND_BUCKET")
    s3_kms_key_arn: str | None = Field(default=None, validation_alias="S3_KMS_KEY_ARN")
    max_artifact_bytes: int = Field(default=20971520, ge=1, validation_alias="MAX_ARTIFACT_BYTES")
    # These two are required, with no default, only because their names contain
    # `URL` and the T0.4/G0.7 rule is mechanical: no field whose name contains
    # SECRET, URL, PASSWORD or TOKEN may carry a non-None default. They are
    # pre-signed URL lifetimes, not credentials — but carving an exemption into
    # the guard is how a rule like this dies, so the two fields conform instead.
    # Section 12.5 gives the deployed values: 900 and 300.
    upload_url_ttl_seconds: int = Field(ge=1, validation_alias="UPLOAD_URL_TTL_SECONDS")
    download_url_ttl_seconds: int = Field(ge=1, validation_alias="DOWNLOAD_URL_TTL_SECONDS")

    # -- 12.6 Email --------------------------------------------------------
    ses_ingest_domain: str | None = Field(default=None, validation_alias="SES_INGEST_DOMAIN")
    ses_from_address: str | None = Field(default=None, validation_alias="SES_FROM_ADDRESS")
    ses_notification_from_address: str | None = Field(
        default=None, validation_alias="SES_NOTIFICATION_FROM_ADDRESS"
    )
    ses_configuration_set: str | None = Field(
        default=None, validation_alias="SES_CONFIGURATION_SET"
    )
    ses_demo_sink_domain: str | None = Field(default=None, validation_alias="SES_DEMO_SINK_DOMAIN")
    action_recipient_mode: RecipientMode = Field(
        default="DEMO_SINK", validation_alias="ACTION_RECIPIENT_MODE"
    )
    pv_action_execution_mode: ActionExecutionMode = Field(
        default="ENABLED", validation_alias="PV_ACTION_EXECUTION_MODE"
    )
    # Raw string, not list[str]: pydantic-settings JSON-decodes complex types
    # from the environment, and ops/41_RUNBOOK.md section 2.5 writes a bare
    # comma-separated address list. Parsed by `action_allowlist_addresses`.
    # Defaults to empty, which means no recipient is allowed — fail closed.
    pv_action_allowlist: str = Field(default="", validation_alias="PV_ACTION_ALLOWLIST")

    # -- 12.7 Events and scheduling ---------------------------------------
    eventbridge_bus_name: str | None = Field(default=None, validation_alias="EVENTBRIDGE_BUS_NAME")
    eventbridge_scheduler_group: str | None = Field(
        default=None, validation_alias="EVENTBRIDGE_SCHEDULER_GROUP"
    )
    sqs_dlq_url: str | None = Field(default=None, validation_alias="SQS_DLQ_URL")
    # Phase 10 creates the scheduler plumbing; Phase 13 supplies the ARNs.
    scheduler_target_lambda_arn: str | None = Field(
        default=None, validation_alias="SCHEDULER_TARGET_LAMBDA_ARN"
    )
    scheduler_role_arn: str | None = Field(default=None, validation_alias="SCHEDULER_ROLE_ARN")
    scheduler_dlq_arn: str | None = Field(default=None, validation_alias="SCHEDULER_DLQ_ARN")
    outbox_sweep_batch_size: int = Field(
        default=50, ge=1, validation_alias="OUTBOX_SWEEP_BATCH_SIZE"
    )

    # -- 12.7b Google platform ---------------------------------------------
    #: The AI Studio key for the Gemini Developer API. The hackathon rules name
    #: this path explicitly ("accessed through Gemini API or Vertex AI"), so no
    #: service account, ADC or IAM binding is involved.
    google_api_key: SecretStr | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    gcs_artifact_bucket: str | None = Field(default=None, validation_alias="GCS_ARTIFACT_BUCKET")
    google_cloud_project: str | None = Field(default=None, validation_alias="GOOGLE_CLOUD_PROJECT")
    #: Cloud Run should sit in ``us-east4``: CockroachDB Cloud stays on AWS
    #: ``us-east-1``, and both are Northern Virginia, so the cross-cloud hop
    #: stays in single-digit milliseconds instead of seventy-plus.
    google_cloud_region: str | None = Field(default=None, validation_alias="GOOGLE_CLOUD_REGION")
    gemini_reasoning_model_id: str = Field(
        default=DEFAULT_GEMINI_REASONING_MODEL_ID,
        validation_alias="GEMINI_REASONING_MODEL_ID",
    )
    gemini_extraction_model_id: str = Field(
        default=DEFAULT_GEMINI_EXTRACTION_MODEL_ID,
        validation_alias="GEMINI_EXTRACTION_MODEL_ID",
    )
    gemini_reasoning_fallback_model_id: str = Field(
        default=DEFAULT_GEMINI_REASONING_FALLBACK_MODEL_ID,
        validation_alias="GEMINI_REASONING_FALLBACK_MODEL_ID",
    )
    #: Names an entry in ``provenance_contracts.embedding_profile``. A profile
    #: binds model id, width, version and the normalization obligation into one
    #: value, so the incoherent combinations (Titan id at 1536, Gemini vectors
    #: in a VECTOR(1024) column) cannot be spelled.

    # -- 12.8 Models, retrieval, agents ------------------------------------
    bedrock_extraction_model_id: str = Field(
        default=DEFAULT_EXTRACTION_MODEL_ID, validation_alias="BEDROCK_EXTRACTION_MODEL_ID"
    )
    bedrock_reasoning_model_id: str = Field(
        default=DEFAULT_REASONING_MODEL_ID, validation_alias="BEDROCK_REASONING_MODEL_ID"
    )
    bedrock_embedding_model_id: Literal["amazon.titan-embed-text-v2:0"] = Field(
        default=FROZEN_EMBEDDING_MODEL_ID, validation_alias="BEDROCK_EMBEDDING_MODEL_ID"
    )
    embedding_dimensions: Literal[1024] = Field(
        default=1024, validation_alias="EMBEDDING_DIMENSIONS"
    )
    embedding_version: Literal["v1"] = Field(default="v1", validation_alias="EMBEDDING_VERSION")
    embedding_normalization: EmbeddingNormalization = Field(
        default="NONE", validation_alias="EMBEDDING_NORMALIZATION"
    )
    embedding_cache_table: str = Field(
        default="embedding_cache", validation_alias="EMBEDDING_CACHE_TABLE"
    )
    vector_search_beam_size: int = Field(
        default=32, ge=1, validation_alias="VECTOR_SEARCH_BEAM_SIZE"
    )
    # Phase 13 writes this back into App Runner (40_INFRA_IAC section 2.4 step 9).
    agentcore_runtime_arn: str | None = Field(
        default=None, validation_alias="AGENTCORE_RUNTIME_ARN"
    )
    agentcore_qualifier: str = Field(default="DEFAULT", validation_alias="AGENTCORE_QUALIFIER")
    # Phase 11.
    mcp_server_url: str | None = Field(default=None, validation_alias="MCP_SERVER_URL")
    mcp_auth_secret_arn: str | None = Field(default=None, validation_alias="MCP_AUTH_SECRET_ARN")
    pv_mcp_enabled: bool = Field(default=True, validation_alias="PV_MCP_ENABLED")
    pv_agent_mode: AgentMode = Field(default="LIVE", validation_alias="PV_AGENT_MODE")

    # -- 12.10 The three gate switches a Python process reads --------------
    pv_sabotage: str | None = Field(default=None, validation_alias="PV_SABOTAGE")
    pv_forbid_mocks: bool = Field(default=False, validation_alias="PV_FORBID_MOCKS")
    pv_bedrock_client: str | None = Field(default=None, validation_alias="PV_BEDROCK_CLIENT")

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def _coerce_embedding_dimensions(cls, value: object) -> object:
        """Accept ``EMBEDDING_DIMENSIONS=1024`` from the environment.

        Environment values arrive as strings and pydantic does **not** coerce a
        string to an ``int`` ``Literal`` — ``Literal[1024]`` rejects ``"1024"``
        with ``literal_error``. Without this, the runbook's own .env template
        would fail to start. A non-numeric string falls through unchanged and
        is rejected by the ``Literal``, so this widens the accepted spelling,
        never the accepted value.
        """
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return value

    @field_validator("bedrock_extraction_model_id", "bedrock_reasoning_model_id")
    @classmethod
    def _chat_model_ids_must_be_inference_profiles(cls, value: str, info: ValidationInfo) -> str:
        """Reject a bare ``anthropic.*`` chat model id — defect ``D-00-002``.

        Bedrock refuses a bare Anthropic chat model id with
        ``ValidationException: Invocation ... with on-demand throughput isn't
        supported. Retry your request with the ID or ARN of an inference
        profile``. That failure surfaces at the first model call, inside a
        graph node, as an opaque client error. Here it surfaces at start-up
        with the fix in the message. This validator is the thing that stops
        the defect recurring, so it is deliberately shape-based rather than a
        list of known-good ids: a new Claude generation must pass without a
        code change (``CANONICAL_DECISIONS.md`` -> Bedrock model id canon,
        *Router obligation*).
        """
        if "anthropic." not in value:
            # A non-Anthropic Bedrock chat model has different rules; this
            # validator has nothing true to say about it.
            return value
        if value.startswith(INFERENCE_PROFILE_PREFIXES):
            return value

        env_name = _ENV_NAMES.get(info.field_name or "", (info.field_name or "").upper())
        prefixes = " or ".join(repr(prefix) for prefix in INFERENCE_PROFILE_PREFIXES)
        suggestion = "us." + value[value.index("anthropic.") :]
        raise ValueError(
            f"{env_name}={value!r} is a bare Bedrock model id. Anthropic chat models on "
            "Bedrock are invocable only through an inference profile id, which carries a "
            f"region-group prefix — {prefixes}. Use {suggestion!r}. A bare id returns "
            "ValidationException: \"Invocation ... with on-demand throughput isn't "
            'supported. Retry your request with the ID or ARN of an inference profile". '
            "Defect D-00-002; see CANONICAL_DECISIONS.md -> 'Bedrock model id canon "
            "(frozen 2026-08-17)'."
        )

    @model_validator(mode="after")
    def _platform_requirements(self) -> Settings:
        """Each platform requires its own variables, and only its own.

        This is where "raises on missing" (``T0.4``) now lives for
        provider-specific configuration. The requirement did not weaken -- it
        became conditional. A ``gcp`` deployment missing ``GOOGLE_API_KEY``
        fails at startup exactly as an ``aws`` deployment missing
        ``COGNITO_ISSUER`` always did.

        Giving these fields defaults instead would have traded a startup
        failure for a runtime one: ``S3_ARTIFACT_BUCKET=""`` constructs
        happily and fails at the first upload, inside a request.
        """
        required: dict[str, object] = {}
        if self.pv_platform == "aws":
            required = {
                "AWS_REGION": self.aws_region,
                "COGNITO_USER_POOL_ID": self.cognito_user_pool_id,
                "COGNITO_ISSUER": self.cognito_issuer,
                "COGNITO_JWKS_URL": self.cognito_jwks_url,
                "COGNITO_TOKEN_ENDPOINT": self.cognito_token_endpoint,
                "COGNITO_WEB_CLIENT_ID": self.cognito_web_client_id,
                "COGNITO_AGENT_CLIENT_ID": self.cognito_agent_client_id,
                "COGNITO_WORKER_CLIENT_ID": self.cognito_worker_client_id,
                "S3_ARTIFACT_BUCKET": self.s3_artifact_bucket,
            }
        elif self.pv_platform == "gcp":
            required = {
                "GOOGLE_API_KEY": self.google_api_key,
                "GCS_ARTIFACT_BUCKET": self.gcs_artifact_bucket,
            }
        # `local` requires nothing provider-specific. It still requires every
        # core variable, because there is no version of this product without
        # canonical memory.

        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError(
                f"PV_PLATFORM={self.pv_platform!r} requires "
                + ", ".join(missing)
                + " -- unset. Set them, or declare a different PV_PLATFORM."
            )
        return self

    @model_validator(mode="after")
    def _issuer_matches_pool(self) -> Settings:
        """Reproduced from ``ops/40_INFRA_IAC.md`` section 12.11.

        Only meaningful on ``aws``. On another platform the Cognito fields are
        absent by design, and checking a relationship between two absent values
        would raise for a deployment that never claimed to use Cognito.
        """
        if self.pv_platform != "aws":
            return self
        if not (self.aws_region and self.cognito_user_pool_id):
            return self
        assert self.cognito_issuer is not None
        assert self.cognito_jwks_url is not None
        expected = (
            f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"
        )
        if self.cognito_issuer.rstrip("/") != expected:
            raise ValueError(f"COGNITO_ISSUER must be {expected}")
        if not self.cognito_jwks_url.startswith(expected):
            raise ValueError(f"COGNITO_JWKS_URL must begin with {expected}")
        return self

    @model_validator(mode="after")
    def _embedding_contract_frozen(self) -> Settings:
        """Reproduced from ``ops/40_INFRA_IAC.md`` section 12.11.

        Honest note: with the ``Literal`` types above, none of these three
        branches can fire today — the field types reject the bad value first.
        It is kept because the ``Literal`` on a model id is the thing most
        likely to be widened by a future task (a second embedding model, a
        second version), and this validator is what still fails the build when
        someone widens the type without widening the ``VECTOR(1024)`` column.
        """
        if self.embedding_dimensions != 1024:
            raise ValueError("EMBEDDING_DIMENSIONS is frozen at 1024")
        if self.bedrock_embedding_model_id != FROZEN_EMBEDDING_MODEL_ID:
            raise ValueError("BEDROCK_EMBEDDING_MODEL_ID is frozen")
        if self.embedding_version != "v1":
            raise ValueError("EMBEDDING_VERSION is frozen at v1")
        return self

    @model_validator(mode="after")
    def _pool_bounds_are_ordered(self) -> Settings:
        if self.cockroach_pool_max < self.cockroach_pool_min:
            raise ValueError(
                f"COCKROACH_POOL_MAX ({self.cockroach_pool_max}) is below "
                f"COCKROACH_POOL_MIN ({self.cockroach_pool_min})"
            )
        return self

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------

    @property
    def action_allowlist_addresses(self) -> tuple[str, ...]:
        """``PV_ACTION_ALLOWLIST`` as addresses. Empty means nothing is allowed."""
        return tuple(part.strip() for part in self.pv_action_allowlist.split(",") if part.strip())

    @property
    def fixture_mode(self) -> bool:
        """True when model outputs are replayed. Rendered by ``GET /v1/version``."""
        return self.pv_agent_mode == "FIXTURE"

    # ------------------------------------------------------------------
    # Point-of-use requirements for values that do not exist yet
    # ------------------------------------------------------------------

    def dsn_for_role(self, role: str) -> SecretStr:
        """The DSN for one of the five SQL roles, or raise naming its source."""
        try:
            binding = ROLE_DSN_BINDINGS[role]
        except KeyError:
            known = ", ".join(sorted(ROLE_DSN_BINDINGS))
            raise ValueError(f"unknown SQL role {role!r}; the roles are: {known}") from None

        if binding.env_var is None or binding.attribute is None:
            raise SettingsNotReadyError(
                f"{binding.role} has no DSN in the environment by design. "
                f"{binding.provenance} Its credential lives in the provenance/db secret "
                f"under the key {binding.secret_key!r}."
            )

        value: SecretStr | None = getattr(self, binding.attribute)
        if value is None:
            raise SettingsNotReadyError(self._missing_role_message(binding))
        return value

    def require_role_dsns(self, *roles: str) -> None:
        """Assert that each named role's DSN is present, or raise naming the phase.

        Called by the consumer at the moment it opens a pool — not at
        construction — so a Phase 0 or Phase 1 process that never touches the
        kernel pool still starts.
        """
        missing: list[str] = []
        for role in roles:
            try:
                self.dsn_for_role(role)
            except SettingsNotReadyError as exc:
                missing.append(str(exc))
        if missing:
            raise SettingsNotReadyError("\n".join(missing))

    @staticmethod
    def _missing_role_message(binding: RoleDsnBinding) -> str:
        return (
            f"{binding.env_var} is not set, so the {binding.role} connection is unavailable. "
            f"It is issued in Phase 0 by T0.5 (ccloud cluster user create) into the "
            f"provenance/db secret under the key {binding.secret_key!r}, and the role only "
            f"becomes usable in Phase 2 (T2.6), which creates the GRANTs. {binding.provenance}"
        )

    def require_agent_runtime_targets(self) -> None:
        """Assert the AgentCore and MCP endpoints exist before invoking them."""
        missing: list[str] = []
        if self.agentcore_runtime_arn is None:
            missing.append(
                "AGENTCORE_RUNTIME_ARN is not set. It is created in Phase 13 (deploy) and "
                "injected into App Runner by the `update-service` step "
                "(ops/40_INFRA_IAC.md section 2.4 step 9), because the API and the agent "
                "runtime reference each other and the cycle is broken at deploy time."
            )
        if self.pv_mcp_enabled and self.mcp_server_url is None:
            missing.append(
                "MCP_SERVER_URL is not set while PV_MCP_ENABLED is true. The CockroachDB "
                "Cloud Managed MCP Server endpoint arrives in Phase 11; set "
                "PV_MCP_ENABLED=false to take the control-plane read path instead."
            )
        if self.pv_mcp_enabled and self.mcp_auth_secret_arn is None:
            missing.append(
                "MCP_AUTH_SECRET_ARN is not set while PV_MCP_ENABLED is true. It carries the "
                "pv_agent_reader credential and is created in Phase 11."
            )
        if missing:
            raise SettingsNotReadyError("\n".join(missing))


#: field name -> environment variable name, computed once. Used by validators,
#: which must report the variable an operator sets, not the Python attribute.
_ENV_NAMES: Final[dict[str, str]] = {
    field_name: env_name_of(field_name, field_info)
    for field_name, field_info in Settings.model_fields.items()
}


def _assert_no_credential_defaults(model: type[BaseSettings]) -> None:
    """Fail at import if a credential-shaped field carries a default.

    T0.4: "add a model validator that fails if any field name matching the
    credential pattern carries a default — so the rule is enforced against
    future fields, not just today's". It is checked here, at class-definition
    time, rather than per instance: whether a field has a default is a property
    of the class, and a check that runs on construction can be reached only by
    a process that already built the object.
    """
    offenders: list[str] = []
    for field_name, field_info in model.model_fields.items():
        env_name = env_name_of(field_name, field_info)
        if not CREDENTIAL_NAME_PATTERN.search(env_name):
            continue
        if field_info.is_required():
            continue
        if field_info.get_default() is not None:
            offenders.append(env_name)

    if offenders:
        raise RuntimeError(
            "credential-shaped settings fields must not carry a default; offending "
            f"fields: {', '.join(sorted(offenders))}. A defaulted credential is how a "
            "service starts against the wrong database. Make the field required, or "
            "Optional with a None default plus a require_*() check at the point of use."
        )


_assert_no_credential_defaults(Settings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings instance.

    Cached so that the environment is read once, at first use, and the same
    object is shared: two components disagreeing about configuration because
    one of them re-read a mutated environment is a class of bug worth removing
    outright. Tests that change the environment call ``reset_settings_cache``.
    """
    # `type: ignore[call-arg]`: mypy --strict is run without the pydantic
    # plugin (see the root pyproject.toml [tool.mypy] block, which is
    # Integrator-owned), so it types BaseSettings.__init__ as requiring every
    # field by keyword. Supplying them here is exactly what this object exists
    # to avoid: the values come from the environment. If the pydantic plugin is
    # ever enabled, --strict will flag this ignore as unused, which is the
    # right prompt to delete it.
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        # See SettingsValidationError. `from None` is deliberate: chaining
        # would put the original exception — carrying the plaintext `input`
        # in its `errors()` — straight back into the traceback this exists to
        # keep it out of.
        raise SettingsValidationError(_redacted_validation_message(exc)) from None


def _redacted_validation_message(exc: ValidationError) -> str:
    """Rebuild a ``ValidationError`` message from its safe fields only.

    ``type``, ``loc`` and ``msg`` name the variable and say what is wrong with
    it. ``input``, ``url`` and ``ctx`` are dropped: ``input`` is the raw value,
    and for a ``mode="after"`` model validator it is the whole environment
    mapping including every credential.
    """
    lines = [f"{exc.error_count()} validation error(s) for {exc.title}"]
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(str(part) for part in error["loc"]) or "<model>"
        lines.append(f"  {location}: {error['msg']} [type={error['type']}]")
    lines.append(
        "Values are omitted on purpose: a validation failure reports which variable is "
        "wrong, never what it was set to. See provenance_contracts.settings."
        "SettingsValidationError."
    )
    return "\n".join(lines)


def reset_settings_cache() -> None:
    """Drop the cached instance. Tests only; nothing in production calls this."""
    get_settings.cache_clear()
