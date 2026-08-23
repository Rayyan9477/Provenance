"""A capability proof must not depend on the second it is checked.

The defect this closes
-----------------------
``CapabilityRecord.expires_at`` for ``TRIGGER_EVALUATION`` and ``ACTION_INTENT``
was ``_derived_expiry()``, which is ``datetime.now(UTC) + TTL``. The proof's MAC
covers ``int(expires_at.timestamp())`` (``capability_proof._message``), and
``verify_capability_proof`` is documented to take ``expires_at`` **from the
loaded row** -- but for these two kinds the "loaded row" value was recomputed
from the wall clock on every request.

So the number inside the MAC changed once a second, and a proof verified only
if it happened to be checked during the same second it was issued. Measured
against the real functions, issuing once and verifying six times over ~2s:

    attempt 0: VERIFIED   (expiry ts 1787639815)
    attempt 1: VERIFIED   (expiry ts 1787639815)
    attempt 2: REFUSED    (expiry ts 1787639816, issuer used 1787639815)
    ...
    verified 2/6, refused 4/6

Nothing about the credential changed between attempt 1 and attempt 2. The clock
ticked.

Why it matters more than an ordinary auth bug
----------------------------------------------
These are two of the four capability kinds, and they gate both of the demo's
reveals: ``TRIGGER_EVALUATION`` is how a fired obligation reaches the Kernel --
prospective memory, one of the four things `00_PRODUCT.md` §2.2 claims ordinary
RAG cannot do -- and ``ACTION_INTENT`` is how an approved draft reaches an
executor. Neither could be dispatched reliably, and the failure is
*intermittent*, which is the worst kind: a retry sometimes works, so it reads
as a flaky network rather than as a broken credential.

``AGENT_RUN`` was unaffected: it takes ``expires_at`` from a stored column, as
the docstring intends.

The repair
----------
The lifetime is still derived rather than taken from the predicate's own
``expires_at`` -- the original comment is right that a months-away obligation
deadline is far too long for a credential. It is now derived from a **stored**
anchor (``updated_at``) instead of from ``now``, which keeps the window bounded,
makes it stable for as long as the row is unchanged, and *rotates the proof
whenever the row changes*. That last property is stronger than what was there
before: a proof observed in a trace stops working the moment the trigger is
evaluated or the intent is approved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.control_plane.app.api.adapters.directory import (
    _DERIVED_TTL_SECONDS,
    _derived_expiry,
)
from services.control_plane.app.api.errors import ApiError
from services.control_plane.app.auth.capability_proof import (
    issue_capability_proof,
    verify_capability_proof,
)

pytestmark = pytest.mark.unit

KEY = b"k" * 32
CAP = "a7803e23-b035-43ee-ac68-af87087bc905"
ANCHOR = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def test_the_derived_expiry_is_a_pure_function_of_its_anchor() -> None:
    """The regression, pinned. Two calls a second apart must agree.

    Previously `_derived_expiry()` took no argument and read the clock, so this
    is also the assertion that it cannot go back to doing that: a signature
    that accepts an anchor cannot silently depend on `now`.
    """
    first = _derived_expiry(ANCHOR)
    second = _derived_expiry(ANCHOR)
    assert first == second
    assert first == ANCHOR + timedelta(seconds=_DERIVED_TTL_SECONDS)


def test_a_proof_still_verifies_after_the_clock_moves() -> None:
    """The observable failure: issue once, verify later, same row."""
    proof = issue_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(ANCHOR), key=KEY)
    # Every later verification re-derives from the SAME stored anchor.
    for _ in range(5):
        verify_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(ANCHOR), proof, key=KEY)


def test_a_proof_stops_verifying_once_the_row_changes() -> None:
    """The property the fix buys, which the broken version did not have.

    A proof observed in a trace -- capability ids appear in `agent_runs`, in
    traces and in Judge Mode, which is why the proof exists at all -- is dead as
    soon as the trigger is evaluated or the intent approved, because
    `updated_at` moves.
    """
    proof = issue_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(ANCHOR), key=KEY)
    moved = ANCHOR + timedelta(seconds=1)
    with pytest.raises(ApiError):
        verify_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(moved), proof, key=KEY)


def test_a_proof_for_one_capability_does_not_verify_for_another() -> None:
    """Positive control. If the MAC stopped covering the key, the tests above
    would still pass while the proof authorised anything."""
    proof = issue_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(ANCHOR), key=KEY)
    other = "00000000-0000-7000-8000-000000000001"
    with pytest.raises(ApiError):
        verify_capability_proof(
            "TRIGGER_EVALUATION", other, _derived_expiry(ANCHOR), proof, key=KEY
        )


def test_a_proof_for_one_kind_does_not_verify_for_another() -> None:
    """Positive control on the kind field."""
    proof = issue_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(ANCHOR), key=KEY)
    with pytest.raises(ApiError):
        verify_capability_proof("ACTION_INTENT", CAP, _derived_expiry(ANCHOR), proof, key=KEY)
