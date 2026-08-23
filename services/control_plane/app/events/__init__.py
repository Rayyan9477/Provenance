"""Transactional outbox helpers. Delivery is at-least-once; consumers are idempotent.

The split this package exists to hold
--------------------------------------
The Memory Kernel writes an ``outbox_events`` row in the **same transaction** as
the state change it describes. This package moves those rows outward, and it
guarantees exactly one thing about delivery: **at least once**. Making it
exactly-once would require a distributed transaction between the database and a
message bus, which is precisely what the outbox pattern exists to avoid. The
other half of the contract lives in :mod:`consumer`, where ``processed_events``
turns "delivered more than once" into "applied exactly once".

Module map
----------
``transport``   the narrow publish Protocol and its in-process implementation.
``catalogue``   the closed event vocabulary and where each event is routed.
``dispatcher``  claim, publish, re-schedule, dead-letter, replay.
``consumer``    the dedupe transaction: one effect per ``(consumer, event_id)``.

The one canonical write
-----------------------
:mod:`dispatcher` issues ``UPDATE outbox_events SET status = ...`` and nothing
else against a canonical table. It is the single enumerated exception to the
Kernel being the sole canonical writer, because it is status bookkeeping about a
row the Kernel already wrote and carries no domain meaning. Nothing here may
*author* an event: an event written outside the transaction that produced the
state is a claim about state that may never have been committed.
"""

from __future__ import annotations

__all__ = ["catalogue", "consumer", "dispatcher", "transport"]
