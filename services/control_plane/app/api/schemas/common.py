"""Shared request/response primitives.

Authority: ``specs/15_API_SPEC.md`` sections 1.2 and 1.3.

``ApiRequest`` sets ``extra="forbid"`` and every request model in this package
inherits it. That is not tidiness -- section 2.6 says Provenance "never
accepts ``tenant_id`` or ``user_id`` from a request body or query string on any
route, public or internal", and ``extra="forbid"`` is what turns that rule into
a ``422`` at the schema layer instead of a code review convention. Section 4.2
adds that ``reason: "extra_forbidden"`` on ``user_id`` should be treated as a
security signal in dashboards, not merely a client bug.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

__all__ = [
    "ApiRequest",
    "ApiResponse",
    "Money",
    "MoneyAmount",
    "Ratio",
    "page_of",
]


class ApiRequest(BaseModel):
    """Every request body. Unknown fields are rejected, never ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiResponse(BaseModel):
    """Every response body.

    ``extra="allow"`` is deliberate on the *response* side and only there:
    section 16.2 makes additive optional fields non-breaking, and the read
    ports return projections that Phase 5 will widen. Forbidding extras here
    would turn a harmless additive column into a 500.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


#: Section 1.3: a decimal string matching the DECIMAL(20,4) surface. Never a
#: JSON number -- IEEE-754 cannot represent a cent reliably and money that is
#: wrong by a cent is money that is wrong.
MoneyAmount = Annotated[str, StringConstraints(pattern=r"^-?\d{1,16}(\.\d{1,4})?$")]

#: Confidence, weight and authority: decimal strings in [0,1], 4 fractional
#: digits, mapping to DECIMAL(5,4).
Ratio = Annotated[str, StringConstraints(pattern=r"^(0(\.\d{1,4})?|1(\.0{1,4})?)$")]


class Money(ApiResponse):
    """``{"currency": "USD", "amount": "1800.0000"}``."""

    model_config = ConfigDict(extra="forbid")

    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    amount: MoneyAmount

    @field_serializer("amount")
    def _as_decimal_string(self, value: str) -> str:
        # Round-trip through Decimal so a caller that handed us "1800" and one
        # that handed us "1800.0000" serialise identically. `float` is never
        # involved on this path.
        return str(Decimal(value).quantize(Decimal("0.0001")))


def page_of(items: list[Any], page: Any) -> dict[str, Any]:
    """The section 5.2 envelope, built in one place."""
    return {"items": items, "page": page}


class PageQuery(ApiRequest):
    """``limit`` and ``cursor``, for documentation of the query surface."""

    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None
