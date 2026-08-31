"""The Judge Mode counterfactual: one graph, walked twice, memory on and off.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` sections 8.30 and 8.31.
- ``docs/specs/14_PROMPTS.md`` section 6.4 -- MEMORY OFF uses
  ``pv-draft-1.0.0`` unchanged, with an empty TRUSTED STRUCTURED CONTEXT block.
- ``docs/CANONICAL_DECISIONS.md`` -> *Counterfactual*, *Counterfactual prompt*,
  *Counterfactual parity canon*, *Judge Mode*.

Why the topology is a constant walked by both sides
----------------------------------------------------
"The same graph" is the load-bearing half of the counterfactual's claim, and a
docstring saying so is not a mechanism. :data:`~agents.runtime.state.
COUNTERFACTUAL_NODES` is walked identically by both modes, and the only node
whose behaviour depends on the mode is ``bind_memory`` -- whose entire job is
to differ.

Why MEMORY OFF does not call the binder
----------------------------------------
It would be easy to write ``context = deps.memory.bind(mode)`` and have the
binder return the empty block for ``MEMORY_OFF``. That reads the same and is
materially weaker: the memory-off path would then *have* a live route into
canonical state, guarded by a conditional in a collaborator. Here the node
substitutes :data:`EMPTY_MEMORY_CONTEXT` and never touches ``deps.memory`` at
all, so "memory off could not see memory" is a property of the control flow.
``tests/test_counterfactual_graph.py`` proves it with a binder that raises on
contact.

Why there is no proposal tool, and no write of any kind
--------------------------------------------------------
Section 8.30's first safety property. This module imports nothing that can
write: no kernel client, no evidence registrar, no action-intent writer. The
AST check in ``tests/test_no_write_tools.py`` covers the package; the absence
here is the reason it can.

What this module does NOT decide
---------------------------------
It does not choose the artifact, does not read the database, does not write the
``agent_runs`` row and does not compute the ``parity`` block. Those are the
control plane's, in ``services/control_plane/app/counterfactual/``. This is the
graph: contexts in, one model call, a typed reading or a typed refusal out.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from agents.runtime.model_router.models import (
    ModelCallRecord,
    OutputContract,
    PendingReview,
    RouterResult,
    RouterSuccess,
    ValidationFailure,
)
from agents.runtime.model_router.router import route
from agents.runtime.model_router.wire_schema import to_wire_schema
from agents.runtime.schemas.counterfactual import (
    COUNTERFACTUAL_SCHEMA_VERSION,
    CounterfactualReading,
)
from agents.runtime.state import (
    COUNTERFACTUAL_NODES,
    GRAPH_NAME_COUNTERFACTUAL,
    GRAPH_VERSION_COUNTERFACTUAL,
    GraphError,
    PromptRenderer,
)
from provenance_contracts.ingestion import ContentBlock

__all__ = [
    "COUNTERFACTUAL_NODES",
    "EMPTY_MEMORY_CONTEXT",
    "GRAPH_NAME_COUNTERFACTUAL",
    "GRAPH_VERSION_COUNTERFACTUAL",
    "PROMPT_VERSION",
    "ROUTE_NODE",
    "CounterfactualDeps",
    "CounterfactualMode",
    "CounterfactualOutcome",
    "CounterfactualState",
    "MemoryBinder",
    "decode_params_digest",
    "initial_counterfactual_state",
    "run_counterfactual",
]

CounterfactualMode = Literal["MEMORY_OFF", "MEMORY_ON"]

#: The router key, and therefore the route. ``draft_action`` is Tier R,
#: ``pv-draft-1.0.0``, ``effort=HIGH``, ``max_output_tokens=16000`` -- the exact
#: configuration ``14_PROMPTS.md`` section 6.4's parity table names. The graph
#: node is called ``draft_reading`` and the *route* is ``draft_action``: one is
#: a position in this topology, the other is an entry in the router's static
#: table, and collapsing them would make the counterfactual's decode parameters
#: a function of this module rather than of section 8.
ROUTE_NODE: Final[str] = "draft_action"

PROMPT_VERSION: Final[str] = route(ROUTE_NODE).prompt_version

#: ``14_PROMPTS.md`` section 6.4, literally. Present, correctly delimited, and
#: empty -- not absent. An absent block would change the prompt's shape and so
#: change what is being compared; an empty one changes only what memory said.
EMPTY_MEMORY_CONTEXT: Final[Mapping[str, Any]] = {
    "state_proof": None,
    "retrieval": {
        "corpus_size_visible": 0,
        "evidence": [],
        "beliefs": [],
        "conflicts": [],
        "commitments": [],
    },
}


class CounterfactualOutcome(enum.Enum):
    """Three outcomes, and ``CANNOT_RUN`` is not ``FAILED`` (``D-00-005``)."""

    COMPLETED = "COMPLETED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    CANNOT_RUN = "CANNOT_RUN"


class MemoryBinder(Protocol):
    """Supplies the TRUSTED STRUCTURED CONTEXT for the MEMORY ON side."""

    def bind(self, mode: str) -> Mapping[str, Any]: ...


class CounterfactualRouter(Protocol):
    """``agents.runtime.model_router.ModelRouter``, structurally.

    Typed as a Protocol rather than as the class so the graph is drivable with
    no SDK and no key, and so the control plane can pass the real router
    without this module importing ``google.genai`` transitively.
    """

    def invoke(
        self,
        node_name: str,
        *,
        system: str,
        user_text: str,
        contract: OutputContract[Any],
    ) -> RouterResult[Any]: ...


@dataclass(frozen=True, slots=True)
class CounterfactualDeps:
    """Two collaborators and a renderer. There is no writer in this list."""

    router: CounterfactualRouter
    renderer: PromptRenderer
    memory: MemoryBinder


@dataclass(frozen=True, slots=True)
class CounterfactualState:
    """One side of the comparison, start to finish."""

    mode: CounterfactualMode
    artifact_id: UUID
    artifact_sha256: str
    blocks: tuple[ContentBlock, ...]
    decode_params_sha256: str
    trusted_context: Mapping[str, Any] | None = None
    reading: CounterfactualReading | None = None
    calls: tuple[ModelCallRecord, ...] = ()
    model_id: str = ""
    prompt_version: str = PROMPT_VERSION
    graph_name: str = GRAPH_NAME_COUNTERFACTUAL
    graph_version: str = GRAPH_VERSION_COUNTERFACTUAL
    schema_version: str = COUNTERFACTUAL_SCHEMA_VERSION
    visits: tuple[str, ...] = ()
    errors: tuple[GraphError, ...] = field(default_factory=tuple)
    outcome: CounterfactualOutcome | None = None

    @property
    def halted(self) -> bool:
        return self.outcome is not None


def decode_params_digest(*, model_id: str) -> str:
    """The ``decode_params_sha256`` of ``specs/15_API_SPEC.md`` section 8.31.

    Every input is read from the router's static route table and from the
    response contract, so the two sides agree **by construction** rather than
    by both being handed the same literal. It is still compared rather than
    assumed: parity that is computed from one source and then asserted against
    itself proves nothing, so the engine hashes each side separately from its
    own recorded model id and compares the results.
    """
    spec = route(ROUTE_NODE)
    payload = {
        "model_id": model_id,
        "max_output_tokens": spec.max_output_tokens,
        "effort": spec.effort,
        "thinking": spec.thinking,
        "tools_enabled": spec.tools_enabled,
        "schema_version": COUNTERFACTUAL_SCHEMA_VERSION,
        "response_schema": to_wire_schema(CounterfactualReading),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def initial_counterfactual_state(
    *,
    mode: CounterfactualMode,
    artifact_id: UUID,
    artifact_sha256: str,
    blocks: Sequence[ContentBlock],
    model_id: str = "",
) -> CounterfactualState:
    """The state before ``bind_memory``, with the decode digest already fixed."""
    return CounterfactualState(
        mode=mode,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        blocks=tuple(blocks),
        decode_params_sha256=decode_params_digest(model_id=model_id),
        model_id=model_id,
    )


def _visit(state: CounterfactualState, node: str, **updates: Any) -> CounterfactualState:
    return dataclasses.replace(state, visits=(*state.visits, node), **updates)


def _fail(
    state: CounterfactualState,
    node: str,
    *,
    code: str,
    detail: str,
    outcome: CounterfactualOutcome,
) -> CounterfactualState:
    return _visit(
        state,
        node,
        errors=(*state.errors, GraphError(node=node, code=code, detail=detail)),
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# bind_memory
# ---------------------------------------------------------------------------


def bind_memory(state: CounterfactualState, deps: CounterfactualDeps) -> CounterfactualState:
    """Supply the trusted block. The one node whose job is to differ by mode."""
    node = "bind_memory"
    if not state.blocks:
        return _fail(
            state,
            node,
            code="NO_ARTIFACT_CONTENT",
            detail=(
                "no content blocks were supplied for this artifact, so there is nothing "
                "for either side to read; a reading produced from an empty document "
                "would describe the prompt rather than the artifact"
            ),
            outcome=CounterfactualOutcome.CANNOT_RUN,
        )
    if state.mode == "MEMORY_OFF":
        # deps.memory is deliberately not touched. See the module docstring.
        return _visit(state, node, trusted_context=EMPTY_MEMORY_CONTEXT)
    return _visit(state, node, trusted_context=deps.memory.bind(state.mode))


# ---------------------------------------------------------------------------
# draft_reading
# ---------------------------------------------------------------------------


def _no_semantic_checks(_: CounterfactualReading) -> Sequence[ValidationFailure]:
    """Layer 2 lives in the contract's own validators; there is no third layer.

    ``CounterfactualReading`` already refuses a recommendation with no draft and
    a draft with no recommendation. A second copy of that rule here would be a
    second definition of the same thing, which is how the two drift.
    """
    return ()


def draft_reading(state: CounterfactualState, deps: CounterfactualDeps) -> CounterfactualState:
    """The single model call. Same prompt, same schema, same budget, both sides."""
    node = "draft_reading"
    assert state.trusted_context is not None  # bind_memory halts otherwise
    rendered = deps.renderer.render_user(trusted_context=state.trusted_context, blocks=state.blocks)
    outcome = deps.router.invoke(
        ROUTE_NODE,
        system=deps.renderer.render_system(PROMPT_VERSION),
        user_text=rendered.user_text,
        contract=OutputContract(model=CounterfactualReading, validate=_no_semantic_checks),
    )
    calls = (*state.calls, *outcome.calls)
    if isinstance(outcome, PendingReview):
        # The records of a failed attempt are kept: the call happened, it cost
        # money and latency, and `agent_runs.model_calls` is what makes the
        # model claim checkable against persisted state rather than a README.
        return _fail(
            dataclasses.replace(state, calls=calls),
            node,
            code=outcome.reason_code,
            detail="the drafting call did not produce a valid reading inside its budget",
            outcome=CounterfactualOutcome.PENDING_HUMAN_REVIEW,
        )
    assert isinstance(outcome, RouterSuccess)
    return _visit(
        state,
        node,
        reading=outcome.value,
        calls=calls,
        model_id=outcome.model_id,
        prompt_version=outcome.prompt_version,
        decode_params_sha256=decode_params_digest(model_id=outcome.model_id),
        outcome=CounterfactualOutcome.COMPLETED,
    )


_NODE_FUNCTIONS: Final[dict[str, Any]] = {
    "bind_memory": bind_memory,
    "draft_reading": draft_reading,
}

# Asserted at import, as the other two graphs do: a name in the topology with
# no implementation must fail on start-up rather than by silently skipping.
assert set(_NODE_FUNCTIONS) == set(COUNTERFACTUAL_NODES)


def run_counterfactual(state: CounterfactualState, deps: CounterfactualDeps) -> CounterfactualState:
    """Walk the topology once. Never raises: a failure is a typed outcome."""
    for node in COUNTERFACTUAL_NODES:
        if state.halted:
            break
        state = _NODE_FUNCTIONS[node](state, deps)
    return state
