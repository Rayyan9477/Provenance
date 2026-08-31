"""The production wiring for sections 8.30, 8.31 and 8.33.

Kept out of ``write.py`` so the adapter imports one factory rather than five
collaborators, and out of ``service.py`` so the service stays constructible
with no SDK, no key and no pool -- which is what makes the whole of sections
8.30 and 8.31 unit-testable.

Where the model configuration comes from
-----------------------------------------
``os.environ``, through ``GeminiRouterConfig.from_env``, which is the one
constructor that reads it. ``provenance_contracts.settings`` deliberately does
not carry the ``GEMINI_*`` fields yet -- ``models.py`` records that and records
what happens when it does: "the mapping comes from there instead, with no
change to any caller here". Reading it **lazily**, at the moment a
counterfactual is requested, rather than at import: a process that starts
before a key is present must still start, and must refuse the endpoint rather
than fail to boot.

Why the transport is ``gemini_transport`` and not the SDK's own callable
-------------------------------------------------------------------------
``wire_schema.py`` exists because ``google.genai.types.Schema`` is
``extra="forbid"`` and refuses several keywords a pydantic contract emits.
``gemini_transport`` is the seam that rewrites the schema on the way out.
Passing ``client.models.generate_content`` directly here would work for the
router suite's ``ToyOutput`` and fail on the first real call -- which is
exactly how that module came to be written.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents.runtime.model_router.gemini import GeminiClient
from agents.runtime.model_router.models import GeminiRouterConfig
from agents.runtime.model_router.router import ModelRouter
from agents.runtime.model_router.wire_schema import gemini_transport
from services.control_plane.app.api.adapters.catalog import ConnectionSource
from services.control_plane.app.api.ports import ReadPort
from services.control_plane.app.counterfactual.probe import ModelProbeService
from services.control_plane.app.counterfactual.service import CounterfactualService
from services.control_plane.app.counterfactual.sql import SqlCounterfactualStore

__all__ = [
    "default_counterfactual_service",
    "default_probe_service",
    "live_router",
]


def _config() -> GeminiRouterConfig:
    return GeminiRouterConfig.from_env(os.environ)


def live_router() -> ModelRouter:
    """A router over the live Gemini Developer API.

    Raises:
        ModelConfigError: no ``GOOGLE_API_KEY``, or a configured id outside the
            allow-set. Both are refusals the caller records rather than
            exceptions the caller swallows -- ``CounterfactualService.start``
            turns either into a ``FAILED`` counterfactual naming the reason.
    """
    config = _config()
    api_key = config.require_api_key()
    return ModelRouter(
        config=config,
        client=GeminiClient(
            config=config,
            generate_content=gemini_transport(api_key=api_key.get_secret_value()),
        ),
    )


def default_counterfactual_service(
    source: ConnectionSource, *, read: ReadPort, clock: Callable[[], datetime]
) -> CounterfactualService:
    return CounterfactualService(
        store=SqlCounterfactualStore(source, read=read),
        router_factory=live_router,
        clock=clock,
    )


def default_probe_service(*, clock: Callable[[], datetime]) -> ModelProbeService:
    return ModelProbeService(
        config_factory=_config,
        client_factory=_client,
        clock=clock,
    )


def _client(config: GeminiRouterConfig) -> Any:
    api_key = config.require_api_key()
    return GeminiClient(
        config=config,
        generate_content=gemini_transport(api_key=api_key.get_secret_value()),
    )
