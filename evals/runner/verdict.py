"""Verdicts and metrics -- the two types that keep `CANNOT RUN` out of `FAIL`.

Why this module exists at all
-----------------------------
``D-00-005``. A probe that could not connect reported that a capability had
*failed*, and acting on that verdict would have forced a working capability
into a permanent fallback. The same shape recurred twice more in one session:
``PostgresActionStore.grounding_snapshot`` returned ``frozenset()`` -- a real
answer meaning "supports nothing" -- where it meant "never loaded", and an
unbuilt capability answered ``500 INTERNAL_ERROR`` where nothing had gone wrong
and nothing had been attempted.

So there are two rules here, and they are types rather than conventions:

1. **A metric that could not be computed is** ``None`` **with a reason.** Never
   ``0.0``. ``recall@20 = 0.0000`` reads as "retrieval is broken"; ``CANNOT RUN
   -- no Titan credential`` reads as "we did not measure it". Those are
   opposite claims and they lead to opposite decisions.
2. **`CANNOT RUN` is not `FAIL`.** It does not decrement a pass count, it does
   not set a non-zero exit code, and it is tallied in its own column.

Every :class:`Metric` and every :class:`Check` also carries the **command that
produced it**, as a required constructor argument rather than a documentation
habit. A number a reader cannot re-run is a marketing claim.

One asymmetry is deliberate. A suite whose every check is ``CANNOT RUN``
reports ``CANNOT RUN``, not ``PASS`` -- a green suite that ran nothing is the
vacuity failure ``quality/23_PHASE_GATES.md`` section 23 exists to prevent, and
it is the more dangerous of the two errors because it produces a green log.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = [
    "UNMEASURED_RENDERING",
    "Check",
    "Metric",
    "RunReport",
    "SuiteResult",
    "Verdict",
]

#: What an unmeasured metric renders as. Never a number, and never blank: a
#: blank cell in a column of numbers reads as "nothing to report here", which
#: is the same wrong inference in quieter clothing.
UNMEASURED_RENDERING = "CANNOT RUN"


class Verdict(enum.Enum):
    """Three outcomes, not two.

    ``CANNOT RUN`` means the question is still open. It must not be recorded as
    a failure and it must not force a fallback.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    CANNOT_RUN = "CANNOT RUN"

    def __str__(self) -> str:
        return self.value


def _require(value: str, field: str, owner: str) -> None:
    if not value or not value.strip():
        raise ValueError(
            f"{owner} was constructed without a {field}. "
            f"Every number this harness prints must name the command that "
            f"produced it and every absence must name what it waits on; a "
            f"report a reader cannot re-run or act on is a marketing document."
        )


@dataclass(frozen=True)
class Metric:
    """A number, or an honest absence of one.

    Construct through :meth:`measured` or :meth:`unmeasured`. The two paths are
    separate so that "the value is zero" cannot be spelled the same way as
    "there is no value".
    """

    name: str
    value: float | None
    reason: str | None
    command: str
    unit: str = ""

    def __post_init__(self) -> None:
        _require(self.command, "command", "Metric")
        if self.value is None:
            _require(self.reason or "", "reason", "Metric with a None value")
        elif self.reason is not None:
            raise ValueError(
                f"metric {self.name!r} carries both a value and an unmeasured "
                f"reason. One of the two is false, and the reader cannot tell "
                f"which."
            )

    @classmethod
    def measured(cls, name: str, value: float, command: str, unit: str = "") -> Metric:
        """A real number. *value* may legitimately be ``0.0``."""
        if value is None:  # pragma: no cover - defensive against an untyped caller
            raise ValueError(f"metric {name!r} was measured as None; use unmeasured()")
        return cls(name=name, value=float(value), reason=None, command=command, unit=unit)

    @classmethod
    def unmeasured(cls, name: str, reason: str, command: str) -> Metric:
        """No number, and the reason there is none."""
        return cls(name=name, value=None, reason=reason, command=command)

    @property
    def is_measured(self) -> bool:
        return self.value is not None

    def render(self) -> str:
        """The metric as one line, with its reason when it has no value."""
        if self.value is None:
            return f"{self.name} = {UNMEASURED_RENDERING} -- {self.reason}"
        suffix = f" {self.unit}" if self.unit else ""
        return f"{self.name} = {self.value:.4f}{suffix}"


@dataclass(frozen=True)
class Check:
    """One assertion, one verdict, and the command that produced it."""

    check_id: str
    verdict: Verdict
    detail: str
    command: str

    def __post_init__(self) -> None:
        _require(self.command, "command", "Check")
        if self.verdict is not Verdict.PASS:
            _require(self.detail, "detail", f"Check {self.check_id} with verdict {self.verdict}")

    def render(self) -> str:
        return f"   {self.verdict!s:<11} {self.check_id:<10} {self.detail}"


@dataclass(frozen=True)
class SuiteResult:
    """One capability suite: its checks, its metrics, and what it left out."""

    suite_id: str
    title: str
    claim: str
    checks: tuple[Check, ...] = ()
    metrics: tuple[Metric, ...] = ()
    exclusions: tuple[str, ...] = ()
    #: Findings a reader needs and an aggregate hides. A mean of 0.77 and a
    #: named list of the four queries that scored 0 are different pieces of
    #: information, and only the second one can be acted on.
    findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.claim, "claim", f"SuiteResult {self.suite_id}")

    def _count(self, verdict: Verdict) -> int:
        return sum(1 for check in self.checks if check.verdict is verdict)

    @property
    def passed(self) -> int:
        return self._count(Verdict.PASS)

    @property
    def failed(self) -> int:
        return self._count(Verdict.FAIL)

    @property
    def cannot_run(self) -> int:
        return self._count(Verdict.CANNOT_RUN)

    @property
    def verdict(self) -> Verdict:
        if self.failed:
            return Verdict.FAIL
        if self.passed:
            return Verdict.PASS
        return Verdict.CANNOT_RUN


@dataclass(frozen=True)
class RunReport:
    """Every suite from one invocation, plus the exit code it implies."""

    suites: tuple[SuiteResult, ...]
    started_at: str
    git_sha: str
    database: str
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> int:
        return sum(suite.passed for suite in self.suites)

    @property
    def failed(self) -> int:
        return sum(suite.failed for suite in self.suites)

    @property
    def cannot_run(self) -> int:
        return sum(suite.cannot_run for suite in self.suites)

    @property
    def exit_code(self) -> int:
        """Non-zero for a failure and for nothing else.

        A ``CANNOT RUN`` exiting non-zero would be read by a gate as a failed
        capability, which is the decision ``D-00-005`` exists to prevent.
        """
        return 1 if self.failed else 0
