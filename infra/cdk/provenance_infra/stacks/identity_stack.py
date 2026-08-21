"""Stack 2 -- ``PvIdentityStack``.

Takes no ``FoundationExports``: nothing in Cognito uses the artifact CMK or the
alert topic, and inventing a dependency to match the deploy order in section 2.4
would make ``cdk deploy --all`` serialise two stacks that are genuinely
independent.

The Cognito user pool, the hosted UI, the ``provenance`` resource server with
its seven closed scopes, the three app clients, the ``provenance-judges`` group,
and the post-confirmation provisioning Lambda.

Owner of the contract: ``specs/15_API_SPEC.md`` section 2. This stack deploys it
and nothing more (40_INFRA_IAC.md section 3).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra import workers as wk
from provenance_infra.config import PvConfig
from provenance_infra.props import IdentityExports
from provenance_infra.removal import stateful_removal


class PvIdentityStack(Stack):
    """Cognito, and the one Lambda that provisions a user's first three rows."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=cfg.USER_POOL_NAME,
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=False)
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(3),
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # AUDIT rather than ENFORCED: enforced mode can challenge a sign-in
            # during a recorded demo, and a blocked login at 0:05 of a
            # three-minute video is a worse outcome than an unmitigated
            # credential-stuffing risk on a five-user pool. Audit mode still
            # populates the risk telemetry.
            feature_plan=cognito.FeaturePlan.PLUS,
            standard_threat_protection_mode=cognito.StandardThreatProtectionMode.AUDIT_ONLY,
            email=cognito.UserPoolEmail.with_cognito(),
            removal_policy=stateful_removal(config),
            deletion_protection=not config.teardown,
        )
        self.user_pool = pool

        # This exact host appears in specs/15_API_SPEC.md section 1.1 and in
        # NEXT_PUBLIC_COGNITO_DOMAIN; changing it breaks the documented token
        # endpoint.
        cognito.UserPoolDomain(
            self,
            "HostedUi",
            user_pool=pool,
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=cfg.HOSTED_UI_PREFIX),
        )

        cognito.CfnUserPoolGroup(
            self,
            "JudgesGroup",
            user_pool_id=pool.user_pool_id,
            group_name=cfg.JUDGES_GROUP_NAME,
            description=(
                "Grants judge_mode_enabled on the HumanPrincipal. " "No cross-user visibility."
            ),
            precedence=10,
        )

        scope_defs = {
            name: cognito.ResourceServerScope(scope_name=name, scope_description=description)
            for name, description in cfg.SCOPES.items()
        }
        resource_server = pool.add_resource_server(
            "ApiResourceServer",
            # Scopes render as ``provenance.memory/read``.
            identifier=cfg.RESOURCE_SERVER_ID,
            user_pool_resource_server_name="provenance-api",
            scopes=list(scope_defs.values()),
        )

        def rs(name: str) -> cognito.OAuthScope:
            return cognito.OAuthScope.resource_server(resource_server, scope_defs[name])

        # ---- 1. provenance-web: human, public client, auth code + PKCE, NO secret.
        #
        # PKCE is not a Cognito toggle. Because ``generate_secret`` is false and
        # the flow is authorizationCodeGrant, Cognito *requires* code_challenge
        # with S256. The infrastructure guarantee is the absence of a client
        # secret; a deployment that generated one here would silently permit a
        # non-PKCE flow, which is why this is load-bearing rather than cosmetic.
        self.web_client = pool.add_client(
            "WebClient",
            user_pool_client_name=cfg.WEB_CLIENT_NAME,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=False,
                custom=False,
                admin_user_password=False,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=False,
                    client_credentials=False,
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                    rs("memory/read"),
                ],
                callback_urls=[
                    f"{config.web_base_url}/auth/callback",
                    "http://localhost:3000/auth/callback",
                ],
                logout_urls=[f"{config.web_base_url}/", "http://localhost:3000/"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
            access_token_validity=Duration.minutes(60),
            id_token_validity=Duration.minutes(60),
            refresh_token_validity=Duration.days(30),
            auth_session_validity=Duration.minutes(3),
            read_attributes=cognito.ClientAttributes().with_standard_attributes(
                email=True, email_verified=True
            ),
            write_attributes=cognito.ClientAttributes().with_standard_attributes(email=False),
        )

        # ---- 2. provenance-agent-runtime: machine, client credentials.
        #
        # It holds ``ingest/write`` deliberately, per specs/15_API_SPEC.md
        # section 9.0: section 9.4 is the one place the agent must register
        # evidence, and the narrowing is done by CLIENT_CAPABILITY_MATRIX, which
        # permits this client to present only an AGENT_RUN capability. The scope
        # alone does not let it reach section 9.1, which demands an INGEST_ALIAS
        # capability the agent client may never present. The section 2.1 scope
        # table lists three scopes; 9.0 is the more specific statement and wins.
        # That tension is 40_INFRA_IAC.md section 15.2 and is a permission, so it
        # is the most consequential ambiguity in the pack.
        #
        # It holds NO action/execute and NO outbox/dispatch. The graph that
        # writes a dispute letter is structurally incapable of sending it.
        self.agent_client = pool.add_client(
            "AgentRuntimeClient",
            user_pool_client_name=cfg.AGENT_CLIENT_NAME,
            generate_secret=True,
            auth_flows=cognito.AuthFlow(),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=False,
                    implicit_code_grant=False,
                    client_credentials=True,
                ),
                scopes=[
                    rs("memory/read"),
                    rs("memory/propose"),
                    rs("action/propose"),
                    rs("ingest/write"),
                ],
                callback_urls=[],
            ),
            access_token_validity=Duration.minutes(60),
            enable_token_revocation=True,
        )

        # ---- 3. provenance-workers: machine, client credentials.
        self.worker_client = pool.add_client(
            "WorkersClient",
            user_pool_client_name=cfg.WORKER_CLIENT_NAME,
            generate_secret=True,
            auth_flows=cognito.AuthFlow(),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=False,
                    implicit_code_grant=False,
                    client_credentials=True,
                ),
                scopes=[
                    rs("ingest/write"),
                    rs("trigger/evaluate"),
                    rs("action/execute"),
                    rs("outbox/dispatch"),
                    rs("memory/read"),
                ],
                callback_urls=[],
            ),
            access_token_validity=Duration.minutes(60),
            enable_token_revocation=True,
        )

        self._post_confirmation(pool)

    # ----------------------------------------------------------------------
    def _post_confirmation(self, pool: cognito.UserPool) -> None:
        """specs/15_API_SPEC.md section 2.5 forbids auto-creating users from an
        API call, so provisioning is a pool trigger rather than an endpoint.

        The two secrets are imported **by name**, not passed as props.
        40_INFRA_IAC.md section 2.4 deploys Identity (step 2) before Data
        (step 3), while section 3.4 needs Data's secret ARNs -- taking the ARNs
        as props would invert that order. Secret *names* are canon
        (``provenance/db``, ``provenance/crypto``) and the secret only has to
        exist at runtime, so a by-name import satisfies both sections. The grant
        renders as ``...:secret:provenance/db-??????``, which is the same
        six-character wildcard the written policies in section 7.2 use, and is
        inherent to Secrets Manager naming rather than a widening. (D-13-001.)
        """
        spec = wk.COGNITO_POST_CONFIRMATION
        db_secret = secretsmanager.Secret.from_secret_name_v2(self, "DbSecretByName", cfg.SECRET_DB)
        crypto_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "CryptoSecretByName", cfg.SECRET_CRYPTO
        )

        log_group = logs.LogGroup(
            self,
            "PostConfirmationLogGroup",
            log_group_name=f"/provenance/{spec.module.replace('_', '-')}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=stateful_removal(self.config),
        )

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
            tracing=lambda_.Tracing.ACTIVE,
            logging_format=lambda_.LoggingFormat.JSON,
            application_log_level_v2=lambda_.ApplicationLogLevel.INFO,
            system_log_level_v2=lambda_.SystemLogLevel.WARN,
            log_group=log_group,
            environment={
                "APP_ENV": "prod",
                "COCKROACH_DATABASE_URL_SECRET_ARN": db_secret.secret_arn,
                "INGEST_ALIAS_HMAC_KEY_ARN": crypto_secret.secret_arn,
                "SES_INGEST_DOMAIN": self.config.ingest_host,
                "OTEL_SERVICE_NAME": spec.function_name,
                "POWERTOOLS_SERVICE_NAME": "provenance",
            },
        )
        db_secret.grant_read(fn)
        crypto_secret.grant_read(fn)
        pool.add_trigger(cognito.UserPoolOperation.POST_CONFIRMATION, fn)
        self.post_confirmation = fn

    # ----------------------------------------------------------------------
    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{cfg.REGION}.amazonaws.com/{self.user_pool.user_pool_id}"

    @property
    def exports(self) -> IdentityExports:
        return IdentityExports(
            user_pool=self.user_pool,
            web_client=self.web_client,
            agent_client=self.agent_client,
            worker_client=self.worker_client,
            issuer=self.issuer,
            jwks_url=f"{self.issuer}/.well-known/jwks.json",
            hosted_ui_domain=self.config.hosted_ui_domain,
        )
