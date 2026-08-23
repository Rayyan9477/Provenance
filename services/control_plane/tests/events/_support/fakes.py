"""In-process doubles for the four boundaries Phase 10 talks to.

Each of these stands in for something another lane owns — the trigger
repository, the projection read, the Memory Kernel, and the event transport —
and each is declared here against the narrow Protocol the production module
depends on rather than by importing the real thing. That is the point: the
evaluator and the dispatcher must be exercisable in full with no database, no
credentials and no socket, which is what the ``unit`` lane's guard enforces.

They are fakes and not mocks on purpose. A mock asserts that a call was made; a
fake behaves, so a test can assert on the *state* afterwards — one transition,
one revision increment, three outbox rows — which is what the gates actually
ask for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.control_plane.app.events.transport import PublishedEvent
from services.control_plane.app.triggers.projection import ProjectionSnapshot
from services.control_plane.app.triggers.service import (
    CommitReceipt,
    CommitRequest,
    KernelUnavailableError,
    TriggerSnapshot,
)

__all__ = [
    "FailingTransport",
    "FakeKernel",
    "FakeProjectionReader",
    "FakeTriggerStore",
    "RecordingTransport",
]


@dataclass
class FakeTriggerStore:
    """One trigger row plus the database clock, read together.

    ``db_now`` travels with the row because the guards in §9.6 compare against
    it: ``expires_at`` and ``not_before`` are judged by the database's clock and
    never by the caller's, and a store that returned the row without a clock
    would force the guard to invent one.
    """

    rows: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    db_now: datetime | None = None
    reads: int = 0

    async def load(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> TriggerSnapshot | None:
        self.reads += 1
        row = self.rows.get(trigger_id)
        if row is None:
            return None
        if row["tenant_id"] != tenant_id or row["user_id"] != user_id:
            # The row is scoped, so a cross-tenant read is indistinguishable
            # from a missing row. Anything else is an existence oracle.
            return None
        assert self.db_now is not None, "the fake store needs a database clock"
        return TriggerSnapshot(row=dict(row), db_now=self.db_now)


@dataclass
class FakeProjectionReader:
    """The §7.2 snapshot, with a hook for "the case moved under us".

    ``mutate_before_read`` runs before each read, so a test can advance the case
    revision between attempts and exercise the re-evaluation loop rather than
    only asserting that it exists.
    """

    case_row: dict[str, Any]
    commitment_rows: dict[uuid.UUID, dict[str, Any]]
    reads: int = 0
    mutate_before_read: Any = None

    async def read(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        case_id: uuid.UUID,
        commitment_ids: tuple[uuid.UUID, ...],
    ) -> ProjectionSnapshot:
        del tenant_id, user_id, case_id
        self.reads += 1
        if self.mutate_before_read is not None:
            self.mutate_before_read(self)
        rows = {
            commitment_id: dict(self.commitment_rows[commitment_id])
            for commitment_id in commitment_ids
            if commitment_id in self.commitment_rows
        }
        return ProjectionSnapshot(case_row=dict(self.case_row), commitment_rows=rows)


@dataclass
class FakeKernel:
    """The only canonical writer, faked at its transaction boundary.

    It enforces the two things the real one enforces and that the evaluator must
    not be allowed to skip: the idempotency key is claimed inside the same
    commit as the effect, and the case revision observed by the evaluation must
    still be current or the commit is refused.
    """

    case_revision: int = 11
    claimed_keys: dict[str, CommitReceipt] = field(default_factory=dict)
    commits: list[CommitRequest] = field(default_factory=list)
    outbox: list[tuple[str, uuid.UUID, int]] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    revision_moves_before_commit: int = 0
    unavailable: bool = False

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        if self.unavailable:
            raise KernelUnavailableError("the control plane could not reach the Kernel")

        replay = self.claimed_keys.get(request.idempotency_key)
        if replay is not None:
            # §11.1: the caller did nothing wrong. Return the stored result.
            return replay

        if self.revision_moves_before_commit > 0:
            self.revision_moves_before_commit -= 1
            self.case_revision += 1
            return CommitReceipt(
                committed=False,
                revision_moved=True,
                case_revision_after=self.case_revision,
            )

        if request.case_revision_observed != self.case_revision:
            return CommitReceipt(
                committed=False,
                revision_moved=True,
                case_revision_after=self.case_revision,
            )

        self.commits.append(request)
        if request.increments_case_revision:
            self.case_revision += 1
            self.transitions.append("TRIGGER_FIRED")
        for event_type in request.outbox_event_types:
            self.outbox.append((event_type, request.trigger_id, self.case_revision))
        receipt = CommitReceipt(
            committed=True,
            revision_moved=False,
            case_revision_after=self.case_revision,
            proposal_id=uuid.uuid4(),
            outbox_event_ids=tuple(uuid.uuid4() for _ in request.outbox_event_types),
        )
        self.claimed_keys[request.idempotency_key] = CommitReceipt(
            committed=True,
            revision_moved=False,
            case_revision_after=receipt.case_revision_after,
            proposal_id=receipt.proposal_id,
            outbox_event_ids=receipt.outbox_event_ids,
            idempotent_replay=True,
        )
        return receipt


@dataclass
class RecordingTransport:
    """An in-process event transport that keeps what it was handed.

    SQS and EventBridge are gone with the pivot, so the dispatcher speaks to a
    Protocol and this is the implementation the tests drive. Publishing is
    recorded rather than performed, which is all a dispatcher test needs: the
    interesting assertions are about *which* rows were claimed, in what order,
    and how failures were re-scheduled.
    """

    published: list[PublishedEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[PublishedEvent]) -> None:
        self.published.extend(events)

    def event_types(self) -> list[str]:
        return [event.event_type for event in self.published]


@dataclass
class FailingTransport:
    """Fails the first *failures* publishes, then succeeds.

    The dispatcher's whole retry schedule is only observable through a transport
    that refuses, so this is the instrument for ``G10.4``.
    """

    failures: int
    error: Exception = field(default_factory=lambda: RuntimeError("transport refused"))
    attempts: int = 0
    published: list[PublishedEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[PublishedEvent]) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        self.published.extend(events)
