"""The outbound transport boundary: a narrow protocol and a recording sink.

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T9.5``: "Implement the safe sink as an
  in-repository recorder with an inspectable call log ... so the test asserts
  against a log rather than a mock", and "Treat a provider-side duplicate as
  success with the original correlation id, not as a second send."
- ``docs/quality/23_PHASE_GATES.md`` ``G9.1``: "**provider calls made: 0**,
  asserted against the sink's call log rather than a mock counter."
- ``db/migrations/versions/0007_action_plane.py`` --
  ``ck_action_executions_provider`` admits ``SES``, ``SAFE_SINK`` and
  ``SIMULATOR``, and nothing else.

Why there is no SES adapter here, and no Google one either
-----------------------------------------------------------
``T9.5`` was written when the transport was SES. The pivot removed it, and
``tools/txn_purity_lint.py`` now bans ``google`` as a transaction-callback root
alongside ``boto3``. Building a mail integration on the strength of a task
description written before that change would put an unwired dependency on the
demo's critical path in exchange for nothing the demo shows.

So the executor is written against :class:`ActionSink` -- one method, one
message type, one receipt -- and :class:`DemoSink` is the implementation that
ships. A real transport is a later wiring decision and needs no change to the
executor when it arrives, which is the property the protocol exists to buy.

Why the call log and the message log are two different lists
--------------------------------------------------------------
``G9.4`` asserts a **message** count of exactly one under one idempotency key,
and ``G9.1`` asserts a **call** count of exactly zero on a stale approval.
Those are different measurements: a duplicate call that the sink recognises and
answers from its own record is one call and zero new messages. Collapsing them
would make one of the two gates unassertable, and it would hide the case worth
seeing -- that a second call happened at all.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "PROVIDER_DEMO_SINK",
    "PROVIDER_SES",
    "PROVIDER_SIMULATOR",
    "ActionSink",
    "DemoSink",
    "SinkMessage",
    "SinkReceipt",
]

#: ``ck_action_executions_provider``'s three permitted values.
PROVIDER_SES: Final[str] = "SES"
PROVIDER_DEMO_SINK: Final[str] = "SAFE_SINK"
PROVIDER_SIMULATOR: Final[str] = "SIMULATOR"


@dataclass(frozen=True, slots=True)
class SinkMessage:
    """Exactly what would leave the system, and the key it leaves under.

    ``request_sha256`` is carried rather than recomputed because the same 32
    bytes go into ``action_executions.request_sha256``: the ledger row and the
    transport must agree about what was sent, and they only can if one of them
    hands the digest to the other.
    """

    action_intent_id: uuid.UUID
    idempotency_key: str
    recipient: str
    subject: str
    body: str
    request_sha256: bytes


@dataclass(frozen=True, slots=True)
class SinkReceipt:
    """The provider's answer. ``duplicate`` means "already sent, here it is again"."""

    provider: str
    provider_correlation_id: str
    duplicate: bool = False


@runtime_checkable
class ActionSink(Protocol):
    """One method. There is nothing here that can read, list or cancel.

    A sink that could list what it had sent would be a second, unauthenticated
    read path over the user's outbound correspondence. The executor needs to
    send and to learn a correlation id; that is the whole surface.
    """

    provider: str

    async def send(self, message: SinkMessage) -> SinkReceipt: ...


class DemoSink:
    """Records what would have been sent. Nothing leaves the process.

    Idempotent by ``idempotency_key``: a second send under a key it has already
    seen returns the **original** correlation id with ``duplicate=True`` and
    adds no new message. That is ``T9.5``'s third sub-task and it is also how a
    real provider behaves, so the executor is exercised against the behaviour it
    will eventually meet rather than against a simplification of it.
    """

    provider: str = PROVIDER_DEMO_SINK

    def __init__(self) -> None:
        self._calls: list[SinkMessage] = []
        self._messages: list[SinkMessage] = []
        self._receipts: dict[str, SinkReceipt] = {}

    @property
    def calls(self) -> tuple[SinkMessage, ...]:
        """Every invocation, duplicates included. ``G9.1`` reads this one."""
        return tuple(self._calls)

    @property
    def messages(self) -> tuple[SinkMessage, ...]:
        """Distinct external effects. ``G9.4`` reads this one."""
        return tuple(self._messages)

    async def send(self, message: SinkMessage) -> SinkReceipt:
        self._calls.append(message)
        existing = self._receipts.get(message.idempotency_key)
        if existing is not None:
            return SinkReceipt(
                provider=self.provider,
                provider_correlation_id=existing.provider_correlation_id,
                duplicate=True,
            )
        receipt = SinkReceipt(
            provider=self.provider,
            provider_correlation_id=self._correlation_id(message),
            duplicate=False,
        )
        self._messages.append(message)
        self._receipts[message.idempotency_key] = receipt
        return receipt

    @staticmethod
    def _correlation_id(message: SinkMessage) -> str:
        """Deterministic, derived from the key rather than from a clock.

        A ``uuid4()`` here would make every replay assertion depend on the
        replay path returning the stored id rather than on the sink being
        honest, and the two are different guarantees.
        """
        digest = hashlib.sha256(message.idempotency_key.encode("utf-8")).hexdigest()
        return f"demo-sink-{digest[:32]}"
