"""Stage H — slot allocation and the budget that refuses (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 11.5 -- the caps, the two
  guarantees, the reduction order, the drop order, and the never-drop set.
- ``provenance_contracts.retrieval.RetrievalContext`` -- the caps as
  ``Field(max_length=)``, so they are unexceedable by construction and this
  module only decides *which* items fill them.

Guarantees before scores
-------------------------
Two reservations are applied before ranking fills anything:

**Conflict evidence, up to three.** Retrieval that drops the evidence behind an
open contradiction has failed at the one job the product is named for -- and it
would fail invisibly, because the remaining nine items still look like a
complete answer.

**The two highest-scoring ``T3_VECTOR_ONLY`` items.** Without this reserve a
mature case with dense structural matches fills every slot, and the system goes
blind to anything new about it. The reserve is what keeps a memory system from
only ever remembering what it already knew.

Refusal, not silent truncation
--------------------------------
``T6.4``'s acceptance says the context "refuses to exceed it rather than
truncating silently". The distinction matters because a silently truncated
context still answers: the model is handed less than the caller believes it
saw, and produces a confident draft about a case it only partly read. Every
drop is named in ``dropped_for_budget`` so the Memory Trace can show what was
withheld and the model can be told its context was truncated.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from services.control_plane.app.retrieval.config import (
    CONTEXT_TOKEN_BUDGET,
    MAX_EVIDENCE_SNIPPETS,
    RESERVED_CONFLICT_SLOTS,
    RESERVED_VECTOR_ONLY_SLOTS,
)
from services.control_plane.app.retrieval.rerank import Tier

__all__ = ["ContextBudgetExceededError", "Slot", "allocate_slots", "enforce_budget"]


class ContextBudgetExceededError(ValueError):
    """The context does not fit and the never-drop set is what is left.

    Section 11.5: after the eight drop steps, retrieval returns
    ``identity_status`` unchanged with ``CONTEXT_BUDGET_EXCEEDED`` rather than
    silently dropping from the never-drop set. Every id that appears anywhere
    in the context is in that set, because ids are what the kernel verifies
    against and a truncated id list turns a verifiable proposal into an
    unverifiable one.
    """


@dataclass(frozen=True)
class Slot:
    """One candidate evidence item competing for one of the ten slots."""

    evidence_id: uuid.UUID
    tier: Tier
    score: float
    backs_open_conflict: bool = False


def allocate_slots(slots: Sequence[Slot], *, limit: int = MAX_EVIDENCE_SNIPPETS) -> list[Slot]:
    """Guarantees first, then ``(tier, -score)`` fills the remainder.

    The reduction order when guarantees collide, from section 11.5: cut the
    ``T3`` reserve to 1, then to 0, then drop the lowest-severity conflict
    evidence -- and never below one conflict item.
    """
    by_rank = sorted(slots, key=lambda slot: (slot.tier.value, -slot.score))

    conflicts = [slot for slot in by_rank if slot.backs_open_conflict]
    vector_only = [slot for slot in by_rank if slot.tier is Tier.T3_VECTOR_ONLY]

    reserved_conflicts = conflicts[:RESERVED_CONFLICT_SLOTS]
    reserved_vector = [slot for slot in vector_only if slot not in reserved_conflicts][
        :RESERVED_VECTOR_ONLY_SLOTS
    ]

    # Reduce the T3 reserve before touching conflict evidence, and never take
    # conflict evidence below one item.
    while len(reserved_conflicts) + len(reserved_vector) > limit and reserved_vector:
        reserved_vector.pop()
    while len(reserved_conflicts) > limit and len(reserved_conflicts) > 1:
        reserved_conflicts.pop()

    chosen: list[Slot] = []
    for slot in (*reserved_conflicts, *reserved_vector):
        if slot not in chosen:
            chosen.append(slot)

    for slot in by_rank:
        if len(chosen) >= limit:
            break
        if slot not in chosen:
            chosen.append(slot)

    return chosen[:limit]


def enforce_budget(
    *, estimated_tokens: int, dropped: Sequence[str], budget: int = CONTEXT_TOKEN_BUDGET
) -> tuple[str, ...]:
    """Return the named drops, or refuse.

    Raises:
        ContextBudgetExceededError: the context still exceeds *budget*. Refusing is
            the contract: a silently truncated context still answers, and it
            answers on less than the caller believes it saw.
    """
    if estimated_tokens > budget:
        raise ContextBudgetExceededError(
            f"retrieval context is {estimated_tokens} tokens against a {budget}-token "
            f"budget after {len(dropped)} drop step(s). The remaining content is the "
            "never-drop set -- open-conflict evidence, the top candidates, sole "
            "SUPPORTS edges, unresolved identity questions, degraded reasons, and "
            "every id in the context. Truncating any of it would hand the model a "
            "context it cannot cite and the kernel a proposal it cannot verify."
        )
    return tuple(dropped)
