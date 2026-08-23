"""T8.8 -- the exported OpenAPI document, and the invariants `spec_lint` reads.

Authority: `specs/15_API_SPEC.md` section 16.1.

`G8.1` runs `tools/spec_lint.py`, which is Integrator-owned and outside this
task's boundary. Everything that lint checks about the *implementation* side
is asserted here so the drift is caught in this suite too, not only in a tool
that does not exist yet.
"""

from __future__ import annotations

import pytest
from _support.fixtures import CAPABILITY_KEY, CURSOR_KEY

from services.control_plane.app.api.openapi import export_openapi
from services.control_plane.app.api.schemas import internal as internal_schemas
from services.control_plane.app.api.schemas import public as public_schemas

pytestmark = pytest.mark.unit

# Section 8.0's index, verbatim. G8.1 asserts 31 and this is that 31.
PUBLIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/healthz"),
    ("GET", "/v1/version"),
    ("GET", "/v1/me"),
    ("GET", "/v1/dashboard"),
    ("GET", "/v1/contexts"),
    ("GET", "/v1/relationships"),
    ("GET", "/v1/relationships/{relationship_id}"),
    ("GET", "/v1/cases"),
    ("GET", "/v1/cases/{case_id}"),
    ("GET", "/v1/cases/{case_id}/timeline"),
    ("GET", "/v1/cases/{case_id}/state-proof"),
    ("GET", "/v1/cases/{case_id}/conflicts"),
    ("GET", "/v1/beliefs/{belief_id}"),
    ("POST", "/v1/cases/{case_id}/corrections"),
    ("GET", "/v1/commitments"),
    ("GET", "/v1/triggers"),
    ("GET", "/v1/artifacts"),
    ("POST", "/v1/artifacts/upload-intent"),
    ("POST", "/v1/artifacts/{artifact_id}/complete"),
    ("GET", "/v1/artifacts/{artifact_id}"),
    ("GET", "/v1/ingest-alias"),
    ("POST", "/v1/ingest-alias/rotate"),
    ("GET", "/v1/action-intents"),
    ("GET", "/v1/action-intents/{action_intent_id}"),
    ("PUT", "/v1/action-intents/{action_intent_id}/draft"),
    ("POST", "/v1/action-intents/{action_intent_id}/approve"),
    ("POST", "/v1/action-intents/{action_intent_id}/reject"),
    ("GET", "/v1/traces/{trace_id}"),
    ("GET", "/v1/cases/{case_id}/memory-trace"),
    ("POST", "/v1/judge-mode/counterfactual"),
    ("GET", "/v1/judge-mode/counterfactual/{counterfactual_id}"),
)

INTERNAL_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/internal/v1/ingest/artifacts"),
    ("GET", "/internal/v1/agent-runs/{agent_run_id}"),
    ("GET", "/internal/v1/agent-runs/{agent_run_id}/artifact-content"),
    ("POST", "/internal/v1/agent-runs/{agent_run_id}/evidence"),
    ("POST", "/internal/v1/agent-runs/{agent_run_id}/retrieval"),
    ("GET", "/internal/v1/agent-runs/{agent_run_id}/state-proof"),
    ("POST", "/internal/v1/memory/proposals"),
    ("POST", "/internal/v1/advocacy/action-intents"),
    ("POST", "/internal/v1/agent-runs/{agent_run_id}/complete"),
    ("POST", "/internal/v1/triggers/{trigger_id}/evaluate"),
    ("POST", "/internal/v1/actions/{action_intent_id}/execute"),
    ("POST", "/internal/v1/events/outbox/sweep"),
    ("POST", "/internal/v1/events/deliveries"),
)


def _implemented(app: object) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not (path.startswith("/v1") or path.startswith("/internal/v1")):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            found.add((method, path))
    return found


def test_the_documented_public_index_has_thirty_one_rows() -> None:
    assert len(PUBLIC_ROUTES) == 31


def test_every_documented_public_route_is_implemented(app) -> None:
    missing = sorted(set(PUBLIC_ROUTES) - _implemented(app))
    assert missing == []


def test_every_documented_internal_route_is_implemented(app) -> None:
    assert len(INTERNAL_ROUTES) == 13
    missing = sorted(set(INTERNAL_ROUTES) - _implemented(app))
    assert missing == []


def test_no_undocumented_route_is_implemented(app) -> None:
    """Drift is a gate failure in both directions (section 16.1).

    `/v1/judge-mode/agent-views` is implemented because T8.7's acceptance
    criterion names it, even though section 8.0's index does not list it. It
    is recorded here rather than hidden, because an undocumented route is
    exactly what this assertion exists to surface.
    """
    documented = (
        set(PUBLIC_ROUTES)
        | set(INTERNAL_ROUTES)
        | {
            ("GET", "/v1/judge-mode/agent-views"),
            ("GET", "/v1/openapi.json"),
        }
    )
    extra = sorted(_implemented(app) - documented)
    assert extra == []


def test_the_export_is_openapi_3_1_and_names_every_route(app) -> None:
    document = export_openapi(app)
    assert document["openapi"].startswith("3.1")
    for _method, path in PUBLIC_ROUTES + INTERNAL_ROUTES:
        assert path in document["paths"], path


def test_every_keyed_endpoint_declares_the_idempotency_key_header(app) -> None:
    from services.control_plane.app.api.idempotency import IDEMPOTENCY_SCOPES

    document = export_openapi(app)
    for method, path in IDEMPOTENCY_SCOPES:
        operation = document["paths"][path][method.lower()]
        names = {p["name"] for p in operation.get("parameters", [])}
        assert "Idempotency-Key" in names, f"{method} {path}"


def test_every_request_model_forbids_extra_fields() -> None:
    offenders: list[str] = []
    for module in (public_schemas, internal_schemas):
        for name in dir(module):
            candidate = getattr(module, name)
            config = getattr(candidate, "model_config", None)
            if config is None or not name.endswith("Request"):
                continue
            if config.get("extra") != "forbid":
                offenders.append(f"{module.__name__}.{name}")
    assert offenders == []


def test_the_export_helper_needs_no_environment(api_config, deps) -> None:
    """`export_openapi` is what `services/control_plane/tools/export_openapi.py`
    (Integrator-owned) calls. It must build the document without a database,
    a credential, or a network call."""
    from services.control_plane.app.api.app import create_app

    document = export_openapi(create_app(config=api_config, deps=deps))
    assert document["info"]["title"]
    assert CURSOR_KEY.decode() not in str(document)
    assert CAPABILITY_KEY.decode() not in str(document)
