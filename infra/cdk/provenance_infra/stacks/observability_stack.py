"""Stack 10 -- ``PvObservabilityStack``.

The ``provenance-ops`` dashboard, the alarm catalogue from
``provenance_infra.alarms``, and the composite that gives the presenter one red
light (40_INFRA_IAC.md section 2.1 stack 10;
``quality/21_OBSERVABILITY_ANALYTICS.md`` sections 4 and 8).

Deploys last, so alarms are created against metrics that already exist and
therefore reach ``OK`` rather than sitting in ``INSUFFICIENT_DATA`` (``G13.7``).
Deploying last is necessary but not sufficient: an alarm only reaches ``OK``
once real traffic has published its metric at least once, and driving that
traffic before signing the gate is a step in the runbook, not a property of this
stack.

**Not built, and reported rather than invented.** Section 2.1 also lists "metric
filters" and "OTEL log-group subscriptions" for this stack. No document in the
pack defines which filters, which patterns, or which subscription destination,
and inventing them would put unreviewed metric definitions behind alarm
thresholds that other documents cite. (D-13-008.)
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from constructs import Construct

from provenance_infra import alarms as catalogue
from provenance_infra import config as cfg
from provenance_infra.alarms import AlarmSpec, MathSpec, MetricRef
from provenance_infra.config import PvConfig
from provenance_infra.props import FoundationExports

_COMPARISON = {
    catalogue.GREATER: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    catalogue.LESS: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
}


def _metric(ref: MetricRef) -> cloudwatch.Metric:
    return cloudwatch.Metric(
        namespace=ref.namespace,
        metric_name=ref.metric,
        statistic=ref.statistic,
        period=Duration.seconds(ref.period_seconds),
        dimensions_map=dict(ref.dimensions) or None,
    )


class PvObservabilityStack(Stack):
    """Dashboard, twenty-four alarms, and one composite."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PvConfig,
        foundation: FoundationExports,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.alert_action = cw_actions.SnsAction(foundation.alert_topic)

        self.alarms: dict[str, cloudwatch.Alarm] = {}
        for spec in catalogue.ALARMS:
            self.alarms[spec.name] = self._alarm(spec)

        self._composite()
        self._dashboard()

    # ------------------------------------------------------------------
    def _alarm(self, spec: AlarmSpec) -> cloudwatch.Alarm:
        metric: cloudwatch.IMetric
        if spec.math is not None:
            metric = self._math_metric(spec.name, spec.math)
        else:
            assert spec.metric is not None
            metric = _metric(spec.metric)

        alarm = cloudwatch.Alarm(
            self,
            self._logical_id(spec.name),
            alarm_name=spec.name,
            # Hyphen, never an em dash, in an alarm description.
            alarm_description=f"{spec.severity} - {spec.reason} Runbook: {spec.runbook}",
            metric=metric,
            threshold=spec.threshold,
            evaluation_periods=spec.evaluation_periods,
            comparison_operator=_COMPARISON[spec.comparison],
            # Every alarm, without exception. A quiet system must report OK.
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            actions_enabled=True,
        )
        alarm.add_alarm_action(self.alert_action)
        alarm.add_ok_action(self.alert_action)
        return alarm

    def _math_metric(self, alarm_name: str, math: MathSpec) -> cloudwatch.MathExpression:
        period_seconds = min(ref.period_seconds for ref in math.variables.values())
        return cloudwatch.MathExpression(
            expression=math.expression,
            using_metrics={name: _metric(ref) for name, ref in math.variables.items()},
            label=alarm_name,
            period=Duration.seconds(period_seconds),
        )

    def _composite(self) -> None:
        rule = cloudwatch.AlarmRule.any_of(
            *[self.alarms[name] for name in catalogue.COMPOSITE_MEMBERS]
        )
        composite = cloudwatch.CompositeAlarm(
            self,
            "DemoNotReady",
            composite_alarm_name=catalogue.COMPOSITE_NAME,
            alarm_rule=rule,
            alarm_description=(
                "P1 - one red light for the presenter. Everything in this rule "
                "breaks the hero flow visibly; nothing in it is a slow-burning "
                "quality signal."
            ),
        )
        composite.add_alarm_action(self.alert_action)
        composite.add_ok_action(self.alert_action)

    # ------------------------------------------------------------------
    def _dashboard(self) -> None:
        """``provenance-ops``: five sections matching the five quality families.

        The widget list is mechanically derived from the alarm catalogue rather
        than hand-written, so a new alarm cannot exist without appearing on the
        dashboard that the gate reviewer reads.
        """
        dashboard = cloudwatch.Dashboard(
            self,
            "OpsDashboard",
            dashboard_name=cfg.DASHBOARD_NAME,
            default_interval=Duration.hours(3),
            period_override=cloudwatch.PeriodOverride.AUTO,
        )

        sections: dict[str, tuple[str, ...]] = {
            "Memory and correctness": (
                "Provenance/Retrieval",
                "Provenance/StateProof",
                "Provenance/Auth",
            ),
            "Agent quality": ("Provenance/Agent", "Provenance/Model"),
            "Delivery and actions": ("Provenance/Outbox", "Provenance/Action", "AWS/SQS"),
            "Database": ("Provenance/Db",),
            "Cost and availability": ("Provenance/Cost", "Provenance/Api"),
        }

        for title, namespaces in sections.items():
            row: list[cloudwatch.IWidget] = [
                cloudwatch.AlarmWidget(
                    title=spec.name,
                    alarm=self.alarms[spec.name],
                    width=8,
                    height=6,
                )
                for spec in catalogue.ALARMS
                if self._section_of(spec) in namespaces
            ]
            if not row:
                continue
            # Each add_widgets call starts a new row, so the heading and its
            # widgets are two calls rather than a Row wrapper.
            dashboard.add_widgets(cloudwatch.TextWidget(markdown=f"## {title}", width=24, height=1))
            dashboard.add_widgets(*row)

    @staticmethod
    def _section_of(spec: AlarmSpec) -> str:
        if spec.metric is not None:
            return spec.metric.namespace
        assert spec.math is not None
        return next(iter(spec.math.variables.values())).namespace

    @staticmethod
    def _logical_id(alarm_name: str) -> str:
        return "".join(part.capitalize() for part in alarm_name.split("-") if part)
