"""``PvWebStack``: Amplify Hosting for deployment unit ``web``.

The CSP assertions matter because both omissions fail in ways that do not look
like CSP: dropping the artifact bucket breaks uploads with what reads as a CORS
error, and dropping the Cognito IdP host breaks login silently on token refresh.
"""

from __future__ import annotations

from typing import Any

import pytest
from pv_cdk_testing import flatten, key_values, resources_of


def _app(template: dict[str, Any]) -> dict[str, Any]:
    apps = resources_of(template, "AWS::Amplify::App")
    assert len(apps) == 1
    return next(iter(apps.values()))["Properties"]


def test_the_platform_is_web_compute_not_static_export(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Required, not preferred: the app has server components and route
    handlers, and ``WEB`` would build a static export that cannot render them.
    """
    assert _app(template_json["PvWebStack"])["Platform"] == "WEB_COMPUTE"


def test_one_production_branch_declared_as_nextjs_ssr(
    template_json: dict[str, dict[str, Any]],
) -> None:
    branches = resources_of(template_json["PvWebStack"], "AWS::Amplify::Branch")
    assert len(branches) == 1
    props = next(iter(branches.values()))["Properties"]
    assert props["BranchName"] == "main"
    assert props["Stage"] == "PRODUCTION"
    assert props["Framework"] == "Next.js - SSR"


@pytest.mark.parametrize(
    "header",
    [
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
    ],
)
def test_the_five_security_headers_are_set(
    template_json: dict[str, dict[str, Any]], header: str
) -> None:
    assert header in _app(template_json["PvWebStack"])["CustomHeaders"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.provenance.app",
        "https://provenance-auth.auth.us-east-1.amazoncognito.com",
        "https://cognito-idp.us-east-1.amazonaws.com",
        "https://provenance-artifacts-us-east-1.s3.us-east-1.amazonaws.com",
    ],
)
def test_connect_src_names_every_origin_the_browser_actually_calls(
    template_json: dict[str, dict[str, Any]], origin: str
) -> None:
    headers = _app(template_json["PvWebStack"])["CustomHeaders"]
    assert origin in headers, origin


def test_frames_and_inline_script_are_refused(
    template_json: dict[str, dict[str, Any]],
) -> None:
    headers = _app(template_json["PvWebStack"])["CustomHeaders"]
    assert "frame-ancestors 'none'" in headers
    assert "script-src 'self'" in headers
    assert "'unsafe-eval'" not in headers


def test_only_public_configuration_reaches_the_browser_bundle(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Every Amplify environment variable is compiled into a public bundle.

    ``NEXT_PUBLIC_`` is the only prefix allowed to carry configuration, and the
    two Amplify build controls are the only exceptions.
    """
    variables = key_values(_app(template_json["PvWebStack"])["EnvironmentVariables"])
    build_controls = {"AMPLIFY_DIFF_DEPLOY", "_LIVE_UPDATES"}
    for name in variables:
        assert name.startswith("NEXT_PUBLIC_") or name in build_controls, name
    assert "NEXT_PUBLIC_COGNITO_WEB_CLIENT_ID" in variables
    # A public client has no secret, so there is nothing here to leak.
    assert not any("SECRET" in name for name in variables)


def test_the_web_bundle_requests_only_the_read_scope(
    template_json: dict[str, dict[str, Any]],
) -> None:
    variables = key_values(_app(template_json["PvWebStack"])["EnvironmentVariables"])
    scopes = variables["NEXT_PUBLIC_COGNITO_SCOPES"]
    assert scopes == "openid email profile provenance.memory/read"
    for forbidden in ("action/execute", "memory/propose", "ingest/write"):
        assert forbidden not in scopes


def test_the_build_sha_is_carried_for_the_footer_comparison(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``G13.2`` compares the footer against ``GET /v1/version``; the value has to
    come from the same place both times.
    """
    variables = key_values(_app(template_json["PvWebStack"])["EnvironmentVariables"])
    assert variables["NEXT_PUBLIC_BUILD_SHA"] == "0" * 40


def test_branch_auto_deletion_is_off(template_json: dict[str, dict[str, Any]]) -> None:
    assert _app(template_json["PvWebStack"])["EnableBranchAutoDeletion"] is False


def test_the_domain_association_points_the_app_subdomain_at_main(
    template_json: dict[str, dict[str, Any]],
) -> None:
    domains = resources_of(template_json["PvWebStack"], "AWS::Amplify::Domain")
    assert len(domains) == 1
    props = next(iter(domains.values()))["Properties"]
    assert props["DomainName"] == "provenance.app"
    assert props["SubDomainSettings"] == [{"BranchName": "main", "Prefix": "app"}]
    assert props["EnableAutoSubDomain"] is False


def test_the_service_role_writes_only_amplify_log_groups(
    template_json: dict[str, dict[str, Any]],
) -> None:
    from pv_cdk_testing import policy_statements

    for _logical, _cfn_type, statement in policy_statements(template_json["PvWebStack"]):
        resources = flatten(statement.get("Resource"))
        assert "/aws/amplify/" in resources or resources == ""
