"""Stage F — grounding-graph expansion, the recall backstop (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 11.1, statements F.1 to F.4.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 0 — the vocabulary guard.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 2.3 and ``T6.4``.
- ``docs/specs/10_DATABASE_DDL.md`` sections 6.3 and 6.4.

The stage that makes this a memory system rather than a search index
---------------------------------------------------------------------
Vector similarity between a June invoice and a 15 May termination confirmation
is mediocre: different vocabulary, different document genre, four months apart.
The confirmation reaches the model **not because it is semantically close to
the invoice**, but because it grounds the canonical ``service_terminated``
belief on the case the invoice was matched to. That is a graph walk, not a
similarity computation, and without it the hero demo fails.

The failure has no error message. Stage D returns twenty perfectly reasonable
rows, the model drafts a perfectly reasonable reply, and the one document that
proves the charge is wrong is simply absent. Every ranking test stays green.

grounding is not lineage, and this module issues both queries
---------------------------------------------------------------
**grounding** is the ``belief_support`` edge set -- ``SUPPORTS`` /
``CONTRADICTS`` / ``QUALIFIES`` -- and answers *why do you believe this*.
**lineage** is the ``belief_versions`` chain with its supersession reasons and
answers *what did you believe before, and what changed your mind*. F.1 and F.2
load the first; F.3 loads the second. They are a different query over a
different table answering a different question, they arrive in two fields under
those two names, and :class:`GroundingLineageMergeError` exists because the
merge is the *plausible* refactor -- both are "the history of this belief",
both render as a list under a heading, and a reviewer skimming a diff would not
stop it.

Parameters are bound by name, not by position
-----------------------------------------------
F.2 takes an array of ``belief_version_id``; F.3 takes an array of
``belief_id``. Positionally the two are indistinguishable -- both are UUID
arrays, and each statement is valid SQL with the other one's array bound into
it. It returns zero rows, which reads as "this belief has no history" rather
than as a bug. :func:`render` therefore emits ``%(name)s`` placeholders and the
binders return mappings, so the swap is a ``KeyError`` at the call site instead
of an empty panel in the UI.

Recorded schema deviations from section 11.1
----------------------------------------------
F.2 as printed selects ``ev.relationship_id`` and ``ev.case_id``. Migration
``0002`` gives ``evidence_items`` neither column: the identity link runs
through ``claims``, which carries both. The two columns are dropped from the
shipped statement rather than invented in a migration, and
``tests/retrieval/test_grounding_sql.py`` executes all six statements against
the live schema so a transcription that cannot run fails a test rather than a
demo. Reported as a spec discrepancy; not resolved here.

No model, and no way to add one
--------------------------------
``days_past_due`` is computed by ``date_trunc`` subtraction in F.4, not by a
model reasoning its way to 64. Those are not the same engineering artifact:
one is re-derivable by anybody reading the Memory Trace and the other is a
number that happened to appear.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, NoReturn

from services.control_plane.app.retrieval.predicates import RETRACTION_PREDICATE
from services.control_plane.app.retrieval.rerank import Candidate

__all__ = [
    "COMMITMENT_LIMIT",
    "CONFLICT_LIMIT",
    "GROUNDED_EVIDENCE_LIMIT",
    "LINEAGE_DEPTH",
    "MAX_EXPANSION_CASES",
    "RELATION_ORDER",
    "STATEMENTS",
    "TRANSITION_LIMIT",
    "GroundingEdge",
    "GroundingLineageMergeError",
    "LineageStep",
    "StageFExpansion",
    "bind_case_scoped",
    "bind_grounded_evidence",
    "bind_lineage",
    "expansion_candidate",
    "expansion_cases",
    "order_edges",
    "parameter_names",
    "render",
    "truncate_edges",
]

# ---- section 11.1's bounds --------------------------------------------------

#: ``$3`` is "at most 3 after Stage G's provisional ordering". The walk fans out
#: per case, so a fourth case is not a slightly larger context: it is four more
#: statements' worth of rows competing for ten slots, and the reserve that
#: protects genuinely novel evidence is what loses first.
MAX_EXPANSION_CASES: Final[int] = 3

#: F.2's ``LIMIT``. The backstop is bounded because a belief with a long
#: grounding history would otherwise fill the context by itself.
GROUNDED_EVIDENCE_LIMIT: Final[int] = 12

#: F.3's ``rn <= 3``: current, the one it superseded, and one more for the
#: "this has flip-flopped" signal. Deeper lineage is available from the State
#: Proof endpoint on demand; it does not belong in an agent context window.
LINEAGE_DEPTH: Final[int] = 3

CONFLICT_LIMIT: Final[int] = 6
COMMITMENT_LIMIT: Final[int] = 6

#: 3 cases x 3 transitions.
TRANSITION_LIMIT: Final[int] = 9

#: ``CONTRADICTS`` first, always. A contradiction is the highest-value thing
#: retrieval can surface, and if the list is later truncated for budget the
#: truncation must eat ``QUALIFIES`` and then ``SUPPORTS`` -- never the edge
#: that says the system disagrees with itself.
RELATION_ORDER: Final[dict[str, int]] = {"CONTRADICTS": 0, "SUPPORTS": 1, "QUALIFIES": 2}


class GroundingLineageMergeError(TypeError):
    """Someone tried to render grounding and lineage as one list.

    A changelog is not an argument, and an argument is not a changelog.
    ``70_TASK_PLAN.md`` section 2.3: "Any task whose output conflates the two is
    rejected at review." This makes the rejection mechanical rather than a
    reviewer's alertness on a Friday afternoon.
    """


# =============================================================================
# The six statements
# =============================================================================

#: F.1 — current canonical belief versions, with their grounding edges.
#:
#: The ``ORDER BY`` is not cosmetic. ``CONTRADICTS`` sorts first so that a
#: later truncation eats the supporting documents and leaves the disagreement.
_F1_CANONICAL_BELIEFS = """
WITH scoped_beliefs AS (
    SELECT b.id                AS belief_id,
           b.predicate,
           b.subject_type,
           b.subject_id,
           b.current_version_id
    FROM beliefs AS b
    WHERE b.tenant_id = $1 AND b.user_id = $2
      AND b.case_id = ANY($3::UUID[])
      AND b.current_version_id IS NOT NULL
)
SELECT sb.belief_id,
       sb.predicate,
       sb.subject_type,
       sb.subject_id,
       bv.id                AS belief_version_id,
       bv.version_no,
       bv.value_type,
       bv.value_json,
       bv.epistemic_status,
       bv.belief_confidence,
       bv.valid_from,
       bv.valid_to,
       bv.recorded_at,
       bs.source_kind,
       bs.source_id,
       bs.relation,
       bs.weight,
       bs.reason_code
FROM scoped_beliefs AS sb
JOIN belief_versions AS bv ON bv.id = sb.current_version_id AND bv.tenant_id = $1
LEFT JOIN belief_support AS bs ON bs.belief_version_id = bv.id AND bs.tenant_id = $1
ORDER BY sb.predicate,
         (bs.relation = 'CONTRADICTS') DESC,
         bs.weight DESC NULLS LAST
"""

#: F.2 — evidence reachable only through grounding. The backstop query.
#:
#: A grounding edge whose ``source_id`` points at retracted evidence is silently
#: skipped by the ``retraction_status = 'ACTIVE'`` join predicate, and that is
#: deliberate: the edge stays in the database as historical grounding for State
#: Proof to render with a retraction badge, but it does not re-enter *retrieval*
#: and cannot influence a new proposal. Two questions, two answers.
#:
#: ``ev.relationship_id`` and ``ev.case_id`` are dropped from the section 11.1
#: text: ``evidence_items`` has neither column. See the module docstring.
_F2_GROUNDED_EVIDENCE = """
SELECT ev.id                     AS evidence_id,
       ev.artifact_id,
       ev.evidence_type,
       ev.normalized_text,
       ev.source_locator,
       ev.valid_from,
       ev.valid_to,
       ev.observed_at,
       ev.extraction_confidence,
       ev.source_authority,
       ev.retraction_status,
       sa.sender_domain,
       bs.belief_version_id,
       bs.relation,
       bs.weight,
       bs.reason_code
FROM belief_support AS bs
JOIN evidence_items AS ev
      ON ev.id = bs.source_id
     AND ev.tenant_id = $1 AND ev.user_id = $2
     AND ev.retraction_status = 'ACTIVE'
LEFT JOIN source_artifacts AS sa ON sa.id = ev.artifact_id AND sa.tenant_id = $1
WHERE bs.tenant_id = $1
  AND bs.user_id = $2
  AND bs.belief_version_id = ANY($4::UUID[])
  AND bs.source_kind = 'EVIDENCE'
  AND NOT (ev.id = ANY($5::UUID[]))
ORDER BY (bs.relation = 'CONTRADICTS') DESC, bs.weight DESC NULLS LAST
LIMIT 12
"""

#: F.3 — lineage, three versions deep per belief. A different table, answering
#: a different question. ``supersession_reason_codes`` comes from the kernel
#: decision that wrote the version, because the reason a belief moved is a
#: property of the decision rather than of the row it produced.
_F3_LINEAGE = """
SELECT belief_id, belief_version_id, version_no, value_json, epistemic_status,
       recorded_at, superseded_at, supersession_reason_codes
FROM (
    SELECT bv.belief_id,
           bv.id AS belief_version_id,
           bv.version_no,
           bv.value_json,
           bv.epistemic_status,
           bv.recorded_at,
           bv.superseded_at,
           COALESCE(kd.reason_codes, '[]'::JSONB) AS supersession_reason_codes,
           row_number() OVER (PARTITION BY bv.belief_id
                              ORDER BY bv.version_no DESC) AS rn
    FROM belief_versions AS bv
    LEFT JOIN kernel_decisions AS kd
           ON kd.id = bv.kernel_decision_id AND kd.tenant_id = $1
    WHERE bv.tenant_id = $1 AND bv.user_id = $2
      AND bv.belief_id = ANY($4::UUID[])
) AS ranked
WHERE rn <= 3
ORDER BY belief_id, version_no DESC
"""

#: F.4a — conflicts. Open and needs-human first; recently resolved retained for
#: context, because "we disagreed about this in March and settled it" is
#: information the draft needs and the absence of a conflict is not.
_F4_CONFLICTS = """
SELECT cf.id AS conflict_id, cf.case_id, cf.subject_type, cf.subject_id, cf.predicate,
       cf.conflict_type, cf.status, cf.severity, cf.requires_human,
       cf.left_source_kind, cf.left_source_id,
       cf.right_source_kind, cf.right_source_id,
       cf.canonical_belief_version_id, cf.detected_at, cf.resolved_at
FROM conflicts AS cf
WHERE cf.tenant_id = $1 AND cf.user_id = $2
  AND cf.case_id = ANY($3::UUID[])
  AND (cf.status IN ('OPEN','NEEDS_HUMAN')
       OR cf.resolved_at > now() - INTERVAL '180 days')
ORDER BY (cf.status IN ('OPEN','NEEDS_HUMAN')) DESC,
         CASE cf.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
         cf.detected_at DESC
LIMIT 6
"""

#: F.4b — commitments, with the deterministic derivations the model must NOT
#: recompute. ``days_past_due`` is a ``date_trunc`` subtraction: it is the value
#: the landlord-deposit prospective trigger fires on, and a model that
#: "reasons" its way to 64 days past due is not the same artifact.
_F4_COMMITMENTS = """
SELECT cm.id AS commitment_id, cm.case_id, cm.commitment_type, cm.description,
       cm.currency, cm.committed_amount, cm.fulfilled_amount, cm.outstanding_amount,
       cm.due_at, cm.status, cm.revision,
       CASE WHEN cm.due_at IS NULL THEN NULL
            ELSE (date_trunc('day', now()) - date_trunc('day', cm.due_at))::INT / 86400
       END AS days_past_due
FROM commitments AS cm
WHERE cm.tenant_id = $1 AND cm.user_id = $2
  AND cm.case_id = ANY($3::UUID[])
  AND cm.status != 'SUPERSEDED'
ORDER BY (cm.status IN ('ACTIVE','PARTIAL','DISPUTED')) DESC,
         cm.outstanding_amount DESC NULLS LAST
LIMIT 6
"""

#: F.4c — recent canonical transitions: the "what changed lately" strip.
_F4_TRANSITIONS = """
SELECT st.case_id, st.case_revision, st.transition_type, st.from_state, st.to_state,
       st.reason_code, st.recorded_at
FROM state_transitions AS st
WHERE st.tenant_id = $1 AND st.user_id = $2
  AND st.case_id = ANY($3::UUID[])
ORDER BY st.case_id, st.case_revision DESC, st.recorded_at DESC
LIMIT 9
"""

#: The six, by the names section 11.1 gives them.
STATEMENTS: Final[dict[str, str]] = {
    "F1_CANONICAL_BELIEFS": _F1_CANONICAL_BELIEFS,
    "F2_GROUNDED_EVIDENCE": _F2_GROUNDED_EVIDENCE,
    "F3_LINEAGE": _F3_LINEAGE,
    "F4_CONFLICTS": _F4_CONFLICTS,
    "F4_COMMITMENTS": _F4_COMMITMENTS,
    "F4_TRANSITIONS": _F4_TRANSITIONS,
}

#: Spec parameter number -> the name it is bound by, per statement. Section
#: 11.1 numbers ``$1``, ``$2``, ``$3`` for the case-scoped statements and
#: ``$1``, ``$2``, ``$4``, ``$5`` for F.2 and F.3, because the numbering is
#: global across the stage rather than per statement. The numbers are kept as
#: printed so the transcription stays diffable against the spec; the *names*
#: are what the binders and psycopg use.
_PARAMETERS: Final[dict[str, dict[int, str]]] = {
    "F1_CANONICAL_BELIEFS": {1: "tenant_id", 2: "user_id", 3: "case_ids"},
    "F2_GROUNDED_EVIDENCE": {
        1: "tenant_id",
        2: "user_id",
        4: "belief_version_ids",
        5: "already_present",
    },
    "F3_LINEAGE": {1: "tenant_id", 2: "user_id", 4: "belief_ids"},
    "F4_CONFLICTS": {1: "tenant_id", 2: "user_id", 3: "case_ids"},
    "F4_COMMITMENTS": {1: "tenant_id", 2: "user_id", 3: "case_ids"},
    "F4_TRANSITIONS": {1: "tenant_id", 2: "user_id", 3: "case_ids"},
}

_PARAM = re.compile(r"\$(\d+)")

#: The tripwire section 13.3 layer 3 asks for, applied to the backstop.
#: An assertion at import, not a test: a build in which F.2 lost its lifecycle
#: predicate must not start. F.2 is the easiest statement in the system to
#: write without one, because reaching a row through an edge feels like a
#: lookup by id rather than like retrieval.
assert RETRACTION_PREDICATE in _F2_GROUNDED_EVIDENCE, (
    "the grounding backstop lost its retraction filter. A retracted item is "
    "frequently the CONTRADICTS edge that justifies why a belief version was "
    "superseded, so without this predicate a correction the user already made "
    "re-enters retrieval through the graph rather than through the vector index."
)

#: The limits are written as literals inside the statements and as named
#: constants beside them, because ``tests/retrieval/test_no_unscoped_sql.py``
#: refuses SQL built by f-string interpolation -- rightly: an f-string is how a
#: bound parameter becomes an inlined value, and one keystroke from there is
#: ``D-06-001``. Two copies of a number drift, so the drift is what is asserted.
assert f"LIMIT {GROUNDED_EVIDENCE_LIMIT}" in _F2_GROUNDED_EVIDENCE
assert f"rn <= {LINEAGE_DEPTH}" in _F3_LINEAGE
assert f"LIMIT {CONFLICT_LIMIT}" in _F4_CONFLICTS
assert f"LIMIT {COMMITMENT_LIMIT}" in _F4_COMMITMENTS
assert f"LIMIT {TRANSITION_LIMIT}" in _F4_TRANSITIONS


def parameter_names(statement: str) -> tuple[str, ...]:
    """The parameters *statement* binds, in spec order.

    Raises:
        KeyError: an unknown statement name. A typo must not silently yield an
            empty tuple, which would make the scope assertions vacuous.
    """
    mapping = _PARAMETERS[statement]
    return tuple(mapping[number] for number in sorted(mapping))


def render(statement: str) -> str:
    """The statement psycopg executes: ``$n`` rewritten to ``%(name)s``.

    Named rather than positional. F.2 mentions ``$1`` three times; a positional
    rendering therefore needs the caller to repeat ``tenant_id`` three times in
    the right places, and getting that wrong produces valid SQL that returns
    the wrong rows.
    """
    mapping = _PARAMETERS[statement]
    sql = STATEMENTS[statement]
    return _PARAM.sub(lambda match: f"%({mapping[int(match.group(1))]})s", sql)


# =============================================================================
# Binders
# =============================================================================


def expansion_cases(case_ids: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """At most :data:`MAX_EXPANSION_CASES`, in the order Stage G ranked them."""
    return tuple(case_ids[:MAX_EXPANSION_CASES])


def bind_case_scoped(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, case_ids: Sequence[uuid.UUID]
) -> dict[str, Any]:
    """Parameters for F.1 and F.4's three statements. One shape, four callers."""
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "case_ids": list(expansion_cases(case_ids)),
    }


def bind_grounded_evidence(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    belief_version_ids: Sequence[uuid.UUID],
    already_present: Sequence[uuid.UUID],
) -> dict[str, Any]:
    """Parameters for F.2.

    *already_present* is the Stage D/E evidence set. An empty exclusion is an
    empty **array**, never ``None``: ``NOT (ev.id = ANY(NULL))`` evaluates to
    ``NULL``, which is not ``TRUE``, which drops every row -- so the backstop
    would return nothing and the hero demo would fail with no error anywhere.
    """
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "belief_version_ids": list(belief_version_ids),
        "already_present": list(already_present),
    }


def bind_lineage(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, belief_ids: Sequence[uuid.UUID]
) -> dict[str, Any]:
    """Parameters for F.3. ``belief_ids``, not ``belief_version_ids``.

    A separate function from :func:`bind_grounded_evidence` on purpose: the two
    arrays are the same type and the two statements are valid SQL with either
    one bound in. The distinct names are the only thing between the two, so
    they are carried by distinct functions rather than by a shared one with a
    parameter.
    """
    return {"tenant_id": tenant_id, "user_id": user_id, "belief_ids": list(belief_ids)}


# =============================================================================
# grounding — the edges
# =============================================================================


@dataclass(frozen=True)
class GroundingEdge:
    """One ``belief_support`` row. *Why* the system believes something."""

    belief_version_id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    relation: str
    weight: float | None = None
    reason_code: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> GroundingEdge:
        """Build from a database row.

        Raises:
            KeyError: *row* has no ``relation``. A ``belief_versions`` row does
                not, and admitting one with a ``None`` relation would produce an
                edge that sorts last, renders as grounding, and says nothing --
                the exact conflation the vocabulary guard forbids.
        """
        if "relation" not in row:
            raise KeyError(
                "a grounding edge needs a `relation`; this row has none, which "
                "means it is a belief_versions (lineage) row rather than a "
                "belief_support (grounding) row. They are not interchangeable."
            )
        return cls(
            belief_version_id=row["belief_version_id"],
            source_kind=row["source_kind"],
            source_id=row["source_id"],
            relation=row["relation"],
            weight=row.get("weight"),
            reason_code=row.get("reason_code"),
        )


def order_edges(edges: Sequence[GroundingEdge]) -> list[GroundingEdge]:
    """``CONTRADICTS`` first, then by descending weight, ``NULLS LAST``.

    F.1's ``ORDER BY`` reproduced in process, because a client-side re-sort --
    by id, by insertion, by anything -- is a two-line change that silently
    reverses the priority the database was asked to apply.

    Raises:
        KeyError: an unrecognised relation. The three are closed; a fourth
            would sort somewhere arbitrary and be read as ranked.
    """
    return sorted(
        edges,
        key=lambda edge: (
            RELATION_ORDER[edge.relation],
            edge.weight is None,
            -(edge.weight or 0.0),
            str(edge.source_id),
        ),
    )


def truncate_edges(
    edges: Sequence[GroundingEdge], *, limit: int
) -> tuple[list[GroundingEdge], list[GroundingEdge]]:
    """``(kept, dropped)``. The truncation eats ``QUALIFIES``, then ``SUPPORTS``.

    Never the edge that says the system disagrees with itself -- which follows
    from the ordering rather than from a special case, so the two cannot drift
    apart.

    Raises:
        ValueError: *limit* is below one. An empty grounding list and "this
            belief has no grounding" are the same JSON and different facts: the
            first is a budget event and the second is an invariant violation,
            so the budget path may not manufacture one.
    """
    if limit < 1:
        raise ValueError(
            f"a grounding list may be truncated but never emptied (limit={limit}); "
            "at least one edge survives, because an empty list is indistinguishable "
            "from a belief that was never grounded at all"
        )
    ordered = order_edges(edges)
    return ordered[:limit], ordered[limit:]


# =============================================================================
# lineage — the chain
# =============================================================================


@dataclass(frozen=True)
class LineageStep:
    """One ``belief_versions`` row. *What the system believed before.*"""

    belief_id: uuid.UUID
    belief_version_id: uuid.UUID
    version_no: int
    epistemic_status: str
    recorded_at: datetime
    value_json: Any = None
    superseded_at: datetime | None = None
    supersession_reason_codes: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> LineageStep:
        """Build from an F.3 row.

        Raises:
            KeyError: *row* has no ``version_no``. A ``belief_support`` row does
                not, and a lineage entry without a version number cannot be
                ordered, which is the only thing lineage is for.
        """
        if "version_no" not in row:
            raise KeyError(
                "a lineage step needs a `version_no`; this row has none, which "
                "means it is a belief_support (grounding) row rather than a "
                "belief_versions (lineage) row. They are not interchangeable."
            )
        codes = row.get("supersession_reason_codes") or ()
        return cls(
            belief_id=row["belief_id"],
            belief_version_id=row["belief_version_id"],
            version_no=int(row["version_no"]),
            epistemic_status=row["epistemic_status"],
            recorded_at=row["recorded_at"],
            value_json=row.get("value_json"),
            superseded_at=row.get("superseded_at"),
            supersession_reason_codes=tuple(str(code) for code in codes),
        )


# =============================================================================
# The stage's output
# =============================================================================


@dataclass(frozen=True)
class StageFExpansion:
    """What Stage F adds to the candidate pool, in two fields under two names.

    ``grounding`` and ``lineage`` are the names ``StateProof`` uses, on purpose:
    the same two words mean the same two things at every layer, so a reader who
    learns them once in the UI can follow them down to the SQL.
    """

    grounding: tuple[GroundingEdge, ...] = ()
    lineage: tuple[LineageStep, ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    commitments: tuple[Mapping[str, Any], ...] = ()
    transitions: tuple[Mapping[str, Any], ...] = ()

    def merged(self) -> NoReturn:
        """Refuse. This method exists only to be the thing that says no.

        It is here rather than absent because the merge is what a well-meaning
        simplification looks like from inside a diff, and a refusal at the point
        of the change is worth more than a paragraph three files away.
        """
        raise GroundingLineageMergeError(
            "grounding and lineage are two fields under two names and are never "
            "merged. grounding is the belief_support edge set and answers 'why "
            "do you believe this'; lineage is the belief_versions chain and "
            "answers 'what did you believe before, and what changed your mind'. "
            "A changelog is not an argument."
        )


# =============================================================================
# Feeding Stage G
# =============================================================================


def expansion_candidate(
    row: Mapping[str, Any],
    *,
    case_status: str | None,
    temporal_gap_days: float = 0.0,
    flag_temporal_overlap: bool = False,
) -> Candidate:
    """One F.2 row as a Stage G candidate: tier ``T2``, no vector contribution.

    Section 11.1: evidence pulled in by F.2 enters "with ``tier =
    T2_GROUNDING_EXPANSION``, ``cosine_similarity`` recorded as ``None``, and a
    ``vector_feature`` of 0.0".

    ``None`` rather than ``0.0`` for the similarity, and the distinction is the
    honest one: this item was never scored against the query vector at all.
    Recording 0.0 would make it indistinguishable from a document the embedding
    space says is orthogonal to the query, and the Memory Trace would show a
    measurement that was never taken.

    ``match_strength`` is 0.0 and every identity flag is false. The item arrived
    through the graph, not through an identifier; awarding it identity credit
    here would let Stage F promote a candidate into ``T0`` and reintroduce
    adjudication-by-accumulation through a side door.

    ``QUALIFIES`` counts as grounding for tier purposes. It is a
    ``belief_support`` row like the other two, and leaving it out would drop a
    qualifying edge to ``T3_VECTOR_ONLY`` -- where it would compete for the two
    reserved novel-evidence slots against evidence that genuinely is novel.
    """
    relation = str(row["relation"])
    if relation not in RELATION_ORDER:
        raise KeyError(
            f"{relation!r} is not one of the three belief_support relations "
            f"{sorted(RELATION_ORDER)}; a fourth would be scored as grounding "
            "without anybody deciding what it means"
        )
    version_id = str(row["belief_version_id"])
    contradicts = (version_id,) if relation == "CONTRADICTS" else ()
    grounds = () if relation == "CONTRADICTS" else (version_id,)

    return Candidate(
        match_strength=0.0,
        cosine_similarity=None,
        source_authority=row.get("source_authority"),
        case_status=case_status,
        flag_temporal_overlap=flag_temporal_overlap,
        grounds_belief_version_ids=grounds,
        contradicts_belief_version_ids=contradicts,
        observed_at=row.get("observed_at"),
        temporal_gap_days=temporal_gap_days,
        evidence_id=str(row["evidence_id"]),
        notes=("GROUNDING_EXPANSION",),
    )
