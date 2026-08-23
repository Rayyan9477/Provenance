"""``T9.1`` -- grounding validation against the committed State Proof.

Authority
---------
- ``docs/specs/11_CONTRACTS.md`` section 16 and
  ``provenance_contracts.actions.DraftAction.validate_against_proof``.
- ``docs/quality/23_PHASE_GATES.md`` ``G9.3``: "an ungrounded claim in a draft
  cannot ship: ``DRAFT_CLAIM_UNSUPPORTED``, no ``ActionIntent`` created."
- ``docs/specs/15_API_SPEC.md`` section 9.8 step 4.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T9.1``.

The snapshot, not a live re-query
----------------------------------
``T9.1``: load the State Proof by case and revision and validate against *that*
snapshot, "so validation and approval agree about the world". A validator that
re-queried would answer a question about a world newer than the one the draft
was written in, and the draft would then be approved against facts nobody
checked it against. :class:`GroundingSnapshot` is that frozen reading, and it
carries the revision it was taken at so a mismatch is a refusal rather than a
silent substitution.

Two ways a draft is ungrounded, and only one of them is obvious
----------------------------------------------------------------
1. **A claim cites an id the proof does not carry.** Set membership, never
   string similarity: a sentence that paraphrases a real evidence item word for
   word is still unsupported if the id beside it is invented. This half is
   already implemented by the contract and is delegated to it rather than
   rewritten.
2. **A factual sentence cites nothing at all.** This is the half a subset check
   misses *vacuously*: with no claim entry there is no id to test, so a purely
   membership-based validator returns green on a body that asserts anything it
   likes. ``T9.1``'s third sub-task names it -- "a draft sentence that
   paraphrases a real evidence item and cites nothing is unsupported" -- and
   :func:`uncited_factual_sentences` is the answer.

What counts as "factual", and why the rule is narrow
-----------------------------------------------------
A sentence is treated as a factual assertion when it carries a **date, a money
amount, or an identifier-like token**: the three shapes an outbound dispute
letter actually makes checkable claims in, and the three the hero flow turns
on. "Please confirm the account is closed" carries none of them and is a
request, not an assertion.

The rule is deliberately narrow in this direction. A validator that refused
every uncited sentence would refuse every greeting and every signature, would
be turned off within a day, and would then be protecting nothing. Provenance
does not refuse a user's own words -- section 8.25 is explicit that an edited
sentence which loses its support is *marked*, not deleted. What may never
happen is an assertion of fact reaching an ``ActionIntent`` with nothing behind
it, and that is what this refuses.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final

from provenance_contracts.actions import DraftAction
from provenance_contracts.proof import StateProof

__all__ = [
    "BASIS_CASE_MISMATCH",
    "BASIS_REVISION_MISMATCH",
    "COMMITTED_DECISIONS",
    "DRAFT_CLAIM_UNSUPPORTED",
    "NO_COMMITTED_BASIS",
    "SUPPORT_SET_UNAVAILABLE",
    "GroundingSnapshot",
    "GroundingVerdict",
    "UnsupportedClaim",
    "factual_sentences",
    "has_committed_basis",
    "uncited_factual_sentences",
    "validate_draft_claims",
]

#: ``G9.3``'s reason code. One code for both failure shapes, because the
#: product statement is one statement: the draft asserts something the record
#: does not support.
DRAFT_CLAIM_UNSUPPORTED: Final[str] = "DRAFT_CLAIM_UNSUPPORTED"

#: The snapshot describes a different revision than the draft was written at.
BASIS_REVISION_MISMATCH: Final[str] = "BASIS_REVISION_MISMATCH"

#: The snapshot describes a different case entirely.
BASIS_CASE_MISMATCH: Final[str] = "BASIS_CASE_MISMATCH"

#: Invariant 4: an action intent references committed rows only.
NO_COMMITTED_BASIS: Final[str] = "NO_COMMITTED_BASIS"

#: The snapshot never loaded a citation set, so the question was not asked.
#:
#: Distinct from :data:`DRAFT_CLAIM_UNSUPPORTED` on purpose, and the distinction
#: is the difference between two facts that look identical in a response body.
#: ``frozenset()`` is a real answer -- the committed record supports nothing, so
#: every citation is invented. ``None`` is the absence of an answer. A store
#: that had not loaded the set would otherwise report every claim in every
#: draft as unsupported, which renders exactly like a correctly refused draft
#: and is a completely different situation: one is the system working, the
#: other is the system not knowing and saying so anyway.
SUPPORT_SET_UNAVAILABLE: Final[str] = "SUPPORT_SET_UNAVAILABLE"

#: The ``kernel_decisions.decision`` values that leave committed state behind.
#: ``NOOP_DUPLICATE`` is included because it means the Kernel found the state
#: already committed -- there is a basis, it simply did not move. Every
#: ``REJECTED_*`` and every ``PENDING_*`` value is absent: a rejected proposal
#: committed nothing, so an intent resting on it rests on nothing. That is
#: ``G9.6``'s second clause, "a REJECTED proposal cannot produce an
#: ``ActionIntent`` at all", expressed as set membership rather than as a
#: special case.
COMMITTED_DECISIONS: Final[frozenset[str]] = frozenset(
    {"ACCEPTED", "ACCEPTED_WITH_CONFLICT", "NOOP_DUPLICATE"}
)

#: A date in any shape the Advocate's prompt can produce, a currency amount, or
#: an identifier-like token (an invoice or account number). Narrow on purpose;
#: see the module docstring.
_DATE = r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b"
_ISO_DATE = r"\b\d{4}-\d{2}-\d{2}\b"
_MONEY = r"(?:\b(?:usd|eur|gbp)\s*\d|\$\s*\d|\d+\.\d{2}\b)"
_IDENTIFIER = r"\b(?:[a-z]{2,}-\d{2,}[-\w]*|\d{5,})\b"
_FACTUAL: Final[re.Pattern[str]] = re.compile(
    f"{_DATE}|{_ISO_DATE}|{_MONEY}|{_IDENTIFIER}", re.IGNORECASE
)

#: Sentence terminators plus the blank line, because a drafted letter's
#: greeting and signature are separated by newlines rather than by full stops.
_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class GroundingSnapshot:
    """The committed record, frozen at one case revision.

    Five fields and no connection: this is a *reading*, and a reading that
    could re-read itself would defeat the purpose of taking one.
    """

    case_id: uuid.UUID
    case_revision: int
    #: Every id a draft claim may cite, or ``None`` when the reading did not
    #: include one. ``None`` and ``frozenset()`` are different answers -- see
    #: :data:`SUPPORT_SET_UNAVAILABLE`. The type is optional so that a store
    #: which cannot produce the set has no way to imply an empty one, and
    #: ``mypy --strict`` makes every reader confront the case.
    support_ids: frozenset[uuid.UUID] | None
    current_belief_version_ids: frozenset[uuid.UUID]
    has_committed_kernel_decision: bool

    @classmethod
    def from_state_proof(
        cls,
        proof: StateProof,
        *,
        case_id: uuid.UUID,
        case_revision: int,
        current_belief_version_ids: frozenset[uuid.UUID],
        has_committed_kernel_decision: bool,
    ) -> GroundingSnapshot:
        """Read the permitted citation set from ``StateProof.support_ids()``.

        Delegated rather than reimplemented. "Which ids may a draft cite" has
        exactly one definition and it lives in the contract; a second one here
        is how the validator and the proof drift into disagreeing about what
        "supported" means, and the drift would be invisible until a draft cited
        something the UI happily rendered.
        """
        return cls(
            case_id=case_id,
            case_revision=case_revision,
            support_ids=proof.support_ids(),
            current_belief_version_ids=current_belief_version_ids,
            has_committed_kernel_decision=has_committed_kernel_decision,
        )


@dataclass(frozen=True, slots=True)
class UnsupportedClaim:
    """One assertion the committed record does not carry.

    ``claim_id`` is ``None`` when the sentence carries no ``DraftClaim`` at
    all: the two failure shapes are reported in one list because the human
    reading the refusal cares about the sentences, not about which mechanism
    caught them.
    """

    sentence_or_span: str
    claim_id: str | None = None
    uncited_support_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class GroundingVerdict:
    """Grounded, or refused with a reason. There is no third outcome.

    ``T9.1``, third sub-task: "Reject rather than downgrade. A draft that
    cannot be grounded is not softened into a hedge; it is refused with a
    reason code." So this object carries no repaired draft and no hedged body
    -- a softened draft would put an unsupported assertion in front of a human
    wearing the system's confidence.
    """

    grounded: bool
    validated_claim_ids: tuple[str, ...] = ()
    unsupported: tuple[UnsupportedClaim, ...] = ()
    reason_code: str | None = None


def has_committed_basis(decision: str | None) -> bool:
    """Did *decision* leave committed state behind? ``None`` means no decision."""
    return decision in COMMITTED_DECISIONS


def factual_sentences(body: str) -> tuple[tuple[int, int, str], ...]:
    """``(start, end, text)`` for every sentence of *body* asserting a fact.

    Offsets are into *body*, so the result can be compared against
    ``DraftClaim`` spans without re-finding anything by string search -- which
    would pick the wrong occurrence the first time a letter repeated itself.
    """
    out: list[tuple[int, int, str]] = []
    cursor = 0
    for piece in _SENTENCE_SPLIT.split(body):
        start = body.find(piece, cursor)
        if start < 0:  # pragma: no cover - split pieces always occur in order
            continue
        cursor = start + len(piece)
        stripped = piece.strip()
        if stripped and _FACTUAL.search(stripped):
            offset = piece.index(stripped)
            out.append((start + offset, start + offset + len(stripped), stripped))
    return tuple(out)


def uncited_factual_sentences(draft: DraftAction) -> tuple[UnsupportedClaim, ...]:
    """Factual sentences of *draft* that no ``DraftClaim`` span covers.

    Coverage is by character range rather than by string equality: a claim may
    quote a fragment of a sentence, and ``DraftAction`` has already proven that
    every span's offsets really contain the text they quote.
    """
    covered = tuple((claim.char_start, claim.char_end) for claim in draft.claims)
    return tuple(
        UnsupportedClaim(sentence_or_span=text)
        for start, end, text in factual_sentences(draft.body)
        if not any(cs <= start and end <= ce for cs, ce in covered)
    )


def validate_draft_claims(draft: DraftAction, snapshot: GroundingSnapshot) -> GroundingVerdict:
    """Is every factual assertion in *draft* carried by *snapshot*?

    The order of the checks is the order of the failures a reviewer should see
    first. A draft validated against the wrong case or the wrong revision has a
    problem that makes every downstream answer meaningless, so those are
    reported before the grounding result rather than alongside it.
    """
    if draft.case_id != snapshot.case_id:
        return GroundingVerdict(grounded=False, reason_code=BASIS_CASE_MISMATCH)
    if draft.basis_case_revision != snapshot.case_revision:
        return GroundingVerdict(grounded=False, reason_code=BASIS_REVISION_MISMATCH)
    if not snapshot.has_committed_kernel_decision:
        return GroundingVerdict(grounded=False, reason_code=NO_COMMITTED_BASIS)
    if snapshot.support_ids is None:
        # Refuse over the question that was never asked, rather than answering
        # a different one confidently. Reporting DRAFT_CLAIM_UNSUPPORTED here
        # would blame the draft for the store's omission.
        return GroundingVerdict(grounded=False, reason_code=SUPPORT_SET_UNAVAILABLE)

    # Shape 1: delegated to the contract, which owns the subset rule.
    unsupported_ids = set(draft.validate_against_proof(snapshot.support_ids))
    cited = tuple(
        UnsupportedClaim(
            sentence_or_span=claim.sentence_or_span,
            claim_id=claim.claim_id,
            uncited_support_ids=tuple(
                support for support in claim.support_ids if support not in snapshot.support_ids
            ),
        )
        for claim in draft.claims
        if claim.claim_id in unsupported_ids
    )

    # Shape 2: the vacuous pass a membership check cannot see.
    uncited = uncited_factual_sentences(draft)

    unsupported = cited + uncited
    if unsupported:
        return GroundingVerdict(
            grounded=False,
            unsupported=unsupported,
            reason_code=DRAFT_CLAIM_UNSUPPORTED,
        )
    return GroundingVerdict(
        grounded=True,
        validated_claim_ids=tuple(claim.claim_id for claim in draft.claims),
    )
