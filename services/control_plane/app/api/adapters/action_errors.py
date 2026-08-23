"""``ActionRefusedError`` -> ``ApiError``. One table, in one place.

Authority
---------
- ``specs/15_API_SPEC.md`` section 4.3 (the closed error catalogue), sections
  8.25-8.27 and 9.11 (which refusals each endpoint may return), and section
  1.7 (a cross-scope read is indistinguishable from absence).
- ``services/control_plane/app/actions/__init__.py``: "Both raise
  ``ActionRefusedError`` carrying a ``reason_code`` and a ``details`` mapping;
  mapping those to ``ErrorCode`` and a status belongs to ``app/api/errors.py``,
  which owns that table."

Why the action plane does not import ``ErrorCode``
---------------------------------------------------
It would invert the dependency: ``app/actions`` would import ``app/api``, and
the action plane would stop being usable by a worker that has no HTTP surface
at all -- which is exactly what the outbox-driven executor is. So the action
plane raises a string reason code and this adapter, which already sits on the
boundary, does the translation. That is one crossing, in one direction, in one
file.

``NO_COMMITTED_BASIS`` now has its own member, and that distinction matters
----------------------------------------------------------------------------
``ErrorCode.NO_COMMITTED_BASIS`` exists (409), so ``G9.6``'s "an ActionIntent
whose case has no committed kernel_decision -> 409 NO_COMMITTED_BASIS" is
answered by its own code rather than approximated onto a neighbour. The
approximation was not cosmetic while it lasted: ``ACTION_STALE`` and
``ACTION_NOT_APPROVABLE`` both invite the client to re-read and try again,
while ``NO_COMMITTED_BASIS`` is invariant 4 -- there was never a committed
basis for the send to be bound to -- so retrying it is precisely the wrong
move. ``errors.py`` says the same thing at the member's definition.

Why ``SUPPORT_SET_UNAVAILABLE`` is not in :data:`APPROXIMATED`
---------------------------------------------------------------
It maps onto ``INTERNAL_ERROR``, and that is the right code rather than the
nearest one. Section 4.3's catalogue is closed and public; enumerating each
internal defect in it would tell a caller about our plumbing and give them
nothing they can act on, since the remedy is a deploy and not a different
request. ``INTERNAL_ERROR`` plus ``details.reason_code`` is the correct shape
for a server-side omission, and the handler renders ``details`` verbatim at
every status, so the exact string survives a 500 the same way it survives a
409. So this is not a gap waiting to be closed, and it is deliberately absent
from the set that tracks gaps.

One reason code still has no member
------------------------------------
``RISK_TIER_NOT_PERMITTED`` maps onto ``VALIDATION_FAILED``, which carries the
status the action plane specified (422), and the exact reason code still
reaches the client under ``details.reason_code``. :data:`APPROXIMATED` names
it so the gap is greppable and so a test can assert that set shrinks rather
than grows.

Two tables, because a refusal and a blocking reason are different things
--------------------------------------------------------------------------
:data:`REFUSAL_STATUS` translates an ``ActionRefusedError`` -- something
``app/actions`` *raised* instead of acting. :data:`BLOCKING_STATUS` translates
a revalidation blocking reason -- something
:func:`~services.control_plane.app.actions.executor.revalidate` *returned*,
which the executor had already written to ``action_executions.error_code``
before declining to send. The two vocabularies overlap by two strings and are
otherwise disjoint, and collapsing them would mean an executor code silently
inheriting whatever an intent-time code of the same name was mapped to.

Why ``details.reason_code`` is set for *every* refusal and not just the two
----------------------------------------------------------------------------
Because a rule with two exceptions is a rule nobody can rely on. A client
branching on ``details.reason_code`` works for all fourteen codes, today and
after the enum grows, and the two temporarily-approximated ones are then not a
special case in anybody's client code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from services.control_plane.app.actions import ActionRefusedError
from services.control_plane.app.api.errors import ApiError, ErrorCode

__all__ = [
    "ABSENT_REASON_CODES",
    "APPROXIMATED",
    "BLOCKING_STATUS",
    "REFUSAL_STATUS",
    "as_api_error",
    "blocking_error",
    "is_absent",
    "raise_as_api_error",
]

#: Reason codes that mean "no such row **for this scope**".
#:
#: These do not become an :class:`ApiError` here. ``routes/actions.py`` already
#: writes ``if row is None: raise absent(ErrorCode.ACTION_INTENT_NOT_FOUND)``,
#: so the adapter returns ``None`` and the 404 is produced in exactly one
#: place. Two definitions of "another user's intent looks absent" is one more
#: than the number that can be kept correct.
ABSENT_REASON_CODES: Final[frozenset[str]] = frozenset({"ACTION_INTENT_NOT_FOUND"})

#: Every refusal ``app/actions`` can raise -> the catalogue code it answers as.
#:
#: ``tests/api/test_port_adapters.py`` enumerates the ``Final[str]`` constants
#: in ``app/actions`` and requires every one of them to appear here, so a
#: reason code added by Phase 9 fails in the unit lane rather than reaching a
#: client as ``500 INTERNAL_ERROR``.
REFUSAL_STATUS: Final[Mapping[str, ErrorCode]] = {
    # -- absence ----------------------------------------------------------
    "ACTION_INTENT_NOT_FOUND": ErrorCode.ACTION_INTENT_NOT_FOUND,
    # -- 409, the state machine refused -----------------------------------
    "ACTION_NOT_APPROVABLE": ErrorCode.ACTION_NOT_APPROVABLE,
    "ACTION_ALREADY_EXECUTED": ErrorCode.ACTION_ALREADY_EXECUTED,
    "ACTION_DRAFT_FROZEN": ErrorCode.ACTION_DRAFT_FROZEN,
    "ACTION_STALE": ErrorCode.ACTION_STALE,
    "IDEMPOTENCY_CONFLICT": ErrorCode.IDEMPOTENCY_CONFLICT,
    # The basis revision the draft was validated against is not the case's
    # current revision. That is staleness by any other name, and section 7.3's
    # body is what the client needs in order to recover from it.
    # Not ACTION_STALE, and the reachability is what settles it.
    # `ActionIntentService.create` raises ACTION_STALE itself when
    # `snapshot.case_revision != request.basis_case_revision`, *before* it
    # calls `validate_draft_claims`. So by the time this code can be produced
    # the request's basis already matches the world, and the only thing left
    # that can differ is `DraftAction.basis_case_revision` against the
    # `CreateIntentRequest.basis_case_revision` carrying it: the draft
    # disagrees with its own request. That is malformed, not stale, and
    # answering ACTION_STALE would send the client into a reload loop against
    # a case that will read exactly the same next time -- the identical
    # reasoning BASIS_CASE_MISMATCH gets below.
    "BASIS_REVISION_MISMATCH": ErrorCode.VALIDATION_FAILED,
    # The operator kill switch (`PV_ACTION_EXECUTION_MODE`). Not a 503: the
    # dependency is fine and retrying will not help, because a human turned it
    # off on purpose. `ACTION_NOT_APPROVABLE` is the closest true statement --
    # this action cannot proceed in the system's current state.
    "ACTION_EXECUTION_DISABLED": ErrorCode.ACTION_NOT_APPROVABLE,
    # `G9.6`, on its own member: distinct from ACTION_STALE because the basis
    # never existed rather than having moved, and a client that retries a
    # NO_COMMITTED_BASIS refusal is retrying invariant 4.
    "NO_COMMITTED_BASIS": ErrorCode.NO_COMMITTED_BASIS,
    # -- 422, the request or the draft was refused ------------------------
    "VALIDATION_FAILED": ErrorCode.VALIDATION_FAILED,
    "RECIPIENT_NOT_ALLOWLISTED": ErrorCode.RECIPIENT_NOT_ALLOWED,
    "DRAFT_CLAIM_UNSUPPORTED": ErrorCode.DRAFT_UNSUPPORTED_CLAIM,
    # A draft naming a different case than the intent is a malformed request,
    # not a stale one: no amount of reloading makes it valid, so answering
    # `ACTION_STALE` would send the client into a refresh loop.
    "BASIS_CASE_MISMATCH": ErrorCode.VALIDATION_FAILED,
    # APPROXIMATED -- no `RISK_TIER_NOT_PERMITTED` member exists.
    "RISK_TIER_NOT_PERMITTED": ErrorCode.VALIDATION_FAILED,
    # -- 500, the server did not know -------------------------------------
    # The ONLY entry in this table that is not the client's fault, and the
    # only one above 499. `GroundingSnapshot.support_ids is None` means the
    # store never loaded a citation set, so the grounding question was not
    # asked. It must never collapse onto `DRAFT_UNSUPPORTED_CLAIM` (422):
    # that would render a store defect exactly like a correctly refused
    # ungrounded draft, which is the demo's headline refusal, and nobody
    # reading the response could tell the two apart.
    #
    # 500 and not 503, though the action plane offered either. Both 503
    # messages in the catalogue promise a retry helps -- "The record was
    # busy. Retry the identical request." and "A dependency is unavailable."
    # Neither is true: nothing was busy, no dependency failed, and the next
    # identical request runs the same code path over the same store and gets
    # the same `None`. Handing out a retry hint for a deterministic condition
    # is the same error as answering `ACTION_STALE` to `NO_COMMITTED_BASIS`,
    # two entries up. `INTERNAL_ERROR`'s message -- "Something went wrong on
    # our side. Nothing was committed." -- is literally true here, because
    # `create` refuses before the insert.
    "SUPPORT_SET_UNAVAILABLE": ErrorCode.INTERNAL_ERROR,
}

#: The codes above whose ``ErrorCode`` is an approximation, named so the gap is
#: greppable and so a test can assert the set shrinks rather than grows.
#: ``NO_COMMITTED_BASIS`` left this set when ``errors.py`` gained its member.
#: Every entry still travels exactly under ``details.reason_code``.
APPROXIMATED: Final[frozenset[str]] = frozenset({"RISK_TIER_NOT_PERMITTED"})

#: A revalidation blocking reason -> the catalogue code the send is refused as.
#:
#: These are the strings :func:`actions.executor.revalidate` returns and the
#: executor writes to ``action_executions.error_code``. Section 9.11's error
#: list is the authority for the three that are not ``ACTION_STALE``:
#: ``422 RECIPIENT_NOT_ALLOWED``, ``409 ACTION_NOT_APPROVABLE`` and
#: ``409 ACTION_ALREADY_EXECUTED`` are listed there beside it, so answering
#: ``ACTION_STALE`` to any of them would send a client off to reload a case
#: that has not moved.
#:
#: The order ``revalidate`` returns them in is fixed -- ``NO_COMMITTED_BASIS``,
#: ``NOT_APPROVED``, ``CASE_REVISION_MOVED``, ``DRAFT_HASH_CHANGED``,
#: ``SUPPORT_BELIEF_SUPERSEDED``, ``ALREADY_EXECUTED``,
#: ``RECIPIENT_NOT_ALLOWLISTED`` -- and ``blocking_reasons[0]`` is the exact
#: string the executor writes to ``action_executions.error_code``. That is why
#: :func:`blocking_error` keys on the first element and not on a search for the
#: "worst" reason: a judge comparing the HTTP body against the row sees one
#: value rather than two.
#:
#: The ledger's ``status`` column will NOT agree with the code chosen here, and
#: that is forced rather than sloppy: ``ck_action_executions_status`` in ``0007``
#: admits five values and ``ABORTED_STALE`` is the only terminal refusal among
#: them, so a ``NO_COMMITTED_BASIS`` or ``RECIPIENT_NOT_ALLOWLISTED`` refusal is
#: recorded as ``ABORTED_STALE`` with the real reason in ``error_code``.
#: ``error_code`` is the field of record on both sides -- it is the same string
#: as ``blocking_reasons[0]`` and as ``details.reason_code`` -- and anybody
#: diffing a response against ``action_executions`` should read that column
#: rather than ``status``. Adding a sixth status to make them match would be
#: refused by the database as a ``23514`` at run time, on the one operation that
#: cannot be undone.
#:
#: ``tests/api/test_port_adapters.py`` enumerates the executor's ``Final[str]``
#: constants and requires every one of them to appear here, for the same reason
#: :data:`REFUSAL_STATUS` is scanned rather than trusted.
BLOCKING_STATUS: Final[Mapping[str, ErrorCode]] = {
    # G9.6, and `revalidate` checks it first, so it wins the primary slot.
    "NO_COMMITTED_BASIS": ErrorCode.NO_COMMITTED_BASIS,
    "NOT_APPROVED": ErrorCode.ACTION_NOT_APPROVABLE,
    "CASE_REVISION_MOVED": ErrorCode.ACTION_STALE,
    "DRAFT_HASH_CHANGED": ErrorCode.ACTION_STALE,
    "SUPPORT_BELIEF_SUPERSEDED": ErrorCode.ACTION_STALE,
    "ALREADY_EXECUTED": ErrorCode.ACTION_ALREADY_EXECUTED,
    "RECIPIENT_NOT_ALLOWLISTED": ErrorCode.RECIPIENT_NOT_ALLOWED,
    # Never reaches an error today: the kill switch returns a `200
    # NOT_EXECUTED` outcome with no attempt recorded (`G9.6`'s rollback
    # position). Mapped anyway, so the completeness scan covers the executor's
    # whole vocabulary rather than the part that currently happens to raise.
    "ACTION_EXECUTION_DISABLED": ErrorCode.ACTION_NOT_APPROVABLE,
}

#: Used only when the action plane raises a code this table has never seen.
#: It should be unreachable -- the drift test above exists to keep it that way
#: -- and it is deliberately a 409 rather than a 500: an unrecognised refusal
#: is still a refusal, and answering "the server broke" to a request the server
#: correctly declined would be the wrong story in the logs.
_FALLBACK: Final[ErrorCode] = ErrorCode.ACTION_NOT_APPROVABLE


def is_absent(error: ActionRefusedError) -> bool:
    """Whether *error* means "no such row for this scope"."""
    return error.reason_code in ABSENT_REASON_CODES


def as_api_error(error: ActionRefusedError) -> ApiError:
    """Translate one refusal, preserving its reason code and its details.

    The details mapping the action plane attached travels verbatim --
    ``stale_reason``, ``current_case_revision``, ``current_draft_sha256``,
    ``unacknowledged``, ``sentences`` -- because section 7.3's ``ACTION_STALE``
    body *is* those fields, and a client that cannot see what moved cannot
    show the user what to re-read.
    """
    code = REFUSAL_STATUS.get(error.reason_code, _FALLBACK)
    details: dict[str, Any] = dict(error.details)
    details["reason_code"] = error.reason_code
    return ApiError(code, details=details)


def raise_as_api_error(error: ActionRefusedError) -> None:
    """Re-raise *error* as an :class:`ApiError`, or return for an absence.

    Returning rather than raising on an absence is what lets a caller write
    the two-line ``except`` block once:

        except ActionRefusedError as refusal:
            raise_as_api_error(refusal)
            return None
    """
    if is_absent(error):
        return
    raise as_api_error(error) from error


def blocking_error(reasons: Sequence[str], **details: Any) -> ApiError:
    """The error for an execution that revalidated, refused, and sent nothing.

    The status is taken from ``reasons[0]`` rather than from a scan for the
    "worst" reason, and that is deliberate:
    :func:`~services.control_plane.app.actions.executor.revalidate` returns its
    reasons in a fixed order and the executor writes ``blocking[0]`` to
    ``action_executions.error_code``. Deriving the HTTP code from the same
    element means the status a client sees and the reason the ledger records
    are one fact read twice, rather than two facts that can disagree in an
    incident review.

    Every reason still travels, under ``details.blocking_reasons`` -- a
    section 9.11 refusal is rarely single-valued, and "the case moved" is
    useless to a human who cannot see what else moved with it.

    Raises:
        ValueError: *reasons* is empty. There is no such thing as a refusal
            with no reason, and inventing one here would produce a 409 whose
            body says nothing at all.
    """
    if not reasons:
        raise ValueError("blocking_error needs at least one blocking reason")
    primary = reasons[0]
    body: dict[str, Any] = dict(details)
    body["reason_code"] = primary
    body["blocking_reasons"] = list(reasons)
    return ApiError(BLOCKING_STATUS.get(primary, _FALLBACK), details=body)
