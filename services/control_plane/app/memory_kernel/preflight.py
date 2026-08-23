"""PHASE A: everything decided before a write transaction is opened.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 1.1: PHASE A is advisory, exists for
  early rejection and cheap telemetry, and is never relied on for correctness.
  Steps 4-16 are re-executed inside PHASE B against rows read there, because a
  ``40001`` retry restarts the callback from fresh reads and a plan replayed
  from a stale snapshot is exactly the partial aggregate state invariant 3
  forbids.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 1.2 steps 1-9.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 9.3 for every reason code below.
- ``quality/23_PHASE_GATES.md`` ``G4.4``: foreign evidence is refused **before**
  a transaction opens, and ``kernel_decisions.transaction_opened = false`` is
  how that is checked. :class:`PreflightOutcome` cannot report otherwise; the
  field is a property, not a parameter.
- ``EXECUTION/70_TASK_PLAN.md`` T4.2.

Why a preflight rejection is not merely an optimisation
--------------------------------------------------------
The guarantee is not that Python refused. The composite foreign keys in
``specs/10_DATABASE_DDL.md`` refuse a cross-user evidence reference at the
database level even when the Kernel is bypassed entirely, so the guarantee does
not rest on this module. What this module adds is that the refusal is *cheap*,
*named*, and *auditable*: it carries a reason code from the closed catalogue and
leaves ``transaction_opened = false`` in the ledger, which is the
machine-readable statement that no aggregate was touched.

Why the currency check does not reject
--------------------------------------
Section 1.2 step 13 is explicit that ``CONFLICT_CURRENCY_MISMATCH`` is "a
conflict, not a rejection". The evidence is still admitted and a person still
gets to see the disagreement; refusing here would discard the artifact and
leave the user with nothing to look at.

The ``PV_SABOTAGE`` hook
------------------------
``G4.9`` runs::

    PV_SABOTAGE=memory_kernel.preflight.assert_grounded pytest ... ; echo "exit=$?"

and requires at least one FAILED plus ``exit=1``. :func:`assert_grounded` is
that symbol. A green run there means the grounding tests do not actually depend
on the grounding check, which is a gate failure rather than a relief.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

from provenance_contracts.proposal import MemoryProposal, ProposedBeliefMutation
from provenance_domain import money
from provenance_domain.enums import (
    CaseReopenReasonCode,
    KernelDecision,
    KernelReasonCode,
    RetractionStatus,
)
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)

__all__ = [
    "KNOWN_REASON_CODES",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "SABOTAGED_SYMBOLS",
    "ArtifactRow",
    "EvidenceRow",
    "Principal",
    "PreflightOutcome",
    "PreflightSnapshot",
    "SupportsGroundingCheck",
    "UngroundedBeliefError",
    "assert_grounded",
    "preflight",
    "validate_currency_coherence",
    "validate_principal",
    "validate_provenance",
    "validate_reason_codes",
    "validate_schema_version",
]

#: The closed set a proposal-supplied reason code must belong to. An unknown
#: reason code is a rejection, never a pass-through string
#: (``EXECUTION/70_TASK_PLAN.md`` T4.2). Both enums are closed and neither is a
#: superset of the other: the hero's own ``CONTRADICTORY_EVIDENCE`` is a
#: ``CaseReopenReasonCode`` and not a ``KernelReasonCode``, so a validator that
#: checked only the latter would reject the transition the demo turns on.
KNOWN_REASON_CODES: Final[frozenset[str]] = frozenset(
    member.value for member in KernelReasonCode
) | frozenset(member.value for member in CaseReopenReasonCode)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated internal principal's capability binding.

    Tenancy is derived from here and never from the proposal: a field the agent
    could fill in is a field an attacker could fill in. ``user_id`` appears on
    both sides precisely so a disagreement is loud.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """The ``evidence_items`` columns preflight reads."""

    evidence_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    artifact_id: uuid.UUID
    retraction_status: RetractionStatus = RetractionStatus.ACTIVE
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    """The ``source_artifacts`` columns preflight reads."""

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightSnapshot:
    """The read-only rows step 4 loaded, in one object a test can build."""

    evidence: Mapping[uuid.UUID, EvidenceRow] = field(default_factory=dict)
    artifacts: Mapping[uuid.UUID, ArtifactRow] = field(default_factory=dict)
    #: ``kernel_decisions.proposal_id`` values already decided (step 6, rule R6).
    decided_proposal_ids: frozenset[uuid.UUID] = frozenset()
    #: Currency by commitment id, for the currency-coherence check.
    commitment_currencies: Mapping[uuid.UUID, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreflightOutcome:
    """The verdict of PHASE A.

    ``transaction_opened`` is not a parameter. PHASE A runs before step 17 by
    construction, so a preflight outcome that could claim a transaction would be
    able to hide where a refusal happened - which is exactly what ``G4.4``
    audits.
    """

    ok: bool
    decision: KernelDecision | None = None
    reason_codes: tuple[KernelReasonCode, ...] = ()

    @property
    def transaction_opened(self) -> bool:
        """Always ``False``. PHASE A opens no transaction."""
        return False

    @property
    def rejected(self) -> bool:
        """True when PHASE A refuses the proposal outright."""
        return not self.ok


class SupportsGroundingCheck(Protocol):
    """Anything that can enumerate its ungrounded belief mutations.

    ``MemoryProposal`` satisfies this structurally. Typing the parameter
    structurally is what lets the grounding check be tested with a two-line
    fake instead of by bypassing Pydantic validation on a real proposal, which
    would be testing the fixture rather than the rule.
    """

    def ungrounded_mutations(self) -> tuple[ProposedBeliefMutation, ...]: ...


class UngroundedBeliefError(ValueError):
    """A belief version was proposed with no grounding and no derivation."""

    code: Final[KernelReasonCode] = KernelReasonCode.INVARIANT_BELIEF_UNGROUNDED


def assert_grounded(proposal: SupportsGroundingCheck) -> tuple[ProposedBeliefMutation, ...]:
    """Raise unless every proposed belief version is grounded.

    A belief is revisable, but it is never free-floating: a canonical version
    carries at least one ``SUPPORTS`` edge, or names a registered deterministic
    derivation. There is no third option.

    Returns:
        The empty tuple. The return exists so the ``PV_SABOTAGE`` replacement
        has something well-typed to hand back, which is what makes the neutered
        run fail on the assertion under test rather than on a ``TypeError``
        three frames away.
    """
    ungrounded = tuple(proposal.ungrounded_mutations())
    if ungrounded:
        raise UngroundedBeliefError(
            f"{len(ungrounded)} proposed belief version(s) carry no SUPPORTS edge and "
            "name no registered deterministic derivation"
        )
    return ungrounded


def validate_schema_version(
    schema_version: str, cfg: KernelConfig = DEFAULT_KERNEL_CONFIG
) -> tuple[KernelReasonCode, ...]:
    """Step 1. ``SCHEMA_VERSION_UNSUPPORTED`` when outside the frozen set.

    An agent runtime on a stale contract is redeployed, not accommodated by a
    compatibility branch nobody can test.
    """
    if schema_version in cfg.supported_schema_versions:
        return ()
    return (KernelReasonCode.SCHEMA_VERSION_UNSUPPORTED,)


def validate_principal(
    proposal: MemoryProposal, principal: Principal
) -> tuple[KernelReasonCode, ...]:
    """Step 3. A machine client asserting a user id it was not issued."""
    if proposal.user_id != principal.user_id:
        return (KernelReasonCode.PRINCIPAL_USER_MISMATCH,)
    return ()


def validate_provenance(
    proposal: MemoryProposal, snapshot: PreflightSnapshot, principal: Principal
) -> tuple[KernelReasonCode, ...]:
    """Steps 4 and 5, in one scoped read's worth of checks.

    Every cited evidence id must exist, belong to this tenant and this user,
    point at a cited artifact, and be retrieval-eligible. The checks run in
    catalogue order and the first refusal is the one reported, so a security
    event is never masked by a schema complaint about the same row.
    """
    codes: list[KernelReasonCode] = []
    cited_artifacts = set(proposal.source_artifact_ids)

    for artifact_id in proposal.source_artifact_ids:
        artifact = snapshot.artifacts.get(artifact_id)
        if artifact is None:
            codes.append(KernelReasonCode.ARTIFACT_NOT_FOUND)
            continue
        if artifact.tenant_id != principal.tenant_id:
            codes.append(KernelReasonCode.TENANT_MISMATCH)
        elif artifact.user_id != principal.user_id:
            codes.append(KernelReasonCode.ARTIFACT_FOREIGN_USER)

    for evidence_id in _cited_evidence_ids(proposal):
        row = snapshot.evidence.get(evidence_id)
        if row is None:
            codes.append(KernelReasonCode.EVIDENCE_NOT_FOUND)
            continue
        if row.tenant_id != principal.tenant_id:
            codes.append(KernelReasonCode.TENANT_MISMATCH)
            continue
        if row.user_id != principal.user_id:
            codes.append(KernelReasonCode.EVIDENCE_FOREIGN_USER)
            continue
        if row.artifact_id not in cited_artifacts:
            codes.append(KernelReasonCode.EVIDENCE_ARTIFACT_MISMATCH)
            continue
        if row.retraction_status is not RetractionStatus.ACTIVE:
            # Section 2.8: retracted, superseded and quarantined evidence keeps
            # its embedding, so a path that ignores `retraction_status` lets
            # corrected evidence resurface and re-litigate settled facts.
            codes.append(KernelReasonCode.SOURCE_RETRACTED_EXCLUDED)

    return _dedupe(codes)


def _cited_evidence_ids(proposal: MemoryProposal) -> tuple[uuid.UUID, ...]:
    """Every persisted evidence id the proposal reaches for.

    ``MemoryProposal`` already requires that these are declared in
    ``evidence_ids``, which is what lets step 4 load and ownership-check the
    whole set in one read.
    """
    seen: list[uuid.UUID] = list(proposal.evidence_ids)
    for claim in proposal.claims:
        if claim.evidence_id not in seen:
            seen.append(claim.evidence_id)
    return tuple(seen)


def validate_reason_codes(proposal: MemoryProposal) -> tuple[KernelReasonCode, ...]:
    """Every proposal-supplied reason code is a member of the closed set.

    A stringly-typed message here is how a closed set leaks: the code would
    reach ``state_transitions.reason_code``, pass a ``STRING`` column, and turn
    up in the UI as a value no consumer has a branch for.
    """
    supplied = [proposal.requested_transition_reason_code]
    supplied.extend(m.reason_code for m in proposal.belief_mutations)
    for mutation in proposal.belief_mutations:
        supplied.extend(edge.reason_code for edge in mutation.grounding)
    if any(code is not None and code not in KNOWN_REASON_CODES for code in supplied):
        return (KernelReasonCode.SCHEMA_TYPE_INVALID,)
    return ()


def validate_currency_coherence(
    proposal: MemoryProposal, snapshot: PreflightSnapshot
) -> tuple[KernelReasonCode, ...]:
    """The Kernel refuses arithmetic across currencies. Advisory here.

    Reported as ``CONFLICT_CURRENCY_MISMATCH`` rather than as a rejection,
    because section 1.2 step 13 is explicit that a currency mismatch becomes a
    conflict, not a refusal.
    """
    currencies = {
        commitment.committed.currency
        for commitment in proposal.commitments
        if commitment.committed is not None
    }
    currencies |= set(snapshot.commitment_currencies.values())
    if len(currencies) > 1:
        return (KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,)
    return ()


def preflight(
    proposal: MemoryProposal,
    *,
    principal: Principal,
    snapshot: PreflightSnapshot,
    preflight_now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> PreflightOutcome:
    """Steps 1-9, in pipeline order, before any write intent exists.

    ``preflight_now`` is never persisted and is never compared against a stored
    timestamp for a decision that survives into PHASE B (section 1.4). It is
    here so the horizon check has a clock without reading one.
    """
    _ = preflight_now

    # Step 1 - schema.
    schema_codes = validate_schema_version(proposal.schema_version, cfg)
    if schema_codes:
        return PreflightOutcome(
            ok=False, decision=KernelDecision.REJECTED_SCHEMA, reason_codes=schema_codes
        )

    reason_codes = validate_reason_codes(proposal)
    if reason_codes:
        return PreflightOutcome(
            ok=False, decision=KernelDecision.REJECTED_SCHEMA, reason_codes=reason_codes
        )

    # Steps 2 and 3 - tenancy and principal.
    principal_codes = validate_principal(proposal, principal)
    if principal_codes:
        return PreflightOutcome(
            ok=False,
            decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
            reason_codes=principal_codes,
        )

    # Steps 4 and 5 - provenance of every cited row.
    provenance_codes = validate_provenance(proposal, snapshot, principal)
    fatal = tuple(
        c for c in provenance_codes if c is not KernelReasonCode.SOURCE_RETRACTED_EXCLUDED
    )
    if fatal:
        return PreflightOutcome(
            ok=False,
            decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
            reason_codes=provenance_codes,
        )

    # Step 6 - replay. Rule R6: a second submission of the same proposal_id is
    # a lookup, not a re-execution, and the revision does not move.
    if proposal.proposal_id in snapshot.decided_proposal_ids:
        return PreflightOutcome(
            ok=False,
            decision=KernelDecision.NOOP_DUPLICATE,
            reason_codes=(KernelReasonCode.PROPOSAL_ALREADY_DECIDED,),
        )

    # Step 16's grounding sweep, run early so an ungrounded plan never reaches
    # a transaction. It is re-run inside PHASE B, because PHASE A is advisory.
    assert_grounded(proposal)

    # Advisory, never fatal: a currency clash is a conflict (step 13).
    advisory = provenance_codes + validate_currency_coherence(proposal, snapshot)
    return PreflightOutcome(ok=True, decision=None, reason_codes=_dedupe(list(advisory)))


def _dedupe(codes: list[KernelReasonCode]) -> tuple[KernelReasonCode, ...]:
    """Preserve first occurrence. Section 9.1 rule 2 makes the order stable so
    golden-file tests can assert on it."""
    seen: list[KernelReasonCode] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return tuple(seen)


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `quality/23_PHASE_GATES.md` G4.9 addresses this symbol as
# `memory_kernel.preflight.assert_grounded`, not by its dotted import path, so
# the module label is explicit rather than `__name__`. The mechanism itself
# lives in `provenance_domain.money` and is reused rather than re-implemented,
# for the same reason the authority grid is: one definition, one place to be
# wrong.

#: The label `tests/sabotage_matrix.yaml` and `G4.9` use for this module.
SABOTAGE_MODULE: Final[str] = "memory_kernel.preflight"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("assert_grounded",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
