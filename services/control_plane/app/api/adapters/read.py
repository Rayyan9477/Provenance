"""``ReadPort`` bound to ``provenance_db.repositories``.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.3-8.29.
- ``services/control_plane/app/api/ports.py`` -- the protocol and the reason
  the routes depend on it.

The one rule this module obeys
-------------------------------
**No SQL.** Every statement is a constant in ``provenance_db.repositories``,
and this class chooses which one to call and reshapes what comes back. That
is not a stylistic preference: it means there is exactly one definition of
each scoping predicate in the system, so a cross-user leak has to be
introduced in a file whose whole purpose is to carry those predicates and
whose test suite scans for them. ``tests/api/test_port_adapters.py`` asserts
the absence of SQL here by walking the AST.

Where a method has no repository behind it, it raises -- see
``adapters/unbound.py`` for why an empty list would be the worse answer.

Pagination
----------
Section 5.4 rule 1: the query asks for ``limit + 1``, the extra row is
dropped, and ``has_more`` is read from whether it existed. That arithmetic is
here rather than in each repository function because the repositories answer
"give me n rows" and the *pagination contract* is an API concern -- a
repository that silently returned one more row than it was asked for would be
a surprise to every other caller.

The clock
---------
Injected, never read from the wall. "Overdue" is a comparison against a clock,
and the case header, the commitment list and the trigger predicate must all
get the same answer -- ``CANONICAL_DECISIONS.md`` -> *Deposit ``due_at``*
derives "95 days" from one instant, and three surfaces reading three clocks is
how that becomes three numbers.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from provenance_db.repositories import (
    actions,
    artifacts,
    beliefs,
    cases,
    commitments,
    conflicts,
    contexts,
    dashboard,
    evidence,
    relationships,
    timeline,
    triggers,
    users,
)
from services.control_plane.app.api.adapters import render
from services.control_plane.app.api.adapters.catalog import ConnectionSource, agent_view_names
from services.control_plane.app.api.adapters.unbound import unbound
from services.control_plane.app.api.ports import OwnerScope

__all__ = ["DEFAULT_FEATURE_FLAGS", "SqlReadPort"]

Row = dict[str, Any]
Rows = tuple[list[Row], bool]

#: Section 8.3: "Clients must treat an absent flag as ``false``." The defaults
#: are therefore all ``False`` except the one capability that needs no external
#: service. ``fixture_mode`` is deliberately absent: the route merges it from
#: :class:`ApiConfig` so that ``GET /v1/me`` and ``GET /v1/version`` read the
#: same value and cannot disagree (``CANONICAL_DECISIONS.md`` ->
#: *Operating-mode disclosure*).
DEFAULT_FEATURE_FLAGS: Mapping[str, bool] = {
    "ses_inbound_enabled": False,
    "upload_ingest_enabled": False,
    "counterfactual_enabled": False,
    "mcp_trace_visible": False,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _after(
    after: tuple[list[str], uuid.UUID] | None,
) -> tuple[str | None, uuid.UUID | None]:
    """A decoded cursor as ``(sort_value, last_id)``.

    The sort value stays a **string**. ``decode_cursor`` returns the tuple as
    strings and the statements cast it -- ``%(after_...)s::TIMESTAMPTZ`` --
    server-side, so there is no parse step here that could disagree with the
    server about what ``2026-06-20T00:00:00+00:00`` means.
    """
    if after is None:
        return None, None
    sort_key, last_id = after
    return (sort_key[0] if sort_key else None), last_id


def _page(rows: list[Row], limit: int) -> Rows:
    """Section 5.4 rule 1: drop the probe row, report that it existed."""
    return rows[:limit], len(rows) > limit


class SqlReadPort:
    """The public read surface, over one ``pv_app_reader_writer`` pool.

    The pool arrives as a :class:`ConnectionSource` rather than as a
    ``RolePool`` so the whole class is drivable with a recording double and no
    cluster. That is what makes "does this method bind the owner?" a hermetic
    unit test instead of an integration test nobody runs on a laptop.
    """

    __slots__ = ("_clock", "_flags", "_source")

    def __init__(
        self,
        source: ConnectionSource,
        *,
        feature_flags: Mapping[str, bool] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._source = source
        self._flags = dict(DEFAULT_FEATURE_FLAGS) | dict(feature_flags or {})
        self._clock = clock

    # -- 8.3 / 8.4 --------------------------------------------------------

    async def me(self, scope: OwnerScope) -> Row | None:
        """Section 8.3. One indexed read plus the alias status it joins."""
        async with self._source.connection() as conn:
            row = await users.get_me(conn, tenant_id=scope.tenant_id, user_id=scope.user_id)
        if row is None:
            return None
        return {
            "home_region": row.get("home_region"),
            "feature_flags": dict(self._flags),
            "ingest_alias_status": row.get("ingest_alias_status"),
        }

    async def dashboard(
        self,
        scope: OwnerScope,
        *,
        context_id: uuid.UUID | None = None,
        attention_only: bool = False,
        statuses: tuple[str, ...] = (),
    ) -> Row:
        """Section 8.4. Six counts, three collections, no model call.

        Every number is counted at query time. ``CANONICAL_DECISIONS.md`` ->
        *Corpus counts* forbids rendering a constant where a counted value
        belongs, and the same reasoning covers every tile here: a figure
        nobody counted is a figure nobody can defend when a judge asks where
        it came from.
        """
        now = self._clock()
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        async with self._source.connection() as conn:
            counts = {
                "unresolved_commitments": await commitments.count_open_commitments(conn, **owner),
                "active_conflicts": await conflicts.count_open_conflicts(conn, **owner),
                "action_intents_pending": await actions.count_pending_action_intents(conn, **owner),
                "cases_needing_attention": await dashboard.count_cases_needing_attention(
                    conn, **owner
                ),
            }
            counts.update(await triggers.get_trigger_counts(conn, **owner))
            context_rows = await contexts.list_contexts(conn, **owner, limit=25)
            context_totals = await contexts.list_context_outstanding(conn, **owner)
            relationship_rows = await relationships.list_relationships(
                conn, **owner, limit=25, context_id=context_id
            )
            relationship_totals = await relationships.list_relationship_outstanding(conn, **owner)
            attention_rows = await dashboard.list_attention_cases(
                conn,
                **owner,
                limit=10,
                now=now,
                attention_only=attention_only,
                context_id=context_id,
                statuses=statuses,
            )
        return {
            "counts": counts,
            "contexts": [
                _context_summary(row, context_totals)
                for row in context_rows
                if context_id is None or row["context_id"] == context_id
            ],
            "relationships_summary": [
                _relationship_summary(row, relationship_totals) for row in relationship_rows
            ],
            "cases_attention": [render.case_item(row) for row in attention_rows],
        }

    # -- 8.5 - 8.7 --------------------------------------------------------

    async def list_contexts(
        self, scope: OwnerScope, *, limit: int, after: tuple[list[str], uuid.UUID] | None = None
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await contexts.list_contexts(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                after_created_at=sort_value,
                after_id=last_id,
            )
            totals = await contexts.list_context_outstanding(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id
            )
        items = [_context_summary(row, totals) for row in rows]
        return _page(items, limit)

    async def list_relationships(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await relationships.list_relationships(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                statuses=tuple(filters.get("statuses") or ()),
                counterparty_id=filters.get("counterparty_id"),
                context_id=filters.get("context_id"),
                after_updated_at=sort_value,
                after_id=last_id,
            )
            totals = await relationships.list_relationship_outstanding(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id
            )
        items = [_relationship_summary(row, totals) for row in rows]
        if filters.get("attention_only"):
            items = [item for item in items if item["attention_level"] != "NONE"]
        return _page(items, limit)

    async def get_relationship(self, scope: OwnerScope, relationship_id: uuid.UUID) -> Row | None:
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        async with self._source.connection() as conn:
            row = await relationships.get_relationship(
                conn, **owner, relationship_id=relationship_id
            )
            if row is None:
                return None
            case_rows = await relationships.list_relationship_cases(
                conn, **owner, relationship_id=relationship_id
            )
            totals = await relationships.list_relationship_outstanding(conn, **owner)
        outstanding = [
            render.money(total["currency"], total["outstanding"])
            for total in totals
            if total["relationship_id"] == relationship_id
        ]
        live = [c for c in case_rows if c["status"] not in ("RESOLVED", "SUPERSEDED")]
        return {
            "relationship_id": str(row["relationship_id"]),
            "counterparty": _counterparty(row),
            "label": row.get("label"),
            "relationship_type": row["relationship_type"],
            "status": row["status"],
            "external_account_ref_masked": row.get("external_account_ref_masked"),
            "normalized_identifiers": row.get("normalized_identifiers") or {},
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "revision": int(row.get("revision") or 0),
            "updated_at": row.get("updated_at"),
            "context": None,
            "cases": [
                {
                    "case_id": str(c["case_id"]),
                    "title": c["title"],
                    "status": c["status"],
                    "revision": int(c["revision"]),
                    "attention_level": c["attention_level"],
                    "case_type": c["case_type"],
                    "opened_at": c["opened_at"],
                    "resolved_at": c.get("resolved_at"),
                    "last_activity_at": c["last_activity_at"],
                }
                for c in case_rows
            ],
            "summary": {
                "total_cases": len(case_rows),
                "open_cases": len(live),
                "outstanding": [item for item in outstanding if item is not None],
            },
        }

    # -- 8.8 - 8.13 -------------------------------------------------------

    async def list_cases(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await cases.list_case_index(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                statuses=tuple(filters.get("statuses") or ()),
                case_types=tuple(filters.get("case_types") or ()),
                relationship_id=filters.get("relationship_id"),
                context_id=filters.get("context_id"),
                attention_only=bool(filters.get("attention_only")),
                now=self._clock(),
                after_last_activity_at=sort_value,
                after_id=last_id,
            )
        return _page([render.case_item(row) for row in rows], limit)

    async def get_case(self, scope: OwnerScope, case_id: uuid.UUID) -> Row | None:
        """Section 8.9. The canonical case projection, six reads, no model."""
        now = self._clock()
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        async with self._source.connection() as conn:
            row = await cases.get_case_detail(conn, **owner, case_id=case_id, now=now)
            if row is None:
                return None
            commitment_rows = await commitments.list_commitments_for_case(
                conn, **owner, case_id=case_id
            )
            conflict_rows = await conflicts.list_conflicts_for_case(
                conn, **owner, case_id=case_id, limit=25, statuses=conflicts.UNRESOLVED_STATUSES
            )
            next_trigger = await triggers.get_next_trigger_for_case(conn, **owner, case_id=case_id)
            latest_action = await actions.get_latest_action_intent_for_case(
                conn, **owner, case_id=case_id
            )
            counts = await cases.get_case_counts(conn, **owner, case_id=case_id)
        item = render.case_item(row)
        return item | {
            "relationship": {
                "relationship_id": str(row["relationship_id"]),
                "label": row.get("relationship_label"),
                "status": row.get("relationship_status"),
            },
            "counterparty": _counterparty(row),
            "context": (
                None
                if row.get("context_id") is None
                else {"context_id": str(row["context_id"]), "title": row.get("context_title")}
            ),
            "last_activity_at": row["last_activity_at"],
            "commitments": [
                render.commitment_item(c | {"relationship_id": row["relationship_id"]}, now=now)
                for c in commitment_rows
            ],
            "active_conflicts": [render.conflict_item(c) for c in conflict_rows],
            "next_trigger": (
                None
                if next_trigger is None
                else {
                    "trigger_id": str(next_trigger["trigger_id"]),
                    "trigger_type": next_trigger["trigger_type"],
                    "state": next_trigger["state"],
                    "not_before": next_trigger.get("not_before"),
                    "expires_at": next_trigger.get("expires_at"),
                    "basis_case_revision": int(next_trigger["basis_case_revision"]),
                }
            ),
            "latest_action_intent": (
                None
                if latest_action is None
                else {
                    "action_intent_id": str(latest_action["action_intent_id"]),
                    "action_type": latest_action["action_type"],
                    "status": latest_action["status"],
                    "basis_case_revision": int(latest_action["basis_case_revision"]),
                    "created_at": latest_action["created_at"],
                }
            ),
            "counts": {key: int(value) for key, value in counts.items()},
        }

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None:
        async with self._source.connection() as conn:
            return await cases.get_case_revision_for(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id, case_id=case_id
            )

    async def list_timeline(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Rows | None:
        """Section 8.10.

        The case-existence check runs first and on its own. Without it a
        timeline for another user's case would return an empty page rather
        than a ``404``, which tells the caller the identifier is real -- the
        exact distinction section 1.7 collapses on purpose.
        """
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        sort_value, last_id = _after(filters.get("after"))
        async with self._source.connection() as conn:
            if await cases.get_case_revision_for(conn, **owner, case_id=case_id) is None:
                return None
            rows = await timeline.list_case_timeline(
                conn,
                **owner,
                case_id=case_id,
                limit=limit + 1,
                kinds=tuple(filters.get("kinds") or ()),
                since_revision=filters.get("since_revision"),
                after_occurred_at=sort_value,
                after_id=last_id,
            )
        return _page([render.timeline_item(row) for row in rows], limit)

    async def state_proof(
        self, scope: OwnerScope, case_id: uuid.UUID, **options: Any
    ) -> Row | None:
        """Section 8.11. Deterministic, and correct with no model reachable.

        Grounding and lineage are two fields under two names and are never
        merged: grounding is the ``belief_support`` edge set and answers *why
        do you believe this*; lineage is the ``belief_versions`` chain and
        answers *what did you believe before, and what changed your mind*. The
        two come from two repository calls over two tables, so conflating them
        would take a deliberate edit rather than a careless one.
        """
        include_retracted = bool(options.get("include_retracted"))
        belief_ids = tuple(options.get("belief_ids") or ())
        max_evidence = int(options.get("max_evidence_per_belief") or 10)
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}

        async with self._source.connection() as conn:
            case_row = await cases.get_case_detail(
                conn, **owner, case_id=case_id, now=self._clock()
            )
            if case_row is None:
                return None
            belief_rows = await beliefs.list_case_beliefs(
                conn, **owner, case_id=case_id, belief_ids=belief_ids
            )
            version_rows = await beliefs.list_case_belief_versions(
                conn, **owner, case_id=case_id, belief_ids=belief_ids
            )
            support_rows = await beliefs.list_case_belief_support(
                conn,
                **owner,
                case_id=case_id,
                belief_ids=belief_ids,
                include_retracted=include_retracted,
            )
            commitment_rows = await commitments.list_commitments_for_case(
                conn, **owner, case_id=case_id
            )
            fulfillment_rows = await commitments.list_fulfillments_for_case(
                conn, **owner, case_id=case_id
            )
            conflict_rows = await conflicts.list_conflicts_for_case(
                conn, **owner, case_id=case_id, limit=50
            )
            transition_rows = await cases.list_case_transitions(conn, **owner, case_id=case_id)
            action_rows = await actions.list_action_intents_for_case(conn, **owner, case_id=case_id)
            retracted = await evidence.count_retracted_evidence_for_case(
                conn, **owner, case_id=case_id
            )

        proofs, warnings = _belief_proofs(
            belief_rows, version_rows, support_rows, max_evidence=max_evidence
        )
        payload: Row = {
            "schema_version": "1.0",
            "case_id": str(case_id),
            "case_revision": int(case_row["revision"]),
            "case_status": case_row["status"],
            "generated_at": self._clock(),
            "deterministic": True,
            "model_used": None,
            "beliefs": proofs,
            "commitments": _proof_commitments(commitment_rows, fulfillment_rows),
            "conflicts": [render.conflict_item(row) for row in conflict_rows],
            "derivations": _derivations(commitment_rows),
            "state_transitions": [
                {
                    "case_revision": int(row["case_revision"]),
                    "transition_type": row["transition_type"],
                    "from_state": row.get("from_state"),
                    "to_state": row.get("to_state"),
                    "reason_code": row["reason_code"],
                    "kernel_decision_id": str(row["kernel_decision_id"]),
                    "trace_id": str(row["trace_id"]),
                    "recorded_at": row["recorded_at"],
                }
                for row in transition_rows
            ],
            "actions_relying_on_this_state": [
                {
                    "action_intent_id": str(row["action_intent_id"]),
                    "action_type": row["action_type"],
                    "status": row["status"],
                    "basis_case_revision": int(row["basis_case_revision"]),
                    "supporting_belief_versions": row.get("supporting_belief_versions") or [],
                    "still_current": int(row["basis_case_revision"])
                    == int(row["current_case_revision"]),
                }
                for row in action_rows
            ],
            "excluded": {
                "retracted_evidence_count": retracted,
                "superseded_belief_versions_hidden": 0,
                "retraction_filter_applied": not include_retracted,
            },
        }
        if warnings:
            payload["integrity_warnings"] = warnings
        return payload

    async def list_conflicts(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Rows | None:
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        sort_value, last_id = _after(filters.get("after"))
        async with self._source.connection() as conn:
            if await cases.get_case_revision_for(conn, **owner, case_id=case_id) is None:
                return None
            rows = await conflicts.list_conflicts_for_case(
                conn,
                **owner,
                case_id=case_id,
                limit=limit + 1,
                statuses=tuple(filters.get("statuses") or ()),
                severity=filters.get("severity"),
                requires_human=filters.get("requires_human"),
                after_detected_at=sort_value,
                after_id=last_id,
            )
        return _page([render.conflict_item(row) for row in rows], limit)

    async def get_belief(
        self, scope: OwnerScope, belief_id: uuid.UUID, **options: Any
    ) -> Row | None:
        """Section 8.13. One belief, its grounding and its lineage -- separately."""
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        include_retracted = bool(options.get("include_retracted"))
        async with self._source.connection() as conn:
            head = await beliefs.get_belief_head(conn, **owner, belief_id=belief_id)
            if head is None:
                return None
            lineage = await beliefs.get_belief_lineage_for(conn, **owner, belief_id=belief_id)
            support = await beliefs.list_case_belief_support(
                conn,
                **owner,
                case_id=head["case_id"],
                belief_ids=(belief_id,),
                include_retracted=include_retracted,
            )
        current_id = head.get("current_version_id")
        current = next(
            (v for v in lineage if v["belief_version_id"] == current_id),
            lineage[-1] if lineage else None,
        )
        edges = [e for e in support if e["belief_version_id"] == current_id]
        return {
            "belief_id": str(belief_id),
            "case_id": str(head["case_id"]),
            "relationship_id": _opt_str(head.get("relationship_id")),
            "predicate": head["predicate"],
            "grounded": any(edge["relation"] == "SUPPORTS" for edge in edges),
            "current_version": None if current is None else _version(current),
            "grounding": [_grounding_edge(edge) for edge in edges],
            "lineage": [_version(entry) for entry in lineage],
        }

    # -- 8.15 - 8.17, 8.21, 8.23 - 8.24 -----------------------------------

    async def list_commitments(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        now = self._clock()
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await commitments.list_commitments_for_user(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                now=now,
                case_id=filters.get("case_id"),
                relationship_id=filters.get("relationship_id"),
                context_id=filters.get("context_id"),
                statuses=tuple(filters.get("statuses") or ()),
                overdue_only=bool(filters.get("overdue_only")),
                outstanding_only=bool(filters.get("outstanding_only")),
                after_due_at=sort_value,
                after_id=last_id,
            )
        return _page([render.commitment_item(row, now=now) for row in rows], limit)

    async def list_triggers(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await triggers.list_triggers_for_user(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                case_id=filters.get("case_id"),
                states=tuple(filters.get("states") or ()),
                trigger_types=tuple(filters.get("trigger_types") or ()),
                after_not_before=sort_value,
                after_id=last_id,
            )
        return _page([render.trigger_item(row) for row in rows], limit)

    async def list_artifacts(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await artifacts.list_artifacts(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                source_types=tuple(filters.get("source_types") or ()),
                parser_statuses=tuple(filters.get("parser_statuses") or ()),
                case_id=filters.get("case_id"),
                after_received_at=sort_value,
                after_id=last_id,
            )
        return _page([render.artifact_item(row) for row in rows], limit)

    async def get_artifact(
        self, scope: OwnerScope, artifact_id: uuid.UUID, **options: Any
    ) -> Row | None:
        del options
        owner: dict[str, Any] = {"tenant_id": scope.tenant_id, "user_id": scope.user_id}
        async with self._source.connection() as conn:
            row = await artifacts.get_artifact(conn, **owner, artifact_id=artifact_id)
            if row is None:
                return None
            linked = await artifacts.list_artifact_cases(conn, **owner, artifact_id=artifact_id)
        return render.artifact_item(row) | {
            "content_blocks_summary": [],
            "linked_cases": [
                {
                    "case_id": str(item["case_id"]),
                    "case_title": item.get("case_title"),
                    "claim_count": int(item["claim_count"]),
                }
                for item in linked
            ],
            "agent_run_id": None,
            "trace_id": None,
        }

    async def ingest_alias(self, scope: OwnerScope) -> Row | None:
        """Section 8.21.

        ``alias_display`` comes from the reversible, non-secret display column
        and is ``None`` when a deployment did not store one -- section 8.21's
        own fallback, where the UI shows "rotate to reveal". The hash is never
        projected: it is the authentication material for inbound mail.
        """
        async with self._source.connection() as conn:
            row = await users.get_ingest_alias(
                conn, tenant_id=scope.tenant_id, user_id=scope.user_id
            )
        if row is None:
            return None
        return {
            "alias_display": row.get("alias_label"),
            "status": row["status"],
            "created_at": row["created_at"],
            "rotated_at": row.get("rotated_at"),
            "artifacts_received": int(row.get("artifacts_received") or 0),
            "last_received_at": row.get("last_received_at"),
        }

    async def list_action_intents(
        self,
        scope: OwnerScope,
        *,
        limit: int,
        after: tuple[list[str], uuid.UUID] | None = None,
        **filters: Any,
    ) -> Rows:
        sort_value, last_id = _after(after)
        async with self._source.connection() as conn:
            rows = await actions.list_action_intents_for_user(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                limit=limit + 1,
                case_id=filters.get("case_id"),
                statuses=tuple(filters.get("statuses") or ()),
                action_types=tuple(filters.get("action_types") or ()),
                after_created_at=sort_value,
                after_id=last_id,
            )
        return _page([render.action_item(row) for row in rows], limit)

    async def get_action_intent(
        self, scope: OwnerScope, action_intent_id: uuid.UUID, **options: Any
    ) -> Row | None:
        """Section 8.24.

        Both digests travel: ``draft_sha256`` and ``approval_draft_sha256``
        are the same 32 bytes on a healthy intent, and their divergence is the
        whole point -- an approval is bound to an exact draft, so an intent
        whose draft changed after approval must be executable by nobody.
        """
        include_body = options.get("include_draft_body", True)
        async with self._source.connection() as conn:
            row = await actions.get_action_intent_detail(
                conn,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                intent_id=action_intent_id,
            )
        if row is None:
            return None
        draft = row.get("draft_payload") or {}
        basis = int(row["basis_case_revision"])
        current = int(row["case_revision_now"])
        return {
            "action_intent_id": str(row["id"]),
            "case_id": str(row["case_id"]),
            "action_type": row["action_type"],
            "status": row["status"],
            "recipient": row.get("recipient"),
            "recipient_masked": render.mask_email(row.get("recipient")),
            "draft": draft if include_body else {"subject": draft.get("subject")},
            "draft_sha256": _hex(row.get("draft_sha256")),
            "approval_draft_sha256": _hex(row.get("approval_draft_sha256")),
            "rationale": row.get("rationale"),
            "supporting_belief_versions": row.get("supporting_belief_versions") or [],
            "basis_case_revision": basis,
            "current_case_revision": current,
            "is_stale": basis != current,
            "risk_tier": int(row.get("risk_tier") or 3),
            "state_proof_url": f"/v1/cases/{row['case_id']}/state-proof",
            "warnings": [],
            "approval": (
                None
                if row.get("approved_at") is None
                else {
                    "approved_at": row["approved_at"],
                    "approved_by_user_id": _opt_str(row.get("approved_by_user_id")),
                    "approval_draft_sha256": _hex(row.get("approval_draft_sha256")),
                }
            ),
            "executions": [],
            "created_at": row["created_at"],
            "created_by_agent_run_id": _opt_str(row.get("created_by_agent_run_id")),
        }

    # -- 8.28 / 8.29 ------------------------------------------------------

    async def get_trace(
        self, scope: OwnerScope, trace_id: uuid.UUID, *, judge: bool = False
    ) -> Row | None:
        del scope, trace_id, judge
        unbound("read.get_trace")

    async def memory_trace(
        self, scope: OwnerScope, case_id: uuid.UUID, *, limit: int, **filters: Any
    ) -> Row | None:
        del scope, case_id, limit, filters
        unbound("read.memory_trace")

    # -- T8.7 -------------------------------------------------------------

    async def agent_view_names(self) -> list[str]:
        """``G11.6``'s diff, read from the catalogue rather than a constant."""
        return await agent_view_names(self._source)


# ==========================================================================
# Row -> response helpers that need more than one row to answer
# ==========================================================================


def _counterparty(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "counterparty_id": str(row["counterparty_id"]),
        "display_name": row.get("counterparty_display_name"),
        "kind": row.get("counterparty_kind"),
        "canonical_domain": row.get("canonical_domain"),
    }


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _hex(value: object) -> str | None:
    """A ``BYTES`` digest as lowercase hex, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return str(value)


def _context_summary(row: Mapping[str, Any], totals: list[dict[str, Any]]) -> dict[str, Any]:
    """Section 8.4's ``contexts[]`` element and section 8.5's item.

    ``total_outstanding`` is an array, one entry per currency. Section 8.4:
    the Kernel refuses arithmetic across currencies without an explicit
    conversion event, so the API refuses to sum them either -- and the refusal
    has to be structural, because a single summed number looks entirely
    reasonable on a screen.
    """
    outstanding = [
        render.money(total["currency"], total["outstanding"])
        for total in totals
        if total["context_id"] == row["context_id"]
    ]
    return {
        "context_id": str(row["context_id"]),
        "title": row["title"],
        "context_type": row["context_type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "case_count": int(row.get("case_count") or 0),
        "open_case_count": int(row.get("open_case_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "total_outstanding": [item for item in outstanding if item is not None],
    }


#: ``attention_rank`` -> the level it stands for. The projection ranks
#: numerically because the strings do not sort in severity order; this is the
#: inverse, and the pair is what keeps "loudest first" true.
_ATTENTION_BY_RANK: Mapping[int, str] = {3: "URGENT", 2: "ATTENTION", 1: "INFO", 0: "NONE"}


def _relationship_summary(row: Mapping[str, Any], totals: list[dict[str, Any]]) -> dict[str, Any]:
    outstanding = [
        render.money(total["currency"], total["outstanding"])
        for total in totals
        if total["relationship_id"] == row["relationship_id"]
    ]
    return {
        "relationship_id": str(row["relationship_id"]),
        "counterparty": _counterparty(row),
        "label": row.get("label"),
        "relationship_type": row["relationship_type"],
        "status": row["status"],
        "external_account_ref_masked": row.get("external_account_ref_masked"),
        "attention_level": _ATTENTION_BY_RANK[int(row.get("attention_rank") or 0)],
        "open_case_count": int(row.get("open_case_count") or 0),
        "last_activity_at": row.get("last_activity_at"),
        "updated_at": row.get("updated_at"),
        "revision": int(row.get("revision") or 0),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "outstanding": [item for item in outstanding if item is not None],
    }


def _version(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``belief_versions`` row, as both a current version and a lineage
    entry -- the two shapes are the same columns under section 8.11."""
    return {
        "belief_version_id": str(row["belief_version_id"]),
        "version_no": int(row["version_no"]),
        "value_type": row.get("value_type"),
        "value_json": row.get("value_json"),
        "epistemic_status": row["epistemic_status"],
        "belief_confidence": _decimal_str(row.get("belief_confidence")),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "recorded_at": row["recorded_at"],
        "superseded_at": row.get("superseded_at"),
        "superseded_by_version_id": _opt_str(row.get("superseded_by_version_id")),
        "superseded_by_version_no": (
            None
            if row.get("superseded_by_version_no") is None
            else int(row["superseded_by_version_no"])
        ),
        "supersession_reason_codes": row.get("supersession_reason_codes") or [],
        "kernel_decision_id": str(row["kernel_decision_id"]),
        "grounding_count": (
            None if row.get("grounding_count") is None else int(row["grounding_count"])
        ),
    }


def _decimal_str(value: object) -> str | None:
    """Section 1.3: a confidence is a decimal string, never a float."""
    return None if value is None else str(value)


def _grounding_edge(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``belief_support`` edge with the observation behind it.

    ``source`` is the evidence or the claim, rendered in full. A proof that
    named an identifier in place of the observation would be asking the reader
    to take its word for it, which is the one thing a proof may not do.
    """
    source: dict[str, Any] | None = None
    if row.get("evidence_id") is not None:
        source = {
            "evidence_id": str(row["evidence_id"]),
            "artifact_id": _opt_str(row.get("artifact_id")),
            "evidence_type": row.get("evidence_type"),
            "exact_text": row.get("exact_text"),
            "normalized_text": row.get("normalized_text"),
            "source_locator": row.get("source_locator"),
            "observed_at": row.get("observed_at"),
            "source_authority": _decimal_str(row.get("source_authority")),
            "extraction_confidence": _decimal_str(row.get("extraction_confidence")),
            "retraction_status": row.get("retraction_status"),
            "retracted_at": row.get("retracted_at"),
            "retracted_by_evidence_id": _opt_str(row.get("retracted_by_evidence_id")),
            "retraction_reason_code": row.get("retraction_reason_code"),
            "artifact": {
                "source_type": row.get("artifact_source_type"),
                "sender_display": row.get("artifact_sender"),
                "subject": row.get("artifact_subject"),
                "received_at": row.get("artifact_received_at"),
            },
        }
    elif row.get("claim_id") is not None:
        source = {
            "claim_id": str(row["claim_id"]),
            "claim_kind": row.get("claim_kind"),
            "predicate": row.get("claim_predicate"),
            "object_json": row.get("object_json"),
            "actor_type": row.get("claim_actor_type"),
            "authority_score": _decimal_str(row.get("authority_score")),
            "evidence_id": _opt_str(row.get("claim_evidence_id")),
            "recorded_at": row.get("claim_recorded_at"),
        }
    return {
        "support_id": str(row["support_id"]),
        "relation": row["relation"],
        "source_kind": row["source_kind"],
        "source_id": str(row["source_id"]),
        "weight": _decimal_str(row.get("weight")),
        "reason_code": row.get("reason_code"),
        "created_at": row["created_at"],
        "source": source,
    }


def _belief_proofs(
    belief_rows: list[dict[str, Any]],
    version_rows: list[dict[str, Any]],
    support_rows: list[dict[str, Any]],
    *,
    max_evidence: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Section 8.11's ``beliefs[]``, and the integrity warnings it may carry.

    ``grounded`` comes from the count of ``SUPPORTS`` edges, not from
    ``support_edge_count`` -- that column counts every relation including
    ``CONTRADICTS``, so a version grounded only by the thing arguing against
    it would read as grounded. Section 8.11.1 calls that condition a P1
    data-integrity alarm; it should be unreachable, because the Kernel refuses
    such a commit, which is exactly why it is worth detecting rather than
    assuming.
    """
    lineage_by_belief: dict[Any, list[dict[str, Any]]] = {}
    for row in version_rows:
        lineage_by_belief.setdefault(row["belief_id"], []).append(row)
    support_by_version: dict[Any, list[dict[str, Any]]] = {}
    for row in support_rows:
        support_by_version.setdefault(row["belief_version_id"], []).append(row)

    proofs: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for belief in belief_rows:
        version_id = belief["belief_version_id"]
        edges = support_by_version.get(version_id, [])[:max_evidence]
        grounded = int(belief.get("supports_count") or 0) > 0
        if not grounded and str(belief.get("derivation_kind")) != "DETERMINISTIC_DERIVATION":
            warnings.append(
                {
                    "code": "UNGROUNDED_CANONICAL_BELIEF",
                    "belief_id": str(belief["belief_id"]),
                    "belief_version_id": str(version_id),
                    "message": (
                        "This belief version has no supporting evidence and is not a "
                        "deterministic derivation."
                    ),
                }
            )
        proofs.append(
            {
                "belief_id": str(belief["belief_id"]),
                "subject_type": belief["subject_type"],
                "subject_id": str(belief["subject_id"]),
                "predicate": belief["predicate"],
                "grounded": grounded,
                "current_version": _version(belief),
                "grounding": [_grounding_edge(edge) for edge in edges],
                "lineage": [
                    _version(entry) for entry in lineage_by_belief.get(belief["belief_id"], [])
                ],
            }
        )
    return proofs, warnings


def _proof_commitments(
    commitment_rows: list[dict[str, Any]], fulfillment_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Section 8.11's ``commitments[]`` with their fulfillment ledgers."""
    by_commitment: dict[Any, list[dict[str, Any]]] = {}
    for row in fulfillment_rows:
        by_commitment.setdefault(row["commitment_id"], []).append(row)
    out: list[dict[str, Any]] = []
    for row in commitment_rows:
        currency = row.get("currency")
        out.append(
            {
                "commitment_id": str(row["commitment_id"]),
                "description": row["description"],
                "status": row["status"],
                "currency": currency,
                "committed_amount": render.money(currency, row.get("committed_amount")),
                "fulfilled_amount": render.money(currency, row.get("fulfilled_amount")),
                "outstanding_amount": render.money(currency, row.get("outstanding_amount")),
                "due_at": row.get("due_at"),
                "source_claim_id": _opt_str(row.get("source_claim_id")),
                "fulfillments": [
                    {
                        "fulfillment_id": str(f["fulfillment_id"]),
                        "amount": render.money(f.get("currency"), f.get("amount")),
                        "fulfilled_at": f.get("fulfilled_at"),
                        "admission_status": f.get("admission_status"),
                        "evidence_id": _opt_str(f.get("evidence_id")),
                    }
                    for f in by_commitment.get(row["commitment_id"], [])
                ],
            }
        )
    return out


def _derivations(commitment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Section 8.11's ``derivations[]``: ``committed - fulfilled``, shown.

    The one deterministic derivation in v1, and it is disclosed rather than
    silently applied: a belief derived this way carries a derivation edge
    instead of an evidence edge and is still grounded
    (``provenance_domain.money.outstanding``). Rendering the inputs beside the
    result is what lets a reader check the arithmetic instead of trusting it.
    """
    out: list[dict[str, Any]] = []
    for row in commitment_rows:
        currency = row.get("currency")
        if currency is None or row.get("committed_amount") is None:
            continue
        out.append(
            {
                "name": "outstanding_amount",
                "target": {"kind": "COMMITMENT", "id": str(row["commitment_id"])},
                "expression": "committed_amount - fulfilled_amount",
                "inputs": {
                    "committed_amount": render.money(currency, row.get("committed_amount")),
                    "fulfilled_amount": render.money(currency, row.get("fulfilled_amount")),
                },
                "result": render.money(currency, row.get("outstanding_amount")),
                "deterministic_derivation": True,
                "grounding_exempt": True,
            }
        )
    return out
