"""Gemini model routing for the agent runtime (``T7.1``, ``T7.6``).

Both tier ids are read from configuration — ``GEMINI_REASONING_MODEL_ID`` and
``GEMINI_EXTRACTION_MODEL_ID`` — and never hard-coded at a call site, so a
model swap is an environment change rather than a code change.
``agent_runs.model_calls`` records the id that actually served each call, so a
run is attributable to the model that produced it and not to the one that was
configured when it started.

Layout
------
``models.py``   configuration, the allow-set, the route row type, call records,
                and the two terminal outcomes.
``gemini.py``   the only module that imports ``google.genai``. One method,
                ``generate``; no retries, no repairs, no budgets.
``router.py``   the route table and all three budgets, in one place.

Provider
--------
Gemini Developer API (AI Studio key), ``google-genai`` 1.60.0. This package
replaces the Bedrock routing this module previously described; the *lesson*
from that build is carried across intact and is written up at the top of
``models.py``: a model id read from documentation is a hypothesis until it has
been invoked, so the allow-set is checked at startup and
``ops/gemini-probe.txt`` is the evidence that is still missing.
"""

from __future__ import annotations

from agents.runtime.model_router.gemini import (
    REFUSAL_FINISH_REASONS,
    GeminiClient,
    GenerateContentCallable,
    ModelClient,
    ModelRequest,
    ModelResponse,
)
from agents.runtime.model_router.models import (
    ALLOWED_MODEL_IDS,
    DEFAULT_EXTRACTION_MODEL_ID,
    DEFAULT_REASONING_FALLBACK_MODEL_ID,
    DEFAULT_REASONING_MODEL_ID,
    ENV_API_KEY,
    ENV_EXTRACTION_MODEL_ID,
    ENV_REASONING_FALLBACK_MODEL_ID,
    ENV_REASONING_MODEL_ID,
    NON_REPAIRABLE_CODES,
    PENDING_REVIEW_REASONS,
    PROBE_EVIDENCE_PATH,
    AllowedModelId,
    Effort,
    GeminiRouterConfig,
    ModelCallOutcome,
    ModelCallRecord,
    ModelConfigError,
    ModelInvocationError,
    ModelRefusalError,
    NodeSpec,
    OutputContract,
    OutputT,
    PendingReview,
    PendingReviewReason,
    RouterResult,
    RouterSuccess,
    UnknownNodeError,
    ValidationFailure,
)
from agents.runtime.model_router.router import (
    DEFAULT_MAX_TRANSPORT_RETRIES,
    FALLBACK_EFFORT,
    MAX_MODEL_CALLS_PER_NODE,
    MAX_SCHEMA_REPAIR_ATTEMPTS,
    MAX_TIER_E_FALLBACK_ATTEMPTS,
    REPAIR_INSTRUCTION,
    REPAIR_PROMPT_VERSION,
    ROUTES,
    ModelRouter,
    render_repair_instruction,
    route,
)

__all__ = [
    "ALLOWED_MODEL_IDS",
    "DEFAULT_EXTRACTION_MODEL_ID",
    "DEFAULT_MAX_TRANSPORT_RETRIES",
    "DEFAULT_REASONING_FALLBACK_MODEL_ID",
    "DEFAULT_REASONING_MODEL_ID",
    "ENV_API_KEY",
    "ENV_EXTRACTION_MODEL_ID",
    "ENV_REASONING_FALLBACK_MODEL_ID",
    "ENV_REASONING_MODEL_ID",
    "FALLBACK_EFFORT",
    "MAX_MODEL_CALLS_PER_NODE",
    "MAX_SCHEMA_REPAIR_ATTEMPTS",
    "MAX_TIER_E_FALLBACK_ATTEMPTS",
    "NON_REPAIRABLE_CODES",
    "PENDING_REVIEW_REASONS",
    "PROBE_EVIDENCE_PATH",
    "REFUSAL_FINISH_REASONS",
    "REPAIR_INSTRUCTION",
    "REPAIR_PROMPT_VERSION",
    "ROUTES",
    "AllowedModelId",
    "Effort",
    "GeminiClient",
    "GeminiRouterConfig",
    "GenerateContentCallable",
    "ModelCallOutcome",
    "ModelCallRecord",
    "ModelClient",
    "ModelConfigError",
    "ModelInvocationError",
    "ModelRefusalError",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "NodeSpec",
    "OutputContract",
    "OutputT",
    "PendingReview",
    "PendingReviewReason",
    "RouterResult",
    "RouterSuccess",
    "UnknownNodeError",
    "ValidationFailure",
    "render_repair_instruction",
    "route",
]
