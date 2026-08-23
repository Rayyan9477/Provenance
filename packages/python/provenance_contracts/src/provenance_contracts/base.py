"""Scalar types, immutability policy, ID generation, and canonical hashing.

Nothing in ``provenance_contracts`` may define its own datetime handling, its
own money type, or its own confidence bound. They live here once.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 6, whose code this module implements rather
  than paraphrases, plus sections 6.1 and 6.2.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, first sub-task, which additionally names
  ``HalfOpenInterval`` — see the recorded deviations below.
- ``quality/23_PHASE_GATES.md`` gate ``G1.5``, the rejection matrix.

This module encodes contract law L1-L6 and L9:

===  =====================================================================
L1   Anything crossing a process, queue or service boundary is a
     ``BoundaryContract`` and carries ``schema_version``.
L2   Money is ``Decimal`` with an explicit currency, never ``float``.
L3   Confidences and weights are ``Decimal`` in ``[0, 1]``, quantised.
L4   Timestamps are timezone-aware UTC. A naive datetime is refused.
L5   Identifiers are constrained so they structurally cannot carry SQL.
L6   Identifiers are UUIDv7 where available, UUIDv4 otherwise, and no code
     may infer ordering from one.
L9   Validated contracts are frozen and their field set is closed.
===  =====================================================================

Recorded deviations from ``specs/11_CONTRACTS.md`` section 6
------------------------------------------------------------
1. **``HalfOpenInterval`` is added.** Section 6 does not declare it; it repeats
   the ``[valid_from, valid_to)`` check inline in six models across sections 8,
   9, 10 and 12. ``EXECUTION/70_TASK_PLAN.md`` T1.5 names it as a base scalar.
   Both are satisfied here: :func:`validate_half_open` is the single rule, and
   :class:`HalfOpenInterval` is the reusable value object. Every model in these
   modules that carries the pair calls the function, so the rule has one
   definition rather than six copies that can drift.

2. **``datetime.UTC`` rather than ``datetime.timezone.utc``.** Mechanical:
   ``ruff`` rule ``UP017`` rewrites the latter at ``target-version = "py312"``,
   and ``ruff format --check`` is a merge-blocking step.

3. **``Sha256Hex`` keeps ``to_lower=True`` and it is inert.** Measured on the
   pinned ``pydantic 2.13.4``: ``StringConstraints`` applies ``to_lower``
   *after* ``pattern``, so an upper-case digest is rejected by the pattern
   before it can be normalised. The flag is retained exactly as section 6
   declares it, and the behaviour is pinned by
   ``tests/test_scalars.py::test_sha256_hex_rejects_upper_case``. Reported
   rather than resolved: making the flag effective would need a
   ``BeforeValidator`` and would change what the type accepts.

4. **No ``strict=True`` on ``Contract``.** T1.5's sub-task line says "strict
   mode on". Section 6's ``ConfigDict`` does not set it, and section 20.3's own
   tests depend on lenient coercion at the edges (``Money(amount=186)``,
   ``score="0.99"``). The spec outranks the task plan. Strictness here is
   delivered by ``extra="forbid"`` plus per-type ``BeforeValidator``s that
   reject rather than coerce — a ``float`` amount, a naive datetime and a
   ``bool`` confidence are all refused, including on the JSON path, which
   section 23 risk 1 required to be measured rather than assumed. The
   measurement is recorded on :class:`Money`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Final, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_MAJORS",
    "UUID_GENERATOR",
    "BlockId",
    "BoundaryContract",
    "Confidence",
    "Contract",
    "CurrencyCode",
    "HalfOpenInterval",
    "IanaTimezone",
    "IdempotencyKey",
    "LocalId",
    "Money",
    "ReasonCode",
    "Revision",
    "SafeIdentifier",
    "Sha256Hex",
    "UtcDatetime",
    "Weight",
    "canonical_json",
    "content_hash",
    "new_id",
    "utc_now",
    "validate_half_open",
]

SCHEMA_VERSION: Final[str] = "1.0"
SUPPORTED_SCHEMA_MAJORS: Final[frozenset[str]] = frozenset({"1"})


# ---------------------------------------------------------------------------
# L6 — identifiers: UUIDv7 where available, UUIDv4 fallback
# ---------------------------------------------------------------------------


def _resolve_uuid7() -> tuple[Callable[[], uuid.UUID], str]:
    """Pick the best available time-ordered UUID generator.

    Order: stdlib ``uuid.uuid7`` (Python 3.14+), then the ``uuid6`` backport
    (returns a stdlib ``uuid.UUID``), then ``uuid_utils`` (Rust, coerced
    through ``str``), then ``uuid.uuid4``. Resolved once at import; the chosen
    name is exported so telemetry can record which one a deployment actually
    used.

    UUIDv7 matters here for a concrete reason: CockroachDB range splits are
    lexicographic on the primary key, and time-ordered UUIDs keep an
    append-heavy table such as ``evidence_items`` from scattering. It is a
    performance preference, never a correctness requirement — **no code may
    infer ordering from an ID.**
    """
    stdlib_uuid7 = getattr(uuid, "uuid7", None)
    if callable(stdlib_uuid7):
        return cast("Callable[[], uuid.UUID]", stdlib_uuid7), "stdlib.uuid7"
    try:
        from uuid6 import uuid7 as _uuid6_uuid7  # type: ignore[import-not-found]

        return cast("Callable[[], uuid.UUID]", _uuid6_uuid7), "uuid6.uuid7"
    except ImportError:
        pass
    try:
        import uuid_utils
    except ImportError:
        pass
    else:
        return (lambda: uuid.UUID(str(uuid_utils.uuid7()))), "uuid_utils.uuid7"
    return uuid.uuid4, "stdlib.uuid4"


_UUID_IMPL, UUID_GENERATOR = _resolve_uuid7()


def new_id() -> uuid.UUID:
    """Allocate an opaque, externally visible identifier."""
    return _UUID_IMPL()


def utc_now() -> datetime:
    """The only clock read in the contracts package."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# L4 — timestamps are timezone-aware UTC
# ---------------------------------------------------------------------------


def _coerce_utc(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "naive datetime rejected: every Provenance timestamp is "
                "timezone-aware UTC; the UI localises, the wire never does"
            )
        return value.astimezone(UTC)
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


def _check_iana_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


IanaTimezone = Annotated[
    str, StringConstraints(min_length=1, max_length=64), BeforeValidator(_check_iana_timezone)
]


# ---------------------------------------------------------------------------
# L3 — confidences and weights
# ---------------------------------------------------------------------------

_CONFIDENCE_QUANTUM: Final[Decimal] = Decimal("0.0001")


def _coerce_confidence(value: Any) -> Any:
    """Accept int/float/str/Decimal, quantise to 4 dp, reject bool and NaN.

    Floats are tolerated here (unlike money) because a model emitting
    ``"confidence": 0.87`` in JSON is normal and the value is advisory. It is
    immediately quantised so two runs producing 0.8700000001 and 0.87 hash
    identically.
    """
    if isinstance(value, bool):
        raise ValueError("bool is not a confidence")
    if isinstance(value, int | float | str | Decimal):
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"not a decimal confidence: {value!r}") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"confidence must be finite, got {value!r}")
        return decimal_value.quantize(_CONFIDENCE_QUANTUM)
    return value


Confidence = Annotated[
    Decimal,
    BeforeValidator(_coerce_confidence),
    Field(ge=Decimal("0"), le=Decimal("1"), description="Decimal in [0,1], 4 dp"),
]

#: Grounding-edge weight. Same domain and coercion as ``Confidence``; a distinct
#: alias so a reader can tell "how sure the extractor was" from "how much this
#: edge counts". Section 23 risk 8: mypy will not catch swapping them, and if
#: the two ever diverge this must become its own ``Annotated`` type.
Weight = Confidence


# ---------------------------------------------------------------------------
# L5 — constrained identifier types that structurally cannot carry SQL
# ---------------------------------------------------------------------------

#: Predicate names, derivation names, transition names. Lowercase snake with
#: dots. A string matching this pattern cannot contain whitespace, quotes,
#: semicolons or parentheses, so it cannot express a SQL statement.
SafeIdentifier = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", max_length=128)
]

#: Machine-readable reason codes are SCREAMING_SNAKE.
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]

#: Within-proposal reference. Two-letter kind prefix keeps cross-reference
#: errors readable: ``ev_`` evidence, ``cl_`` claim, ``cm_`` commitment, ``bm_``
#: belief mutation, ``cf_`` conflict hint, ``tg_`` trigger, ``pc_`` prospective
#: cue, ``un_`` uncertainty, ``ij_`` injection observation.
LocalId = Annotated[
    str, StringConstraints(pattern=r"^(ev|cl|cm|bm|cf|tg|pc|un|ij)_[0-9a-z]{1,24}$")
]

#: Content block reference inside one artifact.
BlockId = Annotated[str, StringConstraints(pattern=r"^blk_[0-9a-z]{1,32}$")]

#: Lower-case hex SHA-256. See recorded deviation 3: ``to_lower`` runs after
#: ``pattern`` on the pinned pydantic, so an upper-case digest is refused
#: rather than normalised.
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", to_lower=True)]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,128}$")]

Revision = Annotated[int, Field(ge=0, le=2**62)]


# ---------------------------------------------------------------------------
# L9 — immutability policy
# ---------------------------------------------------------------------------


class Contract(BaseModel):
    """Base for every Provenance value object.

    ``extra="forbid"`` is load-bearing, not tidiness. It is how an agent is
    prevented from smuggling an ``authority_score``, a ``sql``, or a
    ``_bypass_review`` field into a proposal: an unknown key is a hard
    validation error, not a silently ignored one.

    ``frozen=True`` means a validated contract cannot be edited in place after
    a check has passed. Collections are declared as tuples for the same reason.
    Models carrying a JSON payload are frozen but not hashable (section 23
    risk 7); use :func:`content_hash` when a digest is needed.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        ser_json_bytes="base64",
        arbitrary_types_allowed=False,
    )


class BoundaryContract(Contract):
    """L1. Anything that crosses a process, queue, or service boundary.

    ``schema_version`` carries a default rather than being required: section
    20.9 asserts ``model_fields["schema_version"].default == SCHEMA_VERSION``,
    and every producer in this build emits the current version. What is refused
    is a *present but unusable* value — ``null``, empty, malformed, or a major
    this build does not understand — because that is the shape the failure
    takes on a wire, in a queue, or in a persisted payload column that survived
    a deploy. ``EXECUTION/70_TASK_PLAN.md`` T1.5 asks for a required field; the
    spec outranks it and the discrepancy is recorded in
    ``tests/test_scalars.py``.
    """

    schema_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        major = value.split(".", 1)[0]
        if major not in SUPPORTED_SCHEMA_MAJORS:
            raise ValueError(
                f"unsupported schema major {major!r} for {cls.__name__}; "
                f"this build understands {sorted(SUPPORTED_SCHEMA_MAJORS)}"
            )
        return value


# ---------------------------------------------------------------------------
# L2 — money
# ---------------------------------------------------------------------------


class Money(Contract):
    """An exact monetary amount with an explicit currency.

    Wire form is a JSON **string**: ``{"amount": "186.00", "currency": "USD"}``.
    ``json_schema_extra`` forces ``"type": "string"`` into the generated JSON
    Schema, which is what the Tier E structured-output call is constrained by,
    so the model is told to emit a string rather than a number.

    Rejects: float, bool, non-finite, more than four decimal places. There is
    no rounding here on purpose — silently rounding an obligation is exactly
    the failure mode this product exists to prevent.

    Section 23 risk 1 required this to be **measured, not assumed**: whether
    pydantic-core hands a ``mode="before"`` validator a ``float`` or a
    pre-converted ``Decimal`` for a bare JSON number is a version-dependent
    implementation detail, and if it pre-converted, the guard would be silently
    dead on the JSON path. Measured on the pinned ``pydantic 2.13.4`` /
    ``pydantic-core 2.46.4``: ``model_validate_json('{"amount": 186.00}')``
    hands the validator a Python ``float``, so ``_reject_float_amount`` fires
    and the bare number is refused. ``strict=True`` — the fallback the risk
    prescribes — is therefore **not** set, because it is not needed and would
    tighten the Python-side coercions section 20.3 depends on.
    ``tests/test_scalars.py::test_money_rejects_a_bare_json_number`` pins that
    measurement: if a future pydantic-core pre-converts, that test goes red and
    ``strict=True`` is the documented answer.
    """

    amount: Decimal = Field(
        json_schema_extra={"type": "string", "pattern": r"^-?\d{1,16}(\.\d{1,4})?$"}
    )
    currency: CurrencyCode

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float_amount(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("bool is not a monetary amount")
        if isinstance(value, float):
            raise ValueError(
                "float is not an acceptable monetary amount; send a JSON string "
                'such as "186.00" so no binary rounding error can enter an obligation'
            )
        if isinstance(value, int | str):
            try:
                value = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"not a decimal amount: {value!r}") from exc
        return value

    @field_validator("amount")
    @classmethod
    def _validate_scale(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("monetary amount must be finite")
        # `exponent` is `int | Literal["n", "N", "F"]`; the non-int spellings
        # belong to NaN/Infinity, which the finiteness check above has already
        # refused. The isinstance narrows it for the type checker rather than
        # asserting something new.
        exponent = value.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > 4:
            raise ValueError(
                f"{value} has more than 4 decimal places; DECIMAL(20,4) is the "
                "canonical storage scale and rounding must happen at the source"
            )
        if abs(value) >= Decimal("1e16"):
            raise ValueError(f"{value} exceeds DECIMAL(20,4) precision")
        return value

    def same_currency_as(self, other: Money) -> bool:
        return self.currency == other.currency

    def require_same_currency(self, other: Money) -> None:
        if not self.same_currency_as(other):
            raise ValueError(
                f"currency mismatch {self.currency} vs {other.currency}; "
                "the Kernel refuses cross-currency arithmetic without an "
                "explicit conversion event"
            )

    def __add__(self, other: Money) -> Money:
        self.require_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self.require_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount}"


# ---------------------------------------------------------------------------
# Bitemporal validity — [valid_from, valid_to), valid_to = NULL is open-ended
# ---------------------------------------------------------------------------


def validate_half_open(valid_from: datetime | None, valid_to: datetime | None) -> None:
    """Enforce ``[valid_from, valid_to)``. ``valid_to = None`` is open-ended.

    One rule, one definition. ``specs/11_CONTRACTS.md`` repeats this check
    inline in ``EvidenceCandidate``, ``TemporalInterpretation``,
    ``ClaimAssertion``, ``BeliefMutation`` and elsewhere; six copies of a
    comparison is five chances for one of them to be written ``<`` instead of
    ``<=``. Every model in these modules that carries the pair calls this.

    ``valid_to == valid_from`` is rejected as well as ``valid_to < valid_from``:
    the interval is half-open, so an equal pair describes a fact that was true
    for zero elapsed time, which is never what an extractor meant. An absent
    upper bound means "still true as far as we know", which is a different
    statement from "true until now" and must stay distinguishable in the
    lineage chain.
    """
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError(
            "validity intervals are half-open [valid_from, valid_to); valid_to must be "
            "strictly after valid_from, and valid_to = None means open-ended"
        )


class HalfOpenInterval(Contract):
    """A bitemporal validity window as a value object.

    Used where an interval travels on its own. Models that carry the pair as
    two of their own fields — because the persisted row does — call
    :func:`validate_half_open` instead, so both spellings enforce one rule.
    """

    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> HalfOpenInterval:
        validate_half_open(self.valid_from, self.valid_to)
        return self

    @property
    def is_open_ended(self) -> bool:
        """True when the upper bound is absent: still true as far as we know."""
        return self.valid_to is None

    def contains(self, moment: datetime) -> bool:
        """Half-open membership: the lower bound is in, the upper bound is out."""
        if self.valid_from is not None and moment < self.valid_from:
            return False
        return not (self.valid_to is not None and moment >= self.valid_to)


# ---------------------------------------------------------------------------
# Canonical serialisation and hashing
# ---------------------------------------------------------------------------


def canonical_json(model: BaseModel, *, exclude: frozenset[str] | None = None) -> bytes:
    """Deterministic bytes for hashing and signature comparison.

    Sorted keys, no whitespace, ``None`` omitted, ``Decimal``s as strings,
    datetimes as ISO-8601 with an explicit offset. Two structurally equal
    contracts produce identical bytes on any machine and any Python build.
    """
    payload = model.model_dump(
        mode="json", exclude_none=True, exclude=set(exclude) if exclude else None
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def content_hash(model: BaseModel, *, exclude: frozenset[str] | None = None) -> str:
    """Lower-case hex SHA-256 over :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(model, exclude=exclude)).hexdigest()
