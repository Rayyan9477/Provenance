"""T8.2 -- the route-class check on `client_id`.

Authority: `specs/15_API_SPEC.md` section 2.4. Feeds `G8.2`, `G8.3`, `G8.8`.

Section 2.4: "This is the single check that keeps the two authorisation models
from leaking into each other." It is asserted here in both directions, and the
last test in this file is the one that makes the other two trustworthy -- it
proves the check is reached through the module object, which is what
`PV_SABOTAGE=api.auth.route_class_check` neuters.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from _support import fakes as fakes_mod
from _support.tokens import AGENT_CLIENT_ID, WEB_CLIENT_ID, WORKER_CLIENT_ID

from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth import route_class as route_class_mod
from services.control_plane.app.auth.route_class import RouteClass

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------


def test_a_web_client_may_use_a_public_route() -> None:
    route_class_mod.route_class_check(RouteClass.PUBLIC, "provenance-web")


@pytest.mark.parametrize("client", ["provenance-agent-runtime", "provenance-workers"])
def test_a_workload_client_may_not_use_a_public_route(client: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        route_class_mod.route_class_check(RouteClass.PUBLIC, client)
    assert excinfo.value.code is ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE
    assert excinfo.value.http_status == 403
    assert excinfo.value.details["client_id"] == client


def test_a_web_client_may_not_use_an_internal_route() -> None:
    with pytest.raises(ApiError) as excinfo:
        route_class_mod.route_class_check(RouteClass.INTERNAL, "provenance-web")
    assert excinfo.value.code is ErrorCode.HUMAN_TOKEN_ON_INTERNAL_ROUTE
    assert excinfo.value.http_status == 403


@pytest.mark.parametrize("client", ["provenance-agent-runtime", "provenance-workers"])
def test_a_workload_client_may_use_an_internal_route(client: str) -> None:
    route_class_mod.route_class_check(RouteClass.INTERNAL, client)


def test_an_unknown_client_id_reaches_neither_class() -> None:
    for klass in (RouteClass.PUBLIC, RouteClass.INTERNAL):
        with pytest.raises(ApiError):
            route_class_mod.route_class_check(klass, "some-other-app-client")


# --------------------------------------------------------------------------
# G8.2 / G8.3 over HTTP
# --------------------------------------------------------------------------


def test_g8_2_a_workload_token_on_a_public_route_is_403(client, agent_bearer) -> None:
    response = client.get(
        f"/v1/cases/{fakes_mod.ALEX.case_id}",
        headers={"Authorization": f"Bearer {agent_bearer}"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE"
    assert body["error"]["details"]["client_id"] == "provenance-agent-runtime"


def test_g8_2_holds_for_the_worker_client_too(client, worker_bearer) -> None:
    response = client.get(
        f"/v1/cases/{fakes_mod.ALEX.case_id}",
        headers={"Authorization": f"Bearer {worker_bearer}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE"


def test_g8_3_a_browser_token_on_an_internal_route_is_403(client, auth_alex) -> None:
    response = client.post("/internal/v1/memory/proposals", headers=auth_alex, json={})
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "HUMAN_TOKEN_ON_INTERNAL_ROUTE"
    assert body["error"]["details"]["client_id"] == "provenance-web"


def test_the_route_class_check_precedes_body_validation(client, auth_alex) -> None:
    """A browser token must not learn anything about the internal schema by
    sending a bad body -- the 403 comes first, not a 422."""
    response = client.post(
        "/internal/v1/memory/proposals", headers=auth_alex, json={"nonsense": True}
    )
    assert response.status_code == 403


def test_the_route_class_check_is_mounted_on_every_internal_route(client, auth_alex) -> None:
    paths = [
        ("POST", "/internal/v1/ingest/artifacts"),
        ("GET", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}"),
        ("GET", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}/artifact-content"),
        ("POST", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}/evidence"),
        ("POST", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}/retrieval"),
        ("GET", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}/state-proof"),
        ("POST", "/internal/v1/memory/proposals"),
        ("POST", "/internal/v1/advocacy/action-intents"),
        ("POST", f"/internal/v1/agent-runs/{fakes_mod.ALEX.agent_run_id}/complete"),
        ("POST", f"/internal/v1/triggers/{fakes_mod.ALEX.trigger_id}/evaluate"),
        ("POST", f"/internal/v1/actions/{fakes_mod.ALEX.action_intent_id}/execute"),
        ("POST", "/internal/v1/events/outbox/sweep"),
        ("POST", "/internal/v1/events/deliveries"),
    ]
    assert len(paths) == 13
    for method, path in paths:
        response = client.request(method, path, headers=auth_alex, json={})
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "HUMAN_TOKEN_ON_INTERNAL_ROUTE", path


def test_every_public_route_rejects_a_workload_token(client, agent_bearer, app) -> None:
    headers = {"Authorization": f"Bearer {agent_bearer}"}
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if not path.startswith("/v1/") or path in {"/v1/healthz", "/v1/version"}:
            continue
        concrete = (
            path.replace("{case_id}", str(fakes_mod.ALEX.case_id))
            .replace("{belief_id}", str(fakes_mod.ALEX.belief_id))
            .replace("{artifact_id}", str(fakes_mod.ALEX.artifact_id))
            .replace("{action_intent_id}", str(fakes_mod.ALEX.action_intent_id))
            .replace("{relationship_id}", str(fakes_mod.ALEX.case_id))
            .replace("{trace_id}", str(fakes_mod.ALEX.trace_id))
            .replace("{trigger_id}", str(fakes_mod.ALEX.trigger_id))
            .replace("{counterfactual_id}", str(fakes_mod.ALEX.counterfactual_id))
        )
        for method in methods:
            response = client.request(
                method,
                concrete,
                headers={**headers, "Idempotency-Key": "pv-route-class-00000"},
                json={},
            )
            assert response.status_code == 403, f"{method} {concrete}"
            assert (
                response.json()["error"]["code"] == "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE"
            ), f"{method} {concrete}"
            checked += 1
    assert checked >= 20, "the sweep must actually reach the public surface"


# --------------------------------------------------------------------------
# G8.8 -- the sabotage hook is wired
# --------------------------------------------------------------------------


def test_the_sabotage_hook_names_the_symbol_g8_8_addresses() -> None:
    assert route_class_mod.SABOTAGE_MODULE == "api.auth"
    assert "route_class_check" in route_class_mod.SABOTAGE_HOOKS


def test_neutering_the_check_turns_g8_2_and_g8_3_red(
    monkeypatch, client, agent_bearer, auth_alex
) -> None:
    """`PV_SABOTAGE` rebinds the attribute ON THE MODULE OBJECT. If any caller
    had done `from .route_class import route_class_check`, the rebind would be
    invisible and `make sabotage` would report a green run for a check that no
    longer executes. This test is the reason the matrix entry can be trusted."""
    monkeypatch.setattr(route_class_mod, "route_class_check", lambda *a, **k: None)

    public = client.get(
        f"/v1/cases/{fakes_mod.ALEX.case_id}",
        headers={"Authorization": f"Bearer {agent_bearer}"},
    )
    internal = client.post("/internal/v1/memory/proposals", headers=auth_alex, json={})

    assert public.status_code != 403 or public.json()["error"]["code"] != (
        "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE"
    ), "G8.2 must go red when the check is neutered"
    assert internal.status_code != 403 or internal.json()["error"]["code"] != (
        "HUMAN_TOKEN_ON_INTERNAL_ROUTE"
    ), "G8.3 must go red when the check is neutered"


def test_no_module_from_imports_route_class_check() -> None:
    """The AST proof behind the sabotage entry, in the shape
    `provenance_domain` uses for `money.outstanding`."""
    root = Path(route_class_mod.__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "route_class" in node.module:
                for alias in node.names:
                    if alias.name == "route_class_check":
                        offenders.append(str(path))
    assert offenders == [], (
        "a from-import copies the reference before PV_SABOTAGE rebinds it; "
        "reach the function through its module"
    )


def test_the_client_id_map_is_the_three_cognito_app_clients(api_config) -> None:
    assert set(api_config.client_id_names) == {WEB_CLIENT_ID, AGENT_CLIENT_ID, WORKER_CLIENT_ID}
    assert set(api_config.client_id_names.values()) == {
        "provenance-web",
        "provenance-agent-runtime",
        "provenance-workers",
    }


def test_route_class_check_signature_is_stable() -> None:
    params = list(inspect.signature(route_class_mod.route_class_check).parameters)
    assert params[:2] == ["route_class", "app_client"]
