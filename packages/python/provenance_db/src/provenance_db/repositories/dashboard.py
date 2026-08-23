"""The home read model — the six counts on one screen.

Authority
---------
- ``specs/15_API_SPEC.md`` section 8.4: "deterministic, no LLM call, and
  explicitly **not** a raw table dump".
- ``CANONICAL_DECISIONS.md`` -> *Corpus counts*: "Any surface rendering
  ``corpus_size_user_scoped`` or ``corpus_size_visible`` renders the value
  counted at query time, never a constant."

Why one module rather than a count per domain module
-----------------------------------------------------
This package splits by domain and never by table, and the dashboard is the one
read that is honestly cross-domain: it is a *read model*, not an aggregate. It
spans conflicts, commitments, action intents, triggers and cases, and putting
its six counts in five modules would leave nobody owning the fact that they
have to be consistent with each other.

The rule the split protects is "a repository spanning two aggregates hides a
transaction boundary". There is no transaction boundary here: every statement
is a ``SELECT``, nothing is written, and the six numbers are explicitly a
snapshot rather than a consistent cut. Section 8.4 stamps ``generated_at`` for
exactly that reason.

Each per-domain count still lives in its own module and is *reached* from
here — ``conflicts.count_open_conflicts``, ``commitments.count_open_commitments``,
``actions.count_pending_action_intents``, ``triggers.get_trigger_counts`` — so
there is one definition of "open conflict" in the system, not two. What this
module adds is the two counts that are properties of ``cases`` themselves and
the assembly.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _fetch_one, _owner

__all__ = [
    "ATTENTION_CASES_SQL",
    "ATTENTION_CASE_COUNT_SQL",
    "count_cases_needing_attention",
    "list_attention_cases",
]

#: ``cases_needing_attention``: ``attention_level <> 'NONE'`` on a live case.
#:
#: The four levels are ``NONE``, ``INFO``, ``ATTENTION`` and ``URGENT``
#: (``CANONICAL_DECISIONS.md`` -> *Case attention levels*, "no aliases are
#: accepted"), so the negation is safe here in a way it is not for the trigger
#: and retraction vocabularies: there is exactly one quiet value and three loud
#: ones. Resolved and superseded cases are excluded — an urgent case that was
#: settled last month is not something to put a number on.
ATTENTION_CASE_COUNT_SQL = """
    SELECT count(*) AS cases_needing_attention
    FROM cases k
    WHERE k.tenant_id = %(tenant_id)s
      AND k.user_id = %(user_id)s
      AND k.attention_level <> 'NONE'
      AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')
"""

#: The ``cases_attention`` strip: the loudest live cases, most urgent first.
#:
#: Ordered by an explicit rank rather than by the column, because ``URGENT``
#: sorts *after* ``NONE`` and before ``ATTENTION`` alphabetically — a
#: ``ORDER BY attention_level DESC`` would put the most urgent case third.
ATTENTION_CASES_SQL = """
    SELECT k.id AS case_id, k.title, k.status, k.revision, k.attention_level,
           k.case_type, k.relationship_id, k.context_id, k.opened_at,
           k.resolved_at, k.last_activity_at, k.reopened_count,
           cp.display_name AS counterparty_display_name,
           cp.kind AS counterparty_kind,
           EXISTS (SELECT 1 FROM conflicts cf
                    WHERE cf.tenant_id = k.tenant_id AND cf.user_id = k.user_id
                      AND cf.case_id = k.id
                      AND cf.status IN ('OPEN', 'NEEDS_HUMAN')) AS has_open_conflict,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
                      AND cm.due_at IS NOT NULL
                      AND cm.due_at < %(now)s::TIMESTAMPTZ) AS has_overdue_commitment,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status = 'PARTIAL') AS has_partial_commitment,
           EXISTS (SELECT 1 FROM prospective_triggers t
                    WHERE t.tenant_id = k.tenant_id AND t.user_id = k.user_id
                      AND t.case_id = k.id
                      AND t.state = 'FIRED') AS has_fired_trigger,
           EXISTS (SELECT 1 FROM action_intents ai
                    WHERE ai.tenant_id = k.tenant_id AND ai.user_id = k.user_id
                      AND ai.case_id = k.id
                      AND ai.status IN ('PROPOSED', 'NEEDS_REVIEW')) AS has_pending_action,
           (SELECT cm.currency FROM commitments cm
             WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
               AND cm.case_id = k.id
               AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
               AND cm.outstanding_amount IS NOT NULL
             ORDER BY cm.outstanding_amount DESC LIMIT 1) AS headline_currency,
           (SELECT cm.outstanding_amount FROM commitments cm
             WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
               AND cm.case_id = k.id
               AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
               AND cm.outstanding_amount IS NOT NULL
             ORDER BY cm.outstanding_amount DESC LIMIT 1) AS headline_outstanding,
           (SELECT cm.due_at FROM commitments cm
             WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
               AND cm.case_id = k.id
               AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
               AND cm.outstanding_amount IS NOT NULL
             ORDER BY cm.outstanding_amount DESC LIMIT 1) AS headline_due_at
    FROM cases k
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    WHERE k.tenant_id = %(tenant_id)s
      AND k.user_id = %(user_id)s
      AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')
      AND (NOT %(attention_only)s::BOOL OR k.attention_level <> 'NONE')
      AND (%(context_id)s::UUID IS NULL OR k.context_id = %(context_id)s::UUID)
      AND (cardinality(%(statuses)s::STRING[]) = 0
           OR k.status = ANY(%(statuses)s::STRING[]))
    ORDER BY CASE k.attention_level
                 WHEN 'URGENT' THEN 0 WHEN 'ATTENTION' THEN 1
                 WHEN 'INFO' THEN 2 ELSE 3 END,
             k.last_activity_at DESC,
             k.id DESC
    LIMIT %(limit)s
"""


async def count_cases_needing_attention(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """How many live cases are asking for something."""
    row = await _fetch_one(conn, ATTENTION_CASE_COUNT_SQL, _owner(tenant_id, user_id))
    return int(row["cases_needing_attention"]) if row else 0


async def list_attention_cases(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 10,
    now: Any = None,
    attention_only: bool = False,
    context_id: uuid.UUID | None = None,
    statuses: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """The home screen's case strip, loudest first.

    The three ``headline_*`` columns are the largest outstanding obligation on
    the case, and they exist so the deterministic headline template has real
    numbers to render: section 8.4's example reads "The promised 30 days
    elapsed and USD 1800.0000 is still outstanding", and the template must be
    able to produce that with Bedrock unavailable.
    """
    return await _fetch_all(
        conn,
        ATTENTION_CASES_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "now": now,
            "attention_only": attention_only,
            "context_id": context_id,
            "statuses": list(statuses),
        },
    )
