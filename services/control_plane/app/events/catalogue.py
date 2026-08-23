"""The closed event vocabulary and where each event is routed.

Authority
---------
- ``specs/15_API_SPEC.md`` §10 (the catalogue) and §11 (routing).
- ``db/migrations/versions/0008_events_infrastructure.py``,
  ``ck_outbox_events_event_type`` — the same list as a database CHECK.
- ``provenance_domain.enums.EventType`` — the same list as a Python enum.

Three copies of one list, and why that is not duplication
----------------------------------------------------------
The enum is what code writes, the CHECK is what the database accepts, and this
table is what consumers subscribe to. They are three different failure modes: a
name only the enum knows is a row the database refuses; a name only the CHECK
knows is a row nothing consumes. ``test_catalogue.py`` compares all three, so
they are one list held in three places rather than three lists that agree today.

An unconsumed event is written down as unconsumed
--------------------------------------------------
Every catalogued type appears as a key in :data:`CONSUMER_ROUTES`, and types
nothing listens to yet map to ``()``. A route table with a silent hole is worse
than one with none: "no consumer" and "typo in the consumer's subscription" look
identical from the outside, and both present as "the message never arrived".
"""

from __future__ import annotations

from typing import Final

from provenance_domain.enums import EventType

__all__ = [
    "CONSUMER_ROUTES",
    "TRIGGER_EVENT_TYPES",
    "consumers_for",
    "is_known_event_type",
]

#: The three events prospective memory emits. ``trigger.armed.v1`` on arm or
#: re-arm, and exactly one of ``fired``/``noop`` per completed evaluation.
TRIGGER_EVENT_TYPES: Final[tuple[str, ...]] = (
    "trigger.armed.v1",
    "trigger.fired.v1",
    "trigger.noop.v1",
)

#: The consumer names each event is delivered to. Names match
#: ``ck_processed_events_consumer_shape`` (``^[a-z][a-z0-9_.-]{2,63}$``),
#: because the dedupe insert uses the name as half its primary key: a name the
#: CHECK refuses would fail the insert, and the insert failing rolls the effect
#: back with it.
CONSUMER_ROUTES: Final[dict[str, tuple[str, ...]]] = {
    "artifact.received.v1": ("ingestion.parse",),
    "artifact.parsed.v1": ("ingestion.extract",),
    "artifact.rejected.v1": (),
    "evidence.admitted.v1": (),
    "evidence.retracted.v1": ("projection.rebuild",),
    "memory.proposal.accepted.v1": (),
    "memory.proposal.rejected.v1": (),
    "belief.changed.v1": ("projection.rebuild",),
    "conflict.detected.v1": ("advocate.attention",),
    "conflict.resolved.v1": ("projection.rebuild",),
    "case.reopened.v1": ("advocate.attention",),
    "case.state_changed.v1": ("projection.rebuild",),
    "commitment.created.v1": (),
    "commitment.partially_fulfilled.v1": ("projection.rebuild",),
    "commitment.fulfilled.v1": ("projection.rebuild",),
    # The second reveal's fan-out: an overdue obligation is what the Advocate
    # drafts a follow-up about.
    "commitment.overdue.v1": ("advocate.attention",),
    "trigger.armed.v1": ("trigger.schedule",),
    "trigger.fired.v1": ("advocate.attention",),
    # A no-op is published too, and that is deliberate: "the trigger woke and
    # correctly did nothing" is the observation the false-wake ratio is built
    # from, and a metric you only emit on success measures nothing.
    "trigger.noop.v1": ("trigger.schedule",),
    "action.proposed.v1": (),
    "action.approved.v1": ("action.execute",),
    "action.rejected.v1": (),
    "action.executed.v1": ("projection.rebuild",),
    "action.failed.v1": (),
    "relationship.state_changed.v1": ("projection.rebuild",),
}

_KNOWN: Final[frozenset[str]] = frozenset(member.value for member in EventType)


def is_known_event_type(event_type: str) -> bool:
    """Whether *event_type* is in the closed catalogue.

    "Do not invent event names ad hoc in consumers" — checked here as well as by
    the CHECK, so a consumer subscribing to a name that can never be written
    fails at review rather than by waiting forever.
    """
    return event_type in _KNOWN


def consumers_for(event_type: str) -> tuple[str, ...]:
    """The consumer names *event_type* is delivered to, or ``()``."""
    return CONSUMER_ROUTES.get(event_type, ())
