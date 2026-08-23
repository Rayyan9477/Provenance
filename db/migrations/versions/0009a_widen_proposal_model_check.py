"""Widen ``ck_memory_proposals_model`` to admit the Gemini tiers.

Why this is its own revision
-----------------------------
``0009_gemini_embedding_plane`` already widens this CHECK -- and it also
**destroys the vector corpus**: it drops ``evidence_items.embedding`` and
rebuilds it at 1536 dimensions, leaving all 18,035 Titan vectors NULL and the
ANN index to be rebuilt over roughly an hour. Those two changes have nothing to
do with each other and were bundled because they arrived in the same pivot.

The consequence was that a working agent could not write a proposal. The
agent-to-Kernel path is bound (``internal.submit_proposal``), it builds a valid
typed ``MemoryProposal``, and the INSERT is refused by the applied schema:

    CheckViolation: ck_memory_proposals_model
    model_id = 'gemini-3.5-flash-lite'

``0005`` admits three Bedrock-era Anthropic ids plus ``deterministic.kernel``,
and every one of those was proved un-invocable when the Bedrock canon was
re-probed. So the only id the database accepted for an agent proposal was an id
no agent can call, and the only id it accepted at all was
``deterministic.kernel`` -- which an agent must not claim, because that field is
what ``CANONICAL_DECISIONS.md`` -> *Disclosure* makes the model attribution
checkable against. Writing it would be a false attribution recorded in the row
that exists to prevent false attribution.

Splitting the CHECK out is safe in a way the rest of ``0009`` is not:

* **Widening a CHECK cannot invalidate an existing row.** The new set is a
  strict superset on the ids that matter -- it adds the three Gemini tiers and
  keeps ``deterministic.kernel``, under which all 11 live rows were written.
  ``0009`` retires the Anthropic ids as part of the same statement; that is a
  narrowing, and it is left there rather than performed here.
* Nothing is dropped, rebuilt, re-embedded or re-indexed.

Chain
-----
Inserted between ``0008`` and ``0009`` rather than after it, so that this can be
applied while ``0009`` stays deliberately unapplied. ``0009``'s
``down_revision`` moves to this revision, which keeps a single linear head:

    0008_events_infrastructure -> 0009a_widen_proposal_model_check
                               -> 0009_gemini_embedding_plane

The id list is **imported from 0009**, not restated. Two copies of one admitted
set is how ``ck_memory_proposals_model`` and the runtime constant came to
disagree in the first place, and a second copy here would put the same trap one
layer down.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op


def _load_0009() -> object:
    """Load ``0009`` by path.

    Its filename starts with a digit, so it is not importable by name. Loading
    it is still preferable to copying its ``PROPOSAL_MODEL_IDS``: two copies of
    one admitted set is exactly how the runtime's graph-name constant and
    ``ck_agent_runs_graph`` came to disagree, undetected, until the first row
    was written.
    """
    path = Path(__file__).with_name("0009_gemini_embedding_plane.py")
    spec = importlib.util.spec_from_file_location("_pv_0009", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0009 = _load_0009()
PROPOSAL_MODEL_IDS: tuple[str, ...] = _0009.PROPOSAL_MODEL_IDS  # type: ignore[attr-defined]
quoted = _0009._quoted  # type: ignore[attr-defined]

revision = "0009a_widen_proposal_model_check"
down_revision = "0008_events_infrastructure"
branch_labels = None
depends_on = None

#: What ``0005`` installed. Named so the downgrade restores exactly it rather
#: than an approximation, and so a reader can see what is being widened FROM.
PRIOR_PROPOSAL_MODEL_IDS: tuple[str, ...] = (
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-opus-4-6-v1",
    "us.anthropic.claude-sonnet-4-6",
    "deterministic.kernel",
)

#: The widened set: the prior ids AND the Gemini tiers. A strict superset, so
#: no existing row can be invalidated. ``0009`` performs the *narrowing* that
#: retires the Anthropic ids; doing it here would make this revision
#: irreversible against rows this build has not inspected.
WIDENED_MODEL_IDS: tuple[str, ...] = tuple(
    dict.fromkeys(PRIOR_PROPOSAL_MODEL_IDS + PROPOSAL_MODEL_IDS)
)

DROP_DDL = "ALTER TABLE memory_proposals DROP CONSTRAINT IF EXISTS ck_memory_proposals_model"

ADD_WIDENED_DDL = (
    "ALTER TABLE memory_proposals ADD CONSTRAINT ck_memory_proposals_model CHECK ("
    f" model_id IN ({quoted(WIDENED_MODEL_IDS)})"
    ")"
)

ADD_PRIOR_DDL = (
    "ALTER TABLE memory_proposals ADD CONSTRAINT ck_memory_proposals_model CHECK ("
    f" model_id IN ({quoted(PRIOR_PROPOSAL_MODEL_IDS)})"
    ")"
)


def upgrade() -> None:
    op.execute(DROP_DDL)
    op.execute(ADD_WIDENED_DDL)


def downgrade() -> None:
    """Restore ``0005``'s set exactly. **For local iteration only.**

    From Phase 13 onward the schema rolls forward and the code rolls back --
    every revision in this tree says so in its own docstring, because nobody
    should discover the rule during an incident.

    This can fail, and failing is correct: if a proposal has been written under
    a Gemini id, narrowing the CHECK back would leave a row the constraint
    forbids. CockroachDB validates on ADD, so the downgrade refuses rather than
    installing a constraint the table already violates. The operator then has a
    real decision to make about those rows instead of a silently invalid table.
    """
    op.execute(DROP_DDL)
    op.execute(ADD_PRIOR_DDL)
