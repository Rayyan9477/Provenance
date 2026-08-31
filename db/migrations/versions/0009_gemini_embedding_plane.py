"""0009 — the Gemini embedding plane. Widens the vector and repoints both model CHECKs.

Revision ID: 0009_gemini_embedding_plane
Revises: 0008_events_infrastructure

The schema half of the pivot from Amazon Bedrock to Google Gemini (``PIVOT.md``).
Three schema facts pinned AWS into this database, and a Gemini model id was
rejected **at the database boundary** until all three moved together:

1. ``evidence_items.embedding`` was ``VECTOR(1024)``  → now ``VECTOR(1536)``.
2. ``ck_evidence_embedding_model`` allowed only ``amazon.titan-embed-text-v2:0``
   → now the Gemini embedding ids.
3. ``ck_memory_proposals_model`` allowed three ``us.anthropic.*`` ids plus
   ``deterministic.kernel`` → now the Gemini tiers plus ``deterministic.kernel``.

READ THIS BEFORE YOU RUN IT: THIS REVISION DESTROYS THE VECTOR CORPUS
======================================================================
``provenance`` holds **18,035** Titan vectors at 1024 dimensions. A 1024-dim
vector is not a truncation of a 1536-dim one and cannot be converted into one —
it is a different model's output in a different space. This revision therefore
leaves **every ``embedding`` NULL**, and it says so rather than implying the
corpus came through. The rows themselves survive: evidence is append-only
(``0002``'s docstring), so the ``evidence_items`` rows, their text, their hashes
and their grounding edges are all untouched. What is gone is the vector and the
vector's provenance.

The whole embedding quartet is dropped, not just the vector
-----------------------------------------------------------
``embedding_model``, ``embedding_version`` and ``embedding_generated_at`` go
with it. Two reasons, and the second is the load-bearing one:

- A vector's provenance without the vector is a lie. Leaving
  ``embedding_model = 'amazon.titan-embed-text-v2:0'`` on a row whose vector no
  longer exists would make ``SELECT embedding_model`` report a model that never
  produced anything still stored here.
- ``ALTER TABLE ... ADD CONSTRAINT ck_evidence_embedding_model`` **validates
  existing rows**. Adding the Gemini-only CHECK while 18,035 rows still carried
  the Titan string fails with ``CheckViolation`` — measured on this cluster —
  and CockroachDB reports the offending row *in full*, including the entire
  1024-float vector, into whatever log is capturing the migration. Dropping the
  columns makes the new CHECK trivially satisfiable and keeps a row dump out of
  the transcript.

Dropping columns is also what makes this revision legal at all: nulling the
corpus with ``UPDATE evidence_items SET embedding = NULL`` would be a **write**,
and DDL section 16 rule 1 forbids mixing DML with schema changes in one
CockroachDB transaction. Drop-and-recreate reaches the same state through DDL
alone.

Why not ``ALTER COLUMN ... SET DATA TYPE VECTOR(1536)``
-------------------------------------------------------
Because this cluster refuses it, two independent ways. Both were probed against
CockroachDB v26.2.5 rather than assumed:

- With the ANN index present::

      unimplemented: ALTER COLUMN TYPE requiring rewrite of on-disk data is
      currently not supported for columns that are part of an index

- With the index dropped but rows present::

      expected 1536 dimensions, not 1024

  raised in ``PostCommitPhase stage 2 of 15`` — i.e. **after** the statement was
  accepted. A migration that fails post-commit has already told the operator it
  succeeded, which is the worst available failure mode.

The in-place form succeeds only against a column that is empty *and* unindexed,
which is precisely the case that never occurs in production. So it is not used
here at all.

Statement order, and why the ANN index is last
-----------------------------------------------
``ops/41_RUNBOOK.md`` section 4.2 is the authority and its two constraints are
external, not preferences:

- **``IMPORT INTO`` is unsupported on a table carrying a vector index.** The
  re-seed that follows this revision bulk-loads ``evidence_items``; it cannot do
  that while ``evidence_embedding_ann_idx`` exists.
- **Large batch inserts into a vector-indexed table degrade badly**, because
  every insert also does ANN partition maintenance.

So the index is created **after** the data lands — which here means: this
revision rebuilds the index while the column is entirely NULL, so its build is
free, and the expensive build belongs to the seed's step 7, after the
re-embedded rows are in.

**The ANN build over the full corpus takes 52-55 minutes on this cluster**,
measured three times (52m56s, 55m12s). The runbook's "one to two minutes" is
wrong by roughly a factor of thirty, and budgeting from that figure is how a
demo reset turns from five minutes into over an hour.

**Never run this concurrently with a seed.** CockroachDB serialises schema
changes on a table: a second ``DROP INDEX`` does not fail and does not run — it
**queues** behind the first ``CREATE`` and fires the instant that build
succeeds. That has already destroyed one complete 55-minute index build here.

The two embedding-id spellings, and how one of them dies
---------------------------------------------------------
``ck_evidence_embedding_model`` admits **both** ``gemini-embedding-2`` and
``gemini-embedding-2-preview``. Google's models page spells it one way and its
embeddings page the other, and there is no API key on this machine yet, so
neither spelling has been invoked.

This is the exact trap the last build fell into. ``CANONICAL_DECISIONS.md`` →
*Bedrock model id canon*: ``list-foundation-models`` returns ids that are **not
invocable**, and every documented-but-unprobed Bedrock id turned out to be
wrong. A CHECK that admits two candidates is honest about what we know; a CHECK
that picks one is a guess wearing a constraint's clothes.

**Delete the losing spelling once ``ops/gemini-probe.txt`` records a live
invocation** — a real embed call returning a vector, not a ``models.list()``
listing. That is a one-line edit to :data:`EMBEDDING_MODEL_IDS` in a follow-up
revision. Until that transcript exists, both stay.

The re-embed this revision assumes
-----------------------------------
After this runs, ``evidence_items`` has 18,035 rows and zero vectors, so
retrieval returns nothing: the canonical query filters
``embedding_version = 'v2'`` and no row carries it yet. The sequence that
finishes the job is ``PIVOT.md`` section 8 item 3 — re-embed all 18,035 texts
with ``gemini-embedding-2`` at 1536 dimensions, write
``embedding_version = 'v2'``, regenerate ``db/seeds/vectors.parquet`` and its
``MANIFEST.json`` hash, then rebuild the ANN index once. Budget two hours.

The acknowledgement guard
--------------------------
:func:`upgrade` **refuses to run** unless ``PV_EMBEDDING_REWRITE_ACK`` is set to
the exact number of embeddings the run will destroy. The number is reported by
the refusal itself, so the second attempt is informed and the first cannot be
accidental. It is required even when that number is zero, because the db test
lane runs ``alembic upgrade head`` and ``head`` is now this revision: without the
guard, a routine test run would quietly carry ``provenance_ci`` to a schema no
code on this branch is ready for, and ``make gate-2`` would fail somewhere far
from the cause.

Downgrade
---------
Implemented, and symmetrically destructive: rolling back restores the 1024-wide
Titan *shape* so a branch still on 0008 can run, but it does not resurrect the
Titan vectors — they were gone the moment the column was dropped. For **local
iteration only**; from Phase 13 onward schema rolls forward and code rolls back
(``quality/23_PHASE_GATES.md`` section 5).
"""

from __future__ import annotations

from os import environ
from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Connection

revision = "0009_gemini_embedding_plane"
# Moved off 0008 so the CHECK widening this revision also performs could be
# applied WITHOUT the destructive half. See 0009a: widening
# ck_memory_proposals_model cannot invalidate a row, while dropping and
# rebuilding evidence_items.embedding destroys 18,035 Titan vectors. They
# were bundled because they arrived in the same pivot, not because they
# belong together. Chain stays linear: 0008 -> 0009a -> 0009.
down_revision = "0009b_kernel_idempotency_grant"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# The canon this revision moves to — https://ai.google.dev/gemini-api/docs/models
# read August 2026. Every id below is DOCUMENTED, none is PROBED. See the
# docstring: on Bedrock that distinction cost days.
# ---------------------------------------------------------------------------

#: NOT admitted, deliberately: ``gemini-embedding-001``. It is a *declared*
#: profile in ``provenance_contracts.embedding_profile`` (``gemini-001-v3``, 1536
#: dims, ``embedding_version = 'v3'``), so ``ModelAttribution`` accepts it and
#: this CHECK does not. That asymmetry is intentional and it is the boundary
#: doing its job: ``CANONICAL_DECISIONS.md`` -> *Gemini model id canon* chose
#: ``gemini-embedding-2`` precisely **because** ``001`` does not auto-normalize
#: truncated widths, and this stack ranks by cosine, where a missed
#: normalization is silent - the distances stay numbers, stay ordered, and stop
#: meaning anything. Writing a ``001`` vector into this column is the failure
#: the canon rejected, so the database refuses it rather than storing it.
#:
#: Do not "fix" that by widening this tuple. This CHECK restates the **canon**,
#: not the profile registry - and it cannot derive itself from the registry in
#: any case, because the registry still declares ``titan-v1`` on purpose (the
#: corpus on disk is uninterpretable without it) and admitting Titan would undo
#: the whole point of this revision. Adopting ``001`` is a decision for
#: CANONICAL_DECISIONS.md plus a follow-up revision, not an edit here.

#: Both documented spellings of the embedding model. The models page says
#: ``gemini-embedding-2-preview``; the embeddings page says
#: ``gemini-embedding-2``. One of these is not invocable and we cannot yet tell
#: which. Remove the loser when ``ops/gemini-probe.txt`` records a live embed
#: call — not a model listing.
EMBEDDING_MODEL_IDS: tuple[str, ...] = (
    "gemini-embedding-2",  # PROBE REQUIRED - embeddings page spelling
    "gemini-embedding-2-preview",  # PROBE REQUIRED - models page spelling
)

#: ``PIVOT.md`` section 5: on Google's recommended list (768 / 1536 / 3072), and
#: half the storage and index-build cost of 3072 with little MRL quality loss.
EMBEDDING_DIMENSIONS = 1536

#: The Gemini embedding space. Bumped from ``v1`` so that no query can ever rank
#: a 1024-dim Titan vector against a 1536-dim Gemini one: the canonical
#: retrieval SQL filters on this column (DDL section 5.5).
EMBEDDING_VERSION = "v2"

#: Reasoning tier, extraction tier, and the Tier R fallback held for throttling.
PROPOSAL_MODEL_IDS: tuple[str, ...] = (
    "gemini-3.7-flash",  # PROBE REQUIRED - Tier R
    "gemini-3.6-flash",  # PROBE REQUIRED - Tier R fallback, capacity not capability
    "gemini-3.5-flash-lite",  # PROBE REQUIRED - Tier E
    # Not a Gemini id and not a mistake: the deterministic Memory Kernel writes
    # its own proposals (SYSTEM_DERIVATION, TRIGGER_EVALUATION) and is the only
    # canonical writer. Dropping it would make the Kernel unable to record its
    # own derivations.
    "deterministic.kernel",
)

# ---------------------------------------------------------------------------
# What is being superseded. Named, not merely deleted, so the downgrade and the
# pre-flight census read from one place.
# ---------------------------------------------------------------------------

SUPERSEDED_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
SUPERSEDED_EMBEDDING_DIMENSIONS = 1024
SUPERSEDED_EMBEDDING_VERSION = "v1"
SUPERSEDED_PROPOSAL_MODEL_IDS: tuple[str, ...] = (
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-opus-4-6-v1",
    "us.anthropic.claude-sonnet-4-6",
    "deterministic.kernel",
)

#: The environment variable that acknowledges the destruction. Its value must be
#: the exact count of embeddings the run will drop.
ACK_ENV_VAR = "PV_EMBEDDING_REWRITE_ACK"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# ---------------------------------------------------------------------------
# Pre-flight reads. Both happen before any schema change, for the same reason
# 0008 reads its catalogue first: CockroachDB is strict about statement kinds
# within one transaction, and a precondition that can only be checked halfway
# through a migration is not a precondition.
# ---------------------------------------------------------------------------

EMBEDDING_CENSUS_SQL = "SELECT count(embedding) FROM evidence_items"

PROPOSAL_CENSUS_SQL = (
    "SELECT count(*) FROM memory_proposals "
    f"WHERE model_id NOT IN ({_quoted(PROPOSAL_MODEL_IDS)})"
)


# ---------------------------------------------------------------------------
# Statements — evidence_items.
#
# Order is load-bearing at both ends: indexes and CHECKs must come down before
# the columns they name, and the ANN index must go back up last of all.
# ---------------------------------------------------------------------------

#: Three indexes name an embedding column. The ANN index indexes ``embedding``
#: itself; the other two carry it in a **partial-index predicate**
#: (``WHERE embedding IS NULL`` / ``WHERE embedding IS NOT NULL``), which is
#: easy to miss and blocks the column drop just as hard.
DROP_INDEX_DDL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS evidence_items@evidence_embedding_ann_idx CASCADE",
    "DROP INDEX IF EXISTS evidence_items@idx_evidence_embedding_backlog CASCADE",
    "DROP INDEX IF EXISTS evidence_items@idx_evidence_text_hash CASCADE",
)

DROP_CONSTRAINT_DDL: tuple[str, ...] = (
    "ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS ck_evidence_embedding_provenance",
    "ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS ck_evidence_embedding_model",
)

#: The whole quartet. See the docstring: keeping the provenance columns while
#: dropping the vector would both misreport history and make the new CHECK
#: unaddable.
DROP_COLUMN_DDL: tuple[str, ...] = (
    "ALTER TABLE evidence_items DROP COLUMN IF EXISTS embedding",
    "ALTER TABLE evidence_items DROP COLUMN IF EXISTS embedding_model",
    "ALTER TABLE evidence_items DROP COLUMN IF EXISTS embedding_version",
    "ALTER TABLE evidence_items DROP COLUMN IF EXISTS embedding_generated_at",
)

#: The families are restated on the way back in. ``f_vec`` exists so that a
#: metadata read does not drag 6KB of float with it, and a column re-added
#: without a family lands in the primary one — silently undoing the layout
#: 0002 chose. ``CREATE IF NOT EXISTS`` because dropping the only member of
#: ``f_vec`` may take the family with it.
ADD_COLUMN_DDL: tuple[str, ...] = (
    f"ALTER TABLE evidence_items ADD COLUMN embedding VECTOR({EMBEDDING_DIMENSIONS}) NULL"
    " CREATE IF NOT EXISTS FAMILY f_vec",
    "ALTER TABLE evidence_items ADD COLUMN embedding_model STRING NULL FAMILY f_meta",
    "ALTER TABLE evidence_items ADD COLUMN embedding_version STRING NULL FAMILY f_meta",
    "ALTER TABLE evidence_items ADD COLUMN embedding_generated_at TIMESTAMPTZ NULL FAMILY f_meta",
)

#: Unchanged in meaning from 0002; re-added because its columns were dropped.
EMBEDDING_PROVENANCE_CHECK_DDL = """
ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_embedding_provenance CHECK (
    embedding IS NULL
    OR (embedding_model IS NOT NULL
        AND embedding_version IS NOT NULL
        AND embedding_generated_at IS NOT NULL)
)
"""

#: Fact 2. Two spellings on purpose — see the docstring and
#: :data:`EMBEDDING_MODEL_IDS`.
EMBEDDING_MODEL_CHECK_DDL = (
    "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_embedding_model CHECK ("
    f" embedding_model IS NULL OR embedding_model IN ({_quoted(EMBEDDING_MODEL_IDS)})"
    ")"
)

#: Fact 3. Rejects a stale model id at the database boundary, exactly as the
#: 0005 constraint did for Bedrock. ``agent_runs.model_route`` records the id
#: that actually served a run, so a CHECK still admitting Opus 4.6 would let the
#: database accept a row the submission's disclosure says cannot exist.
PROPOSAL_MODEL_CHECK_DDL = (
    "ALTER TABLE memory_proposals ADD CONSTRAINT ck_memory_proposals_model CHECK ("
    f" model_id IN ({_quoted(PROPOSAL_MODEL_IDS)})"
    ")"
)

DROP_PROPOSAL_CHECK_DDL = (
    "ALTER TABLE memory_proposals DROP CONSTRAINT IF EXISTS ck_memory_proposals_model"
)

#: Rebuilt verbatim from 0002 — same names, same columns, same predicates.
RECREATE_SCALAR_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX idx_evidence_embedding_backlog ON evidence_items (created_at)"
    " WHERE embedding IS NULL",
    "CREATE INDEX idx_evidence_text_hash"
    " ON evidence_items (normalized_text_sha256, embedding_version)"
    " WHERE embedding IS NOT NULL",
)

#: Variant A, unchanged from 0002 — only the column width underneath it moved.
#: ``user_id`` remains the prefix column, and that is not negotiable: it is the
#: mechanism by which ANN search physically cannot return another user's
#: evidence, so invariant I7 does not rest on a ``WHERE`` clause.
#:
#: LAST, always. See the docstring: ``IMPORT INTO`` is refused while this index
#: exists, batch inserts degrade badly against it, and a full build costs 52-55
#: minutes. It is cheap *here* only because the column is entirely NULL at this
#: point.
ANN_INDEX_DDL = (
    "CREATE VECTOR INDEX evidence_embedding_ann_idx "
    "ON evidence_items (user_id, embedding vector_cosine_ops)"
)

UPGRADE_DDL: tuple[str, ...] = (
    *DROP_INDEX_DDL,
    *DROP_CONSTRAINT_DDL,
    *DROP_COLUMN_DDL,
    *ADD_COLUMN_DDL,
    EMBEDDING_PROVENANCE_CHECK_DDL,
    EMBEDDING_MODEL_CHECK_DDL,
    DROP_PROPOSAL_CHECK_DDL,
    PROPOSAL_MODEL_CHECK_DDL,
    *RECREATE_SCALAR_INDEX_DDL,
    ANN_INDEX_DDL,
)


# ---------------------------------------------------------------------------
# Downgrade — the same shape restored at the 0008 width. Not the same data.
# ---------------------------------------------------------------------------

DOWNGRADE_EMBEDDING_MODEL_CHECK_DDL = (
    "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_embedding_model CHECK ("
    f" embedding_model IS NULL OR embedding_model = '{SUPERSEDED_EMBEDDING_MODEL_ID}'"
    ")"
)

DOWNGRADE_PROPOSAL_MODEL_CHECK_DDL = (
    "ALTER TABLE memory_proposals ADD CONSTRAINT ck_memory_proposals_model CHECK ("
    f" model_id IN ({_quoted(SUPERSEDED_PROPOSAL_MODEL_IDS)})"
    ")"
)

DOWNGRADE_DDL: tuple[str, ...] = (
    *DROP_INDEX_DDL,
    *DROP_CONSTRAINT_DDL,
    *DROP_COLUMN_DDL,
    f"ALTER TABLE evidence_items ADD COLUMN embedding"
    f" VECTOR({SUPERSEDED_EMBEDDING_DIMENSIONS}) NULL CREATE IF NOT EXISTS FAMILY f_vec",
    "ALTER TABLE evidence_items ADD COLUMN embedding_model STRING NULL FAMILY f_meta",
    "ALTER TABLE evidence_items ADD COLUMN embedding_version STRING NULL FAMILY f_meta",
    "ALTER TABLE evidence_items ADD COLUMN embedding_generated_at TIMESTAMPTZ NULL FAMILY f_meta",
    EMBEDDING_PROVENANCE_CHECK_DDL,
    DOWNGRADE_EMBEDDING_MODEL_CHECK_DDL,
    DROP_PROPOSAL_CHECK_DDL,
    DOWNGRADE_PROPOSAL_MODEL_CHECK_DDL,
    *RECREATE_SCALAR_INDEX_DDL,
    ANN_INDEX_DDL,
)


# ---------------------------------------------------------------------------
# Guards. Pure functions, so they are testable without a cluster.
# ---------------------------------------------------------------------------


def require_acknowledgement(
    *, embeddings_destroyed: int, acknowledged: str | None, database: str
) -> None:
    """Refuse unless *acknowledged* is exactly the number about to be destroyed.

    An exact-count acknowledgement cannot be set without first having read the
    number, which is the difference between a confirmation and a habit. A stale
    value from a smaller corpus fails, and so does ``yes``.
    """
    expected = str(embeddings_destroyed)
    if acknowledged is not None and acknowledged.strip() == expected:
        return
    raise RuntimeError(
        f"migration 0009 will DESTROY {embeddings_destroyed} embeddings in database "
        f"{database!r}: a 1024-dimension Titan vector cannot be converted to a "
        f"1536-dimension Gemini one, so every evidence_items.embedding becomes NULL. "
        f"The rows survive; the vectors do not, and retrieval returns nothing until "
        f"all of them are re-embedded with {EMBEDDING_MODEL_IDS[0]!r} at "
        f"{EMBEDDING_DIMENSIONS} dimensions and embedding_version {EMBEDDING_VERSION!r} "
        f"(PIVOT.md section 8 item 3; budget two hours, of which 52-55 minutes is the "
        f"ANN index rebuild). "
        f"Set {ACK_ENV_VAR}={expected} to proceed, and do not run this while a seed is "
        f"in flight: CockroachDB serialises schema changes, so a concurrent DROP INDEX "
        f"queues behind this revision's CREATE and fires the instant it succeeds."
        + (
            f" (Received {ACK_ENV_VAR}={acknowledged!r}.)"
            if acknowledged is not None
            else f" ({ACK_ENV_VAR} is not set.)"
        )
    )


def require_no_stranded_proposals(*, stranded: int, database: str) -> None:
    """Refuse if any ``memory_proposals`` row names a model the new CHECK rejects.

    ``ADD CONSTRAINT`` validates existing rows, so this would fail anyway — but
    it would fail as a ``CheckViolation`` that prints the offending row in full
    into the migration transcript. Counting first turns a row dump into a number,
    and names the fix.
    """
    if stranded == 0:
        return
    raise RuntimeError(
        f"migration 0009 cannot repoint ck_memory_proposals_model in database "
        f"{database!r}: {stranded} memory_proposals row(s) name a model id outside "
        f"the Gemini canon {PROPOSAL_MODEL_IDS}. Those proposals were produced by a "
        f"model this build no longer runs. Decide explicitly whether they are history "
        f"worth keeping - in which case widen PROPOSAL_MODEL_IDS and say why in "
        f"CANONICAL_DECISIONS.md - or reseed them; do not let ADD CONSTRAINT discover "
        f"it and dump the rows into the log."
    )


def _scalar(bind: Connection, statement: str) -> int:
    return int(bind.exec_driver_sql(statement).scalar() or 0)


def upgrade() -> None:
    """Census first, then refuse or proceed, then the DDL — ANN index last.

    Both reads happen before any schema change, for the reason 0008 gives: a
    precondition that can only be checked halfway through a migration is not a
    precondition.
    """
    bind = op.get_bind()
    database = str(bind.exec_driver_sql("SELECT current_database()").scalar())
    embeddings_destroyed = _scalar(bind, EMBEDDING_CENSUS_SQL)
    stranded = _scalar(bind, PROPOSAL_CENSUS_SQL)

    require_acknowledgement(
        embeddings_destroyed=embeddings_destroyed,
        acknowledged=environ.get(ACK_ENV_VAR),
        database=database,
    )
    require_no_stranded_proposals(stranded=stranded, database=database)

    for statement in UPGRADE_DDL:
        op.execute(statement)


def downgrade() -> None:
    """Restore the 1024-wide Titan shape. Local iteration only.

    Symmetrically destructive: the 1536-dim Gemini vectors do not survive this
    either. What comes back is the shape, so a branch still on 0008 can run.
    """
    for statement in DOWNGRADE_DDL:
        op.execute(statement)
