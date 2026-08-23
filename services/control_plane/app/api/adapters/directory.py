"""Identity resolution — the two reads that *produce* a scope.

Authority
---------
- ``specs/15_API_SPEC.md`` section 2.5: a verified token whose ``sub`` has no
  ``users`` row returns ``403 USER_NOT_PROVISIONED``. Provenance never
  auto-creates a user on an API call -- that would let any pool member mint a
  tenant by hitting ``GET /v1/me``.
- ``specs/15_API_SPEC.md`` section 3.3: the four capability objects, and the
  rule that ownership comes from the *row*, never from the request.
- ``services/control_plane/app/auth/capabilities.py::CapabilityRecord``, whose
  shape these statements project onto.

Why these two statements are not in ``provenance_db.repositories``
-------------------------------------------------------------------
Every read in that package takes a ``Principal`` or an explicit
``(tenant_id, user_id)`` pair, enforced by
``test_no_read_signature_omits_both_a_principal_and_an_explicit_pair``. These
two cannot: they are what a scope is *made of*. ``by_cognito_sub`` turns a
verified token subject into an owner pair, and ``load`` turns a capability id
into one. Asking them for the pair they exist to produce is circular, and
weakening the repository guard with an exemption list would be worse than
placing two statements where the exemption is visible.

Nothing is duplicated by the placement. There is no scoping predicate in
either statement to have a second copy of -- the ``users`` lookup is keyed on
``cognito_sub``, which is unique cluster-wide, and each capability lookup is
keyed on its own primary key. ``tests/api/test_port_adapters.py`` asserts that
property directly rather than trusting this paragraph.

The rule these statements enforce, restated
--------------------------------------------
``tenant_id`` and ``user_id`` are **selected**, never bound. A caller supplies
a ``sub`` or a capability id and receives whichever owner the row names. There
is no parameter through which a caller can assert an owner, which is contract
law L10 expressed as a query shape: a machine client never names a user.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.ports import UserRecord
from services.control_plane.app.auth.capabilities import CapabilityRecord

__all__ = [
    "AGENT_RUN_CAPABILITY_SQL",
    "INGEST_ALIAS_CAPABILITY_SQL",
    "INTENT_CAPABILITY_SQL",
    "TRIGGER_CAPABILITY_SQL",
    "USER_BY_SUB_SQL",
    "SqlCapabilityStore",
    "SqlUserDirectory",
]

#: Section 2.5. ``uq_users_cognito_sub`` makes this at most one row.
#:
#: ``status`` is projected and checked, not merely returned: a disabled
#: account whose token is still inside its five-minute validity window would
#: otherwise keep working until the token expired, and "disabled" that takes
#: effect in five minutes is not disabled.
USER_BY_SUB_SQL = """
    SELECT u.id AS user_id, u.tenant_id, u.cognito_sub, u.email,
           u.display_name, u.timezone, u.home_region, u.app_role,
           u.judge_mode_enabled, u.status, u.created_at
    FROM users u
    WHERE u.cognito_sub = %(cognito_sub)s
      AND u.status = 'ACTIVE'
"""

#: Section 3.3, capability 1. ``allowed_case_ids`` is a JSONB array on the row
#: and is returned as-is; ``CapabilityBinding`` refuses more than sixteen, so
#: a binding that spans a whole tenant cannot be constructed even if a row
#: somehow held one.
#:
#: ``expires_at`` and ``status`` are projected rather than filtered on, because
#: ``resolve_capability`` distinguishes ``BINDING_EXPIRED`` from
#: ``BINDING_NOT_ACTIVE`` from "no such capability", and a ``WHERE`` here would
#: collapse all three into the last one.
#:
#: ``ar.status`` and **not** ``ar.capability_status``. The two look
#: interchangeable and are opposites. ``status`` is the run's lifecycle and
#: ``ck_agent_runs_status`` admits ``RUNNING``, ``SUCCEEDED``, ``FAILED`` and
#: ``ABANDONED``; ``capability_status`` is section 9.9's ``JSONB`` trace
#: metadata, ``NULL`` until the run completes and an *object* thereafter
#: (``ck_agent_runs_capability_status``). Aliasing the second to ``status``
#: made ``str(row["status"])`` read ``"None"`` for every healthy live run, so
#: ``resolve_capability`` answered ``403 CAPABILITY_CONSUMED`` and no agent
#: could reach any ``/internal/v1`` endpoint. Nothing caught it because the
#: route suites drive a fake capability store and this statement had no test.
AGENT_RUN_CAPABILITY_SQL = """
    SELECT ar.id AS capability_id, ar.tenant_id, ar.user_id,
           ar.input_artifact_id AS artifact_id, ar.allowed_case_ids,
           ar.expires_at, ar.status, ar.trace_id
    FROM agent_runs ar
    WHERE ar.id = %(capability_id)s
"""

#: ``agent_runs.status`` -> the liveness vocabulary section 3.4's capability
#: record speaks. Derived rather than stored, exactly as ``_trigger`` derives
#: ``ACTIVE`` from ``state == 'ARMED'``: there is no ``ACTIVE`` value the
#: column could hold, and inventing one in the DDL would put two names on one
#: fact.
#:
#: A ``dict`` rather than ``"ACTIVE" if status == "RUNNING" else "CONSUMED"``,
#: because a fifth status added to ``ck_agent_runs_status`` would silently read
#: as ``CONSUMED`` under the ternary. Here it raises through the ``.get``
#: default below, which is a state named ``UNKNOWN`` that resolves as
#: not-live -- refusing, and visibly.
_AGENT_RUN_CAPABILITY_STATUS: Final[Mapping[str, str]] = {
    "RUNNING": "ACTIVE",
    "SUCCEEDED": "CONSUMED",
    "FAILED": "CONSUMED",
    "ABANDONED": "CONSUMED",
}

#: Section 3.3, capability 2. The trigger's own case is the binding's case.
TRIGGER_CAPABILITY_SQL = """
    SELECT t.id AS capability_id, t.tenant_id, t.user_id, t.case_id,
           t.expires_at, t.state, t.updated_at
    FROM prospective_triggers t
    WHERE t.id = %(capability_id)s
"""

#: Section 3.3, capability 3.
INTENT_CAPABILITY_SQL = """
    SELECT ai.id AS capability_id, ai.tenant_id, ai.user_id, ai.case_id,
           ai.status, ai.updated_at
    FROM action_intents ai
    WHERE ai.id = %(capability_id)s
"""

#: Section 3.3, capability 4. Keyed on ``alias_hash`` rather than on a UUID:
#: the SES worker has no id yet, only an opaque forwarded-email alias, and the
#: hash is what the ``uq_ingest_aliases_hash`` index is on.
#:
#: The plaintext alias never appears in this statement or in its parameters.
#: The caller hashes what arrived in the envelope and looks the digest up, so
#: a query log holds no forwarding address.
INGEST_ALIAS_CAPABILITY_SQL = """
    SELECT ia.id AS capability_id, ia.tenant_id, ia.user_id, ia.status,
           ia.created_at, COALESCE(ia.rotated_at, ia.created_at) AS anchor_at
    FROM ingest_aliases ia
    WHERE ia.alias_hash = %(alias_hash)s
"""

#: How long a capability with no expiry column of its own is considered live.
#: ``action_intents`` and ``ingest_aliases`` have no ``expires_at``: an intent
#: is live until it is executed or rejected, and an alias until it is rotated.
#: A binding still needs a bound lifetime, so one is derived rather than
#: treated as unlimited -- an unlimited capability is not a capability.
_DERIVED_TTL_SECONDS = 24 * 60 * 60


class SqlUserDirectory:
    """``cognito_sub`` -> :class:`UserRecord`, and nothing else.

    One method, because section 2.5 gives it one job. There is deliberately no
    ``create``: an API request that provisioned a user would let any member of
    the identity pool mint a tenant by calling ``GET /v1/me``.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    async def by_cognito_sub(self, sub: str) -> UserRecord | None:
        """The account for a verified token subject, or ``None``.

        ``None`` means "no provisioned, active account", and the caller turns
        it into ``403 USER_NOT_PROVISIONED``. It does not distinguish "never
        existed" from "disabled", because a token holder who can tell those
        apart can enumerate which subjects have accounts.
        """
        row = await _one(self._source, USER_BY_SUB_SQL, {"cognito_sub": sub})
        if row is None:
            return None
        return UserRecord(
            user_id=_as_uuid(row["user_id"]),
            tenant_id=_as_uuid(row["tenant_id"]),
            cognito_sub=str(row["cognito_sub"]),
            email=row.get("email"),
            display_name=row.get("display_name"),
            timezone=str(row.get("timezone") or "UTC"),
            home_region=row.get("home_region"),
            created_at=row["created_at"],
            judge_mode_allowlisted=bool(row.get("judge_mode_enabled")),
        )


class SqlCapabilityStore:
    """The four capability rows, projected onto one shape.

    ``resolve_capability`` (section 3.4) owns the checks -- scope, liveness,
    payload cross-check, proof, client matrix -- and this object owns only the
    lookup. The split matters: every check lives in one place and applies to
    all four kinds, so a fifth capability added later cannot arrive with three
    of the five checks.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    async def load(self, kind: str, key: str) -> CapabilityRecord | None:
        """One capability row, or ``None`` when nothing matches *key*.

        ``kind`` selects the statement rather than a table name being
        interpolated into one. Four constants and a dispatch is more code than
        one f-string, and it is the difference between a closed set of
        statements and a query whose ``FROM`` clause comes from the request.
        """
        if kind == "AGENT_RUN":
            return await self._agent_run(key)
        if kind == "TRIGGER_EVALUATION":
            return await self._trigger(key)
        if kind == "ACTION_INTENT":
            return await self._intent(key)
        if kind == "INGEST_JOB":
            return await self._ingest_alias(key)
        return None

    async def _agent_run(self, key: str) -> CapabilityRecord | None:
        row = await _one(self._source, AGENT_RUN_CAPABILITY_SQL, {"capability_id": _key(key)})
        if row is None:
            return None
        return CapabilityRecord(
            binding_kind="AGENT_RUN",
            capability_id=_as_uuid(row["capability_id"]),
            tenant_id=_as_uuid(row["tenant_id"]),
            user_id=_as_uuid(row["user_id"]),
            artifact_id=_opt_uuid(row.get("artifact_id")),
            allowed_case_ids=_uuid_tuple(row.get("allowed_case_ids")),
            expires_at=row["expires_at"],
            status=_AGENT_RUN_CAPABILITY_STATUS.get(str(row["status"]), "UNKNOWN"),
            trace_id=_opt_uuid(row.get("trace_id")),
        )

    async def _trigger(self, key: str) -> CapabilityRecord | None:
        row = await _one(self._source, TRIGGER_CAPABILITY_SQL, {"capability_id": _key(key)})
        if row is None:
            return None
        # A trigger's capability is live exactly while the trigger is armed.
        # ``expires_at`` on the row is the *predicate's* expiry -- when the
        # obligation stops mattering -- and is frequently months away, which
        # is far too long for a credential. The binding's lifetime is derived
        # from now instead, and the state is what decides liveness.
        case_id = _opt_uuid(row.get("case_id"))
        return CapabilityRecord(
            binding_kind="TRIGGER_EVALUATION",
            capability_id=_as_uuid(row["capability_id"]),
            tenant_id=_as_uuid(row["tenant_id"]),
            user_id=_as_uuid(row["user_id"]),
            case_id=case_id,
            allowed_case_ids=() if case_id is None else (case_id,),
            expires_at=_derived_expiry(row.get("updated_at")),
            status="ACTIVE" if str(row.get("state")) == "ARMED" else "EXPIRED",
        )

    async def _intent(self, key: str) -> CapabilityRecord | None:
        row = await _one(self._source, INTENT_CAPABILITY_SQL, {"capability_id": _key(key)})
        if row is None:
            return None
        case_id = _opt_uuid(row.get("case_id"))
        status = str(row.get("status"))
        return CapabilityRecord(
            binding_kind="ACTION_INTENT",
            capability_id=_as_uuid(row["capability_id"]),
            tenant_id=_as_uuid(row["tenant_id"]),
            user_id=_as_uuid(row["user_id"]),
            case_id=case_id,
            allowed_case_ids=() if case_id is None else (case_id,),
            expires_at=_derived_expiry(row.get("updated_at")),
            # Only an approved intent may be presented by an executor. Any
            # other status resolves to a consumed capability rather than to a
            # missing one, so the refusal names what happened.
            status="ACTIVE" if status == "APPROVED" else "CONSUMED",
        )

    async def _ingest_alias(self, key: str) -> CapabilityRecord | None:
        row = await _one(self._source, INGEST_ALIAS_CAPABILITY_SQL, {"alias_hash": _digest(key)})
        if row is None:
            return None
        return CapabilityRecord(
            binding_kind="INGEST_JOB",
            capability_id=None,
            alias_hash=key,
            tenant_id=_as_uuid(row["tenant_id"]),
            user_id=_as_uuid(row["user_id"]),
            # Anchored on the LAST ROTATION, falling back to creation. Rotating
            # an alias is the act that is meant to invalidate what came before,
            # so a proof minted against the old alias must stop verifying the
            # moment it rotates -- which anchoring on `created_at` alone would
            # not do.
            expires_at=_derived_expiry(row.get("anchor_at")),
            status="ACTIVE" if str(row.get("status")) == "ACTIVE" else "REVOKED",
        )


# --------------------------------------------------------------------------


async def _one(source: ConnectionSource, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """One row as a mapping, or ``None``.

    Deliberately not ``provenance_db.repositories._execute._fetch_one``: that
    helper is private to the repository package by design, and reaching into
    it from here to save eight lines would make this module's placement look
    like an exception to the boundary rather than a read that sits outside it.
    """
    async with source.connection() as conn, conn.cursor() as cursor:
        await cursor.execute(sql, params)
        description = cursor.description or ()
        columns = [column.name for column in description]
        rows = await cursor.fetchall()
    if not rows:
        return None
    return dict(zip(columns, rows[0], strict=True))


def _key(value: object) -> uuid.UUID:
    """The capability id, whether it arrived as text or already parsed."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _digest(value: object) -> bytes:
    """The 32-byte ``alias_hash`` the caller already computed, as bytes.

    Accepts hex because that is how an HMAC travels through a header, and
    bytes because that is what ``ingest_aliases.alias_hash`` is. It does
    **not** hash anything itself: the HMAC key belongs to the caller that
    holds it, and a second hashing implementation is a second chance to use a
    different key.
    """
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    return bytes.fromhex(str(value))


def _as_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _opt_uuid(value: object) -> uuid.UUID | None:
    return None if value is None else _as_uuid(value)


def _uuid_tuple(value: object) -> tuple[uuid.UUID, ...]:
    """``allowed_case_ids`` as UUIDs.

    A bare string is treated as *no* cases rather than as one, and that
    direction is deliberate: the column is a JSONB array, so a string means
    the row is malformed, and a malformed capability must narrow to nothing
    rather than widen to whatever the string parses as.
    """
    if not value or not isinstance(value, list | tuple):
        return ()
    return tuple(_as_uuid(item) for item in value)


def _derived_expiry(anchor: datetime | None) -> datetime:
    """A bounded lifetime anchored to a STORED timestamp, never to ``now``.

    This read ``datetime.now(UTC) + TTL`` and took no argument, which made the
    value change once a second. ``capability_proof._message`` puts
    ``int(expires_at.timestamp())`` inside the MAC, and
    ``verify_capability_proof`` recomputes it per request -- so a proof
    verified only if it happened to be checked during the same second it was
    issued. Measured: issued once, verified six times over two seconds, 2 pass
    and 4 refuse, with nothing changing but the clock.

    That broke ``TRIGGER_EVALUATION`` and ``ACTION_INTENT`` -- both of the
    demo's reveals -- and broke them *intermittently*, so a retry sometimes
    worked and it read as a flaky network rather than a broken credential.
    ``AGENT_RUN`` was unaffected because it uses a stored ``expires_at``.

    Anchoring to the row's own ``updated_at`` keeps the window bounded and the
    value stable, and buys a property the old version did not have: the proof
    **rotates whenever the row changes**. A capability id observed in a trace
    -- and ids do appear in traces, which is the whole reason this proof exists
    -- stops working the moment the trigger is evaluated or the intent is
    approved.

    A missing anchor falls back to the epoch rather than to ``now``: a row with
    no timestamp is a row we cannot bound, and a fixed value fails closed and
    visibly instead of failing intermittently.
    """
    from datetime import timedelta

    if anchor is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return anchor + timedelta(seconds=_DERIVED_TTL_SECONDS)
