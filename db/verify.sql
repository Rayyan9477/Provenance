-- =============================================================================
-- db/verify.sql - the V1..V11 post-migration verification queries.
--
-- Authority
--   specs/10_DATABASE_DDL.md section 18   the eleven queries, transcribed below
--                                         verbatim. V1..V10 must return zero
--                                         rows; V11 must return at least 3.
--   specs/10_DATABASE_DDL.md section 17.8 the three retraction fixtures V11 counts.
--   quality/23_PHASE_GATES.md section 23.7 "the assertion that passes on an empty
--                                         set" - the positive-control rule.
--   quality/23_PHASE_GATES.md G2.5        make db-verify prints
--                                         "V1 0  V2 0 ... V11 3" and exits 0.
--   EXECUTION/70_TASK_PLAN.md T2.7        the task that owns this file.
--
-- Owned by T2.7. The seed that makes V11 meaningful is T2.8.
--
-- -----------------------------------------------------------------------------
-- THE PAIRING, STATED BEFORE THE QUERIES  (quality/23_PHASE_GATES.md 23.7)
-- -----------------------------------------------------------------------------
-- V10 is a negative assertion: "no retracted row is reachable through the MCP
-- view." V11 is its positive control: "retracted rows exist and still carry
-- vectors, at least 3 of them."
--
-- V10 alone passes on a database with no retracted rows in it, on a database
-- with no rows at all, and on a view that filters nothing over an empty table.
-- Section 23.7 forbids shipping it without the pair, and the mutation that
-- proves the pair does work lives in
-- services/control_plane/tests/db/test_verify_queries.py
-- ::test_v10_retraction_filter_positive_control - it deletes the
-- `WHERE e.retraction_status = 'ACTIVE'` predicate out of
-- agent_evidence_retrieval_v1 and asserts V10 then returns the retracted rows.
-- If that mutation ever stops making V10 non-zero, V10 has stopped verifying.
--
-- Every other "expect zero" check here is paired the same way, by construction
-- rather than by a second query: each one reports `examined=`, the size of the
-- population it ran against, and a status that distinguishes
--
--     HOLDS     returned 0 out of a population that was NOT empty
--     VACUOUS   returned 0 out of a population that WAS empty - proves nothing
--     VIOLATED  returned rows, or (V11) returned fewer than 3
--
-- A verification suite that cannot tell HOLDS from VACUOUS reports success for a
-- database nobody has looked at. That is the failure this file is shaped around.
--
-- -----------------------------------------------------------------------------
-- OUTPUT GRAMMAR - one text column, one line per row
-- -----------------------------------------------------------------------------
--   CHECK <id> returned=<n> examined=<n> expect=<ZERO|ATLEAST3> status=<...>
--         population=<token>
--   SUMMARY V1 0  V2 0  V3 0  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 3
--   VERDICT <code> <one line of prose>
--
-- Verdict codes and the exit status `make db-verify` maps them to:
--   PASS                  0   every check examined a non-empty population
--   PASS_PARTIAL          0   invariants hold; some checks examined nothing,
--                             and the message names which
--   FAIL_INVARIANT        1   a V1..V10 query returned rows
--   FAIL_V11_UNDERSEEDED  1   corpus present, fewer than 3 retracted rows
--   VACUOUS_EMPTY_CORPUS  2   evidence_items is empty: V11 = 0 is CORRECT here,
--                             and V1..V10 returning zero proves nothing
--
-- -----------------------------------------------------------------------------
-- WHY THIS IS ONE STATEMENT
-- -----------------------------------------------------------------------------
-- `psql -f db/verify.sql` and `cursor.execute(open("db/verify.sql").read())` then
-- run identical bytes, so the gate and the test cannot diverge. It also means the
-- verdict is computed once, beside the numbers it is drawn from, instead of being
-- reconstructed by a shell pipeline that nothing tests.
--
-- Deviations, recorded rather than assumed:
--   * The `cockroach` CLI is NOT installed on the build machine. 23_PHASE_GATES
--     writes G2.5 with `cockroach sql`; this file is executed with `psql`, which
--     is wire-compatible and is what the Makefile already uses for G2.2..G2.4.
--   * psql on Windows emits CRLF. Any consumer diffing this output against a
--     repository file must pipe through `tr -d '\r'` first.
-- =============================================================================

WITH

-- V1. Grounding invariant, part 1: no canonical belief version lacks a support edge
--     unless it is a declared deterministic derivation.
--     Expect ZERO. Non-vacuous only when belief_versions holds EVIDENCE_GROUNDED rows.
v1 AS (
    SELECT bv.id, bv.belief_id, bv.version_no
    FROM belief_versions bv
    LEFT JOIN belief_support bs ON bs.belief_version_id = bv.id
    WHERE bv.derivation_kind = 'EVIDENCE_GROUNDED'
    GROUP BY bv.id, bv.belief_id, bv.version_no
    HAVING count(bs.id) = 0
),

-- V2. Grounding invariant, part 2: at least one edge must actually SUPPORT.
--     A version grounded only in CONTRADICTS edges is not grounded.
v2 AS (
    SELECT bv.id
    FROM belief_versions bv
    WHERE bv.derivation_kind = 'EVIDENCE_GROUNDED'
      AND NOT EXISTS (
          SELECT 1 FROM belief_support bs
          WHERE bs.belief_version_id = bv.id AND bs.relation = 'SUPPORTS'
      )
),

-- V3. support_edge_count is not a lie.
v3 AS (
    SELECT bv.id, bv.support_edge_count, count(bs.id) AS actual
    FROM belief_versions bv
    LEFT JOIN belief_support bs ON bs.belief_version_id = bv.id
    GROUP BY bv.id, bv.support_edge_count
    HAVING bv.support_edge_count <> count(bs.id)
),

-- V4. Polymorphic grounding edges point at rows that exist.
--     belief_support.source_id carries no foreign key (DDL section 20 risk 4);
--     this query is the compensating control, so its `examined` count is the one
--     to read before believing its zero.
v4 AS (
    SELECT bs.id, bs.source_kind, bs.source_id
    FROM belief_support bs
    WHERE (bs.source_kind = 'EVIDENCE'
           AND NOT EXISTS (SELECT 1 FROM evidence_items e WHERE e.id = bs.source_id))
       OR (bs.source_kind = 'CLAIM'
           AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.id = bs.source_id))
       OR (bs.source_kind = 'BELIEF_VERSION'
           AND NOT EXISTS (SELECT 1 FROM belief_versions v WHERE v.id = bs.source_id))
),

-- V5. Aggregate revision invariant: a case's revision equals the number of distinct
--     revisions recorded in its ledger.
v5 AS (
    SELECT c.id, c.revision, count(DISTINCT st.case_revision) AS ledger_revisions
    FROM cases c
    JOIN state_transitions st ON st.case_id = c.id
    GROUP BY c.id, c.revision
    HAVING count(DISTINCT st.case_revision) > c.revision
),

-- V6. beliefs.current_version_id and memory_proposals.kernel_decision_id do not dangle.
--     Section 18 writes V6 as two statements; both are counted under V6.
v6a AS (
    SELECT b.id AS belief_id, b.current_version_id
    FROM beliefs b
    WHERE b.current_version_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM belief_versions v
                      WHERE v.id = b.current_version_id AND v.belief_id = b.id)
),
v6b AS (
    SELECT mp.id
    FROM memory_proposals mp
    WHERE mp.kernel_decision_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM kernel_decisions kd WHERE kd.id = mp.kernel_decision_id)
),

-- V7. Money is coherent across the commitment/fulfillment boundary. The CHECKs prove
--     the row is internally consistent; this proves it matches its admitted evidence.
v7 AS (
    SELECT cm.id, cm.fulfilled_amount, coalesce(sum(f.amount), 0) AS admitted_sum
    FROM commitments cm
    LEFT JOIN fulfillments f
      ON f.commitment_id = cm.id AND f.admission_status = 'ADMITTED'
    WHERE cm.committed_amount IS NOT NULL
    GROUP BY cm.id, cm.fulfilled_amount
    HAVING cm.fulfilled_amount <> coalesce(sum(f.amount), 0)
),

-- V8. Tenant isolation: nothing is stitched across tenants. Sampled here for the two
--     tables most likely to be got wrong; the full sweep is generated in CI for all 26.
--     Section 18 writes V8 as two statements; both are counted under V8.
v8a AS (
    SELECT e.id FROM evidence_items e JOIN source_artifacts a ON a.id = e.artifact_id
    WHERE a.tenant_id <> e.tenant_id OR a.user_id <> e.user_id
),
v8b AS (
    SELECT bs.id FROM belief_support bs JOIN belief_versions bv ON bv.id = bs.belief_version_id
    WHERE bv.tenant_id <> bs.tenant_id OR bv.user_id <> bs.user_id
),

-- V9. The agent role really has no base-table reach. Expect ZERO rows.
--     `examined` here is every grant pv_agent_reader holds, view grants included:
--     a zero over zero grants means the role is absent, not that it is contained.
v9 AS (
    SELECT grantee, table_name, privilege_type
    FROM information_schema.role_table_grants
    WHERE grantee = 'pv_agent_reader'
      AND table_name NOT LIKE 'agent\_%\_v1'
),

-- V10. Retracted evidence is never reachable through the MCP view. Expect ZERO rows.
--      POSITIVE CONTROL: V11, below. See the header. The filter this depends on is
--      inside agent_evidence_retrieval_v1 (`WHERE e.retraction_status = 'ACTIVE'`),
--      so `examined` counts the rows that view exposes: a zero over an empty view
--      is VACUOUS and is reported as such.
v10 AS (
    SELECT v.evidence_id
    FROM agent_evidence_retrieval_v1 v
    JOIN evidence_items e ON e.id = v.evidence_id
    WHERE e.retraction_status <> 'ACTIVE'
),

-- V11. Positive control for V10: retracted rows still exist and still have vectors.
--      This one must return AT LEAST 3 rows after seeding. If it returns zero, the
--      retraction fixtures were deleted instead of retracted and canon item C is
--      untested.
--      The three (section 17.8): isp-wrong-term-date (SUPERSEDED, EXTRACTION_ERROR),
--      movers-350-claim (RETRACTED, USER_CORRECTION),
--      injected-instruction (QUARANTINED, ADVERSARIAL_CONTENT).
v11 AS (
    SELECT id, retraction_status, (embedding IS NOT NULL) AS still_embedded
    FROM evidence_items
    WHERE retraction_status <> 'ACTIVE'
),

-- ---------------------------------------------------------------------------
-- The populations. Without these every zero above is unreadable.
-- ---------------------------------------------------------------------------
pop AS (
    SELECT
        (SELECT count(*) FROM belief_versions
          WHERE derivation_kind = 'EVIDENCE_GROUNDED')            AS grounded_versions,
        (SELECT count(*) FROM belief_versions)                    AS belief_versions,
        (SELECT count(*) FROM belief_support)                     AS support_edges,
        (SELECT count(DISTINCT c.id) FROM cases c
           JOIN state_transitions st ON st.case_id = c.id)        AS ledgered_cases,
        (SELECT count(*) FROM beliefs WHERE current_version_id IS NOT NULL)
      + (SELECT count(*) FROM memory_proposals
          WHERE kernel_decision_id IS NOT NULL)                   AS pointer_rows,
        (SELECT count(*) FROM commitments
          WHERE committed_amount IS NOT NULL)                     AS money_commitments,
        (SELECT count(*) FROM evidence_items e
           JOIN source_artifacts a ON a.id = e.artifact_id)
      + (SELECT count(*) FROM belief_support bs
           JOIN belief_versions bv ON bv.id = bs.belief_version_id) AS parented_rows,
        (SELECT count(*) FROM information_schema.role_table_grants
          WHERE grantee = 'pv_agent_reader')                      AS agent_grants,
        (SELECT count(*) FROM agent_evidence_retrieval_v1)        AS retrievable_rows,
        (SELECT count(*) FROM evidence_items)                     AS evidence_rows
),

raw_checks AS (
    SELECT  1 AS ord, 'V1'  AS id, (SELECT count(*) FROM v1)  AS returned,
            p.grounded_versions AS examined, 'ZERO' AS expect,
            'belief_versions_evidence_grounded' AS population FROM pop p
    UNION ALL
    SELECT  2, 'V2', (SELECT count(*) FROM v2), p.grounded_versions, 'ZERO',
            'belief_versions_evidence_grounded' FROM pop p
    UNION ALL
    SELECT  3, 'V3', (SELECT count(*) FROM v3), p.belief_versions, 'ZERO',
            'belief_versions' FROM pop p
    UNION ALL
    SELECT  4, 'V4', (SELECT count(*) FROM v4), p.support_edges, 'ZERO',
            'belief_support' FROM pop p
    UNION ALL
    SELECT  5, 'V5', (SELECT count(*) FROM v5), p.ledgered_cases, 'ZERO',
            'cases_with_ledger_rows' FROM pop p
    UNION ALL
    SELECT  6, 'V6', (SELECT count(*) FROM v6a) + (SELECT count(*) FROM v6b),
            p.pointer_rows, 'ZERO', 'belief_and_proposal_pointers' FROM pop p
    UNION ALL
    SELECT  7, 'V7', (SELECT count(*) FROM v7), p.money_commitments, 'ZERO',
            'commitments_with_committed_amount' FROM pop p
    UNION ALL
    SELECT  8, 'V8', (SELECT count(*) FROM v8a) + (SELECT count(*) FROM v8b),
            p.parented_rows, 'ZERO', 'evidence_and_support_child_rows' FROM pop p
    UNION ALL
    SELECT  9, 'V9', (SELECT count(*) FROM v9), p.agent_grants, 'ZERO',
            'grants_held_by_pv_agent_reader' FROM pop p
    UNION ALL
    SELECT 10, 'V10', (SELECT count(*) FROM v10), p.retrievable_rows, 'ZERO',
            'rows_visible_in_agent_evidence_retrieval_v1' FROM pop p
    UNION ALL
    SELECT 11, 'V11', (SELECT count(*) FROM v11), p.evidence_rows, 'ATLEAST3',
            'evidence_items' FROM pop p
),

scored AS (
    SELECT c.ord, c.id, c.returned, c.examined, c.expect, c.population,
           CASE
               WHEN c.expect = 'ZERO' AND c.returned > 0        THEN 'VIOLATED'
               WHEN c.expect = 'ZERO' AND c.examined = 0        THEN 'VACUOUS'
               WHEN c.expect = 'ZERO'                           THEN 'HOLDS'
               WHEN c.expect = 'ATLEAST3' AND c.examined = 0    THEN 'VACUOUS'
               WHEN c.expect = 'ATLEAST3' AND c.returned < 3    THEN 'VIOLATED'
               ELSE 'HOLDS'
           END AS status
    FROM raw_checks c
),

tally AS (
    SELECT
        (SELECT count(*) FROM scored WHERE expect = 'ZERO' AND status = 'VIOLATED')
            AS zero_violations,
        (SELECT status   FROM scored WHERE id = 'V11') AS v11_status,
        (SELECT returned FROM scored WHERE id = 'V11') AS v11_returned,
        (SELECT examined FROM scored WHERE id = 'V11') AS corpus_rows,
        (SELECT count(*) FROM scored WHERE status = 'VACUOUS') AS vacuous_checks,
        (SELECT string_agg(id || ' returned ' || returned::STRING
                             || ' of ' || examined::STRING || ' examined', ', ')
           FROM (SELECT * FROM scored WHERE expect = 'ZERO' AND status = 'VIOLATED'
                  ORDER BY ord) violated) AS violated_list,
        (SELECT string_agg(id, ', ')
           FROM (SELECT * FROM scored WHERE status = 'VACUOUS' ORDER BY ord) vac)
            AS vacuous_list,
        (SELECT string_agg(id || ' ' || returned::STRING, '  ')
           FROM (SELECT * FROM scored ORDER BY ord) all_checks) AS summary
),

verdict AS (
    SELECT
        CASE
            WHEN t.zero_violations > 0        THEN 'FAIL_INVARIANT'
            WHEN t.corpus_rows = 0            THEN 'VACUOUS_EMPTY_CORPUS'
            WHEN t.v11_status = 'VIOLATED'    THEN 'FAIL_V11_UNDERSEEDED'
            WHEN t.vacuous_checks > 0         THEN 'PASS_PARTIAL'
            ELSE 'PASS'
        END AS code,
        CASE
            WHEN t.zero_violations > 0 THEN
                'V1-V10 must return zero rows (10_DATABASE_DDL.md section 18). '
                || 'These did not: ' || coalesce(t.violated_list, '?') || '.'
            WHEN t.corpus_rows = 0 THEN
                'evidence_items is empty, so V11 returning 0 is CORRECT here and '
                || 'V1-V10 returning zero proves nothing - no row was examined. '
                || 'Seed first (T2.8), which writes the three section 17.8 retraction '
                || 'fixtures isp-wrong-term-date, movers-350-claim and '
                || 'injected-instruction, then run this again.'
            WHEN t.v11_status = 'VIOLATED' THEN
                'V11 returned ' || t.v11_returned::STRING || ' over a corpus of '
                || t.corpus_rows::STRING || ' evidence rows; section 17.8 requires at '
                || 'least 3 retraction fixtures: isp-wrong-term-date (SUPERSEDED, '
                || 'EXTRACTION_ERROR), movers-350-claim (RETRACTED, USER_CORRECTION), '
                || 'injected-instruction (QUARANTINED, ADVERSARIAL_CONTENT). Below 3 '
                || 'means they were deleted instead of retracted, V10 is passing '
                || 'vacuously, and canon item C is untested.'
            WHEN t.vacuous_checks > 0 THEN
                'V1-V10 returned zero and V11 returned ' || t.v11_returned::STRING
                || ', but these checks examined no rows and are therefore unproven: '
                || coalesce(t.vacuous_list, '?') || '.'
            ELSE
                'V1-V10 returned zero over non-empty populations and V11 returned '
                || t.v11_returned::STRING || ' retracted rows (>= 3).'
        END AS message
    FROM tally t
),

report AS (
    SELECT s.ord AS ord,
           'CHECK ' || s.id || ' returned=' || s.returned::STRING
             || ' examined=' || s.examined::STRING
             || ' expect=' || s.expect
             || ' status=' || s.status
             || ' population=' || s.population AS line
    FROM scored s
    UNION ALL
    SELECT 50, 'SUMMARY ' || t.summary FROM tally t
    UNION ALL
    SELECT 99, 'VERDICT ' || v.code || ' ' || v.message FROM verdict v
)

SELECT line FROM report ORDER BY ord;
