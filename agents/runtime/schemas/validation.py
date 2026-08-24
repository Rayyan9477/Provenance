"""Semantic validation — the checks a JSON Schema cannot express.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 7.1 (three enforcement layers) and
  section 7.3 (the per-node check tables).
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 5.4 and 8.
- ``docs/specs/11_CONTRACTS.md`` sections 8.2, 16.1 -- whose pydantic models
  already enforce roughly half of section 7.3's table.

Three layers, and this module is the second
-------------------------------------------
1. **API-level constraint.** The structured-output call is bound to the
   generated JSON Schema, so the response parses and has the right shape.
2. **Semantic validation.** Numeric ranges, cross-field dependencies,
   referential integrity and span-substring equality. That is this module.
3. **Kernel provenance validation.** The Memory Kernel independently re-checks
   that every referenced row exists and belongs to the authenticated user.

Why half the table is not implemented here
------------------------------------------
``14_PROMPTS.md`` section 7.3 lists seventeen extraction codes. Nine of them
are already unrepresentable in
:class:`~provenance_contracts.ingestion.ExtractionResult`: a confidence outside
``[0,1]``, a bare float amount, a two-letter currency, an inverted validity
interval, a duplicate or dangling ``local_id``, a quoted commitment, a
commitment with ``HYPOTHETICAL`` modality. Re-implementing those here would
create a second opinion about a rule that already has one, and the second
opinion is the one that drifts. :data:`CONTRACT_ENFORCED_CODES` names them so
the coverage of the table stays auditable, and
``agents/runtime/tests/test_ingestion_graph.py`` asserts the union.

What is added beyond the table
------------------------------
``AMOUNT_NOT_IN_SOURCE_BLOCK``. Section 7.3 has no code for it, but
``14_PROMPTS.md`` section 3.1 rule 6 -- "money is copied, never computed" -- is
one of the load-bearing extraction rules and had no deterministic check. A
summed total the artifact never printed is exactly the failure that rule exists
to prevent, and it is mechanically detectable: the digits must appear in the
block the mention cites.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from provenance_contracts.actions import DraftAction
from provenance_contracts.ingestion import ContentBlock, ExtractionResult
from provenance_domain.enums import ClaimKind, ContentBlockKind

__all__ = [
    "CONTRACT_ENFORCED_CODES",
    "DRAFT_CODES",
    "EXTRACTION_CODES",
    "NO_REPAIR_CODES",
    "ValidationFailure",
    "validate_attention",
    "validate_draft",
    "validate_extraction",
]

#: A UUID anywhere in a Tier E output is a fabrication: the extractor is told it
#: does not know any real Provenance identifier and must use ``local_id`` values
#: it assigns itself. Adversarial row A6 is the reason this is a scan rather
#: than a field check -- the injected id arrives inside free text.
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

#: ``14_PROMPTS.md`` section 2.2's fence marker.
#:
#: Matched against the **live** nonce only, never generically. A generic match
#: is tempting and wrong: adversarial row A13 is an artifact that *contains* a
#: forged fence marker, and a generic scan would classify Provenance's own
#: correct handling of that row -- scrub, log ``FENCE_BREAKOUT``, extract the
#: forged text as ordinary data -- as a nonce leak and refuse the artifact. The
#: live nonce is generated per invocation and cannot appear in a document
#: authored beforehand, which is precisely what makes it evidence of a leak.
_NONCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"PROVENANCE_UNTRUSTED_[0-9a-fA-F]{16}")

#: Sentence terminators for the two-sentence and three-sentence caps. Kept
#: deliberately crude and shared with nothing: a segmenter that disagrees with
#: the prompt's description of itself shows up as a repair-loop spike rather
#: than as a wrong letter (``14_PROMPTS.md`` risk R2).
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"[.!?](?:\s|$)")

_SUMMARY_SENTENCE_CAP: Final[int] = 2
_RATIONALE_SENTENCE_CAP: Final[int] = 3


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """One failed check, in the shape section 7.2 feeds to the repair envelope."""

    path: str
    code: str
    detail: str

    def as_json_obj(self) -> dict[str, str]:
        """One element of section 7.2's ``failures_json``.

        ``detail`` is written for the *model* and may quote the model's own
        output, so it is never logged: the router logs ``path`` and ``code``
        only.
        """
        return {"path": self.path, "code": self.code, "detail": self.detail}


#: Codes this module can emit for an extraction.
EXTRACTION_CODES: Final[frozenset[str]] = frozenset(
    {
        "UNKNOWN_BLOCK_ID",
        "SPAN_OUT_OF_RANGE",
        "SPAN_TEXT_MISMATCH",
        "SPAN_MISSING",
        "AMOUNT_NOT_IN_SOURCE_BLOCK",
        "COMMITMENT_FROM_QUOTED_BLOCK",
        "INFERENCE_CLAIM_EMITTED",
        "FABRICATED_UUID",
        "NONCE_LEAKED_IN_OUTPUT",
        "SUMMARY_TOO_LONG",
    }
)

#: Codes this module can emit for a draft.
DRAFT_CODES: Final[frozenset[str]] = frozenset(
    {
        "UNSUPPORTED_SENTENCE",
        "SENTENCE_NOT_IN_BODY",
        "UNSUPPORTED_ACTION_TYPE",
        "RECIPIENT_NOT_IN_POLICY",
        "BODY_TOO_LONG",
        "RISK_UNSTATED_ON_HUMAN_CONFLICT",
        "FABRICATED_UUID",
        "STALE_PROOF_BINDING",
    }
)

#: Section 7.3 codes that :mod:`provenance_contracts` already makes
#: unrepresentable. Listed rather than re-implemented; see the module docstring.
CONTRACT_ENFORCED_CODES: Final[frozenset[str]] = frozenset(
    {
        "CONFIDENCE_OUT_OF_RANGE",
        "AMOUNT_NOT_DECIMAL",
        "CURRENCY_SHAPE",
        "TIMESTAMP_UNPARSEABLE",
        "INTERVAL_INVERTED",
        "DANGLING_LOCAL_ID",
        "DUPLICATE_LOCAL_ID",
        "COMMITMENT_BAD_MODALITY",
        "CURRENCY_MISSING_WITHOUT_FLAG",
    }
)

#: ``14_PROMPTS.md`` section 7.2: a leaked nonce and a refusal are never
#: repaired. A repair attempt on a leak would send the leaked nonce back to the
#: model, which is the one thing guaranteed to make the leak worse.
NO_REPAIR_CODES: Final[frozenset[str]] = frozenset({"NONCE_LEAKED_IN_OUTPUT", "MODEL_REFUSAL"})


def _sentence_count(text: str) -> int:
    return len(_SENTENCE_END.findall(text.strip())) or 1


def _scan_for_uuid(path: str, text: str, *, source_text: str = "") -> list[ValidationFailure]:
    """Flag a UUID the model *invented*, not one it *quoted*.

    Section 7.3 prints the check as "no UUID-shaped string anywhere in the
    output", which is right for everything the model authors and wrong for the
    one thing it does not: adversarial row A6 is an artifact whose own text
    contains a UUID, and the extractor is required to admit that text verbatim
    because evidence is append-only. Refusing the artifact outright would
    suppress the record of the attempt, which section 10's positive control
    forbids.

    So the rule is "invented, not quoted": a UUID appearing verbatim in the
    supplied artifact text belongs to the artifact and is admitted as data. Row
    A6's other half still holds -- the Kernel rejects any proposal that
    *references* a row that does not exist or belongs to another user, and this
    graph never turns an evidence candidate's text into an id reference.
    """
    failures: list[ValidationFailure] = []
    for match in _UUID_PATTERN.finditer(text):
        if match.group(0) in source_text:
            continue
        failures.append(
            ValidationFailure(
                path=path,
                code="FABRICATED_UUID",
                detail=(
                    f"{match.group(0)!r} is a UUID-shaped string that appears nowhere in "
                    "the artifact; the model is given no real Provenance identifier and "
                    "may not invent one"
                ),
            )
        )
    return failures


def validate_extraction(
    result: ExtractionResult,
    *,
    blocks: Sequence[ContentBlock],
    nonce: str | None = None,
) -> tuple[ValidationFailure, ...]:
    """The ``validate_extraction_schema`` check list, as data.

    Returns every failure rather than the first, because the repair envelope
    sends the whole array and a repair that fixes one of four problems wastes
    the single attempt.
    """
    by_id: Mapping[str, ContentBlock] = {b.block_id: b for b in blocks}
    failures: list[ValidationFailure] = []

    # A leaked nonce short-circuits: it is never repaired, and reporting the
    # other failures alongside it would invite a repair call that must not happen.
    haystack = result.model_dump_json()
    corpus = "\n".join(b.text for b in blocks)
    if nonce is not None and _NONCE_PATTERN.fullmatch(nonce) and nonce in haystack:
        return (
            ValidationFailure(
                path="$",
                code="NONCE_LEAKED_IN_OUTPUT",
                detail="the live untrusted-evidence fence nonce appears in the model output",
            ),
        )

    if _sentence_count(result.artifact_summary) > _SUMMARY_SENTENCE_CAP:
        failures.append(
            ValidationFailure(
                path="artifact_summary",
                code="SUMMARY_TOO_LONG",
                detail=f"artifact_summary is at most {_SUMMARY_SENTENCE_CAP} sentences",
            )
        )

    failures.extend(_scan_for_uuid("artifact_summary", result.artifact_summary, source_text=corpus))

    for index, candidate in enumerate(result.evidence_candidates):
        path = f"evidence_candidates[{index}]"
        source = by_id.get(candidate.block_id)
        if source is None:
            failures.append(
                ValidationFailure(
                    path=f"{path}.block_id",
                    code="UNKNOWN_BLOCK_ID",
                    detail=(
                        f"{candidate.block_id!r} was not one of the blocks rendered to the "
                        "model; a hallucinated locator is unadmittable provenance"
                    ),
                )
            )
            continue
        locator = candidate.source_locator
        start, end = locator.char_start, locator.char_end
        if start is None or end is None:
            failures.append(
                ValidationFailure(
                    path=f"{path}.source_locator",
                    code="SPAN_MISSING",
                    detail="no character span; no candidate without provenance may be admitted",
                )
            )
            continue
        if end > len(source.text):
            failures.append(
                ValidationFailure(
                    path=f"{path}.source_locator",
                    code="SPAN_OUT_OF_RANGE",
                    detail=(
                        f"chars {start}-{end} exceed block {source.block_id} "
                        f"of length {len(source.text)}"
                    ),
                )
            )
            continue
        if source.text[start:end] != candidate.exact_text:
            failures.append(
                ValidationFailure(
                    path=f"{path}.exact_text",
                    code="SPAN_TEXT_MISMATCH",
                    detail=(
                        f"exact_text does not match block {source.block_id} " f"chars {start}-{end}"
                    ),
                )
            )
        failures.extend(
            _scan_for_uuid(f"{path}.normalized_text", candidate.normalized_text, source_text=corpus)
        )

    for index, amount in enumerate(result.amounts):
        source = by_id.get(amount.block_id)
        if source is None:
            failures.append(
                ValidationFailure(
                    path=f"amounts[{index}].block_id",
                    code="UNKNOWN_BLOCK_ID",
                    detail=f"{amount.block_id!r} was not rendered to the model",
                )
            )
            continue
        if amount.raw_text not in source.text:
            failures.append(
                ValidationFailure(
                    path=f"amounts[{index}].raw_text",
                    code="AMOUNT_NOT_IN_SOURCE_BLOCK",
                    detail=(
                        f"{amount.raw_text!r} does not appear in block {source.block_id}; "
                        "money is copied, never computed, and never summed into a total the "
                        "artifact did not state"
                    ),
                )
            )

    quoted_blocks = {b.block_id for b in blocks if b.kind is ContentBlockKind.QUOTED_HISTORY}
    claims_by_local_id = {c.local_id: c for c in result.claim_candidates}
    evidence_by_local_id = {e.local_id: e for e in result.evidence_candidates}

    for index, claim in enumerate(result.claim_candidates):
        if claim.claim_kind is ClaimKind.INFERENCE:
            failures.append(
                ValidationFailure(
                    path=f"claim_candidates[{index}].claim_kind",
                    code="INFERENCE_CLAIM_EMITTED",
                    detail=(
                        "INFERENCE is reserved for a later deterministic stage; the "
                        "extractor reports what the artifact says, never what follows from it"
                    ),
                )
            )

    for index, commitment in enumerate(result.commitment_candidates):
        source_claim = claims_by_local_id.get(commitment.source_claim_local_id)
        if source_claim is None:
            continue
        evidence = evidence_by_local_id.get(source_claim.evidence_local_id)
        block_id = evidence.block_id if evidence is not None else None
        if source_claim.quoted or (block_id is not None and block_id in quoted_blocks):
            failures.append(
                ValidationFailure(
                    path=f"commitment_candidates[{index}]",
                    code="COMMITMENT_FROM_QUOTED_BLOCK",
                    detail=(
                        "the source claim is quoted history; a promise found only inside a "
                        "forwarded thread is not a new promise, and re-admitting it would "
                        "fabricate a second obligation from one sentence"
                    ),
                )
            )

    return tuple(failures)


def validate_attention(
    assessment: object,
    *,
    supported_actions: Iterable[str],
    proof_ids: frozenset[str],
) -> tuple[ValidationFailure, ...]:
    """Section 5.3's post-checks. Kept importable for the advocate nodes.

    The concrete type is :class:`~agents.runtime.schemas.advocacy.AttentionAssessment`;
    it is accepted as ``object`` here only to keep this module free of an import
    cycle, and every attribute read below is present on that model.
    """
    from agents.runtime.schemas.advocacy import AttentionAssessment

    if not isinstance(assessment, AttentionAssessment):  # pragma: no cover - guard
        raise TypeError("validate_attention expects an AttentionAssessment")

    failures: list[ValidationFailure] = []
    permitted = {str(a) for a in supported_actions}
    if (
        assessment.recommended_action_type is not None
        and str(assessment.recommended_action_type) not in permitted
    ):
        failures.append(
            ValidationFailure(
                path="recommended_action_type",
                code="UNSUPPORTED_ACTION_TYPE",
                detail=(
                    f"{assessment.recommended_action_type} is not in the case's "
                    "action_policy.supported_actions"
                ),
            )
        )
    if _sentence_count(assessment.rationale_summary) > _RATIONALE_SENTENCE_CAP:
        failures.append(
            ValidationFailure(
                path="rationale_summary",
                code="SUMMARY_TOO_LONG",
                detail=f"rationale_summary is at most {_RATIONALE_SENTENCE_CAP} sentences",
            )
        )
    cited = {
        *(str(i) for i in assessment.supporting_belief_version_ids),
        *(str(i) for i in assessment.supporting_conflict_ids),
        *(str(i) for i in assessment.supporting_commitment_ids),
    }
    unknown = sorted(cited - proof_ids)
    if unknown:
        failures.append(
            ValidationFailure(
                path="supporting_ids",
                code="UNSUPPORTED_SENTENCE",
                detail=f"ids {unknown} are not in the State Proof this assessment was given",
            )
        )
    return tuple(failures)


def validate_draft(
    draft: DraftAction,
    *,
    support_ids: frozenset[str],
    supported_actions: Iterable[str],
    recipient_allowlist_domains: Iterable[str],
    max_body_chars: int,
    open_human_conflict: bool,
    current_case_revision: int,
    current_proof_hash: str,
) -> tuple[ValidationFailure, ...]:
    """``validate_draft_claims``, section 8, plus the policy post-checks.

    Sentence/span equality and the forbidden-internal-vocabulary scan are
    already enforced by :class:`~provenance_contracts.actions.DraftAction`, so
    an invalid draft of that kind is unconstructable rather than reported here.
    What remains is everything that needs the State Proof and the action policy,
    which the contract cannot see.
    """
    failures: list[ValidationFailure] = []

    for index, claim in enumerate(draft.claims):
        missing = sorted(str(i) for i in claim.support_ids if str(i) not in support_ids)
        if missing:
            failures.append(
                ValidationFailure(
                    path=f"claims[{index}].support_ids",
                    code="UNSUPPORTED_SENTENCE",
                    detail=(
                        f"support ids {missing} are not in the current State Proof; a claim "
                        "citing anything outside it is unsupported by definition"
                    ),
                )
            )
        if claim.sentence_or_span not in draft.body:
            failures.append(
                ValidationFailure(
                    path=f"claims[{index}].sentence_or_span",
                    code="SENTENCE_NOT_IN_BODY",
                    detail="the cited sentence does not appear in the body it claims to quote",
                )
            )

    if str(draft.action_type) not in {str(a) for a in supported_actions}:
        failures.append(
            ValidationFailure(
                path="action_type",
                code="UNSUPPORTED_ACTION_TYPE",
                detail=f"{draft.action_type} is not in action_policy.supported_actions",
            )
        )

    domains = {d.lower() for d in recipient_allowlist_domains}
    domain = draft.recipient.rsplit("@", 1)[-1].lower()
    if domains and domain not in domains:
        failures.append(
            ValidationFailure(
                path="recipient",
                code="RECIPIENT_NOT_IN_POLICY",
                detail=(
                    f"{domain!r} is not in the case's recipient allowlist; the recipient "
                    "comes from the action policy, never from the artifact"
                ),
            )
        )

    if len(draft.body) > max_body_chars:
        failures.append(
            ValidationFailure(
                path="body",
                code="BODY_TOO_LONG",
                detail=f"body is {len(draft.body)} chars against a policy cap of {max_body_chars}",
            )
        )

    if open_human_conflict and not draft.unresolved_risks:
        failures.append(
            ValidationFailure(
                path="unresolved_risks",
                code="RISK_UNSTATED_ON_HUMAN_CONFLICT",
                detail=(
                    "the case carries a conflict the record says needs a human, and the draft "
                    "states no risk; a confident draft over an unresolved contradiction is a "
                    "calibration failure, not a clean result"
                ),
            )
        )

    failures.extend(_scan_for_uuid("body", draft.body))
    failures.extend(_scan_for_uuid("subject", draft.subject))

    if draft.basis_case_revision != current_case_revision:
        failures.append(
            ValidationFailure(
                path="basis_case_revision",
                code="STALE_PROOF_BINDING",
                detail=(
                    f"draft binds revision {draft.basis_case_revision} but the proof describes "
                    f"{current_case_revision}"
                ),
            )
        )
    if draft.basis_proof_hash != current_proof_hash:
        failures.append(
            ValidationFailure(
                path="basis_proof_hash",
                code="STALE_PROOF_BINDING",
                detail="draft binds a proof hash that is not the current one",
            )
        )

    return tuple(failures)


def money_is_decimal(value: object) -> bool:
    """``Decimal`` or nothing. Never ``float``, anywhere on a monetary path."""
    return isinstance(value, Decimal)
