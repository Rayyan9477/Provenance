"""What is not wired yet, named, in one place.

Authority
---------
- ``services/control_plane/app/api/ports.py`` -- the 47 methods.
- ``implementation/00_IMPLEMENTATION_MAP.md`` -- which phase owns each
  subsystem the entries below are waiting on.

Why a register rather than a ``NotImplementedError`` per method
----------------------------------------------------------------
Two reasons.

**An empty list is a lie.** A read method with no backing that returned
``None`` or ``[]`` renders in the UI as "no conflicts on this case" or "no
obligations outstanding" -- indistinguishable from a real empty result, and
believable enough that nobody investigates. The one thing a memory product
must never do is claim confidently that it has nothing. So every unbound
method raises, and the message names the subsystem it is waiting on rather
than saying "not implemented".

**A register can be counted.** Scattered ``raise NotImplementedError`` calls
cannot answer "how much of the surface is live?" without grepping, and the
answer drifts silently as methods get wired. This dict is the answer, it is
asserted by ``tests/api/test_port_adapters.py``, and wiring a method means
deleting a line from it -- which is a visible act in a diff.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn

__all__ = ["UNBOUND", "unbound"]

#: ``port.method`` -> what it needs before it can be bound.
#:
#: Every value names a subsystem or a spec section, because a message that
#: says "not implemented" sends the reader to grep and a message that says
#: "the trace assembler (section 8.28's seventeen node types)" sends them to
#: the thing that has to exist.
UNBOUND: Mapping[str, str] = {
    # -- ReadPort ---------------------------------------------------------
    # Kept under the 300-character envelope cap deliberately: this exact string
    # is what Judge Mode renders, and a message that needs truncating to reach a
    # judge arrives mangled. errors._fit would now cut it cleanly, but a clean
    # cut is still a cut. It says the whole thing instead.
    "read.get_trace": (
        "the trace assembler. Section 8.28 builds the DAG from persisted "
        "runtime rows and spans, and CANONICAL_DECISIONS.md -> Judge Mode "
        "forbids a scripted animation, so it cannot be synthesised from "
        "state_transitions alone. Needs app/observability to persist spans "
        "first."
    ),
    "read.memory_trace": (
        "the trace assembler, as read.get_trace. Section 8.29 renders the same "
        "DAG per case, from the same absent source. Returning an empty "
        "traces[] would read as 'memory did nothing on this case', the "
        "opposite of what this endpoint exists to show."
    ),
    # -- WritePort --------------------------------------------------------
    "write.create_correction": (
        "app/ingestion, and two mechanisms under it that do not exist anywhere. "
        "Section 8.14 makes a correction first-class evidence, so the path is: "
        "admit an evidence_items row under the app grant (write rule W4), "
        "insert the memory_proposals row the same rule permits, build a typed "
        "MemoryProposal, then call the Kernel -- which only UPDATEs that row, so "
        "it has to exist first. Two things stop it. (1) MemoryProposal.model is "
        "a required ModelAttribution and ModelTier has exactly E, R and "
        "EMBEDDING; there is no way to say 'no model produced this', which is "
        "what a user's typed sentence IS. The column already has the value -- "
        "ck_memory_proposals_model admits 'deterministic.kernel' and every "
        "memory_proposals row carries it -- so the schema can say it and the "
        "contract cannot, and filling the field with a chat id would write a "
        "false attribution into the row CANONICAL_DECISIONS.md -> Disclosure "
        "makes the model claim checkable against. scripts/seed/proposals.py "
        "records the same gap and works around it for disclosed fixture data "
        "only. (2) RETRACT_EVIDENCE has no writer at all: step 5 asks the "
        "Kernel to set evidence_items.retraction_status, and "
        "memory_kernel.transaction.CANONICAL_WRITE_STATEMENTS enumerates every "
        "canonical write the Kernel holds and no evidence_items UPDATE is among "
        "them -- which write rule W2 forbids every other module from holding, "
        "W4 granting the app an INSERT and deliberately not an UPDATE."
    ),
    "write.rotate_ingest_alias": (
        "the ingest-alias minting path. Section 8.22 issues a new alias and "
        "disables the old one, and the plaintext token is returned exactly once "
        "-- which means the HMAC minting has to happen here, not in a seed."
    ),
    # -- InternalPort -----------------------------------------------------
    "internal.retrieve": (
        "the retrieval pipeline's executor. app/retrieval has the ANN statement, "
        "the predicates, the reranker and the stage order, but no module that "
        "runs stages A-G end to end and returns section 9.5's candidate set."
    ),
    "internal.create_action_intent": (
        "agent_runs.model_calls[].prompt_version at intent time, plus a "
        "kind-bearing State Proof on the action plane's own boundary. "
        "ActionIntentService.create is bound-ready and its CreateIntentRequest "
        "takes a provenance_contracts.actions.DraftAction, which section 9.8's "
        "body cannot fill on its own -- correctly so. The body carries no model "
        "block ON PURPOSE: if it did, a caller could CLAIM which model ran, and "
        "agent_runs.model_route is precisely what makes the submission's model "
        "disclosure checkable against persisted state rather than against a "
        "README (CANONICAL_DECISIONS.md -> Disclosure). Five of "
        "ModelAttribution's six fields are therefore already on the row "
        "agent_run_id names: graph_name and graph_version are NOT NULL columns, "
        "model_id is model_route.tier_r, provider follows from the id shape the "
        "validator already dispatches on, and tier is R. The sixth, "
        "prompt_version, is on agent_runs.model_calls[] -- a JSONB NULL column "
        "written only by POST /internal/v1/agent-runs/{id}/complete (section "
        "9.9), which settles the run. Section 9.8 runs BEFORE that, so the "
        "column is null by ordering, not by oversight. Section 9.9 is BOUND as "
        "of 2026-08-24 and that does not move this: its one writer is "
        "app/observability/runs.py::SETTLE_AGENT_RUN_SQL, which still runs at "
        "run completion, so an intent created at 9.8 still reads NULL. "
        "tests/api/test_port_adapters.py asserts that model_calls has exactly "
        "one writer rather than asserting 9.9 is unbound, because the second "
        "is a state that legitimately changed and the first is the property. "
        "Second gap: DraftClaim.support_kind and DraftAction."
        "basis_proof_hash are both derivable from a State Proof -- the kind by "
        "which group of StateProof.support_ids() an id came from, the hash by "
        "StateProof.compute_hash() -- but ActionStore.grounding_snapshot hands "
        "this adapter a flat frozenset[UUID] carrying neither. Re-reading the "
        "proof here to recover them would build a second definition of 'why "
        "does Provenance believe this', which is the exact thing "
        "internal.run_state_proof delegates to the read port to avoid. "
        "requested_outcome, draft_id, claim_id and the claim offsets are NOT "
        "blockers: the first is on the wire (absent means 422, not invention) "
        "and the rest are mintable or derivable from the body. Nothing here is "
        "fixed by adding a field to app/api/schemas/internal.py -- adding one "
        "would hand the model claim back to the caller, which is the failure "
        "this entry exists to prevent."
    ),
}


def unbound(name: str) -> NoReturn:
    """Refuse *name*, naming what it is waiting on.

    Raises:
        NotImplementedError: always. That is the point -- see the module
            docstring on why an empty result would be worse.
        KeyError: *name* is not in the register, which means a method started
            refusing without being declared. That is a bug in the adapter, and
            failing here rather than raising a vague ``NotImplementedError``
            keeps the register honest.
    """
    raise NotImplementedError(f"{name} is not bound yet: {UNBOUND[name]}")
