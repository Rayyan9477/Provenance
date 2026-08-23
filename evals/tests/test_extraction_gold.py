"""The gold labels must be readable off the source document, not off a run.

Rule E1: "A label written by running the system and recording what it did is
not a label; it is a regression snapshot wearing a label's clothes."

That rule is unenforceable as prose, because a label copied from an output and
a label derived from the document look identical on the page. What is
enforceable is the *document*: every money figure a label names must occur in
the artifact bytes, and every token a label excludes must not. A label invented
to agree with an observed output fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.runner.gold import GOLD_FILE, OPERATORS, load_gold, score_extraction
from evals.runner.transcript import RecordedExtraction, parse_transcript

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

#: "USD 1800.00" as written in a label; the document writes "USD 1,800.00".
_MONEY = re.compile(r"^(?P<currency>[A-Z]{3})\s+(?P<amount>[0-9.]+)$")


def _bytes_of(label_file: str) -> str:
    return (REPO_ROOT / label_file).read_text(encoding="utf-8")


def test_the_gold_file_declares_the_rule_it_is_written_under() -> None:
    document = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    assert "Rule E1" in document["label_rule"]
    assert document["coverage"], "the gold file states no coverage; a partial corpus read as full"


def test_every_gold_label_points_at_a_file_that_exists() -> None:
    for label in load_gold().values():
        assert (REPO_ROOT / label.artifact_file).is_file(), label.artifact_file


def test_every_gold_label_cites_where_it_came_from() -> None:
    for label in load_gold().values():
        assert len(label.label_source) > 40, (
            f"{label.artifact} carries no usable label_source. A gold value "
            f"whose derivation is not written down cannot be distinguished "
            f"from one copied off a run."
        )


def test_every_money_figure_in_a_gold_label_occurs_in_the_artifact_bytes() -> None:
    for label in load_gold().values():
        expected = label.expect.get("commitment_money_include")
        if expected is None:
            continue
        match = _MONEY.match(str(expected))
        assert match, f"{label.artifact}: {expected!r} is not '<CUR> <amount>'"
        source = _bytes_of(label.artifact_file)
        amount = match.group("amount")
        # The document may write thousands separators; the label does not.
        variants = {amount, f"{int(float(amount)):,}.{amount.split('.')[1]}"}
        assert any(variant in source for variant in variants), (
            f"{label.artifact} expects {expected!r} but no variant of {amount} "
            f"({sorted(variants)}) occurs in {label.artifact_file}. A gold "
            f"figure that is not in the document was read off a run."
        )
        assert match.group("currency") in source


def test_every_summary_token_a_label_requires_occurs_in_the_artifact_bytes() -> None:
    for label in load_gold().values():
        source = _bytes_of(label.artifact_file)
        for token in label.expect.get("summary_contains_all", ()):
            assert token in source, (
                f"{label.artifact} requires the summary to contain {token!r}, "
                f"which does not appear in {label.artifact_file}. The label is "
                f"then a claim about the model rather than about the document."
            )


def test_every_excluded_summary_token_is_genuinely_absent_from_the_artifact() -> None:
    for label in load_gold().values():
        source = _bytes_of(label.artifact_file)
        for token in label.expect.get("summary_excludes_any", ()):
            assert token not in source, (
                f"{label.artifact} forbids {token!r} in the summary, but the "
                f"document itself contains it. The expectation is then wrong, "
                f"not strict."
            )


def test_a_gold_file_naming_an_unknown_operator_is_refused(tmp_path: Path) -> None:
    rogue = tmp_path / "gold.json"
    rogue.write_text(
        json.dumps(
            {
                "artifacts": {
                    "x": {
                        "artifact_file": "demo/artifacts/northline-final-invoice.eml",
                        "label_source": "s" * 50,
                        "expect": {"claim_kinds_incude": "COUNTERPARTY_CLAIM"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown operator"):
        load_gold(rogue)


def test_the_operator_vocabulary_is_closed_and_every_member_is_implemented() -> None:
    empty = RecordedExtraction(artifact="x", verdict="PASS", summary="")
    for operator in OPERATORS:
        label = load_gold()["injected-instruction"]
        # Each operator takes the shape its name implies. A probe that fed a
        # list to a scalar operator would raise rather than score, and the
        # coverage claim would be about the probe.
        if "_at_" in operator:
            value: object = 0
        elif operator.endswith(("_all", "_any")):
            value = ["Z"]
        else:
            value = "Z"
        probe = type(label)(
            artifact="x",
            artifact_file=label.artifact_file,
            label_source=label.label_source,
            expect={operator: value},
        )
        scores = score_extraction(probe, empty)
        assert len(scores) == 1, f"{operator} produced {len(scores)} scores, expected 1"


def test_the_invoice_label_demands_an_interested_party_claim_kind() -> None:
    """00_PRODUCT.md section 2.2's second bullet, as an executable expectation."""
    label = load_gold()["northline-final-invoice"]
    assert label.expect["claim_kinds_include"] == "COUNTERPARTY_CLAIM", (
        "the invoice label stopped demanding COUNTERPARTY_CLAIM. That single "
        "field is the whole of 'the invoice is a claim by an interested party, "
        "not a fact'."
    )


def test_the_recorded_run_is_scored_against_gold_and_the_scores_are_not_vacuous() -> None:
    recorded = parse_transcript()
    labels = load_gold()
    scored = [
        score
        for name, label in labels.items()
        if name in recorded
        for score in score_extraction(label, recorded[name])
    ]
    assert scored, "no gold label matched a recorded artifact; the suite scores nothing"
    # A scorer that returns True unconditionally would pass every assertion
    # above. Feed it an extraction that is definitively wrong and require it to
    # notice.
    wrong = RecordedExtraction(artifact="x", verdict="PASS", summary="nothing here")
    for name, label in labels.items():
        if name not in recorded:
            continue
        missed = [score for score in score_extraction(label, wrong) if not score.ok]
        assert missed, (
            f"the scorer passed {name} against an empty extraction. Every "
            f"expectation in that label is satisfied by nothing at all, so the "
            f"label measures nothing."
        )
