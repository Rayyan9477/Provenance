"""``tools/manifest_check.py`` - the seed manifest against the live row counts.

Authority
---------
- ``docs/quality/23_PHASE_GATES.md`` ``G2.6``::

      python -m tools.manifest_check db/seeds/MANIFEST.json
      #   -> "26 tables checked, 26 match"

- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T2.7`` sub-task: "Write
  ``tools/manifest_check.py`` to read ``db/seeds/MANIFEST.json`` and compare
  expected against actual row counts per table."
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T2.8`` - the task that writes the
  manifest. It had not landed when this checker was built, which is precisely
  why the absent-manifest behaviour is asserted first below.

The failure this file exists to prevent
---------------------------------------
``26 tables checked, 26 match`` is a sentence a gate reviewer reads as proof.
The two ways it lies are: the manifest is missing and the checker skips, and the
manifest covers four tables while the sentence still says twenty-six. Both are
asserted against here. The count in the summary line is the number of
**canonical tables** from ``db/expected_tables.txt``, never the number of keys
the manifest happened to contain.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tools.manifest_check import (
    CANONICAL_TABLES_FILE,
    ManifestError,
    canonical_tables,
    compare,
    load_manifest,
    main,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _canonical() -> tuple[str, ...]:
    return canonical_tables(REPO_ROOT / CANONICAL_TABLES_FILE)


def _full_manifest(**overrides: int) -> dict[str, int]:
    counts = {table: 1 for table in _canonical()}
    counts.update(overrides)
    return counts


def _counter(counts: Mapping[str, int]):
    def _count(tables: Sequence[str]) -> Mapping[str, int]:
        return {table: counts.get(table, 0) for table in tables}

    return _count


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The canonical table list
# ---------------------------------------------------------------------------


def test_canonical_tables_are_the_twenty_six_from_expected_tables_txt() -> None:
    """``db/expected_tables.txt`` is the authority, not the manifest's own keys.

    DDL section 20 risk 2: "The canonical table count is 26 ... The migration
    chain, expected-table manifest, gates, and submission material must all
    assert 26."
    """
    tables = _canonical()
    assert len(tables) == 26
    assert tables == tuple(sorted(tables))
    assert "evidence_items" in tables
    assert "alembic_version" not in tables


# ---------------------------------------------------------------------------
# A manifest that is not there
# ---------------------------------------------------------------------------


def test_absent_manifest_fails_loudly_and_never_skips(tmp_path: Path, capsys) -> None:
    """``db/seeds/MANIFEST.json`` is ``T2.8``'s deliverable and does not exist yet.

    The checker must say so, name the file and name the task, and exit non-zero.
    A checker that treats an absent manifest as "nothing to do" turns G2.6 into a
    sentence about a file nobody wrote.
    """
    missing = tmp_path / "MANIFEST.json"
    code = main([str(missing)], counter=_counter({}))
    captured = capsys.readouterr()

    assert code != 0
    assert "MANIFEST.json" in captured.err
    assert "T2.8" in captured.err
    assert "match" not in captured.out
    assert "skip" not in (captured.out + captured.err).lower()


def test_absent_manifest_names_the_shape_it_expects(tmp_path: Path, capsys) -> None:
    """The error tells ``T2.8`` what to write, so the two cannot drift silently."""
    main([str(tmp_path / "MANIFEST.json")], counter=_counter({}))
    err = capsys.readouterr().err
    assert "row_counts" in err
    assert "evidence_items" in err


def test_malformed_manifest_is_an_error_not_an_empty_check(tmp_path: Path, capsys) -> None:
    path = tmp_path / "MANIFEST.json"
    path.write_text("{not json", encoding="utf-8")
    code = main([str(path)], counter=_counter({}))
    captured = capsys.readouterr()
    assert code != 0
    assert "match" not in captured.out


def test_manifest_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "MANIFEST.json", [1, 2, 3])
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Shapes the manifest may take
# ---------------------------------------------------------------------------


def test_row_counts_key_is_the_preferred_shape(tmp_path: Path) -> None:
    path = _write(tmp_path / "MANIFEST.json", {"seed_profile": "all", "row_counts": {"cases": 10}})
    assert load_manifest(path) == {"cases": 10}


def test_nested_row_count_objects_are_accepted(tmp_path: Path) -> None:
    """A manifest carrying a hash beside each count is still a manifest."""
    path = _write(
        tmp_path / "MANIFEST.json",
        {"row_counts": {"cases": {"rows": 10, "sha256": "abc"}}},
    )
    assert load_manifest(path) == {"cases": 10}


def test_a_flat_table_to_count_mapping_is_accepted(tmp_path: Path) -> None:
    path = _write(tmp_path / "MANIFEST.json", {"cases": 10, "tenants": 3})
    assert load_manifest(path) == {"cases": 10, "tenants": 3}


def test_a_non_integer_count_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": {"cases": "ten"}})
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------


def test_all_twenty_six_matching_prints_the_gate_sentence(tmp_path: Path, capsys) -> None:
    manifest = _full_manifest()
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": manifest})
    code = main([str(path)], counter=_counter(manifest))
    out = capsys.readouterr().out
    assert code == 0
    assert "26 tables checked, 26 match" in out


def test_one_wrong_count_fails_and_names_both_numbers(tmp_path: Path, capsys) -> None:
    manifest = _full_manifest(evidence_items=18035)
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": manifest})
    actual = dict(manifest, evidence_items=18032)
    code = main([str(path)], counter=_counter(actual))
    captured = capsys.readouterr()
    assert code == 1
    assert "26 tables checked, 25 match" in captured.out
    assert "evidence_items" in captured.out + captured.err
    assert "18035" in captured.out + captured.err
    assert "18032" in captured.out + captured.err


def test_a_manifest_missing_canonical_tables_cannot_report_twenty_six(
    tmp_path: Path, capsys
) -> None:
    """The partial manifest is the vacuous pass this checker must refuse.

    Four tables listed, four tables matching, and a summary line claiming
    twenty-six would be a green log for a seed nobody verified.
    """
    partial = {"tenants": 3, "users": 3, "cases": 10, "evidence_items": 18035}
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": partial})
    code = main([str(path)], counter=_counter(partial))
    captured = capsys.readouterr()
    assert code != 0
    assert "26 tables checked, 26 match" not in captured.out
    assert "belief_versions" in captured.out + captured.err


def test_a_table_outside_the_canonical_set_is_rejected(tmp_path: Path, capsys) -> None:
    manifest = _full_manifest()
    manifest["alembic_version"] = 1
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": manifest})
    code = main([str(path)], counter=_counter(manifest))
    captured = capsys.readouterr()
    assert code != 0
    assert "alembic_version" in captured.out + captured.err


def test_compare_reports_every_mismatch_not_only_the_first() -> None:
    canonical = _canonical()
    expected = {table: 1 for table in canonical}
    actual = dict(expected)
    actual["cases"] = 0
    actual["tenants"] = 5
    report = compare(expected=expected, actual=actual, canonical=canonical)
    assert not report.ok
    assert report.checked == 26
    assert report.matched == 24
    assert {mismatch.table for mismatch in report.mismatches} == {"cases", "tenants"}
    assert report.summary_line == "26 tables checked, 24 match"


def test_a_zero_row_table_that_the_manifest_expects_to_be_zero_matches() -> None:
    """Zero is a legitimate expected count; it is only a lie when unexpected."""
    canonical = _canonical()
    expected = dict.fromkeys(canonical, 0)
    report = compare(expected=expected, actual=dict(expected), canonical=canonical)
    assert report.ok
    assert report.summary_line == "26 tables checked, 26 match"


# ---------------------------------------------------------------------------
# Credential hygiene
# ---------------------------------------------------------------------------


def test_a_dsn_in_a_connection_error_is_redacted(tmp_path: Path, capsys) -> None:
    """G0.3 scans committed gate logs. A failed connection must not print its URL."""
    path = _write(tmp_path / "MANIFEST.json", {"row_counts": _full_manifest()})

    def _explode(tables: Sequence[str]) -> Mapping[str, int]:
        raise RuntimeError(
            "connection failed: postgresql://pv_migrator:hunter2@cluster.example:26257/provenance_ci"
        )

    code = main([str(path)], counter=_explode)
    captured = capsys.readouterr()
    assert code != 0
    assert "hunter2" not in captured.out + captured.err
    assert "<redacted>" in captured.err


def test_the_module_contains_no_credential_shaped_literal() -> None:
    """No ``scheme://user:secret@host`` anywhere in the checker's source."""
    source = (REPO_ROOT / "tools" / "manifest_check.py").read_text(encoding="utf-8")
    assert re.search(r"://[^\s/'\"]+:[^\s/'\"]+@", source) is None
