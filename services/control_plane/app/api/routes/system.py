"""`GET /v1/healthz` and `GET /v1/version` -- the two unauthenticated routes.

Authority: ``specs/15_API_SPEC.md`` sections 8.1 and 8.2.

The separation between them is the point, and it is a disclosure rule rather
than a tidiness one. ``/v1/healthz`` is a bare liveness probe: no token, no
database, and **no `fixture_mode`**. ``/v1/version`` is the single authoritative
operating-mode channel, and it is unauthenticated by design so that a judge can
`curl` it with nothing but the URL.

A load balancer that polled the mode-bearing endpoint would put the mode into
every access log; a document that read the mode from the liveness probe is how
an undisclosed fixture-mode demo happens. Neither is possible if the two
endpoints answer different questions, which is why `db_ok` lives here and not
there.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.deps import api_config, api_deps

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="Liveness probe. No auth, no database.")
async def healthz() -> dict[str, str]:
    """Section 8.1. Exactly one key, and it never grows one.

    Anything added here is read by a load balancer thousands of times a day
    and logged everywhere. ``{"status": "ok"}`` is the whole contract.
    """
    return {"status": "ok"}


@router.get("/version", summary="Build and operating mode. Unauthenticated by design.")
async def version(
    config: Annotated[ApiConfig, Depends(api_config)],
    deps: Annotated[Dependencies, Depends(api_deps)],
) -> dict[str, Any]:
    """Section 8.2.

    ``db_ok`` is a cached bit refreshed by a background task, never a query:
    an unauthenticated endpoint that touches CockroachDB on every call is an
    availability oracle for anyone with the URL.

    The response field is ``git_sha``. ``build_sha`` is the *environment
    variable* the settings object reads and is not a field name; the mapping
    between the two happens once, in ``ApiConfig.from_settings``.
    """
    return {
        "service": config.service,
        "version": config.version,
        "git_sha": config.git_sha,
        "api_version": config.api_version,
        "contracts_schema_version": config.contracts_schema_version,
        "region": config.region,
        "built_at": config.built_at,
        "schema_revision": config.schema_revision,
        "fixture_mode": config.fixture_mode,
        # Which authority actually verified the token on every authenticated
        # request: `cognito`, `google`, or `local`. It sits beside
        # `fixture_mode` because it answers the same kind of question and
        # deserves the same channel -- an authentication path that does not
        # announce itself is the same quiet dishonesty as an undisclosed
        # fixture-mode demo. The value is read from the provider object in
        # force rather than from a configuration field, so it cannot drift
        # from what is actually verifying tokens.
        "identity_provider": config.identity_provider,
        "agent_mode": config.agent_mode,
        "otlp_export": config.otlp_export,
        "db_ok": deps.db_ok(),
    }
