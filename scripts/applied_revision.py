"""Print the Alembic revision a database is actually at.

Why this is not ``scripts/schema_head.py``
------------------------------------------
They answer different questions and the difference is the whole point.

``schema_head.py`` reports what the migration chain *declares* -- the revision
nothing else points down to. On this branch that is
``0009_gemini_embedding_plane``, which widens ``evidence_items.embedding`` to
``VECTOR(1536)`` for the Gemini space.

That revision is **deliberately not applied**. Its own ``upgrade()`` refuses to
run without ``PV_EMBEDDING_REWRITE_ACK``, the corpus in the ground is 18,035
Titan vectors at ``VECTOR(1024)``, and ``ACTIVE_EMBEDDING_PROFILE`` resolves to
``titan-v1``. The live cluster sits at ``0009b_kernel_idempotency_grant``.

So a deploy that reported the chain head as ``schema_revision`` on
``GET /v1/version`` would publish a revision the database is not at -- a
confident, specific, wrong number on the one endpoint the project offers a
judge as its authoritative disclosure channel. Better to say nothing than to
say that: the web app's status strip now renders an unset revision as an
explicit absence marker, which is true, rather than as a bare ``schema=``.

This script therefore *measures* rather than derives, and exits non-zero when
it cannot, so the caller can leave the value unset instead of guessing.

Note that it needs the migrator role. ``pv_app_reader_writer`` -- the role the
control plane runs as -- has no SELECT on ``alembic_version``, by design, which
is why the service cannot answer this question about itself at runtime and the
deploy has to supply it.

Usage
-----
    python scripts/applied_revision.py "$PV_DB_MIGRATOR"
"""

from __future__ import annotations

import sys

_QUERY = "SELECT version_num FROM alembic_version"


def applied_revision(database_url: str, *, connect_timeout: int = 20) -> str:
    """The single revision *database_url* is at.

    Raises ``ValueError`` when the table holds no row or more than one. Both are
    real problems: none means the database was never migrated, and several mean
    a branched chain was applied and no single answer exists.
    """
    import psycopg

    with (
        psycopg.connect(database_url, connect_timeout=connect_timeout) as conn,
        conn.cursor() as cursor,
    ):
        cursor.execute(_QUERY)
        rows = [row[0] for row in cursor.fetchall()]

    if len(rows) != 1:
        raise ValueError(f"alembic_version holds {len(rows)} rows ({rows}); expected exactly one")
    return str(rows[0])


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("usage: applied_revision.py <database-url>", file=sys.stderr)
        return 2
    try:
        print(applied_revision(sys.argv[1]))
    except Exception as exc:  # the caller wants a value or a non-zero exit, never a traceback
        print(f"applied_revision: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
