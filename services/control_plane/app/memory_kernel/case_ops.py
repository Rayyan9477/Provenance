"""Case transition legality and the aggregate revision rule — T4.7.

Authority
---------
``specs/12_KERNEL_ALGORITHMS.md`` section 5 (the matrix, guard ``G1``'s five
conjuncts, guard ``G2``, rule ``C1``) and section 6 (rules ``R1``-``R7``).
``CANONICAL_DECISIONS.md`` -> *Hero commit canon* fixes the hero's numbers.

What this module does not do
----------------------------
It does not re-implement the state machine. ``provenance_domain.transitions``
owns ``CASE_MACHINE`` and :func:`legal_transition`, and this module wraps them.
A second copy of the legality table in the Kernel would be a second source of
truth about what a case may do, and the two copies would diverge on the day
someone adds a state to one of them.

The rule this module exists to make true
----------------------------------------
``RESOLVED -> REOPENED`` is the most consequential transition in the product -
it is what "the move that never really ended" means - and it is also the one
most likely to fire spuriously on a marketing email. :func:`qualifies_for_reopen`
is a conjunction of five tests, and ``Q3`` is the one that stops the marketing
email: new evidence that did nothing canonical does not reopen anything.

Stdlib plus ``provenance_domain``. No ``provenance_db``, no ``asyncio``: the
UPDATE lives here as a string and is executed by ``transaction.py``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from provenance_domain import money
from provenance_domain.enums import (
    AttentionLevel,
    CaseReopenReasonCode,
    CaseStatus,
    ConflictSeverity,
    KernelReasonCode,
)
from provenance_domain.invariants import assert_revision_increment
from provenance_domain.transitions import CASE_MACHINE, TransitionVerdict, legal_transition
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig

__all__ = [
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "CASE_UPDATE_SQL",
    "MATERIAL_COMMITMENT_STATUSES_AFTER",
    "MATERIAL_COMMITMENT_STATUSES_BEFORE",
    "MATERIAL_CONFLICT_SEVERITIES",
    "MATERIAL_DISPUTE_CLAIM_KINDS",
    "CaseRow",
    "CaseSnapshot",
    "CaseUpdate",
    "ClaimSignal",
    "CommitmentSignal",
    "ConflictSignal",
    "EvidenceRecord",
    "IllegalCaseTransitionError",
    "MultipleTransitionsError",
    "ReopenBasis",
    "ReopenVerdict",
    "TriggerSignal",
    "case_transition_verdict",
    "guard_code",
    "plan_case_update",
    "qualifies_for_reopen",
    "revision_after",
]


# ---------------------------------------------------------------------------
# Section 5.3 - the vocabulary each conjunct of G1 reads
# ---------------------------------------------------------------------------

#: ``Q3(a)``: a conflict below ``MEDIUM`` is a difference somebody may want to
#: see, not a reason to reopen a settled case.
MATERIAL_CONFLICT_SEVERITIES: Final[frozenset[ConflictSeverity]] = frozenset(
    {ConflictSeverity.MEDIUM, ConflictSeverity.HIGH, ConflictSeverity.CRITICAL}
)

#: ``Q3(b)``: an obligation that had been discharged is live again.
MATERIAL_COMMITMENT_STATUSES_BEFORE: Final[frozenset[str]] = frozenset({"FULFILLED", "EXPIRED"})
MATERIAL_COMMITMENT_STATUSES_AFTER: Final[frozenset[str]] = frozenset(
    {"ACTIVE", "PARTIAL", "DISPUTED"}
)

#: ``Q3(d)``: the person said the belief is wrong.
MATERIAL_DISPUTE_CLAIM_KINDS: Final[frozenset[str]] = frozenset({"USER_CLAIM", "CORRECTION"})


@dataclass(frozen=True, slots=True)
class CaseRow:
    """The ``cases`` columns the transition rules read.

    ``resolved_at`` is present because ``Q2`` depends on it, and it is
    deliberately never cleared by a reopen: when the case was previously
    resolved is a historical fact.
    """

    case_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    status: CaseStatus
    revision: int
    reopened_count: int = 0
    resolved_at: datetime | None = None
    attention_level: AttentionLevel = AttentionLevel.NONE


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One evidence item's **record** time. Valid time is not read here.

    ``Q2`` is a record-time test on purpose (rule ``T4``): late-arriving
    evidence about an old period is exactly the shape that should reopen a
    case, so testing valid time here would refuse the product's headline
    scenario.
    """

    evidence_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """What is already linked to this case, read inside the transaction."""

    evidence_ids_linked_to_case: frozenset[uuid.UUID] = frozenset()
    artifact_hashes_linked_to_case: frozenset[str] = frozenset()
    evidence: Mapping[uuid.UUID, EvidenceRecord] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConflictSignal:
    """One conflict this commit would open, as ``Q3(a)`` reads it."""

    case_id: uuid.UUID
    severity: ConflictSeverity


@dataclass(frozen=True, slots=True)
class CommitmentSignal:
    """One commitment status move, as ``Q3(b)`` reads it."""

    status_before: str
    status_after: str


@dataclass(frozen=True, slots=True)
class TriggerSignal:
    """One trigger state move, as ``Q3(c)`` reads it."""

    new_state: str
    predicate_result: bool | None


@dataclass(frozen=True, slots=True)
class ClaimSignal:
    """One admitted claim, as ``Q3(d)`` reads it."""

    claim_kind: str
    disputes_case_belief: bool = False


@dataclass(frozen=True, slots=True)
class ReopenBasis:
    """Everything this commit would write, reduced to what ``G1`` asks about.

    A dedicated object rather than the whole ``ChangePlan``: ``G1`` is a rule
    about four signals, and passing the plan would let a future edit make the
    reopen decision depend on something nobody reviewed.
    """

    evidence_ids: tuple[uuid.UUID, ...] = ()
    artifact_hashes: tuple[str, ...] = ()
    conflicts: tuple[ConflictSignal, ...] = ()
    commitment_deltas: tuple[CommitmentSignal, ...] = ()
    trigger_deltas: tuple[TriggerSignal, ...] = ()
    claims: tuple[ClaimSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class ReopenVerdict:
    """Whether ``G1`` passed, and which conjunct refused when it did not."""

    qualifies: bool
    reason_code: KernelReasonCode
    attention_level: AttentionLevel = AttentionLevel.NONE
    test_failed: str | None = None


@dataclass(frozen=True, slots=True)
class CaseUpdate:
    """The single ``cases`` UPDATE this commit will issue, fully decided."""

    case_id: uuid.UUID
    status_before: CaseStatus
    status_after: CaseStatus
    revision_before: int
    revision_after: int
    reopen_delta: int = 0
    attention_before: AttentionLevel = AttentionLevel.NONE
    attention_after: AttentionLevel = AttentionLevel.NONE
    resolved_at: datetime | None = None
    reason_code: str = ""
    changed: bool = False
    reason_codes: tuple[KernelReasonCode, ...] = ()

    @property
    def status_moves(self) -> bool:
        """True when a ``state_transitions`` row of type ``CASE_STATUS`` is due.

        ``ck_state_transitions_moves`` refuses a row whose ``from_state`` equals
        its ``to_state``, so this is the schema's rule expressed in Python
        before the schema has to raise.
        """
        return self.status_after is not self.status_before


class MultipleTransitionsError(ValueError):
    """Rule ``C1``: at most one case status transition per commit.

    Urgency a second hop would have conveyed is carried by
    ``cases.attention_level``, which is not a status and needs no transition.
    """

    code: Final[KernelReasonCode] = KernelReasonCode.CASE_TRANSITION_MULTIPLE_IN_COMMIT


class IllegalCaseTransitionError(ValueError):
    """The requested cell is ``-`` in section 5.1, or its guard did not pass."""

    code: Final[KernelReasonCode] = KernelReasonCode.CASE_TRANSITION_ILLEGAL


# ---------------------------------------------------------------------------
# Section 5.1 - the matrix, wrapped
# ---------------------------------------------------------------------------


def case_transition_verdict(
    frm: CaseStatus | str,
    to: CaseStatus | str,
    *,
    reason_code: str | None = None,
) -> TransitionVerdict:
    """``provenance_domain.transitions.legal_transition`` over ``CASE_MACHINE``."""
    return legal_transition(CASE_MACHINE, str(frm), str(to), reason_code=reason_code)


def guard_code(frm: CaseStatus | str, to: CaseStatus | str) -> str | None:
    """``"G1"``, ``"G2"`` or ``None`` for the cell, ignoring this call's reason
    code. The cell is a property of the table; legality is a property of the
    call."""
    return CASE_MACHINE.guard_codes.get((str(frm), str(to)))


# ---------------------------------------------------------------------------
# Section 5.3 - guard G1
# ---------------------------------------------------------------------------


def qualifies_for_reopen(
    case: CaseRow,
    basis: ReopenBasis,
    snapshot: CaseSnapshot,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> ReopenVerdict:
    """The five conjuncts of section 5.3, evaluated in order.

    Order matters for the reported code: ``Q1`` before ``Q2`` means a
    re-imported artifact is reported as "nothing new" rather than as "nothing
    recent", which is the more accurate of the two diagnoses.
    """
    # Q1 - at least one evidence item never before linked to this case.
    new_evidence = tuple(
        e for e in basis.evidence_ids if e not in snapshot.evidence_ids_linked_to_case
    )
    if not new_evidence:
        return _refused("Q1", KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING)

    # Q2 - record-time freshness. Valid time may be old (rule T4) and in the
    # hero it is; re-importing an artifact we already had must never reopen.
    if case.resolved_at is None:
        return _refused("Q2", KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING)
    resolved_at = case.resolved_at
    fresh = any(
        snapshot.evidence[e].created_at > resolved_at
        for e in new_evidence
        if e in snapshot.evidence
    )
    if not fresh:
        return _refused("Q2", KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING)

    # Q3 - the new evidence must have DONE something canonical.
    if not _is_material(case, basis):
        return _refused("Q3", KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING)

    # Q4 - artifact-level dedupe, defence in depth over pipeline step 6.
    if any(h in snapshot.artifact_hashes_linked_to_case for h in basis.artifact_hashes):
        return _refused("Q4", KernelReasonCode.ARTIFACT_CONTENT_DUPLICATE)

    # Q5 - the flapping guard. A case that has reopened five times needs a
    # person, not a sixth automatic reopen, so this refusal raises attention.
    if case.reopened_count >= cfg.max_reopens:
        return ReopenVerdict(
            qualifies=False,
            reason_code=KernelReasonCode.CASE_REOPEN_LIMIT_REACHED,
            attention_level=AttentionLevel.ATTENTION,
            test_failed="Q5",
        )

    return ReopenVerdict(
        qualifies=True,
        reason_code=KernelReasonCode.CASE_REOPENED_QUALIFYING_EVIDENCE,
        attention_level=AttentionLevel.URGENT,
    )


def _refused(test: str, code: KernelReasonCode) -> ReopenVerdict:
    return ReopenVerdict(qualifies=False, reason_code=code, test_failed=test)


def _is_material(case: CaseRow, basis: ReopenBasis) -> bool:
    """``Q3``'s four branches. Any one of them is enough; none of them is
    "the model thought this was important"."""
    if any(
        c.case_id == case.case_id and c.severity in MATERIAL_CONFLICT_SEVERITIES
        for c in basis.conflicts
    ):
        return True
    if any(
        d.status_before in MATERIAL_COMMITMENT_STATUSES_BEFORE
        and d.status_after in MATERIAL_COMMITMENT_STATUSES_AFTER
        for d in basis.commitment_deltas
    ):
        return True
    if any(t.new_state == "FIRED" and t.predicate_result is True for t in basis.trigger_deltas):
        return True
    return any(
        c.claim_kind in MATERIAL_DISPUTE_CLAIM_KINDS and c.disputes_case_belief
        for c in basis.claims
    )


# ---------------------------------------------------------------------------
# Section 6 - the revision rule
# ---------------------------------------------------------------------------


def revision_after(before: int, *, changed: bool) -> int:
    """Rules ``R1`` and ``R2``: exactly one increment, or none at all.

    The arithmetic is checked against ``provenance_domain.invariants`` rather
    than merely performed, so a future edit here cannot quietly disagree with
    the invariant every other layer asserts.
    """
    after = before + 1 if changed else before
    assert_revision_increment(before, after, changed=changed)
    return after


#: Rule ``R4``. The ``revision`` predicate is redundant under ``SERIALIZABLE``
#: and is required anyway: it turns a subtle isolation regression into zero rows
#: updated, which the executor raises as ``OPTIMISTIC_REVISION_MISMATCH``
#: instead of writing a lost update. Defence in depth costs one ``AND`` clause.
#:
#: ``resolved_at`` is passed through rather than cleared, because a reopen must
#: not erase when the case was previously resolved - ``Q2`` reads it.
CASE_UPDATE_SQL: Final[str] = """
UPDATE cases
   SET status           = %(status_after)s,
       revision         = revision + 1,
       reopened_count   = reopened_count + %(reopen_delta)s,
       attention_level  = %(attention_after)s,
       last_activity_at = %(tx_now)s,
       resolved_at      = %(resolved_at)s,
       updated_at       = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s
   AND user_id   = %(user_id)s
   AND id        = %(case_id)s
   AND revision  = %(revision_before)s
"""


def plan_case_update(
    case: CaseRow,
    *,
    requested: Sequence[CaseStatus],
    reason_code: str | None = None,
    basis: ReopenBasis | None = None,
    snapshot: CaseSnapshot | None = None,
    changed: bool = False,
    attention: AttentionLevel | None = None,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> CaseUpdate:
    """Decide the one ``cases`` UPDATE this commit will issue.

    *changed* is the caller's answer to "did anything canonical happen?", from
    section 6.2's table. It governs the revision independently of the status:
    a commit that admits a claim but withholds the reopen still increments,
    and a trigger evaluation that found its predicate ``FALSE`` does not.
    """
    targets = [CaseStatus(t) for t in requested]
    if len(targets) > 1:
        raise MultipleTransitionsError(
            f"{len(targets)} case transitions requested in one commit "
            f"({', '.join(str(t) for t in targets)}); rule C1 permits one. "
            "Urgency a second hop would carry belongs in attention_level."
        )

    codes: list[KernelReasonCode] = []
    status_after = case.status
    reopen_delta = 0
    attention_after = attention if attention is not None else case.attention_level
    applied_reason = ""

    if targets:
        target = targets[0]
        if target is CaseStatus.REOPENED and case.status is CaseStatus.RESOLVED:
            verdict = qualifies_for_reopen(
                case, basis or ReopenBasis(), snapshot or CaseSnapshot(), cfg
            )
            codes.append(verdict.reason_code)
            if verdict.qualifies:
                _assert_legal(case.status, target, reason_code)
                status_after = target
                reopen_delta = 1
                applied_reason = reason_code or str(CaseReopenReasonCode.CONTRADICTORY_EVIDENCE)
                attention_after = verdict.attention_level
            elif verdict.attention_level is not AttentionLevel.NONE:
                # Q5: the evidence is still written; only the transition is
                # withheld, and the case is escalated to a person.
                attention_after = verdict.attention_level
        else:
            _assert_legal(case.status, target, reason_code)
            status_after = target
            applied_reason = reason_code or ""

    return CaseUpdate(
        case_id=case.case_id,
        status_before=case.status,
        status_after=status_after,
        revision_before=case.revision,
        revision_after=revision_after(case.revision, changed=changed),
        reopen_delta=reopen_delta,
        attention_before=case.attention_level,
        attention_after=attention_after,
        resolved_at=case.resolved_at,
        reason_code=applied_reason,
        changed=changed,
        reason_codes=tuple(codes),
    )


def _assert_legal(frm: CaseStatus, to: CaseStatus, reason_code: str | None) -> None:
    verdict = case_transition_verdict(frm, to, reason_code=reason_code)
    if not verdict:
        raise IllegalCaseTransitionError(
            f"{frm} -> {to} is refused: {verdict.detail or 'no such edge in section 5.1'}"
        )


# ---------------------------------------------------------------------------
# The PV_SABOTAGE hooks — T4.13
#
# `quality/23_PHASE_GATES.md` section 23.5 fixes the semantics and
# `tests/sabotage_matrix.yaml` carries the entries. The mechanism is
# `provenance_domain.money.install_sabotage`, reused rather than reimplemented:
# a second copy of the neutering logic is a second thing that can quietly stop
# neutering.
#
# WHY THESE TWO SYMBOLS, AND WHY THEY ARE REACHABLE
# --------------------------------------------------
# `install_sabotage` rebinds the name in THIS module's `globals()`. Both symbols
# are called from `plan_case_update` as BARE NAMES in the same module, so the
# lookup happens in these globals at call time and the rebind is visible. A
# `from`-import at a call site in another module would copy the reference before
# the rebind and the sabotage would silently never arrive -- which reports a
# green sabotage run, and section 23 counts that as a failure, not a relief.
#
# `revision_after` is rule R1 in one line: exactly one increment per canonical
# commit, none on a no-op. Neutered to the identity it returns `before`, which
# type-checks as an `int`, looks entirely plausible, and means the aggregate
# never advances -- so `cases.revision` stops moving, `state_transitions` and
# `outbox_events` carry a stale version, and `ck_kernel_decisions_revision_step`
# is satisfied by an accepting decision that changed nothing.
#
# `qualifies_for_reopen` is gate G1: it is the reason a marketing email does not
# reopen a settled case. Neutered, the reopen guard stops being consulted and
# the case machine will move RESOLVED -> REOPENED on evidence that was never
# material.
# ---------------------------------------------------------------------------

#: `23_PHASE_GATES.md` addresses kernel symbols as `memory_kernel.<module>.<name>`
#: rather than by their full dotted import path, so the label is explicit.
SABOTAGE_MODULE: Final[str] = "memory_kernel.case_ops"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("revision_after", "qualifies_for_reopen")

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
