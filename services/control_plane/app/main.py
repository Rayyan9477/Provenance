"""ASGI entry point: ``uvicorn --factory services.control_plane.app.main:build_app``.

Authority: ``implementation/00_IMPLEMENTATION_MAP.md`` section 4.2 (deployment
unit 2 is imported by path, not installed), and ``specs/15_API_SPEC.md``
section 14 for what has to be resolved before the first request.

This module is the *only* place in the control plane that reads settings, and
it does so once. Everything downstream takes an
:class:`~services.control_plane.app.api.config.ApiConfig` and a
:class:`~services.control_plane.app.api.config.Dependencies` as arguments, which
is what makes the hermetic API suites possible: they call
:func:`~services.control_plane.app.api.app.create_app` directly with in-memory
ports and never import this file.

What changed here, and why the old refusal is gone
---------------------------------------------------
This function used to raise ``NotImplementedError``. Its reason was accurate at
the time: ``provenance_db.repositories`` was shape-only, every body raised, and
binding the ports here would have meant writing a second copy of every scoping
predicate -- which is how a cross-user leak gets in. That precondition no
longer holds. The repositories carry the statements, the Memory Kernel's
``commit_proposal`` is the single canonical write path, and the wiring below is
what is left: it chooses which repository call answers which port method and
hands each subsystem the credential it is entitled to.

Wiring is not connecting
-------------------------
:func:`build_runtime` resolves configuration and *constructs* pools; it opens
no socket. ``RolePool`` resolves its DSN and builds its ``psycopg_pool`` inside
``open()``, and that separation is deliberate: a process that cannot reach
CockroachDB still starts far enough to answer ``GET /v1/healthz`` and to report
``db_ok: false`` on ``GET /v1/version``, rather than crash-looping before it
can say anything at all. The connecting happens in :meth:`Runtime.start`,
which the application calls on startup.

One pool per SQL role
----------------------
``pv_app_reader_writer`` for reads, ``pv_kernel_writer`` for the Kernel.
``CANONICAL_DECISIONS.md`` -> *Canonical writer* makes "only the Kernel writes
canonical tables" a **grant**, not a convention, and a single pool with a
role-switching argument would turn it back into one: the property would then
depend on call order rather than on credentials. ``pv_migrator`` and
``pv_ops_reader`` are refused outright by ``application_pool``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from provenance_db.pools import RolePool, SqlRole, application_pool
from services.control_plane.app.actions import ActionPolicy, DemoSink
from services.control_plane.app.api.adapters import (
    DbHealth,
    KernelInternalPort,
    KernelWritePort,
    SqlCapabilityStore,
    SqlReadPort,
    SqlUserDirectory,
)
from services.control_plane.app.api.app import create_app
from services.control_plane.app.api.config import ApiConfig, Dependencies
from services.control_plane.app.api.idempotency import InMemoryIdempotencyStore
from services.control_plane.app.auth.jwt import CachingJwksProvider, https_jwks_fetcher
from services.control_plane.app.ingestion import artifacts as ingestion_artifacts
from services.control_plane.app.storage import object_store_for

__all__ = ["Runtime", "build_app", "build_dependencies", "build_runtime"]


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything one serving process owns, and its lifecycle.

    ``Dependencies`` is what the request path sees; this is what the *process*
    holds. The two are separate because a pool has a lifecycle and a
    dependency container does not: ``Dependencies`` is frozen, injected, and
    read on every request, and giving it ``open()`` and ``close()`` would put
    process lifecycle inside a per-request object.

    ``repr`` is suppressed on every field that could reach a DSN. ``D-00-019``
    is a live credential that reached a pytest failure header, and a frozen
    dataclass renders every field it is asked to.
    """

    config: ApiConfig = field(repr=False)
    deps: Dependencies = field(repr=False)
    pools: tuple[RolePool, ...] = field(repr=False)
    health: DbHealth = field(repr=False)

    async def start(self) -> None:
        """Open every pool, then begin observing readiness.

        A pool that cannot open does **not** stop the process. The refusal is
        recorded as ``db_ok: false`` and the unauthenticated
        ``GET /v1/version`` reports it, which is far more useful to whoever is
        debugging the deploy than a container that exits before it can be
        curled. Authenticated routes will fail on their own, at the point of
        use, with the real database error.
        """
        for pool in self.pools:
            # Broad on purpose and narrow in effect. ``open()`` can fail with a
            # DNS error, a TLS refusal, an expired credential or a timeout, and
            # every one of them means the same thing at this point: not ready.
            # The suppression covers exactly one statement, and what it hides
            # is immediately re-reported through ``db_ok``.
            with contextlib.suppress(Exception):
                await pool.open()
        await self.health.start()

    async def stop(self) -> None:
        """Stop the readiness task and close every pool.

        Idempotent, and safe when :meth:`start` never ran or failed part-way:
        a process that cannot close pools it failed to open leaves the event
        loop complaining on every failed deploy, which buries the actual
        cause.
        """
        await self.health.stop()
        for pool in self.pools:
            await pool.close()


def build_runtime(settings: Any, *, config: ApiConfig | None = None) -> Runtime:
    """Bind the ports to the real database, JWKS endpoint and Kernel.

    Args:
        settings: a ``provenance_contracts.settings.Settings``, or anything
            with the same attribute surface. Typed ``Any`` deliberately, for
            the same reason ``ApiConfig.from_settings`` is: importing this
            module must never import ``Settings`` and never read the
            environment at import time.
        config: an already-resolved :class:`ApiConfig`. Passed by
            :func:`build_app` so the object the routes read is the same one
            the JWKS provider was pointed at -- ``ApiConfig`` stamps
            ``built_at`` and selects the identity provider at construction, so
            building it twice creates two objects that can disagree.

    Returns:
        A :class:`Runtime`. No socket has been opened when it returns.
    """
    resolved = config if config is not None else ApiConfig.from_settings(settings)

    app_pool = application_pool(
        SqlRole.APP,
        settings,
        min_size=settings.cockroach_pool_min,
        max_size=settings.cockroach_pool_max,
        statement_timeout_ms=settings.cockroach_statement_timeout_ms,
    )
    kernel_pool = application_pool(
        SqlRole.KERNEL,
        settings,
        min_size=settings.cockroach_pool_min,
        max_size=settings.cockroach_pool_max,
        statement_timeout_ms=settings.cockroach_statement_timeout_ms,
    )

    clock = _clock()

    # The action plane's two policy objects. `ActionPolicy.from_settings` is
    # default-closed: an empty allowlist permits no recipient at all, so a
    # deployment that forgot to configure one refuses to send rather than
    # sending somewhere unreviewed. `DemoSink` is the transport: SES was not
    # built (the pivot), and the sink records what it was asked to send under
    # `provider = "SAFE_SINK"` so a demo is visibly a demo.
    policy = ActionPolicy.from_settings(settings)
    sink = DemoSink()

    # One store, shared by the human upload path and the inbound worker path.
    # `PV_PLATFORM` selects it, exactly as it selects the identity provider:
    # storage must not be able to disagree with authentication about which
    # cloud this deployment is on.
    objects = object_store_for(settings)
    # `agent_runs.model_route` is what makes the model disclosure checkable
    # against persisted state (`CANONICAL_DECISIONS.md` -> *Disclosure*), so the
    # ids a run records are the ids this deployment configured, never a
    # constant that could drift from them.
    model_route = _model_route(settings)

    read = SqlReadPort(
        app_pool,
        feature_flags=_feature_flags(settings, objects),
        clock=clock,
    )
    write = KernelWritePort(
        app_pool,
        kernel_pool=kernel_pool,
        read=read,
        policy=policy,
        clock=clock,
        objects=objects,
        model_route=model_route,
        upload_url_ttl_seconds=settings.upload_url_ttl_seconds,
    )
    internal = KernelInternalPort(
        app_pool,
        kernel_pool=kernel_pool,
        read=read,
        policy=policy,
        sink=sink,
        clock=clock,
        objects=objects,
        model_route=model_route,
    )
    health = DbHealth(app_pool)

    deps = Dependencies(
        jwks=CachingJwksProvider(https_jwks_fetcher(resolved.provider.jwks_url)),
        users=SqlUserDirectory(app_pool),
        capabilities=SqlCapabilityStore(app_pool),
        idempotency=InMemoryIdempotencyStore(),
        read=read,
        write=write,
        internal=internal,
        db_ok=health.ok,
    )
    return Runtime(config=resolved, deps=deps, pools=(app_pool, kernel_pool), health=health)


def build_dependencies(settings: Any) -> Dependencies:
    """The dependency container alone, for a caller that owns the lifecycle.

    Kept because it is the name ``T8.9`` and the surrounding documentation use,
    and because it is the smaller question: "what does the request path reach
    for?" :func:`build_runtime` answers the larger one, "what does the process
    own?", and this delegates so there is one wiring and not two.
    """
    return build_runtime(settings).deps


def _model_route(settings: Any) -> dict[str, str]:
    """``agent_runs.model_route``, read off the configured ids.

    Not a constant: a deployment that pointed both tiers at one model -- the
    documented response to a Tier R capacity failure -- must record the ids it
    actually routed on, because ``proposals/submission.resolve_attribution``
    refuses a proposal whose claimed model is not the one this column holds.
    """
    return {
        "tier_e": getattr(settings, "gemini_extraction_model_id", None)
        or ingestion_artifacts.DEFAULT_MODEL_ROUTE["tier_e"],
        "tier_r": getattr(settings, "gemini_reasoning_model_id", None)
        or ingestion_artifacts.DEFAULT_MODEL_ROUTE["tier_r"],
        "embeddings": ingestion_artifacts.DEFAULT_MODEL_ROUTE["embeddings"],
    }


def _feature_flags(settings: Any, objects: Any = None) -> dict[str, bool]:
    """Section 8.3's ``feature_flags``, derived from what is actually configured.

    Each flag is read off the presence of the thing it enables rather than
    from a switch of its own. A deployment with no object-store bucket that
    advertised ``upload_ingest_enabled: true`` would send the UI to an upload
    screen that cannot work, and section 8.3 tells clients to treat an absent
    flag as ``false`` -- so the safe direction is the default.

    ``fixture_mode`` is deliberately not here. ``CANONICAL_DECISIONS.md`` ->
    *Operating-mode disclosure* makes ``GET /v1/version`` the single
    authoritative channel and ``GET /v1/me.feature_flags.fixture_mode`` its
    UI-binding mirror; the route merges it from :class:`ApiConfig` so the two
    are one value read twice rather than two values that can drift.
    """
    return {
        "ses_inbound_enabled": bool(getattr(settings, "ses_ingest_domain", None)),
        # Read off the store's own capability rather than off a bucket name.
        # The filesystem store really stores objects -- it is the correct store
        # for `PV_PLATFORM=local` -- but a browser cannot `PUT` to a `file:`
        # URL, and a UI sent to an upload screen that cannot work is the
        # "URL that does not work" failure one layer up. `browser_uploadable`
        # is the property that distinguishes the two, so the flag says what a
        # *client* can do rather than what the server has configured.
        "upload_ingest_enabled": bool(
            getattr(objects, "browser_uploadable", False)
            if objects is not None
            else (
                getattr(settings, "s3_artifact_bucket", None)
                or getattr(settings, "gcs_artifact_bucket", None)
            )
        ),
        # The counterfactual runs the graph twice against a live model
        # (`CANONICAL_DECISIONS.md` -> *Counterfactual*: same artifact, model,
        # prompt and graph on both sides). In FIXTURE or DEGRADED mode there is
        # no second run to compare, so the honest flag is false.
        "counterfactual_enabled": getattr(settings, "pv_agent_mode", "LIVE") == "LIVE",
        "mcp_trace_visible": bool(getattr(settings, "pv_mcp_enabled", False)),
    }


def _clock() -> Any:
    """The process clock, as a callable.

    Indirected so the read port takes a clock rather than reading the wall.
    "Overdue" is a comparison against an instant, and the case header, the
    commitment list and the trigger predicate must all get the same answer --
    ``CANONICAL_DECISIONS.md`` -> *Deposit ``due_at``* derives "95 days" from
    one instant, and three surfaces reading three clocks is how that becomes
    three numbers.
    """
    from datetime import UTC, datetime

    def _now() -> datetime:
        return datetime.now(UTC)

    return _now


def build_app() -> FastAPI:
    """Resolve settings once, then hand over an application.

    Served as a **factory**::

        uvicorn --factory services.control_plane.app.main:build_app

    rather than as a module-level ``app``. ``00_IMPLEMENTATION_MAP.md`` section
    4.2 writes the latter, and the deviation is deliberate and recorded: a
    module-level ``app`` resolves ``Settings`` at *import*, so any tool that
    imports this module -- a linter walking the tree, a mistaken test
    collection -- fails with a pydantic validation error about an unset
    environment variable rather than doing its job. A factory moves that
    resolution to the moment the server actually starts, which is the only
    moment it is meaningful.

    The lifecycle handlers are attached here rather than inside ``create_app``
    for the same reason ``create_app`` takes a ``Dependencies``: that function
    must build a complete application with no I/O, so that the hermetic suites
    and ``tools/export_openapi.py`` work on a machine with no database and no
    credential. Opening pools is this function's job, because this is the
    function that resolved the credentials.
    """
    from provenance_contracts.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    config = ApiConfig.from_settings(settings)
    runtime = build_runtime(settings, config=config)
    app = create_app(config=runtime.config, deps=runtime.deps)
    app.add_event_handler("startup", runtime.start)
    app.add_event_handler("shutdown", runtime.stop)
    return app
