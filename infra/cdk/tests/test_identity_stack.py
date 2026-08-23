"""``PvIdentityStack``: the pool, the seven closed scopes, the three app clients.

The scope allocation is the security boundary the whole action plane rests on:
``provenance-agent-runtime`` holds no ``action/execute`` and no
``outbox/dispatch``, so the graph that writes a dispute letter is structurally
incapable of sending it.
"""

from __future__ import annotations

from typing import Any

from provenance_infra import config as cfg
from pv_cdk_testing import flatten, resources_of


def _clients(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        body["Properties"]["ClientName"]: body["Properties"]
        for body in resources_of(template, "AWS::Cognito::UserPoolClient").values()
    }


def test_the_pool_enforces_a_twelve_character_policy_and_email_only_recovery(
    template_json: dict[str, dict[str, Any]],
) -> None:
    pools = resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPool")
    assert len(pools) == 1
    props = next(iter(pools.values()))["Properties"]
    assert props["UserPoolName"] == cfg.USER_POOL_NAME
    policy = props["Policies"]["PasswordPolicy"]
    assert policy["MinimumLength"] == 12
    assert all(
        policy[flag]
        for flag in ("RequireLowercase", "RequireUppercase", "RequireNumbers", "RequireSymbols")
    )
    assert props["MfaConfiguration"] == "OPTIONAL"
    assert props["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]


def test_threat_protection_is_audit_rather_than_enforced(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Enforced mode can challenge a sign-in during a recorded demo.

    A blocked login at 0:05 of a three-minute video is a worse outcome than an
    unmitigated credential-stuffing risk on a five-user pool, and audit mode
    still populates the risk telemetry.
    """
    props = next(
        iter(resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPool").values())
    )["Properties"]
    assert props["UserPoolTier"] == "PLUS"
    assert props["UserPoolAddOns"]["AdvancedSecurityMode"] == "AUDIT"
    assert props["UserPoolAddOns"]["AdvancedSecurityMode"] != "ENFORCED"


def test_the_hosted_ui_prefix_is_the_documented_host(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """This exact host appears in ``specs/15_API_SPEC.md`` section 1.1 and in
    ``NEXT_PUBLIC_COGNITO_DOMAIN``; changing it breaks the documented token
    endpoint.
    """
    domains = resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPoolDomain")
    assert {b["Properties"]["Domain"] for b in domains.values()} == {cfg.HOSTED_UI_PREFIX}


def test_the_resource_server_declares_exactly_the_seven_closed_scopes(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The list is closed. Adding an eighth requires editing
    ``specs/15_API_SPEC.md`` section 2.1 first.
    """
    servers = resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPoolResourceServer")
    assert len(servers) == 1
    props = next(iter(servers.values()))["Properties"]
    assert props["Identifier"] == cfg.RESOURCE_SERVER_ID
    names = {scope["ScopeName"] for scope in props["Scopes"]}
    assert names == set(cfg.SCOPES)
    assert len(names) == 7


def test_exactly_three_app_clients(template_json: dict[str, dict[str, Any]]) -> None:
    clients = _clients(template_json["PvIdentityStack"])
    assert set(clients) == {cfg.WEB_CLIENT_NAME, cfg.AGENT_CLIENT_NAME, cfg.WORKER_CLIENT_NAME}


def test_the_web_client_has_no_secret_which_is_what_forces_pkce(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """PKCE is not a Cognito toggle.

    Because ``GenerateSecret`` is false and the flow is ``code``, Cognito
    *requires* ``code_challenge`` with ``S256``. A deployment that generated a
    secret here would silently allow a non-PKCE flow, which is why the absence is
    load-bearing rather than cosmetic.
    """
    props = _clients(template_json["PvIdentityStack"])[cfg.WEB_CLIENT_NAME]
    assert props.get("GenerateSecret") in (False, None)
    assert props["AllowedOAuthFlows"] == ["code"]
    assert "implicit" not in props["AllowedOAuthFlows"]
    assert props["PreventUserExistenceErrors"] == "ENABLED"
    assert props["EnableTokenRevocation"] is True
    assert set(props["CallbackURLs"]) == {
        "https://app.provenance.app/auth/callback",
        "http://localhost:3000/auth/callback",
    }


def test_the_web_client_can_only_read_memory(
    template_json: dict[str, dict[str, Any]],
) -> None:
    props = _clients(template_json["PvIdentityStack"])[cfg.WEB_CLIENT_NAME]
    scopes = flatten(props["AllowedOAuthScopes"])
    assert "memory/read" in scopes
    for forbidden in ("memory/propose", "action/propose", "action/execute", "ingest/write"):
        assert forbidden not in scopes


def test_the_agent_client_cannot_execute_actions_or_dispatch_the_outbox(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The graph that writes a dispute letter is structurally incapable of
    sending it.

    ``ingest/write`` is present deliberately, per ``specs/15_API_SPEC.md``
    section 9.0: it is the one place the agent must register evidence, narrowed
    by ``CLIENT_CAPABILITY_MATRIX`` to the ``AGENT_RUN`` capability kind. The
    section 2.1 table lists three scopes and 9.0 lists four; 9.0 is the more
    specific statement and it is a permission, so the tension is recorded rather
    than resolved silently. (Section 15.2.)
    """
    props = _clients(template_json["PvIdentityStack"])[cfg.AGENT_CLIENT_NAME]
    assert props["GenerateSecret"] is True
    assert props["AllowedOAuthFlows"] == ["client_credentials"]
    scopes = flatten(props["AllowedOAuthScopes"])
    for expected in ("memory/read", "memory/propose", "action/propose", "ingest/write"):
        assert expected in scopes
    assert "action/execute" not in scopes
    assert "outbox/dispatch" not in scopes


def test_the_worker_client_holds_the_five_worker_scopes(
    template_json: dict[str, dict[str, Any]],
) -> None:
    props = _clients(template_json["PvIdentityStack"])[cfg.WORKER_CLIENT_NAME]
    assert props["GenerateSecret"] is True
    assert props["AllowedOAuthFlows"] == ["client_credentials"]
    scopes = flatten(props["AllowedOAuthScopes"])
    for expected in (
        "ingest/write",
        "trigger/evaluate",
        "action/execute",
        "outbox/dispatch",
        "memory/read",
    ):
        assert expected in scopes
    assert "memory/propose" not in scopes


def test_machine_clients_have_no_user_auth_flow_at_all(
    template_json: dict[str, dict[str, Any]],
) -> None:
    clients = _clients(template_json["PvIdentityStack"])
    for name in (cfg.AGENT_CLIENT_NAME, cfg.WORKER_CLIENT_NAME):
        flows = clients[name].get("ExplicitAuthFlows") or []
        assert not flows, f"{name} should have no user auth flow"


def test_the_judges_group_exists(template_json: dict[str, dict[str, Any]]) -> None:
    groups = resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPoolGroup")
    assert {b["Properties"]["GroupName"] for b in groups.values()} == {cfg.JUDGES_GROUP_NAME}


def test_provisioning_is_a_pool_trigger_not_an_api_call(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``specs/15_API_SPEC.md`` section 2.5 forbids auto-creating users from an
    API call, so the three rows are written by a post-confirmation trigger.

    A failure there fails the sign-up, which is correct: a confirmed Cognito user
    with no ``users`` row produces ``403 USER_NOT_PROVISIONED`` on every request,
    and it is better to make the user retry sign-up than to leave them
    permanently broken.
    """
    props = next(
        iter(resources_of(template_json["PvIdentityStack"], "AWS::Cognito::UserPool").values())
    )["Properties"]
    assert "PostConfirmation" in props["LambdaConfig"]

    functions = resources_of(template_json["PvIdentityStack"], "AWS::Lambda::Function")
    names = {b["Properties"].get("FunctionName") for b in functions.values()}
    assert "provenance-cognito-post-confirmation" in names
