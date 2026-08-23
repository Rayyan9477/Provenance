"""One connection pool per SQL role — T3.1.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 15 (grants) and section 12 (write-path
  ownership): four runtime roles plus the migrator, in ascending privilege
  over canonical tables.
- ``CANONICAL_DECISIONS.md`` -> *Names and counts* (the five role names) and
  -> *Hero commit canon* (``pv_ops_reader`` is created, is strictly read-only,
  and is an operator/CI credential rather than an App Runner pool).
- ``EXECUTION/70_TASK_PLAN.md`` T3.1 and ``quality/23_PHASE_GATES.md`` ``G3.1``.

The point of a pool per role
----------------------------
"Only the Memory Kernel writes canonical tables" is a **grant**, not a
convention (``10_DATABASE_DDL.md`` section 12). A single pool with a
role-switching argument would turn that grant back into a convention, because
the property would then depend on call order rather than on credentials. Here
each pool carries its role as an immutable attribute and authenticates as that
role, so "which role wrote this row" has an answer at run time.

:meth:`RolePool.current_user` is the cheapest possible proof that the grants
are real: one round trip, asserted by ``G3.1``.

The migrator is not an application pool
---------------------------------------
``pv_migrator`` is DDL only and is never used by the running service; the same
is true of ``pv_ops_reader``, which is an operator and CI credential.
:func:`application_pool` refuses both, and ``APPLICATION_ROLES`` is the list
that refusal reads.

T3.1 also asks for that boundary as an import-linter contract — importing the
migrator pool from ``services/control_plane/app/**`` should be a lint
violation, not a convention. Phase 3 does **not** deliver that, and the reason
is worth stating rather than leaving as a silent gap: an import-linter
``forbidden`` contract names *modules*, not symbols, so it cannot forbid one
constructor while the rest of ``pools.py`` — which the control plane must
import for its own pools — stays importable. Making the contract expressible
means moving the migrator constructor into a module of its own, and that is a
layout decision this task should not take unilaterally. What is delivered is
the runtime refusal in :func:`application_pool`, which fails at construction
rather than at review.

Once such a module exists, the contract is one block::

    [importlinter:contract:app-cannot-open-a-migrator-pool]
    name = the control plane never opens a DDL connection
    type = forbidden
    source_modules =
        services.control_plane.app
    forbidden_modules =
        provenance_db.<the module holding the migrator constructor>

TLS, statement timeout, sizing
------------------------------
``sslmode=verify-full`` is enforced by :mod:`provenance_db.urls` before a pool
is built. Each connection is configured with ``statement_timeout``, so a query
that hangs surfaces as ``57014`` — a bounded failure the retry classifier
already knows about — rather than as a pool exhausted by one stuck statement.
The sizing defaults mirror ``COCKROACH_POOL_MIN`` / ``COCKROACH_POOL_MAX`` /
``COCKROACH_STATEMENT_TIMEOUT_MS`` in ``provenance_contracts.settings``.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from types import TracebackType
from typing import Any, Final, final

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from provenance_db.urls import DsnSource, resolve_role_dsn

__all__ = [
    "APPLICATION_ROLES",
    "DEFAULT_MAX_SIZE",
    "DEFAULT_MIN_SIZE",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "MigratorPoolNotForApplicationError",
    "PoolNotOpenError",
    "RolePool",
    "RolePoolImmutableError",
    "SqlRole",
    "application_pool",
]


class SqlRole(StrEnum):
    """The five SQL roles. The values are the login names on the cluster."""

    MIGRATOR = "pv_migrator"
    APP = "pv_app_reader_writer"
    KERNEL = "pv_kernel_writer"
    AGENT = "pv_agent_reader"
    OPS = "pv_ops_reader"


#: The roles a running application process may open a pool for. ``MIGRATOR``
#: is DDL only; ``OPS`` is a human operator and CI credential.
APPLICATION_ROLES: Final[tuple[SqlRole, ...]] = (SqlRole.APP, SqlRole.KERNEL, SqlRole.AGENT)

DEFAULT_MIN_SIZE: Final[int] = 2
DEFAULT_MAX_SIZE: Final[int] = 10
DEFAULT_STATEMENT_TIMEOUT_MS: Final[int] = 15_000


class RolePoolImmutableError(AttributeError):
    """An attempt to reconfigure a pool after construction."""


class PoolNotOpenError(RuntimeError):
    """The pool was used before :meth:`RolePool.open` or after ``close``."""


class MigratorPoolNotForApplicationError(RuntimeError):
    """``application_pool`` was asked for a role the application may not hold."""

    def __init__(self, role: SqlRole) -> None:
        self.role = role
        allowed = ", ".join(member.value for member in APPLICATION_ROLES)
        super().__init__(
            f"{role.value} is not an application role. pv_migrator runs DDL only and "
            f"pv_ops_reader is an operator and CI credential; neither belongs in a "
            f"serving process. The application roles are: {allowed}."
        )


@final
class RolePool:
    """A connection pool bound to exactly one SQL role, for its whole life.

    The role is fixed at construction and every attribute is read-only
    afterwards: ``__setattr__`` refuses, so a caller cannot re-point an agent
    pool at the kernel role between two statements. The DSN is resolved from
    the named secret key through :func:`provenance_db.urls.resolve_role_dsn`
    and is never accepted as an argument.

    The underlying ``psycopg_pool`` object is built in :meth:`open`, not in
    ``__init__``: constructing a pool is a connection-establishing act and
    belongs inside the event loop that will use it.
    """

    __slots__ = ("_max_size", "_min_size", "_pool", "_role", "_source", "_statement_timeout_ms")

    def __init__(
        self,
        role: SqlRole,
        source: DsnSource,
        *,
        min_size: int = DEFAULT_MIN_SIZE,
        max_size: int = DEFAULT_MAX_SIZE,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    ) -> None:
        if max_size < min_size:
            raise ValueError(f"max_size ({max_size}) is below min_size ({min_size})")
        if statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be positive")
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_min_size", min_size)
        object.__setattr__(self, "_max_size", max_size)
        object.__setattr__(self, "_statement_timeout_ms", statement_timeout_ms)
        object.__setattr__(self, "_pool", None)

    # -- immutability -----------------------------------------------------

    def __setattr__(self, name: str, value: object) -> None:
        raise RolePoolImmutableError(
            f"a RolePool is bound to {self.role.value} for its whole life; "
            f"assigning {name!r} would make the role boundary a matter of call "
            f"order rather than of credentials. Construct a second pool instead."
        )

    def __delattr__(self, name: str) -> None:
        raise RolePoolImmutableError(f"a RolePool is immutable; cannot delete {name!r}")

    # -- identity ---------------------------------------------------------

    @property
    def role(self) -> SqlRole:
        """The SQL role this pool authenticates as. Fixed at construction."""
        role: SqlRole = object.__getattribute__(self, "_role")
        return role

    @property
    def is_open(self) -> bool:
        return object.__getattribute__(self, "_pool") is not None

    def __repr__(self) -> str:
        state = "open" if self.is_open else "closed"
        return f"<RolePool {self.role.value} {state}>"

    # -- lifecycle --------------------------------------------------------

    async def open(self) -> RolePool:
        """Resolve the DSN, build the pool and wait for the first connection."""
        if self.is_open:
            return self
        dsn = resolve_role_dsn(self.role.value, object.__getattribute__(self, "_source"))
        timeout_ms: int = object.__getattribute__(self, "_statement_timeout_ms")

        async def configure(conn: AsyncConnection[Any]) -> None:
            # A statement here would leave the connection INTRANS and the pool
            # would discard it, so pooled connections are autocommit and every
            # transaction is opened explicitly by
            # `provenance_db.retry.run_in_serializable_tx`. That is also the
            # posture psycopg recommends: an implicit transaction opened by a
            # stray SELECT is a transaction nobody decided to start.
            await conn.execute(f"SET statement_timeout = '{timeout_ms}ms'")

        pool: AsyncConnectionPool[AsyncConnection[Any]] = AsyncConnectionPool(
            conninfo=dsn.get_secret_value(),
            min_size=object.__getattribute__(self, "_min_size"),
            max_size=object.__getattribute__(self, "_max_size"),
            kwargs={"autocommit": True},
            configure=configure,
            open=False,
            name=f"provenance-{self.role.value}",
        )
        await pool.open(wait=True)
        object.__setattr__(self, "_pool", pool)
        return self

    async def close(self) -> None:
        pool = object.__getattribute__(self, "_pool")
        if pool is not None:
            object.__setattr__(self, "_pool", None)
            await pool.close()

    async def __aenter__(self) -> RolePool:
        return await self.open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- use --------------------------------------------------------------

    def connection(self) -> AbstractAsyncContextManager[AsyncConnection[Any]]:
        """One connection from the pool, as an async context manager."""
        pool = object.__getattribute__(self, "_pool")
        if pool is None:
            raise PoolNotOpenError(
                f"the {self.role.value} pool is not open; call open() or use "
                f"`async with RolePool(...)`"
            )
        connection: AbstractAsyncContextManager[AsyncConnection[Any]] = pool.connection()
        return connection

    async def current_user(self) -> str:
        """``SELECT current_user`` — the health call ``G3.1`` asserts on.

        One round trip at startup is the whole cost, and it is the only
        evidence that the grants in ``10_DATABASE_DDL.md`` section 15 were
        applied to the credential this process is actually holding.
        """
        async with self.connection() as conn:
            cursor = await conn.execute("SELECT current_user")
            row = await cursor.fetchone()
        if row is None:  # pragma: no cover - the server always answers this
            raise RuntimeError("SELECT current_user returned no row")
        return str(row[0])


def application_pool(
    role: SqlRole,
    source: DsnSource,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    max_size: int = DEFAULT_MAX_SIZE,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> RolePool:
    """A pool for one of the three roles a serving process may hold.

    Raises:
        MigratorPoolNotForApplicationError: for ``pv_migrator`` or
            ``pv_ops_reader``.
    """
    if role not in APPLICATION_ROLES:
        raise MigratorPoolNotForApplicationError(role)
    return RolePool(
        role,
        source,
        min_size=min_size,
        max_size=max_size,
        statement_timeout_ms=statement_timeout_ms,
    )
