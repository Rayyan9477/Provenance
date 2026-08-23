"""The route-class check on ``client_id``.

Authority: ``specs/15_API_SPEC.md`` section 2.4.

    "The check is on ``client_id``, not on scope alone. [...] This is the
    single check that keeps the two authorisation models from leaking into
    each other."

It is mounted as a router-level dependency rather than on each route, so a
route added in Phase 9 inherits it by construction rather than by the author
remembering. A route that needs to opt out has to say so out loud, in the
router it is mounted on.

The ``PV_SABOTAGE`` hook
------------------------
``23_PHASE_GATES.md`` G8.8 runs::

    PV_SABOTAGE=api.auth.route_class_check pytest tests/api -q; echo "exit=$?"

and requires at least two FAILED and exit 1. The neutering replaces the symbol
**on this module object**, so every caller must reach it as
``route_class.route_class_check(...)``. A ``from``-import would copy the
reference before the rebind and the sabotage would silently never arrive --
``tests/auth/test_route_class.py`` asserts the absence of such an import
against the AST, which is what makes the matrix entry trustworthy.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Final

from provenance_domain import money
from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = [
    "INTERNAL_CLIENTS",
    "PUBLIC_CLIENTS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "SABOTAGED_SYMBOLS",
    "RouteClass",
    "route_class_check",
]


class RouteClass(StrEnum):
    """Section 2.4's three route classes."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    UNAUTHENTICATED = "UNAUTHENTICATED"


#: The Cognito app clients admitted to each class, by logical name.
PUBLIC_CLIENTS: Final[frozenset[str]] = frozenset({"provenance-web"})
INTERNAL_CLIENTS: Final[frozenset[str]] = frozenset(
    {"provenance-agent-runtime", "provenance-workers"}
)


def route_class_check(route_class: RouteClass, app_client: str) -> None:
    """Admit *app_client* to *route_class*, or raise.

    An app client that appears in neither set reaches nothing: the default is
    refusal, so a fourth Cognito client created by hand cannot silently gain
    the public surface.
    """
    if route_class is RouteClass.UNAUTHENTICATED:
        return
    if route_class is RouteClass.PUBLIC:
        if app_client in PUBLIC_CLIENTS:
            return
        raise ApiError(ErrorCode.WORKLOAD_TOKEN_ON_PUBLIC_ROUTE, details={"client_id": app_client})
    if app_client in INTERNAL_CLIENTS:
        return
    raise ApiError(ErrorCode.HUMAN_TOKEN_ON_INTERNAL_ROUTE, details={"client_id": app_client})


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `G8.8` addresses this symbol as `api.auth.route_class_check`, not by its
# dotted import path, so the module label is explicit rather than `__name__`.
# The mechanism lives in `provenance_domain.money` and is reused rather than
# re-implemented, for the same reason the authority grid is: one definition,
# one place to be wrong.
#
# Matrix entry for `tests/sabotage_matrix.yaml` (Integrator-owned; this task's
# boundary does not include `tests/`):
#
#   - symbol: api.auth.route_class_check
#     tests: services/control_plane/tests/auth/test_route_class.py
#     feeds: G8.8

#: The label `tests/sabotage_matrix.yaml` and `G8.8` use for this module.
SABOTAGE_MODULE: Final[str] = "api.auth"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("route_class_check",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
