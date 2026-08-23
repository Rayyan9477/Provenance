"""The one serializable transaction, in DDL section 13 statement order — T4.10.

Authority
---------
``specs/10_DATABASE_DDL.md`` **section 13**, read as *ordering* rather than as a
suggestion, and ``specs/12_KERNEL_ALGORITHMS.md`` section 7.
``CANONICAL_DECISIONS.md`` -> *Transaction isolation* and -> *Kernel retry
exhaustion* are binding.

The four rules this module makes true
-------------------------------------
1. **The Memory Kernel is the sole canonical writer.** Every INSERT and UPDATE
   against a canonical table in this repository is in this file.
   ``tools/write_path_lint`` checks that claim against the AST of
   ``services/``, ``packages/``, ``workers/`` and ``agents/``.
2. **One serializable transaction, DDL section 13 order, ``40001`` retry, and no
   network call inside the callback.** The retry loop is
   ``provenance_db.retry.run_in_serializable_tx`` - the only one in the
   repository, proven against a real two-connection interleaving. There is no
   second loop here, no backoff arithmetic and no SQLSTATE classification;
   ``tools/txn_purity_lint`` walks this callback for banned constructs.
3. **Retry exhaustion performs no side effect and enqueues nothing.** No kernel
   retry queue exists, the control-plane task role carries no ``sqs:*``
   permission, and re-drive is the caller's job over ``503`` + ``Retry-After``.
4. **A ``kernel_decisions`` row for every outcome**, rejections and NOOPs
   included, with ``transaction_opened = false`` on a preflight rejection.

Why the order matters, concretely
---------------------------------
Foreign keys are validated at statement time. ``belief_versions`` and
``state_transitions`` both carry a NOT NULL ``kernel_decision_id`` foreign key,
so the decision row goes first. The outbox row's ``aggregate_version`` is the
**post**-increment revision, so the ``cases`` UPDATE goes before it; writing the
outbox first produces a plausible-looking row with the wrong version, and
nothing downstream can tell.

Why every statement is labelled
-------------------------------
:func:`apply_write_plan` returns the labels it executed, in order, and
:data:`STATEMENT_ORDER` declares the only order they may appear in. A test
compares the two. Without that, "the order is the specification" is a comment.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from psycopg import errors as pgerr
from psycopg.types.json import Json

from provenance_contracts.base import Money
from provenance_contracts.kernel import (
    BeliefVersionRef,
    CommitmentChange,
    ConflictRef,
    KernelCommitResult,
    StateTransitionRef,
    TriggerChange,
)
from provenance_contracts.proposal import MemoryProposal
from provenance_db.retry import (
    RetryExhausted,
    TelemetrySink,
    TxPool,
    UniqueViolationOutcome,
    in_transaction,
    map_unique_violation,
    run_in_serializable_tx,
)
from provenance_domain.enums import (
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    EpistemicStatus,
    FulfillmentAdmissionStatus,
    KernelDecision,
    KernelReasonCode,
    RetractionStatus,
    SubjectType,
    TriggerState,
)
from services.control_plane.app.memory_kernel import (
    case_ops,
    decisions,
    families,
    money_ops,
    pipeline,
    preflight,
)
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig

__all__ = [
    "CANONICAL_WRITE_STATEMENTS",
    "OUTBOX_INSERT_SQL",
    "STATEMENT_ORDER",
    "STATE_TRANSITION_INSERT_SQL",
    "CommitContext",
    "apply_write_plan",
    "commit_proposal",
    "decision_params",
    "mapped_unique_violation",
    "retry_exhausted_result",
]


def mapped_unique_violation(constraint: str | None) -> UniqueViolationOutcome:
    """Section 7.5's outcome for *constraint*.

    A thin pass-through, and it used to be more.

    Section 7.5's table keyed on POSTGRES AUTO-GENERATED constraint names
    (``fulfillments_commitment_evidence_key``) while migrations 0001-0008 declare
    EXPLICIT ``uq_*`` names, which is what ``diag.constraint_name`` returns. Not
    one of its eight keys existed in the built schema, so every ``23505`` fell
    through to ``REJECTED_INVARIANT``. This module carried a `CONSTRAINT_ALIASES`
    translation layer to bridge that, plus a `SCHEMA_ONLY_VIOLATIONS` table for
    constraints section 7.5 omitted.

    The map has since been corrected at source, in
    ``provenance_db.retry.UNIQUE_VIOLATION_MAP``, so both tables were removed:
    two layers were doing one job, and the outer one existed only to compensate
    for a defect in the inner one. A workaround kept after its cause is fixed
    becomes a second place the truth lives.

    The wrapper stays because the Kernel should not import the mapping directly
    -- a later phase may need to narrow an outcome without touching
    ``provenance_db`` -- and because an unknown constraint must still fail
    closed: "this row already existed" and "this write was wrong" have opposite
    consequences, and guessing between them is how a duplicate becomes a
    silently-swallowed invariant breach.
    """
    return map_unique_violation(constraint)


#: DDL section 13, in the order that document prints it. ``read_case`` is
#: statement 1; the rest follow. A label the executor emits that is not here is
#: a statement nobody reviewed, and a test asserts that set difference is empty.
STATEMENT_ORDER: Final[tuple[str, ...]] = (
    "read_case",
    "kernel_decisions",
    "claims",
    "belief_versions",
    "belief_support",
    "beliefs_pointer",
    "belief_versions_supersede",
    "conflicts",
    # DDL section 13 step 6 is headed "Commitments and fulfillments" and its
    # template prints only the UPDATE, because no statement recorded a new
    # obligation. The INSERT goes at the head of that step and not later:
    # `fk_fulfillments_commitment` is validated at statement time, so a
    # fulfillment against an obligation this same commit opened would fail
    # against a row that does not exist yet.
    "commitments_insert",
    "fulfillments",
    "commitments",
    "cases",
    "state_transitions",
    "prospective_triggers",
    "memory_proposals",
    "outbox_events",
)

#: Every canonical write statement in the repository, named.
#:
#: ``python -m tools.write_path_lint`` counts them; this tuple says *which ones*
#: it counted, so the number stays a claim about specific statements rather than
#: a magic constant that nobody can check when it moves.
#: ``tests/kernel/test_obligations.py`` runs the linter and compares the two.
#:
#: The count was 14 before ``commitments_insert`` and the two
#: ``prospective_triggers`` statements landed, and 17 before prospective memory
#: got a production path. It is 19 now: ``trigger_commit.py`` adds the
#: ``memory_proposals`` INSERT the Kernel needs to author its own deterministic
#: proposal and the ``prospective_triggers`` UPDATE that settles one wake. Both
#: are in this tuple, and the two entries carry that module's symbol names so a
#: reader can find them; the other four statements a trigger fire issues are
#: this module's own, reused rather than re-declared.
CANONICAL_WRITE_STATEMENTS: Final[tuple[str, ...]] = (
    "kernel_decisions INSERT      (decisions.DECISION_INSERT_SQL)",
    "claims INSERT               (_CLAIM_SQL)",
    "beliefs INSERT              (_BELIEF_SQL)",
    "belief_versions INSERT      (_BELIEF_VERSION_SQL)",
    "belief_support INSERT       (_BELIEF_SUPPORT_SQL)",
    "beliefs UPDATE              (_BELIEF_POINTER_SQL)",
    "belief_versions UPDATE      (_SUPERSEDE_SQL)",
    "conflicts INSERT            (_CONFLICT_SQL)",
    "commitments INSERT          (_COMMITMENT_INSERT_SQL)",
    "fulfillments INSERT         (_FULFILLMENT_SQL)",
    "commitments UPDATE          (_COMMITMENT_SQL)",
    "cases UPDATE                (case_ops.CASE_UPDATE_SQL)",
    "state_transitions INSERT    (_STATE_TRANSITION_SQL)",
    "prospective_triggers INSERT (_TRIGGER_ARM_SQL)",
    "prospective_triggers UPDATE (_TRIGGER_DISARM_SQL)",
    "memory_proposals UPDATE     (_PROPOSAL_SQL)",
    "outbox_events INSERT        (_OUTBOX_SQL)",
    "memory_proposals INSERT     (_TRIGGER_PROPOSAL_SQL)",
    "prospective_triggers UPDATE (_TRIGGER_SETTLE_SQL)",
)


@dataclass(frozen=True, slots=True)
class CommitContext:
    """The identities one commit is scoped to.

    Deliberately four fields and no plan, no snapshot and no revision: rule 2 of
    section 7.3 requires fresh reads on every attempt, and a context that could
    carry derived state is a context a retry could replay a rolled-back
    snapshot from.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    proposal_id: uuid.UUID
    trace_id: uuid.UUID


# ---------------------------------------------------------------------------
# The statements. One per canonical table, all of them here and nowhere else.
# ---------------------------------------------------------------------------

_CLAIM_SQL = """
INSERT INTO claims (
    id, tenant_id, user_id, case_id, relationship_id, subject_type, subject_id,
    predicate, object_type, object_json, actor_type, actor_id, evidence_id,
    claim_kind, valid_from, valid_to, authority_score, extraction_confidence,
    recorded_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(relationship_id)s,
    %(subject_type)s, %(subject_id)s, %(predicate)s, %(object_type)s,
    %(object_json)s, %(actor_type)s, %(actor_id)s, %(evidence_id)s,
    %(claim_kind)s, %(valid_from)s, %(valid_to)s, %(authority_score)s,
    %(extraction_confidence)s, %(recorded_at)s
)
"""

_BELIEF_SQL = """
INSERT INTO beliefs (id, tenant_id, user_id, case_id, subject_type, subject_id, predicate)
VALUES (%(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(subject_type)s,
        %(subject_id)s, %(predicate)s)
"""

_BELIEF_VERSION_SQL = """
INSERT INTO belief_versions (
    id, tenant_id, user_id, belief_id, version_no, value_type, value_json,
    epistemic_status, belief_confidence, derivation_kind, support_edge_count,
    supersedes_version_id, supersession_reason_code, valid_from, valid_to,
    recorded_at, kernel_decision_id
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(belief_id)s, %(version_no)s, %(value_type)s,
    %(value_json)s, %(epistemic_status)s, %(belief_confidence)s, %(derivation_kind)s,
    %(support_edge_count)s, %(supersedes_version_id)s, %(supersession_reason_code)s,
    %(valid_from)s, %(valid_to)s, %(recorded_at)s, %(kernel_decision_id)s
)
"""

_BELIEF_SUPPORT_SQL = """
INSERT INTO belief_support (
    id, tenant_id, user_id, belief_version_id, source_kind, source_id, relation,
    weight, reason_code
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(belief_version_id)s, %(source_kind)s,
    %(source_id)s, %(relation)s, %(weight)s, %(reason_code)s
)
"""

_BELIEF_POINTER_SQL = """
UPDATE beliefs SET current_version_id = %(version_id)s, updated_at = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(belief_id)s
"""

_SUPERSEDE_SQL = """
UPDATE belief_versions
   SET epistemic_status = 'SUPERSEDED', superseded_at = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(version_id)s
"""

_CONFLICT_SQL = """
INSERT INTO conflicts (
    id, tenant_id, user_id, case_id, subject_type, subject_id, predicate,
    left_source_kind, left_source_id, right_source_kind, right_source_id,
    conflict_type, status, severity, requires_human, canonical_belief_version_id,
    resolution_reason_code, resolution_notes, detected_at, resolved_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(subject_type)s, %(subject_id)s,
    %(predicate)s, %(left_source_kind)s, %(left_source_id)s, %(right_source_kind)s,
    %(right_source_id)s, %(conflict_type)s, %(status)s, %(severity)s,
    %(requires_human)s, %(canonical_belief_version_id)s, %(resolution_reason_code)s,
    %(resolution_notes)s, %(detected_at)s, %(resolved_at)s
)
"""

#: A new obligation opens with all three amounts or none (M2) and an
#: ``outstanding`` computed by the money identity rather than copied from
#: ``committed``. ``source_claim_id`` is NOT NULL and foreign-keyed: an
#: obligation nothing said is not an obligation this product will store.
_COMMITMENT_INSERT_SQL = """
INSERT INTO commitments (
    id, tenant_id, user_id, case_id, obligor_type, obligor_id, beneficiary_type,
    beneficiary_id, commitment_type, description, currency, committed_amount,
    fulfilled_amount, outstanding_amount, due_at, condition_ast, source_claim_id,
    status, revision, valid_from, valid_to, created_at, updated_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(obligor_type)s, %(obligor_id)s,
    %(beneficiary_type)s, %(beneficiary_id)s, %(commitment_type)s, %(description)s,
    %(currency)s, %(committed_amount)s, %(fulfilled_amount)s, %(outstanding_amount)s,
    %(due_at)s, %(condition_ast)s, %(source_claim_id)s, %(status)s, %(revision)s,
    %(valid_from)s, %(valid_to)s, %(tx_now)s, %(tx_now)s
)
"""

_FULFILLMENT_SQL = """
INSERT INTO fulfillments (
    id, tenant_id, user_id, commitment_id, evidence_id, currency, amount, quantity,
    fulfilled_at, admission_status, confidence
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(commitment_id)s, %(evidence_id)s,
    %(currency)s, %(amount)s, %(quantity)s, %(fulfilled_at)s, %(admission_status)s,
    %(confidence)s
)
"""

#: Recompute, never increment. ``fulfilled_amount`` and ``outstanding_amount``
#: are both written from the recomputed ledger sum; ``ck_commitments_outstanding_identity``
#: is the backstop, and the derivation is the source.
_COMMITMENT_SQL = """
UPDATE commitments
   SET fulfilled_amount   = %(fulfilled_after)s,
       outstanding_amount = %(outstanding_after)s,
       status             = %(status_after)s,
       revision           = revision + 1,
       updated_at         = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(commitment_id)s
   AND revision = %(revision_before)s
"""

_STATE_TRANSITION_SQL = """
INSERT INTO state_transitions (
    id, tenant_id, user_id, case_id, case_revision, transition_type, subject_kind,
    subject_id, from_state, to_state, reason_code, proposal_id, kernel_decision_id,
    trace_id, recorded_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(case_revision)s,
    %(transition_type)s, %(subject_kind)s, %(subject_id)s, %(from_state)s,
    %(to_state)s, %(reason_code)s, %(proposal_id)s, %(kernel_decision_id)s,
    %(trace_id)s, %(recorded_at)s
)
"""

#: ``evaluation_version`` is stated rather than left to the column's
#: ``DEFAULT 0``: ``16_TRIGGER_DSL.md`` section 9.1's INSERT writes ``1`` for a
#: fresh arm and section 9.3 stamps ``schedule_name`` with it, and the schedule
#: name is the wake identity and the idempotency key -- so a row whose
#: generation disagrees with its own schedule name no-ops every wake with
#: ``STALE_SCHEDULE_GENERATION``. ``fired_at`` stays NULL, which is what
#: ``ck_prospective_triggers_fired`` -- ``(state = 'FIRED') = (fired_at IS NOT
#: NULL)`` -- requires of an ``ARMED`` row.
_TRIGGER_ARM_SQL = """
INSERT INTO prospective_triggers (
    id, tenant_id, user_id, case_id, trigger_type, predicate_ast, not_before,
    expires_at, state, evaluation_version, basis_case_revision, schedule_name,
    created_at, updated_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(case_id)s, %(trigger_type)s,
    %(predicate_ast)s, %(not_before)s, %(expires_at)s, 'ARMED',
    %(evaluation_version)s, %(basis_case_revision)s, %(schedule_name)s,
    %(tx_now)s, %(tx_now)s
)
"""

#: ``fired_at`` is cleared explicitly: the biconditional above forbids a
#: non-FIRED row from carrying one, so disarming a trigger that had already
#: fired would otherwise be refused by the CHECK rather than by a review.
#: ``last_result``/``last_reason_code`` are written together because
#: ``ck_prospective_triggers_last_reason`` pairs them per result.
_TRIGGER_DISARM_SQL = """
UPDATE prospective_triggers
   SET state             = 'DISARMED',
       last_result       = 'DISARMED',
       last_reason_code  = %(last_reason_code)s,
       last_evaluated_at = %(tx_now)s,
       fired_at          = NULL,
       updated_at        = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(trigger_id)s
"""

_PROPOSAL_SQL = """
UPDATE memory_proposals
   SET status = %(status)s, decided_at = %(tx_now)s, kernel_decision_id = %(decision_id)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(proposal_id)s
"""

_OUTBOX_SQL = """
INSERT INTO outbox_events (
    id, tenant_id, user_id, aggregate_type, aggregate_id, aggregate_version,
    event_type, payload_version, payload, trace_id, causation_id, status,
    attempt_count, next_attempt_at, occurred_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(aggregate_type)s, %(aggregate_id)s,
    %(aggregate_version)s, %(event_type)s, %(payload_version)s, %(payload)s,
    %(trace_id)s, %(causation_id)s, 'PENDING', 0, %(tx_now)s, %(tx_now)s
)
"""

#: Public aliases, so ``trigger_commit`` can reuse these two statements rather
#: than declare second copies of them. Aliases and not new string literals: a
#: second copy of the outbox INSERT is a second place a column can be forgotten,
#: and ``write_path_lint`` would count it as a second canonical write statement
#: that nobody reviewed.
STATE_TRANSITION_INSERT_SQL: Final[str] = _STATE_TRANSITION_SQL
OUTBOX_INSERT_SQL: Final[str] = _OUTBOX_SQL


# --- reads -----------------------------------------------------------------

_READ_CASE_SQL = """
SELECT id, status, revision, reopened_count, resolved_at, attention_level, relationship_id
  FROM cases
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(case_id)s
"""

_READ_EVIDENCE_SQL = """
SELECT id, tenant_id, user_id, artifact_id, retraction_status, created_at
  FROM evidence_items
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = ANY(%(ids)s)
"""

_READ_ARTIFACTS_SQL = """
SELECT id, tenant_id, user_id, encode(content_sha256, 'hex')
  FROM source_artifacts
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = ANY(%(ids)s)
"""

#: The replay guard's own index, read as a query. ``uq_kernel_decisions_terminal_
#: per_proposal`` is ``UNIQUE (proposal_id) WHERE decision <> 'RETRYABLE_
#: CONCURRENCY'``, so this predicate selects the at-most-one terminal decision a
#: proposal can have. The row is returned rather than a bare ``1`` because
#: section 9.3 says a replay returns the **stored** result, and a receipt that
#: invented a fresh decision id would point the Memory Trace at nothing.
_READ_DECIDED_SQL = """
SELECT id, case_id, transaction_opened FROM kernel_decisions
 WHERE proposal_id = %(proposal_id)s AND decision <> 'RETRYABLE_CONCURRENCY'
 LIMIT 1
"""

_READ_CASE_EVIDENCE_SQL = """
SELECT DISTINCT evidence_id FROM claims
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND case_id = %(case_id)s
"""

_READ_CASE_ARTIFACT_HASHES_SQL = """
SELECT DISTINCT encode(sa.content_sha256, 'hex')
  FROM claims c
  JOIN evidence_items e ON e.id = c.evidence_id
  JOIN source_artifacts sa ON sa.id = e.artifact_id
 WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s AND c.case_id = %(case_id)s
"""

#: The incumbent's authority is read back from ``claims.authority_score``, which
#: the Kernel wrote from the frozen grid at admission time. ``belief_versions``
#: carries no ``source_class`` column, so the grid key is not recoverable from
#: persisted state - see the module-level note in the task report.
_READ_INCUMBENTS_SQL = """
SELECT b.id, bv.id, bv.version_no, b.subject_type, b.subject_id, b.predicate,
       bv.value_type, bv.value_json, bv.epistemic_status, bv.belief_confidence,
       bv.valid_from, bv.valid_to, bv.recorded_at,
       (SELECT max(c.authority_score) FROM belief_support bs
          JOIN claims c ON c.id = bs.source_id
         WHERE bs.belief_version_id = bv.id AND bs.source_kind = 'CLAIM')
  FROM beliefs b
  JOIN belief_versions bv ON bv.id = b.current_version_id
 WHERE b.tenant_id = %(tenant_id)s AND b.user_id = %(user_id)s AND b.case_id = %(case_id)s
"""

_READ_COMMITMENTS_SQL = """
SELECT id, case_id, status, currency, committed_amount, fulfilled_amount,
       outstanding_amount, revision, due_at, valid_to, condition_ast
  FROM commitments
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND case_id = %(case_id)s
"""

#: The last two columns are the ledger row's **grounding claim** and the
#: authority the Kernel scored it at, read back the same way
#: ``_READ_INCUMBENTS_SQL`` reads a belief incumbent's: ``fulfillments`` carries
#: no ``source_class`` and no authority, so the grid key is not recoverable from
#: persisted state, but the score the Kernel wrote at admission time is. Without
#: them a later payment **denial** has nothing to measure a margin against, and
#: ``12_KERNEL_ALGORITHMS.md`` section 3.3's auto-resolution is not computable.
_READ_LEDGER_SQL = """
SELECT f.commitment_id, f.amount, f.currency, f.admission_status, f.evidence_id,
       f.fulfilled_at, cl.id, cl.authority_score
  FROM fulfillments f
  JOIN commitments c ON c.id = f.commitment_id
  LEFT JOIN LATERAL (
        SELECT id, authority_score FROM claims
         WHERE tenant_id = c.tenant_id AND user_id = c.user_id
           AND evidence_id = f.evidence_id
           AND subject_type = 'COMMITMENT' AND subject_id = f.commitment_id
           AND predicate = ANY(%(asserted_payment_predicates)s)
         ORDER BY authority_score DESC NULLS LAST
         LIMIT 1
  ) cl ON true
 WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s AND c.case_id = %(case_id)s
"""


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------


async def apply_write_plan(
    conn: Any,
    plan: pipeline.WritePlan,
    *,
    row: decisions.DecisionRow,
    context: CommitContext,
    tx_now: datetime,
) -> tuple[str, ...]:
    """Execute *plan* in DDL section 13 order and report the labels executed.

    Statement 1 (``read_case``) has already run by the time this is called -
    the plan was built from it - so the returned sequence starts at
    ``kernel_decisions``. Every branch appends its label before issuing the
    statement, so the returned tuple is a record of what happened rather than a
    restatement of what was intended.
    """
    executed: list[str] = []
    scope = {"tenant_id": context.tenant_id, "user_id": context.user_id}

    # 2. The decision row, before the rows whose NOT NULL foreign keys name it.
    executed.append("kernel_decisions")
    await conn.execute(decisions.DECISION_INSERT_SQL, decision_params(row))

    # 3. Claims. Evidence rows already exist and are immutable.
    for claim in plan.claims:
        executed.append("claims")
        await conn.execute(
            _CLAIM_SQL,
            {
                **scope,
                "id": claim.claim_id,
                "case_id": claim.case_id,
                "relationship_id": claim.relationship_id,
                "subject_type": str(claim.subject_type),
                "subject_id": claim.subject_id,
                "predicate": claim.predicate,
                "object_type": claim.object_type,
                "object_json": Json(_jsonable(claim.object_json)),
                "actor_type": claim.actor_type,
                "actor_id": claim.actor_id,
                "evidence_id": claim.evidence_id,
                "claim_kind": claim.claim_kind,
                "valid_from": claim.valid_from,
                "valid_to": claim.valid_to,
                "authority_score": claim.authority_score,
                "extraction_confidence": claim.extraction_confidence,
                "recorded_at": tx_now,
            },
        )

    # 4. New belief rows, then versions with their true support_edge_count,
    #    then the grounding edges, then the pointer, then the predecessor.
    for belief in plan.beliefs:
        if belief.exists:
            continue
        executed.append("belief_versions")
        await conn.execute(
            _BELIEF_SQL,
            {
                **scope,
                "id": belief.belief_id,
                "case_id": belief.case_id,
                "subject_type": str(belief.subject_type),
                "subject_id": belief.subject_id,
                "predicate": belief.predicate,
            },
        )
    for version in plan.belief_versions:
        executed.append("belief_versions")
        await conn.execute(
            _BELIEF_VERSION_SQL,
            {
                **scope,
                "id": version.version_id,
                "belief_id": version.belief_id,
                "version_no": version.version_no,
                "value_type": version.value_type,
                "value_json": Json(_jsonable(version.value_json)),
                "epistemic_status": str(version.epistemic_status),
                "belief_confidence": version.belief_confidence,
                "derivation_kind": version.derivation_kind,
                "support_edge_count": version.support_edge_count,
                "supersedes_version_id": version.supersedes_version_id,
                "supersession_reason_code": version.supersession_reason_code,
                "valid_from": version.valid_from,
                "valid_to": version.valid_to,
                "recorded_at": tx_now,
                "kernel_decision_id": row.id,
            },
        )
        for edge in version.support:
            executed.append("belief_support")
            await conn.execute(
                _BELIEF_SUPPORT_SQL,
                {
                    **scope,
                    "id": edge.edge_id,
                    "belief_version_id": edge.belief_version_id,
                    "source_kind": edge.source_kind,
                    "source_id": edge.source_id,
                    "relation": edge.relation,
                    "weight": edge.weight,
                    "reason_code": edge.reason_code,
                },
            )
        executed.append("beliefs_pointer")
        await conn.execute(
            _BELIEF_POINTER_SQL,
            {
                **scope,
                "version_id": version.version_id,
                "belief_id": version.belief_id,
                "tx_now": tx_now,
            },
        )
    for superseded in plan.supersedes:
        executed.append("belief_versions_supersede")
        await conn.execute(
            _SUPERSEDE_SQL, {**scope, "version_id": superseded.version_id, "tx_now": tx_now}
        )

    # 5. Conflicts.
    for conflict in plan.conflicts:
        executed.append("conflicts")
        settled = conflict.resolution_reason_code is not None
        await conn.execute(
            _CONFLICT_SQL,
            {
                **scope,
                "id": conflict.conflict_id,
                "case_id": conflict.case_id,
                "subject_type": str(conflict.subject_type),
                "subject_id": conflict.subject_id,
                "predicate": conflict.predicate,
                "left_source_kind": conflict.left_source_kind,
                "left_source_id": conflict.left_source_id,
                "right_source_kind": conflict.right_source_kind,
                "right_source_id": conflict.right_source_id,
                "conflict_type": str(conflict.conflict_type),
                "status": str(conflict.status),
                "severity": str(conflict.severity),
                "requires_human": conflict.requires_human,
                "canonical_belief_version_id": conflict.canonical_belief_version_id,
                "resolution_reason_code": conflict.resolution_reason_code,
                "resolution_notes": conflict.resolution_notes,
                "detected_at": tx_now,
                "resolved_at": tx_now if settled else None,
            },
        )

    # 6. Commitments and fulfillments. Totals recomputed, never incremented.
    #    The INSERT comes first inside this step: `fk_fulfillments_commitment`
    #    is validated at statement time.
    for commitment in plan.commitments:
        executed.append("commitments_insert")
        await conn.execute(
            _COMMITMENT_INSERT_SQL,
            {
                **scope,
                "id": commitment.commitment_id,
                "case_id": commitment.case_id,
                "obligor_type": commitment.obligor_type,
                "obligor_id": commitment.obligor_id,
                "beneficiary_type": commitment.beneficiary_type,
                "beneficiary_id": commitment.beneficiary_id,
                "commitment_type": commitment.commitment_type,
                "description": commitment.description,
                "currency": commitment.currency,
                "committed_amount": commitment.committed_amount,
                "fulfilled_amount": commitment.fulfilled_amount,
                "outstanding_amount": commitment.outstanding_amount,
                "due_at": commitment.due_at,
                "condition_ast": (
                    None
                    if commitment.condition_ast is None
                    else Json(_jsonable(commitment.condition_ast))
                ),
                "source_claim_id": commitment.source_claim_id,
                "status": str(commitment.status),
                "revision": commitment.revision,
                "valid_from": commitment.valid_from,
                "valid_to": commitment.valid_to,
                "tx_now": tx_now,
            },
        )
    for fulfillment in plan.fulfillments:
        executed.append("fulfillments")
        await conn.execute(
            _FULFILLMENT_SQL,
            {
                **scope,
                "id": fulfillment.fulfillment_id,
                "commitment_id": fulfillment.commitment_id,
                "evidence_id": fulfillment.evidence_id,
                "currency": fulfillment.currency,
                "amount": fulfillment.amount,
                "quantity": fulfillment.quantity,
                "fulfilled_at": fulfillment.fulfilled_at or tx_now,
                "admission_status": fulfillment.admission_status,
                "confidence": fulfillment.confidence,
            },
        )
    for update in plan.commitment_updates:
        executed.append("commitments")
        await conn.execute(
            _COMMITMENT_SQL,
            {
                **scope,
                "commitment_id": update.commitment_id,
                "fulfilled_after": update.fulfilled_after,
                "outstanding_after": update.outstanding_after,
                "status_after": update.status_after,
                "revision_before": update.revision_before,
                "tx_now": tx_now,
            },
        )

    # 7. The case aggregate: exactly one revision increment per canonical commit.
    case_update = plan.case_update
    if case_update is not None and case_update.changed:
        executed.append("cases")
        cursor = await conn.execute(
            case_ops.CASE_UPDATE_SQL,
            {
                **scope,
                "case_id": case_update.case_id,
                "status_after": str(case_update.status_after),
                "reopen_delta": case_update.reopen_delta,
                "attention_after": str(case_update.attention_after),
                "resolved_at": case_update.resolved_at,
                "revision_before": case_update.revision_before,
                "tx_now": tx_now,
            },
        )
        _assert_one_row(cursor)

    # 8. The audit ledger.
    for transition in plan.transitions:
        executed.append("state_transitions")
        await conn.execute(
            _STATE_TRANSITION_SQL,
            {
                **scope,
                "id": transition.transition_id,
                "case_id": transition.case_id,
                "case_revision": transition.case_revision,
                "transition_type": str(transition.transition_type),
                "subject_kind": transition.subject_kind,
                "subject_id": transition.subject_id,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "reason_code": transition.reason_code,
                "proposal_id": context.proposal_id,
                "kernel_decision_id": row.id,
                "trace_id": context.trace_id,
                "recorded_at": tx_now,
            },
        )

    # 9. Trigger mutations. Arms before disarms, so a commit that stands one
    #    trigger down and arms its replacement leaves exactly one ARMED row and
    #    does it in an order a reader can follow.
    for arm in plan.trigger_arms:
        executed.append("prospective_triggers")
        await conn.execute(
            _TRIGGER_ARM_SQL,
            {
                **scope,
                "id": arm.trigger_id,
                "case_id": arm.case_id,
                "trigger_type": str(arm.trigger_type),
                "predicate_ast": Json(_jsonable(arm.predicate_ast)),
                "not_before": arm.not_before,
                "expires_at": arm.expires_at,
                "evaluation_version": arm.evaluation_version,
                "basis_case_revision": arm.basis_case_revision,
                "schedule_name": arm.schedule_name,
                "tx_now": tx_now,
            },
        )
    for disarm in plan.trigger_disarms:
        executed.append("prospective_triggers")
        await conn.execute(
            _TRIGGER_DISARM_SQL,
            {
                **scope,
                "trigger_id": disarm.trigger_id,
                "last_reason_code": disarm.last_reason_code,
                "tx_now": tx_now,
            },
        )

    # 10. Proposal outcome.
    executed.append("memory_proposals")
    await conn.execute(
        _PROPOSAL_SQL,
        {
            **scope,
            "status": decisions.proposal_status_for(row.decision),
            "decision_id": row.id,
            "proposal_id": context.proposal_id,
            "tx_now": tx_now,
        },
    )

    # 11. Outbox, in the same transaction as the state it describes.
    for event in plan.outbox:
        executed.append("outbox_events")
        await conn.execute(
            _OUTBOX_SQL,
            {
                **scope,
                "id": event.event_id,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "aggregate_version": event.aggregate_version,
                "event_type": str(event.event_type),
                "payload_version": event.payload_version,
                "payload": Json(_jsonable(event.payload)),
                "trace_id": context.trace_id,
                "causation_id": context.proposal_id,
                "tx_now": tx_now,
            },
        )

    return tuple(executed)


def _assert_one_row(cursor: Any) -> None:
    """Rule R4's teeth. Zero rows means the optimistic predicate did not match.

    Under ``SERIALIZABLE`` this should be unreachable, which is exactly why it
    is checked: if it ever fires, the isolation guarantee has regressed and the
    alternative to raising here is a silently lost update.
    """
    rowcount = getattr(cursor, "rowcount", 1)
    if rowcount == 0:
        raise OptimisticRevisionMismatchError(
            "the cases UPDATE matched no row; the revision moved under this "
            "transaction (rule R4)"
        )


class OptimisticRevisionMismatchError(RuntimeError):
    """The ``WHERE revision = $rev_before`` predicate matched nothing."""

    code: Final[KernelReasonCode] = KernelReasonCode.OPTIMISTIC_REVISION_MISMATCH


def decision_params(row: decisions.DecisionRow) -> dict[str, Any]:
    """:meth:`decisions.DecisionRow.as_params` with ``reason_codes`` as JSON.

    The wrapping happens **here** rather than in ``decisions.py`` because
    ``Json`` is a ``psycopg`` adapter and the fifth import-linter contract keeps
    the decision modules free of ``psycopg``. A plain ``list[str]`` handed to
    ``psycopg`` is adapted to a Postgres *array* literal, which arrives at a
    ``jsonb`` column as::

        InvalidTextRepresentation: could not parse
        "{CONFLICT_VALUE_MUTUAL_EXCLUSION,CONFLICT_AUTHORITY_TIE}" as type jsonb

    -- observed against ``provenance_ci`` on the first hero run. The column is
    ``jsonb`` and ``ck_kernel_decisions_reason_codes`` additionally requires
    ``jsonb_typeof(reason_codes) = 'array'``, so a bare string would have
    satisfied the NOT NULL and failed the CHECK.
    """
    params = row.as_params()
    params["reason_codes"] = Json(params["reason_codes"])
    return params


def _jsonable(payload: Any) -> Any:
    """JSON-safe copy. Money becomes a string; it never becomes a float."""
    if isinstance(payload, dict):
        return {str(k): _jsonable(v) for k, v in payload.items()}
    if isinstance(payload, list | tuple):
        return [_jsonable(v) for v in payload]
    if isinstance(payload, uuid.UUID | datetime):
        return str(payload)
    if isinstance(payload, bool | int | str) or payload is None:
        return payload
    return str(payload)


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


async def commit_proposal(
    pool: TxPool,
    proposal: MemoryProposal,
    *,
    principal: preflight.Principal,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
    telemetry: TelemetrySink | None = None,
) -> KernelCommitResult:
    """Decide one proposal and, if it is accepted, commit it.

    PHASE A runs first, on its own connection and outside any transaction. A
    refusal there writes its ledger row with ``transaction_opened = false`` and
    returns; that is ``G4.4``'s whole assertion, and it is true because the
    refusal happens before ``BEGIN`` rather than because a flag was set.
    """
    context = CommitContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        proposal_id=proposal.proposal_id,
        trace_id=proposal.trace_id,
    )

    snapshot = await _read_preflight_snapshot(pool, proposal, principal)
    outcome = preflight.preflight(
        proposal,
        principal=principal,
        snapshot=snapshot,
        preflight_now=datetime.now(UTC),
        cfg=cfg,
    )
    if outcome.rejected:
        decision = outcome.decision or KernelDecision.REJECTED_INVARIANT
        if (
            decision is KernelDecision.NOOP_DUPLICATE
            and KernelReasonCode.PROPOSAL_ALREADY_DECIDED in outcome.reason_codes
        ):
            return await _replayed_result(pool, proposal, principal)
        # The ledger row is scoped to the PROPOSAL's owner, not to the caller.
        #
        # `fk_kernel_decisions_proposal` is FOREIGN KEY (tenant_id, user_id,
        # proposal_id) REFERENCES memory_proposals, and `fk_kernel_decisions_user`
        # is (tenant_id, user_id) REFERENCES users. A row carrying an
        # impersonating principal's user_id therefore cannot be written at all --
        # observed as `ForeignKeyViolation: Key (tenant_id, user_id) is not
        # present in table "users"` -- so the refusal would vanish instead of
        # being recorded, and `G4.4` reads exactly that row. The schema settles
        # the question: a decision row is an audit record ABOUT a proposal, and
        # a proposal has one owner.
        #
        # This does not weaken "tenancy comes from the principal, never from the
        # proposal". Tenancy still does: `proposal` carries no tenant_id by
        # design. What changes is only which user's audit trail records the
        # attempt, and the answer is the user whose proposal was attacked.
        # Nothing canonical is written on this path.
        owner_id = proposal.user_id
        ledger_context = replace(context, user_id=owner_id)
        row = decisions.build_decision_row(
            decision_id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            user_id=owner_id,
            proposal_id=proposal.proposal_id,
            trace_id=proposal.trace_id,
            decision=decision,
            reason_codes=outcome.reason_codes,
            # `case_id` too is a claim this row makes, and a refused proposal may
            # have named a case that does not exist under that owner --
            # `fk_kernel_decisions_case` would then reject the whole audit row
            # over a field that is nullable anyway. A decision cannot claim a
            # case that is not there, so an unresolvable one is recorded as NULL.
            case_id=await _visible_case_id(pool, proposal, principal, owner_id),
        )
        await _write_rejection(pool, row, ledger_context)
        return decisions.result_from_row(row)

    @in_transaction
    async def _callback(conn: Any, tx_now: datetime) -> decisions.DecisionRow:
        """PHASE B. One attempt, fresh reads, fresh ids, no network.

        Every id is minted here rather than above: rule 4 of section 7.3
        forbids deterministic UUIDs across attempts, because idempotency comes
        from ``proposal_id`` and the unique constraints, not from stable
        primary keys.
        """
        decision_id = uuid.uuid4()
        aggregate = await _read_aggregate(conn, proposal, principal)
        result = pipeline.build_write_plan(
            proposal,
            snapshot=aggregate,
            tx_now=tx_now,
            trace_id=proposal.trace_id,
            decision_id=decision_id,
            cfg=cfg,
        )
        row = decisions.build_decision_row(
            decision_id=decision_id,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            proposal_id=proposal.proposal_id,
            trace_id=proposal.trace_id,
            decision=result.decision,
            reason_codes=result.reason_codes,
            case_id=aggregate.case.case_id,
            case_revision_before=aggregate.case.revision,
            case_revision_after=(
                result.plan.case_update.revision_after
                if result.plan.case_update is not None
                else aggregate.case.revision
            ),
            tx_now=tx_now,
            effects=_effects(result, tx_now=tx_now),
        )
        await apply_write_plan(conn, result.plan, row=row, context=context, tx_now=tx_now)
        return row

    try:
        tx_result = await run_in_serializable_tx(pool, _callback, config=cfg, telemetry=telemetry)
    except RetryExhausted as exhausted:
        return retry_exhausted_result(
            proposal_id=proposal.proposal_id,
            trace_id=proposal.trace_id,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            decision_id=uuid.uuid4(),
            case_id=proposal.identity.case_id,
            attempts=exhausted.attempts,
        )
    except pgerr.UniqueViolation as violation:
        return await _from_unique_violation(pool, violation, proposal, principal, context)

    row = tx_result.value
    return decisions.result_from_row(row).model_copy(update={"retry_count": tx_result.retry_count})


def retry_exhausted_result(
    *,
    proposal_id: uuid.UUID,
    trace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    decision_id: uuid.UUID,
    case_id: uuid.UUID | None,
    attempts: int,
) -> KernelCommitResult:
    """The terminal outcome of the retry loop. No side effect of any kind.

    Deliberately does **not** write a ledger row: the transaction rolled back
    and the proposal stays ``SUBMITTED`` for the caller to re-drive with the
    identical ``Idempotency-Key`` over ``503`` + ``Retry-After``. Writing a row
    here would need a second connection and a second transaction, which is the
    side effect this outcome exists to avoid.
    """
    row = decisions.retry_exhausted_row(
        decision_id=decision_id,
        tenant_id=tenant_id,
        user_id=user_id,
        proposal_id=proposal_id,
        trace_id=trace_id,
        case_id=case_id,
        attempts=attempts,
    )
    return decisions.result_from_row(row)


def _effects(result: pipeline.PipelineOutcome, *, tx_now: datetime) -> decisions.CommitEffects:
    """The receipt lines the plan implies, so the caller sees what was written."""
    plan = result.plan
    return decisions.CommitEffects(
        claim_ids=tuple(c.claim_id for c in plan.claims),
        belief_versions=tuple(
            BeliefVersionRef(
                belief_id=v.belief_id,
                belief_version_id=v.version_id,
                version_no=v.version_no,
                predicate=_ref_predicate(plan, v.belief_id),
                epistemic_status=v.epistemic_status,
                supersedes_version_id=v.supersedes_version_id,
                grounding_edge_count=v.support_edge_count,
                is_derived=v.derivation_kind != "EVIDENCE_GROUNDED",
            )
            for v in plan.belief_versions
        ),
        conflicts=tuple(
            ConflictRef(
                conflict_id=c.conflict_id,
                conflict_type=c.conflict_type,
                status=c.status,
                predicate=c.predicate,
                requires_human=c.requires_human,
                created=True,
                canonical_belief_version_id=c.canonical_belief_version_id,
                resolution_reason_code=c.resolution_reason_code,
            )
            for c in plan.conflicts
        ),
        commitment_changes=tuple(
            [
                # `created=True` and no `status_before`: the obligation did not
                # exist before this commit, and reporting a prior status it
                # never had would put a fiction on the receipt.
                CommitmentChange(
                    commitment_id=c.commitment_id,
                    status_after=c.status,
                    committed=_receipt_money(c.currency, c.committed_amount),
                    fulfilled_after=_receipt_money(c.currency, c.fulfilled_amount),
                    outstanding_after=_receipt_money(c.currency, c.outstanding_amount),
                    created=True,
                )
                for c in plan.commitments
            ]
            + [
                CommitmentChange(
                    commitment_id=u.commitment_id,
                    status_before=CommitmentStatus(u.status_before),
                    status_after=CommitmentStatus(u.status_after),
                )
                for u in plan.commitment_updates
            ]
        ),
        trigger_changes=tuple(
            [
                TriggerChange(
                    trigger_id=a.trigger_id,
                    state_after=TriggerState.ARMED,
                    not_before=a.not_before,
                    expires_at=a.expires_at,
                    schedule_name=a.schedule_name,
                    basis_case_revision=a.basis_case_revision,
                    created=True,
                )
                for a in plan.trigger_arms
            ]
            + [
                TriggerChange(
                    trigger_id=d.trigger_id,
                    state_before=TriggerState.ARMED,
                    state_after=TriggerState.DISARMED,
                    basis_case_revision=(
                        plan.case_update.revision_after if plan.case_update else 0
                    ),
                )
                for d in plan.trigger_disarms
            ]
        ),
        state_transitions=tuple(
            StateTransitionRef(
                state_transition_id=t.transition_id,
                transition_type=t.transition_type,
                case_revision=t.case_revision,
                from_state=t.from_state,
                to_state=t.to_state,
                reason_code=t.reason_code,
                recorded_at=tx_now,
            )
            for t in plan.transitions
        ),
        outbox_event_ids=tuple(e.event_id for e in plan.outbox),
        case_status_after=plan.case_update.status_after if plan.case_update else None,
        attention_level_after=plan.case_update.attention_after if plan.case_update else None,
        attention_required=result.attention_required,
    )


def _receipt_money(currency: str | None, amount: Decimal | None) -> Money | None:
    """A receipt amount, or ``None`` for a non-monetary obligation.

    ``CommitmentChange._arithmetic_holds`` re-checks
    ``outstanding = committed - fulfilled`` on the way out of the Kernel, and it
    can only do that when all three are present. Coercing a NULL to
    ``0.0000 USD`` here would satisfy the validator by asserting that nothing is
    owed on something that was never a sum of money.
    """
    if amount is None or currency is None:
        return None
    return Money(amount=amount, currency=currency)


def _ref_predicate(plan: pipeline.WritePlan, belief_id: uuid.UUID) -> str:
    for belief in plan.beliefs:
        if belief.belief_id == belief_id:
            return belief.predicate
    return "unknown_predicate"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def _read_preflight_snapshot(
    pool: TxPool, proposal: MemoryProposal, principal: preflight.Principal
) -> preflight.PreflightSnapshot:
    """PHASE A's one scoped read, outside any transaction."""
    evidence_ids = list(
        dict.fromkeys([*proposal.evidence_ids, *(c.evidence_id for c in proposal.claims)])
    )
    artifact_ids = list(proposal.source_artifact_ids)
    scope = {"tenant_id": principal.tenant_id, "user_id": principal.user_id}

    async with pool.connection() as pooled:
        # `TxPool` describes only what the retry loop needs (`fetchone`); the
        # real connection is a `psycopg.AsyncConnection`. Widening here rather
        # than narrowing the Protocol keeps `provenance_db` — which is
        # Integrator-owned and shared with three other callers — unchanged.
        conn: Any = pooled
        cursor = await conn.execute(_READ_EVIDENCE_SQL, {**scope, "ids": evidence_ids})
        evidence_rows = await cursor.fetchall()
        cursor = await conn.execute(_READ_ARTIFACTS_SQL, {**scope, "ids": artifact_ids})
        artifact_rows = await cursor.fetchall()
        cursor = await conn.execute(_READ_DECIDED_SQL, {"proposal_id": proposal.proposal_id})
        decided = await cursor.fetchone()

    return preflight.PreflightSnapshot(
        evidence={
            r[0]: preflight.EvidenceRow(
                evidence_id=r[0],
                tenant_id=r[1],
                user_id=r[2],
                artifact_id=r[3],
                retraction_status=RetractionStatus(r[4]),
                created_at=r[5],
            )
            for r in evidence_rows
        },
        artifacts={
            r[0]: preflight.ArtifactRow(
                artifact_id=r[0], tenant_id=r[1], user_id=r[2], content_sha256=r[3]
            )
            for r in artifact_rows
        },
        decided_proposal_ids=frozenset({proposal.proposal_id}) if decided else frozenset(),
    )


async def _replayed_result(
    pool: TxPool, proposal: MemoryProposal, principal: preflight.Principal
) -> KernelCommitResult:
    """The receipt for a proposal that was already decided. **Writes nothing.**

    ``12_KERNEL_ALGORITHMS.md`` section 9.3, the ``PROPOSAL_ALREADY_DECIDED``
    row: *"Replay; stored result returned."* And the schema says the same thing
    from the other side -- ``uq_kernel_decisions_terminal_per_proposal`` is
    ``UNIQUE (proposal_id) WHERE decision <> 'RETRYABLE_CONCURRENCY'``, so a
    second terminal row for one proposal is not merely discouraged, it cannot
    exist. Attempting it raises ``UniqueViolation`` and turns an idempotent
    re-submission into a 500; that is what this branch prevents, and it was
    observed against ``provenance_ci`` before it existed.

    So the two readings of "a ``kernel_decisions`` row for every outcome"
    reconcile like this: every *decision* has a row, and a replay is not a
    second decision -- it is the first decision, handed back. The receipt
    carries the **stored** decision id for exactly that reason, so the caller's
    Memory Trace resolves to the commit that really happened rather than to a
    freshly minted id that names nothing.

    ``transaction_opened`` is ``False`` because *this* request opened none.
    The stored row is untouched and still records what the original commit did.
    """
    async with pool.connection() as pooled:
        conn: Any = pooled
        cursor = await conn.execute(_READ_DECIDED_SQL, {"proposal_id": proposal.proposal_id})
        stored = await cursor.fetchone()

    row = decisions.build_decision_row(
        # A missing row here would mean preflight and the database disagree about
        # what has been decided; minting an id would paper over that, so the
        # proposal id is used and the absence stays visible in the trace.
        decision_id=stored[0] if stored is not None else proposal.proposal_id,
        tenant_id=principal.tenant_id,
        user_id=proposal.user_id,
        proposal_id=proposal.proposal_id,
        trace_id=proposal.trace_id,
        decision=KernelDecision.NOOP_DUPLICATE,
        reason_codes=(KernelReasonCode.PROPOSAL_ALREADY_DECIDED,),
        case_id=stored[1] if stored is not None else None,
    )
    return decisions.result_from_row(replace(row, transaction_opened=False))


async def _visible_case_id(
    pool: TxPool,
    proposal: MemoryProposal,
    principal: preflight.Principal,
    owner_id: uuid.UUID,
) -> uuid.UUID | None:
    """The proposal's case id when it exists under *owner_id*, else ``None``.

    Used only on the PHASE A rejection path, and only to keep a nullable
    column from taking an unrecorded audit row down with it.
    """
    case_id = proposal.identity.case_id
    if case_id is None:
        return None
    async with pool.connection() as pooled:
        conn: Any = pooled
        cursor = await conn.execute(
            _READ_CASE_SQL,
            {"tenant_id": principal.tenant_id, "user_id": owner_id, "case_id": case_id},
        )
        return case_id if await cursor.fetchone() is not None else None


async def _read_aggregate(
    conn: Any, proposal: MemoryProposal, principal: preflight.Principal
) -> pipeline.AggregateSnapshot:
    """Statement 1 of DDL section 13, plus everything the plan depends on.

    Every row here is re-read on every attempt. Nothing computed before
    ``BEGIN`` is carried in, which is rule 2 of section 7.3 and the reason a
    ``40001`` retry cannot write a plan built against a rolled-back snapshot.
    """
    case_id = proposal.identity.case_id
    scope = {"tenant_id": principal.tenant_id, "user_id": principal.user_id, "case_id": case_id}

    cursor = await conn.execute(_READ_CASE_SQL, scope)
    case_row = await cursor.fetchone()
    if case_row is None:
        raise CaseNotFoundError(f"case {case_id} is not visible to this principal")
    case = case_ops.CaseRow(
        case_id=case_row[0],
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        status=CaseStatus(case_row[1]),
        revision=case_row[2],
        reopened_count=case_row[3],
        resolved_at=case_row[4],
        attention_level=AttentionLevel(case_row[5]),
    )
    relationship_id = case_row[6]

    cursor = await conn.execute(_READ_DECIDED_SQL, {"proposal_id": proposal.proposal_id})
    decided = await cursor.fetchone()

    cursor = await conn.execute(_READ_CASE_EVIDENCE_SQL, scope)
    linked_evidence = frozenset(r[0] for r in await cursor.fetchall())

    cursor = await conn.execute(_READ_CASE_ARTIFACT_HASHES_SQL, scope)
    linked_hashes = frozenset(r[0] for r in await cursor.fetchall())

    evidence_ids = list(
        dict.fromkeys([*proposal.evidence_ids, *(c.evidence_id for c in proposal.claims)])
    )
    cursor = await conn.execute(
        _READ_EVIDENCE_SQL,
        {"tenant_id": principal.tenant_id, "user_id": principal.user_id, "ids": evidence_ids},
    )
    evidence = {
        r[0]: case_ops.EvidenceRecord(evidence_id=r[0], created_at=r[5])
        for r in await cursor.fetchall()
    }

    cursor = await conn.execute(_READ_INCUMBENTS_SQL, scope)
    incumbents = tuple(_incumbent(r) for r in await cursor.fetchall())

    cursor = await conn.execute(_READ_COMMITMENTS_SQL, scope)
    commitments = tuple(money_commitment_row(r) for r in await cursor.fetchall())

    cursor = await conn.execute(
        _READ_LEDGER_SQL,
        {**scope, "asserted_payment_predicates": list(families.ASSERTED_PAYMENT_PREDICATES)},
    )
    ledger: dict[uuid.UUID, list[money_ops.FulfillmentRow]] = {}
    for r in await cursor.fetchall():
        ledger.setdefault(r[0], []).append(_ledger_row(r))

    return pipeline.AggregateSnapshot(
        case=case,
        relationship_id=relationship_id,
        case_snapshot=case_ops.CaseSnapshot(
            evidence_ids_linked_to_case=linked_evidence,
            artifact_hashes_linked_to_case=linked_hashes,
            evidence=evidence,
        ),
        incumbents=incumbents,
        decided_proposal_ids=frozenset({proposal.proposal_id}) if decided else frozenset(),
        commitments=commitments,
        fulfillment_ledger={k: tuple(v) for k, v in ledger.items()},
    )


class CaseNotFoundError(RuntimeError):
    """The proposal names a case this principal cannot see."""

    code: Final[KernelReasonCode] = KernelReasonCode.CASE_NOT_IN_RELATIONSHIP


def _incumbent(row: Sequence[Any]) -> pipeline.IncumbentBelief:
    """One current belief version, rebuilt as a comparable proposition.

    ``base_authority`` comes from the grounding claim's stored
    ``authority_score`` rather than from the frozen grid, because
    ``belief_versions`` carries no ``source_class`` column and the grid key is
    therefore not recoverable from persisted state. The score was itself written
    from the grid at admission, so this is a read-back rather than a second
    scoring rule.
    """
    subject_type = SubjectType(row[3])
    value_json = row[7] or {}
    valid_from, valid_to = row[10], row[11]
    basis = prop.ValidityBasis.UNKNOWN
    if valid_from is not None:
        basis = (
            prop.ValidityBasis.EXPLICIT
            if valid_to is not None
            else prop.ValidityBasis.EXPLICIT_OPEN
        )
    raw = dict(value_json)
    if isinstance(raw.get("amount"), str):
        raw["amount"] = Decimal(raw["amount"])
    normalised = prop.normalize_claim(
        prop_id=row[1],
        subject_type=subject_type,
        subject_id=row[4],
        predicate=row[5],
        raw_value=raw,
        source_class="PROVIDER_SYSTEM_NOTICE",
        valid_from=valid_from,
        valid_to=valid_to,
        validity_basis=basis,
        recorded_at=row[12],
        source_kind=prop.PropositionSourceKind.BELIEF_VERSION,
        is_incumbent=True,
        epistemic_status=EpistemicStatus(row[8]),
        belief_confidence=row[9],
    )
    proposition = normalised.proposition
    stored_authority = row[13]
    if proposition is not None and stored_authority is not None:
        proposition = replace(proposition, base_authority=stored_authority)
    return pipeline.IncumbentBelief(
        belief_id=row[0],
        version_id=row[1],
        version_no=row[2],
        subject_type=subject_type,
        subject_id=row[4],
        predicate=row[5],
        proposition=proposition,  # type: ignore[arg-type]
        value_type=row[6],
        value_json=value_json,
        belief_confidence=row[9],
    )


def money_commitment_row(row: Sequence[Any]) -> money_ops.CommitmentRow:
    """One ``commitments`` row in the shape ``money_ops`` reads."""
    return money_ops.CommitmentRow(
        commitment_id=row[0],
        case_id=row[1],
        status=CommitmentStatus(row[2]),
        currency=row[3],
        committed_amount=row[4],
        fulfilled_amount=row[5],
        outstanding_amount=row[6],
        revision=row[7],
        due_at=row[8],
        valid_to=row[9],
        has_condition=row[10] is not None,
    )


def _ledger_row(row: Sequence[Any]) -> money_ops.FulfillmentRow:
    return money_ops.FulfillmentRow(
        amount=row[1],
        currency=row[2],
        admission_status=FulfillmentAdmissionStatus(row[3]),
        evidence_id=row[4],
        fulfilled_at=row[5],
        source_claim_id=row[6],
        authority=row[7],
    )


async def _write_rejection(
    pool: TxPool, row: decisions.DecisionRow, context: CommitContext
) -> None:
    """The ledger row for a PHASE A refusal, written outside the canonical
    transaction because a rejection opens none.

    The proposal row is updated in the same short transaction so a refused
    proposal does not sit in ``SUBMITTED`` forever, which is the state the UI
    would render as "queued".
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(decisions.DECISION_INSERT_SQL, decision_params(row))
        await conn.execute(
            _PROPOSAL_SQL,
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "status": decisions.proposal_status_for(row.decision),
                "decision_id": row.id,
                "proposal_id": context.proposal_id,
                "tx_now": row.committed_at or datetime.now(UTC),
            },
        )


async def _from_unique_violation(
    pool: TxPool,
    violation: pgerr.UniqueViolation,
    proposal: MemoryProposal,
    principal: preflight.Principal,
    context: CommitContext,
) -> KernelCommitResult:
    """Section 7.5's mapping, by **constraint name** and never by message text.

    CockroachDB renders the expression rather than the name into ``str(e)``, so
    matching on a substring of the message is how a mapping quietly stops
    firing after a server upgrade.
    """
    constraint = getattr(violation.diag, "constraint_name", None)
    outcome = mapped_unique_violation(constraint)
    row = decisions.build_decision_row(
        decision_id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        proposal_id=proposal.proposal_id,
        trace_id=proposal.trace_id,
        decision=outcome.decision,
        reason_codes=(outcome.reason_code,),
        case_id=proposal.identity.case_id,
    )
    await _write_rejection(pool, row, context)
    return decisions.result_from_row(row)


#: The whole failure surface of one Kernel transaction, in one place. A reader
#: asking what this can raise should not have to grep for it, and a future
#: handler that forgets one of these is a silent 500 in the ingestion path.
FAILURE_MODES: Final[tuple[type[Exception], ...]] = (
    OptimisticRevisionMismatchError,
    CaseNotFoundError,
    pgerr.UniqueViolation,
    RetryExhausted,
)
