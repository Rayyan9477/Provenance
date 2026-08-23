"""The identity every MCP read is scoped by.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` sections 2.5 and 3.3 - ``tenant_id`` and
  ``user_id`` are resolved server-side and are never read from a claim, a
  request body or a tool argument.
- ``provenance_contracts.identity.CapabilityBinding`` - "the Agent Runtime
  presents an ``agent_run_id``, the backend loads the run record, and the
  tenant/user come from that record. A stolen or buggy workload token cannot
  name another user's UUID and be believed, because the UUID it names is never
  consulted."
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``, ``T11.3``.

Why the scope is bound to the server rather than passed to a tool
-----------------------------------------------------------------
An MCP server built here is built **for one agent run**. Its scope is fixed at
construction from the caller's verified identity, which is what lets every tool
signature omit ``user_id`` entirely. That omission is the point: a tool that
accepted an owner argument would have to be trusted to check it, and a check
can be forgotten, mis-ordered, or bypassed by a second code path. An argument
that does not exist cannot be any of those things.

:class:`AgentScope` is frozen so nothing downstream can repoint a live server at
another user between two tool calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

__all__ = ["AgentScope", "VerifiedIdentity"]


class VerifiedIdentity(Protocol):
    """The two fields a scope is built from.

    ``provenance_contracts.identity.Principal`` and ``CapabilityBinding`` both
    satisfy it structurally. Declared as a Protocol rather than importing either
    so this package depends on the *shape* of a verified identity and not on the
    concrete boundary contract another lane owns.
    """

    @property
    def tenant_id(self) -> uuid.UUID: ...

    @property
    def user_id(self) -> uuid.UUID: ...


@dataclass(frozen=True, slots=True)
class AgentScope:
    """One agent run's view of the world.

    ``agent_run_id`` is here because the tool-call record has to land on a row
    (``agent_runs.tool_calls``); it is not part of the read predicate and no
    statement binds it.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    agent_run_id: uuid.UUID

    @classmethod
    def from_verified(cls, identity: VerifiedIdentity, *, agent_run_id: uuid.UUID) -> AgentScope:
        """Build a scope from an already-verified identity.

        There is deliberately no constructor that takes a tenant or user from a
        string, a request body or a tool argument. The only way to obtain a
        scope is to already hold something the auth layer verified.
        """
        return cls(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_run_id=agent_run_id,
        )
