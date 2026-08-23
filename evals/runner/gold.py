"""Hand-written extraction labels, and the eleven operators that score them.

Rule E1 (``docs/quality/22_EVAL_DATASETS.md`` section 1.2): the expected value
is written **before**, and from, the source document -- never from what the
system produced. A label read off a run is a regression snapshot wearing a
label's clothes, and it passes by construction.

That rule is enforced rather than promised.
``evals/tests/test_extraction_gold.py`` asserts that every money figure a label
names actually occurs in the artifact bytes and that every excluded token
actually does not, so a label invented to agree with an observed output fails
its own test.

One operator was considered and deliberately left out. ``InjectionObservation.
action_taken`` is ``Literal["TREATED_AS_DATA"]`` in
``provenance_contracts.ingestion`` -- one admissible value -- so an assertion
that an injection was "treated as data" is satisfied by the type system and
cannot fail. That is a vacuous assertion, and STATUS.md counts nine of those as
defects found this session. What is scored instead is the thing the type cannot
guarantee: that the injected artifact produced **no commitment**.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from evals.runner.transcript import RecordedExtraction

__all__ = [
    "GOLD_FILE",
    "OPERATORS",
    "FieldScore",
    "GoldLabel",
    "load_gold",
    "score_extraction",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
GOLD_FILE: Final[Path] = REPO_ROOT / "evals" / "extraction" / "gold.json"

#: The closed operator vocabulary. A gold file naming anything else is a
#: typo that would otherwise be silently skipped -- and a skipped expectation
#: is an expectation that always passes.
OPERATORS: Final[tuple[str, ...]] = (
    "claim_kinds_include",
    "claim_kinds_exclude_any",
    "claim_modalities_include",
    "commitment_types_include",
    "commitment_money_include",
    "commitments_at_least",
    "commitments_at_most",
    "injections_at_least",
    "injections_at_most",
    "summary_contains_all",
    "summary_excludes_any",
)


@dataclass(frozen=True)
class GoldLabel:
    artifact: str
    artifact_file: str
    label_source: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class FieldScore:
    """One expectation, checked. ``detail`` says what was found, not just that
    it differed -- a diff a reader cannot act on is a failed assertion twice."""

    artifact: str
    operator: str
    expected: Any
    ok: bool
    detail: str


def load_gold(path: Path | None = None) -> dict[str, GoldLabel]:
    """Every label in the gold file, keyed by artifact name."""
    source = path if path is not None else GOLD_FILE
    document = json.loads(source.read_text(encoding="utf-8"))
    labels: dict[str, GoldLabel] = {}
    for name, entry in document["artifacts"].items():
        unknown = sorted(set(entry["expect"]) - set(OPERATORS))
        if unknown:
            raise ValueError(
                f"gold label {name!r} uses unknown operator(s) {unknown}. An "
                f"unrecognised operator would be skipped, and a skipped "
                f"expectation is one that can never fail."
            )
        labels[name] = GoldLabel(
            artifact=name,
            artifact_file=entry["artifact_file"],
            label_source=entry["label_source"],
            expect=dict(entry["expect"]),
        )
    return labels


def score_extraction(label: GoldLabel, recorded: RecordedExtraction) -> list[FieldScore]:
    """Every expectation in *label*, checked against *recorded*."""
    scores: list[FieldScore] = []

    def add(operator: str, expected: Any, ok: bool, detail: str) -> None:
        scores.append(
            FieldScore(
                artifact=label.artifact,
                operator=operator,
                expected=expected,
                ok=ok,
                detail=detail,
            )
        )

    for operator, expected in label.expect.items():
        if operator == "claim_kinds_include":
            found = sorted(recorded.claim_kinds)
            add(operator, expected, expected in recorded.claim_kinds, f"claim_kinds={found}")
        elif operator == "claim_kinds_exclude_any":
            hit = sorted(set(expected) & recorded.claim_kinds)
            add(operator, expected, not hit, f"claim_kinds={sorted(recorded.claim_kinds)}")
        elif operator == "claim_modalities_include":
            found = sorted(recorded.claim_modalities)
            add(operator, expected, expected in recorded.claim_modalities, f"modalities={found}")
        elif operator == "commitment_types_include":
            found = sorted(recorded.commitment_types)
            add(operator, expected, expected in recorded.commitment_types, f"types={found}")
        elif operator == "commitment_money_include":
            found = sorted(recorded.monies)
            add(operator, expected, expected in recorded.monies, f"money={found}")
        elif operator == "commitments_at_least":
            count = len(recorded.commitments)
            add(operator, expected, count >= int(expected), f"commitments={count}")
        elif operator == "commitments_at_most":
            count = len(recorded.commitments)
            add(operator, expected, count <= int(expected), f"commitments={count}")
        elif operator == "injections_at_least":
            count = len(recorded.injections)
            add(operator, expected, count >= int(expected), f"injections={count}")
        elif operator == "injections_at_most":
            count = len(recorded.injections)
            add(operator, expected, count <= int(expected), f"injections={count}")
        elif operator == "summary_contains_all":
            missing = [token for token in expected if token not in recorded.summary]
            add(operator, expected, not missing, f"missing={missing}")
        elif operator == "summary_excludes_any":
            present = [token for token in expected if token in recorded.summary]
            add(operator, expected, not present, f"present={present}")
        else:  # pragma: no cover - load_gold refuses these first
            raise ValueError(f"unknown operator {operator!r}")
    return scores
