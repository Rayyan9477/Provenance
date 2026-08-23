"""The only executable-looking thing Provenance persists, and it is not
executable.

``PredicateNode`` is a closed algebraic term evaluated by deterministic Python
against a whitelisted projection. Trigger predicates are data, never code: the
grammar is closed, the field paths are allowlisted by prefix, and depth is
bounded so a malicious or malformed proposal cannot build an evaluator bomb.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 11, whose code this module implements.
- ``CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, "Trigger
  arithmetic": **"No general arithmetic nodes in the trigger DSL. Add reviewed
  deterministic derived fields to the projection registry."** Restated under
  *Closed former questions*: "Trigger derived comparisons use named projection
  fields, not general arithmetic AST nodes."
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, sixth sub-task.

Why there is no ADD node
------------------------
"Fire when the outstanding balance exceeds zero" needs a subtraction somewhere.
The decision is *where*. An ``ADD``/``SUB`` node would put the arithmetic inside
a persisted, model-authored term — which means the definition of "outstanding"
would be re-derived, differently, by every trigger that ever referenced it, and
a reviewer inspecting a stored predicate could not tell whether it computed the
same thing the Kernel computes. Instead the arithmetic lives once in the
projection registry as a reviewed named field
(``commitments.deposit.outstanding_amount``, which is the money identity from
``provenance_domain.money``), and the predicate only *compares* it. The grammar
in :data:`PREDICATE_ARITY` has no arithmetic member, so this is a property of
the type rather than a rule someone could forget.

The whitelist is enforced twice on purpose: :data:`FieldPath`'s pattern makes a
non-projection root unrepresentable as a string, and
``PredicateNode._validate_shape`` re-checks the root against
:data:`WHITELISTED_FIELD_ROOTS`. The pattern is the structural guard; the
explicit check is what fails loudly if the two ever disagree after an edit.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import Contract
from provenance_domain.enums import PredicateOp

__all__ = [
    "MAX_PREDICATE_DEPTH",
    "PREDICATE_ARITY",
    "WHITELISTED_FIELD_ROOTS",
    "FieldPath",
    "PredicateNode",
]

MAX_PREDICATE_DEPTH: Final[int] = 8

#: Roots of the CaseProjection / CommitmentProjection exposed to predicates.
#:
#: NOTE FOR T1.6 — ``commitments`` and ``conflicts`` are *projection* roots, and
#: they collide by name with two of the 26 canonical tables. The no-SQL test
#: printed in ``specs/11_CONTRACTS.md`` section 20.10 tokenises every non-docstring
#: literal and fails on any token that is a canonical table name, so it will flag
#: this line, and so will the ``no-sql-in-contracts`` rule of ``tools/contract_lint.py``.
#: These names are fixed by section 11 and by the projection registry; they are
#: not table references and no query can be built from them. This needs the same
#: kind of documented carve-out section 20.10 already grants ``AgentSafeView``.
WHITELISTED_FIELD_ROOTS: Final[frozenset[str]] = frozenset(
    {"case", "commitments", "conflicts", "triggers", "clock"}
)

FieldPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^(case|commitments|conflicts|triggers|clock)(\.[a-z0-9_]+){1,5}$",
        max_length=160,
    ),
]

#: ``op -> (minimum args, maximum args)``. The whole grammar, in one table.
#: There is no ADD, SUB, MUL, DIV or CALL member, and adding one is the change
#: that ``CANONICAL_DECISIONS.md`` -> "Trigger arithmetic" forbids. Public
#: rather than private so a test can assert the grammar is exactly ``PredicateOp``.
PREDICATE_ARITY: Final[dict[PredicateOp, tuple[int, int]]] = {
    PredicateOp.AND: (2, 8),
    PredicateOp.OR: (2, 8),
    PredicateOp.NOT: (1, 1),
    PredicateOp.EQ: (2, 2),
    PredicateOp.NE: (2, 2),
    PredicateOp.GT: (2, 2),
    PredicateOp.GTE: (2, 2),
    PredicateOp.LT: (2, 2),
    PredicateOp.LTE: (2, 2),
    PredicateOp.IS_NULL: (1, 1),
    PredicateOp.NOT_NULL: (1, 1),
    PredicateOp.FIELD: (0, 0),
    PredicateOp.CONST: (0, 0),
}

_LEAVES: Final[frozenset[PredicateOp]] = frozenset({PredicateOp.FIELD, PredicateOp.CONST})


class PredicateNode(Contract):
    """One node of a trigger predicate.

    FIELD carries ``path``; CONST carries ``value``; every other operator
    carries ``args``. Mixing them is a validation error, so there is no node
    that is simultaneously a leaf and a branch.
    """

    op: PredicateOp
    path: FieldPath | None = None
    value: JsonValue | None = None
    args: tuple[PredicateNode, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def _validate_shape(self) -> PredicateNode:
        low, high = PREDICATE_ARITY[self.op]
        if not (low <= len(self.args) <= high):
            raise ValueError(
                f"{self.op} takes between {low} and {high} arguments, got {len(self.args)}"
            )
        if self.op is PredicateOp.FIELD:
            if self.path is None:
                raise ValueError("FIELD requires a path")
            if self.value is not None:
                raise ValueError("FIELD must not carry a value")
            root = self.path.split(".", 1)[0]
            if root not in WHITELISTED_FIELD_ROOTS:
                raise ValueError(f"field root {root!r} is not whitelisted")
        elif self.op is PredicateOp.CONST:
            if self.path is not None:
                raise ValueError("CONST must not carry a path")
        elif self.path is not None or self.value is not None:
            raise ValueError(f"{self.op} is a branch node and takes only args")
        if self.op not in _LEAVES and self.depth() > MAX_PREDICATE_DEPTH:
            raise ValueError(
                f"predicate depth {self.depth()} exceeds {MAX_PREDICATE_DEPTH}; "
                "a trigger condition that deep is a modelling error"
            )
        return self

    def depth(self) -> int:
        if not self.args:
            return 1
        return 1 + max(child.depth() for child in self.args)

    def field_paths(self) -> frozenset[str]:
        """Every projection field this term reads, for registry validation."""
        if self.op is PredicateOp.FIELD and self.path is not None:
            return frozenset({self.path})
        if not self.args:
            return frozenset()
        return frozenset[str]().union(*(child.field_paths() for child in self.args))


PredicateNode.model_rebuild()
