"""Emit the run as text a reader can act on and as JSON a machine can diff.

Three rules this module enforces rather than describes
------------------------------------------------------
1. **Every number prints the command that produced it.** The command is a
   required field on :class:`~evals.runner.verdict.Metric` and
   :class:`~evals.runner.verdict.Check`, so a metric that reached the report
   without one could not have been constructed.
2. **Every cap and exclusion is printed.** Silent truncation reads as full
   coverage; a suite that sampled and did not say so is reporting a different
   number from the one it took.
3. **`CANNOT RUN` never renders as a number.** It renders as the words, and
   with the reason attached, because "CANNOT RUN" alone cannot be acted on.

The footer restates the verdict semantics on every run. That is not
decoration: the summary line is the part that gets quoted, and a reader who
sees ``PASS 12 | FAIL 0 | CANNOT RUN 6`` without the semantics beside it will
read the third column as a failure.
"""

from __future__ import annotations

import json
from typing import Any

from evals.runner.verdict import RunReport, SuiteResult, Verdict

__all__ = ["render_json", "render_text"]

RULE = "=" * 78
THIN = "-" * 78


def render_text(report: RunReport) -> str:
    lines: list[str] = [
        RULE,
        "Provenance -- evaluation harness",
        f"started             : {report.started_at}",
        f"git sha             : {report.git_sha}",
        f"database            : {report.database} (session READ ONLY, "
        "role pv_app_reader_writer)",
        "verdict semantics   : PASS / FAIL / CANNOT RUN are THREE outcomes. "
        "Only FAIL changes canon (D-00-005).",
        "metric semantics    : a metric that could not be computed is CANNOT RUN "
        "with a reason, never 0.0000.",
        RULE,
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    if report.notes:
        lines.append("")

    for suite in report.suites:
        lines.extend(_render_suite(suite))

    lines.extend(
        [
            RULE,
            "SUMMARY",
            RULE,
            f"  PASS {report.passed}  |  FAIL {report.failed}  |  "
            f"CANNOT RUN {report.cannot_run}",
            "",
            "  A FAIL means the measured behaviour contradicts the product claim "
            "and must be corrected.",
            "  A CANNOT RUN means the question is still OPEN. It must not be "
            "recorded as a failure",
            "  and it must not force a fallback.",
            "",
            f"  exit code {report.exit_code}",
            RULE,
        ]
    )
    return "\n".join(lines)


def _render_suite(suite: SuiteResult) -> list[str]:
    lines = [
        THIN,
        f"SUITE {suite.suite_id}  {suite.title}   [{suite.verdict}]",
        THIN,
        f"  claim under test: {suite.claim}",
        "",
    ]
    for check in suite.checks:
        lines.append(check.render())
    lines.append("")
    if suite.metrics:
        lines.append("  metrics")
        for metric in suite.metrics:
            lines.append(f"    {metric.render()}")
        # One command line per suite rather than per metric: every metric in a
        # suite is produced by the same invocation, and repeating it 12 times
        # trains the reader to skip it.
        commands = sorted({metric.command for metric in suite.metrics})
        for command in commands:
            lines.append(f"    reproduce with: {command}")
        lines.append("")
    if suite.findings:
        lines.append("  findings the aggregate hides")
        for finding in suite.findings:
            lines.append(f"    - {finding}")
        lines.append("")
    if suite.exclusions:
        lines.append("  caps and exclusions")
        for exclusion in suite.exclusions:
            lines.append(f"    - {exclusion}")
        lines.append("")
    lines.append(
        f"  {suite.suite_id}: PASS {suite.passed}  FAIL {suite.failed}  "
        f"CANNOT RUN {suite.cannot_run}"
    )
    lines.append("")
    return lines


def render_json(report: RunReport) -> str:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "started_at": report.started_at,
        "git_sha": report.git_sha,
        "database": report.database,
        "notes": list(report.notes),
        "totals": {
            "pass": report.passed,
            "fail": report.failed,
            "cannot_run": report.cannot_run,
            "exit_code": report.exit_code,
        },
        "suites": [
            {
                "suite_id": suite.suite_id,
                "title": suite.title,
                "claim": suite.claim,
                "verdict": suite.verdict.value,
                "checks": [
                    {
                        "check_id": check.check_id,
                        "verdict": check.verdict.value,
                        "detail": check.detail,
                        "command": check.command,
                    }
                    for check in suite.checks
                ],
                "metrics": [
                    {
                        "name": metric.name,
                        # `null`, never 0. A JSON consumer that reads a missing
                        # measurement as a zero makes the same mistake in a
                        # different language.
                        "value": metric.value,
                        "unit": metric.unit or None,
                        "cannot_run_reason": metric.reason,
                        "command": metric.command,
                    }
                    for metric in suite.metrics
                ],
                "findings": list(suite.findings),
                "exclusions": list(suite.exclusions),
            }
            for suite in report.suites
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def suite_verdicts(report: RunReport) -> dict[str, Verdict]:
    """Suite id to verdict. Used by the CLI's one-line status."""
    return {suite.suite_id: suite.verdict for suite in report.suites}
