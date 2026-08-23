"""Configuration constants for prospective memory — ``16_TRIGGER_DSL.md`` §16.

Every value here is transcribed from that section. They are constants rather
than settings because each of them is load-bearing on a *stored* row: a trigger
armed under one ``WAKE_MARGIN_SECONDS`` and woken under another would be judged
by a rule it was never written against.

``WAKE_MARGIN_SECONDS`` in particular is canon, not tuning.
``CANONICAL_DECISIONS.md`` -> *Hero dataset canon* fixes the hero wake at
``due_at`` + ``WAKE_MARGIN_SECONDS`` = ``2026-06-15T00:01:00Z``. Changing this
number silently moves a date four documents agree on.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ARM_BACKDATE_TOLERANCE_HOURS",
    "AST_SCHEMA_VERSION",
    "CONCURRENT_MUTATION_REARM_MINUTES",
    "EVALUATOR_CODE_VERSION",
    "IDEMPOTENCY_RETENTION_DAYS",
    "IDEMPOTENCY_SCOPE",
    "MAX_ARMED_TRIGGERS_PER_CASE",
    "MAX_REARM_GENERATIONS",
    "MAX_UNKNOWN_REARMS",
    "REARM_POLICY",
    "SECONDS_PER_DAY",
    "SWEEPER_INTERVAL_MINUTES",
    "SWEEPER_OVERDUE_GRACE_MINUTES",
    "TRIGGER_EVAL_MAX_ATTEMPTS",
    "WAKE_MARGIN_SECONDS",
    "schedule_name_for",
]

AST_SCHEMA_VERSION: Final[str] = "1.0"

#: Bumped whenever Kleene semantics, coercion rules, the registry or a derived
#: field's formula changes, so an old evaluation's replay is never silently
#: reinterpreted by new code. Recorded in every stored evaluation payload.
EVALUATOR_CODE_VERSION: Final[str] = "trigger-eval/1.0.0"

#: ``due_at`` + this is when the schedule is set. EventBridge Scheduler has
#: one-minute granularity, so the margin means jitter cannot deliver *before*
#: the real deadline and the common case never wastes a ``WOKE_TOO_EARLY``.
WAKE_MARGIN_SECONDS: Final[int] = 60

#: Warn, do not reject, beyond this. The hero seed depends on it: the deposit
#: deadline genuinely elapsed in June and the trigger is armed after the fact.
ARM_BACKDATE_TOLERANCE_HOURS: Final[int] = 24

#: A bound on how much prospective memory one hostile artifact can create.
MAX_ARMED_TRIGGERS_PER_CASE: Final[int] = 16

#: After 64 generations a trigger that has never resolved is a bug, not an
#: obligation. It stops and alarms rather than re-arming forever.
MAX_REARM_GENERATIONS: Final[int] = 64

#: Persistent ``UNKNOWN`` means the data is broken — usually a commitment
#: admitted with NULL amounts. The correct response is an operator alarm, not
#: an infinite retry loop.
MAX_UNKNOWN_REARMS: Final[int] = 3

#: Projection rebuilds when the case revision moves between read and write.
TRIGGER_EVAL_MAX_ATTEMPTS: Final[int] = 3

SWEEPER_INTERVAL_MINUTES: Final[int] = 15
SWEEPER_OVERDUE_GRACE_MINUTES: Final[int] = 10
IDEMPOTENCY_RETENTION_DAYS: Final[int] = 90

#: ``16_TRIGGER_DSL.md`` §9.9. One scope, one key space.
#:
#: Lower-cased against the spec, and the schema is why.
#: ``ck_idempotency_scope_shape`` is ``CHECK (scope ~ '^[a-z][a-z0-9_.]{2,63}$')``
#: (``0008_events_infrastructure.py``), so ``TRIGGER_EVALUATION`` -- which §9.9
#: prints -- cannot be written at all. The claim is the FIRST insert of the fire
#: transaction, so every wake, fire and no-op alike, would have been refused
#: before anything was committed. This could not surface while the only
#: ``TriggerKernel`` was a test fake: a fake has no CHECK constraints.
#:
#: It also lines up with the API's own scope vocabulary
#: (``app/api/idempotency.py::IDEMPOTENCY_SCOPES``), every member of which is
#: dotted and lower-case for the same reason.
IDEMPOTENCY_SCOPE: Final[str] = "trigger.evaluation"

#: ``§11.6``: a Kernel that exhausts its retries re-arms in five minutes and
#: never fires optimistically.
CONCURRENT_MUTATION_REARM_MINUTES: Final[int] = 5

SECONDS_PER_DAY: Final[int] = 86_400

#: ``16_TRIGGER_DSL.md`` §9.11, as ISO-8601 day counts. A ``NO_OP`` that leaves
#: the obligation genuinely open re-arms rather than dying, or prospective
#: memory would be single-shot; the backoff is what keeps that from becoming a
#: re-arm storm.
REARM_POLICY: Final[dict[str, tuple[int, ...]]] = {
    "COMMITMENT_DEADLINE": (1, 3, 7, 14, 30),
    "RESPONSE_DEADLINE": (1, 3, 7),
    "CONFLICT_TIMEOUT": (7, 14),
    "WARRANTY_WINDOW": (30,),
}


def schedule_name_for(trigger_id_hex: str, evaluation_version: int) -> str:
    """``pv-trg-<uuid32>-v<N>`` — §9.3.

    Deterministic so reconciliation can diff schedules against rows without
    extra bookkeeping; generation-stamped so a late delivery from a superseded
    schedule is detectable; and it doubles as the wake identity, which is the
    idempotency key. One logical wake, one key, however many deliveries.
    """
    return f"pv-trg-{trigger_id_hex}-v{evaluation_version}"
