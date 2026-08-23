"""The four canon invariants, the grounding invariant, and nothing else.

Authority
---------
- ``00_PRODUCT.md`` section 0.1, the four invariants in their original wording:

  1. **Evidence is append-only.** Admitted evidence is never rewritten or
     deleted; corrections arrive as new evidence.
  2. **Beliefs are revisable.** A changed conclusion creates a new belief
     version and preserves the prior version and the reason it was superseded.
  3. **State is transactional.** No case, commitment, or conflict may be left
     in an impossible partial aggregate state.
  4. **Actions are permissioned.** No uncommitted proposal and no agent
     scratchpad may produce an external side effect.

- ``00_PRODUCT.md`` section 0.2, the grounding invariant: a canonical belief
  version must be GROUNDED - at least one ``belief_support`` edge - unless it
  is an explicitly declared deterministic derivation, in which case it carries
  a ``source_kind = 'DERIVATION'`` edge instead.
- ``specs/11_CONTRACTS.md`` section 5.1 owns this module's money half:
  :func:`derive_outstanding`, :func:`derive_commitment_status`,
  :func:`assert_commitment_consistent`, :func:`assert_revision_increment`, the
  scale rules and the three configuration thresholds.
- ``specs/10_DATABASE_DDL.md`` sections 5.4 and 5.6 own the append-only shape
  of ``evidence_items``: rows are never deleted, ``normalized_text``,
  ``exact_text``, ``source_locator`` and ``embedding`` are never overwritten,
  and retraction is a one-way status transition that must record when and why.
- ``specs/11_CONTRACTS.md`` sections 12 and 14 own lineage: a version above 1
  must name the version it supersedes, and a supersession without a reason
  code is unauditable.
- ``quality/23_PHASE_GATES.md`` section 23.15: every invariant names a test.
  ``packages/python/provenance_domain/INVARIANTS.md`` is that map and
  ``tools/invariant_map_check.py`` is what refuses to let it rot.

Pure invariant functions. No I/O, no Pydantic, no ``provenance_db``, no
``boto3``, no ``httpx``, no ``asyncio``. Safe to call inside a serializable
transaction callback and safe to call again on a 40001 retry.

Why this module imports *modules* and not symbols
-------------------------------------------------
``from provenance_domain import money`` then ``money.outstanding(...)`` - never
``from provenance_domain.money import outstanding``.

``PV_SABOTAGE`` rebinds the named symbol **on the module object** at import
(``money.SABOTAGED_SYMBOLS``). A ``from``-import copies the reference into this
namespace before the rebind is visible, so the sabotage would silently fail to
reach any test in ``tests/test_invariants.py``. ``G1.7`` would then report a
green sabotage run - and a green run on a sabotage assertion is a **gate
failure**, not a pass (``quality/23_PHASE_GATES.md`` section 23). The
invariant-3 mapping in ``INVARIANTS.md`` would point at a test that cannot fail
for the reason it claims to cover. The same reasoning applies to
:mod:`provenance_domain.transitions`: this module never holds a second copy of
the state tables, it reaches T1.2's through the module object.

What this module deliberately does not do
-----------------------------------------
It does not decide *dispositions*. :func:`assert_action_permissioned` says an
execution is not permitted; it does not choose a remedy.
:func:`grounding_verdict` says a belief version is not grounded; it does not
invent an edge. The Kernel turns each refusal into a ``KernelDecision`` with a
reason code from the closed catalogue, and that mapping is
``12_KERNEL_ALGORITHMS.md``'s, not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from provenance_domain import derivations, money, transitions
from provenance_domain.enums import (
    ActionState,
    CommitmentStatus,
    EpistemicStatus,
    RetractionStatus,
    SupportSourceKind,
)

__all__ = [
    "ActionExecution",
    "AppendOnlyVerdict",
    "BeliefRevision",
    "CommitmentAmounts",
    "CurrencyMismatchError",
    "EVIDENCE_MUTABLE_FIELDS",
    "GROUNDING_ROUTE_DERIVATION",
    "GROUNDING_ROUTE_EVIDENCE",
    "GROUNDING_ROUTE_NONE",
    "GROUNDING_ROUTE_RETRACTED",
    "GroundingVerdict",
    "HUMAN_REVIEW_CONFIDENCE_FLOOR",
    "IDENTITY_MARGIN_THRESHOLD",
    "IDENTITY_STRONG_THRESHOLD",
    "InvariantViolation",
    "MONEY_EXPONENT",
    "RETRACTION_REASON_CODES",
    "RevisionVerdict",
    "assert_action_permissioned",
    "assert_belief_revisable",
    "assert_commitment_consistent",
    "assert_evidence_append_only",
    "assert_grounded",
    "assert_money_scale",
    "assert_revision_increment",
    "assert_transition_legal",
    "belief_revision_verdict",
    "derive_commitment_status",
    "derive_outstanding",
    "evidence_change_is_append_only",
    "grounding_verdict",
    "quantise_money",
]

#: Matches ``DECIMAL(20,4)`` exactly. Restated here rather than imported from
#: :mod:`provenance_domain.money` because ``11_CONTRACTS.md`` section 5.1 lists
#: it in *this* module's ``__all__``; the two are asserted equal in
#: ``tests/test_invariants.py``.
MONEY_EXPONENT: Final[Decimal] = Decimal("0.0001")

# Thresholds are configuration constants, never prompt text
# (03_AGENTS_LANGGRAPH_CONTRACTS.md section 5.7). This module is their single
# home: `11_CONTRACTS.md` section 10 imports the floor from here.
HUMAN_REVIEW_CONFIDENCE_FLOOR: Final[Decimal] = Decimal("0.70")
IDENTITY_STRONG_THRESHOLD: Final[Decimal] = Decimal("0.90")
IDENTITY_MARGIN_THRESHOLD: Final[Decimal] = Decimal("0.15")


class InvariantViolation(ValueError):  # noqa: N818 - spec-mandated name
    # ruff N818 wants an `Error` suffix. The name is fixed by
    # `11_CONTRACTS.md` §5 line 1617, where `CurrencyMismatchError` is declared
    # as a SUBCLASS of it; renaming here would make the spec's own inheritance
    # line false and would be exactly the spec-versus-code drift `L-DRIFT`
    # exists to catch. Suppressed narrowly, on this class only.
    """A hard domain rule was broken.

    Maps to ``KernelDecision.REJECTED_INVARIANT``. The ``code`` is stable and
    machine-readable so the Kernel never parses an English message to decide
    what happened.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class CurrencyMismatchError(InvariantViolation):
    """Arithmetic was attempted across two currencies."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(
            "CURRENCY_MISMATCH",
            f"refusing arithmetic across {left} and {right} "
            "without an explicit conversion event",
        )


# ---------------------------------------------------------------------------
# Money scale - 11_CONTRACTS.md section 5.1
# ---------------------------------------------------------------------------


def assert_money_scale(amount: Decimal) -> None:
    """Refuse anything ``DECIMAL(20,4)`` cannot hold exactly.

    Rounding here would silently repair a caller that lost precision upstream,
    which is the one thing a money boundary must never do.
    """
    if not amount.is_finite():
        raise InvariantViolation("MONEY_NOT_FINITE", f"{amount!r} is not a finite decimal")
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - unreachable once finite
        raise InvariantViolation("MONEY_NOT_FINITE", f"{amount!r} is not a finite decimal")
    if -exponent > 4:
        raise InvariantViolation(
            "MONEY_SCALE",
            f"{amount} exceeds DECIMAL(20,4); round at the source, never here",
        )


def quantise_money(amount: Decimal) -> Decimal:
    """Normalise scale without changing value."""
    assert_money_scale(amount)
    return amount.quantize(MONEY_EXPONENT)


@dataclass(frozen=True, slots=True)
class CommitmentAmounts:
    """The three amounts of a commitment, in one currency, derived together."""

    currency: str
    committed: Decimal
    fulfilled: Decimal
    outstanding: Decimal


def derive_outstanding(
    *,
    currency: str,
    committed: Decimal,
    fulfilled: Decimal,
    fulfilment_currency: str,
) -> CommitmentAmounts:
    """``outstanding = committed - fulfilled``. Deterministic and currency-strict.

    Never clamps. Over-fulfilment yields a negative outstanding, which the
    Kernel turns into a ``FULFILLMENT_CONFLICT`` instead of silently absorbing
    (``12_KERNEL_ALGORITHMS.md`` section 4.3).

    The subtraction itself is **not** performed here. It is
    :func:`provenance_domain.money.outstanding`, reached through the ``money``
    module global, which is the symbol ``PV_SABOTAGE`` neuters at ``G1.7``.
    Inlining ``committed - fulfilled`` here, or importing the function by name,
    would put the identity in two places and make the sabotage invisible to
    ``tests/test_invariants.py``.

    Raises:
        CurrencyMismatchError: the two currencies differ.
        InvariantViolation: either amount is outside ``DECIMAL(20,4)``, the
            committed amount is negative (``NEGATIVE_COMMITMENT``), or the
            fulfilment is negative (``NEGATIVE_FULFILMENT``).
    """
    if currency != fulfilment_currency:
        raise CurrencyMismatchError(currency, fulfilment_currency)
    assert_money_scale(committed)
    assert_money_scale(fulfilled)
    try:
        derived = money.outstanding(committed, fulfilled)
    except money.MoneyError as exc:
        # `money` refuses with its own exception family and a stable code; the
        # Kernel consumes `InvariantViolation`. Re-raising preserves the codes
        # section 5.1 prints (`NEGATIVE_COMMITMENT`, `NEGATIVE_FULFILMENT`)
        # while keeping exactly one implementation of each rule.
        raise InvariantViolation(exc.code, exc.detail) from exc
    return CommitmentAmounts(
        currency=currency,
        committed=quantise_money(committed),
        fulfilled=quantise_money(fulfilled),
        outstanding=quantise_money(derived),
    )


def derive_commitment_status(
    amounts: CommitmentAmounts,
    *,
    current: CommitmentStatus,
    has_blocking_conflict: bool,
) -> CommitmentStatus:
    """The only place commitment status is computed from money."""
    if amounts.outstanding < 0 or has_blocking_conflict:
        return CommitmentStatus.DISPUTED
    if amounts.committed > 0 and amounts.outstanding == 0:
        return CommitmentStatus.FULFILLED
    if amounts.fulfilled > 0:
        return CommitmentStatus.PARTIAL
    if current is CommitmentStatus.PROPOSED:
        return CommitmentStatus.PROPOSED
    return CommitmentStatus.ACTIVE


def assert_commitment_consistent(amounts: CommitmentAmounts, status: CommitmentStatus) -> None:
    """Hero scenario: USD 420 owed, USD 200 paid, USD 220 outstanding, PARTIAL.

    Invariant 3 in its aggregate form. The subtraction below is a *check* on an
    already-derived triple, not a second derivation: it is what catches an
    aggregate assembled from three separately-written numbers.
    """
    if amounts.outstanding != amounts.committed - amounts.fulfilled:
        raise InvariantViolation(
            "OUTSTANDING_NOT_DERIVED",
            f"{amounts.outstanding} != {amounts.committed} - {amounts.fulfilled}",
        )
    if status is CommitmentStatus.FULFILLED and amounts.outstanding > 0:
        raise InvariantViolation("FULFILLED_WITH_OUTSTANDING", f"outstanding={amounts.outstanding}")
    if status is CommitmentStatus.PARTIAL and amounts.fulfilled <= 0:
        raise InvariantViolation("PARTIAL_WITHOUT_FULFILMENT", f"fulfilled={amounts.fulfilled}")


def assert_revision_increment(before: int, after: int, *, changed: bool) -> None:
    """Invariant 3, as the case revision counter.

    One canonical commit moves a case revision by exactly one; a no-op does not
    move it at all. A no-op that increments invalidates every approval bound to
    the old number, and a real change that does not increment lets a stale
    approval execute.
    """
    expected = before + 1 if changed else before
    if after != expected:
        raise InvariantViolation(
            "REVISION_INCREMENT",
            f"expected case revision {expected}, got {after} (changed={changed})",
        )


# ---------------------------------------------------------------------------
# Invariant 1 - evidence is append-only
# ---------------------------------------------------------------------------

#: The retraction status block of ``10_DATABASE_DDL.md`` section 5.6, and the
#: only columns of an admitted ``evidence_items`` row that may ever change.
#: Everything else - ``normalized_text``, ``exact_text``, ``source_locator``,
#: ``embedding``, the confidences - is frozen at admission.
EVIDENCE_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "retraction_status",
        "retracted_at",
        "retracted_by_evidence_id",
        "retraction_reason_code",
        "is_retrieval_eligible",
    }
)

#: ``ck_evidence_retraction_reason``. Declared here rather than in
#: :mod:`provenance_domain.enums` because ``11_CONTRACTS.md`` section 3 defines
#: no enum for it; the DDL's ``CHECK`` list is the only closed statement of the
#: set, and this is the domain-side copy of it.
RETRACTION_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "USER_CORRECTION",
        "EXTRACTION_ERROR",
        "SOURCE_WITHDRAWN",
        "DUPLICATE_OF_OTHER",
        "PARSER_DEFECT",
        "ADVERSARIAL_CONTENT",
    }
)

#: Every value ``ck_evidence_retraction_status`` permits.
_RETRACTION_STATUSES: Final[frozenset[str]] = frozenset(str(s) for s in RetractionStatus)

#: The status an admitted row starts in, and the only one it may leave.
_ACTIVE: Final[str] = str(RetractionStatus.ACTIVE)


@dataclass(frozen=True, slots=True)
class AppendOnlyVerdict:
    """Whether one before/after pair of evidence rows was an append-only change.

    Falsy when it was not, so ``if not evidence_change_is_append_only(a, b)``
    reads as it would with a bare bool while still carrying the code and the
    fields that moved.
    """

    append_only: bool
    code: str | None = None
    changed_fields: tuple[str, ...] = ()
    detail: str = ""

    def __bool__(self) -> bool:
        return self.append_only


def _refused(code: str, detail: str, changed: tuple[str, ...] = ()) -> AppendOnlyVerdict:
    return AppendOnlyVerdict(append_only=False, code=code, changed_fields=changed, detail=detail)


def _retraction_block_verdict(after: Mapping[str, object]) -> AppendOnlyVerdict | None:
    """``ck_evidence_retraction_consistent`` and its two neighbours, or ``None``."""
    status = str(after.get("retraction_status", _ACTIVE))
    if status not in _RETRACTION_STATUSES:
        permitted = ", ".join(sorted(_RETRACTION_STATUSES))
        return _refused(
            "EVIDENCE_RETRACTION_STATUS_UNKNOWN",
            f"retraction_status={status!r} is not one of {{{permitted}}}",
        )

    retracted_at = after.get("retracted_at")
    reason = after.get("retraction_reason_code")
    retracted_by = after.get("retracted_by_evidence_id")

    if status == _ACTIVE:
        if retracted_at is not None or reason is not None or retracted_by is not None:
            return _refused(
                "EVIDENCE_RETRACTION_UNEXPLAINED",
                "an ACTIVE row must not carry a retraction timestamp, reason or pointer",
            )
    else:
        if retracted_at is None or reason is None:
            return _refused(
                "EVIDENCE_RETRACTION_UNEXPLAINED",
                f"retraction_status={status} requires retracted_at and a reason code; "
                "evidence that vanished from retrieval without saying when or why is "
                "indistinguishable from evidence that was deleted",
            )
        if str(reason) not in RETRACTION_REASON_CODES:
            permitted = ", ".join(sorted(RETRACTION_REASON_CODES))
            return _refused(
                "EVIDENCE_RETRACTION_REASON_UNKNOWN",
                f"retraction_reason_code={reason!r} is not one of {{{permitted}}}",
            )

    if retracted_by is not None and retracted_by == after.get("id"):
        return _refused(
            "EVIDENCE_SELF_RETRACTION",
            "ck_evidence_no_self_retract: a row may not be its own correction",
        )

    if "is_retrieval_eligible" in after and bool(after["is_retrieval_eligible"]) != (
        status == _ACTIVE
    ):
        return _refused(
            "EVIDENCE_RETRIEVAL_FLAG_DESYNC",
            "is_retrieval_eligible is a STORED column computed from retraction_status; "
            "a disagreement means retracted vectors keep ranking in the ANN index",
        )
    return None


def evidence_change_is_append_only(
    before: Mapping[str, object],
    after: Mapping[str, object] | None,
) -> AppendOnlyVerdict:
    """Invariant 1. Was the change from *before* to *after* an append?

    An admitted evidence row may change in exactly one way: the retraction
    status block of ``10_DATABASE_DDL.md`` section 5.6 moves once, from
    ``ACTIVE`` to a non-``ACTIVE`` status, carrying a timestamp, a reason code
    from the closed set, and a pointer to the evidence that superseded it.
    Nothing else may move, no column may appear or vanish, the row keeps its
    id, and there is no route back to ``ACTIVE``.

    *after* may be ``None`` to express deletion, which is refused: a deleted
    row is not a changed row, it is the absence of the record, and the whole
    point of invariant 1 is that the record survives being inconvenient.
    """
    if after is None:
        return _refused(
            "EVIDENCE_DELETED",
            "admitted evidence is never deleted; a correction is new evidence plus a "
            "retraction of the old row",
        )

    if before.get("id") != after.get("id"):
        return _refused(
            "EVIDENCE_IDENTITY_CHANGED",
            f"{before.get('id')!r} and {after.get('id')!r} are different rows",
        )

    dropped = tuple(sorted(set(before) - set(after)))
    if dropped:
        return _refused(
            "EVIDENCE_FIELD_DROPPED",
            f"columns removed from an admitted row: {', '.join(dropped)}",
            dropped,
        )

    added = tuple(sorted(set(after) - set(before)))
    illegal_additions = tuple(f for f in added if f not in EVIDENCE_MUTABLE_FIELDS)
    if illegal_additions:
        return _refused(
            "EVIDENCE_FIELD_ADDED",
            f"columns appeared on an admitted row: {', '.join(illegal_additions)}",
            illegal_additions,
        )

    changed = tuple(
        sorted(field for field in before if field in after and before[field] != after[field])
    )
    rewritten = tuple(f for f in changed if f not in EVIDENCE_MUTABLE_FIELDS)
    if rewritten:
        return _refused(
            "EVIDENCE_FIELD_REWRITTEN",
            f"immutable columns rewritten: {', '.join(rewritten)}. Corrections arrive as "
            "new evidence; they never overwrite the observation that was admitted",
            rewritten,
        )

    before_status = str(before.get("retraction_status", _ACTIVE))
    after_status = str(after.get("retraction_status", _ACTIVE))
    if before_status != after_status and before_status != _ACTIVE:
        return _refused(
            "EVIDENCE_UNRETRACTED",
            f"retraction is one-way: {before_status} -> {after_status} would rewrite a "
            "settled disposition. Re-admitting evidence means admitting new evidence",
            changed,
        )

    inconsistent = _retraction_block_verdict(after)
    if inconsistent is not None:
        return AppendOnlyVerdict(
            append_only=False,
            code=inconsistent.code,
            changed_fields=changed,
            detail=inconsistent.detail,
        )

    return AppendOnlyVerdict(append_only=True, changed_fields=changed)


def assert_evidence_append_only(
    before: Mapping[str, object],
    after: Mapping[str, object] | None,
) -> None:
    """Raise :class:`InvariantViolation` unless the change was append-only."""
    verdict = evidence_change_is_append_only(before, after)
    if not verdict.append_only:
        raise InvariantViolation(verdict.code or "EVIDENCE_NOT_APPEND_ONLY", verdict.detail)


# ---------------------------------------------------------------------------
# Invariant 2 - beliefs are revisable
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BeliefRevision:
    """One step of a ``belief_versions`` chain, as a before/after pair.

    ``previous_version_no`` is ``0`` for the creation of ``v1``, which is the
    only version that legitimately has no predecessor.
    """

    belief_id: str
    previous_version_id: str | None
    previous_version_no: int
    new_version_id: str
    new_version_no: int
    supersedes_version_id: str | None
    supersession_reason_code: str | None


@dataclass(frozen=True, slots=True)
class RevisionVerdict:
    """Whether a proposed new belief version preserves its lineage."""

    revisable: bool
    code: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.revisable


def belief_revision_verdict(revision: BeliefRevision) -> RevisionVerdict:
    """Invariant 2. A changed conclusion creates a *new* version, explained.

    The prior version is preserved and the reason it was superseded is
    recorded. Lineage without reasons is a changelog; lineage with reason codes
    is an argument, which is what the user needs when a counterparty disputes
    it (``11_CONTRACTS.md`` section 14).
    """
    if revision.new_version_no < 1:
        return RevisionVerdict(
            revisable=False,
            code="LINEAGE_VERSION_NO_INVALID",
            detail=f"version_no must be >= 1, got {revision.new_version_no}",
        )

    if revision.new_version_id == revision.previous_version_id:
        return RevisionVerdict(
            revisable=False,
            code="BELIEF_VERSION_OVERWRITTEN",
            detail=(
                "a revision that reuses the previous version id is an edit in place; "
                "the prior version must survive with its own grounding"
            ),
        )

    if revision.new_version_no != revision.previous_version_no + 1:
        return RevisionVerdict(
            revisable=False,
            code="LINEAGE_GAP",
            detail=(
                f"version {revision.previous_version_no} is followed by "
                f"{revision.new_version_no}; lineage may not have a gap"
            ),
        )

    if revision.new_version_no == 1:
        if revision.previous_version_id is not None or revision.supersedes_version_id is not None:
            return RevisionVerdict(
                revisable=False,
                code="LINEAGE_ROOT_HAS_PREDECESSOR",
                detail="v1 is a creation and cannot supersede anything",
            )
        return RevisionVerdict(revisable=True)

    if revision.supersedes_version_id is None:
        return RevisionVerdict(
            revisable=False,
            code="LINEAGE_PREDECESSOR_MISSING",
            detail=(
                f"version {revision.new_version_no} must name the version it supersedes; "
                "an unattached version is a conclusion with no history"
            ),
        )

    if (
        revision.previous_version_id is None
        or revision.supersedes_version_id != revision.previous_version_id
    ):
        return RevisionVerdict(
            revisable=False,
            code="LINEAGE_PREDECESSOR_MISMATCH",
            detail=(
                f"supersedes {revision.supersedes_version_id!r} but the version it follows "
                f"is {revision.previous_version_id!r}"
            ),
        )

    if not (revision.supersession_reason_code or "").strip():
        return RevisionVerdict(
            revisable=False,
            code="SUPERSESSION_UNEXPLAINED",
            detail=(
                f"version {revision.previous_version_no} was superseded without a reason "
                "code; lineage without reasons is a changelog"
            ),
        )

    return RevisionVerdict(revisable=True)


def assert_belief_revisable(revision: BeliefRevision) -> None:
    """Raise :class:`InvariantViolation` unless the revision preserves lineage."""
    verdict = belief_revision_verdict(revision)
    if not verdict.revisable:
        raise InvariantViolation(verdict.code or "BELIEF_NOT_REVISABLE", verdict.detail)


# ---------------------------------------------------------------------------
# Transition legality - delegated to T1.2, never re-implemented
# ---------------------------------------------------------------------------


def assert_transition_legal(
    machine: transitions.StateMachine | str,
    from_state: str,
    to_state: str,
    *,
    reason_code: str | None = None,
) -> transitions.TransitionVerdict:
    """Return T1.2's verdict, or raise :class:`InvariantViolation`.

    The tables live in :mod:`provenance_domain.transitions` and are reached
    through the module object. A second copy of the 10x10 case grid in this
    file would be a grid that can drift with nothing to notice.
    """
    verdict = transitions.legal_transition(machine, from_state, to_state, reason_code=reason_code)
    if not verdict.legal:
        raise InvariantViolation(
            "TRANSITION_ILLEGAL",
            f"{verdict.machine}: {verdict.from_state} -> {verdict.to_state} "
            f"(code={verdict.code}, reason_code={reason_code!r}) {verdict.detail}",
        )
    return verdict


# ---------------------------------------------------------------------------
# Invariant 4 - actions are permissioned
# ---------------------------------------------------------------------------

#: The one ``ActionState`` in which bytes leave the building.
_EXECUTING: Final[str] = str(ActionState.EXECUTING)


@dataclass(frozen=True, slots=True)
class ActionExecution:
    """A proposed move of an ``action_intents`` row, with its permissions.

    ``approval_draft_sha256`` and ``basis_case_revision`` are what the user
    approved; ``draft_sha256`` and ``current_case_revision`` are what is true
    now. An approval that no longer binds to both is stale, and executing a
    stale approval is the failure invariant 4 exists to stop.
    """

    from_state: str
    to_state: str
    reason_code: str | None = None
    proposal_committed: bool = False
    from_agent_scratchpad: bool = False
    approval_draft_sha256: str | None = None
    draft_sha256: str | None = None
    basis_case_revision: int | None = None
    current_case_revision: int | None = None


def assert_action_permissioned(action: ActionExecution) -> None:
    """Invariant 4. No uncommitted proposal and no agent scratchpad may act.

    Order matters. Provenance is checked before legality, and legality before
    the approval binding, so a refusal names the *first* reason rather than the
    most convenient one.
    """
    if not action.proposal_committed:
        raise InvariantViolation(
            "ACTION_UNCOMMITTED_PROPOSAL",
            "an action whose proposal has not been committed by the Kernel has no "
            "canonical basis; nothing uncommitted may produce an external side effect",
        )
    if action.from_agent_scratchpad:
        raise InvariantViolation(
            "ACTION_FROM_SCRATCHPAD",
            "agent scratchpad state is not canonical memory and may not reach a sink",
        )

    verdict = transitions.legal_transition(
        transitions.ACTION_MACHINE,
        action.from_state,
        action.to_state,
        reason_code=action.reason_code,
    )
    if not verdict.legal:
        raise InvariantViolation(
            "ACTION_TRANSITION_ILLEGAL",
            f"{verdict.from_state} -> {verdict.to_state} "
            f"(code={verdict.code}, reason_code={action.reason_code!r}) {verdict.detail}",
        )

    if str(action.to_state) != _EXECUTING:
        # Nothing leaves the building on this edge, so the approval binding is
        # not evaluated. Requiring it here would force a cancellation to carry
        # a draft hash it has no reason to hold.
        return

    if action.approval_draft_sha256 is None:
        raise InvariantViolation(
            "ACTION_DRAFT_HASH_MISSING",
            "execution requires the sha256 of the draft that was approved",
        )
    if action.draft_sha256 != action.approval_draft_sha256:
        raise InvariantViolation(
            "ACTION_DRAFT_HASH_CHANGED",
            f"approved sha256 {action.approval_draft_sha256} but the draft now hashes to "
            f"{action.draft_sha256}; the approval does not cover this text",
        )
    if action.basis_case_revision is None or action.current_case_revision is None:
        raise InvariantViolation(
            "ACTION_CASE_REVISION_MISSING",
            "execution requires the case revision the approval was given against",
        )
    if action.basis_case_revision != action.current_case_revision:
        raise InvariantViolation(
            "ACTION_CASE_REVISION_CHANGED",
            f"approved against case revision {action.basis_case_revision}, now "
            f"{action.current_case_revision}; the case moved under the approval",
        )


# ---------------------------------------------------------------------------
# The grounding invariant - 00_PRODUCT.md section 0.2
# ---------------------------------------------------------------------------

#: Grounded by at least one ``belief_support`` edge.
GROUNDING_ROUTE_EVIDENCE: Final[str] = "EVIDENCE"
#: Grounded by a registered deterministic derivation instead.
GROUNDING_ROUTE_DERIVATION: Final[str] = "DERIVATION"
#: Not canonical, so the invariant does not constrain it.
GROUNDING_ROUTE_RETRACTED: Final[str] = "RETRACTED_EXEMPT"
#: Neither route holds.
GROUNDING_ROUTE_NONE: Final[str] = "NONE"


@dataclass(frozen=True, slots=True)
class GroundingVerdict:
    """Whether a canonical belief version is GROUNDED, and by which route."""

    grounded: bool
    route: str
    support_edge_count: int
    source_kind: SupportSourceKind | None = None
    derivation: derivations.DerivationSpec | None = None
    code: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.grounded


def grounding_verdict(
    *,
    support_edge_count: int,
    derivation_kind: str | None = None,
    derivation_version: str | None = None,
    epistemic_status: EpistemicStatus | None = None,
) -> GroundingVerdict:
    """The grounding invariant, as a predicate.

    A canonical belief version is GROUNDED when it has at least one
    ``belief_support`` edge, **unless** it declares a ``derivation_kind`` that
    is a registered deterministic derivation, in which case it carries a
    ``source_kind = 'DERIVATION'`` edge instead and is still GROUNDED.

    The derivation route fails closed. If any string could buy grounding, an
    agent could ground any claim by inventing a derivation name and the
    invariant would be decoration; the closed registry in
    :mod:`provenance_domain.derivations` is what stops that.

    A ``RETRACTED`` version is no longer canonical, so the invariant does not
    constrain it - the same exemption ``11_CONTRACTS.md`` section 12 makes on
    the read side in ``BeliefVersionRef``.

    Raises:
        ValueError: *support_edge_count* is negative. That is a caller bug, not
            a grounding question, and answering it would be a guess.
    """
    if support_edge_count < 0:
        raise ValueError(f"support_edge_count must not be negative, got {support_edge_count}")

    if epistemic_status is EpistemicStatus.RETRACTED:
        return GroundingVerdict(
            grounded=True,
            route=GROUNDING_ROUTE_RETRACTED,
            support_edge_count=support_edge_count,
            detail="a retracted version is not canonical; the grounding invariant does not apply",
        )

    if derivation_kind is not None:
        if derivation_version is None:
            return GroundingVerdict(
                grounded=False,
                route=GROUNDING_ROUTE_NONE,
                support_edge_count=support_edge_count,
                code="DERIVATION_VERSION_MISSING",
                detail=(
                    f"derivation {derivation_kind!r} named no function_version; a derivation "
                    "whose rule changed is a different derivation"
                ),
            )
        if not derivations.is_registered_derivation(derivation_kind, derivation_version):
            registered = ", ".join(sorted(derivations.DERIVATION_REGISTRY))
            return GroundingVerdict(
                grounded=False,
                route=GROUNDING_ROUTE_NONE,
                support_edge_count=support_edge_count,
                code="DERIVATION_UNREGISTERED",
                detail=(
                    f"{derivation_kind!r} at version {derivation_version!r} is not a "
                    f"registered deterministic derivation (registered: {registered})"
                ),
            )
        grounding = derivations.grounding_for(derivation_kind, derivation_version)
        return GroundingVerdict(
            grounded=grounding.is_grounded,
            route=GROUNDING_ROUTE_DERIVATION,
            support_edge_count=support_edge_count,
            source_kind=grounding.source_kind,
            derivation=grounding.spec,
        )

    if support_edge_count >= 1:
        return GroundingVerdict(
            grounded=True,
            route=GROUNDING_ROUTE_EVIDENCE,
            support_edge_count=support_edge_count,
            source_kind=SupportSourceKind.EVIDENCE,
        )

    return GroundingVerdict(
        grounded=False,
        route=GROUNDING_ROUTE_NONE,
        support_edge_count=0,
        code="BELIEF_NOT_GROUNDED",
        detail=(
            "a canonical belief version must carry at least one belief_support edge or "
            "declare a registered deterministic derivation"
        ),
    )


def assert_grounded(
    *,
    support_edge_count: int,
    derivation_kind: str | None = None,
    derivation_version: str | None = None,
    epistemic_status: EpistemicStatus | None = None,
) -> GroundingVerdict:
    """Raise :class:`InvariantViolation` unless the version is GROUNDED."""
    verdict = grounding_verdict(
        support_edge_count=support_edge_count,
        derivation_kind=derivation_kind,
        derivation_version=derivation_version,
        epistemic_status=epistemic_status,
    )
    if not verdict.grounded:
        raise InvariantViolation(verdict.code or "BELIEF_NOT_GROUNDED", verdict.detail)
    return verdict
