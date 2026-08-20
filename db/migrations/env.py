"""Alembic environment for the Provenance schema.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` §16, "CockroachDB-specific Alembic
  configuration" — the exact ``context.configure()`` settings reproduced below.
- ``ops/41_RUNBOOK.md`` §4.1 — how this is invoked:

  .. code-block:: bash

      asm-exec --env COCKROACH_DATABASE_URL='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \\
        alembic upgrade head

Where the URL comes from
------------------------
``COCKROACH_DATABASE_URL``, from the process environment, resolved at run time.
It is deliberately **not** in ``alembic.ini``: that file is committed, and a
committed URL is a committed credential. Nothing in this module logs, echoes or
formats the URL into an exception message — SQLAlchemy masks the password in
``repr(URL)``, and this module never prints the raw string at all.

Why no autogenerate
-------------------
``target_metadata`` is pinned to ``None`` and there is no model metadata to
point it at. SQLAlchemy's PostgreSQL dialect cannot emit ``VECTOR``, ``FAMILY``,
``STORING`` or partial indexes, all of which this schema depends on, so an
autogenerate diff would confidently propose dropping the parts of the schema it
cannot see. The revisions are hand-written SQL through ``op.execute()``, which
also keeps them byte-comparable with the DDL spec they come from.

Driver
------
The DSN in Secrets Manager is a plain ``postgresql://`` URL, which SQLAlchemy
would resolve to psycopg2. ``pyproject.toml`` ships **psycopg 3**
(``psycopg[binary,pool]``), so the driver is normalised to ``postgresql+psycopg``
here rather than by asking every caller to write a SQLAlchemy-specific scheme
into a secret that psql and psycopg also read.

The CockroachDB version shim
----------------------------
SQLAlchemy's PostgreSQL dialect parses ``version()`` and raises
``AssertionError: Could not determine version from string 'CockroachDB CCL
v26.2.5 ...'`` before a single statement runs. The supported fix is the
``sqlalchemy-cockroachdb`` dialect, which is not a declared dependency of this
repository — and ``pyproject.toml`` is Integrator-owned
(``EXECUTION/71_AGENT_WORKFLOW.md`` section 7), so a Builder may not add one.

:func:`_shim_cockroach_version` therefore overrides ``_get_server_version_info``
on the **one dialect instance** this process creates. It is scoped to the
migration process, it touches no class and no other engine, and it reports the
PostgreSQL wire version CockroachDB v26.2 declares compatibility with. If
``sqlalchemy-cockroachdb`` is later added to the dev extra, delete this function
and the call to it; nothing else here depends on it.
"""

from __future__ import annotations

import types
from logging.config import fileConfig
from os import environ
from typing import Any

from alembic import context
from sqlalchemy import URL, Engine, create_engine, make_url
from sqlalchemy.pool import NullPool

#: The single environment variable this module reads.
URL_ENV_VAR = "COCKROACH_DATABASE_URL"

#: The PostgreSQL wire version CockroachDB v26.2 declares compatibility with.
#: Used only by :func:`_shim_cockroach_version`.
COCKROACH_PG_WIRE_VERSION: tuple[int, int] = (13, 0)

#: SQLAlchemy drivername for psycopg 3, the driver ``pyproject.toml`` pins.
PSYCOPG3_DRIVER = "postgresql+psycopg"

#: Drivernames that mean "PostgreSQL wire protocol, driver unspecified".
_UNSPECIFIED_DRIVERS = frozenset({"postgres", "postgresql"})

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No autogenerate. See the module docstring.
target_metadata = None


def _shim_cockroach_version(engine: Engine) -> Engine:
    """Teach *engine*'s dialect instance that CockroachDB is PostgreSQL 13.

    See the module docstring. Instance-scoped on purpose: patching the dialect
    *class* would change the behaviour of every engine in the process, including
    ones a future test creates against a genuine PostgreSQL.
    """

    def _version(_self: Any, _connection: Any) -> tuple[int, int]:
        return COCKROACH_PG_WIRE_VERSION

    engine.dialect._get_server_version_info = types.MethodType(_version, engine.dialect)  # type: ignore[method-assign]
    return engine


def _database_url() -> URL:
    """Resolve and normalise the migration URL. Never returns it as a string."""
    raw = environ.get(URL_ENV_VAR, "").strip()
    if not raw:
        raise RuntimeError(
            f"{URL_ENV_VAR} is not set. Migrations resolve their URL from the "
            f"environment, never from alembic.ini. See ops/41_RUNBOOK.md section 4.1: "
            f"asm-exec --env {URL_ENV_VAR}='{{{{resolve:secretsmanager:provenance/db"
            f":SecretString:migrator_url}}}}' -- alembic upgrade head"
        )
    url = make_url(raw)
    if url.drivername in _UNSPECIFIED_DRIVERS:
        url = url.set(drivername=PSYCOPG3_DRIVER)
    return url


def _transaction_per_migration() -> bool:
    """The ``alembic.ini`` setting, read rather than assumed.

    DDL section 16 prints ``transaction_per_migration = true`` in the ini and
    ``transaction_per_migration=True`` in ``context.configure()``. Alembic does
    not wire the two together on its own, so a value edited in the ini would
    otherwise have no effect and the file would document a behaviour it does not
    control.
    """
    configured = (config.get_main_option("transaction_per_migration") or "true").strip()
    return configured.lower() not in {"false", "0", "no", "off"}


def run_migrations_offline() -> None:
    """Emit SQL without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        transaction_per_migration=_transaction_per_migration(),
        transactional_ddl=True,
        compare_type=False,
        render_as_batch=False,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the chain against a live CockroachDB cluster.

    ``NullPool``: a migration process opens one connection, uses it once and
    exits. Pooling would only keep a connection alive after ``upgrade head`` has
    finished.
    """
    engine = _shim_cockroach_version(
        create_engine(_database_url(), poolclass=NullPool, future=True)
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                transaction_per_migration=_transaction_per_migration(),
                transactional_ddl=True,
                compare_type=False,
                render_as_batch=False,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
