"""Every threshold the Memory Kernel decides with, in one frozen object.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 0.5 prints this class field for
  field. The values here are that table, not a paraphrase of it.
- ``CANONICAL_DECISIONS.md`` -> *Hero conflict*: the monetary-exposure
  threshold is ``100.00`` and gate ``H5`` short-circuits on it.
- ``CANONICAL_DECISIONS.md`` -> *Hero dataset canon* and
  ``specs/16_TRIGGER_DSL.md`` section 12: ``WAKE_MARGIN_SECONDS`` is 60.
- ``EXECUTION/70_TASK_PLAN.md`` T4.1: "frozen v1 defaults object ... never
  mutated by a test".

Why the thresholds live here and never in prompt text
-----------------------------------------------------
Changing a threshold has to be a code change with a test diff. A number that
lives in a prompt is a number a model can be argued out of, and one that no
test can pin. ``specs/12_KERNEL_ALGORITHMS.md`` section 0.5 states the rule
directly; this module is where it is true.

Stdlib only, plus ``provenance_domain``. No ``provenance_db``, no ``boto3``,
no ``httpx``, no ``asyncio``: the whole point of this package is that its
decisions are reachable from a unit test with no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

__all__ = [
    "DEFAULT_KERNEL_CONFIG",
    "SUPPORTED_SCHEMA_VERSIONS",
    "KernelConfig",
]

#: The only ``MemoryProposal.schema_version`` values this Kernel accepts.
#: ``specs/12_KERNEL_ALGORITHMS.md`` section 1.6 step 1: ``schema_version`` in
#: ``{"1.0"}``.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"1.0"})


@dataclass(frozen=True, slots=True)
class KernelConfig:
    """The v1 frozen configuration.

    Frozen twice over: ``frozen=True`` stops an accidental write, and
    :data:`DEFAULT_KERNEL_CONFIG` is a module-level singleton so a test that
    wants a different threshold constructs its own instance rather than
    mutating the shared one. ``quality/20_TDD_STRATEGY.md`` section 4.1 makes
    that a fixture contract; a suite that mutates the config proves nothing
    about the configuration the product ships with.
    """

    # --- section 2, contradiction detection ---
    material_overlap_min_seconds: int = 86_400  # 24h
    payment_match_window_days: int = 3
    instant_widen_days: int = 1
    amount_abs_tolerance: Decimal = Decimal("0.01")
    amount_rel_tolerance: Decimal = Decimal("0.005")  # 0.5%

    # --- section 3, disposition ---
    entailment_penalty: Decimal = Decimal("0.30")
    auto_resolve_margin: Decimal = Decimal("0.25")
    auto_resolve_floor: Decimal = Decimal("0.80")
    high_authority_floor: Decimal = Decimal("0.80")
    confirmed_status_floor: Decimal = Decimal("0.90")
    dispute_decay: Decimal = Decimal("0.40")
    action_confidence_floor: Decimal = Decimal("0.60")
    human_review_amount_threshold: Decimal = Decimal("100.00")
    critical_amount_threshold: Decimal = Decimal("1000.00")
    unknown_source_class_authority: Decimal = Decimal("0.10")

    # --- section 4, money ---
    overpay_tolerance: Decimal = Decimal("0.00")
    commitment_grace_seconds: int = 0

    # --- section 5, case machine ---
    max_reopens: int = 5

    # --- section 7, retry ---
    max_tx_attempts: int = 5
    retry_base_delay_ms: int = 50
    retry_max_delay_ms: int = 2_000

    # --- section 8, temporal ---
    future_validity_horizon_days: int = 3_650  # 10 years
    supersession_authority_floor: Decimal = Decimal("0.80")

    # --- prospective memory ---
    #: ``specs/16_TRIGGER_DSL.md`` section 12: ``fire_at = not_before +
    #: WAKE_MARGIN_SECONDS``. The scheduler has one-minute granularity and
    #: jitters in both directions, so without the margin the common case wastes
    #: a ``WOKE_TOO_EARLY`` no-op on every deadline.
    wake_margin_seconds: int = 60

    #: Not in section 0.5's printed dataclass; added because pipeline step 1
    #: needs the accepted set and hard-coding it inside the validator would put
    #: a threshold outside this object.
    supported_schema_versions: frozenset[str] = SUPPORTED_SCHEMA_VERSIONS


#: The shipped configuration. Import this; do not construct ``KernelConfig()``
#: at call sites, so that "which config decided this?" has one answer.
DEFAULT_KERNEL_CONFIG: Final[KernelConfig] = KernelConfig()
