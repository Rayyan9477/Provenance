"""Readiness and the schema catalogue — the two reads that own no user data.

Authority
---------
- ``specs/15_API_SPEC.md`` section 8.2 (``GET /v1/version`` and ``db_ok``).
- ``services/control_plane/app/api/ports.py::ReadPort.agent_view_names``:
  "Read from ``information_schema.views``. T8.7: so ``G11.6``'s diff compares
  the database against itself through the API rather than against a hard-coded
  list."
- ``CANONICAL_DECISIONS.md`` -> *Agent-safe views*: the five ``_v1`` names.

Why these two statements live here and not in ``provenance_db.repositories``
-----------------------------------------------------------------------------
That package's guard requires every public read to take a ``Principal`` or an
explicit ``(tenant_id, user_id)`` pair, and it is right to. Neither statement
below has an owner to take: ``SELECT 1`` touches nothing, and
``information_schema.views`` is server metadata that is identical for every
user in the cluster. There is no scoping predicate here to duplicate, which is
the property that makes the exemption safe rather than convenient --
``tests/api/test_port_adapters.py`` asserts exactly that by checking which
tables the statements in this module read.

``db_ok`` is a bit, not a query
--------------------------------
Section 8.2 makes ``GET /v1/version`` unauthenticated so a judge can ``curl``
it with nothing but the URL. A readiness field that ran a query per call would
therefore be a free, unauthenticated availability oracle against CockroachDB
for anyone who found the URL -- and a cheap amplifier: one HTTP request, one
database round trip, no credential. :class:`DbHealth` separates *observing*
the cluster from *reporting* what was last observed. ``ok()`` is synchronous,
allocation-free and touches nothing; ``refresh()`` is the only thing that
connects, and it is driven by the application's startup and by a background
task on a fixed interval.

The initial value is ``False``, deliberately. Before anything has been
observed the honest answer is "not known to be ready", and optimism in a
readiness bit is how a load balancer sends traffic to a process that has never
reached its database.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import AbstractAsyncContextManager
from typing import Any, Final, Protocol

__all__ = [
    "AGENT_VIEW_NAMES_SQL",
    "READINESS_PROBE_SQL",
    "ConnectionSource",
    "DbHealth",
    "agent_view_names",
]

#: How often the background refresher observes the cluster. Long enough that
#: the probe is not itself a load, short enough that ``db_ok`` is not stale
#: through a whole demo.
DEFAULT_PROBE_INTERVAL_SECONDS: Final[float] = 15.0

#: The cheapest statement that proves a connection was obtained and a
#: round trip completed. Not ``SELECT current_user``: that is the grant proof
#: ``G3.1`` asserts once at startup, and repeating it every fifteen seconds
#: would put the role name into the query log forever for no extra signal.
READINESS_PROBE_SQL = """
    SELECT 1 AS ok
"""

#: ``T8.7``. The five agent-safe views, read from the catalogue rather than
#: from a constant, so ``G11.6`` compares the database against itself.
#:
#: A hard-coded list would pass this check on a cluster where the views had
#: been dropped -- which is the only interesting case. ``table_schema`` is
#: bound rather than interpolated for the same reason every other statement
#: in this repository binds its parameters.
AGENT_VIEW_NAMES_SQL = """
    SELECT table_name
    FROM information_schema.views
    WHERE table_schema = %(schema)s
      AND table_name LIKE 'agent\\_%%\\_v1'
    ORDER BY table_name
"""


class ConnectionSource(Protocol):
    """Anything that hands out a connection as an async context manager.

    ``provenance_db.pools.RolePool`` satisfies this, and so does a recording
    double in the hermetic suites. The adapters depend on the protocol rather
    than on ``RolePool`` so that every one of them is drivable with no cluster
    -- which is what makes "did this method bind the owner?" a unit test
    rather than an integration test.
    """

    def connection(self) -> AbstractAsyncContextManager[Any]: ...


class DbHealth:
    """The cached readiness bit behind ``GET /v1/version``.

    Not a dataclass and not a closure: the bit has to be mutable, the reader
    has to be a plain callable with no arguments (``Dependencies.db_ok`` is
    typed ``Callable[[], bool]``), and the refresher has to be startable and
    stoppable by the application lifecycle. A small object with three methods
    is the shape that admits all three without a module-level global.
    """

    __slots__ = ("_interval", "_ok", "_source", "_task")

    def __init__(
        self, source: ConnectionSource, *, interval: float = DEFAULT_PROBE_INTERVAL_SECONDS
    ) -> None:
        self._source = source
        self._interval = interval
        self._ok = False
        self._task: asyncio.Task[None] | None = None

    def ok(self) -> bool:
        """What was last observed. Never a query; see the module docstring."""
        return self._ok

    async def refresh(self) -> bool:
        """Observe the cluster once and record the result.

        Every exception is swallowed into ``False`` on purpose. This is a
        *readiness* probe: a refused connection, an expired credential, a
        statement timeout and a DNS failure are all "not ready", and letting
        any of them escape would take down the background task that is
        supposed to notice when the condition clears.
        """
        try:
            async with self._source.connection() as conn, conn.cursor() as cursor:
                await cursor.execute(READINESS_PROBE_SQL)
                await cursor.fetchall()
        except Exception:
            self._ok = False
        else:
            self._ok = True
        return self._ok

    async def start(self) -> None:
        """Probe once, then keep probing in the background."""
        await self.refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="provenance-db-health")

    async def stop(self) -> None:
        """Cancel the refresher and clear the bit. Safe when it never started.

        Clearing is not tidiness. Once nothing is observing the cluster any
        more, ``true`` is a claim about a measurement that stopped happening,
        and a process draining its last requests should answer ``db_ok:
        false`` on the way out rather than assert a readiness it is no longer
        checking.
        """
        self._ok = False
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:  # pragma: no cover - a timer, exercised by start/stop
        while True:
            await asyncio.sleep(self._interval)
            await self.refresh()


async def agent_view_names(source: ConnectionSource, *, schema: str = "public") -> list[str]:
    """The ``agent_*_v1`` views this database actually has.

    Returns ``[]`` only when the catalogue genuinely holds none -- which is
    itself the finding ``G11.6`` is looking for, and is why this is one of the
    few reads in the system where an empty list is a real answer rather than a
    stand-in for "not implemented".
    """
    async with source.connection() as conn, conn.cursor() as cursor:
        await cursor.execute(AGENT_VIEW_NAMES_SQL, {"schema": schema})
        rows = await cursor.fetchall()
    return [str(row[0]) for row in rows]
