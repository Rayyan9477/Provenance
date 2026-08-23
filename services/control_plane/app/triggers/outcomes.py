"""The closed outcome taxonomy — one result, exactly one reason, one state.

Authority
---------
- ``CANONICAL_DECISIONS.md`` -> *Names and counts*: "Trigger results ``FIRED``,
  ``NO_OP``, ``DISARMED``, ``EXPIRED``, ``ERROR`` plus one closed-set reason
  code."
- ``16_TRIGGER_DSL.md`` §9.10 (the taxonomy), §9.11 (re-arm), §11.2
  (``classify_false``).
- ``db/migrations/versions/0006_prospective_memory.py``,
  ``ck_prospective_triggers_last_reason`` — the same partition, written as a
  database CHECK. The two are compared by test rather than by eye.

Why "no-op" is not one thing
----------------------------
A predicate that came out ``FALSE`` can mean two opposite things. *The landlord
paid*: the obligation is discharged, there is no future in which the predicate
becomes true again, and re-arming would be a standing promise to keep asking
about a settled matter. *The deadline has not arrived yet*: false now, true
later, and dropping the trigger would silently lose the obligation.
:func:`classify_false` is the small deterministic function that tells them
apart. It is not an inference and it consults no model — it reads the statuses
the Kernel wrote.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from provenance_domain.enums import TriggerReasonCode, TriggerResult, TriggerState
from services.control_plane.app.triggers.ast import PredicateSpec
from services.control_plane.app.triggers.config import REARM_POLICY
from services.control_plane.app.triggers.projection import Projection

__all__ = [
    "RESULT_REASONS",
    "STATE_AFTER",
    "Outcome",
    "classify_false",
    "disarm_reason",
    "rearm_delay",
    "reason_is_legal",
]

#: The partition. Each reason code accompanies exactly one result, which is
#: what "plus one closed-set reason code" means and what the migration's CHECK
#: enforces at the database.
RESULT_REASONS: Final[dict[TriggerResult, frozenset[TriggerReasonCode]]] = {
    TriggerResult.FIRED: frozenset(
        {
            TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
            TriggerReasonCode.RESPONSE_DEADLINE_MISSED,
            TriggerReasonCode.CONFLICT_UNRESOLVED_TIMEOUT,
            TriggerReasonCode.WARRANTY_WINDOW_CLOSING,
        }
    ),
    TriggerResult.NO_OP: frozenset(
        {
            TriggerReasonCode.PREDICATE_FALSE,
            TriggerReasonCode.PREDICATE_UNKNOWN,
            TriggerReasonCode.WOKE_TOO_EARLY,
            TriggerReasonCode.STALE_SCHEDULE_GENERATION,
            TriggerReasonCode.TRIGGER_NOT_ARMED,
            TriggerReasonCode.CONCURRENT_CASE_MUTATION,
            TriggerReasonCode.IDEMPOTENT_REPLAY,
        }
    ),
    TriggerResult.DISARMED: frozenset(
        {
            TriggerReasonCode.COMMITMENT_SATISFIED,
            TriggerReasonCode.COMMITMENT_SUPERSEDED,
            TriggerReasonCode.BINDING_SUPERSEDED,
            TriggerReasonCode.CASE_RESOLVED,
            TriggerReasonCode.CASE_SUPERSEDED,
            TriggerReasonCode.USER_DISMISSED,
        }
    ),
    TriggerResult.EXPIRED: frozenset(
        {TriggerReasonCode.TRIGGER_EXPIRED, TriggerReasonCode.REARM_BUDGET_EXHAUSTED}
    ),
    TriggerResult.ERROR: frozenset(
        {
            TriggerReasonCode.BINDING_UNRESOLVED,
            TriggerReasonCode.PROJECTION_FAILED,
            TriggerReasonCode.KERNEL_UNAVAILABLE,
        }
    ),
}

#: §9.10's terminal-state column. ``NO_OP`` leaves the trigger ``ARMED`` — it is
#: an expected, first-class outcome and not a failure — and ``ERROR`` leaves it
#: ``ARMED`` too, deliberately, so an internal fault never disarms an obligation.
STATE_AFTER: Final[dict[TriggerResult, TriggerState]] = {
    TriggerResult.FIRED: TriggerState.FIRED,
    TriggerResult.NO_OP: TriggerState.ARMED,
    TriggerResult.DISARMED: TriggerState.DISARMED,
    TriggerResult.EXPIRED: TriggerState.EXPIRED,
    TriggerResult.ERROR: TriggerState.ARMED,
}

#: The commitment statuses that mean "discharged, stop watching".
_SATISFIED_STATUSES: Final[frozenset[str]] = frozenset({"FULFILLED"})

#: The commitment statuses that mean "replaced; the successor carries its own
#: trigger". Written as an explicit set rather than ``not in {ACTIVE, ...}``
#: because a negated predicate over a seven-member vocabulary lets new members
#: through by default, and a new commitment status must not silently change
#: what a stored trigger does.
_REPLACED_STATUSES: Final[frozenset[str]] = frozenset({"SUPERSEDED", "EXPIRED"})

_CASE_TERMINAL_REASONS: Final[dict[str, TriggerReasonCode]] = {
    "RESOLVED": TriggerReasonCode.CASE_RESOLVED,
    "SUPERSEDED": TriggerReasonCode.CASE_SUPERSEDED,
}


def reason_is_legal(result: TriggerResult, reason: TriggerReasonCode) -> bool:
    """Whether *reason* may accompany *result*."""
    return reason in RESULT_REASONS[result]


@dataclass(frozen=True, slots=True)
class Outcome:
    """One terminal decision about one wake.

    Validated on construction: a ``DISARMED`` carrying ``PREDICATE_FALSE`` reads
    plausibly and means nothing, and refusing it here means the database's CHECK
    is never the first thing to notice.
    """

    result: TriggerResult
    reason_code: TriggerReasonCode
    #: Whether a ``NO_OP`` should re-arm with backoff. Only meaningful for
    #: ``NO_OP``; ``DISARMED`` and ``EXPIRED`` are terminal for the instance.
    rearm: bool = False

    def __post_init__(self) -> None:
        if not reason_is_legal(self.result, self.reason_code):
            raise ValueError(
                f"{self.reason_code.value} is not a legal reason for "
                f"{self.result.value}; see ck_prospective_triggers_last_reason"
            )
        if self.rearm and self.result is not TriggerResult.NO_OP:
            raise ValueError(f"{self.result.value} is terminal and does not re-arm")

    @property
    def state_after(self) -> TriggerState:
        return STATE_AFTER[self.result]

    @property
    def increments_case_revision(self) -> bool:
        """Only a fire touches the case aggregate.

        ``02_DATA_MEMORY_TRANSACTIONS.md`` §10: "If one proposal produces no
        canonical change, do not increment revision." A no-op updates only
        trigger-local columns, which is why D8 can assert ``cases.revision``
        unchanged and mean it.
        """
        return self.result is TriggerResult.FIRED


def disarm_reason(
    *, case_status: str | None, commitment_statuses: Sequence[str | None]
) -> TriggerReasonCode | None:
    """Why prospective memory should stop watching, or ``None`` to keep watching.

    **This is the one implementation of the disarm precedence.** Two components
    answer the question "why did this trigger stop?" — the evaluator, when a
    predicate comes out ``FALSE``, and the Memory Kernel, when a committed
    proposal disarms a trigger outright. If they answered differently about the
    same event the audit record would contradict itself, and "we agreed in a
    review thread" is not a mechanism. Both call this.

    It takes plain scalars rather than a :class:`Projection` deliberately: the
    Kernel has a write plan rather than a projection at the moment it decides,
    and a signature only one caller could satisfy would guarantee the second
    copy this function exists to prevent.

    The order is the argument, and it is not stylistic:

    1. **The case outranks its commitments.** Reporting
       ``COMMITMENT_SATISFIED`` for a case that resolved while money was still
       owed puts a false statement into an audit record, and the audit record is
       the product.
    2. **Satisfaction requires *every* bound obligation to be discharged.** One
       of two paid is a reason to keep watching, not to stop; disarming there
       would forget the second obligation.
    3. **Any replaced obligation disarms.** The subject the trigger watches has
       been superseded and the successor carries its own trigger, so continuing
       to watch this one would chase a commitment nobody owes any more.

    Args:
        case_status: the case's status, or the status this commit is moving it
            to. ``None`` when the commit does not move the case.
        commitment_statuses: the status of each *bound* commitment. Order is
            irrelevant by construction — ``bindings`` is a mapping and its
            iteration order is an artifact of whoever serialized the predicate,
            so reading it as meaning would let the same world produce two
            different audit records.

    Returns:
        A reason code that is always legal for ``DISARMED``, or ``None`` when
        nothing terminal has happened.
    """
    if case_status is not None:
        terminal = _CASE_TERMINAL_REASONS.get(case_status)
        if terminal is not None:
            return terminal

    statuses = tuple(commitment_statuses)
    if any(status in _REPLACED_STATUSES for status in statuses):
        return TriggerReasonCode.COMMITMENT_SUPERSEDED
    # `statuses` guards the empty case: `all([])` is True, and a trigger that
    # binds no commitment watches only the case, so nothing has been discharged.
    if statuses and all(status in _SATISFIED_STATUSES for status in statuses):
        return TriggerReasonCode.COMMITMENT_SATISFIED
    return None


def classify_false(projection: Projection, spec: PredicateSpec, trigger_type: str) -> Outcome:
    """Distinguish FALSE-and-done from FALSE-and-keep-watching — §11.2.

    The precedence itself lives in :func:`disarm_reason`, which the Memory
    Kernel also calls. This function only supplies the projection's values to it
    and turns the answer into an :class:`Outcome`.
    """
    del trigger_type  # the classification is status-driven; the type selects backoff
    reason = disarm_reason(
        case_status=str(projection.values["case.status"]),
        commitment_statuses=[
            projection.values.get(f"commitments.{binding.name}.status") for binding in spec.bindings
        ],
    )
    if reason is not None:
        return Outcome(result=TriggerResult.DISARMED, reason_code=reason)

    # Still open: false now, possibly true later. Re-arm with backoff, or
    # prospective memory would be single-shot.
    return Outcome(
        result=TriggerResult.NO_OP, reason_code=TriggerReasonCode.PREDICATE_FALSE, rearm=True
    )


def rearm_delay(trigger_type: str, evaluation_version: int) -> timedelta:
    """The backoff for the next generation — §9.11.

    Saturates at the last step rather than wrapping. A sequence that reset would
    turn a long-lived obligation into a daily re-arm storm, which §17 R5 costs
    in Scheduler quota rather than in correctness — but it is still wrong.
    """
    sequence = REARM_POLICY[trigger_type]
    index = min(max(evaluation_version, 1) - 1, len(sequence) - 1)
    return timedelta(days=sequence[index])
