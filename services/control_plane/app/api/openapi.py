"""Export the OpenAPI document.

Authority: ``specs/15_API_SPEC.md`` section 16.1.

    "The document is generated from the implementation, not hand-written.
    Drift is a gate failure in both directions."

That sentence is why this module is three lines of logic rather than a builder:
the only correct source for the document is the application object, and any
step that *edits* the generated paths is a place where the document and the
server can disagree. What is added here is metadata FastAPI has no way to know
-- the security schemes, and the two custom headers every response carries --
neither of which invents a path or a field.

The export must run on a machine with no database, no credential and no
network: ``create_app`` does no I/O, so calling ``app.openapi()`` does none
either. The HMAC keys on :class:`ApiConfig` never reach the document, which
``tests/api/test_openapi_surface.py`` asserts by searching the serialised
document for them.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

__all__ = ["OPENAPI_VERSION", "export_openapi"]

#: FastAPI emits 3.1.0 for pydantic v2 models. Pinned as a constant so a
#: downgrade in a dependency is a failing assertion rather than a silently
#: different client-generation input.
OPENAPI_VERSION = "3.1.0"

_SECURITY_SCHEMES: dict[str, Any] = {
    "CognitoHumanToken": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Cognito access token from the provenance-web app client. "
            "Reaches /v1 only; a workload token on /v1 is 403 "
            "WORKLOAD_TOKEN_ON_PUBLIC_ROUTE."
        ),
    },
    "CognitoWorkloadToken": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Client-credentials token from provenance-agent-runtime or "
            "provenance-workers. Reaches /internal/v1 only, and only together "
            "with a server-resolved capability."
        ),
    },
    "CapabilityProof": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Provenance-Capability-Proof",
        "description": (
            "Short MAC binding a capability id to the dispatch that created "
            "it. Defence in depth: the server-side record is the primary "
            "control."
        ),
    },
}


def export_openapi(app: FastAPI) -> dict[str, Any]:
    """The document for *app*, with the auth schemes FastAPI cannot infer."""
    document: dict[str, Any] = dict(app.openapi())
    document["openapi"] = OPENAPI_VERSION
    components: dict[str, Any] = dict(document.get("components") or {})
    components["securitySchemes"] = {
        **_SECURITY_SCHEMES,
        **(components.get("securitySchemes") or {}),
    }
    document["components"] = components
    return document
