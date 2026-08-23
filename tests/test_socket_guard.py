"""Proof that the E3 socket guard in the root ``conftest.py`` actually fires.

Authority: ``quality/20_TDD_STRATEGY.md`` section 2.3 mechanism **E3**, and
section 4.1 for the ``frozen_clock`` anchor.

Why this file exists
--------------------
A guard that never fires is indistinguishable from no guard at all. The unit
lane is green either way, and the first time anyone notices is when a "unit"
test passes on the reviewer's laptop because it is logged in and fails in CI
because it is not - or worse, passes in both places while quietly calling
Bedrock. Section 3 of ``quality/23_PHASE_GATES.md`` is the same argument at
gate scale: an assertion nobody can watch fail is not an assertion.

So every mechanism the guard installs is exercised here, and so is the thing it
must *not* break: ``asyncio`` builds its event loop self-pipe out of a
``socket.socketpair()``, and a guard that blocked that would fail every async
unit test with a misleading "network call" message.

This file lives in the cross-package ``tests/`` tree because it tests the root
``conftest.py``, which belongs to no package
(``implementation/00_IMPLEMENTATION_MAP.md`` section 5: "only genuinely
cross-package suites live in the top-level tests/").
"""

from __future__ import annotations

import asyncio
import http.client
import os
import socket
from datetime import date, datetime

import pytest

pytestmark = pytest.mark.unit

# A host that is never contacted, because the guard raises before any name is
# resolved. RFC 2606 reserves .invalid precisely so that a test which somehow
# escapes the guard fails on resolution rather than sending traffic to a real
# third party.
UNREACHABLE_HOST = "provenance-guard-probe.invalid"


# ---------------------------------------------------------------------------
# The guard refuses every way out of the machine
# ---------------------------------------------------------------------------


def test_socket_connect_is_refused(socket_guard) -> None:
    """Constructing a socket is fine; connecting it is not."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(socket_guard.error) as excinfo:
            sock.connect((UNREACHABLE_HOST, 443))
    finally:
        sock.close()

    assert "the kernel boundary is wrong" in str(excinfo.value)
    assert "socket.socket.connect" in str(excinfo.value)


def test_socket_connect_ex_is_refused(socket_guard) -> None:
    """``connect_ex`` returns an errno instead of raising, so it needs its own arm."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(socket_guard.error):
            sock.connect_ex((UNREACHABLE_HOST, 443))
    finally:
        sock.close()


def test_create_connection_is_refused(socket_guard) -> None:
    """``socket.create_connection`` is the path ``urllib3`` and ``psycopg`` take.

    The message assertion is what makes this test see its own patch. Without
    it the call falls through to the still-patched ``getaddrinfo`` and raises
    the same exception type, so deleting the ``create_connection`` patch left
    the whole suite green (``D-00-005``).
    """
    with pytest.raises(socket_guard.error) as excinfo:
        socket.create_connection((UNREACHABLE_HOST, 443), timeout=0.01)
    assert "socket.create_connection" in str(excinfo.value)


def test_getaddrinfo_is_refused(socket_guard) -> None:
    """Name resolution leaves the machine too, and it leaks the lookup itself."""
    with pytest.raises(socket_guard.error) as excinfo:
        socket.getaddrinfo(UNREACHABLE_HOST, 443)
    assert "socket.getaddrinfo" in str(excinfo.value)


def test_sendto_is_refused(socket_guard) -> None:
    """A datagram needs no ``connect``: ``sendto`` carries the address itself.

    This is the one reach the guard installed with no test at all before
    ``D-00-005`` - the patch could be deleted and the suite stayed 17/17 green.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(socket_guard.error) as excinfo:
            sock.sendto(b"provenance", ("192.0.2.1", 9))
    finally:
        sock.close()
    assert "socket.socket.sendto" in str(excinfo.value)


@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("gethostbyname", ("example.com",)),
        ("gethostbyname_ex", ("example.com",)),
        ("gethostbyaddr", ("93.184.216.34",)),
    ],
)
def test_the_legacy_resolvers_are_refused(socket_guard, call: str, args: tuple) -> None:
    """``gethostby*`` does not route through ``getaddrinfo`` in CPython.

    Before ``D-00-005`` these three were unguarded, and a ``unit``-marked test
    calling ``socket.gethostbyname("example.com")`` returned a real address -
    a DNS query on the wire from the hermetic lane. A real, resolvable name is
    used here on purpose: against the unpatched stdlib this test would leak,
    which is what makes the refusal worth asserting.
    """
    with pytest.raises(socket_guard.error) as excinfo:
        getattr(socket, call)(*args)
    assert f"socket.{call}" in str(excinfo.value)


def test_every_blocked_reach_is_actually_patched(socket_guard) -> None:
    """The list and the installation cannot drift apart.

    ``install_socket_guard`` iterates ``BLOCKED_REACHES``, so an entry added to
    the list without a working patch, or a patch silently skipped because the
    attribute name is wrong, shows up here rather than as a quiet hole.
    """
    assert socket_guard.reaches, "the blocked-reach list is empty"
    for target, attribute in socket_guard.reaches:
        owner = socket.socket if target == "socket.socket" else socket
        with pytest.raises(socket_guard.error) as excinfo:
            # Called with no arguments: the refusal is raised before the
            # signature is ever examined, so this reaches the guard on every
            # entry regardless of arity.
            getattr(owner, attribute)()
        assert f"{target}.{attribute}" in str(excinfo.value)


def test_a_high_level_http_client_is_refused(socket_guard) -> None:
    """The assertion that matters: an ordinary client library cannot get out.

    Nothing in the product calls ``socket.connect`` directly. It calls
    ``boto3`` or ``httpx``, which call ``urllib3``, which calls
    ``socket.create_connection``. A guard that stopped only the bottom layer
    while the libraries found another route would be decoration.
    """
    conn = http.client.HTTPSConnection(UNREACHABLE_HOST, timeout=0.01)
    try:
        with pytest.raises(socket_guard.error):
            conn.request("GET", "/")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# What the guard must not break
# ---------------------------------------------------------------------------


def test_socketpair_still_works() -> None:
    """The loopback pair is in-process, so it is allowed."""
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"provenance")
        assert right.recv(10) == b"provenance"


def test_an_asyncio_event_loop_still_starts() -> None:
    """``asyncio_mode = "auto"`` means every async unit test depends on this.

    The loop's self-pipe is a ``socket.socketpair()``. A guard that replaced
    ``socket.socket`` wholesale, which is what the illustrative snippet in
    section 2.3 does, would fail here and would report a network violation for
    a purely in-process construct.
    """
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(asyncio.sleep(0, result="ran")) == "ran"
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Scope: the unit marker, and nothing else
# ---------------------------------------------------------------------------


class _FakeItem:
    """The one method of ``pytest.Item`` that the predicate touches."""

    def __init__(self, *, marked: bool) -> None:
        self._marked = marked

    def get_closest_marker(self, name: str) -> object | None:
        if name == "unit" and self._marked:
            return pytest.mark.unit
        return None


def test_guard_applies_only_to_unit_marked_tests(socket_guard) -> None:
    """A ``db`` or ``e2e`` test is supposed to open sockets, and keeps them."""
    assert socket_guard.applies(_FakeItem(marked=True)) is True
    assert socket_guard.applies(_FakeItem(marked=False)) is False


def test_this_test_is_itself_guarded(socket_guard) -> None:
    """The fixture is autouse, so no test had to ask for it.

    A guard that has to be requested is a guard the next test file forgets,
    which is the discipline-instead-of-structure failure section 2.3 exists to
    remove.
    """
    with pytest.raises(socket_guard.error):
        socket.getaddrinfo("localhost", 80)


# ---------------------------------------------------------------------------
# E3's other half: no credentials in the unit lane
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_REGION",
        "COCKROACH_DATABASE_URL",
    ],
)
def test_credentials_are_absent_from_the_unit_lane(name: str) -> None:
    """The five variables E3 names are unset for every unit test.

    CI runs this lane on a runner that never had them. This assertion is for
    the developer machine, where they are routinely exported, and where a unit
    test that quietly used one would pass locally and fail in CI.

    On its own this is a vacuous assertion in the environment that matters -
    see ``test_the_credential_unsetter_actually_removes_them`` below, which is
    the arm that fails when the mechanism is deleted.
    """
    assert name not in os.environ


def test_the_credential_unsetter_actually_removes_them(
    socket_guard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the mechanism, not the ambient environment.

    ``D-00-005``: deleting ``unset_credentials(monkeypatch)`` from the autouse
    fixture left the suite green on any machine that never had the five
    variables set - which is every CI runner, i.e. exactly the environment E3
    exists to reproduce. Setting a sentinel first and then calling the helper
    fails on any machine if the helper stops working.
    """
    assert socket_guard.credential_vars, "the credential list is empty"
    for name in socket_guard.credential_vars:
        monkeypatch.setenv(name, f"sentinel-{name}")
    for name in socket_guard.credential_vars:
        assert os.environ[name] == f"sentinel-{name}"

    socket_guard.unset(monkeypatch)

    for name in socket_guard.credential_vars:
        assert name not in os.environ, f"{name} survived unset_credentials"


# ---------------------------------------------------------------------------
# The frozen clock - section 4.1
# ---------------------------------------------------------------------------


def test_frozen_clock_is_pinned_to_the_demo_anchor(frozen_clock) -> None:
    assert frozen_clock.now().isoformat() == "2026-09-18T09:00:00-04:00"
    assert frozen_clock.now_utc().isoformat() == "2026-09-18T13:00:00+00:00"
    assert frozen_clock.now() == frozen_clock.now()


def test_frozen_clock_rejects_a_naive_instant(frozen_clock) -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        type(frozen_clock)(datetime(2026, 9, 18, 9, 0, 0))


def test_the_deposit_is_ninety_five_days_overdue_at_the_anchor(frozen_clock) -> None:
    """``CANONICAL_DECISIONS.md``, hero commit canon, the ``due_at`` row.

    "Every 'days overdue' figure derives from this against the 2026-09-18 demo
    clock - 95 days." If this assertion fails, either the anchor moved or the
    deposit deadline did, and every figure in the submission is wrong.
    """
    deposit_due = date(2026, 6, 15)
    assert (frozen_clock.now_utc().date() - deposit_due).days == 95
