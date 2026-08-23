"""The event catalogue and the transport boundary.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` §10 (event catalogue) and §11 (routing).
- ``db/migrations/versions/0008_events_infrastructure.py`` —
  ``ck_outbox_events_event_type`` and ``ck_outbox_events_aggregate_type``, the
  same closed vocabularies written as database CHECKs.
- ``packages/python/provenance_contracts/src/provenance_contracts/events.py``
  — the envelope every domain event uses.

Why the catalogue is asserted against the migration
---------------------------------------------------
"Do not invent event names ad hoc in consumers" is enforced by a CHECK
constraint, not only by review. A consumer subscribing to an event type the
database cannot store would wait forever for a message that can never be
written — a silent, permanent no-op that looks like "nothing happened yet". So
the two lists are compared rather than trusted to agree.

Why the transport is a Protocol with an in-process implementation
-----------------------------------------------------------------
SQS and EventBridge left with the pivot to Cloud Run and Gemini. Rather than
guess at a replacement, the dispatcher publishes through a narrow Protocol and
this lane drives an in-process implementation. Choosing the eventual bus is a
wiring decision, not a correctness one, and the outbox semantics — at-least-once
delivery, idempotent consumption — are identical whichever bus arrives.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from provenance_domain.enums import AggregateType, EventType
from services.control_plane.app.events.catalogue import (
    CONSUMER_ROUTES,
    TRIGGER_EVENT_TYPES,
    consumers_for,
    is_known_event_type,
)
from services.control_plane.app.events.transport import (
    InProcessTransport,
    PublishedEvent,
    TransportError,
)

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "db"
    / "migrations"
    / "versions"
    / "0008_events_infrastructure.py"
)


def _event(event_type: str = "trigger.fired.v1") -> PublishedEvent:
    return PublishedEvent(
        event_id=uuid.uuid4(),
        event_type=event_type,
        aggregate_type="TRIGGER",
        aggregate_id=uuid.uuid4(),
        aggregate_version=12,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        occurred_at=datetime(2026, 9, 18, 13, 0, tzinfo=UTC),
        payload_version="1.0",
        payload={"trigger_id": "a7e3d901-5b48-4c26-9f13-8d0a2e6b4c77"},
    )


# ---------------------------------------------------------------------------
# The closed vocabulary.
# ---------------------------------------------------------------------------


def test_the_catalogue_is_exactly_the_migrations_check() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    clause = source.split("ck_outbox_events_event_type CHECK (event_type IN (", 1)[1]
    clause = clause.split("))", 1)[0]
    in_migration = set(re.findall(r"'([a-z_.]+\.v\d+)'", clause))
    assert {member.value for member in EventType} == in_migration


def test_every_trigger_event_is_in_the_catalogue() -> None:
    assert TRIGGER_EVENT_TYPES == ("trigger.armed.v1", "trigger.fired.v1", "trigger.noop.v1")
    for event_type in TRIGGER_EVENT_TYPES:
        assert is_known_event_type(event_type)


def test_an_invented_event_type_is_refused() -> None:
    assert not is_known_event_type("trigger.almost_fired.v1")
    assert consumers_for("trigger.almost_fired.v1") == ()


def test_every_catalogued_event_routes_somewhere_or_declares_that_it_does_not() -> None:
    """A route table with a silent hole is worse than one with none.

    Every event type appears as a key, so "nothing consumes this yet" is written
    down as an empty tuple rather than being indistinguishable from a typo.
    """
    assert set(CONSUMER_ROUTES) == {member.value for member in EventType}


def test_a_consumer_name_matches_the_ledgers_shape() -> None:
    """``ck_processed_events_consumer_shape`` — ``^[a-z][a-z0-9_.-]{2,63}$``.

    A consumer name the ledger's CHECK refuses would make the dedupe insert
    fail, and the dedupe insert failing means the effect rolls back with it.
    """
    pattern = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
    for consumers in CONSUMER_ROUTES.values():
        for consumer in consumers:
            assert pattern.match(consumer), consumer


def test_the_aggregate_type_vocabulary_matches_the_migration() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    clause = source.split("ck_outbox_events_aggregate_type CHECK (aggregate_type IN (", 1)[1]
    clause = clause.split("))", 1)[0]
    in_migration = set(re.findall(r"'([A-Z]+)'", clause))
    assert {member.value for member in AggregateType} == in_migration


# ---------------------------------------------------------------------------
# The transport Protocol.
# ---------------------------------------------------------------------------


async def test_the_in_process_transport_records_what_it_was_given() -> None:
    transport = InProcessTransport()
    event = _event()
    await transport.publish([event])
    assert transport.published == [event]


async def test_publishing_nothing_is_not_an_error() -> None:
    transport = InProcessTransport()
    await transport.publish([])
    assert transport.published == []


async def test_a_transport_failure_is_a_typed_error() -> None:
    """The dispatcher's retry decision turns on this type.

    An arbitrary exception escaping the transport would be indistinguishable
    from a bug in the dispatcher, and the two want opposite handling: one is
    retried with backoff, the other should crash loudly.
    """
    transport = InProcessTransport(fail_with=TransportError("bus refused"))
    with pytest.raises(TransportError):
        await transport.publish([_event()])
    assert transport.published == []


def test_a_published_event_carries_no_document_text() -> None:
    """The bus is a log with extra steps, and the same rule applies.

    An event is a pointer to committed state, not a copy of it. A consumer that
    needs the text reads it through an authorised API using the ids here.
    """
    with pytest.raises(ValueError, match="raw_text"):
        PublishedEvent(
            event_id=uuid.uuid4(),
            event_type="trigger.fired.v1",
            aggregate_type="TRIGGER",
            aggregate_id=uuid.uuid4(),
            aggregate_version=12,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            occurred_at=datetime(2026, 9, 18, 13, 0, tzinfo=UTC),
            payload_version="1.0",
            payload={"raw_text": "the landlord wrote..."},
        )


def test_a_published_event_names_its_aggregate_identity() -> None:
    """``(aggregate_id, aggregate_version, event_type)`` is the fact's identity.

    ``event_id`` is the *delivery* identity and is what a consumer dedupes on;
    the triple is what makes two deliveries recognisable as the same fact even
    across a re-publish with a fresh ``event_id``.
    """
    event = _event()
    assert event.aggregate_key() == (event.aggregate_id, 12, "trigger.fired.v1")
    assert event.dedupe_key("advocate.trigger") == ("advocate.trigger", event.event_id)
