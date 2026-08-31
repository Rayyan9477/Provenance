"""The declared bars and the in-code fallback must agree.

``evals/runner/suites/retrieval.py`` reads ``evals/thresholds.yaml`` and falls
back to literals when PyYAML is absent, so the suite still runs on a bare
machine. Two places holding a threshold is exactly how a threshold gets quietly
lowered: someone edits the YAML, the machine running the battery has no PyYAML,
and the number that was actually asserted is the stale one in the code.

These tests fail on any disagreement, and on a RET-01 that stops computing its
verdict at all -- which is the state it was in before 2026-08-31, when the
verdict was the literal ``Verdict.PASS`` and the suite had no reachable FAIL.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from evals.runner.suites import retrieval

pytestmark = pytest.mark.unit

DECLARED = Path(__file__).resolve().parents[1] / "thresholds.yaml"


def test_the_declaration_file_exists() -> None:
    """`70_TASK_PLAN.md` line 1091 requires it and it did not exist."""
    assert DECLARED.is_file(), f"{DECLARED} is the authority for every asserted bar"


def test_the_fallback_literals_match_the_declared_bars() -> None:
    """The no-PyYAML path must assert the same numbers as the file."""
    yaml = pytest.importorskip("yaml", reason="the fallback is what is under test")
    declared = yaml.safe_load(DECLARED.read_text(encoding="utf-8"))["retrieval"]

    source = textwrap.dedent(inspect.getsource(retrieval._load_thresholds))
    tree = ast.parse(source)
    literals: dict[str, float] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_Thresholds":
            for keyword in node.keywords:
                if isinstance(keyword.value, ast.Constant) and keyword.arg is not None:
                    literals[keyword.arg] = float(keyword.value.value)

    assert literals, "the fallback no longer constructs _Thresholds from literals"
    for name, value in literals.items():
        assert value == pytest.approx(float(declared[name])), (
            f"the fallback asserts {name}={value} but evals/thresholds.yaml "
            f"declares {declared[name]}; a machine without PyYAML would assert "
            "the wrong bar"
        )


def test_the_loaded_thresholds_are_the_declared_ones() -> None:
    yaml = pytest.importorskip("yaml")
    declared = yaml.safe_load(DECLARED.read_text(encoding="utf-8"))["retrieval"]
    assert retrieval.THRESHOLDS.recall_at_20_min == pytest.approx(
        float(declared["recall_at_20_min"])
    )
    assert retrieval.THRESHOLDS.decoy_share_at_20_max == pytest.approx(
        float(declared["decoy_share_at_20_max"])
    )


def test_ret_01_can_still_reach_a_fail() -> None:
    """A suite whose headline check cannot fail is not asserting anything.

    Structural rather than behavioural: running the suite needs a live cluster.
    This asserts that RET-01's verdict is chosen by an expression mentioning
    FAIL, rather than being the bare `Verdict.PASS` it was.
    """
    source = inspect.getsource(retrieval.run_suite)
    marker = 'check_id="RET-01"'
    # RET-01 is constructed twice: once as the early-return CANNOT_RUN when no
    # hero row shares a case (nothing to score), and once as the scored check.
    # The scored one is what must be able to fail, so look at every occurrence
    # and require that at least one asserts against the declared bars.
    windows = []
    cursor = source.find(marker)
    while cursor != -1:
        windows.append(source[cursor : cursor + 900])
        cursor = source.find(marker, cursor + 1)
    assert windows, "RET-01 is no longer constructed at all"

    scored = [w for w in windows if "THRESHOLDS" in w]
    assert scored, "RET-01 no longer asserts against a declared bar"
    assert any("Verdict.FAIL" in w for w in scored), (
        "RET-01's scored verdict no longer has a reachable FAIL; it is a "
        "hardcoded pass beside a computed fact, which is worse than no verdict"
    )
