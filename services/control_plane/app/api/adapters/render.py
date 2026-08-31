"""Row shapes in, response shapes out. No SQL, no clock of its own, no model.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 1.3 (money on the wire), 8.4 (the
  deterministic headline), 8.9, 8.15, 8.16 and 8.23.
- ``frontend/30_UX_SPEC.md`` section on S2: "There is **no** model-authored
  content on S2. ``headline`` is a deterministic template keyed on
  ``attention_reason_codes`` and the dashboard must render identically with
  Bedrock unavailable."

Why the mapping is a module and not a method on the port
---------------------------------------------------------
Two reasons, and the second is the load-bearing one.

1. Every function here is pure. Given a row it returns a dict, so the whole
   translation layer is testable without a connection, a pool or a clock.
2. It keeps the adapters honest about what they are. ``SqlReadPort`` decides
   *which* repository call to make; this module decides what the answer looks
   like. Neither of them builds a ``WHERE``, and a reviewer can confirm that
   by reading two files rather than twenty methods.

Money is a string, and it is a string here
-------------------------------------------
Section 1.3 puts amounts on the wire as decimal *strings*, and
``responses.json_response`` states that the ports hand them over already in
that form. :func:`money` is the one conversion, and it quantises to four
places -- the column is ``DECIMAL(20,4)`` -- so ``1800`` and ``1800.0000``
cannot both appear for the same obligation depending on which path produced
it. There is no float anywhere on this path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "ATTENTION_REASON_CODES",
    "action_item",
    "artifact_item",
    "attention_reason_codes",
    "case_item",
    "commitment_item",
    "conflict_item",
    "days_overdue",
    "headline",
    "kernel_commit_result",
    "mask_email",
    "money",
    "predicate_summary",
    "timeline_item",
    "trigger_evaluation",
    "trigger_item",
]

Row = Mapping[str, Any]

#: ``DECIMAL(20,4)``, matching the column. Written as a string so the exponent
#: is exact rather than a float that happens to round correctly.
_MONEY_EXPONENT = Decimal("0.0001")

#: The closed vocabulary the headline template is keyed on. These are the five
#: values ``apps/web`` renders as chips (``hero.fixture.ts``) and the four
#: section 8.4 prints; ``COMMITMENT_PARTIAL`` is the fifth and appears in the
#: fixture. A code not on this list would render as an unknown chip, so the
#: derivation below emits only these.
ATTENTION_REASON_CODES: tuple[str, ...] = (
    "CONFLICT_OPEN",
    "ACTION_AWAITING_APPROVAL",
    "TRIGGER_FIRED",
    "COMMITMENT_OVERDUE",
    "COMMITMENT_PARTIAL",
)

#: Reason code -> the sentence the dashboard and the case index render.
#:
#: A table, not an f-string chain, because ``30_UX_SPEC.md`` requires this text
#: to be identical with Bedrock unavailable -- so it has to be somewhere a
#: reader can check it against the spec, and somewhere a test can assert the
#: whole set. The first code in :data:`ATTENTION_REASON_CODES` order wins, so
#: a case that is both in conflict and overdue leads with the contradiction:
#: that is the thing the user cannot work out for themselves.
_HEADLINES: Mapping[str, str] = {
    "CONFLICT_OPEN": "New evidence contradicts what this record says.",
    "ACTION_AWAITING_APPROVAL": "A drafted response is waiting for your approval.",
    "TRIGGER_FIRED": "A deadline you were tracking has passed.",
    "COMMITMENT_OVERDUE": "The promised date has passed and {outstanding} is still outstanding.",
    "COMMITMENT_PARTIAL": "Part of this obligation was met; {outstanding} is still outstanding.",
}

_QUIET_HEADLINE = "No action is needed on this record right now."

#: The predicate grammar, and the phrase each operator renders as. Section
#: 8.16: the grammar is a closed whitelist evaluated by deterministic Python,
#: so a summary of it is a lookup rather than a generation.
_OPERATORS: Mapping[str, str] = {
    "EQ": "is",
    "NE": "is not",
    "GT": "is greater than",
    "GTE": "is at least",
    "LT": "is less than",
    "LTE": "is at most",
    "IS_NULL": "is unset",
    "NOT_NULL": "is set",
}


# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------


def money(currency: object, amount: object) -> dict[str, str] | None:
    """Section 1.3's money object, or ``None``.

    ``None`` when either half is absent, and that is not a convenience:
    constraint ``M2`` forbids a commitment with an amount and no currency, so
    a half-populated pair means the row is non-monetary, and a caller that
    rendered ``0`` there would have invented a settled obligation out of a
    ``SERVICE_TERMINATION``.
    """
    if amount is None or currency is None:
        return None
    if isinstance(amount, float):  # pragma: no cover - psycopg never yields one
        raise TypeError(
            "money arrived as a float. DECIMAL(20,4) is the column type and "
            "Decimal is the only exact representation of it; a float here is a "
            "rounding error waiting for a large enough balance."
        )
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return {"currency": str(currency), "amount": str(value.quantize(_MONEY_EXPONENT))}


def days_overdue(due_at: datetime | None, now: datetime) -> int | None:
    """Whole days between *due_at* and *now*, or ``None`` when not overdue.

    Section 8.15: ``overdue`` and ``days_overdue`` are computed at read time
    and are deliberately not stored, because a stored flag goes stale between
    writes. ``None`` rather than ``0`` for a commitment that is not yet due --
    zero days overdue and not overdue at all are different facts, and the UI
    renders them differently.
    """
    if due_at is None or due_at >= now:
        return None
    return (now - due_at).days


def mask_email(address: object) -> str | None:
    """``billing@example.com`` -> ``b•••••g@example.com``.

    The domain survives because the recipient allowlist is keyed on it
    (section 14.4) and a reviewer approving an outbound letter has to be able
    to see where it is going. The local part does not, because it is the half
    that identifies a person.
    """
    if not address:
        return None
    text = str(address)
    local, _, domain = text.partition("@")
    if not domain or len(local) < 2:
        return f"•••••@{domain}" if domain else "•••••"
    return f"{local[0]}•••••{local[-1]}@{domain}"


def _uuid_or_none(value: object) -> str | None:
    return None if value is None else str(value)


# --------------------------------------------------------------------------
# Attention, and the sentence that follows from it
# --------------------------------------------------------------------------


def attention_reason_codes(row: Row) -> list[str]:
    """The chips for one case, derived from the ledgers rather than stored.

    ``cases`` has no ``attention_reason_codes`` column. Each flag arrives from
    an ``EXISTS`` in the case projection, so what the user sees is the current
    state of the conflict, commitment, trigger and action ledgers -- not a
    denormalised copy that drifts the moment one of them changes without the
    case being rewritten.
    """
    flags = {
        "CONFLICT_OPEN": bool(row.get("has_open_conflict")),
        "ACTION_AWAITING_APPROVAL": bool(row.get("has_pending_action")),
        "TRIGGER_FIRED": bool(row.get("has_fired_trigger")),
        "COMMITMENT_OVERDUE": bool(row.get("has_overdue_commitment")),
        "COMMITMENT_PARTIAL": bool(row.get("has_partial_commitment")),
    }
    return [code for code in ATTENTION_REASON_CODES if flags[code]]


def headline(row: Row, codes: Sequence[str]) -> str:
    """The deterministic sentence section 8.4 keys on *codes*.

    Never model-generated, and never assembled from free text on the row: the
    only interpolation is an amount this system computed itself.
    """
    if not codes:
        return _QUIET_HEADLINE
    template = _HEADLINES[codes[0]]
    if "{outstanding}" not in template:
        return template
    rendered = (
        format_money_for_prose(row.get("headline_currency"), row.get("headline_outstanding"))
        or "the outstanding amount"
    )
    return template.format(outstanding=rendered)


def format_money_for_prose(currency: object, amount: object) -> str | None:
    """An amount that reads as money inside a sentence, or ``None``.

    The headline rendered ``USD 1800.0000`` -- the storage form, four decimal
    places, straight out of the column -- into the first sentence on the
    dashboard, three inches above the same figure formatted as ``USD 1,800.00``.
    The screen disagreed with itself about the one number the product exists to
    be trusted about, and `1800.0000` invites the reader to wonder whether the
    decimal has gone wrong.

    The rule is the client's `formatDecimal`, restated here rather than
    approximated, because these two are the only renderers of the same value and
    a near-match is worse than an obvious mismatch:

    * group the integer part in threes,
    * keep at least two decimal places,
    * drop trailing zeros beyond two,
    * and **keep** significant digits beyond two -- truncating them would change
      the amount, and a tenth of a cent dropped from an invoice is a wrong bill.

    Returns ``None`` when either half is absent. It must not fall back to zero:
    zero is a claim about the record, and the opposite one -- it says the
    obligation is discharged.
    """
    if currency is None or amount is None:
        return None
    text = str(amount)
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    whole, _, fraction = text.partition(".")
    trimmed = fraction.rstrip("0")
    places = max(2, len(trimmed))
    padded = fraction.ljust(places, "0")[:places]
    grouped = f"{int(whole):,}" if whole.isdigit() else whole
    sign = "-" if negative else ""
    return f"{currency} {sign}{grouped}.{padded}"


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------


def case_item(row: Row) -> dict[str, Any]:
    """Section 8.4's ``cases_attention`` element plus section 8.8's four extras."""
    codes = attention_reason_codes(row)
    return {
        "case_id": str(row["case_id"]),
        "title": row["title"],
        "status": row["status"],
        "revision": int(row["revision"]),
        "attention_level": row["attention_level"],
        "attention_reason_codes": codes,
        "relationship_id": _uuid_or_none(row.get("relationship_id")),
        "counterparty_display_name": row.get("counterparty_display_name"),
        "last_activity_at": row["last_activity_at"],
        "headline": headline(row, codes),
        "case_type": row["case_type"],
        "opened_at": row["opened_at"],
        "resolved_at": row.get("resolved_at"),
        "reopened_count": int(row.get("reopened_count") or 0),
    }


def commitment_item(row: Row, *, now: datetime) -> dict[str, Any]:
    """Section 8.15's item, with the two read-time derivations."""
    due_at = row.get("due_at")
    overdue_days = days_overdue(due_at, now)
    settled = str(row.get("status")) in {"FULFILLED", "EXPIRED", "SUPERSEDED"}
    return {
        "commitment_id": str(row["commitment_id"]),
        "case_id": str(row["case_id"]),
        "relationship_id": _uuid_or_none(row.get("relationship_id")),
        "counterparty_display_name": row.get("counterparty_display_name"),
        "commitment_type": row["commitment_type"],
        "description": row["description"],
        "obligor_type": row["obligor_type"],
        "beneficiary_type": row["beneficiary_type"],
        "status": row["status"],
        "currency": row.get("currency"),
        "committed_amount": money(row.get("currency"), row.get("committed_amount")),
        "fulfilled_amount": money(row.get("currency"), row.get("fulfilled_amount")),
        "outstanding_amount": money(row.get("currency"), row.get("outstanding_amount")),
        "due_at": due_at,
        "overdue": overdue_days is not None and not settled,
        "days_overdue": None if settled else overdue_days,
        "source_claim_id": _uuid_or_none(row.get("source_claim_id")),
        "revision": int(row.get("revision") or 0),
    }


def predicate_summary(ast: object) -> str:
    """A deterministic sentence for one trigger predicate.

    Section 8.16 requires ``predicate_summary`` to be "rendered by a
    deterministic template, not a model". The grammar is closed --
    ``AND|OR|NOT|EQ|NE|GT|GTE|LT|LTE|IS_NULL|NOT_NULL|FIELD|CONST`` -- so this
    is a fold over a small tree, and an operator outside the whitelist renders
    as its own name rather than raising: a trigger whose summary is
    unfamiliar should still be visible, because an unrenderable predicate is
    precisely the one an operator needs to see.
    """
    if not isinstance(ast, Mapping):
        return "No predicate recorded."

    # The stored column is a WRAPPER, not a bare node:
    #
    #     {"ast_version": "1.0", "bindings": {...}, "predicate": {"op": "AND", ...}}
    #
    # and this function was written for the node. Handed the wrapper it found no
    # `op`, fell through every branch, and returned "No predicate recorded." --
    # so /v1/triggers reported that sentence for two triggers that each carry a
    # seven-clause predicate, and the Watches screen printed it under the
    # heading for the feature it exists to show. The frontend made the mirror
    # error on the same value and rendered the literal string "(undefined )".
    #
    # A false "nothing here" is the specific failure this codebase treats as
    # worst: an empty answer that is indistinguishable from a real one and
    # believable enough that nobody investigates.
    if "predicate" in ast and "op" not in ast:
        inner = ast.get("predicate")
        return predicate_summary(inner) if inner is not None else "No predicate recorded."

    op = str(ast.get("op", "")).upper()
    args = ast.get("args") or []
    if op == "FIELD":
        return str(ast.get("path", "?"))
    if op == "CONST":
        return str(ast.get("value", "?"))
    if op == "NOT" and args:
        return f"not ({predicate_summary(args[0])})"
    if op in {"AND", "OR"} and args:
        joiner = " and " if op == "AND" else " or "
        return joiner.join(predicate_summary(arg) for arg in args)
    if op in _OPERATORS and args:
        left = predicate_summary(args[0])
        if op in {"IS_NULL", "NOT_NULL"}:
            return f"{left} {_OPERATORS[op]}"
        right = predicate_summary(args[1]) if len(args) > 1 else "?"
        return f"{left} {_OPERATORS[op]} {right}"
    return op or "No predicate recorded."


def trigger_item(row: Row) -> dict[str, Any]:
    """Section 8.16's item.

    ``last_evaluation`` is ``None`` rather than an invented object. The field
    values a predicate saw at wakeup are what makes prospective memory
    auditable, and they are recorded by the trigger-evaluation worker (section
    9.10). Fabricating them from the current row would produce a plausible
    audit trail of an evaluation that never happened -- which is worse than
    the honest absence.
    """
    return {
        "trigger_id": str(row["trigger_id"]),
        "case_id": str(row["case_id"]),
        "case_title": row.get("case_title"),
        "trigger_type": row["trigger_type"],
        "state": row["state"],
        "not_before": row.get("not_before"),
        "expires_at": row.get("expires_at"),
        "basis_case_revision": int(row["basis_case_revision"]),
        "evaluation_version": int(row.get("evaluation_version") or 0),
        "last_evaluated_at": row.get("last_evaluated_at"),
        "last_result": row.get("last_result"),
        "last_reason_code": row.get("last_reason_code"),
        "schedule_name": row.get("schedule_name"),
        "predicate_summary": predicate_summary(row.get("predicate_ast")),
        "predicate_ast": row.get("predicate_ast"),
        "last_evaluation": None,
    }


def artifact_item(row: Row) -> dict[str, Any]:
    """Section 8.17's item -- section 8.20 minus the download URL.

    ``download_url`` is ``None`` here by construction: minting a pre-signed
    URL is a write-path act with its own rate limit, and a list endpoint that
    minted twenty-five of them per page would be a bulk-export tool wearing an
    index's clothes.
    """
    return {
        "artifact_id": str(row["artifact_id"]),
        "source_type": row["source_type"],
        "mime_type": row["mime_type"],
        "filename": None,
        "size_bytes": int(row["size_bytes"]),
        "content_sha256": row["content_sha256"],
        "sender_display": row.get("sender_display"),
        "recipient_display": row.get("recipient_display"),
        "subject": row.get("subject"),
        "source_message_id": row.get("source_message_id"),
        "received_at": row["received_at"],
        "event_time": row.get("event_time"),
        "parser_status": row["parser_status"],
        "parser_version": row.get("parser_version"),
        "parser_metadata": row.get("parser_metadata"),
        "evidence_item_count": int(row.get("evidence_item_count") or 0),
        "download_url": None,
        "download_url_expires_at": None,
    }


def action_item(row: Row) -> dict[str, Any]:
    """Section 8.23's item.

    ``is_stale`` is the comparison of the two revisions the projection
    returned together, so the list can grey out drafts that will fail
    approval before the user clicks -- section 8.23's stated reason for
    carrying both numbers on one row.
    """
    basis = int(row["basis_case_revision"])
    current = int(row["current_case_revision"])
    return {
        "action_intent_id": str(row["action_intent_id"]),
        "case_id": str(row["case_id"]),
        "case_title": row.get("case_title"),
        "counterparty_display_name": row.get("counterparty_display_name"),
        "action_type": row["action_type"],
        "status": row["status"],
        "recipient_masked": mask_email(row.get("recipient")),
        "subject_preview": row.get("subject_preview"),
        "basis_case_revision": basis,
        "current_case_revision": current,
        "is_stale": basis != current,
        "warning_count": 0,
        "created_at": row["created_at"],
        "created_by_agent_run_id": _uuid_or_none(row.get("created_by_agent_run_id")),
    }


def conflict_item(row: Row) -> dict[str, Any]:
    """Section 8.11's ``conflicts[]`` element, which 8.12 extends.

    ``left`` and ``right`` carry the kind and the id but no ``summary``: the
    summary in section 8.11's example is prose about the two sides, and
    inventing it here from column values would put generated narration into
    the one response that exists to be deterministic. The identifiers are
    real; a renderer resolves them.
    """
    return {
        "conflict_id": str(row["conflict_id"]),
        "case_id": str(row["case_id"]),
        "conflict_type": row["conflict_type"],
        "predicate": row["predicate"],
        "status": row["status"],
        "severity": row["severity"],
        "requires_human": bool(row["requires_human"]),
        "detected_at": row["detected_at"],
        "resolved_at": row.get("resolved_at"),
        "resolution_reason_code": row.get("resolution_reason_code"),
        "left": {
            "source_kind": row["left_source_kind"],
            "source_id": str(row["left_source_id"]),
        },
        "right": {
            "source_kind": row["right_source_kind"],
            "source_id": str(row["right_source_id"]),
        },
        "canonical_belief_version_id": _uuid_or_none(row.get("canonical_belief_version_id")),
    }


def timeline_item(row: Row) -> dict[str, Any]:
    """Section 8.10's common envelope.

    ``headline`` is a template on ``kind``, for the same reason the dashboard's
    is a template on ``attention_reason_codes``: the timeline must render with
    no model reachable.
    """
    kind = str(row["kind"])
    return {
        "id": str(row["id"]),
        "kind": kind,
        "occurred_at": row["occurred_at"],
        "case_revision": None if row.get("case_revision") is None else int(row["case_revision"]),
        "trace_id": _uuid_or_none(row.get("trace_id")),
        "actor": {"type": row.get("actor_type") or "SYSTEM", "label": row.get("actor_label")},
        "headline": _TIMELINE_HEADLINES.get(kind, kind.replace("_", " ").capitalize() + "."),
        "detail": row.get("detail") or {},
    }


#: One sentence per timeline ``kind``. Same rule as the dashboard's table: a
#: template a reader can check, not a sentence a model produced.
_TIMELINE_HEADLINES: Mapping[str, str] = {
    "ARTIFACT_RECEIVED": "A document arrived.",
    "EVIDENCE_ADMITTED": "Evidence was extracted and admitted.",
    "CLAIM_RECORDED": "A claim was recorded from that evidence.",
    "STATE_TRANSITION": "The record changed state.",
    "CONFLICT_OPENED": "A contradiction was detected.",
    "CONFLICT_RESOLVED": "A contradiction was resolved.",
    "COMMITMENT_CREATED": "An obligation was recorded.",
    "FULFILLMENT_ADMITTED": "A payment or delivery was credited against an obligation.",
    "TRIGGER_ARMED": "A deadline was armed.",
    "TRIGGER_FIRED": "A deadline woke and re-evaluated itself.",
    "ACTION_PROPOSED": "A response was drafted for your review.",
    "ACTION_APPROVED": "You approved a drafted response.",
    "ACTION_REJECTED": "You rejected a drafted response.",
    "USER_CORRECTION": "You corrected the record.",
}


def trigger_evaluation(outcome: Any) -> dict[str, Any]:
    """§9.10's ``200`` body, from one ``TriggerEvaluationOutcome``.

    One renderer for two callers -- the internal scheduled wake (§9.10) and the
    public manual wake (``16_TRIGGER_DSL.md`` §13.2) -- because the two paths
    land in the same evaluator and a judge comparing their answers must be
    comparing the same rendering. Two renderers would let the manual button
    quietly show a different field set from the scheduled path, which is exactly
    the suspicion the shared entry point exists to remove.

    ``field_values`` is ``observed``: the values the predicate actually read,
    keyed by whitelisted registry paths. It carries no document text -- the
    predicate never read any -- and it is what makes "nobody set this reminder"
    checkable rather than assertable.
    """
    return {
        "trigger_id": str(outcome.trigger_id),
        "wake_id": outcome.wake_id,
        "result": outcome.result.value,
        "reason_code": outcome.reason_code.value,
        "state": outcome.state_after.value,
        "state_before": outcome.state_before.value,
        "evaluated_at": outcome.fired_at,
        "case_id": None if outcome.case_id is None else str(outcome.case_id),
        "case_revision_before": outcome.case_revision_observed,
        "case_revision_after": outcome.case_revision_after,
        "basis_case_revision": outcome.basis_case_revision,
        "basis_stale": outcome.basis_stale,
        "predicate_result": outcome.predicate_result,
        "field_values": dict(outcome.observed),
        "outbox_event_ids": [str(value) for value in outcome.outbox_event_ids],
        "outbox_event_types": list(outcome.outbox_event_types),
        "proposal_id": None if outcome.proposal_id is None else str(outcome.proposal_id),
        "idempotent_replay": outcome.idempotent_replay,
        "attempts": outcome.attempts,
        "dry_run": outcome.dry_run,
        "preview_label": outcome.preview_label,
        "rearm_evaluation_version": outcome.rearm_evaluation_version,
        "rearm_not_before": outcome.rearm_not_before,
        "trace_id": str(outcome.trace_id),
        # The status the ROUTE would return. Carried in the body rather than
        # raised, because §9.10 is explicit that "normal stale, false, disarmed,
        # and expired wakes are typed 200 results, not transport failures" --
        # and a caller still needs to tell a 404 from a 409 from a 200.
        "http_status": outcome.http_status,
    }


def kernel_commit_result(result: Any) -> dict[str, Any]:
    """Section 9.7's ``201`` body, from one ``KernelCommitResult``.

    Projected field by field rather than dumped. ``KernelCommitResult`` carries
    ``tenant_id`` and ``user_id`` -- the Kernel needs them, the agent never
    does. Section 9.2 states the rule for the run bootstrap and it is the same
    rule here: withholding the id removes both the temptation to pass one back
    and the possibility of a model seeing and repeating one. A ``model_dump()``
    would have handed both over on the one endpoint an agent calls most.

    Recorded deviation. Section 9.7 prints ``created_claims[].client_ref``
    beside the claim id, and the receipt cannot carry it: ``CommitEffects``
    holds ``claim_ids`` and ``pipeline.ClaimWrite`` has no ``local_id`` field,
    so there is no mapping from a Kernel-minted claim id back to the proposal's
    ``cl_`` reference. The id is reported; a ``client_ref`` is not invented,
    because a fabricated cross-reference is worse than an absent one -- the
    caller would join on it.
    """
    return {
        "decision": result.decision.value,
        "proposal_id": str(result.proposal_id),
        "kernel_decision_id": str(result.kernel_decision_id),
        "proposal_status": result.proposal_status.value,
        "case_id": _uuid_or_none(result.case_id),
        "case_status_after": (
            None if result.case_status_after is None else result.case_status_after.value
        ),
        "case_revision_before": result.case_revision_before,
        "case_revision_after": result.case_revision_after,
        "attention_level_after": (
            None if result.attention_level_after is None else result.attention_level_after.value
        ),
        "created_claims": [{"claim_id": str(value)} for value in result.created_claim_ids],
        "created_belief_versions": [
            {
                "belief_id": str(ref.belief_id),
                "belief_version_id": str(ref.belief_version_id),
                "version_no": ref.version_no,
                "predicate": ref.predicate,
                "epistemic_status": ref.epistemic_status.value,
                "supersedes_version_id": _uuid_or_none(ref.supersedes_version_id),
                "support_edges": ref.grounding_edge_count,
                "is_derived": ref.is_derived,
            }
            for ref in result.created_belief_versions
        ],
        "created_or_updated_conflicts": [
            {
                "conflict_id": str(ref.conflict_id),
                "conflict_type": ref.conflict_type.value,
                "status": ref.status.value,
                "predicate": ref.predicate,
                "requires_human": ref.requires_human,
                "created": ref.created,
                "canonical_belief_version_id": _uuid_or_none(ref.canonical_belief_version_id),
                "resolution_reason_code": ref.resolution_reason_code,
            }
            for ref in result.created_or_updated_conflicts
        ],
        "commitment_changes": [
            {
                "commitment_id": str(change.commitment_id),
                "status_before": (
                    None if change.status_before is None else change.status_before.value
                ),
                "status_after": change.status_after.value,
                "committed": _money_ref(change.committed),
                "fulfilled_after": _money_ref(change.fulfilled_after),
                "outstanding_after": _money_ref(change.outstanding_after),
                "fulfillment_ids": [str(value) for value in change.fulfillment_ids],
                "created": change.created,
            }
            for change in result.commitment_changes
        ],
        "trigger_changes": [
            {
                "trigger_id": str(change.trigger_id),
                "state_before": None if change.state_before is None else change.state_before.value,
                "state_after": change.state_after.value,
                "not_before": change.not_before,
                "expires_at": change.expires_at,
                "schedule_name": change.schedule_name,
                "basis_case_revision": change.basis_case_revision,
                "created": change.created,
            }
            for change in result.trigger_changes
        ],
        "state_transitions": [
            {
                "state_transition_id": str(ref.state_transition_id),
                "transition_type": ref.transition_type.value,
                "case_revision": ref.case_revision,
                "from_state": ref.from_state,
                "to_state": ref.to_state,
                "reason_code": ref.reason_code,
                "recorded_at": ref.recorded_at,
            }
            for ref in result.state_transitions
        ],
        "outbox_event_ids": [str(value) for value in result.outbox_event_ids],
        "attention_required": result.attention_required,
        "retry_count": result.retry_count,
        "reason_codes": [code.value for code in result.reason_codes],
        "transaction_opened": result.transaction_opened,
        "committed_at": result.committed_at,
        "trace_id": str(result.trace_id),
    }


def _money_ref(value: Any) -> dict[str, str] | None:
    """A ``Money`` contract object as section 1.3's money object, or ``None``.

    ``None`` stays ``None``: an obligation with no committed amount is a
    non-monetary obligation, and rendering it as ``0.0000`` would turn "there
    is no amount" into "the amount is zero" -- two facts a reader would act on
    differently. Northline's termination is exactly that row.
    """
    if value is None:
        return None
    return money(value.currency, value.amount)
