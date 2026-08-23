"""No contract may contain SQL text, a canonical table name, or a view name.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 20.10, which prints the three tests this
  file implements, and section 23 risk 4, which records what the rule does and
  does not prove.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, last sub-task: ``no-sql-in-contracts`` is
  one of the three rules ``tools/contract_lint.py`` must implement.

Why this delegates to ``tools.contract_lint``
----------------------------------------------
Section 20.10 prints the scanner inline in the test. Shipping it twice -- once
in the test and once in the lint the gate runs -- is two implementations of one
rule, and the gate's copy is the one nobody reads. The scanner lives in the
tool; this file asserts its behaviour, including the parts a green run cannot
demonstrate on its own.

What a green run does **not** prove, and is tested here explicitly
-------------------------------------------------------------------
"Zero violations" is compatible with a rule that cannot fire. Three of the
tests below therefore run the scanner against *synthetic* sources:

* a genuine ``SELECT ... FROM cases`` planted in a carved-out module is still
  caught, because the carve-out covers the table-name check only;
* every carve-out is load-bearing -- removing it produces exactly the violation
  it was granted for, so a stale carve-out cannot accumulate;
* the carve-out is scoped to one token in one file and suppresses nothing else.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tools import contract_lint
from tools.contract_lint import (
    CANONICAL_TABLES,
    TABLE_NAME_CARVE_OUTS,
    SourceFile,
    check_no_sql_in_contracts,
    load_sources,
    string_constants,
)


def _synthetic(name: str, source: str) -> SourceFile:
    """A parsed module with a chosen file name, for carve-out scoping tests."""
    return SourceFile(
        path=pathlib.Path("/synthetic") / name,
        source=source,
        tree=ast.parse(source, name),
    )


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.10 — the shipped package is clean
# ---------------------------------------------------------------------------


def test_no_canonical_table_name_appears_in_any_contract_literal() -> None:
    offences = [
        v for v in check_no_sql_in_contracts(load_sources()) if "canonical table name" in v.message
    ]
    assert not offences, "canonical table names found in contracts:\n" + "\n".join(
        v.render(contract_lint.PACKAGE_ROOT) for v in offences
    )


def test_no_sql_statement_appears_in_any_contract_literal() -> None:
    offences = [
        v for v in check_no_sql_in_contracts(load_sources()) if "SQL statement shape" in v.message
    ]
    assert not offences, "SQL statement shapes found in contracts:\n" + "\n".join(
        v.render(contract_lint.PACKAGE_ROOT) for v in offences
    )


def test_agent_safe_view_names_live_in_the_domain_package_not_here() -> None:
    """Carve-out, stated deliberately.

    ``AgentSafeView`` names read-model views and is defined in
    ``provenance_domain.enums``, where it functions as an allowlist rather than
    an instruction. The contracts package may reference the enum but must not
    hard-code a view name as a literal.
    """
    offences = [v for v in check_no_sql_in_contracts(load_sources()) if "view name" in v.message]
    assert not offences, "\n".join(v.render(contract_lint.PACKAGE_ROOT) for v in offences)


def test_the_whole_package_is_clean_under_the_shipped_rule() -> None:
    assert check_no_sql_in_contracts(load_sources()) == []


def test_docstrings_may_discuss_the_data_model_in_prose() -> None:
    """Section 3 of the spec depends on this, so it is pinned rather than assumed.

    The same sentence is exempt as a docstring and a violation as a literal.
    Testing only the exempt half would pass on a scanner that had stopped
    looking at literals altogether.
    """
    prose = "This module writes claims and beliefs to cases."

    as_docstring = _synthetic("proposal.py", f'"""{prose}"""\n')
    assert [text for _, text in string_constants(as_docstring.tree)] == []
    assert check_no_sql_in_contracts([as_docstring]) == []

    as_literal = _synthetic("proposal.py", f"NOTE = {prose!r}\n")
    hits = {v.message.split("'")[1] for v in check_no_sql_in_contracts([as_literal])}
    assert hits == {"beliefs", "cases", "claims"}, hits


# ---------------------------------------------------------------------------
# T1.6 — the carve-outs are narrow, named, and load-bearing
# ---------------------------------------------------------------------------


def test_the_carve_outs_are_exactly_the_four_that_were_granted() -> None:
    """A fifth carve-out is a deliberate act, not a drive-by suppression.

    Three were expected before the work started -- the two projection roots and
    the CapabilityBinding sentence. The fourth, triggers.py/"claims", was the
    only new collision that could not be reworded, because a pre-existing test
    asserts the message verbatim. Every other collision in the new modules was
    reworded instead of carved out.
    """
    assert set(TABLE_NAME_CARVE_OUTS) == {
        ("predicates.py", "commitments"),
        ("predicates.py", "conflicts"),
        ("identity.py", "cases"),
        ("triggers.py", "claims"),
    }
    for key, reason in TABLE_NAME_CARVE_OUTS.items():
        assert len(reason) > 40, f"{key} has no stated reason"


def test_every_carve_out_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove them all and exactly the granted set comes back.

    This is what stops a carve-out outliving the literal it was granted for.
    A stale entry would silently widen the blind spot, and nothing else in this
    file would notice.
    """
    monkeypatch.setattr(contract_lint, "TABLE_NAME_CARVE_OUTS", {})
    uncovered = {
        (v.path.name, v.message.split("'")[1])
        for v in check_no_sql_in_contracts(load_sources())
        if "canonical table name" in v.message
    }
    assert uncovered == set(TABLE_NAME_CARVE_OUTS)


def test_a_real_select_in_a_carved_out_module_is_still_caught() -> None:
    """The carve-out covers the table-name check only. This is the whole point.

    ``identity.py`` may say the English word "cases". It may not say
    ``SELECT ... FROM cases``, and the difference has to be mechanical rather
    than a matter of trust.
    """
    planted = _synthetic(
        "identity.py",
        'QUERY = "SELECT id FROM cases WHERE tenant_id = %s"\n',
    )
    violations = check_no_sql_in_contracts([planted])
    assert any("SQL statement shape" in v.message for v in violations), violations


def test_a_carve_out_does_not_cover_other_tables_in_the_same_file() -> None:
    """Carving out "cases" in identity.py says nothing about any other name."""
    planted = _synthetic("identity.py", 'MSG = "may not span more than 16 cases or beliefs"\n')
    messages = [v.message for v in check_no_sql_in_contracts([planted])]
    assert any("'beliefs'" in m for m in messages), messages
    assert not any("'cases'" in m for m in messages), messages


def test_a_carve_out_does_not_travel_to_another_file() -> None:
    """The same sentence in a module without the grant is a violation."""
    planted = _synthetic("proposal.py", 'MSG = "may not span more than 16 cases"\n')
    messages = [v.message for v in check_no_sql_in_contracts([planted])]
    assert any("'cases'" in m for m in messages), messages


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM evidence_items",
        "INSERT INTO claims (id) VALUES (1)",
        "UPDATE commitments SET status = 'FULFILLED'",
        "DELETE FROM outbox_events",
        "UPSERT INTO beliefs (id) VALUES (1)",
        "DROP TABLE cases",
        "GRANT SELECT ON beliefs",
        "SELECT 1 UNION ALL SELECT 2",
        "'; DROP TABLE users",
    ],
)
def test_the_statement_patterns_actually_match_sql(statement: str) -> None:
    """A pattern list nobody has fired is a pattern list nobody can trust."""
    planted = _synthetic("proposal.py", f"QUERY = {statement!r}\n")
    violations = check_no_sql_in_contracts([planted])
    assert any("SQL statement shape" in v.message for v in violations), statement


def test_a_hard_coded_read_model_view_name_is_caught() -> None:
    planted = _synthetic("retrieval.py", 'VIEW = "agent_case_context_v1"\n')
    violations = check_no_sql_in_contracts([planted])
    assert any("view name" in v.message for v in violations), violations


def test_a_secrets_manager_key_is_not_a_view_name() -> None:
    """The shipped ``settings.py`` carries ``secret_key="agent_url"``.

    Section 20.10's printed check is ``'"agent_' not in source``, a prefix test
    that fails on that line. A Secrets Manager key for a DSN is not a read-model
    view, so the shipped rule matches the view *shape* and the enum's own
    values instead. Reported as a spec defect, and pinned here so the looser
    prefix test is not reintroduced by someone reading section 20.10 literally.
    """
    planted = _synthetic("settings.py", 'SECRET = "agent_url"\n')
    assert check_no_sql_in_contracts([planted]) == []


def test_the_table_list_is_the_twenty_six_canonical_tables() -> None:
    assert len(CANONICAL_TABLES) == 26
