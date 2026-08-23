"""``PvObservabilityStack``: the alarm catalogue and the presenter's red light.

``G13.7`` asserts that every ``provenance-`` alarm is in ``OK`` rather than
``INSUFFICIENT_DATA`` and names four of them explicitly. Only a deployed stack
with real traffic can satisfy the *state* half of that; what a synthesised
template can prove is that the alarms exist under exactly those names, with the
documented thresholds, and with ``treatMissingData: notBreaching`` -- which is
the setting that makes a quiet system report ``OK`` instead of failing the gate
while behaving perfectly.
"""

from __future__ import annotations

from typing import Any

import pytest
from provenance_infra import alarms as catalogue
from provenance_infra import config as cfg
from pv_cdk_testing import flatten, resources_of


def _alarms(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        body["Properties"]["AlarmName"]: body["Properties"]
        for body in resources_of(template, "AWS::CloudWatch::Alarm").values()
    }


def test_every_catalogued_alarm_is_created(
    template_json: dict[str, dict[str, Any]],
) -> None:
    created = _alarms(template_json["PvObservabilityStack"])
    assert set(created) == {spec.name for spec in catalogue.ALARMS}
    assert len(created) == 24


@pytest.mark.parametrize("required", catalogue.G13_7_REQUIRED)
def test_the_four_alarms_g13_7_names_exist(
    template_json: dict[str, dict[str, Any]], required: str
) -> None:
    assert required in _alarms(template_json["PvObservabilityStack"])


@pytest.mark.parametrize("spec", catalogue.ALARMS, ids=lambda s: s.name)
def test_alarm_threshold_and_comparison_match_the_catalogue(
    template_json: dict[str, dict[str, Any]], spec: catalogue.AlarmSpec
) -> None:
    """The thresholds are engineering judgement at demo scale, not SLOs derived
    from measurement -- which is exactly why they are asserted.

    A gate that checks "alarms exist" would pass on a threshold somebody moved to
    make a page stop.
    """
    props = _alarms(template_json["PvObservabilityStack"])[spec.name]
    assert props["Threshold"] == spec.threshold
    assert props["ComparisonOperator"] == spec.comparison
    assert props["EvaluationPeriods"] == spec.evaluation_periods


@pytest.mark.parametrize("spec", catalogue.ALARMS, ids=lambda s: s.name)
def test_no_alarm_pages_on_missing_data(
    template_json: dict[str, dict[str, Any]], spec: catalogue.AlarmSpec
) -> None:
    props = _alarms(template_json["PvObservabilityStack"])[spec.name]
    assert props["TreatMissingData"] == "notBreaching"


@pytest.mark.parametrize("spec", catalogue.ALARMS, ids=lambda s: s.name)
def test_every_alarm_notifies_on_both_edges(
    template_json: dict[str, dict[str, Any]], spec: catalogue.AlarmSpec
) -> None:
    """An alarm with no OK action tells you it broke and never tells you it
    recovered.
    """
    props = _alarms(template_json["PvObservabilityStack"])[spec.name]
    assert props["AlarmActions"]
    assert props["OKActions"]


@pytest.mark.parametrize("spec", catalogue.ALARMS, ids=lambda s: s.name)
def test_every_alarm_description_carries_its_severity_and_runbook(
    template_json: dict[str, dict[str, Any]], spec: catalogue.AlarmSpec
) -> None:
    """The reasoning travels with the alarm, so the first person to see a false
    page can evaluate it rather than guess at intent.
    """
    description = _alarms(template_json["PvObservabilityStack"])[spec.name]["AlarmDescription"]
    assert description.startswith(f"{spec.severity} - ")
    assert "Runbook:" in description
    # Hyphens, never em dashes, in an AWS description.
    assert "—" not in description


def test_the_six_correctness_alarms_have_a_zero_threshold(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """These are not tuned; they are invariants.

    A non-zero value means something the architecture says cannot happen has
    happened, so the threshold is zero rather than a rate.
    """
    zero_threshold_p1 = {
        spec.name for spec in catalogue.ALARMS if spec.severity == "P1" and spec.threshold == 0
    }
    assert {
        "provenance-retrieval-retracted-leakage",
        "provenance-retrieval-cross-user-rows",
        "provenance-state-proof-ungrounded-belief",
        "provenance-auth-tenant-mismatch",
        "provenance-proposal-foreign-provenance",
        "provenance-model-tier-r-fallback",
    } <= zero_threshold_p1


def test_the_dlq_alarm_covers_every_dlq_that_exists(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """21_OBSERVABILITY_ANALYTICS.md dimensions this on ``provenance-events-dlq``,
    which no stack creates.

    Pointing an alarm at a non-existent queue leaves it in ``INSUFFICIENT_DATA``
    forever -- failing ``G13.7`` while looking correct -- so it is metric math
    over the five DLQs section 6.2 actually defines. (D-13-005.)
    """
    props = _alarms(template_json["PvObservabilityStack"])["provenance-dlq-depth"]
    rendered = flatten(props["Metrics"])
    for queue_name in cfg.DLQ_NAMES:
        assert queue_name in rendered, queue_name
    assert "provenance-events-dlq" not in rendered


def test_the_prompt_cache_alarm_watches_the_tier_r_model_actually_in_force(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """The source document dimensions this on ``anthropic.claude-opus-5``, which
    is denied on this account and is not invocable in any form. (D-13-007.)
    """
    props = _alarms(template_json["PvObservabilityStack"])["provenance-prompt-cache-cold"]
    rendered = flatten(props)
    assert cfg.BEDROCK_REASONING_MODEL_ID in rendered
    assert "claude-opus-5" not in rendered


def test_metric_math_alarms_carry_their_expression(
    template_json: dict[str, dict[str, Any]],
) -> None:
    math_specs = [spec for spec in catalogue.ALARMS if spec.math is not None]
    assert len(math_specs) >= 6
    for spec in math_specs:
        assert spec.math is not None
        props = _alarms(template_json["PvObservabilityStack"])[spec.name]
        assert "Metrics" in props
        assert spec.math.expression in flatten(props["Metrics"])


def test_the_composite_gives_the_presenter_one_red_light(
    template_json: dict[str, dict[str, Any]],
) -> None:
    composites = resources_of(
        template_json["PvObservabilityStack"], "AWS::CloudWatch::CompositeAlarm"
    )
    assert len(composites) == 1
    props = next(iter(composites.values()))["Properties"]
    assert props["AlarmName"] == catalogue.COMPOSITE_NAME
    rule = flatten(props["AlarmRule"])
    assert rule.count("ALARM") == len(catalogue.COMPOSITE_MEMBERS)
    assert " OR " in rule


def test_the_ops_dashboard_exists_under_its_canonical_name(
    template_json: dict[str, dict[str, Any]],
) -> None:
    dashboards = resources_of(template_json["PvObservabilityStack"], "AWS::CloudWatch::Dashboard")
    assert len(dashboards) == 1
    props = next(iter(dashboards.values()))["Properties"]
    assert props["DashboardName"] == cfg.DASHBOARD_NAME


def test_every_alarm_appears_on_the_dashboard(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Derived from the catalogue rather than hand-written, so a new alarm cannot
    exist without appearing on the dashboard the gate reviewer reads.
    """
    dashboards = resources_of(template_json["PvObservabilityStack"], "AWS::CloudWatch::Dashboard")
    body = flatten(next(iter(dashboards.values()))["Properties"]["DashboardBody"])
    for spec in catalogue.ALARMS:
        assert spec.name in body, spec.name


def test_the_alarm_catalogue_rejects_a_spec_that_is_neither_metric_nor_math() -> None:
    """The dataclass invariant, exercised.

    A spec with both a metric and a math expression would silently drop one, and
    a spec with neither would synthesise an alarm on nothing.
    """
    with pytest.raises(ValueError, match="exactly one"):
        catalogue.AlarmSpec(
            name="x",
            severity="P3",
            threshold=1,
            comparison=catalogue.GREATER,
            evaluation_periods=1,
            reason="r",
            runbook="r",
        )
