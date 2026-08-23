"""Provenance's MCP server: governed, read-only agent access to canonical memory.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts*: the five agent-safe
  views and the five SQL roles.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 14 - Phase 11, ``T11.1``-``T11.5``.
- ``docs/implementation/04_API_EVENTS_SECURITY.md`` sections 20 and 21.
- ``db/migrations/versions/0008_events_infrastructure.py`` - the view
  definitions, ``GRANT SELECT`` on the five views to ``pv_agent_reader``, and
  ``REVOKE ALL ON TABLE`` over all 26 canonical tables from the same role.

The claim this package exists to make demonstrable
---------------------------------------------------
**An agent's database boundary is a SQL grant, not a prompt instruction.**

``pv_agent_reader`` holds ``SELECT`` on ``agent_case_context_v1``,
``agent_active_beliefs_v1``, ``agent_belief_lineage_v1``,
``agent_evidence_retrieval_v1`` and ``agent_open_obligations_v1``, and holds
nothing on any of the 26 base tables. The views execute with their owner's
privileges, so they resolve; the base tables do not. That is the boundary, and
``services/control_plane/tests/db/test_mcp_server.py`` demonstrates it the only
way it can be demonstrated - by issuing a base-table read *through the server's
own connection* and being refused, then issuing the identical statement under a
role that holds the grant and watching it succeed. The only variable is the
role.

What is enforced here, and what is enforced elsewhere
------------------------------------------------------
This package enforces three things that the grant cannot:

- **Scope.** Every statement carries ``tenant_id = %s AND user_id = %s``, bound
  from the caller's verified identity. No tool accepts a ``user_id`` argument -
  not "checks it", *accepts* it - so there is no argument through which an agent
  could name another owner.
- **Shape.** Tools take typed parameters and compose one fixed statement per
  view from a frozen registry. There is no passthrough and no arbitrary SQL
  tool (``00_IMPLEMENTATION_MAP.md`` section 12).
- **Visibility.** Every call, including a refused one, produces one entry for
  ``agent_runs.tool_calls`` so a run is auditable after the fact. The column is
  ``tool_calls``; the HTTP field is ``mcp_tool_calls[]``.

Everything above is application code and could be changed by an edit. The grant
could not. That asymmetry is why the boundary is stated as the grant and the
rest is stated as what it is.

Module map
----------
``scope``       the verified identity a server is built for
``views``       the registry: five views, their projections, their filters
``statements``  composition: fixed text, bound values
``records``     the ``agent_runs.tool_calls`` entry and its closed key set
``ports``       the connection and recorder Protocols
``reader``      one tool call -> one read -> one audit record
``session``     the ``pv_agent_reader`` connection, checked at open
``server``      the FastMCP surface
"""

from __future__ import annotations

__all__ = ["__doc__"]
