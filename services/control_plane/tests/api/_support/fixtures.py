"""Shared pytest fixtures for the Phase 8 API and auth suites.

They live in a plain module rather than in a ``conftest.py`` because both
``tests/api`` and ``tests/auth`` need them and pytest 8 no longer allows a
non-top-level ``conftest`` to declare ``pytest_plugins``. Each suite's
``conftest.py`` star-imports this module, so there is one definition rather
than two that can drift.

Everything here is hermetic: no database, no network, no credential. The
control plane is built through :func:`create_app` with in-memory ports, and
tokens are signed by a keypair minted at session start.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest

from _support import fakes as fakes_mod
from _support.rsa import RsaKeyPair, generate_keypair
from _support.tokens import (
    AGENT_CLIENT_ID,
    ISSUER,
    WEB_CLIENT_ID,
    WORKER_CLIENT_ID,
    agent_token,
    human_token,
    worker_token,
)
from services.control_plane.app.api.app import create_app
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.idempotency import InMemoryIdempotencyStore
from services.control_plane.app.auth.jwt import StaticJwksProvider

__all__ = [
    "CAPABILITY_KEY",
    "CURSOR_KEY",
    "SESSION_EVENT_LOOP",
    "_owned_event_loop",
    "close_session_event_loop",
    "agent_bearer",
    "agent_headers",
    "alex_token",
    "api_config",
    "app",
    "auth_alex",
    "auth_rob",
    "capability_proof",
    "client",
    "deps",
    "fixture",
    "idem",
    "other_key",
    "rob_token",
    "signing_key",
    "worker_bearer",
    "worker_headers",
]


#: An event loop for the main thread, created at *import* -- which is to say
#: during collection, before the first test in the session runs.
#:
#: Not a convenience. ``pytest-asyncio`` 0.24's ``_temporary_event_loop_policy``
#: opens every async test with ``asyncio.get_event_loop()``, purely to remember
#: what to restore afterwards. On a thread with no current loop that call
#: *constructs* one, and on Windows a ``ProactorEventLoop`` builds its self-pipe
#: out of ``socket.socketpair()``. The loop is never run and never closed, so
#: the pair survives until the garbage collector reaches it -- at which point
#: CPython emits ``ResourceWarning: unclosed socket`` from ``__del__``,
#: ``pyproject.toml``'s ``filterwarnings = ["error"]`` promotes it, and pytest
#: reports an ERROR against whichever unrelated test happened to be starting.
#: The failure is real, it is not about that test, and it moves when the suite
#: is reordered -- which is the worst kind of red.
#:
#: Owning the loop removes the ambiguity: ``get_event_loop()`` finds one and
#: constructs nothing. It is created at import rather than in a fixture because
#: the first async test in the session is in ``packages/`` -- earlier than any
#: fixture in this file could possibly run -- and conftest import happens during
#: collection, which precedes every test. :func:`close_session_event_loop` is
#: called from each suite's ``pytest_sessionfinish``.
SESSION_EVENT_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(SESSION_EVENT_LOOP)


@pytest.fixture(autouse=True)
def _owned_event_loop() -> Iterator[None]:
    """Reinstate :data:`SESSION_EVENT_LOOP` around every test.

    ``asyncio.run()`` -- which two tests in ``tests/auth`` call directly --
    clears the current loop on the way out. Without this the next async test
    would find no loop and construct the leaking one all over again.
    """
    asyncio.set_event_loop(SESSION_EVENT_LOOP)
    try:
        yield
    finally:
        asyncio.set_event_loop(SESSION_EVENT_LOOP)


def close_session_event_loop() -> None:
    """Close the loop this module created. Called from ``pytest_sessionfinish``."""
    asyncio.set_event_loop(None)
    if not SESSION_EVENT_LOOP.is_closed():
        SESSION_EVENT_LOOP.close()


CURSOR_KEY = b"cursor-key-for-tests-only-not-a-secret"
CAPABILITY_KEY = b"capability-key-for-tests-only-not-a-secret"


def idem(value: str) -> dict[str, str]:
    """A well-formed ``Idempotency-Key`` header, deterministic per *value*."""
    return {"Idempotency-Key": f"pv-test-key-{value}".ljust(20, "0")[:64]}


@pytest.fixture(scope="session")
def signing_key() -> RsaKeyPair:
    return generate_keypair()


@pytest.fixture(scope="session")
def other_key() -> RsaKeyPair:
    """A second keypair whose ``kid`` the JWKS does not publish."""
    return generate_keypair(kid="pv-test-kid-unknown")


@pytest.fixture
def fixture() -> fakes_mod.Fixture:
    return fakes_mod.build_fixture()


@pytest.fixture
def api_config() -> ApiConfig:
    return ApiConfig(
        git_sha="9c1f2ad",
        region="us-east-1",
        built_at=fakes_mod.NOW,
        schema_revision="0008_events_infrastructure",
        fixture_mode=False,
        agent_mode="LIVE",
        otlp_export="ENABLED",
        cognito_issuer=ISSUER,
        client_id_names={
            WEB_CLIENT_ID: "provenance-web",
            AGENT_CLIENT_ID: "provenance-agent-runtime",
            WORKER_CLIENT_ID: "provenance-workers",
        },
        judge_group="provenance-judges",
        cursor_hmac_key=CURSOR_KEY,
        capability_hmac_key=CAPABILITY_KEY,
        ingest_domain="in.provenance.app",
    )


@pytest.fixture
def deps(fixture: fakes_mod.Fixture, signing_key: RsaKeyPair) -> Dependencies:
    return Dependencies(
        jwks=StaticJwksProvider(signing_key.jwks()),
        users=fixture.users,
        capabilities=fixture.capabilities,
        idempotency=InMemoryIdempotencyStore(),
        read=fixture.read,
        write=fixture.write,
        internal=fixture.internal,
        clock=lambda: fakes_mod.NOW,
        db_ok=lambda: True,
    )


@pytest.fixture
def app(api_config: ApiConfig, deps: Dependencies) -> Any:
    return create_app(config=api_config, deps=deps)


@pytest.fixture
def client(app: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def alex_token(signing_key: RsaKeyPair) -> str:
    return human_token(signing_key, sub=fakes_mod.ALEX.cognito_sub, groups=("provenance-judges",))


@pytest.fixture
def rob_token(signing_key: RsaKeyPair) -> str:
    return human_token(signing_key, sub=fakes_mod.ROB.cognito_sub)


@pytest.fixture
def agent_bearer(signing_key: RsaKeyPair) -> str:
    return agent_token(signing_key)


@pytest.fixture
def worker_bearer(signing_key: RsaKeyPair) -> str:
    return worker_token(signing_key)


@pytest.fixture
def auth_alex(alex_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {alex_token}"}


@pytest.fixture
def auth_rob(rob_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {rob_token}"}


@pytest.fixture
def capability_proof() -> Callable[[str, str | uuid.UUID, datetime], str]:
    from services.control_plane.app.auth.capability_proof import issue_capability_proof

    def _proof(kind: str, capability_key: str | uuid.UUID, expires_at: datetime) -> str:
        return issue_capability_proof(kind, capability_key, expires_at, key=CAPABILITY_KEY)

    return _proof


@pytest.fixture
def agent_headers(
    agent_bearer: str,
    capability_proof: Callable[[str, str | uuid.UUID, datetime], str],
    fixture: fakes_mod.Fixture,
) -> Callable[..., dict[str, str]]:
    def _headers(actor: fakes_mod.Actor = fakes_mod.ALEX) -> dict[str, str]:
        record = fixture.capabilities.records[("AGENT_RUN", str(actor.agent_run_id))]
        return {
            "Authorization": f"Bearer {agent_bearer}",
            "X-Provenance-Capability-Proof": capability_proof(
                "AGENT_RUN", actor.agent_run_id, record.expires_at
            ),
        }

    return _headers


@pytest.fixture
def worker_headers(
    worker_bearer: str,
    capability_proof: Callable[[str, str | uuid.UUID, datetime], str],
    fixture: fakes_mod.Fixture,
) -> Callable[..., dict[str, str]]:
    def _headers(kind: str, key: str | uuid.UUID) -> dict[str, str]:
        record = fixture.capabilities.records[(kind, str(key))]
        return {
            "Authorization": f"Bearer {worker_bearer}",
            "X-Provenance-Capability-Proof": capability_proof(kind, key, record.expires_at),
        }

    return _headers
