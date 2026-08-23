"""T4.1 - the frozen configuration is the spec's table, and nothing else.

`specs/12_KERNEL_ALGORITHMS.md` section 0.5 prints `KernelConfig` field for
field. These tests are that table, transcribed once, so a threshold cannot
drift without a test diff. That is the whole reason the numbers live in code
rather than in prompt text.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from provenance_domain import authority as domain_authority
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    SUPPORTED_SCHEMA_VERSIONS,
    KernelConfig,
)

pytestmark = pytest.mark.unit


#: (field name, expected value) for every threshold section 0.5 prints.
SECTION_0_5 = [
    ("material_overlap_min_seconds", 86_400),
    ("payment_match_window_days", 3),
    ("instant_widen_days", 1),
    ("amount_abs_tolerance", Decimal("0.01")),
    ("amount_rel_tolerance", Decimal("0.005")),
    ("entailment_penalty", Decimal("0.30")),
    ("auto_resolve_margin", Decimal("0.25")),
    ("auto_resolve_floor", Decimal("0.80")),
    ("high_authority_floor", Decimal("0.80")),
    ("confirmed_status_floor", Decimal("0.90")),
    ("dispute_decay", Decimal("0.40")),
    ("action_confidence_floor", Decimal("0.60")),
    ("human_review_amount_threshold", Decimal("100.00")),
    ("critical_amount_threshold", Decimal("1000.00")),
    ("unknown_source_class_authority", Decimal("0.10")),
    ("overpay_tolerance", Decimal("0.00")),
    ("commitment_grace_seconds", 0),
    ("max_reopens", 5),
    ("max_tx_attempts", 5),
    ("retry_base_delay_ms", 50),
    ("retry_max_delay_ms", 2_000),
    ("future_validity_horizon_days", 3_650),
    ("supersession_authority_floor", Decimal("0.80")),
]


@pytest.mark.parametrize(("name", "expected"), SECTION_0_5, ids=[n for n, _ in SECTION_0_5])
def test_every_threshold_matches_the_spec_table(name: str, expected: object) -> None:
    """Section 0.5, one assertion per row."""
    assert getattr(DEFAULT_KERNEL_CONFIG, name) == expected


def test_the_wake_margin_is_sixty_seconds() -> None:
    """`CANONICAL_DECISIONS.md`: the deposit trigger wakes at
    `2026-06-15T00:01:00Z`, one minute after `due_at`. The scheduler has
    one-minute granularity and jitters both ways, so without the margin the
    common case burns a `WOKE_TOO_EARLY` no-op on every deadline."""
    assert DEFAULT_KERNEL_CONFIG.wake_margin_seconds == 60


def test_the_only_supported_schema_version_is_1_0() -> None:
    """Section 1.6 step 1. An agent runtime on a stale contract must be
    rejected with `SCHEMA_VERSION_UNSUPPORTED`, not accommodated."""
    assert frozenset({"1.0"}) == SUPPORTED_SCHEMA_VERSIONS
    assert DEFAULT_KERNEL_CONFIG.supported_schema_versions == frozenset({"1.0"})


def test_the_config_cannot_be_mutated() -> None:
    """Section 4.1's fixture contract: never mutated by a test. A suite that
    edits the shipped thresholds proves nothing about the shipped product."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_KERNEL_CONFIG.auto_resolve_margin = Decimal("0.99")  # type: ignore[misc]


def test_a_test_that_wants_a_different_threshold_builds_its_own() -> None:
    """The supported way to vary a threshold, and proof the default is intact
    afterwards."""
    loosened = KernelConfig(auto_resolve_margin=Decimal("0.05"))
    assert loosened.auto_resolve_margin == Decimal("0.05")
    assert DEFAULT_KERNEL_CONFIG.auto_resolve_margin == Decimal("0.25")


def test_no_threshold_is_a_float() -> None:
    """Money and authority are exact. A float threshold would make the
    comparison it gates inexact in the seventeenth decimal place, invisibly."""
    floats = [
        f.name
        for f in dataclasses.fields(DEFAULT_KERNEL_CONFIG)
        if isinstance(getattr(DEFAULT_KERNEL_CONFIG, f.name), float)
    ]
    assert floats == []


def test_the_monetary_review_threshold_is_the_one_the_hero_turns_on() -> None:
    """`CANONICAL_DECISIONS.md` -> *Hero conflict*: `monetary_exposure =
    186.00 >= 100.00` is what makes gate H5 fire, so this number is the demo."""
    assert DEFAULT_KERNEL_CONFIG.human_review_amount_threshold == Decimal("100.00")
    assert Decimal("186.00") >= DEFAULT_KERNEL_CONFIG.human_review_amount_threshold


def test_the_entailment_penalty_exceeds_the_auto_resolve_margin() -> None:
    """Section 2.3: "The penalty is the whole game." A direct statement must
    outrank an entailed one by *more* than the auto-resolution margin, or
    neither direction of the hero scenario resolves deterministically."""
    assert DEFAULT_KERNEL_CONFIG.entailment_penalty > DEFAULT_KERNEL_CONFIG.auto_resolve_margin


def test_the_config_agrees_with_the_domain_packages_constants() -> None:
    """Section 3.2's grid and section 0.5's thresholds are used together on
    every disposition. Two copies of 0.30 that can drift apart would make the
    authority arithmetic depend on which module happened to be imported."""
    assert DEFAULT_KERNEL_CONFIG.entailment_penalty == domain_authority.ENTAILMENT_PENALTY
    assert DEFAULT_KERNEL_CONFIG.auto_resolve_margin == domain_authority.AUTO_RESOLVE_MARGIN
    assert DEFAULT_KERNEL_CONFIG.auto_resolve_floor == domain_authority.AUTO_RESOLVE_FLOOR
    assert (
        DEFAULT_KERNEL_CONFIG.unknown_source_class_authority == domain_authority.UNKNOWN_AUTHORITY
    )
