"""``.env.example`` must stay true, and must never carry a real value.

Two failure modes, both cheap to prevent and expensive to discover late:

**Drift.** A template that omits a required variable sends a judge into a
startup error the README does not explain. The hackathon requires "Spin-up
Instructions: A step-by-step guide in your README.md", and a guide whose
template is incomplete does not satisfy it.

**Leakage.** This repository becomes public at submission. ``D-00-037`` is
already open about a real SQL username and cluster FQDN being committed; a
credential reaching the template would be worse, because a template is the one
file a reader is invited to copy.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.example"

sys.path.insert(0, str(REPO_ROOT / "packages" / "python" / "provenance_contracts" / "src"))
from provenance_contracts.settings import Settings  # noqa: E402
from tools.scrub import scrub_text  # noqa: E402

pytestmark = pytest.mark.unit


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _mentioned_names() -> set[str]:
    """Every variable name the template mentions, commented or not."""
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{2,})=", _template(), re.M))


def test_the_template_exists() -> None:
    assert TEMPLATE.is_file(), "the README's spin-up guide depends on this file"


class TestItCoversWhatIsRequired:
    def test_every_universally_required_variable_is_present_uncommented(self) -> None:
        """A variable required on all platforms must be live in the template.

        Commented out would leave a judge with a startup failure and a file
        that looks complete.
        """
        live = set(re.findall(r"^([A-Z][A-Z0-9_]{2,})=", _template(), re.M))
        required = {
            field.validation_alias
            for field in Settings.model_fields.values()
            if field.is_required() and isinstance(field.validation_alias, str)
        }
        missing = required - live
        assert not missing, f"required but absent or commented out: {sorted(missing)}"

    @pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "GCS_ARTIFACT_BUCKET", "PV_PLATFORM"])
    def test_every_gcp_required_variable_is_present(self, name: str) -> None:
        """``_platform_requirements`` demands these when PV_PLATFORM=gcp."""
        assert re.search(rf"^{name}=", _template(), re.M), name

    def test_the_aws_block_is_present_but_commented(self) -> None:
        """Retained for the pre-pivot deployment, inert for everyone else.

        Live AWS variables in a Google template would make ``PV_PLATFORM=aws``
        the accidental default configuration.
        """
        assert "COGNITO_USER_POOL_ID" in _template()
        assert not re.search(r"^COGNITO_USER_POOL_ID=", _template(), re.M)

    def test_the_embedding_profile_is_named_and_defaults_to_the_corpus_on_disk(
        self,
    ) -> None:
        """The 18,035 vectors in ``evidence_items`` are Titan 1024-dim.

        Defaulting the template to ``gemini-v2`` before the re-embed has run
        would query a space the corpus was not written in -- which returns
        ordered numbers and no error.
        """
        assert re.search(r"^PV_EMBEDDING_PROFILE=titan-v1", _template(), re.M)


class TestItCarriesNoRealValue:
    def test_every_credential_shaped_line_uses_an_obvious_placeholder(self) -> None:
        """The scrubber cannot help here, and it is worth saying why.

        ``tools/scrub.py`` matches on *shape*, not on value -- that is what
        makes it correct for transcripts. It therefore redacts
        ``postgresql://USER:PASSWORD@HOST`` exactly as readily as a live DSN,
        so "the scrubber finds nothing to redact" is unsatisfiable for a file
        whose job is to show credential shapes.

        The property that actually matters is that every such shape is filled
        with a token no one could mistake for a working value.
        """
        placeholders = ("USER", "PASSWORD", "HOST", "REPLACE", "ACCOUNT", "your-", "REPLACE_WITH")
        offenders = []
        for line in _template().splitlines():
            stripped = line.lstrip("# ").strip()
            if "=" not in stripped:
                continue
            _, _, value = stripped.partition("=")
            if scrub_text(stripped) == stripped:
                continue  # not credential-shaped at all
            if not any(token in value for token in placeholders):
                offenders.append(line)
        assert not offenders, offenders

    def test_no_line_carries_a_high_entropy_value(self) -> None:
        """A real key is dense and unstructured; a placeholder is neither."""
        offenders = []
        for line in _template().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            _, _, raw = line.partition("=")
            value = raw.split("#")[0].strip()
            if len(value) < 24:
                continue
            # Placeholders are words joined by `_`, `-` or `/`. A credential is
            # a long run of mixed-case alphanumerics with no separators.
            if re.fullmatch(r"[A-Za-z0-9+/=]{24,}", value):
                offenders.append(line)
        assert not offenders, offenders

    def test_placeholders_are_obviously_placeholders(self) -> None:
        """``REPLACE_WITH_...`` cannot be mistaken for something that works."""
        assert "REPLACE_WITH_AI_STUDIO_KEY" in _template()
        assert "REPLACE_WITH_BASE64_32_BYTES" in _template()

    def test_it_names_no_real_host(self) -> None:
        """``D-00-037`` is open about a real cluster FQDN already committed."""
        assert "cockroachlabs.cloud" not in _template()
        assert "rayyandb" not in _template()


class TestGitHygiene:
    def test_the_real_env_file_is_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", ".env"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, ".env is NOT gitignored -- it must be"

    def test_the_template_itself_is_not_ignored(self) -> None:
        """A template nobody receives is not a template."""
        result = subprocess.run(
            ["git", "check-ignore", ".env.example"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, ".env.example is gitignored; it must ship"


class TestItExplainsTheProbeObligation:
    """The template must state the probe status, and state it correctly.

    ``D-00-002`` happened because ids were copied without anyone knowing they
    were unverified. The fix for that is not the literal words "PROBE
    REQUIRED" -- which is what this asserted until the probe ran on 2026-08-24
    and made them false. It is that a reader learns, from the template alone,
    which state the ids are in and where the evidence for that claim lives.

    So both states are legal and each has its own obligation: an unprobed
    template must demand the probe, and a probed template must cite the
    transcript. What is illegal is a template that claims neither, or one whose
    claim disagrees with the transcript actually shipped.
    """

    def test_it_always_names_the_probe_that_settles_the_ids(self) -> None:
        assert "gemini_probe.py" in _template()

    def test_its_probe_claim_agrees_with_the_shipped_transcript(self) -> None:
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from agents.runtime.tools.smoke import probe_verdict, read_probe_transcript

        text = _template()
        transcript = read_probe_transcript(REPO_ROOT)

        # The template's own model ids, read from the file rather than restated,
        # so a new tier is covered without anybody remembering to add it here.
        ids = [
            line.split("=", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("GEMINI_") and "MODEL_ID=" in line
        ]
        assert ids, "no GEMINI_*_MODEL_ID lines found; the scan is looking in the wrong place"

        unprobed = [i for i in ids if probe_verdict(i, transcript) != "PASS"]
        if unprobed:
            assert "PROBE REQUIRED" in text, (
                f"{unprobed} have no PASS line in the shipped transcript, so the "
                "template must carry a PROBE REQUIRED warning and does not"
            )
        else:
            assert "PROBED" in text.upper(), (
                "every id has a PASS line but the template does not say so; a reader "
                "cannot tell a verified id from a transcribed one"
            )
            assert "gemini-probe.txt" in text, (
                "the template claims the ids are probed without citing the transcript "
                "that would let a reader check"
            )
