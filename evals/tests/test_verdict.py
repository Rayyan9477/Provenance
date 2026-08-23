"""`CANNOT RUN` is not `FAIL`, and an absence is not a zero.

`D-00-005` is the founding rule of this repository and it has been violated
three separate times: a probe that could not connect reported a capability
FAILED; `grounding_snapshot` returned `frozenset()` for "never loaded"; an
unbuilt capability answered `500` where nothing had gone wrong. Every test here
is a guard on one of the shapes that produced those.
"""

from __future__ import annotations

import pytest

from evals.runner.verdict import Check, Metric, RunReport, SuiteResult, Verdict

pytestmark = pytest.mark.unit

CMD = "python -m evals.runner --suite retrieval"


def _suite(*checks: Check) -> SuiteResult:
    return SuiteResult(
        suite_id="RET",
        title="retrieval under adversarial decoys",
        claim="Contradiction is resolved by cosine similarity.",
        checks=checks,
    )


def _check(check_id: str, verdict: Verdict) -> Check:
    return Check(check_id=check_id, verdict=verdict, detail="detail", command=CMD)


def test_an_unmeasured_metric_holds_none_and_never_a_zero() -> None:
    metric = Metric.unmeasured(
        "recall@20",
        reason="no Titan credential on this machine; a fresh query vector would "
        "land in a different space from the corpus",
        command=CMD,
    )
    assert metric.value is None, (
        "an unmeasured metric carried a numeric value. 0.0 recall reads as "
        "'retrieval is broken'; CANNOT RUN reads as 'we did not measure it'. "
        "Those are opposite claims."
    )
    assert metric.is_measured is False
    assert metric.reason


def test_a_measured_zero_and_an_unmeasured_metric_render_differently() -> None:
    measured = Metric.measured("recall@20", 0.0, command=CMD)
    unmeasured = Metric.unmeasured("recall@20", reason="no Titan credential", command=CMD)
    assert measured.render() != unmeasured.render()
    assert "0.0000" in measured.render()
    assert "0.0000" not in unmeasured.render(), (
        "the unmeasured metric rendered a number. This is the exact substitution "
        "D-00-005 records: an absence presented as a real answer."
    )
    assert "CANNOT RUN" in unmeasured.render()
    assert "no Titan credential" in unmeasured.render(), (
        "an unmeasured metric rendered without its reason. 'CANNOT RUN' alone "
        "cannot be acted on; the reader has to know what it waits for."
    )


def test_a_metric_cannot_claim_to_be_unmeasured_without_a_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        Metric.unmeasured("recall@20", reason="   ", command=CMD)


def test_a_measured_metric_cannot_carry_a_none_value() -> None:
    with pytest.raises(ValueError, match="value"):
        Metric(name="recall@20", value=None, reason=None, command=CMD)


def test_every_metric_carries_the_command_that_produced_it() -> None:
    with pytest.raises(ValueError, match="command"):
        Metric.measured("recall@20", 0.5, command="")


def test_every_check_carries_the_command_that_produced_it() -> None:
    with pytest.raises(ValueError, match="command"):
        Check(check_id="RET-01", verdict=Verdict.PASS, detail="ok", command="")


def test_a_cannot_run_check_must_say_what_it_waits_on() -> None:
    with pytest.raises(ValueError, match="detail"):
        Check(check_id="RET-02", verdict=Verdict.CANNOT_RUN, detail="", command=CMD)


def test_cannot_run_is_tallied_apart_from_pass_and_fail() -> None:
    suite = _suite(
        _check("RET-01", Verdict.PASS),
        _check("RET-02", Verdict.CANNOT_RUN),
        _check("RET-03", Verdict.CANNOT_RUN),
    )
    assert (suite.passed, suite.failed, suite.cannot_run) == (1, 0, 2), (
        "CANNOT RUN was folded into the failure count. A gate reading this "
        "would force a fallback for a capability nobody measured."
    )


def test_a_suite_of_passes_and_cannot_runs_does_not_report_failed() -> None:
    suite = _suite(_check("RET-01", Verdict.PASS), _check("RET-02", Verdict.CANNOT_RUN))
    assert suite.verdict is Verdict.PASS


def test_a_suite_that_measured_nothing_reports_cannot_run_not_pass() -> None:
    suite = _suite(_check("MEM-01", Verdict.CANNOT_RUN))
    assert suite.verdict is Verdict.CANNOT_RUN, (
        "a suite whose every check was CANNOT RUN reported PASS. A green suite "
        "that ran nothing is the vacuity failure phase gates section 23 exists "
        "to prevent."
    )


def test_one_failure_makes_the_suite_fail_however_many_passes_precede_it() -> None:
    suite = _suite(
        _check("RET-01", Verdict.PASS),
        _check("RET-02", Verdict.PASS),
        _check("RET-03", Verdict.FAIL),
    )
    assert suite.verdict is Verdict.FAIL


def _report(*suites: SuiteResult) -> RunReport:
    return RunReport(
        suites=suites, started_at="2026-08-24T00:00:00Z", git_sha="unknown", database="provenance"
    )


def test_a_run_of_cannot_runs_exits_zero() -> None:
    report = _report(_suite(_check("MEM-01", Verdict.CANNOT_RUN)))
    assert report.exit_code == 0, (
        "a run that could not measure anything exited non-zero, which a gate "
        "reads as a failed capability. CANNOT RUN is not FAIL."
    )
    assert report.cannot_run == 1


def test_a_run_containing_one_failure_exits_non_zero() -> None:
    report = _report(_suite(_check("RET-01", Verdict.FAIL)))
    assert report.exit_code != 0
    assert report.failed == 1
