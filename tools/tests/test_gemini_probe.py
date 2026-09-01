"""Tests for ``ops/probes/gemini_probe.py``.

``D-00-013`` closed a defect whose shape was: *three guard mechanisms had no
test that fails when the mechanism is deleted*. The guard this file protects is
the ``PASS`` / ``FAIL`` / ``CANNOT RUN`` distinction, and it is worth protecting
because collapsing it is exactly what ``D-00-005`` did -- a probe that could not
connect reported that a capability had *failed*, which would have forced a
working capability into a permanent fallback.

Every test here is hermetic. The probe makes network calls only after a key is
present, and no test in this file supplies one.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "ops" / "probes" / "gemini_probe.py"
CANON = REPO_ROOT / "docs" / "CANONICAL_DECISIONS.md"

sys.path.insert(0, str(REPO_ROOT / "ops" / "probes"))
import gemini_probe as probe  # noqa: E402

pytestmark = pytest.mark.unit


class FinishReason:
    """The two ``finish_reason`` values the verdict logic branches on.

    ``chat_verdict`` takes the reason as a plain string rather than a
    ``types.FinishReason``, so the verdict -- the part that decides what goes
    into canon -- is testable with no SDK, no key and no network. The probe
    passes ``finish.name``.
    """

    STOP = "STOP"
    MAX_TOKENS = "MAX_TOKENS"


def test_the_probe_file_exists_at_the_path_the_canon_names() -> None:
    """The canon promises this file by name; a rename would strand the obligation."""
    assert PROBE.is_file()
    assert "ops/probes/gemini_probe.py" in CANON.read_text(encoding="utf-8")


class TestTheThreeValuedVerdict:
    """``CANNOT RUN`` is not ``FAIL``. They lead to opposite decisions."""

    def test_a_cannot_run_exits_2_not_1(self) -> None:
        assert probe.exit_code_for(["CANNOT RUN"]) == 2

    def test_a_fail_exits_1(self) -> None:
        assert probe.exit_code_for(["FAIL"]) == 1

    def test_all_pass_exits_0(self) -> None:
        assert probe.exit_code_for(["PASS", "PASS"]) == 0

    def test_a_fail_outranks_a_cannot_run(self) -> None:
        """One real failure is worse news than one unanswered question."""
        assert probe.exit_code_for(["CANNOT RUN", "FAIL", "PASS"]) == 1

    def test_the_three_outcomes_are_not_collapsed(self) -> None:
        """The counterfactual: if any two mapped to one code, this fails."""
        codes = {
            probe.exit_code_for(["PASS"]),
            probe.exit_code_for(["FAIL"]),
            probe.exit_code_for(["CANNOT RUN"]),
        }
        assert len(codes) == 3, f"outcomes collapsed onto {codes}"


class TestRunningWithoutAKey:
    """The state we are actually in today, and it must not read as a failure."""

    def test_no_key_exits_2_and_says_it_is_not_a_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "gemini-probe.txt"
        env = {"PATH": "", "SYSTEMROOT": "C:\\Windows"}
        completed = subprocess.run(
            [sys.executable, str(PROBE), "--out", str(out)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**_clean_env(), **env},
        )
        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "CANNOT RUN" in completed.stdout
        assert "NOT a failure" in completed.stdout


class TestCandidateIdsAgreeWithCanon:
    """A probe that tests ids the canon does not name proves nothing about canon."""

    @pytest.mark.parametrize(
        "model_id",
        ["gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash"],
    )
    def test_every_canon_chat_id_is_probed(self, model_id: str) -> None:
        probed = {mid for mid, _ in probe.CHAT_CANDIDATES}
        assert model_id in probed, f"{model_id} is canon but unprobed"

    def test_both_contested_embedding_spellings_are_probed(self) -> None:
        """The models page and the embeddings page disagree; probe both."""
        assert "gemini-embedding-2" in probe.EMBEDDING_CANDIDATES
        assert "gemini-embedding-2-preview" in probe.EMBEDDING_CANDIDATES

    def test_no_pro_model_is_probed_as_a_tier(self) -> None:
        """``gemini-3.1-pro-preview`` is version 3.1 -- below the 3.5 floor.

        Probing it as a tier candidate would invite adopting a model that fails
        this build's own model floor.
        """
        probed = {mid for mid, _ in probe.CHAT_CANDIDATES}
        assert not any("pro" in mid for mid in probed), probed

    def test_the_target_width_matches_canon(self) -> None:
        assert probe.TARGET_DIMENSIONS == 1536


class TestTheTranscriptIsScrubbed:
    """A live credential once leaked into committed evidence. Positive control."""

    def test_a_credential_shaped_line_is_redacted(self, tmp_path: Path) -> None:
        t = probe.Transcript(tmp_path / "t.txt")
        t.write("postgresql://pv_migrator:sup3rs3cr3t@host.example:26257/provenance")
        t.flush()
        written = (tmp_path / "t.txt").read_text(encoding="utf-8")
        assert "sup3rs3cr3t" not in written, written

    def test_the_scrubber_is_actually_wired_not_merely_imported(self, tmp_path: Path) -> None:
        """Counterfactual: if ``write`` stopped calling the scrubber, this fails.

        Asserting only that the import exists would pass with the call removed,
        which is the vacuity shape ``D-00-013`` was filed for.
        """
        t = probe.Transcript(tmp_path / "t.txt")
        t.write("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY")
        t.flush()
        written = (tmp_path / "t.txt").read_text(encoding="utf-8")
        assert "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY" not in written, written


class TestTheProbeRefusesToTreatListingAsProof:
    """The whole reason this file exists.

    ``list-foundation-models`` returned ids that were not invocable, and the
    previous build froze every one of them from a listing. The probe must say
    so in the transcript a reader will actually see.
    """

    def test_the_listing_probe_is_labelled_not_proof(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        assert re.search(r"REFERENCE ONLY, NOT PROOF", source)

    def test_invocation_is_described_as_the_evidence(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        assert "this is the evidence" in source


def _clean_env() -> dict[str, str]:
    """An environment with no Google key, however the host happens to be set up."""
    import os

    env = dict(os.environ)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env["PV_PROBE_NO_DOTENV"] = "1"
    return env


class TestTheChatVerdictIsNotVacuous:
    """The defect: ``probe_g2_chat`` returned PASS whenever no exception was raised.

    The 2026-08-24 run recorded four PASSes for PB-G2 and three of the four
    replies were unusable -- ``gemini-3.7-flash`` answered ``':'`` and both
    ``gemini-3.6-flash`` and ``gemini-3.5-flash`` answered ``''`` -- because
    every Flash model above the Lite tier thinks by default and
    ``max_output_tokens=16`` was consumed entirely by thinking. Measured:
    ``thoughts_token_count`` 12, ``candidates_token_count`` None,
    ``finish_reason`` MAX_TOKENS. The visible answer never got a token.

    A verdict that cannot distinguish ``'ok'`` from ``''`` is not a verdict, and
    it is the same vacuity class the sabotage matrix exists to catch -- with the
    aggravation that this one was reported as evidence in a probe transcript
    whose entire purpose is to be believed.
    """

    def test_a_real_reply_passes(self) -> None:
        outcome, _ = probe.chat_verdict("ok", FinishReason.STOP)
        assert outcome == "PASS"

    def test_an_empty_reply_is_never_a_pass(self) -> None:
        """The regression, pinned. This is what four PASSes were built on."""
        outcome, _ = probe.chat_verdict("", FinishReason.STOP)
        assert outcome != "PASS", "an empty reply was recorded as evidence the id works"

    def test_an_empty_reply_cut_off_at_max_tokens_is_cannot_run_not_fail(self) -> None:
        """D-00-005, in the place it was born.

        An empty body with ``finish_reason=MAX_TOKENS`` says the PROBE's budget
        was too small, not that the model id is wrong. Calling that FAIL would
        licence a canon correction -- demoting a working Tier R id to a fallback
        -- on the strength of the probe's own misconfiguration.
        """
        outcome, detail = probe.chat_verdict("", FinishReason.MAX_TOKENS)
        assert outcome == "CANNOT RUN", f"got {outcome}: a budget limit is not a model failure"
        assert "budget" in detail.lower() or "max_output_tokens" in detail

    def test_an_empty_reply_that_stopped_normally_is_a_fail(self) -> None:
        """Not cut off, and still said nothing: that is the model, not the probe."""
        outcome, _ = probe.chat_verdict("", FinishReason.STOP)
        assert outcome == "FAIL"

    def test_the_chat_budget_leaves_room_for_thinking(self) -> None:
        """16 tokens is below the measured floor and produced three empty replies.

        Measured 2026-08-24 against the live API: 133 thinking tokens for
        ``gemini-3.7-flash`` to answer the single word ``ok``, 128 for
        ``gemini-3.6-flash``, 84 for ``gemini-3.5-flash``. A budget at or below
        that is not a probe of the model, it is a probe of the budget.
        """
        assert probe.CHAT_MAX_OUTPUT_TOKENS >= 512, (
            f"{probe.CHAT_MAX_OUTPUT_TOKENS} shares one allowance with thinking; "
            "the three non-Lite Flash tiers spend 84-133 tokens before the first "
            "visible one"
        )


class TestTheMultimodalProbeUsesAnImageTheAPIAccepts:
    """The defect: PB-G6 probed with a 1x1 transparent PNG and recorded FAIL.

    The API returned ``400 INVALID_ARGUMENT: Unable to process input image``.
    Re-probed the same minute with an 8x8 solid red PNG -- **the same 75 bytes**
    -- the call succeeds and the model replies ``'Solid red square.'``.

    So the recorded FAIL was a property of the test fixture, not of the
    capability, and acting on it would have kept an external OCR dependency and
    given up the native multimodal read path on the evidence of one transparent
    pixel. The probe's own docstring asserted a 1x1 PNG was "enough
    to establish the request shape is accepted"; that sentence was a prediction,
    and it was wrong.
    """

    def test_the_probe_image_is_not_the_degenerate_one_by_one(self) -> None:
        width, height = probe.probe_image_size()
        assert (width, height) != (1, 1), "the 1x1 PNG is the fixture the API rejects"

    def test_the_probe_image_is_large_enough_to_carry_content(self) -> None:
        width, height = probe.probe_image_size()
        assert width >= 8 and height >= 8, f"{width}x{height} is below the accepted size"

    def test_the_probe_image_is_a_valid_png(self) -> None:
        assert probe.PROBE_IMAGE.startswith(b"\x89PNG\r\n\x1a\n")
