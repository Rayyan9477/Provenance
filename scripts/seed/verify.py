"""The eleven verification queries (``T2.8`` step 11).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 18 -- V1 through V11, transcribed.
- ``docs/quality/23_PHASE_GATES.md`` section 8 ``G2.5``: "V1 0  V2 0  V3 0
  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 3". V11 < 3 is a **failure**:
  it means the retraction fixtures were deleted rather than retracted and canon
  item C is untested.

Overlap with ``db/verify.sql``, stated rather than hidden
---------------------------------------------------------
``T2.7`` owns ``db/verify.sql`` and ``make db-verify``, which is the gate's
entry point. This module is ``T2.8`` step 11 -- "run every section 18
verification query and exit non-zero on any violation" -- and it exists so the
seed fails *at the end of its own run* rather than at the next gate. The two
transcribe the same eleven queries from the same section. When ``db/verify.sql``
lands, the honest consolidation is for this module to execute that file; until
then two independent transcriptions of one authority is the safer duplication,
because a disagreement between them is a real signal.

V11 is a positive control, not a check
--------------------------------------
V1-V10 all expect zero rows, and every one of them passes on an empty database.
V11 is the only query in the set that must return **something**, and it is what
stops the whole battery being vacuous.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import psycopg

__all__ = [
    "OUTSTANDING_TOTAL_SQL",
    "VERIFICATION_QUERIES",
    "VerificationReport",
    "outstanding_total",
    "run_verification",
]

#: Section 18, transcribed. Each entry is a list because V6 and V8 are each
#: printed as two statements in the specification and both must return zero.
VERIFICATION_QUERIES: dict[str, list[str]] = {
    # V1. Grounding invariant, part 1.
    "V1": [
        """
        SELECT bv.id, bv.belief_id, bv.version_no
        FROM belief_versions bv
        LEFT JOIN belief_support bs ON bs.belief_version_id = bv.id
        WHERE bv.derivation_kind = 'EVIDENCE_GROUNDED'
        GROUP BY bv.id, bv.belief_id, bv.version_no
        HAVING count(bs.id) = 0
        """
    ],
    # V2. At least one edge must actually SUPPORT.
    "V2": [
        """
        SELECT bv.id
        FROM belief_versions bv
        WHERE bv.derivation_kind = 'EVIDENCE_GROUNDED'
          AND NOT EXISTS (
              SELECT 1 FROM belief_support bs
              WHERE bs.belief_version_id = bv.id AND bs.relation = 'SUPPORTS'
          )
        """
    ],
    # V3. support_edge_count is not a lie.
    "V3": [
        """
        SELECT bv.id, bv.support_edge_count, count(bs.id) AS actual
        FROM belief_versions bv
        LEFT JOIN belief_support bs ON bs.belief_version_id = bv.id
        GROUP BY bv.id, bv.support_edge_count
        HAVING bv.support_edge_count <> count(bs.id)
        """
    ],
    # V4. Polymorphic grounding edges point at rows that exist.
    "V4": [
        """
        SELECT bs.id, bs.source_kind, bs.source_id
        FROM belief_support bs
        WHERE (bs.source_kind = 'EVIDENCE'
               AND NOT EXISTS (SELECT 1 FROM evidence_items e WHERE e.id = bs.source_id))
           OR (bs.source_kind = 'CLAIM'
               AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.id = bs.source_id))
           OR (bs.source_kind = 'BELIEF_VERSION'
               AND NOT EXISTS (SELECT 1 FROM belief_versions v WHERE v.id = bs.source_id))
        """
    ],
    # V5. Aggregate revision invariant.
    "V5": [
        """
        SELECT c.id, c.revision, count(DISTINCT st.case_revision) AS ledger_revisions
        FROM cases c
        JOIN state_transitions st ON st.case_id = c.id
        GROUP BY c.id, c.revision
        HAVING count(DISTINCT st.case_revision) > c.revision
        """
    ],
    # V6. Two dangling-reference checks.
    "V6": [
        """
        SELECT b.id AS belief_id, b.current_version_id
        FROM beliefs b
        WHERE b.current_version_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM belief_versions v
                          WHERE v.id = b.current_version_id AND v.belief_id = b.id)
        """,
        """
        SELECT mp.id
        FROM memory_proposals mp
        WHERE mp.kernel_decision_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM kernel_decisions kd WHERE kd.id = mp.kernel_decision_id)
        """,
    ],
    # V7. Money is coherent across the commitment/fulfillment boundary.
    "V7": [
        """
        SELECT cm.id, cm.fulfilled_amount, coalesce(sum(f.amount), 0) AS admitted_sum
        FROM commitments cm
        LEFT JOIN fulfillments f
          ON f.commitment_id = cm.id AND f.admission_status = 'ADMITTED'
        WHERE cm.committed_amount IS NOT NULL
        GROUP BY cm.id, cm.fulfilled_amount
        HAVING cm.fulfilled_amount <> coalesce(sum(f.amount), 0)
        """
    ],
    # V8. Tenant isolation, sampled on the two tables most likely to be wrong.
    "V8": [
        """
        SELECT e.id FROM evidence_items e JOIN source_artifacts a ON a.id = e.artifact_id
        WHERE a.tenant_id <> e.tenant_id OR a.user_id <> e.user_id
        """,
        """
        SELECT bs.id FROM belief_support bs
        JOIN belief_versions bv ON bv.id = bs.belief_version_id
        WHERE bv.tenant_id <> bs.tenant_id OR bv.user_id <> bs.user_id
        """,
    ],
    # V9. The agent role really has no base-table reach.
    "V9": [
        r"""
        SELECT grantee, table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = 'pv_agent_reader'
          AND table_name NOT LIKE 'agent\_%\_v1'
        """
    ],
    # V10. Retracted evidence is never reachable through the MCP view.
    "V10": [
        """
        SELECT v.evidence_id
        FROM agent_evidence_retrieval_v1 v
        JOIN evidence_items e ON e.id = v.evidence_id
        WHERE e.retraction_status <> 'ACTIVE'
        """
    ],
    # V11. Positive control for V10. Must return AT LEAST 3 rows after seeding.
    "V11": [
        """
        SELECT id, retraction_status, (embedding IS NOT NULL) AS still_embedded
        FROM evidence_items
        WHERE retraction_status <> 'ACTIVE'
        """
    ],
}

#: V1-V10 must return zero rows. V11 must return at least this many.
V11_MINIMUM = 3


@dataclass
class VerificationReport:
    results: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary_line(self) -> str:
        """The single line ``G2.5`` greps for."""
        return "  ".join(f"{name} {self.results[name]}" for name in VERIFICATION_QUERIES)


#: Retries for one verification query. See ``run_verification``.
_VERIFY_RETRIES: Final[int] = 5
_VERIFY_BACKOFF: Final[float] = 1.0


def _count_rows(conn: psycopg.Connection[Any], statements: Sequence[str]) -> int:
    """Run one verification query's statements, retrying on ``40001``.

    This is the THIRD place in the seed where an unretried transaction against a
    busy cluster ended a long run, and the pattern is worth naming rather than
    patching a third time in silence. Observed here::

        psycopg.errors.SerializationFailure: restart transaction:
        ReadWithinUncertaintyIntervalError: read at time ... encountered previous
        write with future timestamp ... within uncertainty interval

    The seed writes 18,035 rows and then immediately reads them back. On a
    multi-node cluster a read can land inside the uncertainty interval of a write
    it did not wait for, and CockroachDB resolves that by asking the client to
    retry. It is normal operation, not a fault -- but it arrives as an exception,
    and an unhandled one at step 11 discards a run that has already spent
    fifty-five minutes building an index.

    Rolling back before retrying is load-bearing: after a serialization failure
    the connection sits in a failed transaction and every later statement returns
    ``current transaction is aborted`` until it is cleared.
    """
    last: Exception | None = None
    for attempt in range(_VERIFY_RETRIES):
        try:
            total = 0
            for sql in statements:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    total += len(cur.fetchall())
            conn.commit()
            return total
        except psycopg.errors.SerializationFailure as exc:  # 40001
            last = exc
            conn.rollback()
            time.sleep(_VERIFY_BACKOFF * (attempt + 1))
    raise RuntimeError(
        f"a verification query returned 40001 on {_VERIFY_RETRIES} consecutive attempts. "
        f"The corpus is loaded and the index may be built; re-run the verification alone "
        f"with `python -m scripts.seed --verify` rather than reseeding, because a reseed "
        f"would drop the ANN index. Last error: {last}"
    )


def run_verification(dsn: str) -> VerificationReport:
    """Run all eleven. Zero rows for V1-V10, at least three for V11."""
    report = VerificationReport()
    with psycopg.connect(dsn) as conn:
        for name, statements in VERIFICATION_QUERIES.items():
            report.results[name] = _count_rows(conn, statements)

    for name in VERIFICATION_QUERIES:
        if name == "V11":
            if report.results[name] < V11_MINIMUM:
                report.failures.append(
                    f"{name} returned {report.results[name]} rows, expected at least "
                    f"{V11_MINIMUM}: the retraction fixtures were deleted rather than "
                    f"retracted and canon item C is untested"
                )
        elif report.results[name] != 0:
            report.failures.append(f"{name} returned {report.results[name]} rows, expected 0")
    return report


# ---------------------------------------------------------------------------
# The one number the landing screen renders
# ---------------------------------------------------------------------------

#: USD 2,020.00 = Harborview 1,800.00 + Beltline 220.00. Northline's obligation
#: is the non-monetary service termination and contributes nothing; the June
#: invoice's USD 186 moves ``epistemic_status`` to ``DISPUTED`` and never moves
#: an amount. Summed over every commitment the hero owns, with no status filter:
#: a filter would let a status bug change the total silently, and
#: ``ck_commitments_outstanding_identity`` already guarantees each row's
#: arithmetic.
OUTSTANDING_TOTAL_SQL = """
SELECT
    coalesce(sum(c.outstanding_amount), 0) AS outstanding_total,
    count(*)                               AS commitment_rows
FROM commitments c
WHERE c.tenant_id = %s AND c.user_id = %s
"""

OUTSTANDING_BY_COUNTERPARTY_SQL = """
SELECT cp.display_name,
       c.commitment_type,
       c.status,
       c.outstanding_amount
FROM commitments c
JOIN cases ca          ON ca.id = c.case_id
JOIN relationships r   ON r.id = ca.relationship_id
JOIN counterparties cp ON cp.id = r.counterparty_id
WHERE c.tenant_id = %s AND c.user_id = %s
ORDER BY cp.display_name
"""


def outstanding_total(dsn: str) -> tuple[Decimal, int, list[tuple[str, str, str, Decimal | None]]]:
    """The seeded outstanding total, the row count behind it, and its breakdown.

    Returns ``(total, commitment_rows, breakdown)``. ``commitment_rows == 0``
    means step 9 has not run: ``commitments`` is a Kernel-written table and the
    Kernel arrives in Phase 4, so the total is legitimately 0 and legitimately
    not yet the product's answer. The caller is expected to say which.
    """
    from scripts.seed.tenants import HERO_TENANT, HERO_USER

    scope = (HERO_TENANT.id, HERO_USER.id)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(OUTSTANDING_TOTAL_SQL, scope)
        row = cur.fetchone()
        total = Decimal(str(row[0])) if row else Decimal("0")
        rows = int(row[1]) if row else 0
        cur.execute(OUTSTANDING_BY_COUNTERPARTY_SQL, scope)
        breakdown = [
            (str(r[0]), str(r[1]), str(r[2]), None if r[3] is None else Decimal(str(r[3])))
            for r in cur.fetchall()
        ]
    return total, rows, breakdown
