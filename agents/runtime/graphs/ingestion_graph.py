"""The Ingestion/Interpretation graph: evidence -> proposal -> commit result.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 1-5 and 16.
- ``docs/specs/14_PROMPTS.md`` sections 2, 3, 7, 10.
- ``docs/CANONICAL_DECISIONS.md`` -> *Multi-case artifacts*, *Canonical writer*,
  *Gemini model id canon*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.3 and T7.4.

Why the graph is a walk over a constant
---------------------------------------
:data:`~agents.runtime.state.INGESTION_NODES` is section 4's sequence as data,
and :func:`run_ingestion` walks it. Two things follow that a hand-wired
edge list does not give:

* the topology is assertable without executing anything, so "do not invent an
  eighth node" is a test on a tuple rather than a code review of control flow;
* the visit order a test prints is the same object the graph dispatched on, so
  the two cannot disagree.

There is exactly one conditional edge, at ``strong_resolution``, and its
predicate is :func:`should_resolve`.

Session storage is not memory
-----------------------------
The deps carry a :class:`~agents.runtime.state.SessionRecorder`, and every
business fact in this module comes from :class:`ArtifactReader`,
:class:`RetrievalService` or :class:`MemoryKernelClient` instead. A new run
reconstructs everything from those; it never requires a previous session. See
the ``agents/runtime/state.py`` module docstring for why the GenAI SDK's own
session and memory abstractions fall under the same prohibition, and
``agents/runtime/tests/test_no_write_tools.py`` for the check that enforces it
against the AST rather than against good intentions.

The ``PV_SABOTAGE`` hook
------------------------
``70_TASK_PLAN.md`` T7.4 registers ``should_resolve`` as a sabotage symbol.
Neutering it to the identity function makes it return its truthy argument, so
the resolver runs on every fixture and the "resolver is absent from the other
six" assertions go red -- which is the point. The call site below reaches the
symbol through this module's global namespace on purpose: a ``from``-import
would copy the reference before the rebind is visible and the sabotage would
silently never arrive.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Callable, Iterable, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from agents.runtime.nodes import ingestion as nodes
from agents.runtime.state import (
    INGESTION_NODES,
    ArtifactReader,
    EvidenceRegistrar,
    GraphError,
    IngestionGraphState,
    IngestionOutcome,
    MemoryKernelClient,
    ModelRoute,
    ModelRouter,
    PromptRenderer,
    ResolutionSignals,
    RetrievalService,
    SessionRecorder,
)
from provenance_contracts.ingestion import ExtractionResult
from provenance_domain.invariants import (
    IDENTITY_MARGIN_THRESHOLD,
    IDENTITY_STRONG_THRESHOLD,
)

__all__ = [
    "INGESTION_NODES",
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_ENV_VAR",
    "SABOTAGE_HOOKS",
    "IngestionDeps",
    "install_sabotage",
    "resolution_signals",
    "run_ingestion",
    "sabotage_targets",
    "should_resolve",
]


@dataclass(frozen=True, slots=True)
class IngestionDeps:
    """Everything the graph is allowed to reach, and nothing else.

    Assembling the tool surface as one frozen object means a reviewer can see
    the whole of it at once. There is no ``update_belief``, no ``resolve_case``
    and no ``send_email`` here, and there is no client object from which one
    could be reached: ``kernel`` accepts a typed proposal and returns a receipt.
    """

    router: ModelRouter
    renderer: PromptRenderer
    artifacts: ArtifactReader
    registrar: EvidenceRegistrar
    retrieval: RetrievalService
    kernel: MemoryKernelClient
    session: SessionRecorder
    clock: Callable[[], datetime]
    extraction_route: ModelRoute
    resolution_route: ModelRoute


# ---------------------------------------------------------------------------
# The one conditional edge
# ---------------------------------------------------------------------------


def should_resolve(signals: ResolutionSignals) -> bool:
    """Section 5.7, as a pure function of one value object.

    Invoke the Tier R resolver when any of the six documented conditions holds.
    The thresholds come from ``provenance_domain.invariants`` -- they are
    configuration, never prompt text, so tightening one is a reviewed change to
    a named constant rather than an edit inside a paragraph of English.

    The function takes a :class:`~agents.runtime.state.ResolutionSignals` rather
    than the retrieval object so that every branch below is reachable from a
    literal in a test, without a database, a model, or a graph run.
    """
    if signals.kernel_preflight_requests_resolution:
        return True
    if signals.contradicts_canonical_belief:
        return True
    if signals.validity_interval_ambiguous:
        return True
    if signals.commitment_supersession_possible:
        return True
    if signals.blocking_uncertainty:
        return True
    if signals.top_case_score is None or signals.top_case_score < IDENTITY_STRONG_THRESHOLD:
        return True
    return signals.identity_margin is not None and signals.identity_margin < (
        IDENTITY_MARGIN_THRESHOLD
    )


def resolution_signals(*, retrieval: Any, extraction: ExtractionResult | None) -> ResolutionSignals:
    """Deterministic assembly of :func:`should_resolve`'s input."""
    return nodes.resolution_signals_from(retrieval=retrieval, extraction=extraction)


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def run_ingestion(state: IngestionGraphState, deps: IngestionDeps) -> IngestionGraphState:
    """Walk section 4's sequence once, in order, and stop at the first halt.

    Every node returns a new state. The loop never raises: a node that fails
    records a :class:`~agents.runtime.state.GraphError` and a terminal outcome,
    and ``state.halted`` ends the walk with the visit order intact. A run that
    ended at ``validate_extraction_schema`` is therefore distinguishable from a
    run that never reached it, which is what a pending-review row needs in order
    to be actionable.
    """
    for node in INGESTION_NODES:
        if state.halted:
            break
        try:
            if node == "route_resolution_need":
                signals = resolution_signals(
                    retrieval=state.retrieval, extraction=state.extraction_result
                )
                # Through this module's global, so PV_SABOTAGE reaches it.
                resolve = bool(sys.modules[__name__].should_resolve(signals))
                state = nodes.route_resolution_need(state, deps, resolve=resolve)
                state = _replace_signals(state, signals)
                if not resolve:
                    continue
            elif node == "strong_resolution":
                if "RESOLVER_INVOKED" not in state.route_flags:
                    continue
                state = nodes.strong_resolution(state, deps)
            else:
                state = _NODE_FUNCTIONS[node](state, deps)
        except Exception as exc:  # the loop's contract is that it never raises
            state = _fail_safe(state, node, exc)
            break
    return state


#: How much of an exception's ``str`` reaches the recorded detail. Long enough
#: for a Pydantic message to name the offending field, short enough that a
#: 400-line traceback repr cannot become the row.
_DETAIL_MAX: Final[int] = 400


def _fail_safe(state: IngestionGraphState, node: str, exc: Exception) -> IngestionGraphState:
    """Convert an escaping exception into the terminal state the docstring promises.

    This function exists because the promise above was not kept. On 2026-08-29
    a live run against ``northline-final-invoice.eml`` ended with

        FAIL  graph walk raised  ValidationError escaped run_ingestion, which
        documents that the loop never raises: 1 validation error for
        MemoryProposal ... commitment cm_1 cites unknown claim cl_1

    recorded in ``ops/agent-graph-live-run.txt``. The model had emitted a
    commitment citing a claim id it never produced, ``MemoryProposal``'s
    validator correctly refused it, and the ``ValidationError`` then unwound the
    whole walk.

    That unwinding is the actual defect, not the model's mistake. A model
    proposing an inconsistent structure is an ordinary Tuesday and the validator
    catching it is the system working. But an exception leaving this loop
    discards the visit order, the partial state and the reason code -- the three
    things ``GraphError``'s own docstring says a pending-review row needs to be
    actionable -- and it does so precisely when the run most needs to be
    diagnosable. ``PENDING_REVIEW`` is a state, not an exception that unwinds.

    ``FAIL_SAFE`` rather than ``PENDING_HUMAN_REVIEW`` because the two say
    different things. Pending review means the system understood the artifact
    and wants a human decision; fail-safe means a node broke in a way nobody
    anticipated and nothing may be inferred from the partial state. Recording
    the second as the first would invite someone to action a run whose meaning
    is unknown.

    ``KeyboardInterrupt`` and ``SystemExit`` derive from ``BaseException`` and
    are deliberately not caught: an operator stopping a run is not a graph
    failure and must not be written down as one.
    """
    detail = f"{type(exc).__name__}: {exc}"
    if len(detail) > _DETAIL_MAX:
        detail = detail[: _DETAIL_MAX - 1].rstrip() + "…"
    return dataclasses.replace(
        state,
        visits=(*state.visits, node),
        errors=(*state.errors, GraphError(node=node, code="NODE_RAISED", detail=detail)),
        outcome=IngestionOutcome.FAIL_SAFE,
    )


def _replace_signals(state: IngestionGraphState, signals: ResolutionSignals) -> IngestionGraphState:
    return dataclasses.replace(state, resolution_signals=signals)


_NODE_FUNCTIONS: Final[dict[str, Callable[[IngestionGraphState, Any], IngestionGraphState]]] = {
    "load_artifact_metadata": nodes.load_artifact_metadata,
    "load_normalized_content": nodes.load_normalized_content,
    "extract_structured_evidence": nodes.extract_structured_evidence,
    "validate_extraction_schema": nodes.validate_extraction_schema,
    "register_or_lookup_evidence": nodes.register_or_lookup_evidence,
    "retrieve_candidate_context": nodes.retrieve_candidate_context,
    "build_memory_proposal": nodes.build_memory_proposal,
    "submit_to_memory_kernel": nodes.submit_to_memory_kernel,
    "route_commit_result": nodes.route_commit_result,
}

# Every node in the topology is either dispatched from the table above or
# handled by one of the two explicit branches in `run_ingestion`. Asserted at
# import so a name added to the constant without an implementation fails on
# start-up rather than by silently skipping a step.
assert set(_NODE_FUNCTIONS) | {"route_resolution_need", "strong_resolution"} == set(INGESTION_NODES)


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `quality/23_PHASE_GATES.md` G7.7 runs:
#
#     PV_SABOTAGE=agents.runtime.graphs.ingestion_graph.should_resolve \
#         pytest agents/runtime/tests -q; echo "exit=$?"
#
# and requires at least one FAILED and exit=1. A green run there would mean the
# topology tests do not actually depend on the predicate they claim to prove.

#: The environment variable the harness sets. Read once, at import.
SABOTAGE_ENV_VAR: Final[str] = "PV_SABOTAGE"

#: The symbols in *this module* the matrix is allowed to neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("should_resolve",)


def _identity(*args: Any, **kwargs: Any) -> Any:
    """Return the first argument unchanged.

    The neutering shape every ``PV_SABOTAGE`` hook uses: it type-checks at the
    call site, returns something plausible, and is wrong. Handed a
    :class:`~agents.runtime.state.ResolutionSignals`, it returns a truthy
    object, so the neutered predicate resolves every artifact and the "resolver
    is absent from six of the seven fixtures" assertions go red.
    """
    if args:
        return args[0]
    for value in kwargs.values():
        return value
    return None


def sabotage_targets(raw: str | None) -> frozenset[str]:
    """Parse ``PV_SABOTAGE`` into fully qualified symbol names.

    Whitespace- or comma-separated, so one run can neuter several symbols.
    Unset, empty and whitespace-only all mean "no sabotage".
    """
    if not raw:
        return frozenset()
    return frozenset(token for token in raw.replace(",", " ").split() if token)


def install_sabotage(
    namespace: MutableMapping[str, Any],
    module: str,
    symbols: Iterable[str],
    raw: str | None,
) -> tuple[str, ...]:
    """Replace each requested symbol of *module* in *namespace* with the identity.

    A pure function over an explicit namespace rather than import-time magic,
    so the mechanism is testable without re-importing a module or mutating the
    process environment.

    Raises:
        KeyError: a symbol was requested but is not defined here. A stale matrix
            entry must surface loudly; skipping it silently would report a green
            sabotage run for a symbol nobody neutered.
    """
    requested = sabotage_targets(raw)
    if not requested:
        return ()
    replaced: list[str] = []
    for symbol in symbols:
        if f"{module}.{symbol}" not in requested:
            continue
        if symbol not in namespace:
            raise KeyError(f"{module}.{symbol} is not defined in {module}")
        namespace[symbol] = _identity
        replaced.append(symbol)
    return tuple(replaced)


#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = install_sabotage(
    globals(), __name__, SABOTAGE_HOOKS, os.environ.get(SABOTAGE_ENV_VAR)
)
