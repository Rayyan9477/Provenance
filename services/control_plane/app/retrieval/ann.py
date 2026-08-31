"""Stage D — the one sanctioned ANN statement, and the bind that saves it.

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 5.5 -- "the only sanctioned ANN
  query shape", transcribed below verbatim, with the four rules that make it
  correct. Section 3 of ``13_RETRIEVAL_SPEC.md`` declares the DDL authoritative
  over any query printed in that document, which matters here: see the recorded
  deviation.
- ``ops/decisions/VECTOR_INDEX_VARIANT.md`` -- VARIANT A, prefix ``user_id``.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.2``, ``G6.3(a)``, ``G6.3(b)``.
- Defect ``D-06-001``.

``D-06-001``: why the bind is the whole design
-----------------------------------------------
An ANN query vector supplied as a **correlated subquery** silently produces a
full scan. Correct results, no error, no warning; it survives ``ANALYZE`` and
reproduces at 3 dimensions and at 1024. No result-set assertion can see it at
any corpus size -- only latency changes, and latency is invisible at demo
scale.

The remedy is structural, not procedural: retrieval **embeds first**, outside
the transaction, and passes the vector in as a bound parameter.
:func:`bind` refuses a query vector that looks like SQL, so the defect is
unrepresentable at the call site rather than merely documented. A comment
saying "never compute the vector inside the statement" is advice; a refusal is
a boundary, and it is the one that survives the next engineer in a hurry.

Recorded deviation from ``13_RETRIEVAL_SPEC.md`` section 9.2
-------------------------------------------------------------
Section 9.2 prints a **flat** statement carrying ``tenant_id``,
``retraction_status``, ``embedding_version``, ``embedding IS NOT NULL`` and both
temporal predicates inside the same block as the ``ORDER BY ... <=> ...``. DDL
section 5.5 says the opposite on three of those points, and says why:

* non-prefix predicates inside the ANN block "can prevent the vector index from
  being chosen at all";
* ``embedding IS NOT NULL`` is forbidden outright -- rows without a vector are
  not in the index anyway, so the predicate risks disqualifying it without
  changing the result;
* the temporal predicates do not appear in section 5.5 at all.

The DDL wins, by the retrieval spec's own instruction. The temporal window is
therefore applied in Stage E rather than in Stage D -- which section 9.3 already
names as the sanctioned lever if post-ANN filtering ever costs recall. Reported
as a spec discrepancy rather than resolved silently.

Where this module ought to live
--------------------------------
``CANONICAL_DECISIONS.md`` names
``provenance_db.repositories.evidence.ann_search()`` as *the* ANN entry point,
and ``13_RETRIEVAL_SPEC.md`` section 19 puts the SQL in
``provenance_db/queries/retrieval.py``. That package is owned by another task
and its ``ann_search`` currently raises ``NotImplementedError``. The statement
lives here, in the module ``70_TASK_PLAN.md`` T6.3 names, and the repository
entry point should delegate to it. One door either way; the door is currently
in the wrong wall, and that is reported rather than worked around by opening a
second one.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Final

from services.control_plane.app.retrieval import predicates
from services.control_plane.app.retrieval.config import (
    ACTIVE_EMBEDDING_PROFILE,
    K_FINAL,
    K_RAW,
    VECTOR_TARGET,
)

__all__ = [
    "CANONICAL_ANN_SQL",
    "OVERFETCH_EXHAUSTED",
    "PARAMETER_ORDER",
    "QueryVectorNotBoundError",
    "bind",
    "canonical_predicate_from_spec",
    "normalise_sql",
    "overfetch_note",
    "render_ann_sql",
]

#: ``10_DATABASE_DDL.md`` section 5.5, transcribed. Not paraphrased and not
#: reformatted: ``tests/retrieval/test_ann_predicate.py`` diffs this against the
#: block the spec prints, so a spec edit that moves a predicate out of the CTE
#: fails a test rather than a latency graph nobody is watching.
#:
#: The four rules, restated where they are enforced:
#:  1. ``user_id = $1`` is INSIDE the CTE. Moving it out breaks the index-prefix
#:     match and turns an ANN lookup into a full scan across every user -- with
#:     identical results, which is why it is pinned structurally.
#:  2. ``tenant_id``, ``retraction_status`` and ``embedding_version`` are
#:     OUTSIDE. Non-prefix predicates in the ANN block can disqualify the index.
#:  3. Over-fetch is mandatory. ``k_raw == k_final`` lets a run of retracted
#:     near-neighbours silently shrink the result set.
#:  4. No ``embedding IS NOT NULL``. Rows without a vector are not in the index.
_ANN_SQL_BEFORE_LIFECYCLE_FILTER: Final[str] = """
WITH ann AS (
    SELECT
        id,
        tenant_id,
        user_id,
        artifact_id,
        evidence_type,
        normalized_text,
        observed_at,
        valid_from,
        valid_to,
        source_authority,
        extraction_confidence,
        retraction_status,
        embedding_version,
        embedding <=> $3 AS distance
    FROM evidence_items
    WHERE user_id = $1
    ORDER BY embedding <=> $3
    LIMIT $4
)
SELECT id, artifact_id, evidence_type, normalized_text, observed_at,
       valid_from, valid_to, source_authority, extraction_confidence, distance
FROM ann
WHERE tenant_id = $2
  AND embedding_version = $6
ORDER BY distance
LIMIT $5
"""

#: Section 5.5's parameter list, ``$1`` through ``$6``. Frozen so a reordering
#: cannot quietly turn the query vector into the limit.
PARAMETER_ORDER: Final[tuple[str, ...]] = (
    "user_id",
    "tenant_id",
    "query_embedding",
    "k_raw",
    "k_final",
    "embedding_version",
)

#: The section 5.5 statement, complete. Built by handing the base above to
#: :func:`predicates.retraction_filter`, which inserts the lifecycle predicate.
#:
#: Assembled rather than written out for one reason, and it is the reason the
#: ``G6.7`` sabotage entry is worth having: ``PV_SABOTAGE`` replaces
#: ``retraction_filter`` with the identity function, this constant then lacks
#: the predicate, the ANN statement returns retracted rows, and ``G6.3(c)``
#: goes red. Had the complete statement been a literal here with a *check*
#: beside it, neutering the check would have changed nothing and the matrix
#: entry would have been a claim nobody could falsify.
#:
#: The module is reached by attribute (``predicates.retraction_filter``) and
#: never by ``from``-import: a ``from``-import copies the reference before the
#: rebind is visible, and the sabotage silently never arrives.
CANONICAL_ANN_SQL: Final[str] = predicates.retraction_filter(_ANN_SQL_BEFORE_LIFECYCLE_FILTER)

_PARAM = re.compile(r"\$(\d+)")
_COMMENT = re.compile(r"--[^\n]*")
_WS = re.compile(r"\s+")

#: The ``$n`` numbers in the order they appear in the text: ``3, 1, 3, 4, 2, 6,
#: 5``. psycopg's ``%s`` is positional by **appearance**, while the spec numbers
#: its parameters by meaning, and ``$3`` appears twice. Deriving the mapping
#: from the statement rather than hand-writing it is what keeps :func:`bind`
#: correct when the statement is edited -- a hand-written tuple would silently
#: bind ``k_final`` where the vector belongs.
_APPEARANCE_ORDER: Final[tuple[int, ...]] = tuple(
    int(token) for token in _PARAM.findall(CANONICAL_ANN_SQL)
)

#: Anything that smells of SQL rather than of a vector literal. Deliberately
#: broad: the cost of a false positive is a caller passing a properly formatted
#: vector and getting a clear error, and the cost of a false negative is
#: ``D-06-001`` back in production with no symptom.
_SQL_SHAPED = re.compile(r"(?i)\b(select|from|where|join|with|union)\b|\(")


class QueryVectorNotBoundError(ValueError):
    """The query vector was SQL rather than a value. ``D-06-001``.

    Raised at the call site, before anything reaches the planner, because the
    defect has no runtime symptom: results are correct, no error is raised, and
    the only evidence is an ``EXPLAIN`` nobody runs in production.
    """


def normalise_sql(sql: str) -> str:
    """Strip ``--`` comments, fold whitespace, drop a trailing ``;``.

    For comparison only; never used to build a statement. Comparing the shipped
    SQL to the spec's block byte-for-byte would fail on indentation, and on the
    statement terminator the spec prints and psycopg does not need. Comparing
    them after this fails only on substance -- a moved predicate, a changed
    limit, a dropped filter.
    """
    return _WS.sub(" ", _COMMENT.sub("", sql)).strip().rstrip(";").strip()


def canonical_predicate_from_spec(markdown: str) -> str:
    """The single section 5.5 block from the DDL spec text.

    Exactly one block may match. Two would mean the spec grew a second
    "sanctioned" shape, which is the state this whole module exists to prevent.
    """
    blocks = [
        str(block)
        for block in re.findall(r"```sql\n(.*?)```", markdown, re.S)
        if "WITH ann AS (" in block
    ]
    if len(blocks) != 1:
        raise ValueError(f"expected exactly one section 5.5 predicate block, found {len(blocks)}")
    return blocks[0]


def render_ann_sql(*, retraction_filter: bool = True) -> str:
    """The statement psycopg executes: ``$n`` rewritten to ``%s``, in order.

    ``$3`` carries an explicit ``::VECTOR`` cast because CockroachDB needs the
    target width to resolve ``<=>`` against a bound parameter.

    *retraction_filter* exists for **one** caller: ``G6.3(d)``'s positive
    control, which removes the predicate and requires the superseded fixture to
    appear in the top 20. Part (d) failing means part (c) was passing
    vacuously, so the unfiltered form has to be constructible -- and it is a
    keyword-only argument defaulting to ``True`` so that constructing it is a
    visible act rather than an omission.
    """
    sql = CANONICAL_ANN_SQL if retraction_filter else _ANN_SQL_BEFORE_LIFECYCLE_FILTER
    # The width follows the active profile rather than a literal.
    #
    # It was ``VECTOR(1024)`` written out, which is correct today and silently
    # wrong the moment the profile moves: ``config.py`` describes the Gemini
    # flip as gated on prerequisites it does not own, and migration 0009 widens
    # the column to 1536. With the width hardcoded here, that flip would leave
    # the only ANN renderer in the system casting every query vector to the old
    # width -- and the failure would arrive as "expected 1024 dimensions, not
    # 1536" from the database on a live path, because no test binds a real
    # vector.
    width = ACTIVE_EMBEDDING_PROFILE.column_width
    return _PARAM.sub(
        lambda match: f"%s::VECTOR({width})" if match.group(1) == "3" else "%s",
        sql,
    )


def bind(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    query_embedding: str | list[float] | tuple[float, ...],
    embedding_version: str,
    k_raw: int = K_RAW,
    k_final: int = K_FINAL,
) -> tuple[Any, ...]:
    """Parameters for :func:`render_ann_sql`, in ``PARAMETER_ORDER``.

    ``user_id`` and ``tenant_id`` come from the verified ``Principal`` and never
    from a request body; that is Stage A's job and this function only refuses to
    make it easy to bypass.

    Raises:
        QueryVectorNotBoundError: *query_embedding* is SQL text rather than a
            vector. This is ``D-06-001`` made unrepresentable.
        ValueError: ``k_raw <= k_final``, which defeats the over-fetch and lets
            a run of retracted near-neighbours silently shrink the result.
    """
    if isinstance(query_embedding, str):
        if _SQL_SHAPED.search(query_embedding) and not query_embedding.lstrip().startswith("["):
            raise QueryVectorNotBoundError(
                "the query vector must be a bound value, never a subquery or an "
                "expression. D-06-001: a correlated subquery here silently "
                "produces a FULL SCAN -- correct results, no error, no warning, "
                "and it survives ANALYZE. Embed first, outside the transaction, "
                "and pass the vector in."
            )
        vector: Any = query_embedding
    else:
        vector = "[" + ",".join(repr(float(value)) for value in query_embedding) + "]"

    if k_raw <= k_final:
        raise ValueError(
            f"k_raw ({k_raw}) must exceed k_final ({k_final}): ANN returns k_raw "
            "candidates before the retraction and version filters run, so an "
            "equal pair lets a run of retracted near-neighbours shrink the "
            "result set with no signal that anything happened"
        )

    values: dict[str, Any] = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "query_embedding": vector,
        "k_raw": k_raw,
        "k_final": k_final,
        "embedding_version": embedding_version,
    }
    return tuple(values[PARAMETER_ORDER[index - 1]] for index in _APPEARANCE_ORDER)


#: The ``StageStat`` note section 9.3 asks for by name.
OVERFETCH_EXHAUSTED: Final[str] = "OVERFETCH_EXHAUSTED"


def overfetch_note(
    *,
    raw_rows: int,
    surviving_rows: int,
    k_raw: int = K_RAW,
    target: int = VECTOR_TARGET,
) -> tuple[str, ...]:
    """``("OVERFETCH_EXHAUSTED",)`` when the filter ate the tail, else ``()``.

    Section 9.3, and the reason ``LIMIT 20`` is a lie. The ANN traversal
    supplies the ordering and the limit; the predicates that are not index
    prefix columns -- ``tenant_id``, ``retraction_status``,
    ``embedding_version`` -- are applied to the rows it returned. A traversal
    that comes back at exactly *k_raw* and survives filtering at fewer than
    *target* rows means there were more neighbours and the traversal stopped
    before them.

    Two states produce the same short result set and have opposite fixes:

    * the corpus genuinely holds eleven relevant rows -- nothing to do;
    * the over-fetch multiplier is too small for this filter's selectivity --
      raise it, or move the temporal predicates out of Stage D per section 9.3.

    Only the second is reported, because a note that fires on the first would
    fire constantly and be filtered out of the dashboard within a week.

    Recorded discrepancy: section 9.3's condition is ``raw_ann_rows ==
    VECTOR_FETCH_LIMIT`` where ``VECTOR_FETCH_LIMIT`` is ``VECTOR_TARGET *
    VECTOR_OVERFETCH`` (60). ``10_DATABASE_DDL.md`` section 5.5 -- which section
    3 of the retrieval spec declares authoritative over any query in that
    document -- binds ``k_raw = greatest(40, 4 * k_final)`` (80). The bound
    value is what the traversal actually stopped at, so the default is
    :data:`K_RAW` and the comparison is ``>=`` rather than ``==``: a planner
    that returns one row more than requested must not silence the report.

    Raises:
        ValueError: *surviving_rows* exceeds *raw_rows*. That is arithmetically
            impossible for a filter, so it means the two counts were measured at
            different points in the pipeline -- after which every shortfall
            report from this function is meaningless rather than merely wrong.
    """
    if surviving_rows > raw_rows:
        raise ValueError(
            f"surviving_rows ({surviving_rows}) cannot exceed raw_rows ({raw_rows}): "
            "the survivors are a subset of the ANN traversal's output, so a larger "
            "count means the two numbers were measured at different stages"
        )
    if raw_rows >= k_raw and surviving_rows < target:
        return (OVERFETCH_EXHAUSTED,)
    return ()


# ``ann_search()`` lived here until 2026-08-24 and has been removed.
#
# ``CANONICAL_DECISIONS.md`` names
# ``provenance_db.repositories.evidence.ann_search()`` as *the* ANN entry
# point. This copy existed only because that repository module still raised
# ``NotImplementedError``, and said so in its own docstring: *"the divergence
# is reported rather than papered over by opening a second door."* The
# repository is implemented now, so the condition no longer holds and what
# remained was a second door.
#
# It was also broken, in the quietest way available: it applied
# ``predicates.active_rows`` to the section 5.5 statement, whose outer
# ``SELECT`` does not project ``retraction_status``. ``active_rows`` drops any
# row without that key, so **every ANN query returned zero rows** -- no
# exception, no warning, results that merely looked empty. ``active_rows``'s
# own docstring names the mistake exactly: *"a filter fed rows that cannot
# carry the column would drop all of them, which is a different bug wearing
# the same clothes."*
#
# Widening the projection was not available: ``tests/retrieval/
# test_ann_predicate.py`` pins this statement to ``10_DATABASE_DDL.md`` after
# normalisation, and that pin is what catches a dropped filter.
#
# ``render_ann_sql()`` and ``bind()`` above remain the shared machinery, and
# the repository delegates to them -- so there is still exactly one module in
# the tree containing ``<=>``.
