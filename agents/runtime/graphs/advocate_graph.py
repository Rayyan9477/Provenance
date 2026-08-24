"""The Advocate graph: committed state -> action recommendation and draft.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 6, 7, 8, 12.
- ``docs/specs/14_PROMPTS.md`` sections 5 and 6.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.5.

Same construction as the ingestion graph: the topology is a constant, the walk
is a loop over it, and the only conditional edge is the early exit after
``classify_attention_need`` when the answer is ``NONE`` or ``FYI``.

Approval is not a graph state
-----------------------------
Section 6 is explicit: do not use the orchestrator's ``interrupt()`` as the
canonical approval record. A graph may pause and resume for UX, but the
approval that matters is a database transition authenticated as the user and
bound to the case revision and the draft's SHA-256. This module therefore ends
at ``create_action_intent`` and has no notion of approval at all -- the
capability is absent rather than unused, which is the difference between a
design and an intention.

Session storage is not memory
-----------------------------
As with ingestion: the recorder is write-only and every business fact comes
from :class:`~agents.runtime.state.StateProofReader`. See the
``agents/runtime/state.py`` module docstring, and
``agents/runtime/tests/test_no_write_tools.py`` for the AST check that keeps it
true.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from agents.runtime.nodes import advocate as nodes
from agents.runtime.nodes.advocate import select_action_type
from agents.runtime.state import (
    ADVOCATE_NODES,
    ActionIntentWriter,
    AdvocateGraphState,
    ModelRoute,
    ModelRouter,
    PromptRenderer,
    SessionRecorder,
    StateProofReader,
)

__all__ = [
    "ADVOCATE_NODES",
    "AdvocateDeps",
    "run_advocate",
    "select_action_type",
]


@dataclass(frozen=True, slots=True)
class AdvocateDeps:
    """The Advocate's whole tool surface: two reads and one write.

    Section 17: ``get_state_proof`` and ``get_action_policy`` to read,
    ``create_action_intent`` to write. There is no ``send_email`` here and no
    client from which one could be reached, so an injected "send this now" has
    nothing to call.
    """

    router: ModelRouter
    renderer: PromptRenderer
    proofs: StateProofReader
    intents: ActionIntentWriter
    session: SessionRecorder
    clock: Callable[[], datetime]
    attention_route: ModelRoute
    draft_route: ModelRoute


_NODE_FUNCTIONS: Final[dict[str, Callable[[AdvocateGraphState, Any], AdvocateGraphState]]] = {
    "load_state_proof": nodes.load_state_proof,
    "classify_attention_need": nodes.classify_attention_need,
    "select_action_template": nodes.select_action_template,
    "draft_action": nodes.draft_action,
    "validate_draft_claims": nodes.validate_draft_claims,
    "create_action_intent": nodes.create_action_intent,
}

# Asserted at import: a name added to the topology without an implementation
# fails on start-up rather than by silently skipping a step.
assert set(_NODE_FUNCTIONS) == set(ADVOCATE_NODES)


def run_advocate(state: AdvocateGraphState, deps: AdvocateDeps) -> AdvocateGraphState:
    """Walk section 6's sequence once, stopping at the first halt.

    ``NONE`` and ``FYI`` halt inside ``classify_attention_need`` by setting
    ``NO_ATTENTION``, so the early exit needs no branch here: the loop's own
    halt check is the conditional edge. One less place for the topology and the
    control flow to disagree.
    """
    for node in ADVOCATE_NODES:
        if state.halted:
            break
        state = _NODE_FUNCTIONS[node](state, deps)
    return state
