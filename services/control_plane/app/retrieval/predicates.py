"""Evidence lifecycle filtering — canon item C (``T6.5``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 13, and its three enforcement
  layers.
- ``docs/CANONICAL_DECISIONS.md`` -> *Evidence and retrieval*: only ``ACTIVE``
  evidence may enter new retrieval or ground a new belief, and superseded
  evidence is **excluded** rather than down-weighted.
- ``docs/specs/10_DATABASE_DDL.md`` sections 5.4 and 5.5.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.3(c)``, ``G6.3(d)``, ``G6.7``.

The failure, stated as a failure
---------------------------------
Retracted and superseded evidence **keeps its embedding in the vector index**.
It has to: deleting the row is forbidden by invariant I1, ``belief_support``
edges point at it with ``ON DELETE RESTRICT``, and a retracted item is
frequently the ``CONTRADICTS`` edge that justifies why a belief version was
superseded. So the vector is still there, still competing on cosine distance,
and still capable of being the single closest neighbour to a query -- because a
correction is by construction *about the same subject* as the thing it
corrects, which is exactly what makes it semantically adjacent.

Without the filter, the corrected-away version resurfaces first, grounds a new
belief on a fact the user already disowned, and does so with no error, no
warning, and a completely plausible-looking State Proof.

Excluded, not down-weighted
----------------------------
:func:`retraction_mode` exists to be a refusal. "Down-weight it instead" is the
plausible-sounding alternative -- it sounds gentler, it sounds like it preserves
information -- and it keeps a disowned correction in the prompt at reduced
volume. There is no down-weighted active path in v1, and the function says so
by raising rather than by not existing.

The ``PV_SABOTAGE`` hook
-------------------------
``G6.7`` runs::

    PV_SABOTAGE=retrieval.predicates.retraction_filter pytest ...; echo "exit=$?"

and requires ``G6.3(c)`` to go red. :func:`retraction_filter` is that symbol. A
green run there means the retraction tests do not actually depend on the
filter, which is a gate failure rather than a relief.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any, Final, TypeVar

from provenance_domain import money
from provenance_domain.enums import RetractionStatus

__all__ = [
    "ACTIVE_STATUS",
    "EXCLUDED_STATUSES",
    "RETRACTION_PREDICATE",
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "RetractionDownWeightRefusedError",
    "retraction_filter",
    "retraction_mode",
]

#: The one admitted state. ``13_RETRIEVAL_SPEC.md`` section 13.1's first row.
ACTIVE_STATUS: Final[str] = RetractionStatus.ACTIVE.value

#: The other three. All excluded from active retrieval, for three different
#: reasons -- a wrong observation, a replaced one, and an unsafe one -- and a
#: filter written as ``<> 'RETRACTED'`` lets two of them straight through.
EXCLUDED_STATUSES: Final[frozenset[str]] = frozenset(
    status.value for status in RetractionStatus if status is not RetractionStatus.ACTIVE
)

#: The SQL fragment, in one place. ``13_RETRIEVAL_SPEC.md`` section 13.3 layer 3
#: makes the repository assert this string is present in the vector SQL --
#: a tripwire, not decoration.
RETRACTION_PREDICATE: Final[str] = f"retraction_status = '{ACTIVE_STATUS}'"

_Row = TypeVar("_Row", bound=Mapping[str, Any])

#: The line :func:`retraction_filter` inserts, indented to match the outer
#: ``WHERE`` block of the section 5.5 statement.
_PREDICATE_LINE: Final[str] = "  AND " + RETRACTION_PREDICATE + chr(10)

#: Where it goes: immediately after the tenant predicate, which is the first
#: line of that outer block. Anchoring on a neighbour rather than on a line
#: number means an edit to the projection cannot silently move the insertion
#: point somewhere it does not belong.
_ANCHOR: Final[str] = "WHERE tenant_id = $2" + chr(10)


class RetractionFilterNotAppliedError(RuntimeError):
    """The lifecycle predicate did not land in the statement.

    Raised at import, so a build whose ANN statement lost its retraction filter
    does not start. The alternative is a process that runs perfectly and
    resurfaces corrections the user already made.
    """


class RetractionDownWeightRefusedError(ValueError):
    """Someone asked for the softer option. There is not one."""


def retraction_mode(mode: str = "EXCLUDE") -> str:
    """``"EXCLUDE"``, and nothing else, in v1.

    Raises:
        RetractionDownWeightRefusedError: any other mode, and specifically the
            down-weighting one -- keeping a disowned correction in the prompt at
            reduced volume is still keeping it in the prompt.
    """
    if mode != "EXCLUDE":
        raise RetractionDownWeightRefusedError(
            f"retraction mode {mode!r} is not available: superseded and retracted "
            "evidence is EXCLUDED from active retrieval, never down-weighted. "
            "No down-weighted active path exists in v1 "
            "(CANONICAL_DECISIONS.md -> Evidence and retrieval)."
        )
    return mode


def retraction_filter(sql: str) -> str:
    """Add the lifecycle predicate to a retrieval statement. **The G6.7 symbol.**

    This is the function that *applies* canon item C, not one that checks it
    was applied. The distinction is what makes the sabotage entry mean
    something: ``PV_SABOTAGE`` replaces this with the identity function, which
    returns *sql* unchanged -- and unchanged, at this point in the build, means
    **without the retraction predicate**. The ANN statement then returns
    retracted rows, and ``G6.3(c)`` goes red, which is precisely the claim the
    matrix entry makes.

    A function that instead asserted the predicate were present would be
    unfalsifiable by the same mechanism: neutered to the identity it would
    return a statement that already carried the filter, nothing would change,
    and ``make sabotage`` would report green for a check nobody could break.

    Raises:
        RetractionFilterNotAppliedError: the anchor was not found, so the
            predicate has nowhere to go.
    """
    if RETRACTION_PREDICATE in sql:
        return sql
    if _ANCHOR not in sql:
        raise RetractionFilterNotAppliedError(
            "cannot place the retraction predicate: the statement does not "
            f"carry the expected anchor {_ANCHOR.strip()!r}. Retracted and "
            "superseded evidence keeps its embedding in the ANN index, so a "
            "statement without this predicate returns the correction the user "
            "already made, ranked first, with no error anywhere."
        )
    return sql.replace(_ANCHOR, _ANCHOR + _PREDICATE_LINE, 1)


def active_rows(rows: Iterable[_Row]) -> list[_Row]:
    """Drop every row whose ``retraction_status`` is not ``ACTIVE``.

    The in-process half of layer 3, for statements that project the lifecycle
    column -- the grounding-expansion reads of Stage F and the agent-safe-view
    path. The section 5.5 ANN statement does **not** project it, so this is
    deliberately not applied there: a filter fed rows that cannot carry the
    column would drop all of them, which is a different bug wearing the same
    clothes.

    A row with no ``retraction_status`` key is dropped rather than admitted.
    Absence means the query did not select it, and admitting a row whose
    lifecycle is unknown is the mistake this exists to prevent.
    """
    return [row for row in rows if row.get("retraction_status") == ACTIVE_STATUS]


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `23_PHASE_GATES.md` G6.7 addresses this symbol as
# `retrieval.predicates.retraction_filter`, not by its dotted import path, so
# the module label is explicit rather than `__name__`. The mechanism lives in
# `provenance_domain.money` and is reused rather than re-implemented, for the
# same reason the authority grid is: one definition, one place to be wrong.
#
# Callers must reach this through the module (`predicates.retraction_filter`),
# never through a `from`-import: a `from`-import copies the reference before the
# rebind is visible and the sabotage silently never arrives.

#: The label `tests/sabotage_matrix.yaml` and `G6.7` use for this module.
SABOTAGE_MODULE: Final[str] = "retrieval.predicates"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("retraction_filter",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
