"""The conditional Resolver — a reasoning subgraph, not a third agent persona.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 2, 5.7, 5.8
  and 11.
- ``docs/specs/14_PROMPTS.md`` section 4 and section 10 rows A6, A9, A14.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.4.

Section 2 is explicit: "The Resolver is a conditional reasoning node/subgraph,
not a third always-running agent persona." So this module holds the resolver's
*tool surface* and its *deterministic guards*, and the node itself stays in
``agents/runtime/nodes/ingestion.py`` where the eleven-node topology can see it.
Adding a resolver graph with its own node list would have been the eighth node
the task plan forbids, wearing a different hat.

Read-only, structurally
-----------------------
:class:`ResolverTools` names the resolver's entire capability: three reads over
the agent-safe views. There is no write method, so "the resolver proposes
readings, never mutations" is a property of the protocol rather than a sentence
in a prompt. The SQL grants say the same thing and they are the real boundary --
``pv_agent_reader`` holds ``SELECT`` on five ``_v1`` views and nothing else --
but a reader of this file should not have to go and check the grants to know
what the resolver can do.

What the guards are for
-----------------------
A Tier R model can return a well-formed assessment that is nonetheless
unusable: it can cite a row it was never shown (row A6), claim an authority the
sender asserted about itself (row A9), or reach for a case belonging to another
user (row A14). :func:`guard_assessment` is the deterministic check for all
three. It runs on the model's output, before the proposal builder sees it, and
it never repairs -- an assessment that reaches outside its own context is not a
formatting problem.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

from agents.runtime.schemas.validation import ValidationFailure
from provenance_contracts.resolution import ResolutionAssessment
from provenance_contracts.retrieval import RetrievalContext
from provenance_domain.enums import AgentSafeView, SourceClass
from provenance_domain.invariants import HUMAN_REVIEW_CONFIDENCE_FLOOR

__all__ = [
    "RESOLVER_CODES",
    "SELF_DECLARED_AUTHORITY_CLASSES",
    "ResolverTools",
    "guard_assessment",
    "visible_ids",
]

#: Source classes a message may never assign to itself. ``14_PROMPTS.md``
#: section 4.1 rule 5 -- "source authority is not your confidence" -- and
#: adversarial row A9, where an email claims to be a signed agreement. Source
#: class is determined from the source *kind* by deterministic configuration;
#: a self-declaration is evidence of an attempt, not an authority grant.
SELF_DECLARED_AUTHORITY_CLASSES: Final[frozenset[SourceClass]] = frozenset(
    {
        SourceClass.SIGNED_AGREEMENT,
        SourceClass.OFFICIAL_POLICY_DOC,
        SourceClass.BANK_OR_CARD_STATEMENT,
        SourceClass.PAYMENT_PROCESSOR_RECORD,
    }
)

#: Codes :func:`guard_assessment` can emit.
RESOLVER_CODES: Final[frozenset[str]] = frozenset(
    {
        "RESOLVED_ID_NOT_IN_CONTEXT",
        "SUPERSESSION_AUTHORITY_SELF_DECLARED",
        "SUPERSESSION_BELOW_REVIEW_FLOOR",
        "RELATION_TARGET_NOT_IN_CONTEXT",
    }
)


@runtime_checkable
class ResolverTools(Protocol):
    """The resolver's entire capability: three reads over agent-safe views.

    Every method returns rows the connection's SQL role is already permitted to
    read. There is deliberately no write method and no free-text SQL parameter,
    so an injected ``UPDATE cases SET status='RESOLVED'`` (adversarial row A8)
    has nothing to call and nothing to be interpolated into.
    """

    def get_case_context(self, case_id: uuid.UUID) -> Sequence[object]: ...

    def get_active_beliefs(self, case_id: uuid.UUID) -> Sequence[object]: ...

    def get_belief_lineage(self, belief_id: uuid.UUID) -> Sequence[object]: ...

    @property
    def views(self) -> frozenset[AgentSafeView]: ...


def visible_ids(context: RetrievalContext) -> frozenset[uuid.UUID]:
    """Every id the resolver was actually shown.

    An assessment may reference these and nothing else. This is the set that
    turns "no id appears that was not supplied" from a prompt instruction into
    a check, which matters because the instruction is exactly what adversarial
    row A6 tries to talk the model out of.
    """
    ids: set[uuid.UUID] = set()
    for candidate in (*context.relationship_candidates, *context.case_candidates):
        ids.add(candidate.candidate_id)
    for snippet in context.evidence_snippets:
        ids.add(snippet.evidence_id)
        ids.add(snippet.artifact_id)
    for belief in context.canonical_beliefs:
        ids.add(belief.belief_id)
        ids.add(belief.belief_version_id)
        ids.add(belief.subject_id)
    for conflict in context.active_conflicts:
        ids.add(conflict.conflict_id)
    for commitment in context.active_commitments:
        ids.add(commitment.commitment_id)
    for fact in context.temporal_facts:
        if fact.source_evidence_id is not None:
            ids.add(fact.source_evidence_id)
    return frozenset(ids)


def guard_assessment(
    assessment: ResolutionAssessment, *, context: RetrievalContext
) -> tuple[ValidationFailure, ...]:
    """Deterministic checks on an advisory assessment. Never repaired.

    Returns every failure rather than the first: an assessment that both cites
    an unknown id and claims a self-declared authority has two problems, and
    reporting one of them would understate what happened.
    """
    failures: list[ValidationFailure] = []
    visible = visible_ids(context)

    for name, value in (
        ("identity.relationship_id", assessment.identity.relationship_id),
        ("identity.case_id", assessment.identity.case_id),
    ):
        if value is not None and value not in visible:
            failures.append(
                ValidationFailure(
                    path=name,
                    code="RESOLVED_ID_NOT_IN_CONTEXT",
                    detail=(
                        f"{value} was never shown to the resolver; retrieval is user-scoped "
                        "and an id from outside it is either fabricated or foreign"
                    ),
                )
            )

    for index, relation in enumerate(assessment.semantic_relations):
        if relation.target_id not in visible:
            failures.append(
                ValidationFailure(
                    path=f"semantic_relations[{index}].target_id",
                    code="RELATION_TARGET_NOT_IN_CONTEXT",
                    detail=f"{relation.target_id} is not in the supplied context",
                )
            )
        if relation.source_id is not None and relation.source_id not in visible:
            failures.append(
                ValidationFailure(
                    path=f"semantic_relations[{index}].source_id",
                    code="RELATION_TARGET_NOT_IN_CONTEXT",
                    detail=f"{relation.source_id} is not in the supplied context",
                )
            )

    for index, supersession in enumerate(assessment.proposed_supersessions):
        if supersession.target_id not in visible:
            failures.append(
                ValidationFailure(
                    path=f"proposed_supersessions[{index}].target_id",
                    code="RESOLVED_ID_NOT_IN_CONTEXT",
                    detail=f"{supersession.target_id} is not in the supplied context",
                )
            )
        if supersession.authority_basis in SELF_DECLARED_AUTHORITY_CLASSES and not any(
            snippet.evidence_id == supersession.superseding_source_id
            for snippet in context.evidence_snippets
        ):
            failures.append(
                ValidationFailure(
                    path=f"proposed_supersessions[{index}].authority_basis",
                    code="SUPERSESSION_AUTHORITY_SELF_DECLARED",
                    detail=(
                        f"{supersession.authority_basis} was claimed for a source Provenance "
                        "did not classify that way; an email cannot make itself a signed "
                        "agreement by saying so"
                    ),
                )
            )
        if supersession.confidence < HUMAN_REVIEW_CONFIDENCE_FLOOR:
            failures.append(
                ValidationFailure(
                    path=f"proposed_supersessions[{index}].confidence",
                    code="SUPERSESSION_BELOW_REVIEW_FLOOR",
                    detail=(
                        f"{supersession.confidence} is below the {HUMAN_REVIEW_CONFIDENCE_FLOOR} "
                        "human-review floor; closing out a prior version is not a coin flip"
                    ),
                )
            )

    return tuple(failures)
