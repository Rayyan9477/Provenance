"""`create_app` -- the control plane, assembled from a config and its ports.

Authority: ``specs/15_API_SPEC.md`` sections 1, 2.4 and 16.1.

Everything the application reaches outward for arrives as a
:class:`~services.control_plane.app.api.config.Dependencies`, and everything it
knows about itself arrives as an
:class:`~services.control_plane.app.api.config.ApiConfig`. Nothing is read from
the environment here and nothing is constructed at import time. That is what
lets the hermetic suites build a complete application with in-memory ports and
no `.env`, and it is what lets ``tools/export_openapi.py`` emit the document on
a machine with no database and no credential.

Two routers, and the difference is the security boundary
--------------------------------------------------------
``/v1`` is mounted behind ``route_class_check(PUBLIC, ...)`` and a resolved
human principal; ``/internal/v1`` behind ``route_class_check(INTERNAL, ...)``
and a workload token. Both checks are **router-level dependencies**, so a route
added in a later phase inherits them by construction rather than by the author
remembering. Section 2.4 calls this "the single check that keeps the two
authorisation models from leaking into each other", and a check that has to be
repeated per route is one an author can omit.

``/v1/healthz`` and ``/v1/version`` are the two exceptions and they live on
their own router, so opting out of authentication is a visible act rather than
a missing decorator.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI

from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.context import RequestContextMiddleware
from services.control_plane.app.api.deps import (
    enforce_json_media_type,
    require_principal,
    require_workload,
)
from services.control_plane.app.api.errors import install_error_handlers
from services.control_plane.app.api.internal import routes as internal_routes
from services.control_plane.app.api.routes import actions, artifacts, judge, memory, system

__all__ = ["create_app"]

DESCRIPTION = """
Provenance control plane.

`/v1` is the browser surface: a human principal resolved from a Cognito access
token, scoped to one `(tenant_id, user_id)` on every read and every write.

`/internal/v1` is the workload surface: agent runtime and Lambda workers,
authenticated by client credentials and authorised by a **server-resolved
capability object**. No internal endpoint accepts a `user_id`.
""".strip()


def create_app(*, config: ApiConfig, deps: Dependencies) -> FastAPI:
    """Build the application. No I/O, no environment, no import-time work."""
    app = FastAPI(
        title="Provenance Control Plane",
        version=config.version,
        description=DESCRIPTION,
        openapi_url="/openapi.json" if config.serve_openapi else None,
        # FastAPI's `{"detail": ...}` shape is replaced wholesale by
        # `install_error_handlers`; section 4.1's envelope is the only one.
        responses={},
    )
    app.state.api_config = config
    app.state.api_deps = deps

    install_error_handlers(app)

    # Added last, so it is the outermost user middleware: the trace id must be
    # on the response even when an inner layer produced it (section 1.5).
    app.add_middleware(RequestContextMiddleware)

    unauthenticated = APIRouter(prefix="/v1")
    unauthenticated.include_router(system.router)

    public = APIRouter(
        prefix="/v1",
        dependencies=[Depends(enforce_json_media_type), Depends(require_principal)],
    )
    for module in (memory, artifacts, actions, judge):
        public.include_router(module.router)

    internal = APIRouter(
        prefix="/internal/v1",
        dependencies=[Depends(enforce_json_media_type), Depends(require_workload)],
    )
    internal.include_router(internal_routes.router)

    app.include_router(unauthenticated)
    app.include_router(public)
    app.include_router(internal)
    return app
