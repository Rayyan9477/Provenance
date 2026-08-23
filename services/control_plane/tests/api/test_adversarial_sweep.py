"""T8b -- the adversarial lane's sweeping properties.

Authority: `specs/15_API_SPEC.md` section 1.7; `23_PHASE_GATES.md` G8.4;
`EXECUTION/72_DEFECT_PROTOCOL.md` severity rule B2.

`test_adversarial_isolation.py` walks the doors one at a time: this path
parameter, that filter, that body field. Enumerating doors is necessary and it
is also the thing that rots -- Phase 9 adds a route, nobody adds a row to the
parametrize list, and the lane still reports green.

So this file asserts the *property* rather than the instances:

1. every read the API issues, on every GET route the router exposes, is issued
   under the calling principal's own scope;
2. every write likewise;
3. a cursor is a sort position and never an authority, even when it is
   genuinely valid and was genuinely minted by somebody else;
4. a cross-scope `404` and a genuinely-absent `404` are byte-identical apart
   from the trace id -- not merely the same error code;
5. an internal handler receives the binding the *server* resolved, whichever
   user that turns out to be.

Why these are not vacuous
-------------------------
The fakes in `_support/fakes.py` scope by `(tenant_id, user_id)` themselves --
every lookup goes through `_owned()`. A route that dropped the scope would
therefore return `None`/empty rather than another user's row, which is a
different failure from a leak. That is why (1) and (2) assert on the *scopes
the ports were handed*, not only on the bodies that came back: the port
argument is the thing an implementation bug would corrupt, and it is checked
directly.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from _support import fakes as fakes_mod
from _support.fakes import _relationship_id
from _support.fixtures import idem

pytestmark = [pytest.mark.adversarial, pytest.mark.isolation]

ALEX, ROB = fakes_mod.ALEX, fakes_mod.ROB

#: Routes that are deliberately unauthenticated (section 8.1, 8.2) and so
#: issue no scoped read at all.
UNSCOPED = {"/v1/healthz", "/v1/version"}


def _concrete(path: str, actor: fakes_mod.Actor) -> str:
    return (
        path.replace("{case_id}", str(actor.case_id))
        .replace("{belief_id}", str(actor.belief_id))
        .replace("{artifact_id}", str(actor.artifact_id))
        .replace("{action_intent_id}", str(actor.action_intent_id))
        .replace("{relationship_id}", str(_relationship_id(actor)))
        .replace("{trace_id}", str(actor.trace_id))
        .replace("{trigger_id}", str(actor.trigger_id))
        .replace("{counterfactual_id}", str(actor.counterfactual_id))
    )


def _public_gets(app: Any) -> list[str]:
    found: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set())
        if path.startswith("/v1/") and "GET" in methods and path not in UNSCOPED:
            found.append(path)
    return sorted(found)


# --------------------------------------------------------------------------
# 1 and 2 -- the scope every port call was handed
# --------------------------------------------------------------------------


def test_every_public_get_reads_only_under_the_callers_own_scope(
    client, auth_alex, fixture, app
) -> None:
    """The sweep is over ``app.routes``, so a route added later is covered the
    day it is added rather than the day somebody remembers this file."""
    paths = _public_gets(app)
    assert len(paths) >= 18, "the sweep must actually reach the read surface"

    answered = 0
    for path in paths:
        response = client.get(_concrete(path, ALEX), headers=auth_alex)
        assert response.status_code != 500, f"{path} -> {response.text[:200]}"
        answered += response.status_code == 200

    assert answered >= 15, "most of the read surface must actually answer"
    scopes = {scope for _, scope in fixture.read.calls}
    assert scopes <= {ALEX.scope}, "a read was issued under a scope the caller does not own"
    assert scopes, "the sweep issued no scoped read at all, so it proves nothing"


def test_a_foreign_id_on_every_public_get_still_reads_under_the_callers_scope(
    client, auth_alex, fixture, app
) -> None:
    """Same sweep, every path parameter replaced by Rob's identifier.

    Nothing may be returned and -- the load-bearing half -- no read may be
    issued under Rob's scope. A route that passed the path id into the scope
    would show up here as a second member of the set.
    """
    for path in _public_gets(app):
        response = client.get(_concrete(path, ROB), headers=auth_alex)
        assert response.status_code in {200, 400, 404}, f"{path} -> {response.status_code}"
        if response.status_code == 200:
            body = response.text
            assert str(ROB.user_id) not in body
            assert str(ROB.tenant_id) not in body

    scopes = {scope for _, scope in fixture.read.calls}
    assert scopes <= {ALEX.scope}
    assert ROB.scope not in scopes


def test_every_mutation_writes_only_under_the_callers_own_scope(client, auth_alex, fixture) -> None:
    """The eight public mutations, with bodies that reach the write port."""
    calls: list[tuple[str, int]] = []

    calls.append(
        (
            "upload-intent",
            client.post(
                "/v1/artifacts/upload-intent",
                headers={**auth_alex, **idem("sweep-upload")},
                json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
            ).status_code,
        )
    )
    calls.append(
        (
            "complete",
            client.post(
                f"/v1/artifacts/{ALEX.artifact_id}/complete",
                headers={**auth_alex, **idem("sweep-complete")},
                json={},
            ).status_code,
        )
    )
    calls.append(
        (
            "correction",
            client.post(
                f"/v1/cases/{ALEX.case_id}/corrections",
                headers={**auth_alex, **idem("sweep-correction")},
                json={
                    "correction_type": "CONFIRM_BELIEF",
                    "statement": "Still true.",
                    "affected_belief_id": str(ALEX.belief_id),
                    "client_case_revision": 13,
                },
            ).status_code,
        )
    )
    calls.append(
        (
            "rotate",
            client.post(
                "/v1/ingest-alias/rotate",
                headers={**auth_alex, **idem("sweep-rotate")},
                json={},
            ).status_code,
        )
    )
    calls.append(
        (
            "draft",
            client.put(
                f"/v1/action-intents/{ALEX.action_intent_id}/draft",
                headers={**auth_alex, **idem("sweep-draft")},
                json={"subject": "s", "body": "b", "client_case_revision": 13},
            ).status_code,
        )
    )
    calls.append(
        (
            "approve",
            client.post(
                f"/v1/action-intents/{ALEX.action_intent_id}/approve",
                headers={**auth_alex, **idem("sweep-approve")},
                json={
                    "approved_draft": {"subject": "s", "body": "b"},
                    "client_case_revision": 13,
                    "acknowledge_warnings": [],
                },
            ).status_code,
        )
    )
    calls.append(
        (
            "reject",
            client.post(
                f"/v1/action-intents/{ALEX.action_intent_id}/reject",
                headers={**auth_alex, **idem("sweep-reject")},
                json={"reason_code": "NOT_NOW"},
            ).status_code,
        )
    )
    calls.append(
        (
            "counterfactual",
            client.post(
                "/v1/judge-mode/counterfactual",
                headers={**auth_alex, **idem("sweep-counterfactual")},
                json={"artifact_id": str(ALEX.artifact_id)},
            ).status_code,
        )
    )

    assert all(200 <= status < 300 for _, status in calls), calls
    names = {name for name, _, _ in fixture.write.calls}
    assert len(names) == 8, f"the sweep did not reach every write port method: {names}"
    scopes = {scope for _, scope, _ in fixture.write.calls}
    assert scopes == {ALEX.scope}, "a write was issued under a scope the caller does not own"


# --------------------------------------------------------------------------
# 3 -- the cursor
# --------------------------------------------------------------------------


def test_a_genuine_cursor_from_another_session_is_honoured_as_a_position_only(
    client, auth_alex, auth_rob, fixture
) -> None:
    """A *valid*, correctly signed cursor minted inside Rob's session.

    `test_adversarial_isolation.py` has the same intent but skips when the
    seeded fixture returns a single page, which it does -- so the assertion
    that matters has never actually run. Forcing ``has_more`` here makes the
    cursor real, and then the property is checked rather than skipped: the
    cursor reaches the port as a *sort position* (``after`` is not ``None``),
    and every read it produced is still under Alex's scope.
    """
    original = fixture.read.list_cases
    seen: list[Any] = []

    async def paged(scope: Any, *, limit: int, after: Any = None, **filters: Any) -> Any:
        seen.append(after)
        rows, _ = await original(scope, limit=limit, after=after, **filters)
        return rows, True

    fixture.read.list_cases = paged  # type: ignore[method-assign]

    minted = client.get("/v1/cases?limit=1", headers=auth_rob)
    assert minted.status_code == 200
    cursor = minted.json()["page"]["next_cursor"]
    assert cursor, "the fixture must actually mint a cursor for this to prove anything"

    fixture.read.calls.clear()
    seen.clear()
    replayed = client.get(f"/v1/cases?limit=1&cursor={cursor}", headers=auth_alex)

    assert replayed.status_code == 200
    assert seen and seen[0] is not None, "the cursor was dropped, so nothing was proved"
    assert {scope for _, scope in fixture.read.calls} == {ALEX.scope}
    assert str(ROB.case_id) not in replayed.text
    assert str(ROB.user_id) not in replayed.text


# --------------------------------------------------------------------------
# 4 -- the error body
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "attribute"),
    [
        ("/v1/cases/{}", "case_id"),
        ("/v1/cases/{}/timeline", "case_id"),
        ("/v1/cases/{}/state-proof", "case_id"),
        ("/v1/cases/{}/conflicts", "case_id"),
        ("/v1/cases/{}/memory-trace", "case_id"),
        ("/v1/beliefs/{}", "belief_id"),
        ("/v1/artifacts/{}", "artifact_id"),
        ("/v1/action-intents/{}", "action_intent_id"),
        ("/v1/traces/{}", "trace_id"),
        ("/v1/judge-mode/counterfactual/{}", "counterfactual_id"),
    ],
)
def test_a_cross_scope_404_is_byte_identical_to_an_absent_404(
    client, auth_alex, template: str, attribute: str
) -> None:
    """Section 1.7 asks for indistinguishable, not merely for the same code.

    A `details` block naming the identifier, a different message, or a
    different `Retry-After` would each be a side channel: an attacker with one
    valid id and one invented one could tell which of the two exists.
    """
    absent = uuid.UUID(int=0)
    foreign = client.get(template.format(getattr(ROB, attribute)), headers=auth_alex)
    unknown = client.get(template.format(absent), headers=auth_alex)

    assert foreign.status_code == unknown.status_code == 404
    left, right = foreign.json()["error"], unknown.json()["error"]
    left.pop("trace_id")
    right.pop("trace_id")
    assert left == right

    interesting = {"cache-control", "retry-after", "content-type"}
    assert {k.lower(): v for k, v in foreign.headers.items() if k.lower() in interesting} == {
        k.lower(): v for k, v in unknown.headers.items() if k.lower() in interesting
    }


# --------------------------------------------------------------------------
# 5 -- the internal surface
# --------------------------------------------------------------------------


def test_every_internal_handler_is_handed_the_binding_the_server_resolved(
    client, agent_headers, worker_headers, capability_proof, agent_bearer, fixture
) -> None:
    """Alex's proof reaches Alex's binding; Rob's proof reaches Rob's.

    Neither is chosen by the caller: the id selects a row and the row carries
    the ownership. Presenting Rob's id under Alex's dispatch does not reach
    Alex -- it reaches Rob, which is section 3.8's whole point, and is why a
    leaked capability id is not a cross-user hole.
    """
    client.get(f"/internal/v1/agent-runs/{ALEX.agent_run_id}", headers=agent_headers())
    _, binding, _ = fixture.internal.calls[-1]
    assert (binding.tenant_id, binding.user_id) == (ALEX.tenant_id, ALEX.user_id)

    rob_record = fixture.capabilities.records[("AGENT_RUN", str(ROB.agent_run_id))]
    client.get(
        f"/internal/v1/agent-runs/{ROB.agent_run_id}",
        headers={
            "Authorization": f"Bearer {agent_bearer}",
            "X-Provenance-Capability-Proof": capability_proof(
                "AGENT_RUN", ROB.agent_run_id, rob_record.expires_at
            ),
        },
    )
    _, binding, _ = fixture.internal.calls[-1]
    assert (binding.tenant_id, binding.user_id) == (ROB.tenant_id, ROB.user_id)

    client.post(
        "/internal/v1/ingest/artifacts",
        headers={**worker_headers("INGEST_JOB", ROB.alias_hash), **idem("sweep-alias")},
        json={
            "alias_hash": ROB.alias_hash,
            "s3_bucket": "provenance-inbound-us-east-1",
            "s3_key": "ses/2026/06/05/sweep",
            "source_message_id": "<sweep@example>",
            "sender": "billing@northlinebroadband.example",
            "recipient": "x@in.provenance.app",
            "subject": "Invoice",
            "received_at": "2026-06-05T14:19:00Z",
            "size_bytes": 2048,
            "content_sha256": "3f" * 32,
            "ses_verdicts": {
                "spf": "PASS",
                "dkim": "PASS",
                "dmarc": "PASS",
                "spam": "PASS",
                "virus": "PASS",
            },
        },
    )
    _, binding, _ = fixture.internal.calls[-1]
    assert binding.user_id == ROB.user_id


def test_a_retrieval_is_bound_to_its_own_run_and_reports_no_cross_user_rows(
    client, agent_headers, fixture
) -> None:
    """Evidence is the one surface with no per-row endpoint, so the isolation
    claim has to be made at the retrieval boundary instead."""
    response = client.post(
        f"/internal/v1/agent-runs/{ALEX.agent_run_id}/retrieval",
        headers=agent_headers(),
        json={"schema_version": "1.0", "top_k_vector": 20},
    )
    assert response.status_code == 200
    assert response.json()["retrieval_stats"]["cross_user_results"] == 0

    name, binding, _ = fixture.internal.calls[-1]
    assert name == "retrieve"
    assert binding.user_id == ALEX.user_id
    assert str(ROB.user_id) not in response.text
