"""The judge's button needs a door — the manual wake route.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` section 13.2 -- the manual wake entry point.
- ``docs/CANONICAL_DECISIONS.md`` -> *Trigger demonstration*: "Use the same
  manual-wake entry point for a false-predicate no-op and the landlord fire. Do
  not mutate and secretly revert canonical state for presentation."

Why this file exists
--------------------
``write.wake_trigger`` has been implemented and bound since 2026-08-24. It is
not a shortcut: it builds an ordinary wake envelope differing from the scheduled
one in exactly two fields, and calls the identical ``evaluate_trigger``, so the
guards, the projection read, the predicate, the Memory Kernel, the serializable
transaction, the revision guard and the idempotency record are all on the path.

And nothing could reach it. ``python -m tools.demo_readiness`` reported step 10
NOT READY with the reason spelled out: *"internal.evaluate_trigger is bound, but
section 8.0's route index has no public wake route, so there is no manual-wake
entry point to drive it from."*

That is a distinct failure from "unbuilt", and it is worth naming as such: the
capability existed, was tested, was armed against the live cluster, and had no
handle. Prospective memory is one of the four things ``00_PRODUCT.md`` section
2.2 claims ordinary RAG structurally cannot do, and it is the demo's second
reveal.

What is deliberately NOT tested here
------------------------------------
That the predicate evaluates correctly, that the Kernel commits, that the
revision guard fires. Those are ``tests/events`` and ``tests/kernel``, over the
real evaluator. This file tests the *door*: that it exists, that it reaches the
bound port, that it refuses a trigger belonging to someone else, and that it
offers no way to force an outcome.
"""

from __future__ import annotations

import pytest
from _support import fakes as fakes_mod

from services.control_plane.app.api.openapi import export_openapi

pytestmark = pytest.mark.unit

ALEX = fakes_mod.ALEX
ROB = fakes_mod.ROB


def test_the_wake_route_exists_and_reaches_the_bound_port(client, auth_alex) -> None:
    """The route the demo presses.

    Fails if the route is absent (404 from the router rather than from the
    ownership check) or if it is wired to something other than
    ``write.wake_trigger``.
    """
    response = client.post(f"/v1/triggers/{ALEX.trigger_id}/wake", headers=auth_alex)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trigger_id"] == str(ALEX.trigger_id)
    assert body["result"] == "FIRED"
    assert body["reason_code"] == "COMMITMENT_OVERDUE_UNPAID"


def test_waking_another_owners_trigger_is_404_and_never_403(client, auth_alex) -> None:
    """Section 1.7. A 403 confirms the row exists to someone who may not read it.

    The port returns ``None`` for a trigger outside the scope, and the route
    must map that to a typed 404 rather than leaking existence.
    """
    response = client.post(f"/v1/triggers/{ROB.trigger_id}/wake", headers=auth_alex)
    assert response.status_code == 404, response.text
    # Assert WHICH code, not merely that it is not FORBIDDEN. A bare 404 check
    # passes when the route does not exist at all -- the router's own 404 and
    # the ownership refusal are the same status, and this test was vacuous
    # until it named the code only the handler can produce.
    assert response.json()["error"]["code"] == "TRIGGER_NOT_FOUND"


def test_the_wake_route_refuses_an_unauthenticated_caller(client) -> None:
    response = client.post(f"/v1/triggers/{ALEX.trigger_id}/wake")
    assert response.status_code == 401, response.text


def test_the_route_offers_no_way_to_force_an_outcome(client, auth_alex) -> None:
    """``16_TRIGGER_DSL.md`` 13.2: there is no ``force`` parameter and adding one
    is prohibited.

    A wake that can be told what to conclude is a scripted animation with a
    network call in front of it, which ``CANONICAL_DECISIONS.md`` -> *Judge
    Mode* forbids outright. Pressing it twice must reach guard G2 and answer
    ``NO_OP / TRIGGER_NOT_ARMED``; pressing it on a deposit that was actually
    returned must no-op on stage. Both properties die the moment a caller can
    supply the verdict.

    So a body carrying a forcing field is refused rather than ignored: ignoring
    it would let a demo script believe it had control and be silently wrong.
    """
    response = client.post(
        f"/v1/triggers/{ALEX.trigger_id}/wake",
        headers=auth_alex,
        json={"force": True, "result": "FIRED"},
    )
    assert response.status_code == 422, response.text


def test_the_wake_route_is_listed_in_the_exported_openapi_document(app) -> None:
    """A route absent from the schema is a route a judge cannot discover.

    Exported rather than fetched: ``serve_openapi`` is configuration, and this
    asserts the document describes the route regardless of whether a given
    deployment publishes it.
    """
    paths = export_openapi(app)["paths"]
    assert "/v1/triggers/{trigger_id}/wake" in paths
    assert "post" in paths["/v1/triggers/{trigger_id}/wake"]
