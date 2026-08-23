"""Request and response models for the `/v1` and `/internal/v1` surfaces.

Every request model inherits :class:`~services.control_plane.app.api.schemas.common.ApiRequest`,
which sets ``extra="forbid"``. That is the mechanism behind
``specs/15_API_SPEC.md`` section 2.6 -- "never accepts ``tenant_id`` or
``user_id`` from a request body or query string on any route" -- and it is a
schema rule rather than a per-handler convention precisely so that a route
added later cannot forget it.
"""

from __future__ import annotations

__all__ = ["common", "internal", "public"]
