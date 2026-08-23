"""The port implementations: protocols on one side, real systems on the other.

Authority: ``services/control_plane/app/api/ports.py`` (the 47 methods) and
``implementation/00_IMPLEMENTATION_MAP.md`` on write-path ownership.

The shape, in one paragraph
----------------------------
``ports.py`` states the surface the routes need. This package binds it:
:class:`~.read.SqlReadPort` to ``provenance_db.repositories``,
:class:`~.write.KernelWritePort` and :class:`~.internal.KernelInternalPort` to
``app/memory_kernel``, :class:`~.directory.SqlUserDirectory` and
:class:`~.directory.SqlCapabilityStore` to the two identity lookups, and
:class:`~.catalog.DbHealth` to the readiness bit ``GET /v1/version``
discloses. ``main.build_runtime`` assembles them; nothing here reads the
environment and nothing here connects at import.

Two invariants hold across the whole package, and both are asserted by
``services/control_plane/tests/api/test_port_adapters.py``:

1. **No SQL outside ``directory.py`` and ``catalog.py``**, and the statements
   in those two never bind an owner -- they are where a scope comes from, not
   somewhere one is applied. Every user-scoped predicate has exactly one
   definition, in ``provenance_db.repositories``.
2. **A method with no backing raises**, and the message names the subsystem it
   needs. ``UNBOUND`` is the register; returning ``None`` or ``[]`` instead
   would render as "no data" and be indistinguishable from a real empty
   result.
"""

from __future__ import annotations

from services.control_plane.app.api.adapters.catalog import (
    ConnectionSource,
    DbHealth,
    agent_view_names,
)
from services.control_plane.app.api.adapters.directory import (
    SqlCapabilityStore,
    SqlUserDirectory,
)
from services.control_plane.app.api.adapters.internal import KernelInternalPort
from services.control_plane.app.api.adapters.read import DEFAULT_FEATURE_FLAGS, SqlReadPort
from services.control_plane.app.api.adapters.unbound import UNBOUND, unbound
from services.control_plane.app.api.adapters.write import KernelWritePort

__all__ = [
    "DEFAULT_FEATURE_FLAGS",
    "UNBOUND",
    "ConnectionSource",
    "DbHealth",
    "KernelInternalPort",
    "KernelWritePort",
    "SqlCapabilityStore",
    "SqlReadPort",
    "SqlUserDirectory",
    "agent_view_names",
    "unbound",
]
