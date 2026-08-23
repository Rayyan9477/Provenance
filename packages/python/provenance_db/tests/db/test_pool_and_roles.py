"""One connection pool per SQL role, proven against the live cluster — T3.1, L2.

Authority
---------
- ``quality/23_PHASE_GATES.md`` section 9, ``G3.1``: the battery prints
  ``app pool: current_user = pv_app_reader_writer``,
  ``kernel pool: current_user = pv_kernel_writer``,
  ``agent pool: current_user = pv_agent_reader`` and reports ``3 passed``.
- ``EXECUTION/70_TASK_PLAN.md`` T3.1 — six tests, of which three are the
  identity assertions above; the other three are the structural guarantees the
  same task lists: a pool cannot be reconfigured to another role after
  construction, no pool is constructed from a URL passed as a function
  argument by application code, and the migrator pool is unavailable to the
  running application.
- ``specs/10_DATABASE_DDL.md`` section 15 — the grants that make the three
  identities mean something.
- ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*: ``pv_ops_reader`` exists,
  and ``provenance/db`` carries five keys.

Why ``current_user()`` is worth a round trip
--------------------------------------------
It is the cheapest possible proof that the grants are real. Everything else in
this repository that claims least privilege — the agent reaching only the five
``_v1`` views, the Kernel being the only canonical writer — is a claim about
*which role* a connection authenticates as. If that is not checked once at
startup, every later claim rests on a configuration file nobody re-read.

Deliberately **not** asserted here: what each role may read or write. The
canonical tables do not exist yet (Phase 2 is in flight), and a grant
assertion against an empty schema would pass vacuously. ``G11.x`` owns the
grant boundary; this file owns identity.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from provenance_db import pools as pools_module
from provenance_db.pools import (
    APPLICATION_ROLES,
    MigratorPoolNotForApplicationError,
    RolePool,
    RolePoolImmutableError,
    SqlRole,
    application_pool,
)
from provenance_db.urls import MappingDsnSource

pytestmark = pytest.mark.db

RoleLookup = Callable[[str], SecretStr]


async def _assert_identity(dsn: SecretStr, role: SqlRole, label: str) -> None:
    source = MappingDsnSource({role.value: dsn})
    async with RolePool(role, source) as pool:
        who = await pool.current_user()
    print(f"{label} pool: current_user = {who}")
    assert who == role.value


async def test_app_pool_authenticates_as_pv_app_reader_writer(require_role: RoleLookup) -> None:
    await _assert_identity(require_role(SqlRole.APP.value), SqlRole.APP, "app")


async def test_kernel_pool_authenticates_as_pv_kernel_writer(require_role: RoleLookup) -> None:
    await _assert_identity(require_role(SqlRole.KERNEL.value), SqlRole.KERNEL, "kernel")


async def test_agent_pool_authenticates_as_pv_agent_reader(require_role: RoleLookup) -> None:
    await _assert_identity(require_role(SqlRole.AGENT.value), SqlRole.AGENT, "agent")


def test_a_pool_cannot_be_reconfigured_to_another_role_after_construction() -> None:
    """The role boundary is a runtime fact, not a comment.

    A pool that could be re-pointed at ``pv_kernel_writer`` after construction
    would make "only the Kernel writes canonical tables" a statement about
    call order rather than about credentials.
    """
    source = MappingDsnSource(
        {SqlRole.AGENT.value: SecretStr("postgresql://host/db?sslmode=verify-full")}
    )
    pool = RolePool(SqlRole.AGENT, source)
    assert pool.role is SqlRole.AGENT
    with pytest.raises(RolePoolImmutableError):
        pool.role = SqlRole.KERNEL  # type: ignore[misc]
    with pytest.raises(RolePoolImmutableError):
        pool._role = SqlRole.KERNEL  # type: ignore[attr-defined]
    assert pool.role is SqlRole.AGENT


def test_no_pool_is_constructed_from_a_url_passed_by_application_code() -> None:
    """Every URL is resolved from the named secret key, never from an argument.

    Asserted on the signatures rather than by convention: a ``dsn=`` parameter
    on any public constructor in ``pools.py`` is the whole failure mode, since
    it is what lets a caller pass a URL a settings object never saw — and what
    lets one land in a log line, a stack frame or a test fixture.
    """
    banned = {"dsn", "url", "conninfo", "connection_string", "database_url"}
    for name, obj in vars(pools_module).items():
        if name.startswith("_") or not (inspect.isclass(obj) or inspect.isfunction(obj)):
            continue
        if getattr(obj, "__module__", None) != pools_module.__name__:
            continue
        target = obj.__init__ if inspect.isclass(obj) else obj
        parameters = set(inspect.signature(target).parameters)
        assert not (parameters & banned), f"{name} accepts a raw URL: {parameters & banned}"

    source = Path(pools_module.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source, "pools.py must not read the environment; urls.py resolves"
    assert "os.getenv" not in source


def test_the_migrator_pool_is_refused_to_application_code() -> None:
    """``pv_migrator`` is DDL only, and is never used by the running service.

    The import-linter contract that would forbid ``services.control_plane.app``
    from importing it cannot be added by this task — ``.importlinter`` is
    Integrator-owned — so the refusal is enforced at construction as well, and
    reported so the contract line can follow.
    """
    source = MappingDsnSource(
        {role.value: SecretStr("postgresql://host/db?sslmode=verify-full") for role in SqlRole}
    )
    assert SqlRole.MIGRATOR not in APPLICATION_ROLES
    assert SqlRole.OPS not in APPLICATION_ROLES
    assert APPLICATION_ROLES == (SqlRole.APP, SqlRole.KERNEL, SqlRole.AGENT)

    with pytest.raises(MigratorPoolNotForApplicationError):
        application_pool(SqlRole.MIGRATOR, source)
    with pytest.raises(MigratorPoolNotForApplicationError):
        application_pool(SqlRole.OPS, source)
    assert application_pool(SqlRole.KERNEL, source).role is SqlRole.KERNEL
