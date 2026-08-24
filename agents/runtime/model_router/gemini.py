"""The Gemini Developer API adapter — the only module that knows ``google.genai``.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 2.1 (the boundary is structural),
  section 7.1 (API-level output constraint), section 7.2 (the repair turn is a
  *middle* assistant turn), section 9.2 (sampling parameters are never set),
  section 9.3 (thinking is explicit and its text stays omitted), section 9.7
  (a refusal is a status, not an exception, and must be read before content).
- ``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon* -> Router
  obligation: the configured id string is passed through **unmodified**. The
  previous provider needed a region-group prefix for one vendor and forbade it
  for every other; a client that synthesises identifiers cannot call both. The
  same refusal applies here even though Gemini ids are currently uniform.

Verified against the installed SDK, not from memory
---------------------------------------------------
``google-genai`` **1.60.0**. Every symbol used below was read out of the
installed package: ``Client(api_key=...)``, ``client.models.generate_content(
model=..., contents=..., config=...)``, ``GenerateContentConfig(
system_instruction, response_mime_type, response_schema, max_output_tokens,
thinking_config, http_options)``, ``ThinkingConfig(thinking_level,
include_thoughts)``, ``GenerateContentResponse(candidates, usage_metadata,
model_version)``, ``Candidate.finish_reason``,
``GenerateContentResponseUsageMetadata(prompt_token_count,
candidates_token_count, thoughts_token_count, cached_content_token_count)``,
and ``errors.APIError`` / ``ClientError`` / ``ServerError`` with ``.code`` and
``.status``.

**Still unprobed:** no live call has been made from this machine — there is no
API key. Whether ``gemini-3.7-flash`` and ``gemini-3.5-flash-lite`` are
*invocable* under these exact parameters is a hypothesis until
``ops/gemini-probe.txt`` exists. In particular ``response_schema`` behaviour on
a Flash-Lite model, and the exact ``finish_reason`` a safety block returns, are
documented rather than observed.

The seam
--------
``generate_content`` is injected. The production wiring is
``Client(api_key=...).models.generate_content``; the suite passes a script that
raises on an unbudgeted call. No test in this package constructs a real client,
so a passing run is proof that no request left the process.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from google.genai import errors, types
from pydantic import BaseModel

from agents.runtime.model_router.models import (
    Effort,
    GeminiRouterConfig,
    ModelInvocationError,
    ModelRefusalError,
)

__all__ = [
    "REFUSAL_FINISH_REASONS",
    "GeminiClient",
    "GenerateContentCallable",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
]

#: A 200 that carries a decision rather than content (section 9.7). Branch on
#: the finish reason; never on the absence of text, which is also what a
#: truncated call looks like.
REFUSAL_FINISH_REASONS: Final[frozenset[types.FinishReason]] = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.SPII,
        types.FinishReason.RECITATION,
        types.FinishReason.IMAGE_SAFETY,
        types.FinishReason.IMAGE_PROHIBITED_CONTENT,
        types.FinishReason.IMAGE_RECITATION,
    }
)

_EFFORT_TO_THINKING_LEVEL: Final[dict[Effort, types.ThinkingLevel]] = {
    "MINIMAL": types.ThinkingLevel.MINIMAL,
    "LOW": types.ThinkingLevel.LOW,
    "MEDIUM": types.ThinkingLevel.MEDIUM,
    "HIGH": types.ThinkingLevel.HIGH,
}

#: Quota exhaustion. A 429 is a capacity fact and is worth one bounded retry;
#: a 400 is a contract fact and retrying it is superstition.
_RETRYABLE_CLIENT_CODES: Final[frozenset[int]] = frozenset({408, 409, 429})

DEFAULT_TIMEOUT_MS: Final[int] = 120_000


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One invocation, in the router's vocabulary rather than the SDK's.

    ``prior_assistant`` plus ``extra_user`` is the repair shape from section
    7.2: the previous output is a **middle** assistant turn followed by the
    repair user turn. It is never a trailing assistant turn — prefill is
    refused by the structured-output path.
    """

    model_id: str
    system_instruction: str
    user_text: str
    response_schema: type[BaseModel]
    max_output_tokens: int
    effort: Effort | None
    thinking: bool
    prior_assistant: str | None = None
    extra_user: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """What came back, with the counts an ``agent_runs`` row needs.

    ``model_version`` is the id the *service* says served the call, kept
    alongside the id the router asked for. When the two disagree, the run is
    still attributable — which is the failure mode the previous provider's
    id canon was written to survive.
    """

    text: str
    model_version: str
    input_tokens: int
    output_tokens: int
    thought_tokens: int
    cached_input_tokens: int
    truncated: bool


class ModelClient(Protocol):
    """The seam the router talks to. One method, no provider vocabulary."""

    def generate(self, request: ModelRequest) -> ModelResponse: ...


class GenerateContentCallable(Protocol):
    """The shape of ``client.models.generate_content`` in google-genai 1.60.0."""

    def __call__(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse: ...


def build_contents(request: ModelRequest) -> list[types.Content]:
    """``user`` — or ``user, model, user`` on a repair.

    The SYSTEM POLICY never appears here: it travels as ``system_instruction``.
    Section 2.1's boundary is structural, and concatenating the two would make
    it typographic, which is the exact failure that section exists to prevent.
    """
    contents = [types.Content(role="user", parts=[types.Part(text=request.user_text)])]
    if request.prior_assistant is not None:
        contents.append(
            types.Content(role="model", parts=[types.Part(text=request.prior_assistant)])
        )
    if request.extra_user is not None:
        contents.append(types.Content(role="user", parts=[types.Part(text=request.extra_user)]))
    return contents


def build_config(
    request: ModelRequest, *, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> types.GenerateContentConfig:
    """The request configuration, and everything deliberately absent from it.

    ``temperature``, ``top_p``, ``top_k`` and ``seed`` are never set (section
    9.2). Determinism was never available from ``temperature=0`` on any model;
    reproducibility here comes from the result cache and from the fact that the
    deterministic Kernel, not the model, decides state.

    ``include_thoughts=False`` keeps reasoning text out of the response
    entirely. The Definition of Done forbids exposing raw chain-of-thought, and
    a field that is never populated cannot be logged by accident.
    """
    thinking_config: types.ThinkingConfig | None = None
    if request.thinking and request.effort is not None:
        thinking_config = types.ThinkingConfig(
            thinking_level=_EFFORT_TO_THINKING_LEVEL[request.effort],
            include_thoughts=False,
        )
    return types.GenerateContentConfig(
        system_instruction=request.system_instruction,
        max_output_tokens=request.max_output_tokens,
        response_mime_type="application/json",
        response_schema=request.response_schema,
        thinking_config=thinking_config,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


def _is_retryable(error: errors.APIError) -> bool:
    code = error.code or 0
    return code >= 500 or code in _RETRYABLE_CLIENT_CODES


def _usage(response: types.GenerateContentResponse) -> tuple[int, int, int, int]:
    usage = response.usage_metadata
    if usage is None:
        return (0, 0, 0, 0)
    return (
        usage.prompt_token_count or 0,
        usage.candidates_token_count or 0,
        usage.thoughts_token_count or 0,
        usage.cached_content_token_count or 0,
    )


def _finish_reason(response: types.GenerateContentResponse) -> types.FinishReason | None:
    candidates: Sequence[types.Candidate] = response.candidates or []
    if not candidates:
        return None
    return candidates[0].finish_reason


class GeminiClient:
    """``ModelClient`` over the Gemini Developer API (AI Studio key).

    The real client is built lazily and only when a call is actually made, so
    importing this module, constructing a router and running the whole test
    suite all work with no credential present — which is the situation today.
    """

    def __init__(
        self,
        *,
        config: GeminiRouterConfig,
        generate_content: GenerateContentCallable | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        self._config = config
        self._generate_content = generate_content
        self._timeout_ms = timeout_ms

    def _transport(self) -> GenerateContentCallable:
        if self._generate_content is None:
            # Raises ModelConfigError naming GOOGLE_API_KEY when no key exists,
            # rather than letting an opaque 401 come back from the first call.
            api_key = self._config.require_api_key()
            from google.genai import Client

            client = Client(api_key=api_key.get_secret_value())
            self._generate_content = client.models.generate_content
        return self._generate_content

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Invoke once. Never retries, never repairs — the router owns budgets.

        The model id is passed through byte-for-byte as configured. This client
        synthesises no prefix and normalises no string: an identifier the
        service does not recognise must fail as an identifier error, not be
        silently rewritten into a different model's id.
        """
        transport = self._transport()
        try:
            response = transport(
                model=request.model_id,
                contents=build_contents(request),
                config=build_config(request, timeout_ms=self._timeout_ms),
            )
        except errors.APIError as exc:
            raise ModelInvocationError(
                f"{exc.code} {exc.status} calling {request.model_id}",
                retryable=_is_retryable(exc),
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise ModelInvocationError(
                f"transport failure calling {request.model_id}: {type(exc).__name__}",
                retryable=True,
            ) from exc

        # Section 9.7: check the finish reason BEFORE touching content. On a
        # pre-output refusal there are no parts and indexing them raises.
        finish = _finish_reason(response)
        if finish in REFUSAL_FINISH_REASONS:
            category = finish.value if finish is not None else None
            raise ModelRefusalError(f"{request.model_id} declined the request", category=category)

        prompt_tokens, output_tokens, thought_tokens, cached_tokens = _usage(response)
        text = response.text or ""
        truncated = finish is types.FinishReason.MAX_TOKENS

        if not text and not truncated:
            raise ModelInvocationError(
                f"{request.model_id} returned no text with finish_reason="
                f"{finish.value if finish is not None else 'NONE'}",
                retryable=True,
            )

        return ModelResponse(
            text=text,
            model_version=response.model_version or request.model_id,
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
            thought_tokens=thought_tokens,
            cached_input_tokens=cached_tokens,
            truncated=truncated,
        )
