"""The eight-stage pipeline, and the two rules its ordering satisfies.

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 1, 2 and 5.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 2.2 -- no model or network call
  inside a transaction callback.
- Defect ``D-06-001``.

Vector ANN is the fourth stage, and that is the thesis
--------------------------------------------------------
Retrieval here is not ``top_k`` vector search. ANN is one of eight stages and it
is deliberately placed after the two that can produce *certainty*. An account
number that exact-matches a relationship is worth more than any cosine score,
and a system that lets cosine similarity adjudicate a contradiction is the
failure this product exists to fix. :data:`STAGES` is asserted by a test rather
than left as a comment, because a reordering would be invisible in behaviour
until the one case where it matters.

Retrieval is advisory
----------------------
The Memory Kernel re-reads everything it intends to write about, inside its own
serializable transaction, from fresh reads. A wrong retrieval result produces a
wrong *proposal*, which the kernel then rejects or routes to
``PENDING_IDENTITY``. Retrieval being advisory is what makes it safe to make it
fast -- and it is why this module contains no LLM call of any kind.

Embed first, then open the transaction
----------------------------------------
:data:`EMBED_BEFORE_TRANSACTION` is where two independent rules meet.

``tools/txn_purity_lint.py`` forbids a network call inside a transaction
callback: the callback runs once per retry, so a model call inside it is
charged again on every attempt while the transaction holds its locks, and an
external effect inside it cannot be rolled back when the transaction is.

``D-06-001`` forbids computing the query vector inside the ranking statement:
an ANN query vector supplied as a correlated subquery silently produces a full
scan -- correct results, no error, no warning, survives ``ANALYZE``.

Both are satisfied by the same discipline. The embedding call happens **before**
``BEGIN``, and its result is bound into the statement as a parameter. Neither
rule is satisfied by remembering to; both are satisfied by the order below,
which a test asserts.
"""

from __future__ import annotations

from typing import Final

__all__ = ["EMBED_BEFORE_TRANSACTION", "STAGES", "call_order"]

#: Section 5's ladder, A to H. ``D_VECTOR`` is at index 3 and that placement is
#: the product thesis rather than an implementation detail.
STAGES: Final[tuple[str, ...]] = (
    "A_SCOPE",
    "B_IDENTITY",
    "C_TEMPORAL",
    "D_VECTOR",
    "E_RELATIONAL",
    "F_GROUNDING",
    "G_RERANK",
    "H_CONTEXT",
)

#: Not a toggle. A declaration with a test attached, so that "we embed outside
#: the transaction" is a checkable claim rather than a convention.
EMBED_BEFORE_TRANSACTION: Final[bool] = True


def call_order() -> tuple[str, ...]:
    """The externally observable call sequence of one retrieval pass.

    Named at this granularity because these are the three events the two rules
    are about: the network call, the transaction boundary, and the statement
    that consumes the vector. Everything between them is in-process.
    """
    return (
        "EMBED",
        "BEGIN_TRANSACTION",
        "IDENTITY_CANDIDATES",
        "ANN_SEARCH",
        "RELATIONAL_VALIDATION",
        "GROUNDING_EXPANSION",
        "COMMIT",
        "RERANK",
        "BUILD_CONTEXT",
    )
