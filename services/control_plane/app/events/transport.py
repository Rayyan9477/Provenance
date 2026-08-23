"""The event transport boundary — a narrow Protocol and one in-process impl.

Why this is a Protocol and not an SQS client
--------------------------------------------
SQS and EventBridge left the design with the pivot to Cloud Run and the Gemini
Developer API. Rather than guess at a replacement bus and build an integration
nothing yet needs, the dispatcher publishes through :class:`EventTransport` and
this module ships :class:`InProcessTransport` to satisfy it. Choosing the
eventual bus is a wiring decision; the outbox semantics that matter —
transactional write, at-least-once delivery, idempotent consumption — are
identical whichever bus arrives, and they are what this phase proves.

Why the payload is validated here rather than trusted
-----------------------------------------------------
An event bus is a log with extra steps, and the rule that keeps document text
out of logs applies to it unchanged. An event is a **pointer to committed
state**, not a copy of it: a consumer that needs the text reads it through an
authorised API using the ids in the payload. :data:`FORBIDDEN_PAYLOAD_KEYS`
comes from ``provenance_contracts.events`` so there is one list rather than two
that drift.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from provenance_contracts.events import (
    FORBIDDEN_PAYLOAD_KEYS,
    MAX_EVENT_PAYLOAD_BYTES,
)

__all__ = [
    "FORBIDDEN_PAYLOAD_KEYS",
    "MAX_EVENT_PAYLOAD_BYTES",
    "EventTransport",
    "InProcessTransport",
    "PublishedEvent",
    "TransportError",
]


class TransportError(RuntimeError):
    """The bus refused an entry, or could not be reached.

    A typed error rather than whatever the client raised, because the
    dispatcher's retry decision turns on it: this is retryable with backoff,
    while an arbitrary exception escaping the transport is a bug and should
    crash loudly rather than be re-scheduled five times.
    """


def _forbidden_keys_in(payload: object, *, depth: int = 0) -> set[str]:
    if depth > 6:
        return set()
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_PAYLOAD_KEYS:
                found.add(str(key))
            found |= _forbidden_keys_in(value, depth=depth + 1)
    elif isinstance(payload, list | tuple):
        for item in payload:
            found |= _forbidden_keys_in(item, depth=depth + 1)
    return found


@dataclass(frozen=True, slots=True)
class PublishedEvent:
    """One outbox row, on its way out.

    Mirrors ``provenance_contracts.events.DomainEvent`` field for field, as a
    plain frozen dataclass: the dispatcher builds thousands of these from
    database rows in a sweep and does not need Pydantic's coercion on a path
    where every value already came out of a typed column. The two validations
    that are *not* redundant — no document text, and a payload size cap — are
    re-applied here, because the dispatcher is the last place that can refuse.
    """

    event_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    trace_id: uuid.UUID
    occurred_at: datetime
    payload_version: str
    payload: Mapping[str, Any]
    causation_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        forbidden = _forbidden_keys_in(dict(self.payload))
        if forbidden:
            raise ValueError(
                "event payload contains keys that may carry document text or "
                f"credentials: {sorted(forbidden)}; publish ids, not contents"
            )
        encoded = json.dumps(
            dict(self.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError(
                f"event payload is {len(encoded)} bytes, over the "
                f"{MAX_EVENT_PAYLOAD_BYTES} byte cap; reference the aggregate "
                "instead of embedding it"
            )

    def aggregate_key(self) -> tuple[uuid.UUID, int, str]:
        """``(aggregate_id, aggregate_version, event_type)`` — the fact's identity.

        Two deliveries with this triple describe the same committed fact,
        whatever their ``event_id``. ``event_id`` is the *delivery* identity and
        is what a consumer dedupes on.
        """
        return (self.aggregate_id, self.aggregate_version, self.event_type)

    def dedupe_key(self, consumer_name: str) -> tuple[str, uuid.UUID]:
        """The primary key of ``processed_events``. The only key a consumer may use."""
        return (consumer_name, self.event_id)


@runtime_checkable
class EventTransport(Protocol):
    """Publish a batch of events, or raise :class:`TransportError`.

    Deliberately one method. A transport that also acknowledged, deleted or
    polled would be a queue client, and the dispatcher would start depending on
    a bus's semantics instead of on the outbox's.
    """

    async def publish(self, events: Sequence[PublishedEvent]) -> None: ...


@dataclass
class InProcessTransport:
    """Records what it was handed. The implementation this phase runs against.

    Not a test double: it is the honest statement of where the pivot left
    delivery. The dispatcher's guarantees — a row is claimed by exactly one
    sweeper, a failure is re-scheduled on the published backoff, an exhausted
    row goes ``DEAD`` rather than being retried forever — are all observable
    against this, because they are all properties of the outbox table rather
    than of the bus.
    """

    published: list[PublishedEvent] = field(default_factory=list)
    #: When set, every publish raises it. The instrument for the retry schedule.
    fail_with: Exception | None = None

    async def publish(self, events: Sequence[PublishedEvent]) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.published.extend(events)
