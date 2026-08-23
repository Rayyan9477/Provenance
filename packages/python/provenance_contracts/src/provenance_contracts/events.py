"""The single envelope every domain event uses.

Written inside the Kernel transaction to the outbox, swept by the dispatcher,
published to EventBridge, consumed with ``event_id`` as the dedupe key.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 15, whose code this module implements.
- ``specs/15_API_SPEC.md`` section 10 -- the envelope is keyed on
  ``(aggregate_id, aggregate_version, event_type)``: the aggregate it happened
  to, the version it happened at, and what happened.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, fourth sub-task.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Annotated, Final

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Revision,
    UtcDatetime,
    new_id,
    utc_now,
)
from provenance_domain.enums import EVENT_AGGREGATE_TYPE, AggregateType, EventType

__all__ = ["FORBIDDEN_PAYLOAD_KEYS", "MAX_EVENT_PAYLOAD_BYTES", "DomainEvent"]

#: EventBridge caps a PutEvents entry at 256 KB. We cap far below that on
#: purpose: an event is a pointer to committed state, not a copy of it.
MAX_EVENT_PAYLOAD_BYTES: Final[int] = 16 * 1024

#: "Do not put raw document contents into logs" applied to the event bus,
#: which is a log with extra steps. A consumer that needs the text reads it
#: through an authorised API using the ids in the payload.
FORBIDDEN_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "raw_text",
        "exact_text",
        "body",
        "html_body",
        "mime_content",
        "attachment_bytes",
        "normalized_content",
        "draft_body",
        "email_body",
        "password",
        "token",
        "access_token",
        "secret",
    }
)


def _forbidden_keys_in(payload: object, *, depth: int = 0) -> set[str]:
    """Every forbidden key anywhere in a nested payload, bounded in depth."""
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


class DomainEvent(BoundaryContract):
    """A fact that already happened, addressed to nobody in particular.

    ``aggregate_version`` is the case revision (or the aggregate's own
    revision) at commit time. Consumers use it to detect out-of-order delivery:
    EventBridge is at-least-once and unordered, so a consumer that sees version
    8 after version 9 must drop, not apply.

    The identifying key is ``(aggregate_id, aggregate_version, event_type)``.
    ``event_id`` is the *delivery* identity and is what a consumer dedupes on;
    the triple is what makes two deliveries of the same fact recognisable as
    the same fact even across a re-publish.
    """

    event_id: uuid.UUID = Field(default_factory=new_id)
    event_type: EventType
    aggregate_type: AggregateType
    aggregate_id: uuid.UUID
    aggregate_version: Revision
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    trace_id: uuid.UUID
    causation_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    occurred_at: UtcDatetime = Field(default_factory=utc_now)
    payload_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = "1.0"
    payload: Mapping[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aggregate_type_matches_event(self) -> DomainEvent:
        expected = EVENT_AGGREGATE_TYPE[self.event_type]
        if self.aggregate_type is not expected:
            raise ValueError(f"{self.event_type} is an {expected} event, got {self.aggregate_type}")
        return self

    @model_validator(mode="after")
    def _payload_is_small_and_clean(self) -> DomainEvent:
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
        return self

    def dedupe_key(self, consumer_name: str) -> tuple[str, uuid.UUID]:
        """The primary key of the processed-event ledger.

        Idempotent consumption is the consumer's job, and this is the only key
        it may use.
        """
        return (consumer_name, self.event_id)

    def aggregate_key(self) -> tuple[uuid.UUID, int, EventType]:
        """``(aggregate_id, aggregate_version, event_type)``.

        The identity ``specs/15_API_SPEC.md`` section 10 keys the envelope on:
        two deliveries with the same triple describe the same committed fact,
        whatever their ``event_id``.
        """
        return (self.aggregate_id, self.aggregate_version, self.event_type)
