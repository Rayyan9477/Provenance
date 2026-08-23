"""State Proof — grounding and lineage, assembled from committed rows (``T5.3``).

Authority
---------
- ``docs/specs/11_CONTRACTS.md`` section 14, and
  ``provenance_contracts.proof``, which implements it.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 2.3 and ``T5.3``.
- ``docs/specs/10_DATABASE_DDL.md`` sections 6.3 (``belief_versions``) and 6.4
  (``belief_support``).
- ``docs/quality/23_PHASE_GATES.md`` ``G5.1``-``G5.5``.

Two fields, two tables, two questions
--------------------------------------
**grounding** is the ``belief_support`` edge set -- ``SUPPORTS`` /
``CONTRADICTS`` / ``QUALIFIES`` -- and it answers *why do you believe this*.
**lineage** is the ``belief_versions`` supersession chain with its reason codes,
and it answers *what did you believe before, and what changed your mind*.

They are loaded by two functions, from two tables, into two fields under
exactly those two names. :class:`GroundingLineageMergeError` exists because the
merge is a *plausible* refactor: both are "the history of this belief", both
render as a list under a heading, and a reviewer skimming a diff would not stop
it. What it destroys is the product: a user disputing a charge needs to show the
counterparty the evidence, and a system that hands them a version history
instead has answered a different question with total confidence.

No model, and no way to add one
--------------------------------
Everything here is a projection of committed rows. The module imports nothing
from ``agents/`` and nothing from a Bedrock client, which
``tests/retrieval/test_no_unscoped_sql.py`` asserts against the import graph and
``G5.1`` asserts dynamically by putting an ``ExplodingClient`` in the
environment. Two mechanisms, because the structural one keeps holding when
nobody remembers to set the variable and the dynamic one keeps holding when
somebody adds an indirection the AST scan cannot follow.

Retracted evidence is *shown* here and *excluded* from retrieval
-----------------------------------------------------------------
Those are different questions and this module answers the first. A retracted
invoice is frequently the ``CONTRADICTS`` edge that explains why a belief moved;
hiding it would defeat the purpose of keeping lineage at all. Retrieval's job
is to keep it out of a *new* prompt, which is
``services/control_plane/app/retrieval/predicates.py``.

The ``PV_SABOTAGE`` hook
-------------------------
``G5.5`` runs ``PV_SABOTAGE=read_models.state_proof.load_grounding`` and
requires the snapshot suite to go red. The module label registered here is
``state_proof.builder`` -- the package this task is permitted to write is
``app/state_proof/``, not ``app/read_models/``. The discrepancy is reported
rather than resolved by inventing the other package.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from provenance_contracts.proof import (
    BeliefProof,
    BeliefVersionProof,
    CaseSnapshot,
    EvidenceProof,
    GroundingEdgeProof,
    LineageEntry,
    StateProof,
    StateTransitionProof,
)
from provenance_domain import money
from provenance_domain.enums import MemoryMode, SupportSourceKind

__all__ = [
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "GroundingLineageMergeError",
    "MissingEvidenceError",
    "build_belief_proof",
    "build_state_proof",
    "load_grounding",
    "load_lineage",
    "scalar_paths",
]

Row = Mapping[str, Any]


class GroundingLineageMergeError(TypeError):
    """Someone tried to render grounding and lineage as one list.

    A changelog is not an argument, and an argument is not a changelog.
    ``70_TASK_PLAN.md`` section 2.3: "Any task whose output conflates the two
    is rejected at review." This makes the rejection mechanical.
    """


class MissingEvidenceError(LookupError):
    """An ``EVIDENCE`` grounding edge had no evidence row to render.

    A proof that names an identifier in place of the observation is asking the
    reader to take its word for it, which is the one thing a proof may not do.
    """


def load_grounding(
    support_rows: Iterable[Row], evidence_rows: Mapping[uuid.UUID, Row]
) -> tuple[GroundingEdgeProof, ...]:
    """The ``belief_support`` edges for one belief version, rendered.

    This is the ``PV_SABOTAGE`` symbol. It is reached through the module global
    by :func:`build_belief_proof` -- never through a ``from``-import, which
    would copy the reference before the rebind is visible and make the sabotage
    silently never arrive.

    Ordering is ``CONTRADICTS`` first, then ``SUPPORTS``, then ``QUALIFIES``,
    and by descending weight within each. Not cosmetic: the contradiction is
    the thing the user needs to see first, and a proof that buries it under
    four supporting documents has technically disclosed it.

    Raises:
        MissingEvidenceError: an ``EVIDENCE`` edge whose source has no row.
    """
    order = {"CONTRADICTS": 0, "SUPPORTS": 1, "QUALIFIES": 2}
    edges: list[GroundingEdgeProof] = []
    for row in support_rows:
        source_kind = SupportSourceKind(row["source_kind"])
        evidence: EvidenceProof | None = None
        if source_kind is SupportSourceKind.EVIDENCE:
            source_id = row["source_id"]
            if source_id not in evidence_rows:
                raise MissingEvidenceError(
                    f"grounding edge {row['support_id']} cites evidence {source_id} "
                    "which was not loaded; a proof that names an identifier in "
                    "place of the observation is not a proof"
                )
            evidence = EvidenceProof(**dict(evidence_rows[source_id]))
        edges.append(
            GroundingEdgeProof(
                support_id=row["support_id"],
                source_kind=source_kind,
                source_id=row["source_id"],
                relation=row["relation"],
                weight=row.get("weight"),
                reason_code=row.get("reason_code"),
                evidence=evidence,
                claim_summary=row.get("claim_summary"),
            )
        )
    return tuple(
        sorted(edges, key=lambda e: (order[str(e.relation)], -(e.weight or 0.0), str(e.support_id)))
    )


def load_lineage(version_rows: Iterable[Row]) -> tuple[LineageEntry, ...]:
    """The ``belief_versions`` chain, oldest first, each with its reason code.

    Oldest first because that is the order a person reads a history in, and
    because ``BeliefProof`` validates ``version_no`` ascending. Section 14
    prints the reverse; the contract module records that deviation and the
    reason, and this follows the contract.
    """
    entries = [
        LineageEntry(
            belief_version_id=row["belief_version_id"],
            version_no=row["version_no"],
            value_json=row["value_json"],
            epistemic_status=row["epistemic_status"],
            valid_from=row.get("valid_from"),
            valid_to=row.get("valid_to"),
            recorded_at=row["recorded_at"],
            superseded_at=row.get("superseded_at"),
            superseded_by_version_id=row.get("superseded_by_version_id"),
            superseded_by_version_no=row.get("superseded_by_version_no"),
            supersession_reason_code=row.get("supersession_reason_code"),
            kernel_decision_id=row["kernel_decision_id"],
        )
        for row in version_rows
    ]
    return tuple(sorted(entries, key=lambda entry: entry.version_no))


def build_belief_proof(
    *,
    belief_row: Row,
    version_rows: Sequence[Row],
    support_rows: Sequence[Row],
    evidence_rows: Mapping[uuid.UUID, Row],
    merge_grounding_and_lineage: bool = False,
) -> BeliefProof:
    """One proposition, its current version, its grounding, and its lineage.

    *merge_grounding_and_lineage* is a parameter that exists only to be
    refused. It is here because the merge is what a well-meaning
    simplification looks like from inside a diff, and a refusal at the point of
    the change is worth more than a paragraph three files away.
    """
    if merge_grounding_and_lineage:
        raise GroundingLineageMergeError(
            "grounding and lineage are two fields under two names and are never "
            "merged. grounding is the belief_support edge set and answers 'why "
            "do you believe this'; lineage is the belief_versions chain and "
            "answers 'what did you believe before, and what changed your mind'. "
            "A changelog is not an argument."
        )

    lineage = load_lineage(version_rows)
    if not lineage:
        raise ValueError("a belief proof needs at least one version to render")
    current_row = max(version_rows, key=lambda row: int(row["version_no"]))

    # Reached through the module global on purpose: PV_SABOTAGE rebinds the
    # attribute on the module object, and a local alias would keep calling the
    # original while `make sabotage` reported green.
    grounding = load_grounding(support_rows, evidence_rows)

    return BeliefProof(
        belief_id=belief_row["belief_id"],
        subject_type=belief_row["subject_type"],
        subject_id=belief_row["subject_id"],
        subject_label=belief_row["subject_label"],
        predicate=belief_row["predicate"],
        current_version=BeliefVersionProof(
            belief_version_id=current_row["belief_version_id"],
            version_no=current_row["version_no"],
            value_type=current_row["value_type"],
            value_json=current_row["value_json"],
            epistemic_status=current_row["epistemic_status"],
            belief_confidence=current_row["belief_confidence"],
            valid_from=current_row.get("valid_from"),
            valid_to=current_row.get("valid_to"),
            recorded_at=current_row["recorded_at"],
            kernel_decision_id=current_row["kernel_decision_id"],
        ),
        grounding=grounding,
        lineage=lineage,
        derivation=belief_row.get("derivation"),
    )


def build_state_proof(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    generated_at: Any,
    case_row: Row | None,
    belief_proofs: Sequence[BeliefProof] = (),
    transition_rows: Sequence[Row] = (),
    memory_mode: MemoryMode = MemoryMode.ON,
    memory_disabled_reason: str | None = None,
    proof_id: uuid.UUID | None = None,
) -> StateProof:
    """Everything Provenance can justify about one case, at one revision.

    The ``MemoryMode.OFF`` path is a real, valid, **empty** proof with a stated
    reason. Judge Mode runs the identical artifact twice, and the comparison is
    only a comparison if the OFF side is genuinely empty -- which
    ``StateProof`` enforces rather than trusts, because a leaked belief would
    make the counterfactual a demonstration of nothing while looking exactly
    like a demonstration of something.
    """
    return StateProof(
        proof_id=proof_id or uuid.uuid4(),
        generated_at=generated_at,
        tenant_id=tenant_id,
        user_id=user_id,
        memory_mode=memory_mode,
        case=CaseSnapshot(**dict(case_row)) if case_row is not None else None,
        beliefs=tuple(belief_proofs),
        transitions=tuple(StateTransitionProof(**dict(row)) for row in transition_rows),
        memory_disabled_reason=memory_disabled_reason,
    )


def scalar_paths(payload: Any, prefix: str = "") -> list[str]:
    """Every dotted path to a scalar in a serialised proof. ``G5.4``'s scan.

    The gate greps the HTTP response body; this walks the object that becomes
    it, so a chain-of-thought field is caught in the unit lane by the task that
    introduces it rather than by a ``curl`` at gate time.
    """
    if isinstance(payload, dict):
        out: list[str] = []
        for key, value in payload.items():
            out.extend(scalar_paths(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(payload, list):
        out = []
        for index, value in enumerate(payload):
            out.extend(scalar_paths(value, f"{prefix}.{index}"))
        return out
    return [prefix]


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `23_PHASE_GATES.md` G5.5 addresses the symbol as
# `read_models.state_proof.load_grounding`. The package this task may write is
# `services/control_plane/app/state_proof/`, so the label registered here is
# `state_proof.builder`. Reported as a discrepancy; inventing a `read_models`
# package to match the gate string would put the builder somewhere the task
# plan does not name and would not make the gate command work either.

#: The label `tests/sabotage_matrix.yaml` and `G5.5` use for this module.
SABOTAGE_MODULE: Final[str] = "state_proof.builder"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("load_grounding",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
