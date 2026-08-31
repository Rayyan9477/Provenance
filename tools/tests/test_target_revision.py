"""``TARGET_REVISION`` is the revision this build runs against, and must stay so.

Why the Makefile does not say ``head``
--------------------------------------
The chain head is ``0009_gemini_embedding_plane``. It widens
``evidence_items.embedding`` to ``VECTOR(1536)`` for the Gemini embedding
space, and it is deliberately unapplied: its ``upgrade()`` refuses without
``PV_EMBEDDING_REWRITE_ACK``, ``ACTIVE_EMBEDDING_PROFILE`` resolves to
``titan-v1``, and the 18,035 vectors in the ground are Titan at
``VECTOR(1024)``.

So ``alembic upgrade head`` aborts on any database this code should run
against. Two targets called it anyway -- ``make demo-reset``, which drops and
recreates the database first and therefore left it empty and unmigrated, and
G2.1's from-zero round-trip gate, whose only recorded run failed.

Naming a revision instead of ``head`` fixes that and introduces a new way to be
wrong: the name can fall behind the chain silently. These tests are the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _ROOT / "Makefile"
_VERSIONS = _ROOT / "db" / "migrations" / "versions"

_REVISION = re.compile(r"""^revision(?::[^=]+)?\s*=\s*["']([^"']+)["']""", re.M)
_DOWN = re.compile(r"""^down_revision(?::[^=]+)?\s*=\s*["']([^"']+)["']""", re.M)


def _target() -> str:
    match = re.search(
        r"^TARGET_REVISION\s*:?=\s*(\S+)", _MAKEFILE.read_text(encoding="utf-8"), re.M
    )
    assert match, "the Makefile no longer defines TARGET_REVISION"
    return match.group(1)


def _chain() -> dict[str, str | None]:
    """revision -> down_revision, for every migration on disk."""
    chain: dict[str, str | None] = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = _REVISION.search(text)
        if revision is None:
            continue
        parent = _DOWN.search(text)
        chain[revision.group(1)] = parent.group(1) if parent else None
    return chain


def test_the_target_revision_exists_in_the_chain() -> None:
    target = _target()
    chain = _chain()
    assert target in chain, (
        f"TARGET_REVISION is {target!r} but no migration declares that revision; "
        f"the chain holds {sorted(chain)}"
    )


def test_no_makefile_target_migrates_to_head() -> None:
    """`head` is the Gemini revision, which refuses to run. Nothing may ask for it."""
    # Comment lines are excluded: the rule above explains itself by quoting the
    # command it forbids, and a guard that cannot tell a recipe from a note
    # about a recipe would fire on the change that introduced the rule.
    offenders = [
        line.strip()
        for line in _MAKEFILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
        and "alembic" in line
        and re.search(r"upgrade\s+head\b", line)
    ]
    assert not offenders, (
        "these Makefile lines migrate to head, which is "
        "0009_gemini_embedding_plane and aborts without PV_EMBEDDING_REWRITE_ACK: "
        f"{offenders}"
    )


def test_the_target_is_the_last_revision_before_the_embedding_rewrite() -> None:
    """The build should run against everything except the Gemini widening.

    If a migration is added after the target and before the rewrite, this fails
    -- which is correct: the new revision is one this build should be applying,
    and TARGET_REVISION has to be moved deliberately rather than drift.
    """
    chain = _chain()
    target = _target()
    rewrite = "0009_gemini_embedding_plane"
    if rewrite not in chain:
        pytest.skip("the embedding-rewrite revision is not in this tree")

    assert chain[rewrite] == target, (
        f"{rewrite}'s parent is {chain[rewrite]!r}, but TARGET_REVISION is "
        f"{target!r}. A revision was added without moving the target, so "
        "`make demo-reset` and G2.1 would stop short of it."
    )


def test_the_active_embedding_profile_agrees_with_stopping_short() -> None:
    """Stopping before the rewrite is only right while the corpus is Titan.

    If the active profile ever becomes a 1536-wide one, the target must move to
    the rewrite in the same change -- otherwise the code would query a
    VECTOR(1536) space against a VECTOR(1024) column.
    """
    from services.control_plane.app.retrieval.config import ACTIVE_EMBEDDING_PROFILE

    if ACTIVE_EMBEDDING_PROFILE.column_width == 1024:
        assert _target() != "0009_gemini_embedding_plane", (
            "the corpus is 1024-wide; migrating to the rewrite would widen the "
            "column out from under it"
        )
    else:
        assert _target() == "0009_gemini_embedding_plane", (
            f"the active profile is {ACTIVE_EMBEDDING_PROFILE.column_width}-wide, "
            "so TARGET_REVISION must include the embedding rewrite"
        )
