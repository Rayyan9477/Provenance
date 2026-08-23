"""An in-memory ``outbox_events`` table that behaves like the real one.

The column names, the statuses and the two CHECKs that constrain the dispatcher
— ``ck_outbox_events_attempts`` and ``ck_outbox_events_dispatched`` — are
transcribed from ``0008_events_infrastructure.py``. It is a fake rather than a
mock so a test can assert on the *state* afterwards, which is what the gate
asks for: a row that reached ``DEAD`` after the schedule, with an error
recorded and no ``dispatched_at``.

Enforcing the CHECKs here is deliberate. A store double that accepted
``attempt_count = 6`` would let the dispatcher pass its unit tests and then fail
its bookkeeping UPDATE against the real cluster, at the exact moment it was
recording a dead letter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from services.control_plane.app.events.dispatcher import MAX_ATTEMPT_COUNT, OutboxStatus

__all__ = ["MemoryOutboxStore", "outbox_row"]

_CLAIMABLE = (OutboxStatus.PENDING, OutboxStatus.FAILED_RETRYABLE)


def outbox_row(
    *,
    next_attempt_at: datetime,
    occurred_at: datetime | None = None,
    event_type: str = "trigger.fired.v1",
    aggregate_type: str = "TRIGGER",
    aggregate_id: uuid.UUID | None = None,
    aggregate_version: int = 12,
    status: str = OutboxStatus.PENDING,
    attempt_count: int = 0,
    last_error: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One row, with every column ``0008`` declares."""
    now = occurred_at or next_attempt_at
    return {
        "id": uuid.uuid4(),
        "tenant_id": uuid.UUID("0f6c1e88-2a94-4b31-8d5c-77e1a0b93f42"),
        "user_id": uuid.UUID("b1d47a03-8e26-4c9f-a0b3-5f2c9d8e1470"),
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id or uuid.uuid4(),
        "aggregate_version": aggregate_version,
        "event_type": event_type,
        "payload_version": "1.0",
        "payload": payload if payload is not None else {"trigger_id": "a7e3d901"},
        "trace_id": uuid.UUID("2b6f8c14-9d33-4a02-8e77-13c5a90b6d21"),
        "causation_id": None,
        "correlation_id": None,
        "status": status,
        "attempt_count": attempt_count,
        "next_attempt_at": next_attempt_at,
        "last_error": last_error,
        "occurred_at": now,
        "created_at": now,
        "dispatched_at": None,
    }


@dataclass
class MemoryOutboxStore:
    """The dispatcher's store Protocol, backed by a list.

    Every mutation goes through :meth:`_assert_checks`, so this double refuses
    exactly what the cluster refuses.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)

    # -- the checks the migration writes -----------------------------------

    def _assert_checks(self, row: dict[str, Any]) -> None:
        assert 0 <= row["attempt_count"] <= MAX_ATTEMPT_COUNT, "ck_outbox_events_attempts"
        assert (row["status"] == OutboxStatus.DISPATCHED) == (
            row["dispatched_at"] is not None
        ), "ck_outbox_events_dispatched"
        if row["status"] in (OutboxStatus.DEAD, OutboxStatus.FAILED_RETRYABLE):
            assert row["last_error"] is not None, "ck_outbox_events_dead_has_error"

    def _find(self, event_id: uuid.UUID) -> dict[str, Any] | None:
        for row in self.rows:
            if row["id"] == event_id:
                return row
        return None

    # -- the Protocol -------------------------------------------------------

    async def claim(self, *, limit: int, now: datetime, lease_seconds: int) -> list[dict[str, Any]]:
        """``... WHERE status IN ('PENDING','FAILED_RETRYABLE') AND next_attempt_at <= now``.

        Ordered by ``next_attempt_at`` then ``occurred_at`` then
        ``aggregate_version``: the timestamp alone is not a total order, because
        several events from one Kernel transaction share it.
        """
        due = [
            row
            for row in self.rows
            if row["status"] in _CLAIMABLE and row["next_attempt_at"] <= now
        ]
        due.sort(
            key=lambda row: (row["next_attempt_at"], row["occurred_at"], row["aggregate_version"])
        )
        claimed = due[:limit]
        for row in claimed:
            row["status"] = OutboxStatus.DISPATCHING
            # No lease column exists in 0008, so the hold's expiry rides on
            # next_attempt_at. See the dispatcher's module docstring.
            row["next_attempt_at"] = now + timedelta(seconds=lease_seconds)
            self._assert_checks(row)
        return [dict(row) for row in claimed]

    async def mark_dispatched(self, event_ids: list[uuid.UUID], now: datetime) -> None:
        self.mark_dispatched_sync(event_ids, now)

    def mark_dispatched_sync(self, event_ids: list[uuid.UUID], now: datetime) -> None:
        for event_id in event_ids:
            row = self._find(event_id)
            if row is None:
                continue
            row["status"] = OutboxStatus.DISPATCHED
            row["dispatched_at"] = now
            self._assert_checks(row)

    async def mark_failed(
        self,
        *,
        event_id: uuid.UUID,
        attempt_count: int,
        next_attempt_at: datetime,
        last_error: str,
    ) -> None:
        row = self._find(event_id)
        if row is None:
            return
        row["status"] = OutboxStatus.FAILED_RETRYABLE
        row["attempt_count"] = attempt_count
        row["next_attempt_at"] = next_attempt_at
        row["last_error"] = last_error
        self._assert_checks(row)

    async def mark_dead(self, *, event_id: uuid.UUID, attempt_count: int, last_error: str) -> None:
        row = self._find(event_id)
        if row is None:
            return
        row["status"] = OutboxStatus.DEAD
        row["attempt_count"] = attempt_count
        row["last_error"] = last_error
        self._assert_checks(row)

    async def reclaim_expired(self, *, now: datetime) -> int:
        """Return ``DISPATCHING`` rows whose lease elapsed to the retry queue."""
        reclaimed = 0
        for row in self.rows:
            if row["status"] != OutboxStatus.DISPATCHING or row["next_attempt_at"] > now:
                continue
            row["status"] = OutboxStatus.FAILED_RETRYABLE
            row["last_error"] = "lease expired; the sweeper that claimed this row did not report"
            self._assert_checks(row)
            reclaimed += 1
        return reclaimed

    async def replay(self, *, event_id: uuid.UUID, now: datetime) -> bool:
        row = self._find(event_id)
        if row is None or row["status"] != OutboxStatus.DEAD:
            return False
        row["status"] = OutboxStatus.PENDING
        row["attempt_count"] = 0
        row["next_attempt_at"] = now
        self._assert_checks(row)
        return True
