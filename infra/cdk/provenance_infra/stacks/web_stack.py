"""Stack 9 -- ``PvWebStack``.

Amplify Hosting for deployment unit ``web``: the app, the ``main`` branch, the
domain association, the security headers, and the public build-time environment
(40_INFRA_IAC.md section 10).

``apps/web`` is the only TypeScript surface in the repository; everything else,
this stack included, is Python.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_amplify as amplify
from aws_cdk import aws_iam as iam
from constructs import Construct

from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.props import IdentityExports


def _custom_headers(config: PvConfig) -> str:
    """The Amplify custom-headers document, as YAML text.

    The CSP ``connect-src`` list is the API origin, the Cognito hosted UI, the
    Cognito IdP endpoint (for JWKS and token refresh), and the S3 artifact
    bucket (for the direct pre-signed PUT). Omitting the bucket breaks uploads in
    a way that looks like a CORS bug in the browser console; omitting the IdP
    host breaks login silently on token refresh.
    """
    connect_src = " ".join(
        [
            "'self'",
            config.api_base_url,
            f"https://{config.hosted_ui_domain}",
            f"https://cognito-idp.{cfg.REGION}.amazonaws.com",
            f"https://{cfg.ARTIFACT_BUCKET_NAME}.s3.{cfg.REGION}.amazonaws.com",
        ]
    )
    csp = (
        "default-src 'self'; "
        f"connect-src {connect_src}; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    lines = [
        "customHeaders:",
        '  - pattern: "**"',
        "    headers:",
        "      - key: Strict-Transport-Security",
        '        value: "max-age=63072000; includeSubDomains; preload"',
        "      - key: X-Content-Type-Options",
        '        value: "nosniff"',
        "      - key: X-Frame-Options",
        '        value: "DENY"',
        "      - key: Referrer-Policy",
        '        value: "strict-origin-when-cross-origin"',
        "      - key: Content-Security-Policy",
        f'        value: "{csp}"',
    ]
    return "\n".join(lines)


class PvWebStack(Stack):
    """Deployment unit ``web``."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        identity: IdentityExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        amplify_role = iam.Role(
            self,
            "AmplifyServiceRole",
            role_name="provenance-amplify-service-role",
            assumed_by=iam.ServicePrincipal(
                "amplify.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Amplify build and deploy role for provenance-web",
        )
        # Amplify writes its own build logs. Scoped to the Amplify log-group
        # namespace rather than granted on ``*``.
        amplify_role.add_to_policy(
            iam.PolicyStatement(
                sid="AmplifyBuildLogs",
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    f"arn:{self.partition}:logs:{cfg.REGION}:{self.account}:"
                    f"log-group:/aws/amplify/*"
                ],
            )
        )

        environment_variables = {
            "NEXT_PUBLIC_API_BASE_URL": config.api_base_url,
            "NEXT_PUBLIC_AWS_REGION": cfg.REGION,
            "NEXT_PUBLIC_COGNITO_USER_POOL_ID": identity.user_pool.user_pool_id,
            "NEXT_PUBLIC_COGNITO_WEB_CLIENT_ID": identity.web_client.user_pool_client_id,
            "NEXT_PUBLIC_COGNITO_DOMAIN": identity.hosted_ui_domain,
            "NEXT_PUBLIC_COGNITO_SCOPES": "openid email profile provenance.memory/read",
            "NEXT_PUBLIC_BUILD_SHA": config.git_sha,
            "AMPLIFY_DIFF_DEPLOY": "false",
            "_LIVE_UPDATES": (
                '[{"name":"Node.js version","pkg":"node","type":"nvm","version":"20"}]'
            ),
        }

        app = amplify.CfnApp(
            self,
            "WebApp",
            name=cfg.AMPLIFY_APP_NAME,
            description="Provenance Next.js experience plane",
            # WEB_COMPUTE is required, not preferred: the app has server
            # components and route handlers, and WEB would build a static export
            # that cannot render them.
            platform="WEB_COMPUTE",
            # The repository connection carries a token. It is never in a
            # template: ``pv:web_repository`` names the repository, and the
            # access token is attached out of band with
            # ``aws amplify update-app --access-token``.
            repository=config.web_repository,
            iam_service_role=amplify_role.role_arn,
            enable_branch_auto_deletion=False,
            custom_headers=_custom_headers(config),
            environment_variables=[
                amplify.CfnApp.EnvironmentVariableProperty(name=name, value=value)
                for name, value in environment_variables.items()
            ],
        )

        amplify.CfnBranch(
            self,
            "MainBranch",
            app_id=app.attr_app_id,
            branch_name="main",
            stage="PRODUCTION",
            enable_auto_build=True,
            framework="Next.js - SSR",
            enable_performance_mode=False,
        )

        if config.custom_domains_enabled:
            amplify.CfnDomain(
                self,
                "WebDomain",
                app_id=app.attr_app_id,
                domain_name=config.root_domain,
                sub_domain_settings=[
                    amplify.CfnDomain.SubDomainSettingProperty(
                        prefix=config.web_host.split(".", 1)[0], branch_name="main"
                    )
                ],
                enable_auto_sub_domain=False,
            )
