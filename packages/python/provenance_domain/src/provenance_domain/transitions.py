"""Frozen state-machine transition tables and the single legality helper.

Authority
---------
* `specs/11_CONTRACTS.md` §4 — this module, table by table, and the names in
  ``__all__``.
* `specs/12_KERNEL_ALGORITHMS.md` §5.1 — the 10x10 case grid, including the
  ``G1`` / ``G2`` guard labels this module reports as ``TransitionVerdict.code``
  and the rule that a self-transition is not a transition.
* `specs/16_TRIGGER_DSL.md` §9.10 — the trigger outcome taxonomy: which
  ``TriggerResult`` leaves a ``prospective_triggers`` row in which
  ``TriggerState``.
* `CANONICAL_DECISIONS.md`, *Hero commit canon* — ``CONTRADICTORY_EVIDENCE`` is
  a **guard** on ``RESOLVED -> REOPENED``, so ``CONTRADICTORY_EVIDENCE_ADMITTED``
  and ``RC_CONTRADICTORY_EVIDENCE`` raise rather than merely reading oddly.

Design rules
------------
* Tables are ``MappingProxyType`` over ``frozenset`` values: immutable at
  import time, cheap to read, impossible to mutate from a caller.
* A transition is legal only if it appears in the table AND satisfies any
  registered guard.
* Guards are reason-code allowlists, not callables. The one guard that must
  inspect an amount — ``FULFILLED`` is unreachable while ``outstanding > 0`` —
  is a separate, explicitly amount-aware helper (:func:`commitment_transition`)
  rather than a callable smuggled into a table, because the Kernel evaluates
  the tables inside a serializable transaction with no I/O.
* Self-transitions are illegal unless the machine sets ``allow_self_loop=True``:
  a no-op must not increment a case revision.
* Every answer is a :class:`TransitionVerdict`, never a bare ``bool``. A bool
  forces the caller to invent an error message, and invented error messages are
  how closed reason-code sets leak. The verdict is falsy when the transition is
  refused, so ``if not legal_transition(...)`` reads exactly as it would have.

Legality is necessary, not sufficient (§4.4): this module answers "is this
shape of change allowed", not "is this change consistent with the amounts".
The Kernel calls it at step 14 and the invariant functions at step 16.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from provenance_domain.enums import (
    # `CASE_REOPEN_REASON_CODES` is declared in `enums.py` (T1.1) and
    # re-exported here because `11_CONTRACTS.md` §4 lists it in this module's
    # `__all__`. Declaring it twice is how a closed set acquires two
    # memberships.
    CASE_REOPEN_REASON_CODES,
    ActionState,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    EpistemicStatus,
    KernelReasonCode,
    OutboxStatus,
    ProposalStatus,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
)

__all__ = [
    "ACTION_EXECUTE_REASON_CODES",
    "ACTION_MACHINE",
    "ACTION_STALE_REASON_CODES",
    "ACTION_TRANSITIONS",
    "CASE_MACHINE",
    "CASE_REOPEN_REASON_CODES",
    "CASE_SUPERSEDE_REASON_CODES",
    "CASE_TRANSITIONS",
    "COMMITMENT_MACHINE",
    "COMMITMENT_TRANSITIONS",
    "COMMITMENT_UNFULFIL_REASON_CODES",
    "CONFLICT_MACHINE",
    "CONFLICT_REOPEN_REASON_CODES",
    "CONFLICT_TRANSITIONS",
    "EPISTEMIC_MACHINE",
    "EPISTEMIC_TRANSITIONS",
    "GUARD_CODE",
    "ILLEGAL_CODE",
    "IllegalTransition",
    "IllegalTransitionError",
    "LEGAL_CODE",
    "MACHINES",
    "NOOP_CODE",
    "OUTBOX_MACHINE",
    "OUTBOX_TRANSITIONS",
    "PROPOSAL_MACHINE",
    "PROPOSAL_TRANSITIONS",
    "StateMachine",
    "TRIGGER_MACHINE",
    "TRIGGER_RESULT_STATE",
    "TRIGGER_TRANSITIONS",
    "TransitionVerdict",
    "assert_transition",
    "commitment_transition",
    "legal_transition",
    "reachable_states",
    "resolve_machine",
    "trigger_wake_transition",
]

# ---------------------------------------------------------------------------
# Verdict codes — the vocabulary of `TransitionVerdict.code`
# ---------------------------------------------------------------------------

#: The cell is legal unconditionally (`Y` in the 12 §5.1 grid).
LEGAL_CODE: Final[str] = "Y"

#: The cell is not in the table at all (`-` in the grid), or the source state is
#: terminal, or one of the two states is not a member of this machine.
ILLEGAL_CODE: Final[str] = "ILLEGAL"

#: A guarded cell whose machine declares no more specific label. The case
#: machine labels its two guards `G1` and `G2` because `12 §5.1` does.
GUARD_CODE: Final[str] = "G"

#: A wake that legitimately leaves the state alone (`16 §9.10`: `NO_OP` and
#: `ERROR` update trigger-local columns only and never touch the case).
NOOP_CODE: Final[str] = "NO_OP"


class IllegalTransitionError(ValueError):
    """Raised when a state change is not in the frozen transition table.

    Carries structured attributes so the Kernel can turn it into a
    ``REJECTED_INVARIANT`` decision with a machine-readable reason code
    (``CASE_TRANSITION_ILLEGAL`` for the case machine) instead of a
    stringly-typed error.
    """

    def __init__(
        self,
        machine: str,
        from_state: str,
        to_state: str,
        *,
        reason_code: str | None = None,
        detail: str = "",
    ) -> None:
        self.machine = machine
        self.from_state = from_state
        self.to_state = to_state
        self.reason_code = reason_code
        self.detail = detail
        message = f"{machine}: {from_state} -> {to_state} is not a legal transition"
        if reason_code is not None:
            message += f" with reason_code={reason_code!r}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


#: `CANONICAL_DECISIONS.md` (*Hero commit canon*) names the exception
#: `IllegalTransition`; `11_CONTRACTS.md` §4 declares the class as
#: `IllegalTransitionError`. One class, both spellings, so neither document is
#: wrong and no second exception type can drift into existence.
IllegalTransition = IllegalTransitionError


@dataclass(frozen=True, slots=True)
class TransitionVerdict:
    """Why a state change is permitted, or why it is not.

    Falsy when refused, so it substitutes for the boolean the Kernel used to
    get while still carrying the guard that rejected and the reason code that
    was offered.
    """

    machine: str
    from_state: str
    to_state: str
    legal: bool
    code: str
    guard: str | None = None
    allowed_reason_codes: frozenset[str] | None = None
    reason_code: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.legal

    def raise_if_illegal(self) -> None:
        """Raise :class:`IllegalTransitionError` unless this verdict is legal."""
        if self.legal:
            return
        raise IllegalTransitionError(
            self.machine,
            self.from_state,
            self.to_state,
            reason_code=self.reason_code,
            detail=self.detail,
        )


@dataclass(frozen=True, slots=True)
class StateMachine:
    """An immutable declaration of one aggregate's legal state changes."""

    name: str
    transitions: Mapping[str, frozenset[str]]
    terminal: frozenset[str]
    guards: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType({})
    #: Guard labels, for machines whose specification names its guards. The
    #: case machine reports `G1` and `G2` so a test can compare a verdict
    #: against the grid in `12_KERNEL_ALGORITHMS.md` §5.1 cell for cell.
    guard_codes: Mapping[tuple[str, str], str] = MappingProxyType({})
    allow_self_loop: bool = False

    @property
    def states(self) -> frozenset[str]:
        out: set[str] = set(self.transitions)
        for targets in self.transitions.values():
            out.update(targets)
        return frozenset(out)

    def can(self, from_state: str, to_state: str, *, reason_code: str | None = None) -> bool:
        return bool(legal_transition(self, from_state, to_state, reason_code=reason_code))


def _table(raw: dict[str, Iterable[str]]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType({str(k): frozenset(str(v) for v in vs) for k, vs in raw.items()})


# ---------------------------------------------------------------------------
# 4.1 Case machine
#
# Stated assumption, carried from `11_CONTRACTS.md` §4.1:
# `02_DATA_MEMORY_TRANSACTIONS.md` §13 calls SUPERSEDED "terminal for a case
# replaced by another case" but does not enumerate its inbound edges. It is
# reachable from every non-terminal state, guarded by a merge/split reason code,
# so case merges are expressible without a later schema change.
# ---------------------------------------------------------------------------

CASE_SUPERSEDE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"MERGED_INTO_CASE", "SPLIT_INTO_CASES", "DUPLICATE_CASE"}
)

_CASE_NON_TERMINAL: Final[tuple[CaseStatus, ...]] = (
    CaseStatus.OPEN,
    CaseStatus.WAITING,
    CaseStatus.ACTIONABLE,
    CaseStatus.IN_PROGRESS,
    CaseStatus.DISPUTED,
    CaseStatus.BLOCKED,
    CaseStatus.AWAITING_USER,
    CaseStatus.RESOLVED,
    CaseStatus.REOPENED,
)

CASE_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        CaseStatus.OPEN: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.WAITING: (
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.ACTIONABLE: (
            CaseStatus.IN_PROGRESS,
            CaseStatus.AWAITING_USER,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.IN_PROGRESS: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.DISPUTED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.AWAITING_USER,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.BLOCKED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.AWAITING_USER: (
            CaseStatus.ACTIONABLE,
            CaseStatus.IN_PROGRESS,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        # The hero scenario lives on this line.
        CaseStatus.RESOLVED: (CaseStatus.REOPENED, CaseStatus.SUPERSEDED),
        CaseStatus.REOPENED: (
            CaseStatus.WAITING,
            CaseStatus.ACTIONABLE,
            CaseStatus.DISPUTED,
            CaseStatus.RESOLVED,
            CaseStatus.SUPERSEDED,
        ),
        CaseStatus.SUPERSEDED: (),
    }
)

CASE_MACHINE: Final[StateMachine] = StateMachine(
    name="case",
    transitions=CASE_TRANSITIONS,
    terminal=frozenset({str(CaseStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (str(CaseStatus.RESOLVED), str(CaseStatus.REOPENED)): CASE_REOPEN_REASON_CODES,
            **{
                (str(s), str(CaseStatus.SUPERSEDED)): CASE_SUPERSEDE_REASON_CODES
                for s in _CASE_NON_TERMINAL
            },
        }
    ),
    guard_codes=MappingProxyType(
        {
            (str(CaseStatus.RESOLVED), str(CaseStatus.REOPENED)): "G1",
            **{(str(s), str(CaseStatus.SUPERSEDED)): "G2" for s in _CASE_NON_TERMINAL},
        }
    ),
    allow_self_loop=False,
)


# ---------------------------------------------------------------------------
# 4.2 Commitment, conflict, action machines
#
# `FULFILLED -> DISPUTED` exists deliberately: a settled obligation can be
# reopened by a clawback or a counterparty retraction. It is guarded so a
# careless proposal cannot silently un-fulfil an obligation.
#
# There is no edge from PROPOSED or NEEDS_REVIEW to EXECUTING. The only initial
# route to EXECUTING is APPROVED -> EXECUTING, guarded by REVALIDATION_PASSED;
# a retry may use FAILED_RETRYABLE -> EXECUTING under the same guard. That is
# invariant 4 expressed as a table.
# ---------------------------------------------------------------------------

COMMITMENT_UNFULFIL_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "FULFILLMENT_REVERSED",
        "PAYMENT_CLAWED_BACK",
        "USER_DISPUTE",
        "COUNTERPARTY_RETRACTION",
    }
)

COMMITMENT_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        CommitmentStatus.PROPOSED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.ACTIVE: (
            CommitmentStatus.PARTIAL,
            CommitmentStatus.FULFILLED,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.PARTIAL: (
            CommitmentStatus.FULFILLED,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.DISPUTED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.PARTIAL,
            CommitmentStatus.FULFILLED,
            CommitmentStatus.EXPIRED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.FULFILLED: (
            CommitmentStatus.DISPUTED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.EXPIRED: (
            CommitmentStatus.ACTIVE,
            CommitmentStatus.DISPUTED,
            CommitmentStatus.SUPERSEDED,
        ),
        CommitmentStatus.SUPERSEDED: (),
    }
)

COMMITMENT_MACHINE: Final[StateMachine] = StateMachine(
    name="commitment",
    transitions=COMMITMENT_TRANSITIONS,
    terminal=frozenset({str(CommitmentStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (
                str(CommitmentStatus.FULFILLED),
                str(CommitmentStatus.DISPUTED),
            ): COMMITMENT_UNFULFIL_REASON_CODES,
        }
    ),
)

CONFLICT_REOPEN_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"NEW_CONTRADICTORY_EVIDENCE", "USER_REJECTED_AUTO_RESOLUTION", "AUTHORITY_REASSESSED"}
)

CONFLICT_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ConflictStatus.OPEN: (
            ConflictStatus.AUTO_RESOLVED,
            ConflictStatus.NEEDS_HUMAN,
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.NEEDS_HUMAN: (
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.AUTO_RESOLVED: (
            ConflictStatus.OPEN,
            ConflictStatus.NEEDS_HUMAN,
            ConflictStatus.RESOLVED,
            ConflictStatus.SUPERSEDED,
        ),
        ConflictStatus.RESOLVED: (ConflictStatus.OPEN, ConflictStatus.SUPERSEDED),
        ConflictStatus.SUPERSEDED: (),
    }
)

CONFLICT_MACHINE: Final[StateMachine] = StateMachine(
    name="conflict",
    transitions=CONFLICT_TRANSITIONS,
    terminal=frozenset({str(ConflictStatus.SUPERSEDED)}),
    guards=MappingProxyType(
        {
            (
                str(ConflictStatus.RESOLVED),
                str(ConflictStatus.OPEN),
            ): CONFLICT_REOPEN_REASON_CODES,
            (
                str(ConflictStatus.AUTO_RESOLVED),
                str(ConflictStatus.OPEN),
            ): CONFLICT_REOPEN_REASON_CODES,
        }
    ),
)

ACTION_EXECUTE_REASON_CODES: Final[frozenset[str]] = frozenset({"REVALIDATION_PASSED"})

ACTION_STALE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "CASE_REVISION_CHANGED",
        "DRAFT_HASH_CHANGED",
        "SUPPORT_BELIEF_SUPERSEDED",
        "USER_CANCELLED",
    }
)

ACTION_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ActionState.PROPOSED: (
            ActionState.NEEDS_REVIEW,
            ActionState.APPROVED,
            ActionState.REJECTED,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.NEEDS_REVIEW: (
            ActionState.APPROVED,
            ActionState.REJECTED,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.APPROVED: (
            ActionState.EXECUTING,
            ActionState.NEEDS_REVIEW,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.EXECUTING: (
            ActionState.EXECUTED,
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_FINAL,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.FAILED_RETRYABLE: (
            ActionState.EXECUTING,
            ActionState.NEEDS_REVIEW,
            ActionState.FAILED_FINAL,
            ActionState.CANCELLED,
            ActionState.CANCELLED_STALE,
        ),
        ActionState.EXECUTED: (),
        ActionState.REJECTED: (),
        ActionState.FAILED_FINAL: (),
        ActionState.CANCELLED: (),
        ActionState.CANCELLED_STALE: (),
    }
)

ACTION_MACHINE: Final[StateMachine] = StateMachine(
    name="action_intent",
    transitions=ACTION_TRANSITIONS,
    terminal=frozenset(
        {
            str(ActionState.EXECUTED),
            str(ActionState.REJECTED),
            str(ActionState.FAILED_FINAL),
            str(ActionState.CANCELLED),
            str(ActionState.CANCELLED_STALE),
        }
    ),
    guards=MappingProxyType(
        {
            (
                str(ActionState.APPROVED),
                str(ActionState.EXECUTING),
            ): ACTION_EXECUTE_REASON_CODES,
            (
                str(ActionState.FAILED_RETRYABLE),
                str(ActionState.EXECUTING),
            ): ACTION_EXECUTE_REASON_CODES,
            (
                str(ActionState.APPROVED),
                str(ActionState.CANCELLED_STALE),
            ): ACTION_STALE_REASON_CODES,
        }
    ),
)


# ---------------------------------------------------------------------------
# 4.3 Trigger, outbox, proposal, epistemic machines
#
# Epistemic status is a property of a belief **version**, not of a mutable row.
# "Transition" therefore means: the status a new version may carry given the
# status of the version it supersedes. `allow_self_loop=True` because v2 may
# legitimately restate v1's status with stronger grounding.
# ---------------------------------------------------------------------------

TRIGGER_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        TriggerState.ARMED: (
            TriggerState.FIRED,
            TriggerState.DISARMED,
            TriggerState.EXPIRED,
        ),
        TriggerState.FIRED: (
            TriggerState.ARMED,
            TriggerState.DISARMED,
            TriggerState.EXPIRED,
        ),
        TriggerState.DISARMED: (),
        TriggerState.EXPIRED: (),
    }
)

TRIGGER_MACHINE: Final[StateMachine] = StateMachine(
    name="prospective_trigger",
    transitions=TRIGGER_TRANSITIONS,
    terminal=frozenset({str(TriggerState.DISARMED), str(TriggerState.EXPIRED)}),
)

#: `16_TRIGGER_DSL.md` §9.10, the outcome taxonomy. One wake produces exactly
#: one `TriggerResult`; this map says which `TriggerState` the row is left in.
#: `None` means "unchanged": a `NO_OP` re-arms or waits and an `ERROR` is an
#: HTTP 5xx that Scheduler retries, and neither touches the case aggregate, so
#: neither may be modelled as a state change that could consume a revision.
TRIGGER_RESULT_STATE: Final[Mapping[str, str | None]] = MappingProxyType(
    {
        str(TriggerResult.FIRED): str(TriggerState.FIRED),
        str(TriggerResult.NO_OP): None,
        str(TriggerResult.DISARMED): str(TriggerState.DISARMED),
        str(TriggerResult.EXPIRED): str(TriggerState.EXPIRED),
        str(TriggerResult.ERROR): None,
    }
)

OUTBOX_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        OutboxStatus.PENDING: (OutboxStatus.DISPATCHING,),
        OutboxStatus.DISPATCHING: (
            OutboxStatus.DISPATCHED,
            OutboxStatus.FAILED_RETRYABLE,
        ),
        OutboxStatus.FAILED_RETRYABLE: (OutboxStatus.DISPATCHING, OutboxStatus.DEAD),
        OutboxStatus.DISPATCHED: (),
        OutboxStatus.DEAD: (),
    }
)

OUTBOX_MACHINE: Final[StateMachine] = StateMachine(
    name="outbox_event",
    transitions=OUTBOX_TRANSITIONS,
    terminal=frozenset({str(OutboxStatus.DISPATCHED), str(OutboxStatus.DEAD)}),
)

_PROPOSAL_OUTCOMES: Final[tuple[ProposalStatus, ...]] = (
    ProposalStatus.ACCEPTED,
    ProposalStatus.ACCEPTED_WITH_CONFLICT,
    ProposalStatus.NOOP_DUPLICATE,
    ProposalStatus.PENDING_IDENTITY,
    ProposalStatus.PENDING_HUMAN_REVIEW,
    ProposalStatus.REJECTED_INVALID_PROVENANCE,
    ProposalStatus.REJECTED_INVARIANT,
    ProposalStatus.REJECTED_SCHEMA,
)

PROPOSAL_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        ProposalStatus.SUBMITTED: _PROPOSAL_OUTCOMES,
        ProposalStatus.PENDING_IDENTITY: (
            ProposalStatus.ACCEPTED,
            ProposalStatus.ACCEPTED_WITH_CONFLICT,
            ProposalStatus.PENDING_HUMAN_REVIEW,
            ProposalStatus.REJECTED_INVALID_PROVENANCE,
            ProposalStatus.REJECTED_INVARIANT,
        ),
        ProposalStatus.PENDING_HUMAN_REVIEW: (
            ProposalStatus.ACCEPTED,
            ProposalStatus.ACCEPTED_WITH_CONFLICT,
            ProposalStatus.REJECTED_INVARIANT,
        ),
        ProposalStatus.ACCEPTED: (),
        ProposalStatus.ACCEPTED_WITH_CONFLICT: (),
        ProposalStatus.NOOP_DUPLICATE: (),
        ProposalStatus.REJECTED_INVALID_PROVENANCE: (),
        ProposalStatus.REJECTED_INVARIANT: (),
        ProposalStatus.REJECTED_SCHEMA: (),
    }
)

PROPOSAL_MACHINE: Final[StateMachine] = StateMachine(
    name="memory_proposal",
    transitions=PROPOSAL_TRANSITIONS,
    terminal=frozenset(
        {
            str(ProposalStatus.ACCEPTED),
            str(ProposalStatus.ACCEPTED_WITH_CONFLICT),
            str(ProposalStatus.NOOP_DUPLICATE),
            str(ProposalStatus.REJECTED_INVALID_PROVENANCE),
            str(ProposalStatus.REJECTED_INVARIANT),
            str(ProposalStatus.REJECTED_SCHEMA),
        }
    ),
)

EPISTEMIC_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = _table(
    {
        EpistemicStatus.CONFIRMED: (
            EpistemicStatus.PROBABLE,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.PROBABLE: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.UNCERTAIN: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.PROBABLE,
            EpistemicStatus.DISPUTED,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.DISPUTED: (
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.PROBABLE,
            EpistemicStatus.UNCERTAIN,
            EpistemicStatus.SUPERSEDED,
            EpistemicStatus.RETRACTED,
        ),
        EpistemicStatus.SUPERSEDED: (),
        EpistemicStatus.RETRACTED: (),
    }
)

EPISTEMIC_MACHINE: Final[StateMachine] = StateMachine(
    name="epistemic_status",
    transitions=EPISTEMIC_TRANSITIONS,
    terminal=frozenset({str(EpistemicStatus.SUPERSEDED), str(EpistemicStatus.RETRACTED)}),
    allow_self_loop=True,
)

MACHINES: Final[Mapping[str, StateMachine]] = MappingProxyType(
    {
        m.name: m
        for m in (
            CASE_MACHINE,
            COMMITMENT_MACHINE,
            CONFLICT_MACHINE,
            ACTION_MACHINE,
            TRIGGER_MACHINE,
            OUTBOX_MACHINE,
            PROPOSAL_MACHINE,
            EPISTEMIC_MACHINE,
        )
    }
)

#: Short names callers may use instead of a `StateMachine` handle. The kernel
#: reads a machine name out of a `state_transitions` row as a plain string, and
#: `20_TDD_STRATEGY.md` §5.1 calls the case machine `"CASE"`.
_MACHINE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "CASE": "case",
        "COMMITMENT": "commitment",
        "CONFLICT": "conflict",
        "ACTION": "action_intent",
        "ACTION_INTENT": "action_intent",
        "TRIGGER": "prospective_trigger",
        "PROSPECTIVE_TRIGGER": "prospective_trigger",
        "OUTBOX": "outbox_event",
        "OUTBOX_EVENT": "outbox_event",
        "PROPOSAL": "memory_proposal",
        "MEMORY_PROPOSAL": "memory_proposal",
        "EPISTEMIC": "epistemic_status",
        "EPISTEMIC_STATUS": "epistemic_status",
    }
)


def resolve_machine(machine: StateMachine | str) -> StateMachine:
    """Return the :class:`StateMachine` for a handle or a name.

    An unknown *state* is a data error and produces an illegal verdict; an
    unknown *machine* is a programming error and raises, because there is no
    honest verdict to return about a machine that does not exist.
    """
    if isinstance(machine, StateMachine):
        return machine
    key = _MACHINE_ALIASES.get(str(machine).upper())
    if key is None:
        known = ", ".join(sorted(_MACHINE_ALIASES))
        raise ValueError(f"unknown state machine {machine!r}; permitted: {known}")
    return MACHINES[key]


# ---------------------------------------------------------------------------
# The single legality helper
# ---------------------------------------------------------------------------


def legal_transition(
    machine: StateMachine | str,
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
) -> TransitionVerdict:
    """Return the verdict on ``from_state -> to_state`` for ``machine``.

    Accepts enum members or their string values interchangeably, because the
    Kernel reads states back from CockroachDB as plain strings, and accepts a
    machine handle or a machine name for the same reason.

    The verdict is falsy when the change is refused. ``verdict.code`` reports
    the *cell* — ``Y``, ``G1``, ``G2``, ``G`` or ``ILLEGAL`` — which is a
    property of the table; ``verdict.legal`` reports whether *this call*, with
    this reason code, is permitted.
    """
    resolved = resolve_machine(machine)
    frm = str(from_state)
    to = str(to_state)

    def verdict(
        *,
        legal: bool,
        code: str,
        guard: str | None = None,
        allowed: frozenset[str] | None = None,
        detail: str = "",
    ) -> TransitionVerdict:
        return TransitionVerdict(
            machine=resolved.name,
            from_state=frm,
            to_state=to,
            legal=legal,
            code=code,
            guard=guard,
            allowed_reason_codes=allowed,
            reason_code=reason_code,
            detail=detail,
        )

    known = resolved.states
    unknown = [state for state in (frm, to) if state not in known]
    if unknown:
        return verdict(
            legal=False,
            code=ILLEGAL_CODE,
            detail=f"{', '.join(repr(s) for s in unknown)} is not a state of {resolved.name}",
        )

    if frm == to:
        if resolved.allow_self_loop:
            return verdict(legal=True, code=LEGAL_CODE)
        return verdict(
            legal=False,
            code=ILLEGAL_CODE,
            detail="a status that does not change is not a transition",
        )

    if frm in resolved.terminal:
        return verdict(legal=False, code=ILLEGAL_CODE, detail=f"{frm} is terminal")

    if to not in resolved.transitions.get(frm, frozenset()):
        return verdict(legal=False, code=ILLEGAL_CODE, detail="no such edge in the table")

    guard = resolved.guards.get((frm, to))
    if guard is None:
        return verdict(legal=True, code=LEGAL_CODE)

    code = resolved.guard_codes.get((frm, to), GUARD_CODE)
    if reason_code is None or reason_code not in guard:
        allowed = ", ".join(sorted(guard))
        return verdict(
            legal=False,
            code=code,
            guard=code,
            allowed=guard,
            detail=f"guarded transition requires reason_code in {{{allowed}}}",
        )
    return verdict(legal=True, code=code, allowed=guard)


def assert_transition(
    machine: StateMachine | str,
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
) -> None:
    """Raise :class:`IllegalTransitionError` unless the transition is legal."""
    legal_transition(machine, from_state, to_state, reason_code=reason_code).raise_if_illegal()


def reachable_states(machine: StateMachine | str, from_state: str) -> frozenset[str]:
    """Breadth-first closure. Used by tests to prove no state is orphaned."""
    resolved = resolve_machine(machine)
    seen: set[str] = set()
    frontier: list[str] = [str(from_state)]
    while frontier:
        current = frontier.pop()
        for nxt in resolved.transitions.get(current, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return frozenset(seen)


# ---------------------------------------------------------------------------
# The two rules that need more than a reason-code allowlist
# ---------------------------------------------------------------------------

#: The guard that refuses `FULFILLED` while money is still owed. It is a member
#: of the Kernel's closed reason-code catalogue (`12_KERNEL_ALGORITHMS.md` §9.3)
#: so the refusal is reportable without anyone inventing wording for it.
_OUTSTANDING_GUARD: Final[str] = str(KernelReasonCode.INVARIANT_FULFILLED_STATUS_MISMATCH)

#: `16_TRIGGER_DSL.md` §9.6: a wake delivered for a row that is no longer ARMED
#: is a no-op carrying this code, not an error and not a state change.
_NOT_ARMED_GUARD: Final[str] = str(TriggerReasonCode.TRIGGER_NOT_ARMED)


def commitment_transition(
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
    outstanding_amount: Decimal | None = None,
) -> TransitionVerdict:
    """Legality of a commitment status change, including the amount rule.

    ``FULFILLED`` is unreachable while ``outstanding_amount > 0``. The shape of
    the transition may be perfectly legal — ``ACTIVE -> FULFILLED`` is a ``Y``
    cell — and the amount is what refuses it, which is why this cannot be a
    reason-code allowlist in :data:`COMMITMENT_MACHINE`.

    ``outstanding_amount=None`` means "not supplied": the table-level verdict is
    returned unchanged so the Kernel can order its checks (§4.4) rather than
    being forced to compute an amount before it knows the shape is allowed.
    Not knowing the amount is not the same as knowing it is zero, and the
    Kernel's invariant pass at step 16 is what closes that gap.
    """
    verdict = legal_transition(COMMITMENT_MACHINE, from_state, to_state, reason_code=reason_code)
    if not verdict.legal:
        return verdict
    if str(to_state) != str(CommitmentStatus.FULFILLED):
        return verdict
    if outstanding_amount is None or outstanding_amount <= Decimal(0):
        return verdict
    return TransitionVerdict(
        machine=verdict.machine,
        from_state=verdict.from_state,
        to_state=verdict.to_state,
        legal=False,
        code=verdict.code,
        guard=_OUTSTANDING_GUARD,
        allowed_reason_codes=verdict.allowed_reason_codes,
        reason_code=reason_code,
        detail=(
            "FULFILLED is unreachable while outstanding_amount > 0 "
            f"(outstanding_amount={outstanding_amount})"
        ),
    )


def trigger_wake_transition(
    current_state: str,
    result: str,
    *,
    reason_code: str | None = None,
) -> TransitionVerdict:
    """Legality of applying one wake outcome to a trigger row.

    `16_TRIGGER_DSL.md` §9: a wake is delivered at least once, the evaluator
    guards on state, generation, expiry and ``not_before``, and produces exactly
    one ``TriggerResult``. §9.10 fixes which state each result leaves the row
    in: only ``FIRED`` moves the row and touches the case; ``NO_OP`` and
    ``ERROR`` leave it ``ARMED``.

    Raises:
        ValueError: if ``result`` is not a member of ``TriggerResult``. The
            result set is closed; an unknown one is a programming error, not a
            state the evaluator may land in.
    """
    outcome = str(result)
    if outcome not in TRIGGER_RESULT_STATE:
        known = ", ".join(sorted(TRIGGER_RESULT_STATE))
        raise ValueError(f"unknown trigger result {result!r}; permitted: {known}")

    frm = str(current_state)
    target = TRIGGER_RESULT_STATE[outcome] or frm

    if frm != str(TriggerState.ARMED):
        return TransitionVerdict(
            machine=TRIGGER_MACHINE.name,
            from_state=frm,
            to_state=target,
            legal=False,
            code=ILLEGAL_CODE,
            guard=_NOT_ARMED_GUARD,
            reason_code=reason_code,
            detail=f"wake delivered for a trigger in state {frm}, not ARMED",
        )

    if target == frm:
        return TransitionVerdict(
            machine=TRIGGER_MACHINE.name,
            from_state=frm,
            to_state=target,
            legal=True,
            code=NOOP_CODE,
            reason_code=reason_code,
            detail=f"{outcome} leaves the trigger in {frm}; no canonical change",
        )

    return legal_transition(TRIGGER_MACHINE, frm, target, reason_code=reason_code)
