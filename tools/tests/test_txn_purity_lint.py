"""No model call and no network call inside a transaction callback — T3.4.

Authority
---------
- ``quality/23_PHASE_GATES.md`` section 9, ``G3.5``: ``python -m
  tools.txn_purity_lint services packages workers`` prints ``scanned NN
  transaction callbacks, 0 network constructs found``. "The lint is AST-based:
  it walks every function decorated ``@in_transaction`` or passed to
  ``run_in_transaction`` and rejects imports/attribute chains rooted at the
  banned clients."
- ``EXECUTION/70_TASK_PLAN.md`` T3.4, including the sub-task that says the
  scanned count must be printed, not only the violation count: "``0
  violations`` over 0 scanned callbacks is the classic vacuous pass and G3.5's
  expected output names both numbers for that reason."
- ``specs/12_KERNEL_ALGORITHMS.md`` section 1.3 — the list of calls that are a
  build defect inside PHASE B.
- ``CANONICAL_DECISIONS.md`` -> *Memory, action, and time*: "no model or
  network call inside the callback".
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.txn_purity_lint import (
    BANNED_ROOTS,
    WRAPPER_CALLS,
    main,
    scan_paths,
    scan_source,
    summary_line,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

CLEAN = textwrap.dedent(
    """
    from provenance_db.retry import in_transaction

    @in_transaction
    async def commit(conn, tx_now):
        await conn.execute("UPDATE cases SET revision = revision + 1")
        return 1
    """
)

DIRTY = textwrap.dedent(
    """
    import boto3
    from provenance_db.retry import in_transaction

    @in_transaction
    async def commit(conn, tx_now):
        client = boto3.client("bedrock-runtime")
        return await client.converse()
    """
)

ALIASED = textwrap.dedent(
    """
    import boto3 as b
    from provenance_db.retry import in_transaction

    @in_transaction
    async def commit(conn, tx_now):
        return b.client("s3").get_object(Bucket="x", Key="y")
    """
)

LAMBDA_CALLBACK = textwrap.dedent(
    """
    import httpx
    from provenance_db.retry import run_in_serializable_tx

    async def go(pool):
        return await run_in_serializable_tx(
            pool, lambda conn, tx_now: httpx.get("https://example.invalid")
        )
    """
)

NAMED_CALLBACK = textwrap.dedent(
    """
    import requests
    from provenance_db.retry import run_in_serializable_tx

    async def _plan(conn, tx_now):
        return requests.post("https://example.invalid", json={})

    async def go(pool):
        return await run_in_serializable_tx(pool, _plan)
    """
)

OUTSIDE = textwrap.dedent(
    """
    import boto3

    async def embed(text):
        return boto3.client("bedrock-runtime").invoke_model(body=text)
    """
)


def test_a_decorated_callback_constructing_a_boto3_client_is_reported() -> None:
    result = scan_source(DIRTY, "dirty.py")
    assert result.scanned == 1
    assert [v.construct for v in result.violations] == ["boto3.client"]
    assert result.violations[0].callback == "commit"


def test_a_decorated_callback_with_no_network_construct_is_not_reported() -> None:
    result = scan_source(CLEAN, "clean.py")
    assert result.scanned == 1
    assert result.violations == ()


def test_an_aliased_import_is_followed() -> None:
    """``import boto3 as b`` must be caught: name resolution follows aliases."""
    result = scan_source(ALIASED, "aliased.py")
    assert result.scanned == 1
    assert [v.construct for v in result.violations] == ["b.client"]
    assert result.violations[0].root == "boto3"


def test_a_lambda_passed_to_the_wrapper_is_scanned() -> None:
    result = scan_source(LAMBDA_CALLBACK, "lambda.py")
    assert result.scanned == 1
    assert [v.root for v in result.violations] == ["httpx"]


def test_a_named_function_passed_to_the_wrapper_is_resolved_and_scanned() -> None:
    """A reference argument is the common shape; only scanning lambdas would
    miss every real call site."""
    result = scan_source(NAMED_CALLBACK, "named.py")
    assert result.scanned == 1
    assert [v.callback for v in result.violations] == ["_plan"]
    assert [v.root for v in result.violations] == ["requests"]


def test_network_calls_outside_a_transaction_callback_are_not_reported() -> None:
    """Embeddings are computed *before* the kernel is entered. The lint must
    not turn into a blanket ban on the SDK."""
    result = scan_source(OUTSIDE, "outside.py")
    assert result.scanned == 0
    assert result.violations == ()


def test_the_summary_names_the_scanned_count_not_only_the_violation_count() -> None:
    assert summary_line(scan_source(CLEAN, "c.py")) == (
        "scanned 1 transaction callbacks, 0 network constructs found"
    )
    assert summary_line(scan_source(DIRTY, "d.py")) == (
        "scanned 1 transaction callbacks, 1 network constructs found"
    )


def test_every_banned_root_is_detected(tmp_path: Path) -> None:
    """The list is enumerated in one place and every entry is exercised, so a
    decorative addition to it cannot go unnoticed."""
    for root in sorted(BANNED_ROOTS):
        source = textwrap.dedent(
            f"""
            import {root}
            from provenance_db.retry import in_transaction

            @in_transaction
            async def commit(conn, tx_now):
                return {root}.anything()
            """
        )
        result = scan_source(source, f"{root}.py")
        assert result.violations, f"{root} is in BANNED_ROOTS but is not detected"


def test_main_exits_non_zero_on_a_planted_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ok.py").write_text(CLEAN, encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert "scanned 1 transaction callbacks, 0 network constructs found" in capsys.readouterr().out

    (tmp_path / "planted.py").write_text(DIRTY, encoding="utf-8")
    assert main([str(tmp_path)]) == 1
    captured = capsys.readouterr().out
    assert "planted.py" in captured
    assert "scanned 2 transaction callbacks, 1 network constructs found" in captured


def test_the_wrapper_names_the_lint_recognises_are_both_spellings() -> None:
    """``23_PHASE_GATES.md`` says ``run_in_transaction``;
    ``12_KERNEL_ALGORITHMS.md`` section 7.2 prints ``run_in_serializable_tx``.
    The lint answers to both so the discrepancy cannot hide a callback."""
    assert {"run_in_transaction", "run_in_serializable_tx"} <= WRAPPER_CALLS


def test_the_repository_tree_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    """``G3.5``, run over the three trees the gate names."""
    targets = [REPO_ROOT / name for name in ("services", "packages", "workers")]
    result = scan_paths([path for path in targets if path.exists()])
    print(summary_line(result))
    assert result.violations == (), "\n".join(str(v) for v in result.violations)
    assert (
        result.scanned > 0
    ), "0 scanned callbacks is the vacuous pass this lint exists to make impossible"
    assert "scanned" in capsys.readouterr().out
