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
    "read.get_trace": (
        "the trace assembler. Section 8.28 defines seventeen closed node types "
        "and builds the DAG from persisted runtime rows and spans; "
        "CANONICAL_DECISIONS.md -> Judge Mode forbids a scripted animation, so "
        "this cannot be synthesised from state_transitions alone. Needs "
        "app/observability to persist spans first."
    ),
    "read.memory_trace": (
        "the trace assembler, as read.get_trace. Section 8.29 renders the same "
        "DAG per case; it is the same shape from the same absent source, and "
        "returning an empty traces[] would read as 'memory did nothing on this "
        "case', which is the opposite of what this endpoint exists to show."
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
    "write.upload_intent": (
        "the object-store client. Section 8.18 returns a pre-signed PUT for a key "
        "the *server* chooses, and there is no S3/GCS client wired into the "
        "control plane: nothing under app/ calls generate_presigned_url or "
        "builds a storage client, and the one boto3 client in the tree is "
        "app/retrieval/embeddings.py's bedrock-runtime, which is a model client "
        "and not a store. No bucket is configured either -- S3_ARTIFACT_BUCKET "
        "and GCS_ARTIFACT_BUCKET are both unset, which is why "
        "main._feature_flags already reports upload_ingest_enabled=false. "
        "Returning a URL that does not work would be worse than refusing."
    ),
    "write.complete_artifact": (
        "the object-store client, then app/ingestion. Section 8.19 steps 1-3 are "
        "a HeadObject, a ContentLength comparison and a ChecksumSHA256 "
        "comparison against the stored object, so all three need the client "
        "write.upload_intent is waiting on; step 5 enqueues the interpretation "
        "run, which is Phase 7. There is also nothing for it to complete: the "
        "only artifact reaching this endpoint is one write.upload_intent "
        "created, and that is refused for the same missing client."
    ),
    "write.rotate_ingest_alias": (
        "the ingest-alias minting path. Section 8.22 issues a new alias and "
        "disables the old one, and the plaintext token is returned exactly once "
        "-- which means the HMAC minting has to happen here, not in a seed."
    ),
    "write.start_counterfactual": (
        "the agent runtime. CANONICAL_DECISIONS.md -> Counterfactual requires the "
        "same artifact, model, prompt and graph on both sides with only "
        "retrieval and canonical memory differing; that parity is only "
        "provable by actually running the graph twice."
    ),
    "write.get_counterfactual": (
        "the agent runtime, as write.start_counterfactual. Section 8.31's parity "
        "block compares six fields recorded by the two runs, and "
        "parity.all_equal = false must suppress the output columns -- there is "
        "nothing to read until the runs exist."
    ),
    "write.run_probe": (
        "the model router. The probe invokes the configured Gemini ids and "
        "records what answered; CANONICAL_DECISIONS.md -> Gemini model id canon "
        "marks every id '# PROBE REQUIRED' until ops/gemini-probe.txt exists."
    ),
    # -- InternalPort -----------------------------------------------------
    "internal.ingest_artifact": (
        "the object-store client, then app/ingestion. Section 9.1's body carries "
        "the key the SES Lambda wrote -- 'ses/2026/06/05/...' in the spec's own "
        "example -- and source_artifacts refuses it: "
        "ck_source_artifacts_s3_key_shape is CHECK (s3_key LIKE 'raw/%'), so the "
        "INSERT fails at the database rather than at a missing helper. Section "
        "8.18 fixes the layout at raw/{tenant_id}/{user_id}/{artifact_id}/"
        "original, so the row can only be written once the bytes have been "
        "copied into that prefix -- and what copies them is the client "
        "write.upload_intent is waiting on. Synthesising a raw/ key without the "
        "copy satisfies the CHECK and stores a locator for bytes nobody wrote."
    ),
    "internal.artifact_content": (
        "the parser first, then the object-store client. Section 9.3 hands the "
        "graph the parsed content blocks for the artifact its capability is bound "
        "to, and nothing in this system has ever produced or stored one: no "
        "migration creates a table or a column that holds a block, app/ingestion "
        "and workers/textract_complete each hold nothing but a docstring, and "
        "agents/runtime/state.py's ArtifactReader is a Protocol whose only "
        "implementation is a test fake. Section 8.18 puts parser output at "
        "normalized/{tenant_id}/{user_id}/{artifact_id}/parser-v{n}.json, in the "
        "store that does not exist either. Every source_artifacts row the seed "
        "writes carries parser_status='PARSED' and parser_version='seed-1.0.0', "
        "so that column asserts a parse whose output nobody can read back."
    ),
    "internal.register_evidence": (
        "app/ingestion, and two of section 9.4's five steps have no mechanism. "
        "Admitting the row is the easy half -- evidence_items INSERT is "
        "app-permitted under write rule W4 -- and it belongs in the ingestion "
        "module where the parser is, not in an API adapter. Steps 1 and 2 are "
        "the block-exists and span-is-inside-the-block checks, which the spec "
        "calls the deterministic defence against a model inventing a quotation, "
        "and they have nothing to check against: see internal.artifact_content, "
        "the endpoint a run would have fetched those blocks from. Step 4 stamps "
        "a server-computed embedding, and the embedder this build ships -- "
        "GeminiEmbedder over gemini-embedding-2, 1536 wide -- returns a vector "
        "evidence_items cannot hold: the applied column is VECTOR(1024) and "
        "ck_evidence_embedding_model admits only amazon.titan-embed-text-v2:0. "
        "Migration 0009 widens both and is deliberately unapplied. The one legal "
        "write is embedding=NULL, which silently excludes the row from every ANN "
        "query -- in a table that is append-only, so it cannot be corrected in "
        "place."
    ),
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
