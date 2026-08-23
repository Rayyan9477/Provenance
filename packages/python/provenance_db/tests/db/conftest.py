"""Fixtures for the ``provenance_db`` layer-2 tests — T3.1, T3.2.

Scope, deliberately narrow
--------------------------
This is **not** the database harness of ``T3.5``. That harness clones a
per-module database from the seeded template, implements ``as_role`` over the
four runtime roles, and lives in ``services/control_plane/tests/db/conftest.py``
with helpers in ``tests/support/``. It depends on ``T2.8``'s seeded template,
which does not exist yet. What is here is the minimum these two files need: a
role -> DSN source read from the environment, and a scratch table with a
``_pv_t3_`` prefix that each module creates and drops for itself.

Two environmental facts, recorded rather than worked around silently
--------------------------------------------------------------------
1. **The event loop.** ``psycopg`` refuses to run async on Windows'
   ``ProactorEventLoop`` — ``InterfaceError: Psycopg cannot use the
   'ProactorEventLoop' to run in async mode``. ``pytest-asyncio`` builds its
   loop from the policy, so the policy is overridden here. Any later harness
   that opens an async connection on Windows needs the same fixture; ``T3.5``
   inherits this note.

2. **The environment variable names.** ``provenance_contracts.settings``
   declares ``COCKROACH_MIGRATOR_URL`` / ``COCKROACH_DATABASE_URL`` /
   ``COCKROACH_KERNEL_URL`` and *no* variable for ``pv_agent_reader`` or
   ``pv_ops_reader`` — both are resolved from the ``provenance/db`` secret at
   the moment of use. The cluster provisioned by ``T0.5`` exports
   ``PV_DB_MIGRATOR`` / ``PV_DB_KERNEL`` / ``PV_DB_AGENT_READER`` /
   ``PV_DB_OPS_READER`` instead. Both spellings are accepted below, canonical
   name first, and the divergence is reported as a defect rather than being
   quietly normalised into product code — ``urls.py`` knows only roles, never
   variable names.

Reading ``os.environ`` here does not breach the settings rule
------------------------------------------------------------
``provenance_contracts/settings.py`` owns environment reads *for product
code*. A test harness is the other side of that boundary: it supplies the
values a ``Settings`` object would otherwise carry, and
``quality/23_PHASE_GATES.md`` section 2.2 governs it instead. No product module
in ``provenance_db`` reads the environment, and
``test_pool_and_roles.py::test_no_pool_is_constructed_from_a_url_passed_by_application_code``
asserts exactly that.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

#: The repository root, from this file's position in the tree.
REPO_ROOT = Path(__file__).resolve().parents[5]

#: Role -> the environment variables that may carry its DSN, canonical first.
ROLE_ENV_VARS: dict[str, tuple[str, ...]] = {
    "pv_migrator": ("COCKROACH_MIGRATOR_URL", "PV_DB_MIGRATOR"),
    "pv_app_reader_writer": ("COCKROACH_DATABASE_URL",),
    "pv_kernel_writer": ("COCKROACH_KERNEL_URL", "PV_DB_KERNEL"),
    "pv_agent_reader": ("PV_DB_AGENT_READER",),
    "pv_ops_reader": ("PV_DB_OPS_READER",),
}

#: The CI database. Every test that creates a table uses this and nothing else.
TEST_DB_ENV_VAR = "PROVENANCE_TEST_DB_URL"


def _dotenv_value(name: str) -> str | None:
    """*name* as written in the gitignored repository-root ``.env``.

    The fallback exists because a **skip** is the worst outcome for ``G3.1``
    and ``G3.2``: a gate battery run without the environment exported would
    report "skipped" and look like a pass. ``services/control_plane/tests/db/
    conftest.py`` reads ``.env`` for the same reason, so the two lanes behave
    the same way on the same machine.

    This does not weaken the settings rule: no *product* module reads the
    environment, and none reads this file. The value is never printed.
    """
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == name:
            return value.strip() or None
    return None


def _first_set(names: tuple[str, ...]) -> str | None:
    """The first of *names* set in the environment, then in ``.env``."""
    for name in names:
        value = os.environ.get(name) or _dotenv_value(name)
        if value:
            return value
    return None


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """A selector loop on Windows; the platform default everywhere else."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="session")
def role_dsns() -> dict[str, SecretStr]:
    """Every role DSN this machine actually has, keyed by SQL role name."""
    found: dict[str, SecretStr] = {}
    for role, names in ROLE_ENV_VARS.items():
        value = _first_set(names)
        if value is not None:
            found[role] = SecretStr(value)
    return found


@pytest.fixture(scope="session")
def require_role(role_dsns: dict[str, SecretStr]) -> Callable[[str], SecretStr]:
    """Look a role's DSN up, or skip naming the variable that would supply it."""

    def lookup(role: str) -> SecretStr:
        dsn = role_dsns.get(role)
        if dsn is None:
            names = " or ".join(ROLE_ENV_VARS[role])
            pytest.skip(f"{names} is not set; {role} identity cannot be proven here")
        return dsn

    return lookup


@pytest.fixture(scope="session")
def test_db_dsn() -> SecretStr:
    """``PROVENANCE_TEST_DB_URL`` — ``pv_migrator`` on ``provenance_ci``.

    Returns a :class:`~pydantic.SecretStr`, and that is not decoration.
    **pytest prints every test function argument in a failure header**, so a
    fixture that returns a bare ``str`` writes the live DSN — password included
    — into the transcript of any database test that fails. It was found doing
    exactly that in a ``G3.6`` sabotage run, whose whole purpose is to make
    tests fail. ``SecretStr`` renders as ``SecretStr('**********')``; the value
    is unwrapped inside a function body, where pytest does not print it.

    Refuses any database that is not ``provenance_ci``: the demo database
    ``provenance`` holds seeded state, and a scratch table created there is a
    configuration error that must fail loudly rather than proceed quietly.
    """
    value = _first_set((TEST_DB_ENV_VAR,))
    if not value:
        pytest.skip(f"{TEST_DB_ENV_VAR} is not set; this test needs the CI cluster")
    if not value.rsplit("/", 1)[-1].startswith("provenance_ci"):
        pytest.fail(
            f"{TEST_DB_ENV_VAR} does not point at provenance_ci. These tests create "
            f"and drop _pv_t3_ tables; they may not run against the demo database."
        )
    return SecretStr(value)


@pytest.fixture(scope="module")
def scratch_table(request: pytest.FixtureRequest) -> Iterator[str]:
    """A ``_pv_t3_``-prefixed table name unique to the requesting module."""
    stem = request.module.__name__.rsplit(".", 1)[-1].removeprefix("test_")
    yield f"_pv_t3_{stem}"
