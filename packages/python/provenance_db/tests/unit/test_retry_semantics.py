"""The serialization retry contract, without a database — T3.2, layer L1.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 7 — the retryable SQLSTATE table
  (7.1), the loop and its backoff schedule (7.2), the five callback rules
  (7.3), the no-side-effect posture after the cap (7.4) and the
  unique-violation mapping (7.5).
- ``specs/12_KERNEL_ALGORITHMS.md`` section 1.3 — ``_IN_KERNEL_TX`` and
  ``assert_no_side_effects``.
- ``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*: "The Kernel performs
  **no** side effect after the retry cap. It returns ``RETRYABLE_CONCURRENCY``
  with ``RETRY_EXHAUSTED_NOT_ENQUEUED``."
- ``quality/23_PHASE_GATES.md`` section 9 — ``G3.2``, ``G3.3``, ``G3.4``,
  ``G3.6``.
- ``EXECUTION/70_TASK_PLAN.md`` T3.2, which sizes this file at 14 tests, L1.

What this file does NOT prove
-----------------------------
That CockroachDB emits ``40001`` for a real interleaving. A fake connection
raising ``psycopg.errors.SerializationFailure`` proves the *loop's* behaviour
and nothing about the cluster, which is why ``G3.2`` is asserted by
``packages/python/provenance_db/tests/db/test_retry.py`` against two real
overlapping transactions instead. The exceptions raised below are real
``psycopg`` classes carrying the real SQLSTATEs the driver produces, so the
classifier is tested against the true values — but the *arrival* of those
values is the database test's claim, not this file's.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import errors as pgerr

from provenance_db.retry import (
    RETRYABLE_SQLSTATES,
    RetryExhausted,
    RetryPolicy,
    SideEffectInsideTransaction,
    assert_no_side_effects,
    backoff_delay_seconds,
    in_kernel_transaction,
    is_retryable,
    map_unique_violation,
    run_in_serializable_tx,
)
from provenance_domain.enums import KernelDecision, KernelReasonCode

pytestmark = pytest.mark.unit

TX_NOW = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes. Deliberately minimal: they implement the two Protocols the wrapper
# declares and nothing else, so widening those Protocols breaks this file.
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeTransaction:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeTransaction:
        self._conn.log.append("BEGIN")
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._conn.log.append("ROLLBACK" if exc_type is not None else "COMMIT")
        return False


class FakeConnection:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.isolation_levels: list[Any] = []

    async def set_isolation_level(self, level: Any) -> None:
        self.isolation_levels.append(level)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, query: str, params: Any = None) -> FakeCursor:
        self.log.append(query)
        return FakeCursor((TX_NOW,))


class FakePoolConnection:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakeConnection:
        conn = FakeConnection()
        self._pool.connections.append(conn)
        return conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class FakePool:
    """One fresh connection per attempt, which is what the real pool does."""

    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []

    def connection(self) -> FakePoolConnection:
        return FakePoolConnection(self)


@dataclass
class SleepRecorder:
    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class RecordingTelemetry:
    """A telemetry sink with a call log, not a mock with a counter."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.observations: list[tuple[str, float]] = []

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1

    def observe(self, name: str, value: float) -> None:
        self.observations.append((name, value))


class MidpointRandom:
    """A ``random.Random`` stand-in that always returns the midpoint."""

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


class LowRandom:
    def uniform(self, a: float, b: float) -> float:
        return a


class HighRandom:
    def uniform(self, a: float, b: float) -> float:
        return b


def failing_then_succeeding(
    failures: int, error: Callable[[], BaseException]
) -> tuple[Callable[[Any, datetime], Awaitable[str]], list[datetime]]:
    """A callback that raises *failures* times and then returns a fresh value."""
    seen: list[datetime] = []

    async def callback(conn: Any, tx_now: datetime) -> str:
        seen.append(tx_now)
        if len(seen) <= failures:
            raise error()
        return f"committed-on-attempt-{len(seen)}"

    return callback, seen


def serialization_failure() -> pgerr.SerializationFailure:
    return pgerr.SerializationFailure("restart transaction: RETRY_SERIALIZABLE")


#: A policy with the real attempt cap and a negligible delay, so the loop's
#: arithmetic is asserted separately from its wall-clock cost.
FAST = RetryPolicy(max_tx_attempts=5, retry_base_delay_ms=1, retry_max_delay_ms=2)


# ---------------------------------------------------------------------------
# 1-2 — classification, and the wiring that makes G3.6 meaningful
# ---------------------------------------------------------------------------


def test_the_retryable_set_is_exactly_40001_and_25p02() -> None:
    """Section 7.1, in both directions.

    ``40003`` is absent deliberately: statement completion is unknown, so a
    blind retry can double-apply. ``57014`` is absent because a statement
    timeout is not a serialization failure, even though it reaches the caller
    as the same HTTP status.
    """
    assert frozenset({"40001", "25P02"}) == RETRYABLE_SQLSTATES
    assert is_retryable("40001") is True
    assert is_retryable("25P02") is True
    for sqlstate in ("23505", "23514", "40003", "57014", "08006", None, ""):
        assert is_retryable(sqlstate) is False, sqlstate


def test_the_retry_loop_reaches_is_retryable_through_the_module_global() -> None:
    """``G3.6`` is only meaningful if the sabotage can actually arrive.

    ``PV_SABOTAGE`` rebinds the attribute on the *module object*. A call to a
    name captured by a ``from``-import, or to a local alias, would resolve
    before the rebind is visible and the sabotage would silently never land —
    a green ``G3.6`` run that proves nothing. Asserted against the AST rather
    than by reading the source, because the second one rots.
    """
    tree = ast.parse(inspect.getsource(run_in_serializable_tx))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    bare = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "is_retryable"]
    through_module = [
        c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "is_retryable"
    ]
    assert not bare, "is_retryable must not be called through a name the sabotage cannot rebind"
    assert through_module, "the loop must reach is_retryable through its own module object"


# ---------------------------------------------------------------------------
# 3-4 — the backoff schedule of section 7.2
# ---------------------------------------------------------------------------


def test_backoff_doubles_per_attempt_and_is_capped() -> None:
    """base 50 ms, cap 2000 ms: 50, 100, 200, 400 ms, then the cap holds."""
    policy = RetryPolicy()
    rng = MidpointRandom()
    assert (policy.max_tx_attempts, policy.retry_base_delay_ms, policy.retry_max_delay_ms) == (
        5,
        50,
        2_000,
    )
    assert [backoff_delay_seconds(n, policy, rng) for n in (1, 2, 3, 4)] == [
        0.050,
        0.100,
        0.200,
        0.400,
    ]
    assert backoff_delay_seconds(9, policy, rng) == 2.0


def test_backoff_jitter_is_symmetric_around_the_target() -> None:
    """``uniform(0.5*d, 1.5*d)``, not ``uniform(0, d)`` — section 7.2.

    Symmetric jitter is what stops two kernel writers colliding on one case
    from both collapsing toward zero delay and re-colliding.
    """
    policy = RetryPolicy()
    assert backoff_delay_seconds(1, policy, LowRandom()) == pytest.approx(0.025)
    assert backoff_delay_seconds(1, policy, HighRandom()) == pytest.approx(0.075)
    assert backoff_delay_seconds(4, policy, LowRandom()) == pytest.approx(0.200)
    assert backoff_delay_seconds(4, policy, HighRandom()) == pytest.approx(0.600)


# ---------------------------------------------------------------------------
# 5-10 — the loop
# ---------------------------------------------------------------------------


async def test_first_attempt_success_reports_retry_count_zero() -> None:
    """Task plan section 23.9: single-writer tests must assert ``retry_count == 0``."""
    callback, seen = failing_then_succeeding(0, serialization_failure)
    sleep = SleepRecorder()
    telemetry = RecordingTelemetry()
    outcome = await run_in_serializable_tx(
        FakePool(), callback, config=FAST, sleep=sleep, rng=MidpointRandom(), telemetry=telemetry
    )
    assert outcome.value == "committed-on-attempt-1"
    assert outcome.retry_count == 0
    assert outcome.attempts == 1
    assert sleep.delays == []
    assert len(seen) == 1
    assert telemetry.observations == [("kernel_tx_retries_total", 0)]
    assert telemetry.counters == {}


async def test_two_serialization_failures_are_retried_and_the_third_commits() -> None:
    """``G3.2``'s shape, with the arrival of the 40001 faked. See the module docstring."""
    callback, _ = failing_then_succeeding(2, serialization_failure)
    sleep = SleepRecorder()
    pool = FakePool()
    outcome = await run_in_serializable_tx(
        pool, callback, config=FAST, sleep=sleep, rng=MidpointRandom()
    )
    assert outcome.value == "committed-on-attempt-3"
    assert outcome.retry_count == 2
    assert outcome.attempts == 3
    assert len(sleep.delays) == 2
    assert len(pool.connections) == 3, "each attempt takes a fresh connection"
    assert [conn.log[-1] for conn in pool.connections] == ["ROLLBACK", "ROLLBACK", "COMMIT"]


async def test_exhaustion_raises_retry_exhausted_carrying_attempts_5() -> None:
    """``G3.3``: the budget is bounded and exhaustion is loud, not a silent NOOP."""
    callback, seen = failing_then_succeeding(99, serialization_failure)
    sleep = SleepRecorder()
    with pytest.raises(RetryExhausted) as caught:
        await run_in_serializable_tx(
            FakePool(), callback, config=FAST, sleep=sleep, rng=MidpointRandom()
        )
    assert caught.value.attempts == 5
    assert caught.value.sqlstate == "40001"
    assert isinstance(caught.value.last, pgerr.SerializationFailure)
    assert len(seen) == 5
    assert len(sleep.delays) == 4, "four backoffs between five attempts, none after the last"


async def test_exhaustion_performs_no_side_effect_and_enqueues_nothing() -> None:
    """``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*.

    There is no kernel retry queue and the control plane holds no ``sqs:*``
    permission, so the only correct behaviour after the cap is to raise
    carrying the two reason codes the caller reports. The telemetry counters
    are asserted as an exact dictionary rather than by membership: a loop that
    grew a third emitter — an enqueue, a dead-letter publish, a "compensating"
    write — would have to break this test to land.
    """
    callback, _ = failing_then_succeeding(99, serialization_failure)
    telemetry = RecordingTelemetry()
    with pytest.raises(RetryExhausted) as caught:
        await run_in_serializable_tx(
            FakePool(),
            callback,
            config=FAST,
            sleep=SleepRecorder(),
            rng=MidpointRandom(),
            telemetry=telemetry,
        )
    assert caught.value.decision is KernelDecision.RETRYABLE_CONCURRENCY
    assert caught.value.reason_codes == (
        KernelReasonCode.RETRYABLE_CONCURRENCY,
        KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED,
    )
    assert telemetry.counters == {"kernel_tx_retry": 5, "kernel_tx_retry_exhausted": 1}
    assert telemetry.observations == [], "nothing committed, so nothing is observed as committed"


async def test_a_non_retryable_sqlstate_is_raised_without_a_single_sleep() -> None:
    """A check violation is a statement about the data, not about contention."""
    callback, seen = failing_then_succeeding(99, lambda: pgerr.CheckViolation("bad row"))
    sleep = SleepRecorder()
    with pytest.raises(pgerr.CheckViolation):
        await run_in_serializable_tx(
            FakePool(), callback, config=FAST, sleep=sleep, rng=MidpointRandom()
        )
    assert len(seen) == 1
    assert sleep.delays == []


async def test_every_attempt_reruns_the_callback_from_fresh_reads() -> None:
    """Section 7.3 rules 2 and 3: no derived state survives a failed attempt.

    The callback is handed a *new* connection and a *new* ``tx_now`` each
    attempt and its previous return value is never reused; a wrapper that
    cached either would write a plan derived from a rolled-back snapshot,
    which is exactly how invariant 3's "impossible partial aggregate state"
    gets written.
    """
    seen_connections: list[Any] = []

    async def callback(conn: Any, tx_now: datetime) -> int:
        seen_connections.append(conn)
        if len(seen_connections) < 3:
            raise serialization_failure()
        return len(seen_connections)

    pool = FakePool()
    outcome = await run_in_serializable_tx(
        pool, callback, config=FAST, sleep=SleepRecorder(), rng=MidpointRandom()
    )
    assert outcome.value == 3
    assert len({id(conn) for conn in seen_connections}) == 3, "a retry must not reuse a connection"
    for conn in pool.connections:
        assert conn.log[0] == "BEGIN"
        assert any(
            "transaction_timestamp()" in entry for entry in conn.log
        ), "every attempt must re-read the transaction clock inside its own transaction"


# ---------------------------------------------------------------------------
# 11-12 — the side-effect guard, mechanism E2
# ---------------------------------------------------------------------------


async def test_in_kernel_tx_is_set_inside_the_callback_and_cleared_after() -> None:
    inside: list[bool] = []

    async def callback(conn: Any, tx_now: datetime) -> None:
        inside.append(in_kernel_transaction())

    assert in_kernel_transaction() is False
    await run_in_serializable_tx(
        FakePool(), callback, config=FAST, sleep=SleepRecorder(), rng=MidpointRandom()
    )
    assert inside == [True]
    assert in_kernel_transaction() is False


async def test_assert_no_side_effects_raises_only_inside_the_transaction() -> None:
    """``12_KERNEL_ALGORITHMS.md`` section 1.3, and mechanism E2 of the TDD strategy.

    The guard refuses the *call*; it does not undo it. That is the whole
    point: a transaction rolls back, an e-mail that has already left does not.
    """
    assert_no_side_effects("ses.send_email")  # outside a transaction: silent

    async def callback(conn: Any, tx_now: datetime) -> None:
        assert_no_side_effects("bedrock_runtime.converse")

    with pytest.raises(SideEffectInsideTransaction) as caught:
        await run_in_serializable_tx(
            FakePool(), callback, config=FAST, sleep=SleepRecorder(), rng=MidpointRandom()
        )
    assert "bedrock_runtime.converse" in str(caught.value)
    assert_no_side_effects("ses.send_email")  # and the flag is reset on the way out


# ---------------------------------------------------------------------------
# 13-14 — section 7.5, the unique-violation mapping
# ---------------------------------------------------------------------------


def test_unique_violation_maps_by_constraint_name_not_by_message() -> None:
    """Section 7.5. "Never inspect the error message string" — section 7.1."""
    duplicate = map_unique_violation("uq_source_artifacts_content")
    assert duplicate.decision is KernelDecision.NOOP_DUPLICATE
    assert duplicate.reason_code is KernelReasonCode.ARTIFACT_CONTENT_DUPLICATE
    assert duplicate.retry_as_serialization_failure is False

    invariant = map_unique_violation("uq_belief_support_edge")
    assert invariant.decision is KernelDecision.REJECTED_INVARIANT
    assert invariant.reason_code is KernelReasonCode.INVARIANT_DUPLICATE_SUPPORT_EDGE

    unknown = map_unique_violation("some_constraint_nobody_wrote_down")
    assert unknown.decision is KernelDecision.REJECTED_INVARIANT
    assert unknown.reason_code is KernelReasonCode.INVARIANT_UNIQUE_VIOLATION

    assert map_unique_violation(None).reason_code is KernelReasonCode.INVARIANT_UNIQUE_VIOLATION


def test_belief_version_number_race_is_retried_as_a_serialization_failure() -> None:
    """The one ``23505`` section 7.5 sends back round the loop.

    ``uq_belief_versions_chain`` means another commit inserted
    ``version_no = n+1`` between our read and our write: a serialization race
    wearing a different error code, which a fresh read resolves.
    """
    outcome = map_unique_violation("uq_belief_versions_chain")
    assert outcome.retry_as_serialization_failure is True
    assert outcome.reason_code is KernelReasonCode.RETRYABLE_CONCURRENCY
    assert outcome.decision is KernelDecision.RETRYABLE_CONCURRENCY

    for constraint in (
        "uq_source_artifacts_content",
        "uq_source_artifacts_message_id",
        "uq_fulfillments_commitment_evidence",
        "uq_belief_support_edge",
        "uq_beliefs_proposition",
        "uq_action_intents_idempotency",
        "uq_claims_evidence_proposition",
    ):
        assert map_unique_violation(constraint).retry_as_serialization_failure is False, constraint
