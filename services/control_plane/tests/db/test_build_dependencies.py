"""``build_dependencies()`` returns, and returns something usable -- ``T8.9``.

Authority
---------
- ``services/control_plane/app/main.py`` -- the function under test and the
  reasoning recorded above it.
- ``specs/15_API_SPEC.md`` sections 8.2 and 14.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer*: the Kernel holds
  ``pv_kernel_writer`` and the read path holds ``pv_app_reader_writer``. One
  pool per role, so "which role wrote this row" has an answer at run time.

Why this file is marked ``unit`` although it lives in ``tests/db/``
-------------------------------------------------------------------
The same reason ``packages/python/provenance_db/tests/db/test_repository_read_only.py``
is: the path groups it with the database lane, and the marker describes what
the test *needs*. This one needs no cluster, and that is the point of the
first assertion below -- **wiring is not connecting**. ``build_dependencies``
resolves configuration and constructs pools; it opens no socket, so a process
that cannot reach CockroachDB still starts far enough to answer
``GET /v1/healthz`` and to report ``db_ok: false`` on ``GET /v1/version``
rather than crash-looping before it can say anything at all.

No DSN appears anywhere in this module. The stub source below hands out a
syntactically valid ``sslmode=verify-full`` URL with no host and no credential,
and it is never printed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import SecretStr

from services.control_plane.app.api.config import ApiConfig, Dependencies

pytestmark = pytest.mark.unit

#: Not a credential and not reachable: no host, no password, and the database
#: name is a reserved-invalid label. It exists so ``resolve_role_dsn`` has
#: something shaped like a URL to refuse or accept, and it is never connected
#: to by anything in this module.
_INERT_DSN = "postgresql://pv@127.0.0.1:1/nowhere?sslmode=verify-full"


class StubSettings:
    """The subset of ``provenance_contracts.settings.Settings`` startup reads.

    ``ApiConfig.from_settings`` and ``build_dependencies`` both take ``Any``
    deliberately, so that importing either module never imports ``Settings``
    and never reads the environment at import time. A stub is therefore the
    honest test double: it exercises exactly the attribute surface production
    reads, and a new required attribute shows up here as an ``AttributeError``
    rather than as a silent default.
    """

    pv_platform = "local"
    cockroach_pool_min = 2
    cockroach_pool_max = 10
    cockroach_statement_timeout_ms = 15_000

    cursor_hmac_key = SecretStr("cursor-key-for-tests-0000000000000000")
    provenance_capability_hmac_key = SecretStr("capability-key-for-tests-00000000000")

    ses_ingest_domain = None
    s3_artifact_bucket = None
    gcs_artifact_bucket = None
    pv_mcp_enabled = True
    pv_agent_mode = "LIVE"

    # The four fields ``ActionPolicy.from_settings`` narrows itself to. An
    # empty allowlist is not an oversight in this stub: the policy is
    # default-closed, so this asserts that a deployment which configured no
    # recipients can still be *wired* -- it simply refuses to send.
    action_allowlist_addresses: tuple[str, ...] = ()
    pv_action_execution_mode = "ENABLED"
    action_recipient_mode = "DEMO_SINK"
    ses_demo_sink_domain = None
    build_sha = "0" * 40
    aws_region = None
    google_cloud_region = "us-east4"
    schema_revision = "0008_events_infrastructure"
    cognito_judge_group = None
    max_artifact_bytes = 20_971_520
    otel_exporter_otlp_endpoint = None
    # Section 8.18's pre-signed-URL lifetime, read by ``KernelWritePort`` so a
    # deployment's configured TTL is the one an upload target carries.
    upload_url_ttl_seconds = 900
    download_url_ttl_seconds = 300
    # ``agent_runs.model_route`` is built from these. They are read rather than
    # constant so a run records the ids this deployment actually routed on --
    # ``proposals/submission.resolve_attribution`` refuses a proposal whose
    # claimed model is not the one that column holds.
    gemini_extraction_model_id = "gemini-3.5-flash-lite"
    gemini_reasoning_model_id = "gemini-3.7-flash"

    def dsn_for_role(self, role: str) -> SecretStr:
        del role
        return SecretStr(_INERT_DSN)


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> StubSettings:
    """``PV_PLATFORM=local`` needs its signing secret, and refuses a default."""
    monkeypatch.setenv("PV_LOCAL_AUTH_SECRET", "local-development-signing-secret")
    return StubSettings()


# ==========================================================================


def test_build_dependencies_no_longer_raises(settings: StubSettings) -> None:
    """The blocker, stated as an assertion.

    Until ``T8.9`` this function raised ``NotImplementedError`` because
    ``provenance_db.repositories`` was shape-only. It is not any more, so the
    refusal is now the bug.
    """
    from services.control_plane.app.main import build_dependencies

    deps = build_dependencies(settings)
    assert isinstance(deps, Dependencies)


def test_every_dependency_field_is_populated(settings: StubSettings) -> None:
    """A ``None`` in this object is a request-time ``AttributeError`` later.

    Iterating the dataclass rather than naming seven fields is deliberate:
    ``Dependencies`` is owned by another task and may gain a field while this
    one is in flight, and a field added without wiring should fail here.
    """
    import dataclasses

    from services.control_plane.app.main import build_dependencies

    deps = build_dependencies(settings)
    unset = [f.name for f in dataclasses.fields(deps) if getattr(deps, f.name) is None]
    assert unset == [], f"unwired dependencies: {unset}"


def test_wiring_opens_no_connection(settings: StubSettings) -> None:
    """Construction is not connection.

    ``RolePool`` resolves its DSN and builds its ``psycopg_pool`` inside
    ``open()``, never in ``__init__``. If that ever changes, this test starts
    failing in the hermetic lane -- which is exactly where a startup that
    silently requires a reachable cluster should be caught.
    """
    from services.control_plane.app.main import build_runtime

    runtime = build_runtime(settings)
    assert runtime.pools, "no pool was constructed"
    assert all(not pool.is_open for pool in runtime.pools)


def test_the_read_path_and_the_kernel_hold_different_roles(settings: StubSettings) -> None:
    """One pool per SQL role. A single pool with a role argument would turn
    "only the Kernel writes canonical tables" back into a convention."""
    from provenance_db.pools import SqlRole
    from services.control_plane.app.main import build_runtime

    roles = {pool.role for pool in build_runtime(settings).pools}
    assert SqlRole.APP in roles
    assert SqlRole.KERNEL in roles
    assert SqlRole.MIGRATOR not in roles, "a serving process must not hold DDL rights"


def test_db_ok_starts_false_and_is_a_callable_bit(settings: StubSettings) -> None:
    """Section 8.2. Before anything has been observed the honest answer is
    ``false``; optimism here would make an unauthenticated endpoint assert a
    readiness nobody measured."""
    from services.control_plane.app.main import build_dependencies

    deps = build_dependencies(settings)
    assert callable(deps.db_ok)
    assert deps.db_ok() is False


def test_the_ports_are_the_sql_adapters_not_stubs(settings: StubSettings) -> None:
    from services.control_plane.app.api import adapters
    from services.control_plane.app.main import build_dependencies

    deps = build_dependencies(settings)
    assert isinstance(deps.read, adapters.SqlReadPort)
    assert isinstance(deps.write, adapters.KernelWritePort)
    assert isinstance(deps.internal, adapters.KernelInternalPort)
    assert isinstance(deps.users, adapters.SqlUserDirectory)


def test_an_application_can_be_assembled_from_the_result(settings: StubSettings) -> None:
    """The end of the spine: config plus dependencies produce a real app.

    ``make run-api`` fails at exactly one of two places -- resolving settings,
    or this call. Asserting the second here means a failure in CI names the
    wiring rather than the uvicorn command line.
    """
    from services.control_plane.app.api.app import create_app
    from services.control_plane.app.main import build_runtime

    runtime = build_runtime(settings)
    app = create_app(config=runtime.config, deps=runtime.deps)
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/v1/version" in paths
    assert "/v1/cases/{case_id}/state-proof" in paths


def test_the_config_is_built_once_and_shared(settings: StubSettings) -> None:
    """``ApiConfig`` stamps ``built_at`` at construction and selects the
    identity provider; building it twice would let the object the routes read
    disagree with the object the JWKS provider was pointed at."""
    from services.control_plane.app.main import build_runtime

    runtime = build_runtime(settings)
    assert isinstance(runtime.config, ApiConfig)
    assert runtime.config.platform == "local"


async def test_start_and_stop_are_idempotent_without_a_cluster(settings: StubSettings) -> None:
    """Shutdown must work even when startup never reached the cluster.

    A process that cannot close pools it failed to open leaves the event loop
    complaining on every failed deploy, which buries the actual cause.
    """
    from services.control_plane.app.main import build_runtime

    runtime = build_runtime(settings)
    await runtime.stop()
    await runtime.stop()
    assert runtime.deps.db_ok() is False


def test_no_dsn_is_reachable_from_the_runtime_repr(settings: StubSettings) -> None:
    """``D-00-019``: a frozen dataclass renders every field, and these objects
    are constructed in fixtures and therefore printed in failure headers."""
    from services.control_plane.app.main import build_runtime

    rendered = repr(build_runtime(settings))
    assert "postgres" not in rendered
    assert "sslmode" not in rendered


def test_the_directory_resolves_users_and_nothing_else(settings: StubSettings) -> None:
    """``UserDirectory`` is one method by design: section 2.5 refuses to
    auto-create a user, so the directory has no write to be asked for."""
    from services.control_plane.app.main import build_dependencies

    deps = build_dependencies(settings)
    public = {name for name in dir(deps.users) if not name.startswith("_")}
    assert public == {"by_cognito_sub"}, public


def test_unknown_scope_is_never_fabricated(settings: StubSettings) -> None:
    """``OwnerScope`` is constructible only from a verified principal or a
    server-resolved binding; there is no factory taking raw ids off a request."""
    from services.control_plane.app.api.ports import OwnerScope

    factories = {name for name in dir(OwnerScope) if not name.startswith("_")}
    assert factories == {"of", "of_binding", "tenant_id", "user_id"}
    assert OwnerScope(tenant_id=uuid.uuid4(), user_id=uuid.uuid4()) is not None


def test_the_action_planes_kill_switch_reaches_the_ports(
    settings: StubSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``G9.6``'s rollback position is a wire, not a setting.

    "Set ``PV_ACTION_EXECUTION_MODE=DISABLED``; approvals continue to be
    recorded, nothing is sent" is only true if the value reaches the
    ``ActionPolicy`` the executor consults. A policy built from defaults would
    read ``ENABLED`` no matter what the operator set, and the failure would be
    invisible until the moment somebody needed the switch -- which is, by
    construction, the moment something has already gone wrong.

    Asserted through the port's own policy rather than by re-reading the
    setting, because "the setting says DISABLED" and "the executor will refuse
    to send" are different claims and only the second one matters.
    """
    from services.control_plane.app.main import build_runtime

    monkeypatch.setattr(settings, "pv_action_execution_mode", "DISABLED", raising=False)
    deps = build_runtime(settings).deps

    assert deps.write._policy.execution_enabled is False
    assert deps.internal._policy.execution_enabled is False


def test_the_recipient_allowlist_is_default_closed_end_to_end(settings: StubSettings) -> None:
    """An unset ``PV_ACTION_ALLOWLIST`` must permit nobody, at the port.

    ``ActionPolicy`` reads an empty allowlist as "no recipient is permitted",
    which is the only reading that makes the variable a safety control. The
    stub carries ``action_allowlist_addresses = ()`` deliberately, so this
    asserts that a deployment which configured no recipients is still wirable
    and simply refuses to send -- rather than starting up permissive.
    """
    from services.control_plane.app.main import build_runtime

    policy = build_runtime(settings).deps.internal._policy
    assert policy.recipient_allowlisted("billing@northlinefiber.example") is False
    # And the null recipient stays permitted: `ck_action_intents_recipient`
    # allows one only for INTERNAL_REMINDER, and an action that reaches nobody
    # has nothing for an allowlist to decide.
    assert policy.recipient_allowlisted(None) is True


def test_the_outbound_sink_is_wired_and_is_not_a_real_transport(
    settings: StubSettings,
) -> None:
    """The provider a demo would actually record under.

    ``ck_action_executions_provider`` admits ``SES``, ``SAFE_SINK`` and
    ``SIMULATOR``. SES was never built (the pivot), so a port wired to
    something claiming ``SES`` would put an unreachable transport on the
    demo's critical path and label the resulting rows with a provider that
    never saw the message.
    """
    from services.control_plane.app.main import build_runtime

    assert build_runtime(settings).deps.internal._sink.provider == "SAFE_SINK"


def test_settings_stub_covers_what_production_reads(settings: StubSettings) -> None:
    """The vacuity guard on the stub itself.

    If ``build_runtime`` stopped reading settings altogether -- say by
    hard-coding a pool size -- every test above would still pass. This asserts
    the stub is actually consulted.
    """
    from services.control_plane.app.main import build_runtime

    read: list[str] = []

    class Watched(StubSettings):
        def __getattribute__(self, name: str) -> Any:
            if not name.startswith("_"):
                read.append(name)
            return object.__getattribute__(self, name)

    build_runtime(Watched())
    assert "cockroach_pool_max" in read
    assert "pv_platform" in read
    # The four fields `ActionPolicy.from_settings` narrows itself to. A wiring
    # that stopped consulting them would leave the kill switch and the
    # allowlist at their defaults while every test above still passed, because
    # the defaults are the same shape as a real configuration.
    for field in (
        "action_allowlist_addresses",
        "pv_action_execution_mode",
        "action_recipient_mode",
        "ses_demo_sink_domain",
    ):
        assert field in read, f"the action policy was built without reading {field}"
    # The ingestion path's three, for the same reason: a wiring that stopped
    # reading them would silently store objects somewhere else and attribute
    # every run to a hard-coded pair of model ids.
    for field in (
        "upload_url_ttl_seconds",
        "gemini_extraction_model_id",
        "gemini_reasoning_model_id",
    ):
        assert field in read, f"the ingestion path was wired without reading {field}"
    # And the mirror of `test_wiring_opens_no_connection`, from the settings
    # side: no credential is even *read* during construction, let alone used.
    # `RolePool` resolves its DSN inside `open()`, which is what lets a process
    # with unreachable secrets still start and report `db_ok: false`.
    assert "dsn_for_role" not in read
