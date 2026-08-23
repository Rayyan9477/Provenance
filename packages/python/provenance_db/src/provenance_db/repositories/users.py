"""Reads over the account plane: the session bootstrap and the ingest alias.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.3 (``GET /v1/me``) and 8.21
  (``GET /v1/ingest-alias``).
- ``specs/10_DATABASE_DDL.md`` section 12 — write-path ownership. ``users``,
  ``tenants`` and ``ingest_aliases`` belong to ``pv_app_reader_writer``; this
  module reads them and writes nothing.

What is deliberately **not** here
---------------------------------
The ``cognito_sub`` -> ``users`` lookup. Section 2.5 makes it the read that
*produces* a scope: it turns a verified token subject into the
``(tenant_id, user_id)`` pair every other statement in this package binds. It
therefore cannot take that pair as an argument, and the package's own guard —
``tests/db/test_repository_read_only.py::
test_no_read_signature_omits_both_a_principal_and_an_explicit_pair`` — is right
to refuse it. It lives in
``services/control_plane/app/api/adapters/directory.py`` with that reasoning
recorded beside it, and there is no scoping predicate duplicated by the move:
the statement is where a scope first comes from.

Everything below takes the pair, so the two questions stay visibly different.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_one, _owner

__all__ = [
    "INGEST_ALIAS_SQL",
    "ME_SQL",
    "get_ingest_alias",
    "get_me",
]

#: Section 8.3. The ingest alias status is joined rather than fetched
#: separately because ``GET /v1/me`` is the session bootstrap and section 8.3
#: describes it as "one indexed read"; two round trips for one screen is how a
#: bootstrap becomes the slowest call in the app.
#:
#: ``LEFT JOIN``, not ``JOIN``: a user with no alias provisioned still has an
#: account, and an inner join would render them as unprovisioned — which
#: section 2.5 reserves for a genuinely absent ``users`` row and answers with
#: ``403 USER_NOT_PROVISIONED``.
ME_SQL = """
    SELECT u.id AS user_id, u.tenant_id, u.email, u.display_name, u.timezone,
           u.home_region, u.judge_mode_enabled, u.app_role, u.status,
           u.created_at,
           ia.status AS ingest_alias_status
    FROM users u
    LEFT JOIN ingest_aliases ia
      ON ia.tenant_id = u.tenant_id
     AND ia.user_id = u.id
     AND ia.status = 'ACTIVE'
    WHERE u.tenant_id = %(tenant_id)s
      AND u.id = %(user_id)s
"""

#: Section 8.21. ``alias_hash`` is never projected: it is the authentication
#: material for inbound mail, and a display surface has no use for it.
#: ``alias_label`` is the reversible, non-secret display column section 8.21
#: names, and a deployment that leaves it unset renders ``null`` and the UI
#: shows "rotate to reveal" — which is the honest answer, not an error.
#:
#: The two counters are correlated subqueries rather than a ``GROUP BY`` join:
#: ``source_artifacts`` has no ``alias_id``, so the relationship between an
#: alias and the mail it received is "same owner, arrived by email", and a join
#: would multiply the alias row by the artifact count.
INGEST_ALIAS_SQL = """
    SELECT ia.id, ia.alias_label, ia.status, ia.created_at, ia.rotated_at,
           (SELECT count(*) FROM source_artifacts sa
             WHERE sa.tenant_id = ia.tenant_id
               AND sa.user_id = ia.user_id
               AND sa.source_type = 'EMAIL_INBOUND') AS artifacts_received,
           (SELECT max(sa.received_at) FROM source_artifacts sa
             WHERE sa.tenant_id = ia.tenant_id
               AND sa.user_id = ia.user_id
               AND sa.source_type = 'EMAIL_INBOUND') AS last_received_at
    FROM ingest_aliases ia
    WHERE ia.tenant_id = %(tenant_id)s
      AND ia.user_id = %(user_id)s
    ORDER BY ia.status, ia.created_at DESC
    LIMIT 1
"""


async def get_me(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    """The session bootstrap row, or ``None`` when the account is absent."""
    return await _fetch_one(conn, ME_SQL, _owner(tenant_id, user_id))


async def get_ingest_alias(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    """The user's current ingest alias, or ``None`` when none was provisioned.

    ``ORDER BY ia.status`` puts ``ACTIVE`` ahead of ``DISABLED`` — the strings
    sort that way — so a user who has rotated sees the live alias rather than
    whichever row happened to be newest.
    """
    return await _fetch_one(conn, INGEST_ALIAS_SQL, _owner(tenant_id, user_id))
