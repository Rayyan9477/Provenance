"""Fixtures for the Phase 2 database lane (``T2.1``-``T2.6``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` sections 2-11, 14-16 and 19.
- ``docs/ops/41_RUNBOOK.md`` section 4.1 - the migration and reversibility drill.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, tasks ``T2.1``-``T2.6``.

Every test in this directory carries the ``db`` marker, registered in
``pyproject.toml``. The hermetic ``unit`` lane must stay free of anything that
opens a socket, and the root ``conftest.py`` guard is keyed off that marker.

The database of record
----------------------
These fixtures resolve ``PROVENANCE_TEST_DB_URL`` and **refuse** any database
whose name is not ``provenance_ci``. The demo database ``provenance`` holds
seeded state that a downgrade drill would destroy, so pointing this lane at it
is a configuration error that must fail loudly rather than skip quietly.

Credential hygiene
------------------
``G0.3`` scans this repository with gitleaks. Nothing here prints a DSN:
:func:`_scrub` redacts any ``postgres://`` / ``postgresql://`` URL out of
captured subprocess output before a test can assert on it or a transcript can
record it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

pytestmark = pytest.mark.db

# --------------------------------------------------------------------------
# Repository geometry
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"
ENV_PY = MIGRATIONS_DIR / "env.py"
DDL_SPEC = REPO_ROOT / "docs" / "specs" / "10_DATABASE_DDL.md"
VARIANT_DECISION = REPO_ROOT / "ops" / "decisions" / "VECTOR_INDEX_VARIANT.md"

#: The eight revisions of the Phase 2 chain, in order (DDL section 16).
#: ``T2.1``-``T2.3`` built 0001-0003; ``T2.4``-``T2.6`` build 0004-0008. The
#: revision id is the filename stem, and both come from DDL section 16's table -
#: ``ops/41_RUNBOOK.md`` section 4.1 quotes the final line of a clean upgrade as
#: "Running upgrade 0007_action_plane -> 0008_events_infrastructure", so the ids
#: are contract values, not local choices.
REVISION_FILENAMES: tuple[str, ...] = (
    "0001_identity_aggregates.py",
    "0002_evidence_plane.py",
    "0003_epistemic_plane.py",
    "0004_obligation_ledger.py",
    "0005_kernel_control.py",
    "0006_prospective_memory.py",
    "0007_action_plane.py",
    "0008_events_infrastructure.py",
    "0009a_widen_proposal_model_check.py",
    "0009_gemini_embedding_plane.py",
)

#: The revision this lane migrates to, which is NOT ``head``.
#:
#: ``0009`` drops ``evidence_items.embedding`` and its three provenance
#: columns -- the only way to reach ``VECTOR(1536)`` on this cluster, since
#: ``ALTER COLUMN TYPE`` is refused with an index present and fails
#: *post-commit* without one. It therefore refuses to run unless explicitly
#: acknowledged.
#:
#: Pointing the lane at ``head`` would make every db test fail at the
#: fixture, far from the cause. Pinning it to ``0008`` keeps the lane honest
#: about what is actually deployed: ``0009`` lands with the re-embed, not
#: before it.
# 0009a only WIDENS ck_memory_proposals_model, which cannot invalidate a row,
# so it is deployed. 0009 -- which drops the embedding quartet and destroys
# 18,035 Titan vectors -- is not, which is why this is not `head`.
DEPLOYED_HEAD = "0009a_widen_proposal_model_check"

#: The only database this lane is permitted to touch.
CI_DATABASE_NAME = "provenance_ci"

#: The variable ``env.py`` reads, per ``ops/41_RUNBOOK.md`` section 4.1.
MIGRATION_URL_ENV = "COCKROACH_DATABASE_URL"

#: The variable the test lane reads, per ``pyproject.toml``'s ``db`` marker.
TEST_URL_ENV = "PROVENANCE_TEST_DB_URL"

_URL_PATTERN = re.compile(r"(?i)\b(postgres(?:ql)?://)[^\s'\"<>]+")


def _scrub(text: str) -> str:
    """Redact any database URL out of *text* before it can be recorded."""
    return _URL_PATTERN.sub(r"\1<redacted>", text)


def _dotenv_value(name: str) -> str | None:
    """Read *name* from the gitignored ``.env`` at the repository root."""
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return None
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return None


def _resolve_test_dsn() -> str | None:
    return os.environ.get(TEST_URL_ENV) or _dotenv_value(TEST_URL_ENV)


def _database_name(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


# --------------------------------------------------------------------------
# Paths and source text
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoPaths:
    """Where the Alembic scaffold lives. Asserted, never guessed."""

    root: Path
    alembic_ini: Path
    migrations_dir: Path
    versions_dir: Path
    env_py: Path
    revision_filenames: tuple[str, ...]

    def revision_paths(self) -> tuple[Path, ...]:
        return tuple(self.versions_dir / name for name in self.revision_filenames)


@pytest.fixture(scope="session")
def repo_paths() -> RepoPaths:
    return RepoPaths(
        root=REPO_ROOT,
        alembic_ini=ALEMBIC_INI,
        migrations_dir=MIGRATIONS_DIR,
        versions_dir=VERSIONS_DIR,
        env_py=ENV_PY,
        revision_filenames=REVISION_FILENAMES,
    )


@pytest.fixture(scope="session")
def ddl_spec_text() -> str:
    """``docs/specs/10_DATABASE_DDL.md`` - the owning spec, read as text."""
    return DDL_SPEC.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def variant_decision_text() -> str:
    """``ops/decisions/VECTOR_INDEX_VARIANT.md`` - the frozen ``T0.6`` decision."""
    return VARIANT_DECISION.read_text(encoding="utf-8")


class MaskedDsn(str):
    """A DSN that does not print its password when pytest reports a failure.

    THE PROBLEM THIS SOLVES, demonstrated rather than assumed:

        $ pytest services/control_plane/tests/db/test_zz_probe.py -q
        test_dsn = 'postgresql://pv_migrator:<the live password>@rayyandb-...'

    pytest renders every test-function argument in the failure header using
    ``repr()``. A session fixture returning a plain ``str`` DSN therefore writes
    the live migrator credential into the failure output of ANY failing test in
    this lane -- and that output goes into CI logs, into ``ops/tdd/`` evidence
    transcripts, and into gate reports, all of which are committed. The password
    is for the role that owns every canonical table.

    Scrubbing subprocess output, which this module already does, does not help:
    the leak is pytest's own header, not the child process.

    WHY A ``str`` SUBCLASS AND NOT ``SecretStr``:
    every consumer here passes the DSN straight to psycopg or to Alembic and
    needs the real string. ``SecretStr`` would require ``.get_secret_value()``
    at each of those call sites, and the failure mode of forgetting one is
    silent -- a connection to the literal text ``**********``. A ``str``
    subclass is substitutable everywhere a ``str`` works, so no call site can
    forget, while ``repr()`` -- the one path pytest uses -- is masked.

    ``str(dsn)`` and f-string interpolation deliberately still yield the real
    value: masking those would break psycopg. This narrows the accident, it does
    not make the value safe to print on purpose.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"MaskedDsn({_scrub(str(self))!r})"


# --------------------------------------------------------------------------
# The live cluster
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_dsn() -> MaskedDsn:
    """The ``provenance_ci`` DSN, or a skip when the lane is not configured.

    A DSN naming any other database is a hard error, not a skip: migrations are
    destructive and ``provenance`` carries the demo corpus.
    """
    dsn = _resolve_test_dsn()
    if not dsn:
        pytest.skip(f"{TEST_URL_ENV} is not set; the db lane needs a CockroachDB cluster")
    name = _database_name(dsn)
    if name != CI_DATABASE_NAME:
        raise RuntimeError(
            f"{TEST_URL_ENV} names database {name!r}; this lane runs migrations and "
            f"downgrades and may only touch {CI_DATABASE_NAME!r}."
        )
    return MaskedDsn(dsn)


#: ``.env`` keys holding a login DSN per runtime SQL role. Every one of them
#: names the **demo** database ``provenance``; :func:`role_dsn` rewrites the path
#: to ``provenance_ci`` so this lane can demonstrate a grant boundary without
#: ever opening a connection to the demo corpus.
ROLE_DSN_ENV: dict[str, str] = {
    "pv_migrator": "PV_DB_MIGRATOR_CI",
    "pv_app_reader_writer": "COCKROACH_DATABASE_URL",
    "pv_kernel_writer": "PV_DB_KERNEL",
    "pv_agent_reader": "PV_DB_AGENT_READER",
    "pv_ops_reader": "PV_DB_OPS_READER",
}


def _repoint_at_ci(dsn: str) -> str:
    """Return *dsn* with its database path replaced by ``provenance_ci``."""
    parts = urlsplit(dsn)
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/{CI_DATABASE_NAME}", parts.query, parts.fragment)
    )


@pytest.fixture(scope="session")
def role_dsn() -> Callable[[str], MaskedDsn]:
    """``role_dsn("pv_agent_reader")`` -> that role's ``provenance_ci`` DSN.

    ``G11.2`` is not an assertion about ``information_schema``; it is a refusal
    observed from the far side of the boundary. Proving it needs a connection
    **as** the constrained role, and the only credentials on this machine are the
    ``provenance``-pointed ones in ``.env``. Repointing the path is safe in the
    direction that matters: it can only ever move a connection *towards*
    ``provenance_ci``, never away from it, and the result is asserted below.
    """

    def _for(role: str) -> MaskedDsn:
        key = ROLE_DSN_ENV.get(role)
        if key is None:  # pragma: no cover - a typo in a test, not a state
            raise KeyError(f"no .env key is registered for role {role!r}")
        raw = os.environ.get(key) or _dotenv_value(key)
        if not raw:
            pytest.skip(f"{key} is not configured; cannot connect as {role}")
        repointed = _repoint_at_ci(raw)
        assert _database_name(repointed) == CI_DATABASE_NAME
        return MaskedDsn(repointed)

    return _for


@pytest.fixture(scope="session")
def alembic() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run the Alembic CLI against a DSN, returning scrubbed output."""

    def _run(*args: str, dsn: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env[MIGRATION_URL_ENV] = dsn
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return subprocess.CompletedProcess(
            args=["alembic", *args],
            returncode=completed.returncode,
            stdout=_scrub(completed.stdout or ""),
            stderr=_scrub(completed.stderr or ""),
        )

    return _run


@pytest.fixture(scope="session")
def migrated(
    test_dsn: MaskedDsn,
    alembic: Callable[..., subprocess.CompletedProcess[str]],
) -> MaskedDsn:
    """``alembic upgrade head`` against ``provenance_ci``; yields the DSN.

    Session-scoped so the chain runs once. The up/down/up drill in
    ``test_migrations.py`` restores head before it returns, so ordering between
    modules does not matter.
    """
    result = alembic("upgrade", DEPLOYED_HEAD, dsn=test_dsn)
    if result.returncode != 0:
        pytest.fail(
            f"alembic upgrade {DEPLOYED_HEAD} failed\n"
            f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return test_dsn


@pytest.fixture
def db_connection(migrated: str) -> Iterator[psycopg.Connection]:
    """A connection to the migrated ``provenance_ci`` database.

    Not autocommit: every test that writes rolls back, so the lane leaves no
    residue for the next one to trip over.
    """
    conn = psycopg.connect(migrated)
    try:
        yield conn
        conn.rollback()
    finally:
        conn.close()


@pytest.fixture
def show_create(db_connection: psycopg.Connection) -> Callable[[str], str]:
    """``SHOW CREATE TABLE <name>`` as text.

    CockroachDB renders every CHECK, FAMILY, generated column and partial index
    into this one string, which is the only introspection surface carrying all
    of them at once.
    """

    def _show(table: str) -> str:
        with db_connection.cursor() as cur:
            cur.execute(f"SHOW CREATE TABLE {table}")
            row = cur.fetchone()
        assert row is not None, f"SHOW CREATE TABLE {table} returned nothing"
        return str(row[1])

    return _show
