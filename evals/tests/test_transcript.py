"""Reading a recorded run must preserve its three verdicts, not two."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.runner.transcript import LIVE_RUN_TRANSCRIPT, parse_transcript

pytestmark = pytest.mark.unit


def test_the_recorded_run_exists_and_names_its_three_artifacts() -> None:
    parsed = parse_transcript()
    assert set(parsed) == {
        "harborview-deposit-promise",
        "northline-final-invoice",
        "injected-instruction",
    }


def test_the_deposit_promise_parses_into_its_typed_parts() -> None:
    recorded = parse_transcript()["harborview-deposit-promise"]
    assert recorded.claim_kinds == {"COMMITMENT_CLAIM"}
    assert recorded.claim_modalities == {"PROMISED_FUTURE"}
    assert recorded.commitment_types == {"DEPOSIT_RETURN"}
    assert recorded.monies == {"USD 1800.00"}
    assert recorded.injections == ()


def test_a_thousands_separator_in_the_transcript_does_not_defeat_the_match() -> None:
    """The document writes ``USD 1,800.00``; a gold label writes ``USD 1800.00``.

    A parser that kept the comma would report a miss for a correct extraction,
    which is a harness defect reported as a model defect.
    """
    from evals.runner.transcript import RecordedCommitment

    assert RecordedCommitment("cm_1", "DEPOSIT_RETURN", "USD", "1,800.00").money == "USD 1800.00"


def test_the_injection_artifact_parses_both_injections_and_no_commitment() -> None:
    recorded = parse_transcript()["injected-instruction"]
    assert len(recorded.injections) == 2
    assert {injection.classification for injection in recorded.injections} == {
        "INSTRUCTION_OVERRIDE",
        "TOOL_CALL_IMITATION",
    }
    assert recorded.commitments == ()


def test_a_cannot_run_in_the_transcript_is_preserved_as_its_own_verdict(
    tmp_path: Path,
) -> None:
    """The transcript's own footer says PASS / FAIL / CANNOT RUN are three
    outcomes.

    The first version of this test asserted only that every parsed verdict was
    a member of that set, and read the live transcript to do it. All three of
    that transcript's `ExtractionResult` lines happen to say `PASS`, so
    deleting `CANNOT RUN` from the parser's own pattern left the test green --
    the assertion was about a set the data never exercised. The counterfactual
    is what found it.

    So the third outcome is fed in explicitly. A parser that dropped it would
    either omit the block entirely or record it as something else, and both are
    failures a set-membership check cannot see.
    """
    text = LIVE_RUN_TRANSCRIPT.read_text(encoding="utf-8")
    assert "CANNOT RUN" in text, "the transcript under test carries no CANNOT RUN to preserve"

    stub = tmp_path / "run.txt"
    stub.write_text(
        "\n".join(
            [
                "ARTIFACT could-not-extract",
                "   CANNOT RUN  ExtractionResult                   schema=1.0 claims=0",
                "   summary: the extractor was never reached",
                "",
                "ARTIFACT extracted-fine",
                "   PASS        ExtractionResult                   schema=1.0 claims=1",
                "   claim: cl_1 COUNTERPARTY_CLAIM invoice.amount_due modality=ASSERTED_PRESENT",
            ]
        ),
        encoding="utf-8",
    )
    parsed = parse_transcript(stub)
    assert set(parsed) == {"could-not-extract", "extracted-fine"}, (
        "a CANNOT RUN block was dropped by the parser. Dropping it and failing "
        "it are different wrong answers and neither is what was recorded."
    )
    assert parsed["could-not-extract"].verdict == "CANNOT RUN", (
        f"a recorded CANNOT RUN was re-read as "
        f"{parsed['could-not-extract'].verdict!r}. Only FAIL changes canon."
    )
    assert parsed["extracted-fine"].verdict == "PASS"


def test_an_artifact_whose_block_reached_no_extraction_is_omitted_not_emptied(
    tmp_path: Path,
) -> None:
    """An empty extraction scores as a total miss. 'The run never reached this
    stage' is a different claim and must not be scored at all."""
    stub = tmp_path / "run.txt"
    stub.write_text(
        "\n".join(
            [
                "ARTIFACT halted-before-extraction",
                "-- PB-A5  the graph walks",
                "   FAIL        graph walk raised",
                "",
                "ARTIFACT reached-extraction",
                "   PASS        ExtractionResult                   schema=1.0 claims=1",
                "   claim: cl_1 COUNTERPARTY_CLAIM invoice.amount_due modality=ASSERTED_PRESENT",
            ]
        ),
        encoding="utf-8",
    )
    parsed = parse_transcript(stub)
    assert set(parsed) == {"reached-extraction"}, (
        "an artifact that never reached the extraction stage was recorded as an "
        "empty extraction, which scores as a total miss."
    )


def test_a_missing_transcript_raises_rather_than_returning_an_empty_mapping(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="CANNOT RUN"):
        parse_transcript(tmp_path / "absent.txt")
