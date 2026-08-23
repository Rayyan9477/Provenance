"""Repositories, split by domain. None of them writes a canonical table — T3.3.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 12 — write-path ownership. The Kernel
  being the sole canonical writer is a **grant**, not a convention.
- ``specs/13_RETRIEVAL_SPEC.md`` section 19 — module layout.
- ``CANONICAL_DECISIONS.md`` -> *Names and counts*:
  ``provenance_db.repositories.evidence.ann_search()`` is the ANN entry point.
- ``EXECUTION/70_TASK_PLAN.md`` T3.3.

The boundary, stated so it is legible rather than implied
---------------------------------------------------------
**No module in this package writes a canonical table.** The canonical set is
exactly the tables ``pv_kernel_writer`` may ``INSERT`` or ``UPDATE``:
``counterparties``, ``relationships``, ``contexts``, ``cases``,
``evidence_items``, ``claims``, ``beliefs``, ``belief_versions``,
``belief_support``, ``conflicts``, ``commitments``, ``fulfillments``,
``state_transitions``, ``memory_proposals``, ``kernel_decisions``,
``prospective_triggers`` and ``outbox_events``. Those writes live in
``services/control_plane/app/memory_kernel/`` and nowhere else;
``tools/write_path_lint.py`` (T4.1) checks it across ``packages/``.

**Non-canonical writes are permitted here and are enumerated.** They are the
ones ``pv_app_reader_writer`` owns outright — ``idempotency_records``,
``agent_runs``, ``processed_events``, ``action_intents``,
``action_executions``, ``source_artifacts``, ``tenants``, ``users``,
``ingest_aliases`` — plus exactly one write against a canonical table: the
dispatcher's ``UPDATE outbox_events SET status = ...``, which is status
bookkeeping about a row the Kernel wrote and carries no domain meaning. None
of those writes exists yet: Phase 3 declares reads. Each arrives with the
phase that needs it, and the enumeration above is what a reviewer checks the
addition against.

**Every read is user-scoped by construction.** A read method takes a
``Principal`` or an explicit ``(tenant_id, user_id)`` pair, and the predicate
lives in the SQL rather than in the caller's discipline.
``tests/retrieval/test_no_unscoped_sql.py`` (``G6.4``) scans for this later;
making it structurally impossible now is cheaper than fixing it then.

Why the bodies raise ``NotImplementedError``
--------------------------------------------
The canonical schema is built in Phase 2 and read models in Phase 5. What T3.3
buys is the *shape*: one entry point per read, with its scoping predicate,
before three call sites grow independently. The task plan asks for exactly
this for ``ann_search()``, and the same reasoning applies to its neighbours.
"""

from __future__ import annotations

__all__ = [
    "actions",
    "agent_runs",
    "artifacts",
    "beliefs",
    "cases",
    "commitments",
    "conflicts",
    "contexts",
    "dashboard",
    "evidence",
    "events",
    "relationships",
    "timeline",
    "triggers",
    "users",
]
