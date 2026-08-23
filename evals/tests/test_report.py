"""A report a reader cannot re-run is a marketing document.

Three properties are asserted here and they are the three the brief names:
every number prints the command that produced it; every cap and exclusion is
printed; and `CANNOT RUN` never renders as a number, in text or in JSON.
"""

from __future__ import annotations

import json

import pytest

from evals.runner.report import render_json, render_text
from evals.runner.verdict import Check, Metric, RunReport, SuiteResult, Verdict

pytestmark = pytest.mark.unit

COMMAND = "python -m evals.runner --suite retrieval"


def _report() -> RunReport:
    suite = SuiteResult(
        suite_id="RET",
        title="retrieval under adversarial decoys",
        claim="00_PRODUCT.md section 2.2: whichever ranks higher wins the answer.",
        checks=(
            Check(check_id="RET-01", verdict=Verdict.PASS, detail="31 queries", command=COMMAND),
            Check(
                check_id="RET-02",
                verdict=Verdict.CANNOT_RUN,
                detail="no Titan credential on this machine",
                command=COMMAND,
            ),
        ),
        metrics=(
            Metric.measured("recall@20", 0.7715, COMMAND),
            Metric.unmeasured(
                "natural_language_recall@20",
                "no Titan credential on this machine",
                COMMAND,
            ),
        ),
        exclusions=("ranks deeper than 100 are not read",),
        findings=("northline-final-invoice.eml: 0 of 3 case-mates within the top 100",),
    )
    return RunReport(
        suites=(suite,),
        started_at="2026-08-24T10:57:10+00:00",
        git_sha="a26f278",
        database="provenance",
        notes=("18035 evidence rows counted at query time",),
    )


def test_the_text_report_prints_the_command_for_every_suite_that_reports_a_metric() -> None:
    text = render_text(_report())
    assert COMMAND in text, (
        "the report printed metrics without the command that produced them. A "
        "number a reader cannot re-run is a claim, not a measurement."
    )


def test_the_text_report_prints_every_cap_and_exclusion() -> None:
    text = render_text(_report())
    assert "ranks deeper than 100 are not read" in text, (
        "an exclusion was dropped from the report. Silent truncation reads as " "full coverage."
    )
    assert "caps and exclusions" in text


def test_the_text_report_names_the_queries_the_aggregate_hides() -> None:
    text = render_text(_report())
    assert "northline-final-invoice.eml: 0 of 3 case-mates" in text


def test_an_unmeasured_metric_never_renders_as_a_number_in_text() -> None:
    text = render_text(_report())
    assert "natural_language_recall@20 = CANNOT RUN" in text
    assert "natural_language_recall@20 = 0.0000" not in text


def test_the_report_restates_the_verdict_semantics_beside_the_totals() -> None:
    text = render_text(_report())
    assert "PASS 1  |  FAIL 0  |  CANNOT RUN 1" in text
    tail = text.split("SUMMARY", 1)[1]
    assert "must not be recorded as a failure" in tail, (
        "the summary quotes three counts without saying what the third one "
        "means. The summary line is the part that gets quoted."
    )


def test_the_json_report_carries_null_for_an_unmeasured_metric_and_never_zero() -> None:
    payload = json.loads(render_json(_report()))
    metrics = {metric["name"]: metric for metric in payload["suites"][0]["metrics"]}
    unmeasured = metrics["natural_language_recall@20"]
    assert unmeasured["value"] is None, (
        "the JSON report encoded an unmeasured metric as a number. A consumer "
        "reading it makes the same mistake in a different language."
    )
    assert unmeasured["cannot_run_reason"]
    assert metrics["recall@20"]["value"] == pytest.approx(0.7715)
    assert metrics["recall@20"]["cannot_run_reason"] is None


def test_the_json_report_carries_the_command_and_the_exclusions() -> None:
    payload = json.loads(render_json(_report()))
    suite = payload["suites"][0]
    assert all(metric["command"] for metric in suite["metrics"])
    assert all(check["command"] for check in suite["checks"])
    assert suite["exclusions"] == ["ranks deeper than 100 are not read"]
    assert payload["totals"] == {"pass": 1, "fail": 0, "cannot_run": 1, "exit_code": 0}
