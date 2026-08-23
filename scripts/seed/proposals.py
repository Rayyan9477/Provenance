"""The curated ``MemoryProposal`` fixtures step 9 replays (``T2.8`` step 9).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5 ``T2.8`` sub-task 9 -- "Replay
  the curated ``MemoryProposal`` fixtures through ``MemoryKernel.commit()`` as
  ``pv_kernel_writer``".
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero dataset canon* -- Alex Rivera,
  Northline Fiber's two relationships, Harborview, Beltline, Kestrel (the
  **employer**, never the mover) and Cascade Power. "Example names must be
  drawn from section 17.3, never invented."
- ``docs/specs/12_KERNEL_ALGORITHMS.md`` section 2.1 -- the closed
  predicate-family registry these claims are written against.
- ``docs/quality/22_EVAL_DATASETS.md`` section 3 fixture
  ``the_move_baseline_rev12`` -- "Full hero world, case 1 at revision 12".

What these fixtures are for
---------------------------
They establish the world **as it stood before the hero event**: the beliefs,
claims and obligations the June invoice will contradict. Every row they produce
is written by ``MemoryKernel.commit()``; this module contains no SQL and
``scripts/seed/loader.py`` contains no canonical ``INSERT``. That is not
tidiness -- ``70_TASK_PLAN.md`` T2.8 step 9 is explicit that "seeding canonical
rows by raw INSERT to unblock Phase 2 would create a second canonical writer
and is forbidden", and ``tools/write_path_lint`` checks it structurally.

What is deliberately **not** here
---------------------------------
The June invoice for USD 186.00, the ``VALUE_CONFLICT`` it produces, the
``RESOLVED -> REOPENED`` transition and the revision ``12 -> 13`` increment.
Those are what the demo performs live. Seeding them would make the demo a
replay of itself and would make the counterfactual a fiction.
``services/control_plane/tests/db/test_seed_step9.py`` asserts the string
``186`` appears in no proposal.

Three shapes the Kernel imposes on these fixtures
-------------------------------------------------
1. **One proposal per case.** ``case_ops.revision_after`` is rule ``R1``:
   exactly one ``cases.revision`` increment per accepted commit. Two proposals
   for one case would therefore spend two revisions, and every case revision is
   eval ground truth (``22_EVAL_DATASETS.md`` section 2).
2. **At most one belief-bearing claim per (subject, family) in the whole
   seed.** ``uq_beliefs_proposition`` is
   ``(tenant_id, user_id, subject_type, subject_id, predicate)`` and does not
   include the case, and Rule ``N1`` stores the *family's canonical* predicate.
   So ``service_terminated`` and ``service_active`` are one belief, and two
   claims of one family on one subject are two INSERTs of the same row.
   Everything else is written with a predicate outside the closed registry,
   which section 2.1 admits as a claim and never turns into a belief -- honest
   silence rather than an invented contradiction.
3. **The hero's incumbent balance is ``PROVIDER_AGENT_WRITTEN``, and that is
   load-bearing.** The frozen grid scores ``(BALANCE, PROVIDER_AGENT_WRITTEN)``
   at ``0.7200``. The June invoice arrives as ``PROVIDER_SYSTEM_NOTICE``, which
   the same grid scores at ``0.9000``. Matcher ``M13`` promotes a
   ``VALUE_CONFLICT`` to ``AUTHORITY_CONFLICT`` when
   ``min(left, right) >= high_authority_floor`` (0.80) and the two are within
   ``auto_resolve_margin`` (0.25) -- so an incumbent seeded at, say,
   ``PROVIDER_SYSTEM_NOTICE`` would quietly change *which rule decides the
   hero*, from gate ``H5`` to gate ``H1``. The canon fixes it as
   ``VALUE_CONFLICT`` via ``H5``, so the source class is a contract value here,
   not a stylistic choice. ``test_seed_step9.py`` asserts the margin.

The obligations, and why they arrive in two passes
--------------------------------------------------
The four commitments of ``scripts/seed/obligations.py`` are carried here as
``ProposedCommitment`` and are written by the Kernel. They were not, for a
while: ``pipeline.build_write_plan`` read ``proposal.claims`` and nothing else,
so a proposed commitment validated, was persisted inside the
``memory_proposals`` payload, and had nowhere to go. That gap is closed, and
:func:`proposed_obligation_content` still counts what the proposals carry so
the seed transcript states it rather than leaving it to be inferred.

The two **fulfillments** cannot travel with them. ``pipeline._apply_payment``
resolves a payment against ``snapshot.commitment(subject_id)`` -- the aggregate
read at the *start* of the transaction -- so a commitment created by the same
commit is not there to be paid, and ``_commitment_row`` mints its id with
``uuid.uuid4()`` inside the transaction, so no fixture can name it in advance.
:func:`fulfillment_proposals` is therefore a **factory**: the loader commits the
curated proposals, reads the Kernel-minted commitment id back per case, and
submits one further proposal per fulfillment.

That second pass is the difference between a landing screen that renders
**USD 2,020.00** and one that renders USD 4,570.00. Without it every obligation
opens at its full committed amount -- 1800 + 420 + 2350 -- because nothing has
paid anything; ``obligations.outstanding_total()`` returns 2,020.00 only
because ``obligations.FULFILLMENTS`` declares the USD 200.00 damage payment and
the USD 2,350.00 relocation payment. Those two figures are read from that
module here rather than re-typed, so the seed and the total cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from provenance_contracts.base import Money
from provenance_contracts.predicates import PredicateNode
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposalIdentity,
    ProposedClaim,
    ProposedCommitment,
    ProposedTrigger,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    ClaimKind,
    CommitmentType,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    TriggerMutationKind,
    TriggerType,
    ValueType,
)
from scripts.seed.cases import case_of
from scripts.seed.counterparties import BELTLINE, HARBORVIEW, KESTREL, NORTHLINE, relationship_of
from scripts.seed.evidence import evidence_of
from scripts.seed.ids import DEPOSIT_DUE_AT, sid
from scripts.seed.obligations import COMMITMENTS, FULFILLMENTS, TRIGGERS
from scripts.seed.tenants import HERO_USER

__all__ = [
    "CURATED_PROPOSALS",
    "SEED_MODEL_ID",
    "SEED_PROMPT_VERSION",
    "SeedProposal",
    "curated_proposals",
    "fulfillment_proposal_ids",
    "fulfillment_proposals",
    "payload_sha256",
    "proposal_payload",
    "proposed_obligation_content",
]

#: ``ck_memory_proposals_model`` carries this value in both migration ``0005``
#: and migration ``0009`` -- across a whole provider pivot -- precisely because
#: the deterministic Kernel writes proposals nobody's model produced. It is what
#: ``memory_proposals.model_id`` records for every row step 9 creates.
SEED_MODEL_ID: Final[str] = "deterministic.kernel"

#: ``memory_proposals.prompt_version``. No prompt ran; the column is NOT NULL,
#: so it names the fixture generation instead of pretending to name a prompt.
SEED_PROMPT_VERSION: Final[str] = "pv-seed-1.0.0"

#: ``ModelAttribution`` cannot express :data:`SEED_MODEL_ID`: its validator
#: requires a live provider's id shape, and there is no "no model ran" member.
#: The typed attribution therefore names the configured **extraction** tier
#: while the persisted column carries ``deterministic.kernel``, which is the
#: honest value and the one every consumer reads.
#:
#: Two candidates, tried in order, because the provider pivot is in flight
#: (``CANONICAL_DECISIONS.md`` -> *Gemini model id canon* supersedes the Bedrock
#: canon for new work, and both shapes are presently constructable). A frozen
#: single id here would turn a canon change into an ImportError in the seed.
_ATTRIBUTION_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("gemini", "gemini-3.5-flash-lite"),
    ("bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
)


def _seed_attribution() -> ModelAttribution:
    errors: list[str] = []
    for provider, model_id in _ATTRIBUTION_CANDIDATES:
        try:
            return ModelAttribution(
                provider=provider,  # type: ignore[arg-type]
                model_id=model_id,
                tier=ModelTier.E,
                prompt_version=SEED_PROMPT_VERSION,
                graph_name="seed",
                graph_version="1.0.0",
            )
        except ValueError as exc:  # pragma: no cover - only on a canon change
            errors.append(f"{provider}/{model_id}: {exc}")
    raise RuntimeError(
        "no ModelAttribution shape in _ATTRIBUTION_CANDIDATES validates against "
        "the current contract, so the curated seed proposals cannot be built. "
        "Add the current tier-E id from CANONICAL_DECISIONS.md. Tried:\n  " + "\n  ".join(errors)
    )


# ---------------------------------------------------------------------------
# The claim table
# ---------------------------------------------------------------------------

#: ``subject`` values. ``CASE`` hangs the claim off the proposal's own case;
#: anything else names a relationship by its seed slug.
_SUBJECT_CASE: Final[str] = "CASE"


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    """One curated evidence item, read as one assertion.

    ``dated`` carries the *evidence's* validity interval onto the claim rather
    than inventing one. Rule ``T2``: the Kernel never invents a validity basis,
    and an item whose evidence carries no ``valid_from`` is normalised as
    ``VALIDITY_UNKNOWN_NOT_COMPARABLE`` -- which is the truthful outcome, not a
    defect to paper over with ``observed_at``.
    """

    evidence_slug: str
    predicate: str
    object_type: ValueType
    object_value: Any
    claim_kind: ClaimKind
    source_class: SourceClass
    modality: Modality
    actor_type: ActorType
    subject: str
    dated: bool = False


def _money(amount: str) -> dict[str, str]:
    """The wire form of money: a **string** amount, never a float.

    ``pipeline._money_ready`` turns it into a ``Decimal`` exactly once, at the
    boundary, and ``families.normalize_money`` refuses anything else.
    """
    return {"currency": "USD", "amount": str(Decimal(amount).quantize(Decimal("0.0001")))}


_TERMINATED: Final[dict[str, str]] = {"state": "TERMINATED"}
_ACTIVE: Final[dict[str, str]] = {"state": "ACTIVE"}

#: Case 1 -- the hero case. Two belief-bearing claims, in two different
#: families, on the one relationship the June invoice will name.
_ISP_CANCELLATION: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "isp-cancellation-request",
        "service_cancellation_requested",
        ValueType.BOOLEAN,
        True,
        ClaimKind.OBSERVATION,
        SourceClass.USER_STATEMENT,
        Modality.ASSERTED_PAST,
        ActorType.USER,
        "northline-old",
    ),
    _ClaimSpec(
        "isp-cancellation-confirmed",
        "cancellation_confirmed",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "northline-old",
    ),
    # The fact the hero invoice contradicts. Rule N1 stores it under the
    # SERVICE_STATUS family's canonical predicate `service_active`, so the row
    # reads `service_active = {"state": "TERMINATED"}` and the two surface forms
    # cannot become two mutually exclusive beliefs.
    _ClaimSpec(
        "isp-termination-effective-31-may",
        "service_terminated",
        ValueType.ENUM,
        _TERMINATED,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "northline-old",
        dated=True,
    ),
    _ClaimSpec(
        "isp-final-bill-notice",
        "final_bill_announced",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.PROMISED_FUTURE,
        ActorType.COUNTERPARTY,
        "northline-old",
    ),
    _ClaimSpec(
        "isp-equipment-return-receipt",
        "equipment_returned",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "northline-old",
    ),
    _ClaimSpec(
        "isp-closure-email",
        "account_closed",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "northline-old",
    ),
    # The incumbent the June invoice's USD 186.00 contradicts. Explicitly
    # dated -- validity open from 31 May 2026 -- because the matcher needs a
    # material temporal overlap with the invoice's June billing period, and an
    # undated incumbent is `VALIDITY_UNKNOWN_NOT_COMPARABLE` and contradicts
    # nothing. See shape 3 in the module docstring for the source class.
    _ClaimSpec(
        "isp-account-status-snapshot",
        "balance_owed",
        ValueType.MONEY,
        _money("0.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "northline-old",
        dated=True,
    ),
)

#: Case 2 -- the near-miss retrieval target. Its balance belief hangs off the
#: CASE rather than the relationship: the relationship already carries one
#: ``balance_owed`` belief (case 1's), and ``uq_beliefs_proposition`` does not
#: include the case.
_ISP_FINAL_BILL: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "isp-final-invoice",
        "final_invoice_issued",
        ValueType.MONEY,
        _money("74.20"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_SYSTEM_NOTICE,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
        dated=True,
    ),
    _ClaimSpec(
        "isp-final-invoice-paid",
        "final_invoice_paid",
        ValueType.MONEY,
        _money("74.20"),
        ClaimKind.FULFILLMENT_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
    _ClaimSpec(
        "isp-zero-balance-statement",
        "balance_owed",
        ValueType.MONEY,
        _money("0.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_SYSTEM_NOTICE,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
    _ClaimSpec(
        "isp-closure-acknowledgement",
        "billing_relationship_concluded",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
)

#: Case 3 -- the deposit. No ``balance_owed`` belief: the landlord's promise is
#: ``PROMISED_FUTURE`` and belongs in ``commitments``, and recording a promise
#: as an asserted present balance would be a second, wrong source of truth for
#: the one figure the landing screen renders.
_LANDLORD_DEPOSIT: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "deposit-lease-clause",
        "security_deposit_terms",
        ValueType.MONEY,
        _money("1800.00"),
        ClaimKind.POLICY_TERM,
        SourceClass.SIGNED_AGREEMENT,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
    ),
    # The same clause, read as the balance it states: the landlord holds USD
    # 1,800.00 of the tenant's money and it is returnable. That is an asserted
    # present amount, not the promise -- the promise is the COMMITMENT_CLAIM
    # below and belongs in `commitments`, where a `due_at` can live.
    _ClaimSpec(
        "deposit-lease-clause",
        "balance_owed",
        ValueType.MONEY,
        _money("1800.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.SIGNED_AGREEMENT,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
    _ClaimSpec(
        "deposit-inspection-completed",
        "inspection_completed",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
        dated=True,
    ),
    _ClaimSpec(
        "deposit-thirty-day-promise",
        "deposit_return_promise",
        ValueType.MONEY,
        _money("1800.00"),
        ClaimKind.COMMITMENT_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.PROMISED_FUTURE,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
        dated=True,
    ),
    _ClaimSpec(
        "deposit-followup",
        "deposit_not_received",
        ValueType.BOOLEAN,
        True,
        ClaimKind.OBSERVATION,
        SourceClass.USER_STATEMENT,
        Modality.ASSERTED_PRESENT,
        ActorType.USER,
        "harborview-tenancy",
    ),
    _ClaimSpec(
        "deposit-no-response",
        "no_response_received",
        ValueType.BOOLEAN,
        True,
        ClaimKind.OBSERVATION,
        SourceClass.USER_STATEMENT,
        Modality.ASSERTED_PRESENT,
        ActorType.USER,
        "harborview-tenancy",
    ),
)

#: Case 4 -- the inspection that starts the 30-day clock.
_LANDLORD_INSPECTION: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "inspection-scheduling",
        "inspection_scheduled",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.PROMISED_FUTURE,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
    ),
    _ClaimSpec(
        "inspection-walkthrough-report",
        "inspection_outcome_no_deductions",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
    ),
    # "the tenancy concluded on the inspection date" is a SERVICE_STATUS
    # assertion about the tenancy relationship, so it is written with the
    # family's surface predicate rather than a private one -- a predicate
    # outside the registry is admitted as a claim and grounds no belief, and
    # this fact is one the read models need.
    _ClaimSpec(
        "inspection-key-handover",
        "service_terminated",
        ValueType.ENUM,
        _TERMINATED,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "harborview-tenancy",
        dated=True,
    ),
)

#: Case 5 -- Beltline Movers. ``damage-outstanding-balance`` is an
#: ``AMOUNT_ASSERTION`` of what is still owed, in the counterparty's own words,
#: so it *is* an asserted present balance and becomes the belief.
_MOVERS_DAMAGE: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "damage-report",
        "damage_reported",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "beltline-engagement",
    ),
    _ClaimSpec(
        "damage-reimbursement-promise",
        "damage_reimbursement_promise",
        ValueType.MONEY,
        _money("420.00"),
        ClaimKind.COMMITMENT_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.PROMISED_FUTURE,
        ActorType.COUNTERPARTY,
        "beltline-engagement",
    ),
    _ClaimSpec(
        "damage-partial-payment",
        "partial_payment_sent",
        ValueType.MONEY,
        _money("200.00"),
        ClaimKind.FULFILLMENT_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "beltline-engagement",
    ),
    _ClaimSpec(
        "damage-partial-payment-ack",
        "partial_payment_acknowledged",
        ValueType.MONEY,
        _money("200.00"),
        ClaimKind.OBSERVATION,
        SourceClass.USER_STATEMENT,
        Modality.ASSERTED_PAST,
        ActorType.USER,
        "beltline-engagement",
    ),
    _ClaimSpec(
        "damage-outstanding-balance",
        "balance_owed",
        ValueType.MONEY,
        _money("220.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
)

#: Case 6 -- restored to the replay once the Kernel could record it.
#:
#: Both items are scheduling facts: a move rescheduled by a day, and a crew that
#: arrived. Neither is a service status, a balance, a payment, an outstanding
#: amount or a commitment status, so neither maps to a v1 predicate family --
#: which section 2.1 handles correctly, by admitting them as claims that ground
#: no belief. This is the only curated case with no mapped predicate at all.
#:
#: It was withheld because the Kernel could not record that outcome.
#: ``pipeline.build_write_plan`` collected its reason codes from normalisation,
#: from the disposition verdict and from the case update; a commit that only
#: admitted unmapped claims produced none of the three, and
#: ``decisions.build_decision_row`` then raised
#:
#:     ValueError: ACCEPTED was built with no reason code; audit is not
#:     optional (23_PHASE_GATES.md section 23.8) and the column is NOT NULL
#:
#: -- observed against a live cluster on the first replay of this fixture. The
#: refusal was right (an unaudited acceptance is worse than a crash) and the
#: hole was upstream of it: a claim-only acceptance is legal per section 6.2's
#: table -- "admitting a claim is a memory change even if no belief moves" --
#: and had no code to carry. The Kernel now emits
#: ``CONFLICT_HINT_UNMAPPED_FAMILY`` when it admits a claim whose predicate is
#: outside the v1 registry, which is the only member of the closed catalogue in
#: ``12_KERNEL_ALGORITHMS.md`` section 9.3 that means that; a new code would
#: have been a layer-local alias, which ``CANONICAL_DECISIONS.md`` ->
#: *Closed domain vocabularies* forbids.
_MOVERS_SCHEDULING: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "scheduling-rescheduled",
        "move_rescheduled",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "beltline-engagement",
    ),
    _ClaimSpec(
        "scheduling-arrival-confirmed",
        "move_completed",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "beltline-engagement",
    ),
)

#: Case 7 -- Kestrel Analytics, the **employer**. The final payment record says
#: nothing remains outstanding, which is an asserted present balance of zero.
_EMPLOYER_RELOCATION: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "relocation-expense-submitted",
        "expense_claim_submitted",
        ValueType.BOOLEAN,
        True,
        ClaimKind.OBSERVATION,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "kestrel-employment",
    ),
    _ClaimSpec(
        "relocation-expense-approved",
        "expense_claim_approved",
        ValueType.MONEY,
        _money("2350.00"),
        ClaimKind.COMMITMENT_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.PROMISED_FUTURE,
        ActorType.COUNTERPARTY,
        "kestrel-employment",
    ),
    _ClaimSpec(
        "relocation-reimbursement-received",
        "balance_owed",
        ValueType.MONEY,
        _money("0.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
)

#: Case 8 -- the stipend, settled. "paid the approved temporary housing
#: stipend in full with no further payment due" is an asserted present
#: balance of zero, so it is written as one.
_EMPLOYER_STIPEND: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "stipend-approved",
        "stipend_approved",
        ValueType.BOOLEAN,
        True,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PAST,
        ActorType.COUNTERPARTY,
        "kestrel-employment",
    ),
    _ClaimSpec(
        "stipend-paid",
        "balance_owed",
        ValueType.MONEY,
        _money("0.00"),
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_AGENT_WRITTEN,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        _SUBJECT_CASE,
    ),
)

#: Case 9 -- the identity decoy, and the reason it is one. The same
#: counterparty, the same sender domain, the same brand voice, a **different**
#: relationship -- and now a different canonical belief: ``NF-9913-2250`` is
#: ACTIVE while ``NF-4471-8802`` is TERMINATED. An identity gate that matches on
#: counterparty name alone now contradicts a belief that says the opposite.
_NEW_INSTALL_CREDIT: Final[tuple[_ClaimSpec, ...]] = (
    _ClaimSpec(
        "new-install-credit-terms",
        "promotional_credit_terms",
        ValueType.BOOLEAN,
        True,
        ClaimKind.POLICY_TERM,
        SourceClass.OFFICIAL_POLICY_DOC,
        Modality.CONDITIONAL,
        ActorType.COUNTERPARTY,
        "northline-new",
    ),
    _ClaimSpec(
        "new-install-credit-terms",
        "service_active",
        ValueType.ENUM,
        _ACTIVE,
        ClaimKind.COUNTERPARTY_CLAIM,
        SourceClass.PROVIDER_SYSTEM_NOTICE,
        Modality.ASSERTED_PRESENT,
        ActorType.COUNTERPARTY,
        "northline-new",
    ),
)


# ---------------------------------------------------------------------------
# The commitments -- proposed here, executed when the Kernel can (see above)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CommitmentSpec:
    """One obligation, transcribed from ``scripts/seed/obligations.py``."""

    commitment_type: CommitmentType
    description: str
    obligor_ref: str
    amount: str | None
    due_at: datetime | None
    source_claim_predicate: str


_COMMITMENTS: Final[dict[str, _CommitmentSpec]] = {
    # `due_at` is `2026-06-15T00:00:00Z` and every "days overdue" figure derives
    # from it against the `2026-09-18` demo clock -- 95 days. It is not stored
    # as a number anywhere.
    "landlord-deposit": _CommitmentSpec(
        CommitmentType.DEPOSIT_RETURN,
        "Return of the USD 1,800.00 security deposit within 30 days of the final inspection.",
        HARBORVIEW.slug,
        "1800.00",
        DEPOSIT_DUE_AT,
        "deposit_return_promise",
    ),
    "movers-damage": _CommitmentSpec(
        CommitmentType.MONETARY_REIMBURSEMENT,
        "Reimbursement of USD 420.00 for damage caused during the move.",
        BELTLINE.slug,
        "420.00",
        None,
        "damage_reimbursement_promise",
    ),
    "employer-relocation": _CommitmentSpec(
        CommitmentType.MONETARY_REIMBURSEMENT,
        "Reimbursement of USD 2,350.00 of approved relocation expenses.",
        KESTREL.slug,
        "2350.00",
        None,
        "expense_claim_approved",
    ),
    "isp-cancellation": _CommitmentSpec(
        CommitmentType.SERVICE_TERMINATION,
        "Termination of internet service at 214 Ridgeway Apt 3B effective 31 May 2026.",
        NORTHLINE.slug,
        None,
        None,
        "service_terminated",
    ),
}


# ---------------------------------------------------------------------------
# The triggers -- translated into the dialect the proposal contract speaks
# ---------------------------------------------------------------------------

#: Case slug -> the trigger ``scripts/seed/obligations.py`` declares for it.
_TRIGGERS_BY_CASE: Final[dict[str, Any]] = {t.case_slug: t for t in TRIGGERS}

#: The two operand keys ``triggers/ast.py`` reads for a branch node, in the
#: order the contract's ``args`` tuple wants them.
_BINARY_OPERANDS: Final[tuple[str, ...]] = ("left", "right")


def _contract_predicate(node: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a stored predicate node into ``PredicateNode``'s dialect.

    Two dialects exist for one grammar, and they are not interchangeable:

    * ``scripts/seed/obligations.py`` authors the **stored** form that
      ``specs/16_TRIGGER_DSL.md`` section 6 prints and
      ``services/control_plane/app/triggers/ast.py`` parses -- binary operators
      carry ``left``/``right``, unary carries ``arg``, and ``CONST`` carries a
      required ``type``.
    * ``provenance_contracts.predicates.PredicateNode`` -- which is what
      ``ProposedTrigger.predicate`` is typed as, so it is the only dialect a
      ``MemoryProposal`` can carry -- puts every operand in ``args`` and has no
      ``type`` field on ``CONST`` at all. It is ``extra="forbid"``, so the
      stored form does not merely lose information against it: it raises.

    ``parse_spec`` accepts **both**, which is why nothing had noticed. Measured
    rather than assumed: the contract dialect round-trips through
    ``build_spec_document`` and ``parse_spec`` with the real registry resolver.
    So the seed translates on the way in and the Kernel stores what it was
    given; ``obligations.py`` keeps one authored copy of the seven conjuncts and
    this function keeps no second copy of their meaning.

    The asymmetry is worth reporting rather than absorbing: an agent can only
    ever produce the contract dialect, so the stored dialect is reachable only
    from a fixture, and a grammar with two spellings and one parser is a grammar
    that will grow a third.
    """
    op = str(node["op"])
    if op == "FIELD":
        return {"op": "FIELD", "path": node["path"]}
    if op == "CONST":
        # `type` is deliberately dropped: `PredicateNode` forbids it, and the
        # value is already a string, which is what `DECIMAL_MUST_BE_STRING`
        # exists to require of a DECIMAL comparand.
        return {"op": "CONST", "value": node["value"]}
    operands = [node[key] for key in _BINARY_OPERANDS if key in node]
    if "arg" in node:
        operands.append(node["arg"])
    operands.extend(node.get("args", ()))
    return {"op": op, "args": [_contract_predicate(child) for child in operands]}


def _build_trigger(case_slug: str) -> ProposedTrigger | None:
    """The ARM this case's proposal carries, if ``obligations.py`` declares one.

    Armed in the **same** commit that creates the obligation it watches, which
    costs no extra ``cases.revision``: the Kernel resolves ``commitments.<name>``
    against the commitment the plan just minted, and ``basis_case_revision`` is
    the revision that commit produces -- rule ``R3``, and the number the
    evaluator compares against when the scheduler wakes it months later.
    """
    seeded = _TRIGGERS_BY_CASE.get(case_slug)
    if seeded is None:
        return None
    return ProposedTrigger(
        local_id="tg_001",
        mutation_kind=TriggerMutationKind.ARM,
        trigger_type=TriggerType(seeded.trigger_type),
        predicate=PredicateNode.model_validate(
            _contract_predicate(seeded.predicate_ast["predicate"])
        ),
        not_before=seeded.not_before,
        expires_at=seeded.expires_at,
        rationale=(
            "Seeded prospective memory: wake after the deadline and check whether "
            "the obligation is still outstanding."
        ),
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

#: Case slug -> its claims, in the order the evidence was observed. The order is
#: the order the rows are written in, so it is data rather than an accident of
#: iteration.
_CLAIMS_BY_CASE: Final[tuple[tuple[str, tuple[_ClaimSpec, ...]], ...]] = (
    ("isp-cancellation", _ISP_CANCELLATION),
    ("isp-final-bill", _ISP_FINAL_BILL),
    ("landlord-deposit", _LANDLORD_DEPOSIT),
    ("landlord-inspection", _LANDLORD_INSPECTION),
    ("movers-damage", _MOVERS_DAMAGE),
    ("movers-scheduling", _MOVERS_SCHEDULING),
    ("employer-relocation", _EMPLOYER_RELOCATION),
    ("employer-stipend", _EMPLOYER_STIPEND),
    ("new-install-credit", _NEW_INSTALL_CREDIT),
)


@dataclass(frozen=True, slots=True)
class SeedProposal:
    """One curated proposal plus the seed context step 9 needs to replay it.

    ``case_slug`` and ``case_revision`` are carried alongside the contract
    object rather than derived from it because the loader has to reconcile the
    Kernel's rule ``R1`` increment against the revision ``scripts/seed/cases.py``
    declares, and reaching back into the fixtures from the loader would put the
    reconciliation two modules away from the number it reconciles.
    """

    case_slug: str
    case_revision: int
    proposal: MemoryProposal


def _subject(spec: _ClaimSpec, case_id: uuid.UUID) -> tuple[SubjectType, uuid.UUID]:
    if spec.subject == _SUBJECT_CASE:
        return SubjectType.CASE, case_id
    return SubjectType.RELATIONSHIP, relationship_of(spec.subject).id


def _build_claim(spec: _ClaimSpec, index: int, case_id: uuid.UUID) -> ProposedClaim:
    evidence = evidence_of(spec.evidence_slug)
    subject_type, subject_id = _subject(spec, case_id)
    return ProposedClaim(
        local_id=f"cl_{index:03d}",
        claim_kind=spec.claim_kind,
        subject_type=subject_type,
        subject_id=subject_id,
        predicate=spec.predicate,
        object_type=spec.object_type,
        object_value=spec.object_value,
        actor_type=spec.actor_type,
        actor_ref=None if spec.actor_type is ActorType.USER else evidence.counterparty_name,
        evidence_id=evidence.id,
        source_class=spec.source_class,
        modality=spec.modality,
        valid_from=evidence.valid_from if spec.dated else None,
        valid_to=evidence.valid_to if spec.dated else None,
        extraction_confidence=evidence.extraction_confidence,
    )


def _build_commitment(
    case_slug: str, claims: tuple[ProposedClaim, ...]
) -> ProposedCommitment | None:
    spec = _COMMITMENTS.get(case_slug)
    if spec is None:
        return None
    source = next(c for c in claims if c.predicate == spec.source_claim_predicate)
    return ProposedCommitment(
        local_id="cm_001",
        commitment_type=spec.commitment_type,
        description=spec.description,
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref=spec.obligor_ref,
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(HERO_USER.id),
        committed=None
        if spec.amount is None
        else Money(amount=Decimal(spec.amount), currency="USD"),
        due_at=spec.due_at,
        source_claim_local_id=source.local_id,
        confidence=Decimal("0.9800"),
    )


def _build(case_slug: str, specs: tuple[_ClaimSpec, ...]) -> SeedProposal:
    case = case_of(case_slug)
    claims = tuple(_build_claim(spec, i + 1, case.id) for i, spec in enumerate(specs))
    evidence = tuple(dict.fromkeys(evidence_of(s.evidence_slug).id for s in specs))
    artifacts = tuple(dict.fromkeys(evidence_of(s.evidence_slug).artifact_id for s in specs))
    commitment = _build_commitment(case_slug, claims)
    trigger = _build_trigger(case_slug)
    # The instant the world could first have been known, derived from the
    # evidence rather than from a clock: `20_TDD_STRATEGY.md` section 4.2 rule 2
    # forbids an absolute literal anywhere but `ids.py`.
    created_at = max(evidence_of(s.evidence_slug).observed_at for s in specs)
    proposal = MemoryProposal(
        proposal_id=sid("proposal", case_slug),
        proposal_type=ProposalType.SEED_FIXTURE,
        trace_id=sid("trace", f"seed-step9-{case_slug}"),
        agent_run_id=sid("agent-run", f"seed-step9-{case_slug}"),
        user_id=HERO_USER.id,
        source_artifact_ids=artifacts,
        evidence_ids=evidence,
        identity=ProposalIdentity(
            relationship_id=case.relationship_id,
            case_id=case.id,
            confidence=Decimal("1.0000"),
            resolved_by="DETERMINISTIC",
        ),
        claims=claims,
        commitments=() if commitment is None else (commitment,),
        trigger_mutations=() if trigger is None else (trigger,),
        model=_seed_attribution(),
        idempotency_key=f"seed.step9.{case_slug}",
        created_at=created_at,
    )
    return SeedProposal(case_slug=case_slug, case_revision=case.revision, proposal=proposal)


CURATED_PROPOSALS: Final[tuple[SeedProposal, ...]] = tuple(
    _build(case_slug, specs) for case_slug, specs in _CLAIMS_BY_CASE
)


def curated_proposals() -> tuple[MemoryProposal, ...]:
    """The contract objects alone, in replay order."""
    return tuple(seeded.proposal for seeded in CURATED_PROPOSALS)


def proposal_payload(proposal: MemoryProposal) -> dict[str, Any]:
    """The ``memory_proposals.payload`` JSONB value: the proposal itself.

    Storing anything smaller would make the persisted row a summary of a
    proposal rather than the proposal, and the Memory Trace reads this column.
    """
    return json.loads(proposal.model_dump_json())


def payload_sha256(proposal: MemoryProposal) -> bytes:
    """``memory_proposals.payload_sha256`` -- 32 bytes, and deterministic.

    ``uq_memory_proposals_payload`` is ``(tenant_id, user_id, payload_sha256)``,
    so a stable digest is what makes a reseed offer the row that is already
    there instead of a second logical proposal.
    """
    return hashlib.sha256(proposal.model_dump_json().encode("utf-8")).digest()


#: Commitment seed-slug -> the case its proposal is attached to, read off
#: ``obligations.COMMITMENTS`` rather than restated. The map is a bijection --
#: each of the four commitments sits on a different case -- which is what lets
#: the loader identify a Kernel-minted commitment row by its ``case_id`` alone
#: without matching on an amount or a description.
_COMMITMENT_CASE_BY_SLUG: Final[dict[str, str]] = {c.slug: c.case_slug for c in COMMITMENTS}


def fulfillment_proposal_ids() -> dict[str, uuid.UUID]:
    """Case slug -> the proposal id :func:`fulfillment_proposals` will mint.

    Knowable before the first pass runs, because the *proposal* id is a
    ``uuid5`` even though the commitment id it will name is not. The loader
    needs exactly this to decide, in one read and before it writes anything,
    how many commits each case is about to receive -- and therefore how far to
    position its revision counter.
    """
    return {
        _COMMITMENT_CASE_BY_SLUG[f.commitment_slug]: sid(
            "proposal", f"{_COMMITMENT_CASE_BY_SLUG[f.commitment_slug]}-fulfillment"
        )
        for f in FULFILLMENTS
    }


def fulfillment_proposals(
    commitment_ids: Mapping[uuid.UUID, uuid.UUID],
) -> tuple[SeedProposal, ...]:
    """The second pass: one proposal per fulfillment, against a real commitment.

    *commitment_ids* maps ``case_id`` -> the ``commitments.id`` the Kernel minted
    for that case. The loader reads it back after the first pass; nothing here
    can know it in advance, because ``pipeline._commitment_row`` mints it with
    ``uuid.uuid4()`` inside the transaction (rule 4 of section 7.3 forbids a
    deterministic one).

    Every figure comes from ``obligations.FULFILLMENTS`` -- USD 200.00 against
    Beltline's 420.00, USD 2,350.00 against Kestrel's 2,350.00 -- so the seed
    and ``obligations.outstanding_total()`` cannot drift apart. After both are
    admitted the outstanding total is Harborview 1,800.00 + Beltline 220.00 +
    Kestrel 0.00 = **USD 2,020.00**, and Northline's non-monetary termination
    contributes NULL rather than a coerced zero.

    A replayed fulfillment is a no-op at the ledger as well as at the decision:
    ``money_ops.apply_fulfillment`` branch 2 returns ``FULFILLMENT_EVIDENCE_
    DUPLICATE`` for evidence already in the ledger, so even a proposal id
    collision could not double-count a payment.
    """
    built: list[SeedProposal] = []
    for fulfillment in FULFILLMENTS:
        case_slug = _COMMITMENT_CASE_BY_SLUG[fulfillment.commitment_slug]
        case = case_of(case_slug)
        commitment_id = commitment_ids.get(case.id)
        if commitment_id is None:
            continue
        evidence = evidence_of(fulfillment.evidence_slug)
        claim = ProposedClaim(
            local_id="cl_001",
            claim_kind=ClaimKind.FULFILLMENT_CLAIM,
            # The subject is the obligation, not the case: `_apply_payment`
            # refuses to guess which obligation an undirected payment settles,
            # and that refusal is the reason this pass exists at all.
            subject_type=SubjectType.COMMITMENT,
            subject_id=commitment_id,
            predicate="payment_received",
            object_type=ValueType.MONEY,
            object_value={
                "currency": fulfillment.currency,
                "amount": str(fulfillment.amount.quantize(Decimal("0.0001"))),
                # `families._require_datetime` refuses a naive instant: the
                # Kernel never assumes a timezone (section 8.1), and section 2.7
                # buckets payments by `paid_at`, so an assumed one can move a
                # payment out of the window that matches it.
                "paid_at": fulfillment.fulfilled_at.isoformat(),
            },
            actor_type=ActorType.COUNTERPARTY,
            actor_ref=evidence.counterparty_name,
            evidence_id=evidence.id,
            source_class=SourceClass.PROVIDER_AGENT_WRITTEN,
            modality=Modality.ASSERTED_PAST,
            valid_from=fulfillment.fulfilled_at,
            extraction_confidence=fulfillment.confidence,
        )
        proposal = MemoryProposal(
            proposal_id=sid("proposal", f"{case_slug}-fulfillment"),
            proposal_type=ProposalType.FULFILLMENT_ADMISSION,
            trace_id=sid("trace", f"seed-step9-{case_slug}-fulfillment"),
            agent_run_id=sid("agent-run", f"seed-step9-{case_slug}-fulfillment"),
            user_id=HERO_USER.id,
            source_artifact_ids=(evidence.artifact_id,),
            evidence_ids=(evidence.id,),
            identity=ProposalIdentity(
                relationship_id=case.relationship_id,
                case_id=case.id,
                confidence=Decimal("1.0000"),
                resolved_by="DETERMINISTIC",
            ),
            claims=(claim,),
            model=_seed_attribution(),
            idempotency_key=f"seed.step9.{case_slug}.fulfillment",
            created_at=evidence.observed_at,
        )
        built.append(
            SeedProposal(case_slug=case_slug, case_revision=case.revision, proposal=proposal)
        )
    return tuple(built)


def proposed_obligation_content() -> dict[str, int]:
    """The obligation content the curated proposals carry, counted.

    This used to be called ``unwritable_proposal_content`` and counted what the
    Kernel would silently drop. The Kernel now writes both -- ``commitments``
    via ``INSERT INTO commitments`` and triggers via ``INSERT INTO
    prospective_triggers`` -- so a count named for the gap would be a count
    that reports four dropped rows every run while four rows are in fact
    written. A stale instrument is worse than none: it is a number a reader
    trusts.

    It stays, renamed, because the seed transcript should say what a replay
    writes beyond claims and beliefs, and because a future proposal carrying a
    trigger this seed does not yet author will show up here rather than in a
    table nobody counted.
    """
    return {
        "commitments": sum(len(s.proposal.commitments) for s in CURATED_PROPOSALS),
        "trigger_mutations": sum(len(s.proposal.trigger_mutations) for s in CURATED_PROPOSALS),
        "fulfillments": len(FULFILLMENTS),
    }


# ---------------------------------------------------------------------------
# Import-time invariants
#
# Every one of these is a mistake that would otherwise be discovered as a
# `23505` in the middle of a replay, three minutes into a seed, with the case
# revisions already moved. Cheap here, expensive there.
# ---------------------------------------------------------------------------

assert len({s.case_slug for s in CURATED_PROPOSALS}) == len(
    CURATED_PROPOSALS
), "one proposal per case: rule R1 spends one cases.revision per accepted commit"
assert set(_COMMITMENTS) <= {s.case_slug for s in CURATED_PROPOSALS}, sorted(
    set(_COMMITMENTS) - {s.case_slug for s in CURATED_PROPOSALS}
)


def _belief_bearing(specs: tuple[_ClaimSpec, ...]) -> tuple[str, ...]:
    """The claims in *specs* whose predicate is in the closed family registry."""
    from services.control_plane.app.memory_kernel.families import Family, family_of

    return tuple(s.predicate for s in specs if family_of(s.predicate) is not Family.UNMAPPED)


#: Which cases carry a belief-bearing claim, kept as data rather than as a
#: precondition.
#:
#: This used to be an import-time assertion that **every** case carried one,
#: because a commit admitting only unmapped claims reached
#: ``decisions.build_decision_row`` with an empty reason-code tuple and raised.
#: The Kernel now emits ``CONFLICT_HINT_UNMAPPED_FAMILY`` for exactly that
#: outcome, so the precondition is gone and ``movers-scheduling`` -- the only
#: curated case with no mapped predicate -- is replayed like the other seven.
#: The mapping stays because ``test_seed_step9.py`` counts expected beliefs from
#: it, and because "which cases ground a belief" is worth being able to read.
_HAS_BELIEF_BEARING_CLAIM: Final[dict[str, tuple[str, ...]]] = {
    slug: _belief_bearing(specs) for slug, specs in _CLAIMS_BY_CASE
}
assert _HAS_BELIEF_BEARING_CLAIM["movers-scheduling"] == (), (
    "movers-scheduling is the claim-only fixture: if a predicate here ever "
    "joins the registry it stops exercising the claim-only acceptance path"
)
