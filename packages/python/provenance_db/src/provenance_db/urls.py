"""Role -> connection URL resolution. No URL is ever a function argument — T3.1.

Authority
---------
- ``ops/40_INFRA_IAC.md`` section 11.3 and ``provenance_contracts.settings``'s
  ``ROLE_DSN_BINDINGS``: the ``provenance/db`` secret carries five keys —
  ``migrator_url``, ``app_url``, ``kernel_url``, ``agent_url``,
  ``ops_reader_url`` — one per SQL role.
- ``CANONICAL_DECISIONS.md`` -> *Names and counts* and -> *Hero commit canon*:
  five roles, and ``pv_ops_reader`` is one of them.
- ``EXECUTION/70_TASK_PLAN.md`` T3.1: "no pool is constructed from a URL passed
  as a function argument by application code — every URL is resolved from the
  named secret key".

The rule this module exists to enforce
--------------------------------------
Application code names a **role**. It never names, holds, formats, logs or
passes a URL. A caller that could pass one could pass a different cluster, a
different role, or a URL a ``Settings`` object never validated — and the value
would then live in a stack frame, a traceback and a log line. So the only
input here is a role name plus a :class:`DsnSource`, and the only output is a
:class:`~pydantic.SecretStr` whose ``repr`` masks itself.

``Settings`` from ``provenance_contracts`` satisfies :class:`DsnSource`
structurally — it already has ``dsn_for_role(role) -> SecretStr`` — so nothing
here imports it, and this package holds no opinion about environment
variables. The resolution chain is:

    ``provenance/db`` secret  ->  ``asm-exec`` substitution  ->  environment
    ->  ``Settings``  ->  ``dsn_for_role``  ->  here  ->  a pool.

TLS
---
Every DSN must carry ``sslmode=verify-full``. ``verify-ca`` proves the
certificate chain but not the hostname, which leaves the connection open to a
DNS-level substitution; CockroachDB Cloud publishes a public chain, so there
is no cost to the stronger mode. :func:`require_verify_full` refuses anything
weaker and names the **role and secret key** in the message, never the URL.

The CA store is libpq's default: ``~/.postgresql/root.crt`` on POSIX and
``%APPDATA%\\postgresql\\root.crt`` on Windows. CockroachDB Cloud BASIC
clusters are signed by a public CA, so on a machine with a current system trust
store no ``sslrootcert`` parameter is needed; the cluster-provisioned DSNs in
this build carry none. If a deployment pins its own CA it belongs in the DSN
as ``sslrootcert=``, written into the secret, not into code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Protocol
from urllib.parse import parse_qsl, urlsplit

from pydantic import SecretStr

from provenance_contracts.settings import ROLE_DSN_BINDINGS

__all__ = [
    "REQUIRED_SSL_MODE",
    "SQL_ROLE_NAMES",
    "DsnNotAvailableError",
    "DsnSource",
    "InsecureDsnError",
    "MappingDsnSource",
    "UnknownSqlRoleError",
    "require_verify_full",
    "resolve_role_dsn",
    "secret_key_for_role",
]

#: The five SQL roles, in the order ``ROLE_DSN_BINDINGS`` declares them. Taken
#: from ``provenance_contracts`` rather than re-listed, so the vocabulary has
#: one home (``CANONICAL_DECISIONS.md`` -> *Closed domain vocabularies*).
SQL_ROLE_NAMES: Final[tuple[str, ...]] = tuple(ROLE_DSN_BINDINGS)

#: The only acceptable ``sslmode``. See the module docstring.
REQUIRED_SSL_MODE: Final[str] = "verify-full"


class UnknownSqlRoleError(ValueError):
    """A role name that is not one of the five."""


class DsnNotAvailableError(LookupError):
    """The role is known but this process has no DSN for it.

    Carries the ``provenance/db`` secret key that supplies it, because the
    operator's next action is to look there.
    """

    def __init__(self, role: str) -> None:
        self.role = role
        self.secret_key = secret_key_for_role(role)
        super().__init__(
            f"no connection URL is available for {role}; it is supplied by the "
            f"provenance/db secret under the key {self.secret_key!r}"
        )


class InsecureDsnError(ValueError):
    """A DSN whose ``sslmode`` is weaker than ``verify-full``.

    The message names the role and the secret key and never the URL: an
    exception is the single most likely place for a credential to reach a log.
    """

    def __init__(self, role: str, sslmode: str | None) -> None:
        self.role = role
        self.sslmode = sslmode
        found = sslmode or "unset"
        super().__init__(
            f"the connection URL for {role} has sslmode={found}, and "
            f"{REQUIRED_SSL_MODE} is required; fix the value in the provenance/db "
            f"secret under the key {secret_key_for_role(role)!r}"
        )


class DsnSource(Protocol):
    """Anything that can hand over one role's DSN.

    ``provenance_contracts.settings.Settings`` satisfies this already. So does
    :class:`MappingDsnSource`, which is how a test harness supplies values
    without this package ever reading an environment variable.
    """

    def dsn_for_role(self, role: str) -> SecretStr:
        """The DSN for *role*, or raise naming where it should have come from."""
        ...


class MappingDsnSource:
    """A :class:`DsnSource` over an explicit mapping.

    It cannot *find* a credential — someone must already hold it — so it does
    not weaken the rule at the top of this module. It exists so a harness can
    supply the two or three roles a test needs without constructing the whole
    ``Settings`` object, which requires every unrelated required variable to be
    set as well.
    """

    __slots__ = ("_dsns",)

    def __init__(self, dsns: Mapping[str, SecretStr]) -> None:
        for role in dsns:
            _require_known_role(role)
        self._dsns = dict(dsns)

    def dsn_for_role(self, role: str) -> SecretStr:
        _require_known_role(role)
        try:
            return self._dsns[role]
        except KeyError:
            raise DsnNotAvailableError(role) from None

    def roles(self) -> tuple[str, ...]:
        """The roles this source can supply, in declaration order."""
        return tuple(name for name in SQL_ROLE_NAMES if name in self._dsns)


def secret_key_for_role(role: str) -> str:
    """The ``provenance/db`` key that carries *role*'s URL."""
    return _require_known_role(role)


def _require_known_role(role: str) -> str:
    try:
        return ROLE_DSN_BINDINGS[role].secret_key
    except KeyError:
        known = ", ".join(SQL_ROLE_NAMES)
        raise UnknownSqlRoleError(f"unknown SQL role {role!r}; the roles are: {known}") from None


def _sslmode_of(dsn: str) -> str | None:
    """The ``sslmode`` of a URL-form or keyword-form conninfo string."""
    parts = urlsplit(dsn)
    if parts.query:
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "sslmode":
                return value
    if "=" in dsn and not parts.scheme:
        for token in dsn.split():
            key, _, value = token.partition("=")
            if key == "sslmode":
                return value
    return None


def require_verify_full(role: str, dsn: SecretStr) -> None:
    """Refuse *dsn* unless it carries ``sslmode=verify-full``.

    Called on the way out of :func:`resolve_role_dsn`, so a pool cannot be
    built on a downgraded connection even if the secret is edited by hand.
    """
    sslmode = _sslmode_of(dsn.get_secret_value())
    if sslmode != REQUIRED_SSL_MODE:
        raise InsecureDsnError(role, sslmode)


def resolve_role_dsn(role: str, source: DsnSource) -> SecretStr:
    """*role*'s connection URL, from the named secret key, TLS checked.

    Args:
        role: one of :data:`SQL_ROLE_NAMES`.
        source: where the value comes from — a ``Settings`` object in a running
            process, a :class:`MappingDsnSource` in a test.

    Raises:
        UnknownSqlRoleError: *role* is not one of the five.
        DsnNotAvailableError: the source has no value for it.
        InsecureDsnError: the value does not carry ``sslmode=verify-full``.
    """
    _require_known_role(role)
    dsn = source.dsn_for_role(role)
    require_verify_full(role, dsn)
    return dsn
