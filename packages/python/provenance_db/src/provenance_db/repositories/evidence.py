"""Reads over the evidence plane, including the single ANN entry point.

Authority
---------
- ``CANONICAL_DECISIONS.md`` -> *Names and counts*:
  ``provenance_db.repositories.evidence.ann_search()`` is **the** ANN entry
  point.
- ``specs/13_RETRIEVAL_SPEC.md`` section 13.3 layer 3, which puts the retraction
  tripwire in this function **by name**, and section 19, which wants exactly one
  module to contain ``<=>``.
- ``CANONICAL_DECISIONS.md`` -> *Retrieval eligibility*: only ``ACTIVE``
  evidence may enter new retrieval or ground a new belief. Retracted and
  superseded evidence keeps its bytes, its metadata, its embeddings and its
  historical support edges; it is excluded from retrieval, not deleted.
- ``specs/10_DATABASE_DDL.md`` section 5.5 — the exact retrieval predicate, and
  section 12 — write-path ownership.
- Defect ``D-06-001``.

Where the statement lives, and why this module does not hold a copy
--------------------------------------------------------------------
``13_RETRIEVAL_SPEC.md`` section 19 puts the SQL in
``provenance_db/queries/retrieval.py`` and calls it "THE ONLY module containing
``<=>``". That module does not exist. The statement was built instead in
``services/control_plane/app/retrieval/ann.py`` under ``T6.3``, where it is
transcribed from DDL section 5.5, diffed against the spec text by
``tests/retrieval/test_ann_predicate.py``, and assembled through
``predicates.retraction_filter`` so the ``G6.7`` sabotage entry is falsifiable.

So the door named by ``CANONICAL_DECISIONS.md`` is here and the statement is
there. This module **delegates**; it does not transcribe. A second copy of a
40-line vector statement is how one of the two loses the ``user_id`` prefix
from inside the CTE — which turns an ANN lookup into a full scan across every
user in the cluster, with identical-looking results. One statement, one entry
point, and the discrepancy in the two documents reported rather than resolved
by opening a second door.

The import is deferred to call time on purpose. ``provenance-db`` is a
distributable package and ``services/`` is a deployment unit imported by path;
a module-level import would invert that dependency and make the wheel
un-importable wherever the control plane is not on ``sys.path``. Deferring it
means ``import provenance_db.repositories.evidence`` costs nothing and only
:func:`ann_search` requires the retrieval module to be present.

``D-06-001``, restated where the vector is passed
--------------------------------------------------
An ANN query vector supplied as a **correlated subquery** silently defeats
vector-index selection. Correct results, no error, no warning, and it survives
``ANALYZE``. No result-set assertion can see it at any corpus size — only
latency changes, and latency is invisible at demo scale.

The vector is therefore built by :func:`ann.bind` into the **parameter tuple**
and never into the statement text, and ``bind`` refuses a query vector that
looks like SQL. That refusal is the boundary; a comment would be advice.
``tests/unit/test_repositories.py`` asserts on the pair the driver receives and
``tests/db/test_repository_reads.py`` asserts on the ``EXPLAIN``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from provenance_contracts.identity import Principal
from provenance_db import retry
from provenance_db.repositories._execute import _fetch_all, _fetch_one, _owner, _scope

__all__ = [
    "ACTIVE_EVIDENCE_FOR_CASE_SQL",
    "EVIDENCE_ITEM_SQL",
    "RETRACTED_EVIDENCE_COUNT_SQL",
    "count_retracted_evidence_for_case",
    "RETRACTION_PREDICATE",
    "RetractionFilterMissingError",
    "ann_search",
    "explain_ann_search",
    "get_evidence_item",
    "list_active_evidence_for_case",
]

#: ``13_RETRIEVAL_SPEC.md`` section 13.3 layer 3's tripwire, as a constant so
#: the assertion and the SQL cannot disagree about the spelling.
RETRACTION_PREDICATE = "retraction_status = 'ACTIVE'"


class RetractionFilterMissingError(RuntimeError):
    """The vector statement reached this function without its lifecycle filter.

    Raised **before** the statement is executed, because the failure it guards
    has no symptom: a retracted item keeps its embedding, so an unfiltered ANN
    returns it, ranks it plausibly, and grounds a new belief on a fact the user
    already disowned — with no error and a completely plausible-looking State
    Proof. There is nothing to notice afterwards, so the check has to be before.
    """


#: ``evidence_items`` as the migrations actually define it (``0002``): the
#: artifact column is ``artifact_id``, the timestamp is ``created_at``, and
#: there is **no** ``case_id`` — evidence reaches a case through ``claims``.
EVIDENCE_ITEM_SQL = """
    SELECT id, artifact_id, evidence_type, normalized_text, actor_ref,
           valid_from, valid_to, observed_at, extraction_confidence,
           source_authority, retraction_status, retracted_at,
           retracted_by_evidence_id, retraction_reason_code,
           is_retrieval_eligible, embedding_version, created_at
    FROM evidence_items
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND id = %(evidence_id)s
"""

#: Evidence for one case, through ``claims``.
#:
#: ``claims`` is the only table that carries both ``evidence_id`` and
#: ``case_id``; ``evidence_items`` carries neither a case nor a relationship.
#: A statement that selected ``evidence_items.case_id`` would be rejected by the
#: server, which is exactly what ``tests/db/test_repository_reads.py`` is for.
#:
#: ``DISTINCT`` because one evidence item routinely yields several claims on the
#: same case — the June invoice admits three (``00_PRODUCT.md`` section 2.3) —
#: and the join would otherwise return the item once per claim.
ACTIVE_EVIDENCE_FOR_CASE_SQL = """
    SELECT DISTINCT
           e.id, e.artifact_id, e.evidence_type, e.normalized_text,
           e.valid_from, e.valid_to, e.observed_at, e.extraction_confidence,
           e.source_authority, e.retraction_status, e.embedding_version,
           e.created_at
    FROM evidence_items e
    JOIN claims c
      ON c.tenant_id = e.tenant_id
     AND c.user_id = e.user_id
     AND c.evidence_id = e.id
    WHERE e.tenant_id = %(tenant_id)s
      AND e.user_id = %(user_id)s
      AND c.case_id = %(case_id)s
      AND e.retraction_status = 'ACTIVE'
    ORDER BY e.observed_at DESC
    LIMIT %(limit)s
"""


async def get_evidence_item(
    conn: AsyncConnection[Any],
    principal: Principal,
    evidence_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> dict[str, Any] | None:
    """One evidence item, scoped to *principal*.

    Returns the row whatever its ``retraction_status`` is. This is the State
    Proof read, not a retrieval read: ``CANONICAL_DECISIONS.md`` -> *Historical
    visibility* keeps retracted and superseded evidence visible with a status
    badge, and hiding it here would make a correction the user made look like
    a row that never existed. Retrieval eligibility is enforced where retrieval
    happens — :func:`ann_search` and
    :func:`list_active_evidence_for_case` — and the caller renders the badge
    from ``retraction_status``.
    """
    return await _fetch_one(
        conn,
        EVIDENCE_ITEM_SQL,
        {**_scope(principal), "evidence_id": evidence_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def list_active_evidence_for_case(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    limit: int = 50,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Retrieval-eligible evidence for one case, most recent first.

    Both halves of layer 3 run here, and the redundancy is the design: the SQL
    carries ``retraction_status = 'ACTIVE'`` and
    :func:`~services.control_plane.app.retrieval.predicates.active_rows` is
    applied to the rows that come back — this statement **projects** the
    lifecycle column, so the in-process filter has something real to read.

    The in-process half is a no-op whenever the predicate is intact, which is
    precisely why it is worth having: a single missed predicate is a silent
    correctness failure, and the only affordable response to a failure nothing
    reports is to make it take two independent mistakes rather than one.
    """
    rows = await _fetch_all(
        conn,
        ACTIVE_EVIDENCE_FOR_CASE_SQL,
        {**_scope(principal), "case_id": case_id, "limit": limit},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )
    return _active_rows(rows)


#: Section 8.11's ``excluded.retracted_evidence_count`` — what the retraction
#: filter removed from this case's proof.
#:
#: It is a **count of what was hidden**, and that is the whole reason the field
#: exists: ``retraction_filter_applied: true`` beside a number of zero and
#: ``true`` beside a number of two are different claims, and only the second
#: shows the filter doing anything. A surface that rendered the flag without
#: the count would be asserting that a filter ran with no way to tell whether
#: it had anything to run on.
RETRACTED_EVIDENCE_COUNT_SQL = """
    SELECT count(DISTINCT e.id) AS retracted_evidence_count
    FROM evidence_items e
    JOIN claims c
      ON c.tenant_id = e.tenant_id
     AND c.user_id = e.user_id
     AND c.evidence_id = e.id
    WHERE e.tenant_id = %(tenant_id)s
      AND e.user_id = %(user_id)s
      AND c.case_id = %(case_id)s
      AND e.retraction_status <> 'ACTIVE'
"""


async def count_retracted_evidence_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> int:
    """How much of this case's evidence the lifecycle filter excludes."""
    row = await _fetch_one(
        conn, RETRACTED_EVIDENCE_COUNT_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )
    return int(row["retracted_evidence_count"]) if row else 0


def _active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Layer 3's in-process half, reached through the module object.

    ``PV_SABOTAGE`` rebinds ``predicates.retraction_filter`` on the module
    object; a ``from``-import here would copy the reference before the rebind
    and the sabotage would silently never arrive. The import is deferred for
    the packaging reason in the module docstring, and degrades to the identity
    only when the retrieval module is genuinely absent — a wheel installed
    without the control plane — rather than swallowing an error from inside it.
    """
    try:
        from services.control_plane.app.retrieval import predicates
    except ModuleNotFoundError:  # pragma: no cover - control plane not installed
        return [row for row in rows if row.get("retraction_status") == "ACTIVE"]
    return predicates.active_rows(rows)


def _ann_statement() -> str:
    """The section 5.5 statement, with the tripwire ``13.3`` asks for by name.

    ``assert`` is deliberately not used. ``python -O`` strips assertions, and a
    build flag that silently removes the only check standing between a user's
    correction and its resurfacing is not a risk worth the two saved
    characters. The spec writes ``assert``; the intent is a tripwire, and a
    raise is the version of it that survives the deployment.
    """
    from services.control_plane.app.retrieval import ann

    sql = ann.render_ann_sql()
    if RETRACTION_PREDICATE not in sql:
        raise RetractionFilterMissingError(
            "the ANN statement reached provenance_db.repositories.evidence."
            f"ann_search() without {RETRACTION_PREDICATE!r}. Retracted and "
            "superseded evidence keeps its embedding, so an unfiltered vector "
            "search returns it, ranks it plausibly — a correction is by "
            "construction about the same subject as the thing it corrects — and "
            "grounds a new belief on a fact the user already disowned. "
            "13_RETRIEVAL_SPEC.md section 13.3 layer 3: tripwire, not decoration."
        )
    return sql


async def ann_search(
    conn: AsyncConnection[Any],
    principal: Principal,
    query_embedding: list[float],
    *,
    limit: int,
    embedding_version: str,
    k_raw: int | None = None,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """The single ANN entry point. Nothing else in this system issues vector SQL.

    Args:
        conn: an open connection. The caller owns the transaction; this issues
            one statement and holds nothing.
        principal: the verified identity. ``user_id`` is the ANN index prefix
            and ``tenant_id`` is the defence-in-depth predicate applied after
            the traversal; **neither is a parameter of this function**, so
            there is no argument through which a caller could name a different
            owner (contract law L10).
        query_embedding: the vector, **already computed**. Retrieval embeds
            first, outside the transaction, and passes the vector in — that
            ordering is what makes ``D-06-001`` unrepresentable rather than
            merely documented.
        limit: ``k_final``, the number of rows after filtering.
        embedding_version: the frozen version, ``v1``. Ranking two embedding
            versions in one cosine ordering produces distances that are
            arithmetically fine and semantically meaningless.
        k_raw: the over-fetch. Defaults to DDL section 5.5's
            ``greatest(40, 4 * k_final)``. Over-fetch is mandatory: with
            ``k_raw == k_final`` a run of retracted near-neighbours silently
            shrinks the result set.

    Raises:
        QueryVectorNotBoundError: *query_embedding* is SQL text rather than a
            vector (``D-06-001``).
        RetractionFilterMissingError: the statement lost its lifecycle
            predicate.
        ValueError: ``k_raw <= k_final``.

    Returns:
        Rows ordered by ascending cosine distance, each a mapping keyed by the
        projection. ``distance`` is advisory: ``CANONICAL_DECISIONS.md`` ->
        *Identity order* puts exact identifiers and deterministic identity
        signals ahead of vector similarity, and vector output is never
        canonical truth.
    """
    from services.control_plane.app.retrieval import ann
    from services.control_plane.app.retrieval.config import k_raw_for

    sql = _ann_statement()
    params = ann.bind(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        query_embedding=query_embedding,
        embedding_version=embedding_version,
        k_raw=k_raw if k_raw is not None else k_raw_for(limit),
        k_final=limit,
    )
    return await _fetch_all(conn, sql, params, policy=policy, sleep=sleep, rng=rng)


async def explain_ann_search(
    conn: AsyncConnection[Any],
    principal: Principal,
    query_embedding: list[float],
    *,
    limit: int,
    embedding_version: str,
    k_raw: int | None = None,
) -> list[str]:
    """The query plan for :func:`ann_search`, as lines.

    Not diagnostics for their own sake. ``D-06-001`` has **no result-set
    symptom**: the defective form returns the same rows in the same order and
    only latency moves, which is invisible at demo scale. The plan is the one
    observation that separates ``vector search ... @evidence_embedding_ann_idx``
    from a full scan, so the assertion that the index was chosen needs a
    supported way to look — and a test reaching for ``EXPLAIN`` by hand would
    have to rebuild the statement, which is the second copy this module exists
    to avoid.
    """
    from services.control_plane.app.retrieval import ann
    from services.control_plane.app.retrieval.config import k_raw_for

    params = ann.bind(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        query_embedding=query_embedding,
        embedding_version=embedding_version,
        k_raw=k_raw if k_raw is not None else k_raw_for(limit),
        k_final=limit,
    )
    async with conn.cursor() as cursor:
        await cursor.execute("EXPLAIN " + _ann_statement(), params)
        return [str(record[0]) for record in await cursor.fetchall()]
