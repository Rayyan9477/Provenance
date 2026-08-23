"""The serialization retry contract — SQLSTATE ``40001``, bounded — T3.2.

Authority
---------
``specs/12_KERNEL_ALGORITHMS.md`` section 7, in full: 7.1 the retryable
SQLSTATEs, 7.2 the loop and its backoff, 7.3 the five rules the callback must
obey, 7.4 what happens after the cap, 7.5 the unique-violation mapping.
Section 1.3 owns ``_IN_KERNEL_TX`` and :func:`assert_no_side_effects`.
``CANONICAL_DECISIONS.md`` -> *Transaction isolation* and -> *Kernel retry
exhaustion* are binding on both.

THE RULE THIS MODULE EXISTS TO MAKE ENFORCEABLE
-----------------------------------------------
**No model call and no network call may occur inside a transaction callback.**
Not "should not": the callback runs once per attempt, so a Bedrock call inside
it is charged again on every retry, and an e-mail, an S3 put or an EventBridge
publish inside it cannot be rolled back when the transaction is. That is why
the outbox exists.

Two mechanisms enforce it, and neither is trust:

* At run time, :data:`_IN_KERNEL_TX` is set for the duration of the
  transaction and every outbound client wrapper calls
  :func:`assert_no_side_effects` first (``20_TDD_STRATEGY.md`` section 2.3,
  guard E2).
* Statically, ``tools/txn_purity_lint.py`` walks the AST of every function
  decorated :func:`in_transaction` and every callable passed to
  :func:`run_in_serializable_tx`, and rejects attribute chains rooted at
  ``boto3``, ``httpx``, ``requests``, ``aiohttp`` and the Bedrock wrapper. It
  runs in ``make lint``, so the rule is checked on every push rather than at
  the gate (``23_PHASE_GATES.md`` ``G3.5``).

After the cap
-------------
The loop raises :class:`RetryExhausted` and performs **no** side effect. There
is no kernel retry queue, the control plane holds no ``sqs:*`` permission, and
re-drive is the caller's job over ``503`` + ``Retry-After``. The exception
carries the two reason codes the caller reports —
``RETRYABLE_CONCURRENCY`` and ``RETRY_EXHAUSTED_NOT_ENQUEUED`` — so that the
receipt cannot drift from the behaviour that produced it.

Recorded deviations from section 7.2's printed snippet
------------------------------------------------------
1. **The return type.** The snippet ends ``return
   result.with_retry_count(attempt - 1)``. ``KernelCommitResult`` has a
   ``retry_count`` field but no ``with_retry_count`` method, and requiring one
   would bind this wrapper to a single caller: repositories, workers and the
   trigger evaluator all need the same transaction, and none of them returns a
   ``KernelCommitResult``. :func:`run_in_serializable_tx` is generic and
   returns :class:`TxResult`, which carries ``value``, ``retry_count`` and
   ``attempts``. The Kernel writes
   ``result.value.model_copy(update={"retry_count": result.retry_count})``.
2. **The driver API.** The snippet calls ``conn.fetchval(...)`` and
   ``conn.set_isolation_level("SERIALIZABLE")``. ``fetchval`` is an
   ``asyncpg`` method and does not exist in ``psycopg``; the isolation level is
   an :class:`psycopg.IsolationLevel` member, not a string. Both are used in
   their real form here and reported as documentation defects.
3. **Injected clock and randomness.** ``sleep`` and ``rng`` are parameters with
   the real defaults. A retry loop whose timing cannot be observed is a retry
   loop whose backoff schedule is asserted by reading the source.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import random
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Any, Final, Generic, Protocol, TypeVar

from psycopg import IsolationLevel
from psycopg import errors as pgerr

# Imported from provenance_domain, which defines them, rather than from
# provenance_contracts, which re-exports them without listing them in __all__ —
# `mypy --strict` rejects an implicit re-export, and the alternative is a
# stringly-typed reason code, which is exactly how a closed vocabulary leaks.
from provenance_domain.enums import KernelDecision, KernelReasonCode

__all__ = [
    "RETRYABLE_SQLSTATES",
    "UNIQUE_VIOLATION_MAP",
    "RetryExhausted",
    "RetryPolicy",
    "SideEffectInsideTransaction",
    "TransactionRetryConfig",
    "TxResult",
    "UniqueViolationOutcome",
    "assert_no_side_effects",
    "backoff_delay_seconds",
    "in_kernel_transaction",
    "in_transaction",
    "is_retryable",
    "map_unique_violation",
    "run_in_serializable_tx",
]

T = TypeVar("T")

#: This module, by object rather than by name. ``PV_SABOTAGE`` rebinds
#: attributes here, so every call site that must see the rebind goes through
#: it. ``sys.modules`` is the supported way to reach it during and after import.
_MODULE: Final[ModuleType] = sys.modules[__name__]


# ---------------------------------------------------------------------------
# Section 7.1 — what is retryable
# ---------------------------------------------------------------------------

#: The only two SQLSTATEs the loop retries.
#:
#: ``40001`` covers every CockroachDB retry reason (``RETRY_SERIALIZABLE``,
#: ``RETRY_WRITE_TOO_OLD``, ``RETRY_ASYNC_WRITE_FAILURE``, ``ABORT_REASON_*``,
#: ``ReadWithinUncertaintyInterval``) — the *reason* is diagnostic, the
#: SQLSTATE is the contract. ``25P02`` means the transaction is already in a
#: failed state: roll back, then treat it as ``40001``.
#:
#: Deliberately absent: ``40003`` (statement completion unknown — a blind
#: retry can double-apply, so the caller checks ``kernel_decisions`` for the
#: proposal first), ``23514`` (a check violation is a statement about the data,
#: mapped to ``REJECTED_INVARIANT``), ``57014`` (statement timeout) and
#: ``23505`` (see :data:`UNIQUE_VIOLATION_MAP`).
RETRYABLE_SQLSTATES: Final[frozenset[str]] = frozenset({"40001", "25P02"})


def is_retryable(sqlstate: str | None) -> bool:
    """Whether *sqlstate* means "run the whole callback again".

    Match on the SQLSTATE. Never inspect the error message string — section
    7.1 states it outright, and CockroachDB's retry-reason text is not a
    stable interface.

    This function is the ``PV_SABOTAGE`` hook for ``G3.6``; see the bottom of
    this module for how it is neutered and why the loop must reach it through
    the module object.
    """
    return sqlstate in RETRYABLE_SQLSTATES


# ---------------------------------------------------------------------------
# Section 7.5 — the unique-violation mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UniqueViolationOutcome:
    """What a ``23505`` on a named constraint means.

    ``23505`` is never retried *as a unique violation*: it is a deterministic
    statement about what already exists. The single exception is
    ``belief_versions_belief_version_no_key``, which is a serialization race
    wearing a different error code — ``retry_as_serialization_failure`` is
    ``True`` there and nowhere else.
    """

    decision: KernelDecision
    reason_code: KernelReasonCode
    retry_as_serialization_failure: bool = False


_NOOP = KernelDecision.NOOP_DUPLICATE
_INVARIANT = KernelDecision.REJECTED_INVARIANT

#: SQLSTATE ``23505`` constraint name -> what the Kernel does about it.
#:
#: REWRITTEN 2026-08-19 against the built schema. Every one of the eight keys
#: this table previously carried was a POSTGRES AUTO-GENERATED name
#: (``fulfillments_commitment_evidence_key``), and migrations 0001-0008 declare
#: EXPLICIT ``uq_*`` names. ``diag.constraint_name`` returns the declared name,
#: so not a single key matched and the whole table was dead: every 23505 fell
#: through to ``REJECTED_INVARIANT``.
#:
#: Verified against ``provenance_ci`` at head 0008 -- all eight old keys returned
#: zero rows from ``information_schema.table_constraints``.
#:
#: The worst consequence was silent. ``belief_versions_belief_version_no_key``
#: was the ONLY entry carrying ``retry_as_serialization_failure=True``, so the
#: version-race retry -- two writers racing for the same ``version_no`` -- could
#: never fire. A lost update that the Kernel is designed to retry was instead
#: reported as an invariant breach, and ``G4.7`` is the assertion that would have
#: caught it.
UNIQUE_VIOLATION_MAP: Final[Mapping[str, UniqueViolationOutcome]] = {
    # --- artifact identity. Two constraints, not one: dedupe keyed only on
    # --- source_message_id is the bug DDL section 19 test 1 exists to catch,
    # --- because that column is NULL for every uploaded .eml.
    "uq_source_artifacts_content": UniqueViolationOutcome(
        _NOOP, KernelReasonCode.ARTIFACT_CONTENT_DUPLICATE
    ),
    "uq_source_artifacts_message_id": UniqueViolationOutcome(
        _NOOP, KernelReasonCode.ARTIFACT_CONTENT_DUPLICATE
    ),
    # --- the same evidence admitted twice against one commitment
    "uq_fulfillments_commitment_evidence": UniqueViolationOutcome(
        _NOOP, KernelReasonCode.FULFILLMENT_EVIDENCE_DUPLICATE
    ),
    # --- THE VERSION RACE. Two writers reached the same (belief_id, version_no).
    # --- That is contention, not corruption: the loser must retry and will read
    # --- the winner's version. This is the entry whose absence was invisible.
    "uq_belief_versions_chain": UniqueViolationOutcome(
        KernelDecision.RETRYABLE_CONCURRENCY,
        KernelReasonCode.RETRYABLE_CONCURRENCY,
        retry_as_serialization_failure=True,
    ),
    "uq_belief_support_edge": UniqueViolationOutcome(
        _INVARIANT, KernelReasonCode.INVARIANT_DUPLICATE_SUPPORT_EDGE
    ),
    "uq_beliefs_proposition": UniqueViolationOutcome(
        _INVARIANT, KernelReasonCode.INVARIANT_BELIEF_IDENTITY
    ),
    "uq_action_intents_idempotency": UniqueViolationOutcome(
        _NOOP, KernelReasonCode.ACTION_IDEMPOTENCY_REPLAY
    ),
    # --- ABSENT from the specification's table, and the one that actually fires
    # --- on a replayed payment: claims is statement 3 of DDL section 13 and
    # --- fulfillments is statement 6, so the claim collides first.
    "uq_claims_evidence_proposition": UniqueViolationOutcome(
        _NOOP, KernelReasonCode.CLAIM_SEMANTIC_DUPLICATE
    ),
}

#: `uq_kernel_decisions_terminal_per_proposal` and `uq_outbox_events_aggregate_event`
#: are deliberately NOT here either. Both are real constraints section 7.5 omits,
#: but neither has been OBSERVED reaching this handler, and an unobserved mapping
#: is a guess with a comment on it. They stay on the fail-closed path until a
#: transcript shows one firing.
#:
#: ``state_transitions_case_revision_key`` is deliberately NOT here. The
#: specification maps it, but the DDL downgraded that constraint to a plain
#: index, so it can never raise 23505 and an entry for it would be an
#: unreachable branch that reads as coverage.

#: Anything not in the table. An unknown unique violation is still an invariant
#: breach — it is never silently treated as a duplicate, because "this row
#: already existed" and "this write was wrong" have opposite consequences.
UNKNOWN_UNIQUE_VIOLATION: Final[UniqueViolationOutcome] = UniqueViolationOutcome(
    _INVARIANT, KernelReasonCode.INVARIANT_UNIQUE_VIOLATION
)


def map_unique_violation(constraint_name: str | None) -> UniqueViolationOutcome:
    """Section 7.5's table, with the unknown case mapped rather than raised."""
    if constraint_name is None:
        return UNKNOWN_UNIQUE_VIOLATION
    return UNIQUE_VIOLATION_MAP.get(constraint_name, UNKNOWN_UNIQUE_VIOLATION)


def constraint_name_of(error: pgerr.Error) -> str | None:
    """The constraint a ``23505`` names, from the diagnostics rather than the text."""
    return error.diag.constraint_name


# ---------------------------------------------------------------------------
# Section 1.3 — the side-effect guard (mechanism E2)
# ---------------------------------------------------------------------------

_IN_KERNEL_TX: contextvars.ContextVar[bool] = contextvars.ContextVar("in_kernel_tx", default=False)


class SideEffectInsideTransaction(RuntimeError):  # noqa: N818 - the name is specified
    """An outbound call was attempted inside the serializable transaction.

    The ``Error`` suffix ``N818`` asks for is deliberately absent: this class
    is named in ``12_KERNEL_ALGORITHMS.md`` section 1.3 and the specification
    is the interface. Renaming it here would leave the document describing a
    symbol that does not exist.
    """


def in_kernel_transaction() -> bool:
    """Whether the calling context is inside a kernel transaction."""
    return _IN_KERNEL_TX.get()


def assert_no_side_effects(op: str) -> None:
    """Refuse *op* if we are inside the transaction.

    Every outbound client wrapper calls this first. The refusal is of the
    *call*, not of its effect: a transaction rolls back, an e-mail that has
    already left does not.
    """
    if _IN_KERNEL_TX.get():
        raise SideEffectInsideTransaction(
            f"{op} attempted inside the kernel serializable transaction; the "
            f"callback runs once per retry and its effects cannot be rolled "
            f"back. Compute it before the transaction, or emit an outbox event."
        )


_TX_CALLBACK_MARKER: Final[str] = "__pv_transaction_callback__"


def in_transaction(func: T) -> T:
    """Mark *func* as a transaction callback.

    The decorator does not change behaviour — the transaction is opened by
    :func:`run_in_serializable_tx`. It exists so that
    ``tools/txn_purity_lint.py`` has a syntactic handle on callbacks that are
    passed indirectly, and so that a reader of the function knows the five
    rules of section 7.3 apply to it.
    """
    setattr(func, _TX_CALLBACK_MARKER, True)
    return func


# ---------------------------------------------------------------------------
# Section 7.2 — configuration, the seams, and the loop
# ---------------------------------------------------------------------------


class TransactionRetryConfig(Protocol):
    """The three fields of ``KernelConfig`` this loop reads.

    A Protocol rather than an import: ``provenance_domain.kernel.config`` is
    Phase 4's, and ``provenance_db`` must not depend on ``provenance_domain``
    in either direction (the ``kernel-purity`` contract runs one way; the
    package manifest declines the other). ``KernelConfig`` satisfies this
    structurally, so Phase 4 passes its own config object unchanged.
    """

    @property
    def max_tx_attempts(self) -> int: ...

    @property
    def retry_base_delay_ms(self) -> int: ...

    @property
    def retry_max_delay_ms(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """The v1 defaults of ``12_KERNEL_ALGORITHMS.md`` section 0.5.

    Backoff with base 50 ms and cap 2000 ms: attempt 1 -> 25-75 ms, 2 ->
    50-150 ms, 3 -> 100-300 ms, 4 -> 200-600 ms, then give up. Worst-case
    added latency before exhaustion is about 1.1 s, which keeps the
    synchronous ingestion API inside a sane p99.
    """

    max_tx_attempts: int = 5
    retry_base_delay_ms: int = 50
    retry_max_delay_ms: int = 2_000


DEFAULT_RETRY_POLICY: Final[RetryPolicy] = RetryPolicy()


class Jitter(Protocol):
    """``random.Random``'s ``uniform``, and nothing else."""

    def uniform(self, a: float, b: float) -> float: ...


class TelemetrySink(Protocol):
    """The two emitters section 7.2 uses. ``provenance_telemetry`` implements it."""

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None: ...

    def observe(self, name: str, value: float) -> None: ...


class TxCursor(Protocol):
    async def fetchone(self) -> tuple[Any, ...] | None: ...


class TxConnection(Protocol):
    """The connection surface the loop uses. ``psycopg.AsyncConnection`` satisfies it."""

    async def set_isolation_level(self, level: IsolationLevel | None) -> None: ...

    def transaction(self) -> AbstractAsyncContextManager[Any]: ...

    async def execute(self, query: str, params: Any = None) -> TxCursor: ...


class TxPool(Protocol):
    """The pool surface the loop uses. :class:`~provenance_db.pools.RolePool`
    and ``psycopg_pool.AsyncConnectionPool`` both satisfy it."""

    def connection(self) -> AbstractAsyncContextManager[TxConnection]: ...


@dataclass(frozen=True, slots=True)
class TxResult(Generic[T]):
    """What one committed transaction produced, and what it cost.

    ``retry_count`` is exposed to the caller and to telemetry because two
    different tests read it: single-writer tests assert ``== 0`` and the
    concurrency test asserts ``>= 1`` on at least one run. Retries must appear
    where contention is intended and nowhere else (task plan section 23.9).

    Written with :class:`typing.Generic` rather than PEP 695 syntax: the pinned
    ``mypy`` (1.11.2) rejects ``class TxResult[T]`` outright with "PEP 695
    generics are not yet supported", and a type checker that cannot read the
    signature is a type checker that is not checking it.
    """

    value: T
    retry_count: int
    attempts: int


class RetryExhausted(RuntimeError):  # noqa: N818 - the name is specified
    """The attempt cap was reached. Nothing was written and nothing was enqueued.

    The caller re-drives the identical request with the identical
    ``Idempotency-Key`` over ``503`` + ``Retry-After``
    (``specs/15_API_SPEC.md`` section 4.3). :attr:`reason_codes` is the pair
    the resulting ``KernelCommitResult`` carries, so a caller cannot invent a
    third story about what happened.
    """

    def __init__(self, attempts: int, last: BaseException | None) -> None:
        self.attempts = attempts
        self.last = last
        self.sqlstate: str | None = getattr(last, "sqlstate", None)
        self.decision: Final[KernelDecision] = KernelDecision.RETRYABLE_CONCURRENCY
        self.reason_codes: Final[tuple[KernelReasonCode, ...]] = (
            KernelReasonCode.RETRYABLE_CONCURRENCY,
            KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED,
        )
        super().__init__(
            f"serializable transaction failed on all {attempts} attempts "
            f"(last sqlstate {self.sqlstate}); no side effect was performed and "
            f"nothing was enqueued — the caller re-drives"
        )


def backoff_delay_seconds(attempt: int, config: TransactionRetryConfig, rng: Jitter) -> float:
    """Seconds to wait after a failed *attempt*, jittered.

    ``delay = min(base * 2 ** (attempt - 1), cap)``, then
    ``uniform(0.5 * delay, 1.5 * delay)``. The jitter is symmetric around the
    target rather than ``uniform(0, delay)``: two kernel writers colliding on
    one case is the expected contention pattern, and symmetric jitter keeps
    both from collapsing toward zero delay and re-colliding.
    """
    delay_ms = min(config.retry_base_delay_ms * (2 ** (attempt - 1)), config.retry_max_delay_ms)
    return rng.uniform(0.5 * delay_ms, 1.5 * delay_ms) / 1000


async def run_in_serializable_tx(
    pool: TxPool,
    callback: Callable[[Any, datetime], Awaitable[T]],
    *,
    config: TransactionRetryConfig = DEFAULT_RETRY_POLICY,
    telemetry: TelemetrySink | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Jitter | None = None,
) -> TxResult[T]:
    """Run *callback* inside one ``SERIALIZABLE`` transaction, retrying ``40001``.

    ``callback(conn, tx_now)`` MUST be free of side effects outside this
    connection and MUST re-read every row it depends on. It is called once per
    attempt on a **fresh connection**, and its previous return value is never
    reused: a retry that replayed a plan computed against a rolled-back
    snapshot is exactly how invariant 3's "impossible partial aggregate state"
    gets written.

    Args:
        pool: a pool bound to one SQL role.
        callback: the work. See section 7.3 for the five rules it must obey.
        config: anything with the three fields of :class:`RetryPolicy`.
        telemetry: optional sink for ``kernel_tx_retry``,
            ``kernel_tx_retry_exhausted`` and ``kernel_tx_retries_total``.
        sleep: injected for tests; the default is real.
        rng: injected for tests; the default is a module-level ``Random``.

    Returns:
        :class:`TxResult` carrying the callback's value and the retry count.

    Raises:
        RetryExhausted: every attempt failed with a retryable SQLSTATE.
        psycopg.Error: any non-retryable database error, unchanged and
            immediately — a check violation is not contention and waiting will
            not help.
    """
    jitter = rng if rng is not None else _JITTER
    last: BaseException | None = None

    for attempt in range(1, config.max_tx_attempts + 1):
        try:
            async with pool.connection() as conn:
                await conn.set_isolation_level(IsolationLevel.SERIALIZABLE)
                token = _IN_KERNEL_TX.set(True)
                try:
                    async with conn.transaction():
                        cursor = await conn.execute("SELECT transaction_timestamp()")
                        row = await cursor.fetchone()
                        if row is None:  # pragma: no cover - the server always answers
                            raise RuntimeError("SELECT transaction_timestamp() returned no row")
                        tx_now: datetime = row[0]
                        value = await callback(conn, tx_now)
                finally:
                    _IN_KERNEL_TX.reset(token)

        except pgerr.Error as error:
            sqlstate = _retryable_sqlstate_of(error)
            # Reached through the module object on purpose: PV_SABOTAGE rebinds
            # the attribute there, and a call through any other name would
            # resolve to a reference captured before the rebind, making G3.6
            # pass vacuously.
            if not _MODULE.is_retryable(sqlstate):
                raise
            last = error
            if telemetry is not None:
                telemetry.increment(
                    "kernel_tx_retry", {"sqlstate": str(sqlstate), "attempt": str(attempt)}
                )
            if attempt == config.max_tx_attempts:
                break
            await sleep(backoff_delay_seconds(attempt, config, jitter))
            continue

        if telemetry is not None:
            telemetry.observe("kernel_tx_retries_total", attempt - 1)
        return TxResult(value=value, retry_count=attempt - 1, attempts=attempt)

    if telemetry is not None:
        telemetry.increment("kernel_tx_retry_exhausted")
    raise RetryExhausted(config.max_tx_attempts, last)


def _retryable_sqlstate_of(error: pgerr.Error) -> str | None:
    """The SQLSTATE to classify *error* by.

    A ``23505`` on ``belief_versions_belief_version_no_key`` is reported as
    ``40001`` here, because section 7.5 sends exactly that constraint back
    round the loop: another commit inserted ``version_no = n + 1`` between our
    read and our write, and a fresh read resolves it. Every other unique
    violation keeps its own SQLSTATE and is raised.
    """
    sqlstate = error.sqlstate
    if (
        sqlstate == "23505"
        and map_unique_violation(constraint_name_of(error)).retry_as_serialization_failure
    ):
        return "40001"
    return sqlstate


#: The default jitter source. Seeded from the OS, and used only for backoff —
#: ``12_KERNEL_ALGORITHMS.md`` section 1.3 bans unseeded randomness *inside*
#: the transaction, which this is not.
_JITTER: Final[random.Random] = random.Random()


# ---------------------------------------------------------------------------
# The PV_SABOTAGE hook
#
# `quality/23_PHASE_GATES.md` G3.6 runs:
#
#     PV_SABOTAGE=provenance_db.retry.is_retryable \
#         pytest packages/python/provenance_db/tests/db/test_retry.py -q; echo "exit=$?"
#
# and requires FAILED test_injected_40001_retries_and_commits with exit=1.
#
# RECORDED DEVIATION FROM THE MATRIX'S DEFAULT NEUTERING SHAPE
# ------------------------------------------------------------
# `tests/sabotage_matrix.yaml` documents the shape as "replaces the named
# attribute ON THE MODULE OBJECT with an identity function", and
# `provenance_domain.money` implements exactly that. An identity is the right
# neutering for a *computation*: it type-checks at the call site, returns
# something plausible, and is wrong.
#
# For a *predicate* it is neither plausible nor wrong in the right direction.
# `is_retryable("40001")` under an identity returns the string "40001", which
# is truthy, so the loop would keep retrying and G3.2 would still pass — a
# sabotage that changes nothing is worse than no sabotage, because it reports
# a strength the suite does not have. G3.6's own words are "remove the retry",
# and the honest neutering of "is this retryable?" is a predicate that says no
# to everything. That is what is installed below.
# ---------------------------------------------------------------------------

#: The environment variable the harness sets. Read once, at import.
SABOTAGE_ENV_VAR: Final[str] = "PV_SABOTAGE"

#: The symbols in *this module* the matrix is allowed to neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("is_retryable",)


def _never_retryable(*_args: Any, **_kwargs: Any) -> bool:
    """The neutered :func:`is_retryable`: nothing is ever worth retrying."""
    return False


def sabotage_targets(raw: str | None) -> frozenset[str]:
    """Parse ``PV_SABOTAGE`` into fully qualified symbol names.

    Whitespace- or comma-separated, so one run can neuter several symbols.
    Unset, empty and whitespace-only all mean "no sabotage".
    """
    if not raw:
        return frozenset()
    return frozenset(token for token in raw.replace(",", " ").split() if token)


def install_sabotage(
    namespace: MutableMapping[str, Any],
    module: str,
    symbols: Iterable[str],
    raw: str | None,
) -> tuple[str, ...]:
    """Replace each requested symbol of *module* in *namespace* with a stub.

    A pure function over an explicit namespace rather than import-time magic,
    so the mechanism is testable without re-importing a module or mutating the
    process environment.

    Raises:
        KeyError: a symbol was requested but is not defined here. A stale
            matrix entry must surface loudly; skipping it silently would
            report a green sabotage run for a symbol nobody neutered.
    """
    requested = sabotage_targets(raw)
    if not requested:
        return ()
    replaced: list[str] = []
    for symbol in symbols:
        if f"{module}.{symbol}" not in requested:
            continue
        if symbol not in namespace:
            raise KeyError(f"{module}.{symbol} is not defined in {module}")
        namespace[symbol] = _never_retryable
        replaced.append(symbol)
    return tuple(replaced)


#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = install_sabotage(
    globals(), __name__, SABOTAGE_HOOKS, os.environ.get(SABOTAGE_ENV_VAR)
)
