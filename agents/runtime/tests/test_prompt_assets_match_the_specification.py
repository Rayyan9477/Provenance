"""The on-disk prompt assets are the specification's literal blocks.

Why this test rather than the manifest
---------------------------------------
``MANIFEST.json`` proves the bytes on disk are the bytes that were reviewed.
``render.py``'s own docstring is explicit that it "does **not** prove they
match ``14_PROMPTS.md`` today -- that is a separate check against a document
that is itself editable, and pretending one hash does both jobs would be the
kind of quiet conflation this repository spends its effort preventing."

This is that separate check. It reads the ```text fenced blocks out of
``docs/specs/14_PROMPTS.md`` and compares them byte for byte with the assets,
so an asset that drifts from the specification -- or a specification edit that
never reached the asset -- fails here rather than changing a live model's
behaviour under an unchanged ``prompt_version``.

Why it also enumerates the routed versions
-------------------------------------------
``ROUTES`` names a ``prompt_version`` per model node. A routed version with no
asset on disk raises ``PromptAssetError`` at the moment the node renders --
which is inside a paid model call in the ingestion graph and, for
``draft_action``, at the exact moment the Judge Mode counterfactual runs. The
absence is checkable statically, so it is checked statically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.runtime.model_router.router import ROUTES
from agents.runtime.prompts.render import ASSET_NAMES, PROMPTS_ROOT, load_manifest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = _REPO_ROOT / "docs" / "specs" / "14_PROMPTS.md"

#: ``<version>/<asset>`` -> the heading in ``14_PROMPTS.md`` whose first
#: ```text block holds it. Written out rather than discovered: a discovery rule
#: that matched nothing would make every assertion below vacuous.
SPEC_SECTIONS: dict[str, str] = {
    "pv-extract-1.1.0/system_policy.txt": "### 3.1 SYSTEM POLICY (literal)",
    "pv-extract-1.1.0/task.txt": "### 3.2 TASK (literal)",
    "pv-resolve-1.1.0/system_policy.txt": "### 4.1 SYSTEM POLICY (literal)",
    "pv-resolve-1.1.0/task.txt": "### 4.2 TASK (literal)",
    "pv-attention-1.1.0/system_policy.txt": "### 5.1 SYSTEM POLICY (literal)",
    "pv-attention-1.1.0/task.txt": "### 5.2 TASK (literal)",
    "pv-draft-1.0.0/system_policy.txt": "### 6.1 SYSTEM POLICY (literal)",
    "pv-draft-1.0.0/task.txt": "### 6.2 TASK (literal)",
}


def _literal_block(heading: str) -> str:
    """The first ```text fenced block after *heading*."""
    text = _SPEC.read_text(encoding="utf-8")
    start = text.index(heading)
    opened = text.index("```text\n", start) + len("```text\n")
    closed = text.index("\n```", opened)
    return text[opened : closed + 1]


def test_the_spec_reader_actually_finds_text() -> None:
    """A reader that returned '' would make every comparison below vacuous."""
    for heading in SPEC_SECTIONS.values():
        block = _literal_block(heading)
        assert len(block) > 500, f"{heading} yielded {len(block)} characters"


def test_every_routed_prompt_version_has_assets_on_disk() -> None:
    """A routed version with no asset raises inside a paid model call."""
    missing = {
        f"{spec.prompt_version}/{asset}"
        for spec in ROUTES.values()
        for asset in ASSET_NAMES
        if not (PROMPTS_ROOT / spec.prompt_version / asset).is_file()
    }
    assert not missing, (
        f"these prompt versions are routed but have no asset on disk: {sorted(missing)}. "
        "AssetPromptRenderer.render_system raises PromptAssetError for them, and for "
        "draft_action that is the moment the Judge Mode counterfactual runs."
    )


@pytest.mark.parametrize("relative", sorted(SPEC_SECTIONS))
def test_each_asset_is_byte_identical_to_the_specification(relative: str) -> None:
    path = PROMPTS_ROOT / relative
    assert path.is_file(), f"{relative} does not exist"
    assert path.read_bytes() == _literal_block(SPEC_SECTIONS[relative]).encode("utf-8"), (
        f"{relative} differs from {SPEC_SECTIONS[relative]} in 14_PROMPTS.md. "
        "A prompt whose text changed under an unchanged prompt_version makes every "
        "evaluation number recorded against that version a claim about a different prompt."
    )


def test_the_manifest_covers_every_asset_the_spec_names() -> None:
    recorded = set(load_manifest())
    assert set(SPEC_SECTIONS) <= recorded, (
        f"MANIFEST.json does not hash {sorted(set(SPEC_SECTIONS) - recorded)}; an asset "
        "outside the manifest can be edited without load_manifest() noticing."
    )
