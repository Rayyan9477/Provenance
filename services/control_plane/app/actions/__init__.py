"""Action intents, approval, revalidation, and idempotent execution.

Invariant 4 lives here: no external effect from an uncommitted proposal, and an
approval is bound to a case revision and to the SHA-256 of the draft it
approved.

The sequence, which is the whole point
---------------------------------------
``CANONICAL_DECISIONS.md`` -> *Memory, action, and time* fixes it exactly:

    draft -> validate grounding -> create intent -> human approve -> bind
    approval to case revision AND draft SHA-256 -> revalidate -> execute
    idempotently

Every clause has a module:

===========================  ==================================================
``support_validation.py``    validate grounding, against a frozen snapshot of
                             the committed State Proof (``T9.1``, ``G9.3``)
``drafts.py``                the one canonical serialization both the approval
                             and the executor hash (``T9.2``)
``policy.py``                the recipient allowlist and the kill switch, both
                             default closed (``G9.5``, ``G9.6``)
``intents.py``               create, edit, approve, reject -- the approval
                             freeze writes both bindings in one statement
                             (``T9.2``, ``T9.3``, ``G9.2``)
``sink.py``                  the transport boundary: one protocol, one
                             recording implementation (``T9.5``)
``executor.py``              revalidate at execution time and send once
                             (``T9.4``-``T9.6``, ``G9.1``, ``G9.4``, ``G9.7``)
``store.py``                 the persistence protocol, its row types, and the
                             in-memory reference implementation
``store_postgres.py``        the same protocol over ``action_intents`` and
                             ``action_executions``
===========================  ==================================================

What this package never does
-----------------------------
It writes ``action_intents`` and ``action_executions`` and no other table. Both
are in the app-permitted enumeration in
``provenance_db.repositories.__init__``; neither is canonical, so
``python -m tools.write_path_lint`` counts none of these statements and the
Memory Kernel's canonical writes stay inside the Kernel. Re-measure with
``python -m tools.write_path_lint`` rather than quoting a count -- it was
fourteen in one module until Phase 10's outbox dispatcher landed, and any
number written here is a timestamp. What does not move is *which* modules
may hold one: the Kernel, and the dispatcher's ``UPDATE outbox_events SET
status`` under rule ``W5``.

The canonical consequences of an approval -- ``cases.revision``, the
``state_transitions`` row, the ``action.approved.v1`` outbox event -- are
expressed as :class:`~services.control_plane.app.actions.store.CanonicalRecorder`,
a protocol the caller binds to the Kernel. The action plane carries the
sequencing without acquiring the credential.

Binding this package
---------------------
``ActionIntentService`` covers ``WritePort.update_draft`` / ``approve`` /
``reject`` and ``InternalPort.create_action_intent``; ``ActionExecutor`` covers
``InternalPort.execute_action``. Both take an
:class:`~services.control_plane.app.actions.store.ActionScope`, which
:meth:`ActionScope.of` builds from an ``OwnerScope``, a ``Principal`` or a
``CapabilityBinding`` without unpacking two fields by hand. Both raise
:class:`~services.control_plane.app.actions.intents.ActionRefusedError` carrying a
``reason_code`` and a ``details`` mapping; mapping those to ``ErrorCode`` and a
status belongs to ``app/api/errors.py``, which owns that table.
"""

from __future__ import annotations

from services.control_plane.app.actions.drafts import (
    canonical_json_bytes,
    draft_digest,
    draft_digest_hex,
    draft_payload_of,
    merge_approved_draft,
    mint_idempotency_key,
)
from services.control_plane.app.actions.executor import (
    ActionExecutor,
    ExecutionOutcome,
    ProviderFinalError,
    ProviderTransientError,
    RevalidationInput,
    RevisionCheck,
    revalidate,
    revalidate_revision,
)
from services.control_plane.app.actions.intents import (
    ActionIntentService,
    ActionRefusedError,
    ApprovalRecord,
    ApproveRequest,
    CreatedIntent,
    CreateIntentRequest,
    DraftUpdate,
    RejectionRecord,
    RejectRequest,
    UpdateDraftRequest,
)
from services.control_plane.app.actions.policy import ActionPolicy
from services.control_plane.app.actions.sink import (
    ActionSink,
    DemoSink,
    SinkMessage,
    SinkReceipt,
)
from services.control_plane.app.actions.store import (
    ActionExecutionRow,
    ActionIntentRow,
    ActionScope,
    ActionStore,
    CanonicalRecorder,
    InMemoryActionStore,
    NullRecorder,
)
from services.control_plane.app.actions.store_postgres import PostgresActionStore
from services.control_plane.app.actions.support_validation import (
    GroundingSnapshot,
    GroundingVerdict,
    UnsupportedClaim,
    validate_draft_claims,
)

__all__ = [
    "ActionExecutionRow",
    "ActionExecutor",
    "ActionIntentRow",
    "ActionIntentService",
    "ActionPolicy",
    "ActionRefusedError",
    "ActionScope",
    "ActionSink",
    "ActionStore",
    "ApprovalRecord",
    "ApproveRequest",
    "CanonicalRecorder",
    "CreateIntentRequest",
    "CreatedIntent",
    "DemoSink",
    "DraftUpdate",
    "ExecutionOutcome",
    "GroundingSnapshot",
    "GroundingVerdict",
    "InMemoryActionStore",
    "NullRecorder",
    "PostgresActionStore",
    "ProviderFinalError",
    "ProviderTransientError",
    "RejectRequest",
    "RejectionRecord",
    "RevalidationInput",
    "RevisionCheck",
    "SinkMessage",
    "SinkReceipt",
    "UnsupportedClaim",
    "UpdateDraftRequest",
    "canonical_json_bytes",
    "draft_digest",
    "draft_digest_hex",
    "draft_payload_of",
    "merge_approved_draft",
    "mint_idempotency_key",
    "revalidate",
    "revalidate_revision",
    "validate_draft_claims",
]
