"""The three bindings behind the demo's closing argument.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` sections 8.30, 8.31 and 8.33.
- ``docs/ops/41_RUNBOOK.md`` section 8.1 step 8 -- the assertions this exists
  to make executable.
- ``services/control_plane/app/api/adapters/unbound.py`` -- the register these
  three entries were deleted from.

What "bound" has to mean here
------------------------------
Not "the method returns a dict". A hard-coded pair of strings satisfying the
runbook's assertions would pass a shape test and be exactly the lie
``CANONICAL_DECISIONS.md`` -> *Judge Mode* forbids. So every test below drives
the **real** service over the **real** graph and asserts that the answer came
from the machinery: the router was called once per side with the same system
prompt, the two ``agent_runs`` rows were written with the memory modes the
schema's CHECK constraints allow, and the parity block was computed from those
rows rather than from the object that produced them.

The case revision is *measured*, not declared
----------------------------------------------
``safety.case_revision_changed_by_counterfactual`` is the guarantee that the
demo's most dramatic step cannot damage the record it is demonstrating. A
constant ``false`` would satisfy the runbook and prove nothing, so the store is
asked for the revision before the runs and again after them and the flag is the
comparison. ``test_a_revision_that_moved_is_reported_rather_than_hidden``
drives a store whose revision changes underneath the run and asserts the flag
flips -- which is the only way to know the ``false`` in the happy path is a
measurement.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agents.runtime.model_router.models import (
    DEFAULT_EXTRACTION_MODEL_ID,
    DEFAULT_REASONING_MODEL_ID,
    ModelCallRecord,
    ModelConfigError,
    ModelInvocationError,
    PendingReview,
    RouterSuccess,
)
from agents.runtime.prompts.render import content_block
from agents.runtime.schemas.counterfactual import CounterfactualReading
from provenance_contracts.ingestion import ContentBlockKind, SourceLocator
from provenance_domain.enums import ModelTier
from services.control_plane.app.api.adapters import UNBOUND
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.counterfactual.artifacts import ArtifactBytesUnavailableError
from services.control_plane.app.counterfactual.probe import ModelProbeService
from services.control_plane.app.counterfactual.service import (
    ArtifactFacts,
    CaseFacts,
    CounterfactualService,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID("eaf56bfd-2fa3-5de4-bf55-34478e87b351")
USER = uuid.UUID("88f54715-2808-58e8-8591-93f515ee21ba")
ARTIFACT = uuid.UUID("efd261e6-1a78-5cca-8c90-2d3579cc385a")
CASE = uuid.UUID("3f8360c5-d785-5392-9c95-e7e4710010d4")
SCOPE = OwnerScope(tenant_id=TENANT, user_id=USER)
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
SHA = "7d2f" + "0" * 60

ARTIFACT_FACTS = ArtifactFacts(
    artifact_id=ARTIFACT,
    content_sha256=SHA,
    mime_type="message/rfc822",
    subject="Final invoice",
    summary="Final invoice - message/rfc822 - 512 bytes",
)
CASE_FACTS = CaseFacts(case_id=CASE, title="Old ISP final bill reconciliation", revision=6)


def _payload(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "artifact_id": ARTIFACT,
        "modes": None,
        "memory_on_strategy": "REPLAY_COMMITTED",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _reading(mode: str) -> CounterfactualReading:
    if mode == "MEMORY_OFF":
        return CounterfactualReading(
            headline="Final invoice for USD 74.20, payable within twenty-one days.",
            classification="ROUTINE_DOCUMENT",
            conflicts_detected=0,
            recommended_action="NONE",
            draft_text="",
            why="Read on its own the document is a valid invoice.",
        )
    return CounterfactualReading(
        headline="Your own zero-balance statement contradicts this invoice.",
        classification="COUNTERPARTY_CLAIM_CONTRADICTING_RECORD",
        conflicts_detected=1,
        recommended_action="OUTBOUND_EMAIL_DISPUTE",
        draft_text="I am writing about the final invoice on this account.",
        why="The record carries a payment confirmation and a zero-balance statement.",
        support_ids=("31ad04bb-c264-40ec-9d0f-28ab7c129734",),
    )


class _Router:
    """Answers per invocation, and remembers exactly what it was asked."""

    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = list(results or [])
        self.seen: list[dict[str, str]] = []

    def invoke(self, node_name: str, *, system: str, user_text: str, contract: Any) -> Any:
        self.seen.append({"node": node_name, "system": system, "user_text": user_text})
        if self.results:
            return self.results.pop(0)
        mode = "MEMORY_ON" if "balance_owed" in user_text else "MEMORY_OFF"
        return RouterSuccess(
            node=node_name,
            value=_reading(mode),
            model_id="gemini-3.7-flash",
            prompt_version="pv-draft-1.0.0",
            repaired=False,
            calls=(
                ModelCallRecord(
                    seq=1,
                    node=node_name,
                    model_id="gemini-3.7-flash",
                    prompt_version="pv-draft-1.0.0",
                    input_tokens=100,
                    output_tokens=200,
                    repair_attempts=0,
                    duration_ms=1200,
                    started_at=NOW,
                    tier=ModelTier.R,
                    outcome="OK",
                ),
            ),
            logical_attempts=1,
        )


@dataclass
class _Store:
    """The database boundary, with the revision under the test's control."""

    revisions: list[int] = field(default_factory=lambda: [6, 6])
    evidence_counts: list[int] = field(default_factory=lambda: [4, 4])
    artifact: ArtifactFacts | None = ARTIFACT_FACTS
    case: CaseFacts | None = CASE_FACTS
    committed_draft: bool = False
    corpus: int = 16035
    inserted: list[dict[str, Any]] = field(default_factory=list)
    settled: list[dict[str, Any]] = field(default_factory=list)

    async def artifact_facts(self, scope: OwnerScope, artifact_id: uuid.UUID) -> Any:
        assert scope == SCOPE
        return self.artifact

    async def case_facts(self, scope: OwnerScope, artifact_id: uuid.UUID) -> Any:
        return self.case

    async def state_proof(self, scope: OwnerScope, case_id: uuid.UUID) -> Any:
        return {"case_revision": 6, "beliefs": [{"predicate": "balance_owed"}], "conflicts": []}

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None:
        return self.revisions.pop(0) if self.revisions else None

    async def evidence_count(self, scope: OwnerScope, case_id: uuid.UUID) -> int:
        return self.evidence_counts.pop(0) if self.evidence_counts else 0

    async def corpus_size(self, scope: OwnerScope) -> int:
        return self.corpus

    async def has_committed_draft(self, scope: OwnerScope, artifact_id: uuid.UUID) -> bool:
        return self.committed_draft

    async def open_run(self, params: dict[str, Any]) -> None:
        self.inserted.append(dict(params))

    async def settle_run(self, params: dict[str, Any]) -> bool:
        self.settled.append(dict(params))
        return True

    async def read_pair(self, scope: OwnerScope, trace_id: uuid.UUID) -> list[dict[str, Any]]:
        """The rows as the database would hand them back.

        Assembled from what was actually inserted and settled rather than from
        a literal, so a service that wrote the wrong ``memory_mode`` or lost the
        model calls fails the parity assertions rather than passing them
        against a fixture that agrees with the test instead of with the code.
        """
        rows: list[dict[str, Any]] = []
        for insert in self.inserted:
            if insert["trace_id"] != trace_id:
                continue
            settle = next((s for s in self.settled if s["id"] == insert["id"]), {})
            rows.append(
                {
                    "id": insert["id"],
                    "trace_id": insert["trace_id"],
                    "graph_name": insert["graph_name"],
                    "graph_version": insert["graph_version"],
                    "memory_mode": insert["memory_mode"],
                    "is_counterfactual": insert["is_counterfactual"],
                    "status": settle.get("status", "RUNNING"),
                    "started_at": insert["started_at"],
                    "finished_at": settle.get("finished_at"),
                    "input_artifact_id": insert["input_artifact_id"],
                    "allowed_case_ids": insert["allowed_case_ids"].obj,
                    "retrieval_candidate_count": insert["retrieval_candidate_count"],
                    "error_code": settle.get("error_code"),
                    "model_calls": (settle.get("model_calls").obj if settle else []),
                    "tool_calls": [],
                    "capability_status": (
                        settle.get("capability_status").obj
                        if settle
                        else insert["capability_status"].obj
                    ),
                }
            )
        return sorted(rows, key=lambda row: row["memory_mode"], reverse=True)


class _Artifacts:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def blocks_for(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        text = "Final invoice for internet service. Amount due USD 74.20."
        return (
            content_block(
                artifact_id=kwargs["artifact_id"],
                block_id="blk_0001",
                ordinal=0,
                kind=ContentBlockKind.BODY,
                text=text,
                source_locator=SourceLocator(
                    kind="TEXT_SPAN", block_id="blk_0001", char_start=0, char_end=len(text)
                ),
            ),
        )


def _service(
    *,
    store: _Store | None = None,
    router: _Router | None = None,
    artifacts: _Artifacts | None = None,
    router_factory: Any = None,
) -> tuple[CounterfactualService, _Store, _Router]:
    store = store or _Store()
    router = router or _Router()
    service = CounterfactualService(
        store=store,
        router_factory=router_factory or (lambda: router),
        artifacts=artifacts or _Artifacts(),
        clock=lambda: NOW,
    )
    return service, store, router


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ==========================================================================
# 0. The register
# ==========================================================================


def test_the_three_methods_are_no_longer_declared_unbound() -> None:
    """Wiring a method means deleting a line, which is visible in a diff."""
    for method in ("write.start_counterfactual", "write.get_counterfactual", "write.run_probe"):
        assert method not in UNBOUND, f"{method} is bound but still declared unbound"


def _port(service: Any, probe: Any = None) -> Any:
    from services.control_plane.app.api.adapters import KernelWritePort

    return KernelWritePort(
        SimpleNamespace(),
        kernel_pool=None,
        read=SimpleNamespace(),
        policy=SimpleNamespace(),
        clock=lambda: NOW,
        counterfactual=service,
        probe=probe or SimpleNamespace(),
    )


def test_the_write_port_answers_rather_than_refusing() -> None:
    """The register saying a method is bound and the method answering are two
    facts, and only the second one is the one a caller experiences."""
    service, store, _ = _service()
    port = _port(service)
    body = _run(port.start_counterfactual(SCOPE, _payload()))
    assert body is not None and body["status"] == "COMPLETED"
    poll = _run(port.get_counterfactual(SCOPE, uuid.UUID(body["counterfactual_id"])))
    assert poll is not None and poll["parity"]["all_equal"] is True
    assert _run(port.get_counterfactual(SCOPE, uuid.uuid4())) is None


#: The two ids the probe actually asks for, taken from the configuration.
#:
#: These were written out as "gemini-3.5-flash-lite" and "gemini-3.7-flash".
#: When Tier R was swapped to gemini-3.6-flash on 2026-08-31 -- because 3.7 was
#: answering 503 UNAVAILABLE -- four tests failed with `KeyError:
#: 'gemini-3.6-flash'`, because the fake response map no longer had a key for
#: the id the code asked for. The tests were pinned to a configuration value
#: rather than to the behaviour they are about, which is whether the probe asks
#: for each configured id and reports what answered. Deriving them means the
#: next swap costs nothing.
CONFIG_IDS: tuple[str, str] = (DEFAULT_EXTRACTION_MODEL_ID, DEFAULT_REASONING_MODEL_ID)


def test_the_write_port_runs_the_probe() -> None:
    client = _ProbeClient(
        {
            CONFIG_IDS[0]: '{"ok":true,"echo":"PROVENANCE"}',
            CONFIG_IDS[1]: '{"ok":true,"echo":"PROVENANCE"}',
        }
    )
    port = _port(_service()[0], probe=_probe(client))
    body = _run(port.run_probe(SCOPE, SimpleNamespace(probe_type="MODEL_AVAILABILITY")))
    assert body["counts"]["PASS"] == 2


# ==========================================================================
# 1. Section 8.30 -- the run
# ==========================================================================


def test_an_artifact_that_is_not_this_callers_returns_none() -> None:
    """``None`` is the route's 404. Never a 403: section 1.7."""
    service, _, router = _service(store=_Store(artifact=None))
    assert _run(service.start(SCOPE, _payload())) is None
    assert router.seen == [], "a model was called for an artifact this caller cannot see"


def test_both_sides_run_and_each_leaves_a_settled_agent_runs_row() -> None:
    service, store, router = _service()
    body = _run(service.start(SCOPE, _payload()))
    assert body is not None and body["status"] == "COMPLETED"
    assert len(router.seen) == 2, "one model call per side, and no more"
    assert {row["graph_name"] for row in store.inserted} == {"counterfactual"}
    assert [row["memory_mode"] for row in store.inserted] == ["OFF", "ON"]
    assert [row["is_counterfactual"] for row in store.inserted] == [True, False]
    assert {row["status"] for row in store.settled} == {"SUCCEEDED"}


def test_the_memory_off_run_is_scoped_to_no_case_and_no_corpus() -> None:
    """Section 8.30 safety property 1, as columns rather than as prose."""
    service, store, _ = _service()
    _run(service.start(SCOPE, _payload()))
    off, on = store.inserted
    assert off["allowed_case_ids"].obj == []
    assert off["retrieval_candidate_count"] == 0
    assert on["allowed_case_ids"].obj == [str(CASE)]
    assert on["retrieval_candidate_count"] == 16035


def test_neither_run_is_written_as_holding_the_proposal_tool() -> None:
    service, store, _ = _service()
    _run(service.start(SCOPE, _payload()))
    for row in store.settled:
        assert row["capability_status"].obj["proposal_tool_bound"] is False


def test_both_sides_receive_the_same_system_prompt() -> None:
    service, _, router = _service()
    _run(service.start(SCOPE, _payload()))
    assert router.seen[0]["system"] == router.seen[1]["system"]
    assert "prompt_version: pv-draft-1.0.0" in router.seen[0]["system"]


def test_only_the_memory_on_side_is_shown_canonical_state() -> None:
    service, _, router = _service()
    _run(service.start(SCOPE, _payload()))
    off_text, on_text = router.seen[0]["user_text"], router.seen[1]["user_text"]
    assert "balance_owed" not in off_text
    assert "balance_owed" in on_text
    assert '"state_proof":null' in off_text.replace(" ", "")


def test_a_pending_side_makes_the_run_partial_rather_than_completed() -> None:
    pending = PendingReview(
        node="draft_action",
        reason_code="MODEL_INVOCATION_FAILED",
        failures=(),
        calls=(),
        logical_attempts=1,
    )
    router = _Router(results=[pending])
    service, store, _ = _service(router=router)
    body = _run(service.start(SCOPE, _payload()))
    assert body is not None and body["status"] == "PARTIAL"
    assert [row["status"] for row in store.settled] == ["FAILED", "SUCCEEDED"]
    assert store.settled[0]["error_code"] == "MODEL_INVOCATION_FAILED"


def test_artifact_bytes_that_cannot_be_read_refuse_with_the_reason() -> None:
    """``D-00-005``: CANNOT RUN carries a reason and calls no model."""
    artifacts = _Artifacts(ArtifactBytesUnavailableError("no file hashes to 7d2f..."))
    service, store, router = _service(artifacts=artifacts)
    body = _run(service.start(SCOPE, _payload()))
    assert body is not None
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "ARTIFACT_BYTES_UNAVAILABLE"
    assert "no file hashes to" in body["error"]["message"]
    assert router.seen == []
    assert [row["error_code"] for row in store.settled] == [
        "ARTIFACT_BYTES_UNAVAILABLE",
        "ARTIFACT_BYTES_UNAVAILABLE",
    ]


def test_an_unconfigured_model_is_a_recorded_refusal_rather_than_a_500() -> None:
    def explode() -> Any:
        raise ModelConfigError("GOOGLE_API_KEY is not set")

    service, store, _ = _service(router_factory=explode)
    body = _run(service.start(SCOPE, _payload()))
    assert body is not None and body["error"]["code"] == "MODEL_UNCONFIGURED"
    assert len(store.inserted) == 2, "the attempt is in the ledger even though nothing ran"


def test_replay_committed_falls_back_to_a_sandboxed_rerun_and_discloses_it() -> None:
    """Section 8.30: RERUN_SANDBOXED "exists only to show the reasoning path
    when no committed result is available"."""
    service, store, _ = _service()
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    body = _run(service.get(SCOPE, counterfactual_id))
    assert body is not None
    assert body["memory_on"]["strategy"] == "RERUN_SANDBOXED"
    assert "no committed Advocate draft" in body["memory_on"]["strategy_reason"]


def test_a_committed_draft_keeps_the_replay_strategy() -> None:
    service, store, _ = _service(store=_Store(committed_draft=True))
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    body = _run(service.get(SCOPE, counterfactual_id))
    assert body is not None and body["memory_on"]["strategy"] == "REPLAY_COMMITTED"


# ==========================================================================
# 2. Section 8.31 -- the parity block and the safety block
# ==========================================================================


def test_an_unknown_counterfactual_is_none() -> None:
    service, _, _ = _service()
    assert _run(service.get(SCOPE, uuid.uuid4())) is None


def test_the_parity_block_has_its_six_fields_and_is_computed_from_the_rows() -> None:
    service, _, _ = _service()
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    parity = _run(service.get(SCOPE, counterfactual_id))["parity"]
    assert set(parity) == {
        "artifact_id",
        "artifact_sha256",
        "model_id",
        "prompt_version",
        "graph_version",
        "decode_params_sha256",
        "all_equal",
    }
    assert parity["all_equal"] is True
    assert parity["artifact_sha256"]["off"] == SHA


def test_parity_fails_when_the_two_rows_disagree_on_the_model() -> None:
    """The render gate is only worth anything if the block can say false."""
    service, store, router = _service()
    _run(service.start(SCOPE, _payload()))
    counterfactual_id = store.inserted[0]["trace_id"]
    # Rewrite one persisted row the way a genuinely different route would have.
    store.settled[1]["model_calls"].obj[0]["model_id"] = "gemini-3.5-flash-lite"
    body = _run(service.get(SCOPE, counterfactual_id))
    assert body["parity"]["model_id"]["equal"] is False
    assert body["parity"]["all_equal"] is False


def test_a_revision_that_did_not_move_is_reported_as_measured() -> None:
    service, store, _ = _service()
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    safety = _run(service.get(SCOPE, counterfactual_id))["safety"]
    assert safety["measured"] is True
    assert safety["case_revision_before"] == 6
    assert safety["case_revision_after"] == 6
    assert safety["case_revision_changed_by_counterfactual"] is False
    assert safety["memory_off_wrote_canonical_state"] is False
    assert safety["memory_off_admitted_evidence"] is False
    assert safety["memory_off_had_proposal_tool"] is False


def test_a_revision_that_moved_is_reported_rather_than_hidden() -> None:
    """The only way to know the ``false`` above is a measurement."""
    service, store, _ = _service(store=_Store(revisions=[6, 7], evidence_counts=[4, 5]))
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    safety = _run(service.get(SCOPE, counterfactual_id))["safety"]
    assert safety["case_revision_changed_by_counterfactual"] is True
    assert safety["memory_off_admitted_evidence"] is True
    assert safety["memory_off_wrote_canonical_state"] is True


def test_the_two_outputs_are_the_readings_the_router_returned() -> None:
    service, store, _ = _service()
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    body = _run(service.get(SCOPE, counterfactual_id))
    assert body["memory_off"]["output"]["classification"] == "ROUTINE_DOCUMENT"
    assert body["memory_off"]["output"]["recommended_action"] == "NONE"
    assert body["memory_on"]["output"]["classification"] == (
        "COUNTERPARTY_CLAIM_CONTRADICTING_RECORD"
    )
    assert body["delta"]["conflicts_detected"] == {"off": 0, "on": 1}


def test_a_poll_from_a_process_that_did_not_run_it_says_so() -> None:
    """Absence is not emptiness: ``output: null`` carries a reason."""
    service, store, _ = _service()
    counterfactual_id = uuid.UUID(_run(service.start(SCOPE, _payload()))["counterfactual_id"])
    cold = CounterfactualService(
        store=store, router_factory=lambda: _Router(), artifacts=_Artifacts(), clock=lambda: NOW
    )
    body = _run(cold.get(SCOPE, counterfactual_id))
    assert body["memory_off"]["output"] is None
    assert body["output_retention"]["readings_available"] is False
    assert "not persisted" in body["output_retention"]["reason"]
    assert body["safety"]["measured"] is False


# ==========================================================================
# 3. Section 8.33 -- the probe
# ==========================================================================


class _ProbeClient:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def generate(self, request: Any) -> Any:
        self.asked.append(request.model_id)
        answer = self.answers[request.model_id]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(text=answer, input_tokens=12, output_tokens=8)


def _probe(client: Any, *, key: str | None = "k") -> ModelProbeService:
    from pydantic import SecretStr

    from agents.runtime.model_router.models import GeminiRouterConfig

    config = GeminiRouterConfig(api_key=SecretStr(key) if key else None)
    return ModelProbeService(
        config_factory=lambda: config,
        client_factory=lambda _config: client,
        clock=lambda: NOW,
    )


def test_the_probe_invokes_each_configured_id_and_reports_what_answered() -> None:
    config_ids = CONFIG_IDS
    ok = '{"ok":true,"echo":"PROVENANCE"}'
    client = _ProbeClient({config_ids[0]: ok, config_ids[1]: ok})
    body = _run(_probe(client).run(SimpleNamespace(probe_type="MODEL_AVAILABILITY")))
    assert client.asked == list(config_ids)
    assert body["counts"]["PASS"] == 2
    assert [r["model_id"] for r in body["results"]] == list(config_ids)


def test_an_answer_that_does_not_satisfy_the_schema_is_a_fail() -> None:
    """A length check would have called this a pass. ``D-00-046``."""
    config_ids = CONFIG_IDS
    client = _ProbeClient({config_ids[0]: "{}", config_ids[1]: "I cannot help with that."})
    body = _run(_probe(client).run(SimpleNamespace(probe_type="MODEL_AVAILABILITY")))
    assert body["counts"] == {"PASS": 0, "FAIL": 2, "CANNOT_RUN": 0}
    assert "do not satisfy ProbeAnswer" in body["results"][0]["detail"]


def test_an_id_that_fails_to_invoke_is_a_fail_and_not_an_exception() -> None:
    client = _ProbeClient(
        {
            CONFIG_IDS[0]: ModelInvocationError("429 RESOURCE_EXHAUSTED"),
            CONFIG_IDS[1]: '{"ok":true,"echo":"PROVENANCE"}',
        }
    )
    body = _run(_probe(client).run(SimpleNamespace(probe_type="MODEL_AVAILABILITY")))
    assert body["counts"] == {"PASS": 1, "FAIL": 1, "CANNOT_RUN": 0}
    assert "429" in body["results"][0]["detail"]


def test_no_api_key_is_cannot_run_and_no_call_is_attempted() -> None:
    """``CANNOT RUN`` is not ``FAIL``. The ids are not being blamed."""
    client = _ProbeClient({})
    body = _run(_probe(client, key=None).run(SimpleNamespace(probe_type="MODEL_AVAILABILITY")))
    assert client.asked == []
    assert body["counts"] == {"PASS": 0, "FAIL": 0, "CANNOT_RUN": 2}
    assert "GOOGLE_API_KEY" in body["results"][0]["detail"]
