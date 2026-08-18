"""Repository-wide pytest configuration.

Authority
---------
- ``quality/20_TDD_STRATEGY.md`` section 2.3, mechanism **E3**: the unit lane
  runs with ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_PROFILE``,
  ``AWS_REGION`` and ``COCKROACH_DATABASE_URL`` unset, and with outbound
  sockets blocked by an autouse fixture that raises. "Any accidental network
  reach fails loudly rather than silently succeeding on a developer laptop that
  happens to be logged in."
- ``quality/20_TDD_STRATEGY.md`` section 4.1: the session fixture
  ``frozen_clock``, a ``FrozenClock`` pinned to ``DEMO_ANCHOR``
  ``2026-09-18T09:00:00-04:00``.
- ``quality/20_TDD_STRATEGY.md`` section 4.2 rule 3: wall-clock reads are
  banned in tests; tests use ``frozen_clock`` and the kernel uses ``tx_now``
  from ``transaction_timestamp()``.
- ``quality/20_TDD_STRATEGY.md`` section 2.1: the rule the guard defends. The
  Memory Kernel must be fully testable with no network access, no AWS
  credentials and no model call of any kind. If the kernel needs an LLM to unit
  test, the boundary is wrong.

Scope of the guard
------------------
The guard is applied **only to tests carrying the ``unit`` marker**. Section
2.3 prints its snippet as ``tests/unit/conftest.py``, but the layout this
repository actually builds (``implementation/00_IMPLEMENTATION_MAP.md`` section
5) puts unit tests beside their package - ``packages/python/*/tests/``,
``services/control_plane/tests/unit/``, ``agents/runtime/tests/`` - and there
is no single ``tests/unit/`` directory to hang a conftest on. One root conftest
keyed off the marker covers every one of those directories and cannot be
forgotten when the next package is added. The ``db``, ``contract``, ``e2e``,
``retrieval``, ``concurrency`` and ``live_model`` lanes are untouched: they are
supposed to open sockets.

Why this file does not replace ``socket.socket`` wholesale
----------------------------------------------------------
Section 2.3's illustrative snippet does ``monkeypatch.setattr(socket,
"socket", _blocked)``. Doing that here would break every async unit test on
both CI and the developer machine, and the failure would be misreported as a
network violation: the ``asyncio`` event loop builds its self-pipe with
``socket.socketpair()``, whose CPython implementation constructs
``socket.socket`` objects and, on Windows, connects one loopback socket to
another. ``pyproject.toml`` sets ``asyncio_mode = "auto"``, so a loop is
created for every async test.

The guard therefore blocks the *reach* rather than the *object*. The blocked
set is :data:`BLOCKED_REACHES`; ``requests``, ``httpx``, ``psycopg``, ``boto3``
and ``urllib3`` all bottom out in ``connect`` or ``create_connection``.
``socket.socketpair`` is wrapped rather than blocked, so the loopback pair that
``asyncio`` needs still works. ``tests/test_socket_guard.py`` asserts both
halves of that trade: the refusal fires, and the event loop still starts.

The name-resolution half of that list is not decoration and it is not covered
by ``getaddrinfo`` alone. ``socket.gethostbyname``, ``gethostbyname_ex`` and
``gethostbyaddr`` call ``gethostbyname``/``gethostbyaddr`` in CPython's
``_socket`` module and do **not** route through ``getaddrinfo``, so before
``D-00-005`` a ``unit``-marked test could resolve a public hostname and leak
the lookup itself - which is the exact escape
``test_getaddrinfo_is_refused`` claims to prevent. Each blocked reach in
:data:`BLOCKED_REACHES` has a test that fails if its patch is removed; that
mutation matrix is what stops this list from growing a decorative entry.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, NoReturn

import pytest

# ---------------------------------------------------------------------------
# E3 - the no-credentials, no-socket unit lane
# ---------------------------------------------------------------------------


class NetworkAccessInUnitTestError(RuntimeError):
    """A ``unit``-marked test tried to reach the network.

    Raised by the guard installed in :func:`install_socket_guard`. It reports a
    design defect, not a flake: either the test belongs in another lane, or the
    code under test has an I/O dependency it should not have.
    """


#: The five variables E3 names. They are removed from the environment of every
#: ``unit``-marked test so that a laptop with a live AWS profile and a live
#: ``COCKROACH_DATABASE_URL`` runs the lane CI runs. This is the in-process
#: equivalent of the ``env -u`` in ``.github/workflows/ci.yml``.
CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_PROFILE",
    "AWS_REGION",
    "COCKROACH_DATABASE_URL",
)

_REFUSAL = (
    "unit test attempted a network call ({call}); the kernel boundary is wrong.\n"
    "quality/20_TDD_STRATEGY.md section 2.1: given database state plus a "
    "deterministic MemoryProposal fixture, the Memory Kernel must produce an "
    "exact result with no network access, no AWS credentials and no model call "
    "of any kind.\n"
    "If this test genuinely needs a socket then it is not a unit test. Mark it "
    "db, contract, retrieval, concurrency, e2e or live_model instead: those "
    "lanes are allowed out, and pyproject.toml already registers every one of "
    "them."
)

# Captured once, at import, before anything is patched. Delegating to these is
# what lets socketpair keep working while connect() is refused.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_SOCKETPAIR = socket.socketpair

# Set for the duration of one socketpair() call. Unit tests are single
# threaded and the window is the body of a single stdlib function, so a plain
# module-level flag is the honest mechanism here rather than thread-local
# state. This is a tripwire, not a security boundary.
_IN_SOCKETPAIR = False

#: Reaches replaced wholesale by a refusal, as ``(owner, attribute)`` pairs.
#:
#: ``connect`` and ``connect_ex`` are deliberately absent: they need the
#: ``socketpair`` delegation below and are patched separately.
#:
#: The three ``gethostby*`` entries were added by ``D-00-005``. They are not
#: reachable through ``getaddrinfo`` in CPython, so a ``unit``-marked test could
#: resolve a public name and put a DNS query on the wire while the guard
#: reported itself installed.
#:
#: ``sendmsg`` does not exist on Windows and does exist on the Linux CI runner,
#: so the list is filtered by ``hasattr`` rather than assumed. An entry filtered
#: out here is one this platform cannot reach either.
_CANDIDATE_REACHES: tuple[tuple[str, str], ...] = (
    ("socket.socket", "sendto"),
    ("socket.socket", "sendmsg"),
    ("socket", "create_connection"),
    ("socket", "getaddrinfo"),
    ("socket", "gethostbyname"),
    ("socket", "gethostbyname_ex"),
    ("socket", "gethostbyaddr"),
)


def _owner_of(target: str) -> Any:
    """The object carrying *target*'s attribute: the class, or the module."""
    return socket.socket if target == "socket.socket" else socket


BLOCKED_REACHES: tuple[tuple[str, str], ...] = tuple(
    (target, attribute)
    for target, attribute in _CANDIDATE_REACHES
    if hasattr(_owner_of(target), attribute)
)


def _refuse(call: str) -> Callable[..., NoReturn]:
    """Build a replacement for *call* that raises instead of reaching out."""

    def _blocked(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise NetworkAccessInUnitTestError(_REFUSAL.format(call=call))

    return _blocked


def _blocked_connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    if _IN_SOCKETPAIR:
        return _REAL_CONNECT(sock, address, *args, **kwargs)
    raise NetworkAccessInUnitTestError(_REFUSAL.format(call="socket.socket.connect"))


def _blocked_connect_ex(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    if _IN_SOCKETPAIR:
        return _REAL_CONNECT_EX(sock, address, *args, **kwargs)
    raise NetworkAccessInUnitTestError(_REFUSAL.format(call="socket.socket.connect_ex"))


def _guarded_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
    """Allow the loopback pair ``asyncio`` needs, and nothing else.

    On Linux this delegates to the ``socketpair(2)`` syscall and never connects
    anywhere. On Windows CPython emulates it by connecting one loopback socket
    to another, which is the only reason the flag exists.
    """
    global _IN_SOCKETPAIR
    _IN_SOCKETPAIR = True
    try:
        return _REAL_SOCKETPAIR(*args, **kwargs)
    finally:
        _IN_SOCKETPAIR = False


def install_socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every outbound reach for the duration of one test.

    ``monkeypatch.setattr`` on ``socket.socket.connect`` records the descriptor
    inherited from ``_socket.socket`` and reinstates it on teardown; the
    restored attribute is the same callable, materialised in the subclass
    dictionary rather than inherited. That difference is not observable.
    """
    monkeypatch.setattr(socket, "socketpair", _guarded_socketpair)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    for target, attribute in BLOCKED_REACHES:
        monkeypatch.setattr(_owner_of(target), attribute, _refuse(f"{target}.{attribute}"))


def unset_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove E3's five variables from this test's environment."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def guard_applies(node: Any) -> bool:
    """True when *node* is a ``unit``-marked test item.

    ``get_closest_marker`` walks the item, its class and its module, so a
    module-level ``pytestmark = pytest.mark.unit`` counts. That is how every
    unit test file in this repository declares itself.
    """
    return node.get_closest_marker("unit") is not None


@dataclass(frozen=True)
class SocketGuard:
    """Handle onto the guard, for the test that proves the guard fires.

    ``conftest.py`` is loaded by pytest rather than imported by name, and
    ``--import-mode=importlib`` means a test module cannot rely on
    ``import conftest`` resolving. Handing the pieces over as a fixture is the
    supported route.
    """

    error: type[NetworkAccessInUnitTestError]
    applies: Callable[[Any], bool]
    #: :func:`unset_credentials`, handed over so the credential half of E3 can
    #: be tested by *calling* it rather than by asserting on the ambient
    #: environment. The ambient assertion is vacuous on any machine that never
    #: had the five variables set - which is every CI runner, i.e. exactly
    #: where the lane it defends actually runs. (``D-00-005``.)
    unset: Callable[[pytest.MonkeyPatch], None]
    #: :data:`BLOCKED_REACHES`, so the mutation matrix in
    #: ``tests/test_socket_guard.py`` enumerates the real list rather than a
    #: copy of it that can silently fall behind.
    reaches: tuple[tuple[str, str], ...]
    credential_vars: tuple[str, ...]


@pytest.fixture
def socket_guard() -> SocketGuard:
    """The guard's exception type, its marker predicate and its two lists."""
    return SocketGuard(
        error=NetworkAccessInUnitTestError,
        applies=guard_applies,
        unset=unset_credentials,
        reaches=BLOCKED_REACHES,
        credential_vars=CREDENTIAL_ENV_VARS,
    )


@pytest.fixture(autouse=True)
def _pv_hermetic_unit_lane(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E3, applied to every ``unit``-marked test and to no other."""
    if not guard_applies(request.node):
        return
    unset_credentials(monkeypatch)
    install_socket_guard(monkeypatch)


# ---------------------------------------------------------------------------
# The frozen clock - 20_TDD_STRATEGY.md sections 4.1 and 4.2
# ---------------------------------------------------------------------------

#: The demo clock. Every seeded timestamp is an offset from this instant
#: (section 4.2 rule 2) so that "four months ago" stays four months ago in
#: March. ``CANONICAL_DECISIONS.md`` (hero commit canon) fixes the deposit
#: ``due_at`` at ``2026-06-15T00:00:00Z``, and the 95-days-overdue figure the
#: demo prints is that date measured against this anchor.
DEMO_ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4)))


@dataclass(frozen=True)
class FrozenClock:
    """A clock that does not move.

    Deliberately *not* a monkeypatch of ``datetime.now``. Section 4.2 rule 3
    bans wall-clock reads in tests outright, and Phase 14 adds an AST lint
    (``tests/test_no_wallclock_in_tests.py``) that fails any test file calling
    ``datetime.now()``. A test asks this object for the time; production code
    takes ``tx_now`` from ``transaction_timestamp()``. Patching the standard
    library would hide both mistakes instead of surfacing them.
    """

    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None:
            raise ValueError(
                "FrozenClock requires an aware datetime: provenance_contracts "
                "rejects naive datetimes at every boundary, and a fixture that "
                "produced one would be testing something the product refuses."
            )

    def now(self) -> datetime:
        """The anchor, in its own offset (``-04:00``)."""
        return self.instant

    def now_utc(self) -> datetime:
        """The anchor as UTC, which is how every row stores it."""
        return self.instant.astimezone(UTC)

    def shift(self, **offset: float) -> datetime:
        """The anchor moved by a ``timedelta`` keyword, for example ``days=-95``."""
        return self.instant + timedelta(**offset)


@pytest.fixture(scope="session")
def frozen_clock() -> FrozenClock:
    """Session-scoped ``FrozenClock`` pinned to :data:`DEMO_ANCHOR`."""
    return FrozenClock(DEMO_ANCHOR)
