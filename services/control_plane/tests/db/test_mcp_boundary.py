"""The SQL grant boundary: ``T2.6``, re-asserted at ``G11.1`` and ``G11.2``.

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 12 (write-path ownership), section 14
  (the five agent-safe views) and section 15 (the grants themselves).
- ``docs/specs/10_DATABASE_DDL.md`` section 18, verification query ``V9``.
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero commit canon* (``pv_ops_reader``).
- ``docs/quality/23_PHASE_GATES.md`` section 17 - ``G11.1``, ``G11.2``.
- ``docs/ops/40_INFRA_IAC.md`` section 11 - the eleven operational tables
  ``pv_ops_reader`` may read, enumerated.

Why this file exists in Phase 2 and not in Phase 11
---------------------------------------------------
The claim "agent database access is least privilege" is made in the submission,
in the README and to a judge's face. It rests on grants written in migration
``0008``, and grants are the easiest thing in a schema to widen by accident: one
``GRANT SELECT ON ALL TABLES`` in a later migration and the claim is false while
every functional test still passes. So the boundary is asserted the moment it is
created, and asserted again at ``G11.1``/``G11.2`` when the MCP server is wired
to it. Nothing here needs seeded data.

Two kinds of assertion, and the difference matters
--------------------------------------------------
:func:`test_agent_reader_has_no_base_table_grant` reads
``information_schema.role_table_grants``. That is ``V9``, and it proves an
absence in the catalogue. :func:`test_agent_reader_is_refused_a_base_table`
connects **as** ``pv_agent_reader`` and is refused. DDL section 20 risk 11 is
explicit that the catalogue check is the weaker of the two - "the definitive
test is to connect *as* ``pv_agent_reader``" - because a view executing with its
owner's privileges is an assumption about this cluster's behaviour until
something demonstrates it. Both are here.

Credential hygiene
------------------
The ``role_dsn`` fixture returns a ``MaskedDsn``. No test in this module prints,
returns, or asserts on a DSN.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from provenance_domain.enums import AgentSafeView

pytestmark = pytest.mark.db

# --------------------------------------------------------------------------
# The grant map - DDL section 12, transcribed
# --------------------------------------------------------------------------

#: The five agent-safe views, canon names, from the domain enum.
AGENT_VIEWS: frozenset[str] = frozenset(member.value for member in AgentSafeView)

#: Every canonical table. ``pv_agent_reader`` may reach none of them.
ALL_TABLES: frozenset[str] = frozenset(
    {
        "tenants",
        "users",
        "ingest_aliases",
        "counterparties",
        "relationships",
        "contexts",
        "cases",
        "source_artifacts",
        "evidence_items",
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "conflicts",
        "commitments",
        "fulfillments",
        "state_transitions",
        "memory_proposals",
        "kernel_decisions",
        "prospective_triggers",
        "action_intents",
        "action_executions",
        "outbox_events",
        "processed_events",
        "agent_runs",
        "idempotency_records",
    }
)

#: DDL section 12: the Kernel is the only role with ``INSERT``/``UPDATE`` on the
#: canonical planes. Listed by table so a widening shows up as a named diff
#: rather than as a count that drifted.
KERNEL_WRITER_INSERT_UPDATE: frozenset[str] = frozenset(
    {
        "counterparties",
        "relationships",
        "contexts",
        "cases",
        "beliefs",
        "belief_versions",
        "conflicts",
        "commitments",
        "prospective_triggers",
        "kernel_decisions",
        "evidence_items",
    }
)

#: Insert-only for the Kernel: append-only planes. ``claims`` and
#: ``state_transitions`` are never updated; ``belief_support`` edges and
#: ``fulfillments`` are facts, not mutable state; ``outbox_events`` rows are
#: written by the Kernel and *updated* by the dispatcher, which is a different
#: role.
KERNEL_WRITER_INSERT_ONLY: frozenset[str] = frozenset(
    {"claims", "belief_support", "fulfillments", "state_transitions", "outbox_events"}
)

#: The Kernel can never send anything and can never mint an approval.
KERNEL_WRITER_FORBIDDEN: frozenset[str] = frozenset(
    {
        "action_intents",
        "action_executions",
        "ingest_aliases",
        "idempotency_records",
        "processed_events",
    }
)

#: DDL section 12's one deliberate widening of the control plane's reach into a
#: canonical table: the outbox dispatcher marks rows ``DISPATCHED``. It is
#: status only, it is Phase 10, and it is the single exception to "no repository
#: writes a canonical table". Named here so widening it further is a visible
#: edit to a constant with this comment attached.
APP_WRITER_CANONICAL_UPDATE: frozenset[str] = frozenset({"outbox_events"})

#: ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*: "``SELECT`` on the five
#: ``_v1`` views and eleven operational tables". ``ops/40_INFRA_IAC.md``
#: section 11 enumerates them; they are exactly the tables the section 6.3 trace
#: assembly query and the section 7.2 row census read. In particular there is no
#: ``evidence_items``, no ``claims``, no ``belief_versions``.
OPS_READER_TABLES: frozenset[str] = frozenset(
    {
        "source_artifacts",
        "agent_runs",
        "memory_proposals",
        "kernel_decisions",
        "state_transitions",
        "outbox_events",
        "processed_events",
        "prospective_triggers",
        "action_intents",
        "action_executions",
        "idempotency_records",
    }
)

WRITE_PRIVILEGES: frozenset[str] = frozenset({"INSERT", "UPDATE", "DELETE"})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _grants(connection: psycopg.Connection, grantee: str) -> set[tuple[str, str]]:
    """``{(table_name, privilege_type)}`` currently held by *grantee*."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND table_schema = 'public'",
            (grantee,),
        )
        return {(str(row[0]), str(row[1])) for row in cur.fetchall()}


def _tables_with(grants: set[tuple[str, str]], privilege: str) -> set[str]:
    return {table for table, held in grants if held == privilege}


# --------------------------------------------------------------------------
# pv_agent_reader - the whole point of the boundary
# --------------------------------------------------------------------------


def test_agent_reader_has_no_base_table_grant(db_connection) -> None:
    """``V9`` / ``G11.1``: header only, zero data rows.

    The gate runs this query and expects nothing back. Reproduced here as a set
    difference so a failure names the table that leaked rather than printing a
    row count.
    """
    leaked = {
        (table, privilege)
        for table, privilege in _grants(db_connection, "pv_agent_reader")
        if table not in AGENT_VIEWS
    }
    assert leaked == set(), (
        "pv_agent_reader can reach something other than the five agent views: " f"{sorted(leaked)}"
    )


def test_agent_reader_selects_every_agent_view(db_connection) -> None:
    """The other half: the role must actually be able to do its job.

    Without this, revoking every grant in existence would pass the test above.
    """
    granted = _tables_with(_grants(db_connection, "pv_agent_reader"), "SELECT")
    missing = AGENT_VIEWS - granted
    assert missing == set(), f"pv_agent_reader cannot read: {sorted(missing)}"


def test_agent_reader_holds_no_write_privilege_anywhere(db_connection) -> None:
    """Read-only is a grant, not a configuration flag on the MCP server.

    DDL section 14: "The MCP server is read-only by configuration; these grants
    are what makes it read-only by enforcement."
    """
    writes = {
        (table, privilege)
        for table, privilege in _grants(db_connection, "pv_agent_reader")
        if privilege in WRITE_PRIVILEGES
    }
    assert writes == set(), f"pv_agent_reader holds write privileges: {sorted(writes)}"


def test_agent_reader_is_refused_a_base_table(role_dsn: Callable[[str], str]) -> None:
    """``G11.2``: the boundary demonstrated by refusal, from the far side.

    DDL section 20 risk 11: "Views executing with owner privileges is asserted,
    not proven here ... the definitive test is to connect *as* ``pv_agent_reader``
    and attempt ``SELECT * FROM evidence_items`` - that must fail." This is that
    test, and its companion below is the half that must succeed.
    """
    with (
        psycopg.connect(role_dsn("pv_agent_reader")) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo,
    ):
        cur.execute("SELECT id FROM evidence_items LIMIT 1")
    assert "evidence_items" in str(excinfo.value)


def test_agent_reader_reads_a_view_it_has_no_base_table_for(
    role_dsn: Callable[[str], str],
) -> None:
    """Positive control for the refusal above, and the actual mechanism under test.

    ``agent_evidence_retrieval_v1`` reads ``evidence_items`` - the table the
    previous test just proved this role cannot touch. If this succeeds, views on
    this cluster really do execute with the owner's privileges, which is the
    assumption every grant in DDL section 15 rests on. If it fails, Phase 11
    stops (``CANONICAL_DECISIONS.md`` -> Phase 0 verification decisions) rather
    than the grants being weakened.
    """
    with psycopg.connect(role_dsn("pv_agent_reader")) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_evidence_retrieval_v1")
        assert cur.fetchone() is not None


def test_agent_reader_cannot_write_through_a_view_either(
    role_dsn: Callable[[str], str],
) -> None:
    """A view is not a back door. ``INSERT`` through the surface is refused too."""
    with (
        psycopg.connect(role_dsn("pv_agent_reader")) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.Error),
    ):
        cur.execute("INSERT INTO claims (id) VALUES ('00000000-0000-0000-0000-000000000000')")


# --------------------------------------------------------------------------
# pv_kernel_writer - the only canonical writer
# --------------------------------------------------------------------------


def test_kernel_writer_owns_the_canonical_write_path(db_connection) -> None:
    """DDL section 12: the Kernel writes the canonical planes and nothing else does."""
    grants = _grants(db_connection, "pv_kernel_writer")
    inserts = _tables_with(grants, "INSERT")
    updates = _tables_with(grants, "UPDATE")

    missing_insert = (KERNEL_WRITER_INSERT_UPDATE | KERNEL_WRITER_INSERT_ONLY) - inserts
    assert missing_insert == set(), f"pv_kernel_writer cannot INSERT into: {sorted(missing_insert)}"

    missing_update = KERNEL_WRITER_INSERT_UPDATE - updates
    assert missing_update == set(), f"pv_kernel_writer cannot UPDATE: {sorted(missing_update)}"

    over_update = KERNEL_WRITER_INSERT_ONLY & updates
    assert over_update == set(), (
        "append-only tables must stay append-only for the Kernel too: "
        f"{sorted(over_update)} are UPDATE-able"
    )


def test_kernel_writer_can_never_send_or_mint_an_approval(db_connection) -> None:
    """The Kernel has no reach into the action plane or the request-scoped tables.

    An approval is a human act and a send is an external one. A canonical writer
    that could do either would make invariant 4 a Python convention.
    """
    grants = _grants(db_connection, "pv_kernel_writer")
    reachable = {table for table, _ in grants} & KERNEL_WRITER_FORBIDDEN
    writable = {
        table for table, privilege in grants if privilege in WRITE_PRIVILEGES
    } & KERNEL_WRITER_FORBIDDEN
    assert writable == set(), f"pv_kernel_writer can write: {sorted(writable)}"
    assert reachable <= {"action_intents"}, (
        "the Kernel reads action_intents for staleness checks and reaches nothing else "
        f"in the action plane; it currently reaches {sorted(reachable)}"
    )


def test_kernel_writer_updates_memory_proposals_but_never_inserts_them(db_connection) -> None:
    """Agents propose through the control plane; the Kernel only decides.

    ``pv_app_reader_writer`` inserts the proposal on the agent's behalf and the
    Kernel stamps its outcome. A Kernel that could insert a proposal could
    manufacture the evidence for its own decision.
    """
    grants = _grants(db_connection, "pv_kernel_writer")
    assert ("memory_proposals", "UPDATE") in grants
    assert ("memory_proposals", "INSERT") not in grants


# --------------------------------------------------------------------------
# pv_app_reader_writer - non-canonical writes, and exactly one exception
# --------------------------------------------------------------------------


def test_app_writer_touches_no_canonical_table_except_the_outbox_status(
    db_connection,
) -> None:
    """The one legitimate exception, named and bounded.

    DDL section 12 grants ``UPDATE`` on ``outbox_events`` to the control plane so
    the dispatcher can mark a row ``DISPATCHED``. That is the only canonical
    table it may update, and this test is where widening it becomes visible.
    ``evidence_items`` and ``memory_proposals`` are ``INSERT``-only for the same
    reason: the parser admits evidence and the API records a proposal, but only
    the Kernel may change either afterwards.
    """
    grants = _grants(db_connection, "pv_app_reader_writer")
    canonical_updates = _tables_with(grants, "UPDATE") & (
        KERNEL_WRITER_INSERT_UPDATE | KERNEL_WRITER_INSERT_ONLY
    )
    assert canonical_updates == APP_WRITER_CANONICAL_UPDATE, (
        "the control plane may update exactly one canonical table (outbox_events, "
        f"dispatcher status); it can currently update {sorted(canonical_updates)}"
    )
    assert ("evidence_items", "UPDATE") not in grants, "only the Kernel may retract evidence"
    assert ("evidence_items", "INSERT") in grants, "the parser admits evidence before any proposal"
    assert ("memory_proposals", "INSERT") in grants
    assert ("memory_proposals", "UPDATE") not in grants


def test_app_writer_reads_everything(db_connection) -> None:
    """DDL section 12: ``SELECT`` on all 26. Read models are built here, not in the Kernel."""
    granted = _tables_with(_grants(db_connection, "pv_app_reader_writer"), "SELECT")
    missing = ALL_TABLES - granted
    assert missing == set(), f"pv_app_reader_writer cannot read: {sorted(missing)}"


# --------------------------------------------------------------------------
# pv_ops_reader - the credential that provably could not have written the rows
# --------------------------------------------------------------------------


def test_ops_reader_is_strictly_read_only(db_connection) -> None:
    """``CANONICAL_DECISIONS.md``: "no ``INSERT``/``UPDATE``/``DELETE``".

    ``tools/trace_verify.py`` exists to let a sceptic falsify "the Memory Trace
    is a hand-authored fixture". A verifier running as a role that could have
    written the rows proves nothing, so read-only here is the whole argument.
    """
    writes = {
        (table, privilege)
        for table, privilege in _grants(db_connection, "pv_ops_reader")
        if privilege in WRITE_PRIVILEGES
    }
    assert writes == set(), f"pv_ops_reader holds write privileges: {sorted(writes)}"


def test_ops_reader_reads_the_five_views_and_the_eleven_operational_tables(
    db_connection,
) -> None:
    """The enumerated surface, and nothing beyond it.

    No ``evidence_items``, no ``claims``, no ``belief_versions``: an operator
    verifying a trace needs the rows the trace is *assembled from*, not the
    evidence corpus.
    """
    granted = _tables_with(_grants(db_connection, "pv_ops_reader"), "SELECT")
    expected = OPS_READER_TABLES | AGENT_VIEWS
    assert (
        granted == expected
    ), f"unexpected={sorted(granted - expected)} missing={sorted(expected - granted)}"


def test_ops_reader_is_refused_a_write(role_dsn: Callable[[str], str]) -> None:
    """``G12.8``, demonstrated rather than asserted from the catalogue."""
    with (
        psycopg.connect(role_dsn("pv_ops_reader")) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        cur.execute("UPDATE agent_runs SET tool_calls = NULL WHERE false")


# --------------------------------------------------------------------------
# The views themselves - DDL section 14's "what is deliberately absent"
# --------------------------------------------------------------------------


def _view_columns(connection: psycopg.Connection, view: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (view,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def test_every_agent_view_exposes_the_tenancy_pair(db_connection) -> None:
    """DDL section 14: "every view exposes ``tenant_id`` and ``user_id``".

    Not for the agent's benefit - so the MCP server's parameterised query is
    *able* to scope by them. A view without the columns makes the scoping
    unwritable, and the isolation claim becomes a promise about a WHERE clause
    nobody can add.
    """
    for view in sorted(AGENT_VIEWS):
        columns = _view_columns(db_connection, view)
        assert {"tenant_id", "user_id"} <= columns, f"{view} cannot be scoped: {sorted(columns)}"


def test_no_agent_view_exposes_a_withheld_column(db_connection) -> None:
    """DDL section 14's "what is deliberately absent from every view".

    ``cognito_sub`` is an identity secret, ``exact_text`` and ``embedding`` are
    the raw corpus, ``draft_payload`` is an unsent outbound message, and
    ``payload`` is a proposal's unreviewed model output. None of them belongs on
    the MCP surface, and a view that added one would leak it silently.
    """
    withheld = {
        "cognito_sub",
        "alias_hash",
        "draft_payload",
        "exact_text",
        "embedding",
        "payload",
        "source_locator",
        "request_hash",
        "response_body",
    }
    for view in sorted(AGENT_VIEWS):
        leaked = _view_columns(db_connection, view) & withheld
        assert leaked == set(), f"{view} exposes withheld columns: {sorted(leaked)}"


def test_the_retrieval_view_filters_retractions_inside_the_view(db_connection) -> None:
    """Canon item C, enforced at the MCP boundary rather than by the caller.

    ``V10`` returns zero and ``V11`` returns 3 *because the predicate is in the
    view*. An agent using MCP cannot forget it. Asserted here against the view
    definition because Phase 2 has no seeded corpus; ``T2.7``'s ``V10``/``V11``
    pair asserts it against rows.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT view_definition FROM information_schema.views "
            "WHERE table_schema = 'public' AND table_name = %s",
            ("agent_evidence_retrieval_v1",),
        )
        row = cur.fetchone()
    assert row is not None, "agent_evidence_retrieval_v1 does not exist"
    definition = str(row[0])
    assert "retraction_status" in definition, definition
    assert "'ACTIVE'" in definition, definition


def test_the_active_beliefs_view_excludes_retracted_versions(db_connection) -> None:
    """DDL section 14: "``SUPERSEDED`` belief versions excluded".

    The view joins on ``beliefs.current_version_id``, which is what excludes
    superseded rows structurally; the explicit predicate is what excludes
    ``RETRACTED`` ones. Both matter - a retracted version can still be a
    belief's current pointer for the instant before the Kernel repoints it.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT view_definition FROM information_schema.views "
            "WHERE table_schema = 'public' AND table_name = %s",
            ("agent_active_beliefs_v1",),
        )
        row = cur.fetchone()
    assert row is not None, "agent_active_beliefs_v1 does not exist"
    definition = str(row[0])
    assert "current_version_id" in definition, definition
    assert "RETRACTED" in definition, definition
