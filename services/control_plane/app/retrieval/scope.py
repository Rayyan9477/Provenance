"""Stage A — tenant and security scope (``T6.2``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 6, and its three deliberate
  choices.
- ``docs/specs/11_CONTRACTS.md`` -- ``Principal``, whose ``tenant_id`` and
  ``user_id`` are resolved server-side from ``cognito_sub`` via the users
  table, never read from a token claim.

Nothing in a request body establishes identity
------------------------------------------------
The principal comes from the verified Cognito JWT, or from the M2M token
carrying the user binding the control plane issued when it started the agent
run. :func:`scope_from_principal` takes one argument and it is the principal;
there is deliberately no overload that accepts a user id "for a background
job", because that overload is how every cross-tenant read in this class of
system gets written.

The three session choices, and the failure behind each
--------------------------------------------------------
**``READ ONLY``** makes rule R-3 structural rather than procedural. A retrieval
bug that tries to write fails with ``25006 read_only_sql_transaction`` instead
of corrupting memory.

**``PRIORITY LOW``** — under CockroachDB's serializable isolation a
low-priority reader yields to a concurrent writer rather than pushing it.
Retrieval runs on every artifact and every Advocate invocation; the Memory
Kernel commits rarely and is the thing whose latency users feel. Retrieval must
never be the reason a kernel transaction hits ``40001``.

**No follower reads, ever.** Follower reads are bounded-staleness reads roughly
4.8 seconds behind present. The ingestion graph writes evidence rows and then
calls retrieval within the same run, typically within one second. A stale read
would silently omit the evidence just admitted -- invisible in the happy path
and catastrophic in the duplicate-detection path, because the retrieval would
not see the duplicate it was supposed to find. The latency saving is not worth
a correctness hole that only appears under load.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from provenance_contracts.identity import Principal
from services.control_plane.app.retrieval.config import VECTOR_SEARCH_BEAM_SIZE

__all__ = ["SESSION_STATEMENTS", "CrossUserScopeError", "RetrievalScope", "scope_from_principal"]

#: Section 6's A.2 block. Emitted in order on the retrieval connection.
#:
#: ``vector_search_beam_size`` is ``SET LOCAL`` -- a session variable, not a
#: query hint (``10_DATABASE_DDL.md`` section 5.5). It ships at the documented
#: default and section 16.2 is explicit that it must not be touched before the
#: eval harness exists: tuning a recall parameter without a recall measurement
#: is how a system acquires a number nobody can defend.
SESSION_STATEMENTS: Final[tuple[str, ...]] = (
    "SET application_name = 'provenance-retrieval'",
    "BEGIN TRANSACTION READ ONLY, PRIORITY LOW",
    f"SET LOCAL vector_search_beam_size = {VECTOR_SEARCH_BEAM_SIZE}",
)


class CrossUserScopeError(PermissionError):
    """A row was reached that the scope does not own.

    Raised by the post-hoc audit rather than only by the query predicate: a
    view cannot compel a ``user_id`` predicate (section 20 risk R3), so every
    returned row is checked against the principal and a mismatch fails closed.
    """


@dataclass(frozen=True)
class RetrievalScope:
    """``(tenant_id, user_id)`` from the verified principal, and nothing else.

    Frozen, and carrying no request-derived field. A scope that could be
    widened after construction would make the predicate later stages inherit a
    suggestion rather than a boundary.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID

    def assert_owns(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Row-level ownership check, applied to what came back.

        Raises:
            CrossUserScopeError: the row belongs to another user or tenant.
        """
        if tenant_id != self.tenant_id or user_id != self.user_id:
            raise CrossUserScopeError(
                "retrieval returned a row outside its scope; the user_id vector "
                "prefix is the physical boundary and this means it was not applied"
            )


def scope_from_principal(principal: Principal) -> RetrievalScope:
    """The only way to build a scope. One argument, and it is the principal."""
    return RetrievalScope(tenant_id=principal.tenant_id, user_id=principal.user_id)
