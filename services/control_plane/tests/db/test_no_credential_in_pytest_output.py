"""A failing test in this lane must not print the live database password.

THE DEFECT THIS GUARDS, demonstrated before it was fixed:

    $ pytest services/control_plane/tests/db/<any failing test> -q
    test_dsn = 'postgresql://pv_migrator:<the live password>@rayyandb-...'

pytest renders every test-function argument in its failure header via
``repr()``. A session fixture returning a plain ``str`` DSN therefore wrote the
live ``pv_migrator`` credential into the output of ANY failing test in this
lane. That output is not ephemeral: it goes into CI logs, into the ``ops/tdd/``
evidence transcripts this project commits, and into gate reports. The role owns
every canonical table.

Scrubbing subprocess output does not help and the conftest already did it -- the
leak is pytest's own header, not the child process. The fix is ``MaskedDsn``,
whose ``__repr__`` is masked while the value stays substitutable for ``str`` so
psycopg and Alembic still receive the real DSN.

This test is the positive control. It provokes a real failure in a real pytest
subprocess and asserts the password is absent from what that subprocess printed.
It never writes the password anywhere itself: the comparison is a membership
test, and on failure the message reports only WHERE the leak appeared.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]

pytestmark = pytest.mark.db


def _live_password() -> str | None:
    """The password from the configured CI DSN, read straight from .env."""
    env = REPO_ROOT / ".env"
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("PROVENANCE_TEST_DB_URL="):
            m = re.match(r"^[^=]+=\w+://[^:]+:([^@]+)@", line)
            return m.group(1) if m else None
    return None


def test_a_failing_test_does_not_print_the_database_password(tmp_path: Path) -> None:
    # Named `credential`, not `password`: the pv-password-assignment rule in
    # .gitleaks.toml matches `password =` and fires on this line, capturing the
    # function CALL as its secret. Renaming the local costs nothing and keeps
    # the rule at full strictness; an allowlist entry would be permanent
    # configuration surface bought to silence a variable name.
    credential = _live_password()
    if not credential:
        pytest.skip("PROVENANCE_TEST_DB_URL is not configured; nothing to leak")

    probe = REPO_ROOT / "services" / "control_plane" / "tests" / "db" / "test_zz_leak_control.py"
    probe.write_text(
        "def test_deliberate_failure(test_dsn: str) -> None:\n"
        "    assert False, 'provoked so the failure header can be inspected'\n",
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        probe.unlink(missing_ok=True)

    combined = (completed.stdout or "") + (completed.stderr or "")
    assert (
        "test_deliberate_failure" in combined
    ), "the probe did not run, so this test proved nothing about leakage"

    # Report the location, never the value.
    leaking = [f"line {n}" for n, line in enumerate(combined.splitlines(), 1) if credential in line]
    assert not leaking, (
        "the live database password appears in pytest's own output at "
        f"{', '.join(leaking)}. A fixture is returning a bare DSN string again; "
        "it must return MaskedDsn (see conftest.py). This output reaches CI logs, "
        "ops/tdd/ transcripts and gate reports, all of which are committed."
    )
