"""The sabotage runner must not read "did not pass" as "was caught".

What this is for
----------------
`tests/sabotage_matrix.yaml` holds 13 claims of the form "neuter this symbol and
that test selection goes red". `tools/sabotage_guard.py` checks the matrix is
append-only and counts it. **Nothing ran it.** Thirteen claims, zero proofs, and
the file's own header says the quiet part:

    A green test suite proves that the tests pass. It does not prove that they
    would fail if the thing they test were broken.

That applies to this file too. A runner that mapped "pytest exited non-zero" to
"the sabotage was caught" would be the same vacuity one level up, because three
different non-zero exits mean nothing of the kind:

===  ==========================  ================================
 0   all tests passed            **GATE FAILURE** -- the sabotage survived
 1   tests failed                PASS -- the neutering was caught
 2   collection / internal error CANNOT RUN -- nothing was proved
 4   usage error                 CANNOT RUN -- the selection is malformed
 5   no tests collected          CANNOT RUN -- an empty selection fails nothing
===  ==========================  ================================

Exit 5 is the trap worth naming. A matrix entry whose `tests:` path is a typo,
or points at a directory that has since moved, collects zero tests and exits
non-zero. Read as "red", it reports a **passing sabotage for a selection that
executed nothing** -- precisely the failure `D-00-005` names: `CANNOT RUN`
recorded as a result.

Exit 2 has already happened in this repository for an unrelated reason: a full
`C:` drive produced a `MemoryError` during collection, which exits 2 and reads
exactly like a failing test.
"""

from __future__ import annotations

import pytest

from tools.sabotage_run import Outcome, classify, skipped_count

pytestmark = pytest.mark.unit


def test_a_passing_suite_under_sabotage_is_a_gate_failure() -> None:
    """The whole point. The symbol was neutered and nothing noticed."""
    assert classify(0, skipped=0) is Outcome.SURVIVED


def test_a_pass_with_skips_is_cannot_run_not_survived() -> None:
    """The bug this runner shipped with, and found in itself on run one.

    `retrieval.predicates.retraction_filter` reported SURVIVED. It had not
    survived: the selection was `2 passed, 4 skipped`, and the 4 skipped were
    exactly the ones that touch the filter -- they skip because the retrieval
    lane needs `provenance_ci`, which is unseeded.

    Exit 0 therefore means one of two opposite things: every test ran and the
    sabotage was missed, or the discriminating tests never executed. Collapsing
    them reports a gate failure the code did not commit, which is the same
    `CANNOT RUN` / `FAIL` confusion as `D-00-005` -- inverted, and in a tool
    built to police exactly that.
    """
    assert classify(0, skipped=4) is Outcome.CANNOT_RUN
    assert classify(0, skipped=1) is Outcome.CANNOT_RUN


def test_a_failure_with_skips_is_still_caught() -> None:
    """Something failed, so something discriminated. Skips do not weaken that."""
    assert classify(1, skipped=4) is Outcome.CAUGHT


def test_a_failing_suite_under_sabotage_is_the_pass() -> None:
    assert classify(1, skipped=0) is Outcome.CAUGHT


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_every_other_exit_is_cannot_run_rather_than_caught(code: int) -> None:
    """The vacuity guard.

    Exit 5 (no tests collected) is the one that would silently pass: a matrix
    entry pointing at a moved directory collects nothing, exits non-zero, and a
    naive runner calls that a proof.
    """
    assert classify(code, skipped=0) is Outcome.CANNOT_RUN


def test_cannot_run_is_not_caught() -> None:
    """Stated as its own assertion because collapsing them is the defect.

    A runner that treated CANNOT_RUN as a pass would report a fully green
    sabotage gate over a matrix where every selection was misspelled.
    """
    assert Outcome.CANNOT_RUN is not Outcome.CAUGHT
    assert Outcome.CANNOT_RUN is not Outcome.SURVIVED


def test_only_caught_counts_as_success() -> None:
    """`Outcome.ok` is what the exit code of the whole run is built from."""
    assert Outcome.CAUGHT.ok is True
    assert Outcome.SURVIVED.ok is False
    assert Outcome.CANNOT_RUN.ok is False


def test_an_unknown_exit_code_is_not_silently_a_pass() -> None:
    """Defensive: a future pytest exit code must not default to CAUGHT."""
    assert classify(99, skipped=0) is Outcome.CANNOT_RUN
    assert classify(-1, skipped=0) is Outcome.CANNOT_RUN


def test_the_skip_count_is_read_off_real_pytest_output() -> None:
    """A parser that silently returned 0 would re-open the hole above."""
    assert skipped_count("2 passed, 4 skipped in 5.12s") == 4
    assert skipped_count("13 passed in 1.2s") == 0
    assert skipped_count("1 failed, 2 passed, 3 skipped in 9s") == 3
    assert skipped_count("2708 passed, 4 skipped, 884 deselected in 67.27s") == 4
    # No summary at all (a crash before pytest printed one) must not read as
    # "nothing was skipped" -- it is unknown, and unknown is not zero.
    assert skipped_count("") is None
    assert skipped_count("MemoryError") is None
