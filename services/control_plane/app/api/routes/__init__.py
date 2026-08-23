"""The thirty-one public `/v1` routes of ``specs/15_API_SPEC.md`` section 8.

Split by resource rather than by verb, so the file a reader opens is the one
named after the thing they are looking at. ``system`` is the only
unauthenticated router; every other module here is mounted behind the
route-class check and the principal resolution in ``app/api/app.py``.
"""

from __future__ import annotations

__all__ = ["actions", "artifacts", "judge", "memory", "system"]
