"""G4.3 - the Memory Kernel is the only canonical writer, checked structurally.

Authority
---------
- ``quality/23_PHASE_GATES.md`` ``G4.3``::

      python -m tools.write_path_lint
      #  -> "canonical write statements found in 2 modules:
      #      .../app/events, .../app/memory_kernel"
      #  -> "agents/: 0    workers/: 0    apps/web/: 0    packages/: 0"

  ``G4.3`` prints "1 module" because it was written before the outbox
  dispatcher existed. ``app/events`` issues ``UPDATE outbox_events SET
  status = ...`` and nothing else -- the single exception
  ``provenance_db.repositories.__init__`` enumerates, status bookkeeping about
  a row the Kernel wrote, carrying no domain meaning. The guarantee was never
  "one module"; it is "these modules, for these reasons", and the tests below
  assert the second form.

- ``specs/10_DATABASE_DDL.md`` section 12, the write-path ownership table, which
  is the source of truth for which tables are Kernel-only and for the three
  places where another role legitimately writes.
- ``EXECUTION/70_TASK_PLAN.md`` T4.1 and section 7's acceptance for ``T4.1``.

Why the scanned counts are asserted
-----------------------------------
``0 violations`` over ``0`` scanned statements is a vacuous pass, and it is the
exact failure ``tools/contract_lint.py`` was built to avoid repeating
(``D-00-014``: ``tools/`` was missing from ``testpaths`` and 28 tests ran
nowhere for a week). Every test below that asserts a clean tree also asserts
that the linter actually found the Kernel's writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import write_path_lint as lint

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The rule set
# ---------------------------------------------------------------------------


def test_the_canonical_table_set_is_not_empty() -> None:
    assert len(lint.CANONICAL_TABLES) >= 15


def test_the_canonical_tables_are_the_ddl_section_12_kernel_writable_ones() -> None:
    for table in (
        "cases",
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "conflicts",
        "commitments",
        "fulfillments",
        "state_transitions",
        "kernel_decisions",
        "prospective_triggers",
        "outbox_events",
        "counterparties",
        "relationships",
        "contexts",
        "evidence_items",
        "memory_proposals",
    ):
        assert table in lint.CANONICAL_TABLES, f"{table} is Kernel-writable in DDL section 12"


def test_tables_no_role_but_the_app_writes_are_not_canonical() -> None:
    """``users``, ``tenants``, ``action_executions``, ``processed_events`` and
    ``idempotency_records`` have no ``pv_kernel_writer`` write grant at all, so
    flagging a control-plane INSERT into them would be a false positive."""
    for table in (
        "users",
        "tenants",
        "ingest_aliases",
        "action_executions",
        "processed_events",
        "idempotency_records",
    ):
        assert table not in lint.CANONICAL_TABLES


def test_every_rule_is_named() -> None:
    assert len(lint.RULES) >= 4
    assert len(set(lint.RULES)) == len(lint.RULES)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_an_insert_outside_the_kernel_is_a_violation() -> None:
    source = 'SQL = "INSERT INTO conflicts (id) VALUES (%s)"\n'
    result = lint.scan_source(source, "services/control_plane/app/api/routes.py")
    assert len(result.violations) == 1
    assert result.violations[0].table == "conflicts"
    assert result.violations[0].operation == "INSERT"


def test_an_update_outside_the_kernel_is_a_violation() -> None:
    source = 'SQL = "UPDATE cases SET status = %s WHERE id = %s"\n'
    result = lint.scan_source(source, "workers/outbox/dispatch.py")
    assert len(result.violations) == 1
    assert result.violations[0].operation == "UPDATE"


def test_a_delete_from_a_canonical_table_is_always_a_violation() -> None:
    """Nothing deletes a canonical row, including the Kernel. Retraction is an
    UPDATE (DDL section 5.4): the embedding cannot simply be deleted."""
    source = 'SQL = "DELETE FROM evidence_items WHERE id = %s"\n'
    result = lint.scan_source(source, "services/control_plane/app/memory_kernel/transaction.py")
    assert len(result.violations) == 1
    assert result.violations[0].operation == "DELETE"


def test_the_same_statement_inside_the_kernel_is_counted_not_flagged() -> None:
    source = 'SQL = "INSERT INTO conflicts (id) VALUES (%s)"\n'
    result = lint.scan_source(source, "services/control_plane/app/memory_kernel/transaction.py")
    assert result.violations == []
    assert result.kernel_statements == 1
    assert result.canonical_statements == 1


def test_an_app_insert_into_evidence_items_is_permitted() -> None:
    """DDL section 12: the parser admits evidence before any proposal exists.
    ``UPDATE`` stays Kernel-only, so only the Kernel can retract."""
    source = 'SQL = "INSERT INTO evidence_items (id) VALUES (%s)"\n'
    assert (
        lint.scan_source(source, "services/control_plane/app/ingestion/registrar.py").violations
        == []
    )


def test_an_app_update_of_evidence_items_is_refused() -> None:
    source = 'SQL = "UPDATE evidence_items SET retraction_status = %s"\n'
    result = lint.scan_source(source, "services/control_plane/app/ingestion/registrar.py")
    assert len(result.violations) == 1


def test_an_app_insert_into_memory_proposals_is_permitted() -> None:
    source = 'SQL = "INSERT INTO memory_proposals (id) VALUES (%s)"\n'
    assert lint.scan_source(source, "services/control_plane/app/api/proposals.py").violations == []


def test_an_app_update_of_memory_proposals_is_refused() -> None:
    source = 'SQL = "UPDATE memory_proposals SET status = %s"\n'
    assert (
        len(lint.scan_source(source, "services/control_plane/app/api/proposals.py").violations) == 1
    )


def test_the_dispatcher_may_update_outbox_events_but_not_insert() -> None:
    permitted = 'SQL = "UPDATE outbox_events SET status = %s"\n'
    refused = 'SQL = "INSERT INTO outbox_events (id) VALUES (%s)"\n'
    assert lint.scan_source(permitted, "workers/outbox/dispatch.py").violations == []
    assert len(lint.scan_source(refused, "workers/outbox/dispatch.py").violations) == 1


def test_a_multiline_statement_is_found() -> None:
    source = 'SQL = """\n    INSERT INTO belief_versions (id)\n    VALUES (%s)\n"""\n'
    assert len(lint.scan_source(source, "agents/runtime/writer.py").violations) == 1


def test_case_and_whitespace_do_not_hide_a_write() -> None:
    source = 'SQL = "insert   into\\n  conflicts (id) values (%s)"\n'
    assert len(lint.scan_source(source, "agents/runtime/writer.py").violations) == 1


def test_a_select_is_not_a_write() -> None:
    source = 'SQL = "SELECT id FROM conflicts WHERE case_id = %s"\n'
    assert lint.scan_source(source, "agents/runtime/reader.py").violations == []


def test_a_comment_mentioning_a_table_is_not_a_write() -> None:
    source = "# the Kernel inserts into conflicts here\nX = 1\n"
    assert lint.scan_source(source, "agents/runtime/reader.py").violations == []


def test_a_docstring_showing_the_forbidden_shape_is_not_a_write() -> None:
    """A module that documents the rule must not trip it. The linter reads
    assigned string constants and SQL passed to a cursor, not prose."""
    source = '"""Never write: INSERT INTO conflicts (id) VALUES (1)."""\nX = 1\n'
    assert lint.scan_source(source, "agents/runtime/reader.py").violations == []


def test_a_table_whose_name_is_a_prefix_of_a_canonical_one_is_not_matched() -> None:
    source = 'SQL = "INSERT INTO cases_archive (id) VALUES (%s)"\n'
    assert lint.scan_source(source, "agents/runtime/writer.py").violations == []


# ---------------------------------------------------------------------------
# The tree as it stands
# ---------------------------------------------------------------------------


def test_the_repository_tree_is_clean_and_the_scan_was_not_vacuous() -> None:
    result = lint.scan_paths([REPO_ROOT / root for root in lint.DEFAULT_ROOTS])
    assert result.violations == [], "\n".join(str(v) for v in result.violations)
    assert result.scanned_modules > 50, "the linter walked almost nothing"
    assert result.kernel_statements > 0, (
        "no canonical write was found in the Kernel; a lint that cannot see the "
        "one legitimate writer cannot see an illegitimate one either"
    )


def test_the_non_kernel_roots_report_zero() -> None:
    result = lint.scan_paths([REPO_ROOT / root for root in lint.DEFAULT_ROOTS])
    for root in ("agents", "workers", "packages"):
        assert result.per_root.get(root, 0) == 0, f"{root} holds a canonical write"


def test_the_summary_prints_a_rule_count_and_a_violation_count() -> None:
    result = lint.scan_paths([REPO_ROOT / root for root in lint.DEFAULT_ROOTS])
    lines = lint.summary_lines(result)
    joined = "\n".join(lines)
    assert f"{len(lint.RULES)} rules" in joined
    assert "0 violations" in joined
    assert lint.KERNEL_MODULE in joined
    assert "agents/: 0" in joined
    assert "workers/: 0" in joined
    assert "packages/: 0" in joined
    assert "apps/web/: 0" in joined

    # This said "found in 1 module" until 2026-08-24, when `app/events` landed
    # the outbox dispatcher. Six `UPDATE outbox_events SET status = ...`
    # statements is not a breach: it is the ONE exception
    # `provenance_db.repositories.__init__` enumerates -- status bookkeeping
    # about a row the Kernel wrote, carrying no domain meaning.
    #
    # The assertion is now on WHICH modules rather than on how many, because a
    # count is exactly the wrong shape here. Relaxing "1" to "2" would have
    # admitted any second module; naming them means a third one fails, and so
    # does the right module being replaced by the wrong one. That distinction
    # is the whole guarantee: it is not that few modules write canonically, it
    # is that *these* do.
    modules = (
        {module for module, _ in result.per_module.items()}
        if hasattr(result, "per_module")
        else None
    )
    permitted = {lint.KERNEL_MODULE, "services/control_plane/app/events"}
    named = {m for m in permitted if m in joined}
    assert named == permitted, (
        f"the summary names {named}, expected exactly {permitted}. A module "
        "appearing here that is not the Kernel must be the outbox dispatcher, "
        "and it must be the outbox dispatcher for the enumerated reason."
    )
    assert (
        modules is None or modules <= permitted
    ), f"a module outside the enumeration writes canonical tables: {modules - permitted}"


def test_the_summary_reports_what_it_excluded() -> None:
    """Tests write fixture rows directly and are excluded on purpose. An
    exclusion nobody can see is an exclusion that grows."""
    result = lint.scan_paths([REPO_ROOT / root for root in lint.DEFAULT_ROOTS])
    joined = "\n".join(lint.summary_lines(result))
    assert "excluded" in joined
    assert result.excluded_test_modules > 0


def test_main_exits_zero_on_a_clean_tree(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = lint.main([])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out
    assert "0 violations" in captured.out


def test_main_exits_one_when_a_violation_is_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The counterfactual, run as a test rather than only by hand at the gate."""
    planted = tmp_path / "agents" / "runtime"
    planted.mkdir(parents=True)
    (planted / "writer.py").write_text(
        'SQL = "INSERT INTO conflicts (id) VALUES (%s)"\n', encoding="utf-8"
    )
    exit_code = lint.main([str(tmp_path / "agents")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "1 violations" in captured.out or "1 violation" in captured.out
    assert "conflicts" in captured.out


def test_a_missing_path_is_a_usage_error_not_a_silent_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = lint.main([str(tmp_path / "nowhere")])
    capsys.readouterr()
    assert exit_code == 2
