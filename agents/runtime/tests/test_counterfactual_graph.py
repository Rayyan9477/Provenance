"""The counterfactual graph: one topology, two memory bindings, no third thing.

What these tests are actually defending
---------------------------------------
``CANONICAL_DECISIONS.md`` -> *Judge Mode* forbids a scripted animation, and
``CANONICAL_DECISIONS.md`` -> *Counterfactual parity canon* permits exactly
four differences between the two sides. Both statements are about code that
does not exist unless something checks it, so:

* the same node tuple is walked by both sides;
* the same prompt asset, schema, effort and token budget reach the router;
* the untrusted evidence is byte-identical between the sides;
* and MEMORY OFF **cannot** reach the memory binder at all -- the node does not
  call it, which is a stronger claim than "it calls it and ignores the answer".

The last one is asserted with a binder that raises on contact. A binder that
returned an empty dict would let a future edit start consulting it and every
test here would still pass.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from agents.runtime.graphs.counterfactual_graph import (
    EMPTY_MEMORY_CONTEXT,
    PROMPT_VERSION,
    ROUTE_NODE,
    CounterfactualDeps,
    CounterfactualOutcome,
    decode_params_digest,
    initial_counterfactual_state,
    run_counterfactual,
)
from agents.runtime.model_router.models import (
    ModelCallRecord,
    PendingReview,
    RouterSuccess,
)
from agents.runtime.prompts.render import NONCE_PATTERN, AssetPromptRenderer
from agents.runtime.schemas.counterfactual import CounterfactualReading
from agents.runtime.state import COUNTERFACTUAL_NODES
from provenance_contracts.ingestion import ContentBlock, ContentBlockKind, SourceLocator
from provenance_domain.enums import ModelTier

pytestmark = pytest.mark.unit

ARTIFACT_ID = uuid.UUID("efd261e6-1a78-5cca-8c90-2d3579cc385a")
SHA = "a" * 64


def _block(text: str = "Amount due USD 186.00 by 30 June 2026.") -> ContentBlock:
    return ContentBlock(
        artifact_id=ARTIFACT_ID,
        block_id="blk_0001",
        ordinal=0,
        kind=ContentBlockKind.BODY,
        text=text,
        content_sha256="b" * 64,
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id="blk_0001", char_start=0, char_end=len(text)
        ),
    )


def _reading(**overrides: Any) -> CounterfactualReading:
    fields: dict[str, Any] = {
        "headline": "Invoice for USD 186.00 due 30 June.",
        "classification": "ROUTINE_DOCUMENT",
        "conflicts_detected": 0,
        "recommended_action": "NONE",
        "draft_text": "",
        "why": "Nothing here contradicts anything I was given.",
    }
    fields.update(overrides)
    return CounterfactualReading(**fields)


class RecordingRouter:
    """Captures every argument the graph hands the router."""

    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.seen: list[dict[str, Any]] = []

    def invoke(self, node: str, *, system: str, user_text: str, contract: Any) -> Any:
        self.seen.append(
            {"node": node, "system": system, "user_text": user_text, "contract": contract}
        )
        if self.result is not None:
            return self.result
        return RouterSuccess(
            node=node,
            value=_reading(),
            model_id="gemini-3.7-flash",
            prompt_version=PROMPT_VERSION,
            repaired=False,
            calls=(
                ModelCallRecord(
                    seq=1,
                    node=node,
                    model_id="gemini-3.7-flash",
                    prompt_version=PROMPT_VERSION,
                    input_tokens=10,
                    output_tokens=20,
                    repair_attempts=0,
                    duration_ms=5,
                    started_at=datetime(2026, 8, 24, tzinfo=UTC),
                    tier=ModelTier.R,
                    outcome="OK",
                ),
            ),
            logical_attempts=1,
        )


class ExplodingBinder:
    """Contact is the failure. See the module docstring."""

    def bind(self, mode: str) -> dict[str, Any]:
        raise AssertionError(f"the memory binder was consulted under {mode}")


class ProofBinder:
    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context
        self.modes: list[str] = []

    def bind(self, mode: str) -> dict[str, Any]:
        self.modes.append(mode)
        return self.context


ON_CONTEXT = {
    "case_id": "a2d57a2a-d335-5597-978c-3ec31f8b1ae2",
    "case_revision": 12,
    "corpus_size_visible": 16035,
    "state_proof": {"beliefs": [{"predicate": "service_active"}]},
}


def _deps(router: Any, binder: Any) -> CounterfactualDeps:
    return CounterfactualDeps(router=router, renderer=AssetPromptRenderer(), memory=binder)


def _run(mode: str, router: Any, binder: Any, blocks: Sequence[ContentBlock] | None = None) -> Any:
    state = initial_counterfactual_state(
        mode=mode,
        artifact_id=ARTIFACT_ID,
        artifact_sha256=SHA,
        blocks=tuple(blocks if blocks is not None else (_block(),)),
    )
    return run_counterfactual(state, _deps(router, binder))


# -- topology ---------------------------------------------------------------


def test_both_modes_walk_the_same_nodes_in_the_same_order() -> None:
    off = _run("MEMORY_OFF", RecordingRouter(), ExplodingBinder())
    on = _run("MEMORY_ON", RecordingRouter(), ProofBinder(dict(ON_CONTEXT)))
    assert off.visits == COUNTERFACTUAL_NODES
    assert on.visits == COUNTERFACTUAL_NODES


def test_memory_off_never_consults_the_memory_binder() -> None:
    """The node does not call it. Not: it calls it and discards the answer."""
    state = _run("MEMORY_OFF", RecordingRouter(), ExplodingBinder())
    assert state.trusted_context == EMPTY_MEMORY_CONTEXT
    assert state.outcome is CounterfactualOutcome.COMPLETED


def test_memory_on_binds_the_proof_it_was_given() -> None:
    binder = ProofBinder(dict(ON_CONTEXT))
    state = _run("MEMORY_ON", RecordingRouter(), binder)
    assert binder.modes == ["MEMORY_ON"]
    assert state.trusted_context == ON_CONTEXT


def test_the_empty_context_is_the_shape_section_6_4_prints() -> None:
    """``state_proof: null`` and a zeroed retrieval block, present and empty."""
    assert EMPTY_MEMORY_CONTEXT["state_proof"] is None
    retrieval = EMPTY_MEMORY_CONTEXT["retrieval"]
    assert retrieval == {
        "corpus_size_visible": 0,
        "evidence": [],
        "beliefs": [],
        "conflicts": [],
        "commitments": [],
    }


# -- parity by construction --------------------------------------------------


def test_both_sides_send_the_same_prompt_the_same_schema_and_the_same_node() -> None:
    off_router, on_router = RecordingRouter(), RecordingRouter()
    _run("MEMORY_OFF", off_router, ExplodingBinder())
    _run("MEMORY_ON", on_router, ProofBinder(dict(ON_CONTEXT)))
    (off_call,), (on_call,) = off_router.seen, on_router.seen
    assert off_call["node"] == on_call["node"] == ROUTE_NODE
    assert off_call["system"] == on_call["system"]
    assert off_call["contract"].model is on_call["contract"].model is CounterfactualReading


def test_the_untrusted_evidence_is_byte_identical_between_the_two_sides() -> None:
    """The only permitted difference is the trusted block's contents."""
    off_router, on_router = RecordingRouter(), RecordingRouter()
    _run("MEMORY_OFF", off_router, ExplodingBinder())
    _run("MEMORY_ON", on_router, ProofBinder(dict(ON_CONTEXT)))
    marker = "=== UNTRUSTED EVIDENCE ==="
    off_tail = off_router.seen[0]["user_text"].split(marker, 1)[1]
    on_tail = on_router.seen[0]["user_text"].split(marker, 1)[1]
    # The nonce differs per invocation by design (14_PROMPTS.md 2.2). Blank it
    # and the two halves must be equal character for character -- a containment
    # check would pass while one side carried an extra paragraph.
    assert NONCE_PATTERN.sub("N", off_tail) == NONCE_PATTERN.sub("N", on_tail)
    assert _block().text in off_tail


def test_the_trusted_block_is_where_the_two_sides_differ() -> None:
    off_router, on_router = RecordingRouter(), RecordingRouter()
    _run("MEMORY_OFF", off_router, ExplodingBinder())
    _run("MEMORY_ON", on_router, ProofBinder(dict(ON_CONTEXT)))
    assert "service_active" in on_router.seen[0]["user_text"]
    assert "service_active" not in off_router.seen[0]["user_text"]


def test_the_decode_digest_is_equal_across_modes_and_moves_when_decoding_does() -> None:
    off = _run("MEMORY_OFF", RecordingRouter(), ExplodingBinder())
    on = _run("MEMORY_ON", RecordingRouter(), ProofBinder(dict(ON_CONTEXT)))
    assert off.decode_params_sha256 == on.decode_params_sha256
    assert off.decode_params_sha256 == decode_params_digest(model_id="gemini-3.7-flash")
    assert decode_params_digest(model_id="gemini-3.7-flash") != decode_params_digest(
        model_id="gemini-3.5-flash-lite"
    )


# -- failure is a value ------------------------------------------------------


def test_a_pending_router_result_becomes_an_outcome_rather_than_an_exception() -> None:
    pending = PendingReview(
        node=ROUTE_NODE,
        reason_code="SCHEMA_REPAIR_EXHAUSTED",
        failures=(),
        calls=(),
        logical_attempts=2,
    )
    state = _run("MEMORY_OFF", RecordingRouter(pending), ExplodingBinder())
    assert state.outcome is CounterfactualOutcome.PENDING_HUMAN_REVIEW
    assert state.reading is None
    assert state.errors and state.errors[0].code == "SCHEMA_REPAIR_EXHAUSTED"


def test_a_run_with_no_content_blocks_refuses_rather_than_drafting_from_nothing() -> None:
    """``D-00-005``: no artifact text is CANNOT RUN, never an empty reading."""
    router = RecordingRouter()
    state = _run("MEMORY_OFF", router, ExplodingBinder(), blocks=())
    assert state.outcome is CounterfactualOutcome.CANNOT_RUN
    assert state.errors[0].code == "NO_ARTIFACT_CONTENT"
    assert router.seen == [], "a model was called with no artifact to read"


def test_the_model_calls_are_kept_so_the_run_row_can_be_attributed() -> None:
    state = _run("MEMORY_OFF", RecordingRouter(), ExplodingBinder())
    assert [c.model_id for c in state.calls] == ["gemini-3.7-flash"]
    assert state.model_id == "gemini-3.7-flash"
    assert state.prompt_version == PROMPT_VERSION
