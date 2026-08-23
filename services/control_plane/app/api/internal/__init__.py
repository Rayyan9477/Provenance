"""The thirteen `/internal/v1` routes of ``specs/15_API_SPEC.md`` section 9.

Reachable only by ``provenance-agent-runtime`` and ``provenance-workers``, and
only while holding a server-resolved capability. No route here reads identity
from a request body.
"""

from __future__ import annotations

__all__ = ["routes"]
