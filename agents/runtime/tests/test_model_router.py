"""The Gemini model router — routing, budgets, attribution (``T7.1``, ``T7.6``).

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 7 (structured output and the single
  repair attempt), section 8 (deterministic routing), section 9 (model-specific
  parameters and refusals).
- ``docs/CANONICAL_DECISIONS.md`` -> *Models and prompts* and *Bedrock model id
  canon*. The canon's lesson is the one this file is built around: a model id
  read from documentation is a hypothesis, so the id is configuration, it is
  validated against an allow-set at startup, and the id **actually used** is
  recorded on every call.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.1 and T7.6.

The assertion that matters most
-------------------------------
``test_a_malformed_response_costs_two_calls_and_never_three``. Schema failure
and invocation failure carry *different* budgets, and a router that conflates
them spends four calls where the spec allows two. Every fake in ``conftest.py``
raises on an unscripted call for that reason.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest
from google.genai import errors, types

from agents.runtime.model_router import (
    ALLOWED_MODEL_IDS,
    DEFAULT_EXTRACTION_MODEL_ID,
    DEFAULT_REASONING_FALLBACK_MODEL_ID,
    DEFAULT_REASONING_MODEL_ID,
    FALLBACK_EFFORT,
    MAX_MODEL_CALLS_PER_NODE,
    MAX_SCHEMA_REPAIR_ATTEMPTS,
    MAX_TIER_E_FALLBACK_ATTEMPTS,
    PENDING_REVIEW_REASONS,
    PROBE_EVIDENCE_PATH,
    REPAIR_PROMPT_VERSION,
    ROUTES,
    GeminiClient,
    GeminiRouterConfig,
    ModelConfigError,
    ModelInvocationError,
    ModelRefusalError,
    ModelRouter,
    PendingReview,
    RouterSuccess,
    UnknownNodeError,
    route,
)
from agents.runtime.tests.conftest import (
    TOO_LONG_JSON,
    UNPARSEABLE_JSON,
    VALID_JSON,
    WRONG_SHAPE_JSON,
    ScriptedClient,
    ScriptedGenerate,
    SteppingClock,
    gemini_response,
    iter_text_parts,
    ok,
    truncated,
)
from provenance_domain.enums import ModelTier, ProposalStatus

pytestmark = pytest.mark.unit

EXTRACT = "extract_structured_evidence"
RESOLVE = "strong_resolution"
ATTENTION = "classify_attention_need"
DRAFT = "draft_action"

SYSTEM = "# SYSTEM POLICY — provenance.extract_structured_evidence"
USER = "### UNTRUSTED EVIDENCE\nInvoice 88431"


def build(
    config: GeminiRouterConfig,
    clock: SteppingClock,
    *outcomes: Any,
    max_transport_retries: int = 0,
) -> tuple[ModelRouter, ScriptedClient]:
    client = ScriptedClient(outcomes=list(outcomes))
    router = ModelRouter(
        config=config,
        client=client,
        clock=clock,
        max_transport_retries=max_transport_retries,
        sleep=lambda _seconds: None,
    )
    return router, client


# ===========================================================================
# 1. Configuration is the only source of a model id, and it is validated
# ===========================================================================


def test_the_allow_set_is_exactly_the_three_documented_flash_class_ids() -> None:
    """No Pro model satisfies the "3.5 or newer" floor, so both tiers are Flash.

    ``gemini-3.1-pro-preview`` is version 3.1 and is below the floor; naming it
    here as a *rejected* id is what stops it being reintroduced by someone who
    reasons "Pro is the strong one".
    """
    assert (
        frozenset({"gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"})
        == ALLOWED_MODEL_IDS
    )
    assert "gemini-3.1-pro-preview" not in ALLOWED_MODEL_IDS


def test_the_documented_defaults_are_the_tier_assignment_from_the_brief() -> None:
    assert DEFAULT_REASONING_MODEL_ID == "gemini-3.7-flash"
    assert DEFAULT_EXTRACTION_MODEL_ID == "gemini-3.5-flash-lite"
    assert DEFAULT_REASONING_FALLBACK_MODEL_ID == "gemini-3.6-flash"


def test_config_reads_all_four_environment_variables(env: dict[str, str]) -> None:
    config = GeminiRouterConfig.from_env(env)
    assert config.reasoning_model_id == "gemini-3.7-flash"
    assert config.extraction_model_id == "gemini-3.5-flash-lite"
    assert config.reasoning_fallback_model_id == "gemini-3.6-flash"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "not-a-real-key"


def test_an_empty_environment_yields_the_documented_defaults() -> None:
    config = GeminiRouterConfig.from_env({})
    assert config.reasoning_model_id == DEFAULT_REASONING_MODEL_ID
    assert config.extraction_model_id == DEFAULT_EXTRACTION_MODEL_ID
    assert config.api_key is None


@pytest.mark.parametrize(
    ("variable", "stale"),
    [
        ("GEMINI_REASONING_MODEL_ID", "gemini-3.1-pro-preview"),
        ("GEMINI_REASONING_MODEL_ID", "us.anthropic.claude-opus-4-6-v1"),
        ("GEMINI_EXTRACTION_MODEL_ID", "gemini-2.0-flash"),
        ("GEMINI_EXTRACTION_MODEL_ID", "gemini-3.5-flash-lite-preview-09-2025"),
        ("GEMINI_REASONING_FALLBACK_MODEL_ID", "gemini-flash-latest"),
    ],
)
def test_a_stale_model_id_is_a_startup_failure(
    env: dict[str, str], variable: str, stale: str
) -> None:
    """The Bedrock lesson, enforced: an unrecognised id fails at startup.

    Not at 3 a.m. on the first artifact that happens to route through it.
    """
    env[variable] = stale
    with pytest.raises(ModelConfigError) as caught:
        GeminiRouterConfig.from_env(env)
    message = str(caught.value)
    assert variable in message
    assert stale in message
    assert PROBE_EVIDENCE_PATH in message


def test_the_api_key_never_renders(env: dict[str, str]) -> None:
    config = GeminiRouterConfig.from_env(env)
    assert "not-a-real-key" not in repr(config)
    assert "not-a-real-key" not in str(config)


def test_a_missing_api_key_is_refused_at_the_point_of_use_not_at_startup() -> None:
    """Phase-shaped, like ``Settings.require_role_dsns``.

    There is no key yet, so the router must still be constructible, printable
    and testable. The refusal lands where the key is actually needed.
    """
    config = GeminiRouterConfig.from_env({})
    with pytest.raises(ModelConfigError) as caught:
        config.require_api_key()
    assert "GOOGLE_API_KEY" in str(caught.value)


def test_the_ids_carry_a_probe_required_marker_in_the_source() -> None:
    """Documented-but-unprobed is a state the source must admit to.

    ``ops/gemini-probe.txt`` exists but records ``CANNOT RUN`` -- no key, no
    invocation. Until it carries a PASS line, every id in this package is a
    hypothesis, and the comment says so at the constant.
    """
    from pathlib import Path

    import agents.runtime.model_router.models as models_module

    source = Path(models_module.__file__).read_text(encoding="utf-8")
    for default in (
        DEFAULT_REASONING_MODEL_ID,
        DEFAULT_EXTRACTION_MODEL_ID,
        DEFAULT_REASONING_FALLBACK_MODEL_ID,
    ):
        line = next(row for row in source.splitlines() if f'"{default}"' in row)
        index = source.splitlines().index(line)
        window = "\n".join(source.splitlines()[max(0, index - 6) : index + 1])
        assert "# PROBE REQUIRED" in window, f"{default} has no PROBE REQUIRED marker"


# ===========================================================================
# 2. Deterministic routing — by task, never by preference
# ===========================================================================


def test_extraction_and_classification_route_to_tier_e() -> None:
    assert route(EXTRACT).tier is ModelTier.E


def test_the_four_reasoning_tasks_route_to_tier_r() -> None:
    """Semantic resolution, contradiction characterisation, attention, drafting.

    Contradiction characterisation is part of ``strong_resolution``'s output
    contract rather than a fifth node, which is why three names carry four
    responsibilities.
    """
    assert route(RESOLVE).tier is ModelTier.R
    assert route(ATTENTION).tier is ModelTier.R
    assert route(DRAFT).tier is ModelTier.R


def test_the_route_table_is_exactly_the_four_model_nodes() -> None:
    assert set(ROUTES) == {EXTRACT, RESOLVE, ATTENTION, DRAFT}


def test_an_unknown_node_name_is_refused() -> None:
    with pytest.raises(UnknownNodeError):
        route("summarise_everything")


def test_no_call_site_hardcodes_a_model_id(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Swapping the environment swaps the id on the wire. One line, no code."""
    swapped = GeminiRouterConfig.from_env({"GEMINI_EXTRACTION_MODEL_ID": "gemini-3.6-flash"})
    router, client = build(swapped, clock, ok())
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert client.model_ids == ["gemini-3.6-flash"]
    assert config.extraction_model_id == "gemini-3.5-flash-lite"


def test_tier_e_carries_no_thinking_effort_and_tier_r_does() -> None:
    assert route(EXTRACT).effort is None
    assert route(RESOLVE).effort == "HIGH"
    assert route(ATTENTION).effort == "MEDIUM"
    assert route(DRAFT).effort == "HIGH"


# ===========================================================================
# 3. Attribution — every call is recorded against the id that served it
# ===========================================================================


def test_a_successful_call_records_model_id_prompt_version_and_token_counts(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ok())
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)

    assert isinstance(result, RouterSuccess)
    assert result.value.amount_decimal == Decimal("186.00")
    assert result.repaired is False
    assert len(result.calls) == 1

    record = result.calls[0]
    assert record.model_id == "gemini-3.5-flash-lite"
    assert record.prompt_version == route(EXTRACT).prompt_version
    assert record.input_tokens == 1200
    assert record.output_tokens == 340
    assert record.thought_tokens == 90
    assert record.duration_ms == 250


def test_the_call_record_serialises_to_the_agent_runs_model_calls_element(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """``specs/10_DATABASE_DDL.md`` prints the element shape; this is it."""
    router, _ = build(config, clock, ok())
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    element = result.calls[0].as_agent_runs_element()
    assert set(element) == {
        "seq",
        "node",
        "model_id",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "repair_attempts",
        "duration_ms",
        "started_at",
    }
    assert element["seq"] == 1
    assert element["node"] == EXTRACT
    assert element["repair_attempts"] == 0
    assert json.dumps(element)


def test_the_recorded_id_is_the_one_that_served_the_call_not_the_configured_default(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """The whole point of ``agent_runs.model_route``: attributable to reality."""
    router, _ = build(
        config,
        clock,
        ModelInvocationError("503 UNAVAILABLE", retryable=True),
        ok(),
    )
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert [record.model_id for record in result.calls] == [
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
    ]
    assert result.model_id == "gemini-3.7-flash"


# ===========================================================================
# 4. The repair budget — schema failure gets exactly one attempt
# ===========================================================================


def test_a_malformed_response_costs_two_calls_and_never_three(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """``T7.6`` acceptance, verbatim: ``model_calls=2`` then ``PENDING_REVIEW``."""
    router, client = build(config, clock, ok(TOO_LONG_JSON), ok(TOO_LONG_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)

    assert isinstance(result, PendingReview)
    assert len(client.requests) == 2
    assert len(result.calls) == 2
    assert result.reason_code == "SCHEMA_REPAIR_EXHAUSTED"
    assert result.status == "PENDING_REVIEW"


def test_a_repair_that_succeeds_is_flagged_as_repaired(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ok(TOO_LONG_JSON), ok(VALID_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert result.repaired is True
    assert len(result.calls) == 2
    assert result.calls[1].repair_attempts == 1


def test_the_repair_call_records_the_repair_prompt_version_suffix(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ok(TOO_LONG_JSON), ok(VALID_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert result.calls[1].prompt_version == (
        f"{route(EXTRACT).prompt_version}+{REPAIR_PROMPT_VERSION}"
    )


def test_the_repair_reuses_byte_identical_system_text_and_the_original_user_text(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Section 7.2: byte-identical system preserves the cache prefix."""
    router, client = build(config, clock, ok(TOO_LONG_JSON), ok(VALID_JSON))
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    first, second = client.requests
    assert second.system_instruction == first.system_instruction == SYSTEM
    assert second.user_text == first.user_text == USER


def test_the_prior_output_is_a_middle_assistant_turn_not_a_trailing_prefill(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Section 7.2's closing note. A trailing assistant turn is a 400."""
    router, client = build(config, clock, ok(TOO_LONG_JSON), ok(VALID_JSON))
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    repair = client.requests[1]
    assert repair.prior_assistant == TOO_LONG_JSON
    assert repair.extra_user is not None
    assert "REPAIR INSTRUCTION" in repair.extra_user
    assert REPAIR_PROMPT_VERSION in repair.extra_user


def test_the_repair_instruction_carries_the_failures_as_json(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(config, clock, ok(TOO_LONG_JSON), ok(VALID_JSON))
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    body = client.requests[1].extra_user
    payload = json.loads(body[body.index("[") :])
    assert payload == [
        {
            "path": "artifact_summary",
            "code": "SUMMARY_TOO_LONG",
            "detail": "artifact_summary is at most 2 sentences",
        }
    ]


@pytest.mark.parametrize("text", [UNPARSEABLE_JSON, WRONG_SHAPE_JSON])
def test_a_decode_or_schema_failure_is_repairable_once(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any, text: str
) -> None:
    router, client = build(config, clock, ok(text), ok(VALID_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert len(client.requests) == 2


def test_a_truncated_response_is_a_schema_failure_not_an_invocation_failure(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Section 9.3: thinking and text share ``max_tokens``; truncation is JSON."""
    router, client = build(config, clock, truncated(), ok(VALID_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert client.model_ids == ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]


def test_tier_r_gets_a_repair_but_never_a_weaker_model(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(config, clock, ok(TOO_LONG_JSON), ok(TOO_LONG_JSON))
    result = router.invoke(RESOLVE, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert client.model_ids == ["gemini-3.7-flash", "gemini-3.7-flash"]
    assert result.reason_code == "SCHEMA_REPAIR_EXHAUSTED"


# ===========================================================================
# 5. The fallback budget — invocation failure is a different failure
# ===========================================================================


def test_tier_e_invocation_failure_falls_back_once_to_tier_r_at_low_effort(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(config, clock, ModelInvocationError("429 RESOURCE_EXHAUSTED"), ok())
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert client.model_ids == ["gemini-3.5-flash-lite", "gemini-3.7-flash"]
    assert client.requests[0].effort is None
    assert client.requests[1].effort == FALLBACK_EFFORT
    assert FALLBACK_EFFORT == "LOW"


def test_tier_e_fallback_failure_becomes_pending_review(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(
        config,
        clock,
        ModelInvocationError("429 RESOURCE_EXHAUSTED"),
        ModelInvocationError("503 UNAVAILABLE"),
    )
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.reason_code == "MODEL_INVOCATION_FAILED"
    assert len(client.requests) == 2


def test_tier_r_invocation_failure_never_downgrades(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Canon: "Tier R fallback — no downgrade to a weaker model."""
    router, client = build(config, clock, ModelInvocationError("503 UNAVAILABLE"))
    result = router.invoke(RESOLVE, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.reason_code == "MODEL_INVOCATION_FAILED"
    assert len(client.requests) == 1


def test_the_two_budgets_are_separate_and_still_cannot_reach_four_calls(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Invocation failure, then a fallback whose output is malformed.

    A router with one shared counter spends four calls here. A router with two
    counters and no cap spends three. The spec allows two.
    """
    router, client = build(
        config,
        clock,
        ModelInvocationError("429 RESOURCE_EXHAUSTED"),
        ok(TOO_LONG_JSON),
    )
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert len(client.requests) == MAX_MODEL_CALLS_PER_NODE == 2
    assert result.reason_code == "SCHEMA_REPAIR_EXHAUSTED"


def test_the_budget_constants_are_the_ones_the_spec_prints() -> None:
    assert MAX_SCHEMA_REPAIR_ATTEMPTS == 1
    assert MAX_TIER_E_FALLBACK_ATTEMPTS == 1
    assert MAX_MODEL_CALLS_PER_NODE == 2


def test_the_budget_lives_in_the_router_not_in_the_node(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """A node cannot opt out: ``invoke`` takes no budget parameter at all."""
    import inspect

    signature = inspect.signature(ModelRouter.invoke)
    assert set(signature.parameters) == {"self", "node_name", "system", "user_text", "contract"}


def test_the_declared_reasoning_fallback_id_is_never_selected_automatically(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """``gemini-3.6-flash`` is a capacity lever, not a correctness one.

    It is configured and startup-validated so that a throttling incident is a
    one-line ``GEMINI_REASONING_MODEL_ID`` change. It is deliberately not an
    automatic downgrade: canon forbids one on Tier R, and on Tier E the spec
    names the *reasoning* model as the fallback target. This test is what stops
    it quietly becoming one.
    """
    router, client = build(
        config,
        clock,
        ModelInvocationError("429 RESOURCE_EXHAUSTED"),
        ok(),
    )
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert config.reasoning_fallback_model_id == "gemini-3.6-flash"
    assert "gemini-3.6-flash" not in client.model_ids


# ===========================================================================
# 6. Refusals and non-repairable failures
# ===========================================================================


def test_a_refusal_is_never_repaired_and_never_retried(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(config, clock, ModelRefusalError("SAFETY", category="SAFETY"))
    result = router.invoke(DRAFT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.reason_code == "MODEL_REFUSAL"
    assert len(client.requests) == 1


def test_a_tier_e_refusal_does_not_take_the_fallback_either(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """A refusal is a decision, not an outage. Retrying it on a bigger model is
    shopping for a different answer to the same question."""
    router, client = build(config, clock, ModelRefusalError("SAFETY"))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.reason_code == "MODEL_REFUSAL"
    assert len(client.requests) == 1


def test_a_leaked_nonce_is_never_repaired(config: GeminiRouterConfig, clock: SteppingClock) -> None:
    """Section 2.2: ``NONCE_LEAKED_IN_OUTPUT`` skips the repair attempt."""
    from agents.runtime.model_router import OutputContract, ValidationFailure
    from agents.runtime.tests.conftest import ToyOutput

    def leaks(_value: ToyOutput) -> list[ValidationFailure]:
        return [
            ValidationFailure(path="$", code="NONCE_LEAKED_IN_OUTPUT", detail="fence nonce echoed")
        ]

    router, client = build(config, clock, ok())
    result = router.invoke(
        EXTRACT,
        system=SYSTEM,
        user_text=USER,
        contract=OutputContract(model=ToyOutput, validate=leaks),
    )
    assert isinstance(result, PendingReview)
    assert result.reason_code == "NONCE_LEAKED_IN_OUTPUT"
    assert len(client.requests) == 1


# ===========================================================================
# 7. PENDING_REVIEW is a state, not an exception
# ===========================================================================


def test_pending_review_is_returned_not_raised(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ModelInvocationError("503"))
    result = router.invoke(RESOLVE, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)


def test_pending_review_maps_to_the_canonical_proposal_status(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ModelInvocationError("503"))
    result = router.invoke(RESOLVE, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.proposal_status is ProposalStatus.PENDING_HUMAN_REVIEW


def test_every_pending_review_reason_is_from_the_closed_set(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, _ = build(config, clock, ok(TOO_LONG_JSON), ok(TOO_LONG_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, PendingReview)
    assert result.reason_code in PENDING_REVIEW_REASONS
    assert (
        frozenset(
            {
                "SCHEMA_REPAIR_EXHAUSTED",
                "MODEL_REFUSAL",
                "MODEL_INVOCATION_FAILED",
                "NONCE_LEAKED_IN_OUTPUT",
                "MODEL_CALL_BUDGET_EXHAUSTED",
            }
        )
        == PENDING_REVIEW_REASONS
    )


def test_a_failed_run_still_records_every_call_it_made(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """The pending path writes an ``agent_runs`` row rather than silently
    retrying, so the records have to survive the failure."""
    router, _ = build(config, clock, ok(TOO_LONG_JSON), ok(TOO_LONG_JSON))
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert [record.seq for record in result.calls] == [1, 2]
    assert all(record.model_id in ALLOWED_MODEL_IDS for record in result.calls)


# ===========================================================================
# 8. Transport retry is a third budget and does not spend the other two
# ===========================================================================


def test_transport_retry_is_off_by_default(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(config, clock, ModelInvocationError("503", retryable=True), ok())
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert client.model_ids == ["gemini-3.5-flash-lite", "gemini-3.7-flash"]


def test_transport_retry_does_not_consume_the_repair_budget(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    """Section 7.2: throttling is retried separately and costs no repair."""
    router, client = build(
        config,
        clock,
        ModelInvocationError("429", retryable=True),
        ok(TOO_LONG_JSON),
        ok(VALID_JSON),
        max_transport_retries=1,
    )
    result = router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert isinstance(result, RouterSuccess)
    assert len(client.requests) == 3
    assert result.logical_attempts == 2
    assert client.model_ids == ["gemini-3.5-flash-lite"] * 3


def test_a_non_retryable_error_is_not_retried_at_transport_level(
    config: GeminiRouterConfig, clock: SteppingClock, contract: Any
) -> None:
    router, client = build(
        config,
        clock,
        ModelInvocationError("400 INVALID_ARGUMENT", retryable=False),
        ok(),
        max_transport_retries=1,
    )
    router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    assert client.model_ids == ["gemini-3.5-flash-lite", "gemini-3.7-flash"]


# ===========================================================================
# 9. Nothing raw ever reaches a log line
# ===========================================================================


SECRET_USER_TEXT = "TENANT SECRET account 8841724417 balance 186.00"


@pytest.mark.parametrize(
    ("script", "expected_reason"),
    [
        pytest.param(
            (ok(TOO_LONG_JSON), ok(TOO_LONG_JSON)),
            "SCHEMA_REPAIR_EXHAUSTED",
            id="schema-failure",
        ),
        pytest.param(
            (ModelInvocationError("503 UNAVAILABLE"), ModelInvocationError("503")),
            "MODEL_INVOCATION_FAILED",
            id="invocation-failure",
        ),
        pytest.param(
            (ModelRefusalError("SAFETY", category="SAFETY"),),
            "MODEL_REFUSAL",
            id="refusal",
        ),
        pytest.param((ok(),), None, id="success"),
    ],
)
def test_the_router_never_logs_artifact_content_or_model_output(
    config: GeminiRouterConfig,
    clock: SteppingClock,
    contract: Any,
    caplog: pytest.LogCaptureFixture,
    script: tuple[Any, ...],
    expected_reason: str | None,
) -> None:
    """Every path, not just the happy one.

    A hygiene assertion that only covers the schema path leaves the invocation
    and refusal handlers free to log the request body, which is where a real
    leak would sit.
    """
    router, _ = build(config, clock, *script)
    with caplog.at_level(logging.DEBUG):
        router.invoke(EXTRACT, system=SYSTEM, user_text=SECRET_USER_TEXT, contract=contract)
    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert emitted != ""
    assert "8841724417" not in emitted
    assert "TENANT SECRET" not in emitted
    assert TOO_LONG_JSON not in emitted
    assert VALID_JSON not in emitted
    if expected_reason is not None:
        assert expected_reason in emitted


def test_the_router_never_logs_the_api_key(
    config: GeminiRouterConfig,
    clock: SteppingClock,
    contract: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _ = build(config, clock, ok())
    with caplog.at_level(logging.DEBUG):
        router.invoke(EXTRACT, system=SYSTEM, user_text=USER, contract=contract)
    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert "not-a-real-key" not in emitted


# ===========================================================================
# 10. The Gemini adapter — the real SDK surface, no network
# ===========================================================================


def build_client(config: GeminiRouterConfig, *outcomes: Any) -> tuple[Any, ScriptedGenerate]:
    generate = ScriptedGenerate(outcomes=list(outcomes))
    return GeminiClient(config=config, generate_content=generate), generate


def request_for(node: str, **overrides: Any) -> Any:
    from agents.runtime.model_router import ModelRequest
    from agents.runtime.tests.conftest import ToyOutput

    spec = route(node)
    defaults: dict[str, Any] = {
        "model_id": "gemini-3.7-flash",
        "system_instruction": SYSTEM,
        "user_text": USER,
        "response_schema": ToyOutput,
        "max_output_tokens": spec.max_output_tokens,
        "effort": spec.effort,
        "thinking": spec.thinking,
    }
    defaults.update(overrides)
    return ModelRequest(**defaults)


def test_the_adapter_asks_for_json_and_hands_the_schema_to_the_sdk(
    config: GeminiRouterConfig,
) -> None:
    from agents.runtime.tests.conftest import ToyOutput

    client, generate = build_client(config, gemini_response())
    client.generate(request_for(RESOLVE))
    sent = generate.calls[0]
    assert sent.model == "gemini-3.7-flash"
    assert sent.config.response_mime_type == "application/json"
    assert sent.config.response_schema is ToyOutput
    assert sent.config.response_json_schema is None


def test_the_adapter_never_sets_temperature_top_p_or_top_k(
    config: GeminiRouterConfig,
) -> None:
    """Section 9.2: Provenance steers with prompt text, never with sampling."""
    client, generate = build_client(config, gemini_response())
    client.generate(request_for(RESOLVE))
    sent = generate.calls[0].config
    assert sent.temperature is None
    assert sent.top_p is None
    assert sent.top_k is None
    assert sent.seed is None


def test_the_system_policy_travels_as_system_instruction_not_as_user_text(
    config: GeminiRouterConfig,
) -> None:
    """Section 2.1: the boundary is structural. Concatenating the two would make
    the fence typographic, which is the failure the whole section exists to
    prevent."""
    client, generate = build_client(config, gemini_response())
    client.generate(request_for(RESOLVE))
    sent = generate.calls[0]
    assert sent.config.system_instruction == SYSTEM
    parts = list(iter_text_parts(sent.contents))
    assert parts == [("user", USER)]


def test_the_repair_turn_is_user_model_user(config: GeminiRouterConfig) -> None:
    client, generate = build_client(config, gemini_response())
    client.generate(request_for(RESOLVE, prior_assistant=TOO_LONG_JSON, extra_user="# REPAIR"))
    roles = [role for role, _ in iter_text_parts(generate.calls[0].contents)]
    assert roles == ["user", "model", "user"]


def test_tier_e_sends_no_thinking_config_and_tier_r_sends_a_level(
    config: GeminiRouterConfig,
) -> None:
    client, generate = build_client(config, gemini_response(), gemini_response())
    client.generate(request_for(EXTRACT, model_id="gemini-3.5-flash-lite"))
    client.generate(request_for(RESOLVE))
    assert generate.calls[0].config.thinking_config is None
    thinking = generate.calls[1].config.thinking_config
    assert thinking is not None
    assert thinking.thinking_level == types.ThinkingLevel.HIGH
    assert thinking.include_thoughts is False


def test_the_adapter_reports_the_token_counts_the_sdk_returned(
    config: GeminiRouterConfig,
) -> None:
    client, _ = build_client(
        config,
        gemini_response(prompt_tokens=2050, output_tokens=411, thought_tokens=1200),
    )
    response = client.generate(request_for(RESOLVE))
    assert response.input_tokens == 2050
    assert response.output_tokens == 411
    assert response.thought_tokens == 1200
    assert response.model_version == "gemini-3.7-flash"


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_retryable_api_error_becomes_a_retryable_invocation_error(
    config: GeminiRouterConfig, code: int
) -> None:
    error_class = errors.ClientError if code < 500 else errors.ServerError
    client, _ = build_client(
        config, error_class(code, {"error": {"message": "busy", "status": "X", "code": code}})
    )
    with pytest.raises(ModelInvocationError) as caught:
        client.generate(request_for(RESOLVE))
    assert caught.value.retryable is True


def test_a_client_error_that_is_not_throttling_is_not_retryable(
    config: GeminiRouterConfig,
) -> None:
    client, _ = build_client(
        config,
        errors.ClientError(
            400, {"error": {"message": "bad", "status": "INVALID_ARGUMENT", "code": 400}}
        ),
    )
    with pytest.raises(ModelInvocationError) as caught:
        client.generate(request_for(RESOLVE))
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "finish",
    [
        types.FinishReason.SAFETY,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.SPII,
        types.FinishReason.RECITATION,
    ],
)
def test_a_blocking_finish_reason_becomes_a_refusal(
    config: GeminiRouterConfig, finish: types.FinishReason
) -> None:
    client, _ = build_client(config, gemini_response(text=None, finish_reason=finish))
    with pytest.raises(ModelRefusalError) as caught:
        client.generate(request_for(DRAFT))
    assert caught.value.category == finish.value


def test_the_finish_reason_is_read_before_the_content(
    config: GeminiRouterConfig,
) -> None:
    """Section 9.7: on a pre-output refusal ``content`` is empty and indexing it
    raises. The refusal must surface as a refusal, not as an IndexError."""
    client, _ = build_client(
        config, gemini_response(text=None, finish_reason=types.FinishReason.SAFETY)
    )
    with pytest.raises(ModelRefusalError):
        client.generate(request_for(DRAFT))


def test_max_tokens_is_reported_as_truncation_not_as_refusal(
    config: GeminiRouterConfig,
) -> None:
    client, _ = build_client(
        config,
        gemini_response(
            text='{"artifact_summary": "unf', finish_reason=types.FinishReason.MAX_TOKENS
        ),
    )
    response = client.generate(request_for(RESOLVE))
    assert response.truncated is True


def test_an_empty_body_on_a_clean_stop_is_an_invocation_failure(
    config: GeminiRouterConfig,
) -> None:
    client, _ = build_client(config, gemini_response(text=None))
    with pytest.raises(ModelInvocationError):
        client.generate(request_for(RESOLVE))


def test_the_adapter_refuses_to_build_a_live_client_without_a_key() -> None:
    """No key exists yet. The failure is a named configuration error, not an
    opaque 401 from the first real call."""
    keyless = GeminiRouterConfig.from_env({})
    client = GeminiClient(config=keyless)
    with pytest.raises(ModelConfigError):
        client.generate(request_for(RESOLVE))


# ===========================================================================
# 11. The smoke tool
# ===========================================================================


def test_smoke_prints_a_line_per_tier_naming_the_configured_id(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    from agents.runtime.tools.smoke import main

    exit_code = main(["--tier", "E", "--tier", "R", "--print-model-id"], env=env)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "tier=E model=gemini-3.5-flash-lite ok" in out
    assert "tier=R model=gemini-3.7-flash ok" in out


def test_smoke_never_names_a_superseded_identifier(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """``T7.1`` acceptance, carried across the pivot: any output naming a stale
    identifier is a FAILURE."""
    from agents.runtime.tools.smoke import main

    main(["--tier", "E", "--tier", "R"], env=env)
    out = capsys.readouterr().out
    for stale in ("anthropic", "claude", "Sonnet", "Gemma", "GLM", "Kimi", "pro-preview"):
        assert stale not in out


def test_smoke_reports_the_probe_state_the_transcript_actually_records(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Smoke's claim must track the transcript, in whichever direction it points.

    This asserted ``"PROBE REQUIRED" in out`` until 2026-08-24, when the probe
    ran and every canon id came back PASS. An assertion pinned to one of two
    legitimate states fails the moment the other becomes true, and the pressure
    then is to delete it — losing the guard exactly when there is finally
    something to guard.

    So it asserts the *correspondence*: whatever the shipped transcript says,
    the tool says the same thing. Both branches are reachable and both are
    checked, which is what makes it a guard rather than a snapshot.
    """
    from pathlib import Path

    from agents.runtime.tools.smoke import main, probe_verdict, read_probe_transcript

    main(["--tier", "E"], env=env)
    out = capsys.readouterr().out
    assert PROBE_EVIDENCE_PATH in out, "the tool must cite where its evidence lives"

    root = Path(__file__).resolve().parents[3]
    transcript = read_probe_transcript(root)
    tier_e = env["GEMINI_EXTRACTION_MODEL_ID"]
    if probe_verdict(tier_e, transcript) == "PASS":
        assert "probed" in out, f"{tier_e} has a PASS line but smoke did not say so"
        assert (
            "PROBE REQUIRED" not in out
        ), f"{tier_e} has a PASS line and smoke still demands a probe"
    else:
        assert (
            "PROBE REQUIRED" in out
        ), f"{tier_e} has no PASS line and smoke did not demand a probe"


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("", "UNPROBED"),
        ("CANNOT RUN  GOOGLE_API_KEY is not set.", "UNPROBED"),
        ("PASS  gemini-3.7-flash  generate_content 200", "PASS"),
        ("  PASS  gemini-3.7-flash  ok", "PASS"),
        ("FAIL  gemini-3.7-flash  404 NOT_FOUND", "FAIL"),
        ("PASS  gemini-3.5-flash-lite  ok", "UNPROBED"),
    ],
)
def test_a_transcript_is_read_for_a_verdict_not_for_its_existence(
    transcript: str, expected: str
) -> None:
    from agents.runtime.tools.smoke import probe_verdict

    assert probe_verdict("gemini-3.7-flash", transcript) == expected


def test_every_canon_id_has_a_verdict_in_the_shipped_transcript() -> None:
    """No canon id may sit at UNPROBED once the transcript claims a run.

    The original intent — "guards against a future edit that adds a PASS line
    without a real run" — is kept below by
    ``test_a_pass_must_come_from_the_invocation_section``. What changed is that
    UNPROBED stopped being the correct answer on 2026-08-24, so asserting it
    would now block the probe result rather than protect it.
    """
    from pathlib import Path

    from agents.runtime.tools.smoke import probe_verdict, read_probe_transcript

    root = Path(__file__).resolve().parents[3]
    transcript = read_probe_transcript(root)
    unprobed = [m for m in sorted(ALLOWED_MODEL_IDS) if probe_verdict(m, transcript) == "UNPROBED"]
    assert not unprobed, (
        f"{unprobed} have no verdict line in ops/gemini-probe.txt. "
        "Re-run `python ops/probes/gemini_probe.py`."
    )


def test_a_pass_must_come_from_the_invocation_section_not_the_listing() -> None:
    """The original guard, kept and sharpened.

    PB-G1 prints every id the account can enumerate, one per line, under a
    heading that says ``REFERENCE ONLY, NOT PROOF``. Those lines name the canon
    ids too. If a verdict could be read off them, the transcript would confirm
    every id the moment listing succeeded — which is precisely the Bedrock trap
    that cost this project a full re-probe.
    """
    from agents.runtime.tools.smoke import probe_verdict

    listing_only = "\n".join(
        [
            "-- PB-G1  model listing (REFERENCE ONLY, NOT PROOF)",
            "   models/gemini-3.7-flash",
            "   models/gemini-3.5-flash-lite",
            "   models/gemini-3.6-flash",
        ]
    )
    for model_id in sorted(ALLOWED_MODEL_IDS):
        assert (
            probe_verdict(model_id, listing_only) == "UNPROBED"
        ), f"{model_id} was confirmed by a LISTING line; enumeration is not invocation"


def test_a_longer_id_containing_a_shorter_one_does_not_decide_its_verdict() -> None:
    """The substring defect, pinned.

    ``gemini-3.5-flash`` is a prefix of ``gemini-3.5-flash-lite`` and the probe
    invokes both, so both lines sit in one transcript. Matching the id as a
    bare substring made whichever line came first decide the verdict for the
    other — reporting **PASS for an id the transcript records as FAILING**.
    """
    from agents.runtime.tools.smoke import probe_verdict

    transcript = "\n".join(
        [
            "  PASS        PB-G2  invoke gemini-3.5-flash-lite       reply='ok'",
            "  FAIL        PB-G2  invoke gemini-3.5-flash             404 NOT_FOUND",
        ]
    )
    assert probe_verdict("gemini-3.5-flash", transcript) == "FAIL"
    assert probe_verdict("gemini-3.5-flash-lite", transcript) == "PASS"


def test_smoke_exits_nonzero_on_a_stale_model_id(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    from agents.runtime.tools.smoke import main

    env["GEMINI_REASONING_MODEL_ID"] = "gemini-3.1-pro-preview"
    exit_code = main(["--tier", "R"], env=env)
    assert exit_code == 1
    assert "gemini-3.1-pro-preview" in capsys.readouterr().err


def test_smoke_makes_no_model_call(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    """There is no key and no probe. The tool prints configuration and stops;
    the socket guard on this ``unit``-marked test is the proof."""
    from agents.runtime.tools.smoke import main

    assert main(["--tier", "E", "--tier", "R"], env={}) == 0
    assert "GOOGLE_API_KEY is not set" in capsys.readouterr().out
