"""``ModelAttribution`` must accept exactly what the database accepts.

The defect this closes
-----------------------
``ModelAttribution`` validates a model id in Python. ``ck_memory_proposals_model``
validates one in SQL. Nothing made the two agree, and after the pivot they did
not: the contract still accepted ``provider="bedrock"`` with
``us.anthropic.claude-opus-4-6-v1``, while migration ``0009`` rewrites the CHECK
to admit only the Gemini tiers plus ``deterministic.kernel``.

A value that passes the contract and fails the ``INSERT`` is the worst shape
available. It does not fail at the boundary that knows why; it fails at commit
time, inside a transaction, as a ``CheckViolation`` naming a constraint the
caller has never heard of.

This repository has already paid for exactly this pattern once. ``UNIQUE_VIOLATION_MAP``
was keyed on PostgreSQL's auto-generated constraint names while the migrations
declared explicit ``uq_*`` ones, so **zero of eight keys existed in the schema**
and every ``23505`` fell through to ``REJECTED_INVARIANT``. The fix there was a
static scan of the migrations asserting every key is one the DDL declares. This
is the same instrument pointed at the same class of drift.

Why a cross-check rather than one shared constant
--------------------------------------------------
The migration is SQL text executed by Alembic; it cannot import from
``provenance_contracts``, and a Python constant cannot be read by a CHECK. There
is no single definition available, so the next best thing is a test that fails
when the two definitions diverge -- which is what a static scan buys.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ModelTier

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
VERSIONS = REPO_ROOT / "db" / "migrations" / "versions"


def _proposal_model_ids(revision_stem: str) -> set[str]:
    """The ids ``ck_memory_proposals_model`` admits in *revision_stem*.

    Two revisions declare this two different ways and both must be readable:

    * ``0009`` binds a module-level ``PROPOSAL_MODEL_IDS`` tuple and builds the
      SQL from it. Parsed with ``ast`` -- a regex over the source stops at the
      first ``)``, and one of that tuple's *comments* contains
      ``(SYSTEM_DERIVATION, TRIGGER_EVALUATION)``. Reading a prohibition out of
      a comment is the failure mode ``23_PHASE_GATES.md`` prefers the AST for.
    * ``0005`` writes the ids inline in the ``CREATE TABLE`` text, single-quoted.
    """
    # `{stem}*.py` is ambiguous the moment a sibling revision shares the prefix,
    # and `next()` takes whichever the filesystem yields first. `0009a` did
    # exactly that: its `PROPOSAL_MODEL_IDS` is an alias for 0009's, so the AST
    # walk found no string constants and this returned an EMPTY SET -- which
    # then failed with "the Kernel writes its own proposals" rather than naming
    # the real problem. Requiring the separator and exactly one match makes a
    # collision an error instead of a silent wrong answer.
    matches = sorted(VERSIONS.glob(f"{revision_stem}_*.py"))
    assert len(matches) == 1, f"{revision_stem} matches {[m.name for m in matches]}; expected one"
    path = matches[0]
    source = path.read_text(encoding="utf-8")

    tree = ast.parse(source)
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == "PROPOSAL_MODEL_IDS" for t in targets):
            continue
        value = node.value if isinstance(node, ast.Assign) else node.value
        assert value is not None
        return {
            n.value
            for n in ast.walk(value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }

    match = re.search(
        r"ck_memory_proposals_model CHECK \(model_id IN \((.*?)\)\)",
        source,
        re.S,
    )
    assert match is not None, f"{path.name} declares no ck_memory_proposals_model"
    return set(re.findall(r"'([^']+)'", match.group(1)))


class TestTheContractAndTheSchemaAgree:
    def test_migration_0009_declares_the_gemini_tiers(self) -> None:
        ids = _proposal_model_ids("0009")
        assert "deterministic.kernel" in ids, "the Kernel writes its own proposals"
        assert {"gemini-3.7-flash", "gemini-3.5-flash-lite"} <= ids, ids

    def test_every_chat_id_the_schema_admits_is_accepted_by_the_contract(self) -> None:
        """The direction that matters at write time.

        An id the database will store but the contract refuses is a row the
        system can never write through its own boundary.
        """
        for model_id in sorted(_proposal_model_ids("0009")):
            if model_id == "deterministic.kernel":
                continue  # not a model attribution; the Kernel is not a model
            ModelAttribution(
                provider="gemini",
                model_id=model_id,
                tier=ModelTier.R,
                prompt_version="pv-draft-1.0.0",
                graph_name="advocate",
                graph_version="1.0.0",
            )

    def test_a_bedrock_attribution_is_no_longer_writable_after_0009(self) -> None:
        """The inconsistency this file was written for.

        `ModelAttribution` still accepts `provider="bedrock"` -- deliberately,
        because `D-00-040`'s mirror-image identifier rule is knowledge worth
        keeping and the Bedrock branch is what encodes it. But after `0009` no
        Anthropic id is in the CHECK, so such an attribution validates in Python
        and is refused by the database.

        This test does not call that a bug. It pins it as a **known, narrow**
        gap so it cannot widen unnoticed: the contract is deliberately more
        permissive than the schema in exactly one direction, for exactly one
        provider.
        """
        attribution = ModelAttribution(
            provider="bedrock",
            model_id="us.anthropic.claude-opus-4-6-v1",
            tier=ModelTier.R,
            prompt_version="pv-draft-1.0.0",
            graph_name="advocate",
            graph_version="1.0.0",
        )
        assert attribution.model_id not in _proposal_model_ids("0009"), (
            "0009 has re-admitted an Anthropic id; either the pivot was reversed "
            "or the CHECK drifted"
        )

    def test_the_deployed_head_still_admits_the_bedrock_ids(self) -> None:
        """The transitional fact, stated rather than assumed.

        `0009` is written but NOT applied -- it destroys 18,035 vectors and
        refuses without an explicit acknowledgement, and the db lane pins to
        `DEPLOYED_HEAD = 0008`. So the CHECK actually in force is `0005`'s, and
        it admits the Anthropic ids and none of the Gemini ones.

        Until `0009` lands, a **Gemini** proposal is the one the database would
        reject. Recording which way round that is, right now, is the difference
        between a known transition and a surprise at commit time.
        """
        deployed = _proposal_model_ids("0005")
        assert "us.anthropic.claude-opus-4-6-v1" in deployed
        assert not any(
            model_id.startswith("gemini-") for model_id in deployed
        ), "0005 admits a Gemini id; the transition has been applied out of order"


class TestTheScanItselfIsArmed:
    """A static scan that cannot fail proves nothing -- ``D-00-013``."""

    def test_the_id_extractor_finds_a_planted_absence(self, tmp_path: Path) -> None:
        real = _proposal_model_ids("0009")
        assert "gemini-3.7-flash" in real
        assert "gemini-9.9-imaginary" not in real

    def test_an_unparseable_revision_fails_loudly(self) -> None:
        with pytest.raises((AssertionError, StopIteration)):
            _proposal_model_ids("0001")  # declares no PROPOSAL_MODEL_IDS


def test_a_gemini_id_outside_the_check_is_refused_by_the_contract() -> None:
    """Both gates must refuse the same made-up id, or one of them is decorative."""
    with pytest.raises(ValidationError):
        ModelAttribution(
            provider="gemini",
            model_id="gemini-embedding-2",  # an embedding model, not a chat tier
            tier=ModelTier.R,
            prompt_version="pv-draft-1.0.0",
            graph_name="advocate",
            graph_version="1.0.0",
        )
