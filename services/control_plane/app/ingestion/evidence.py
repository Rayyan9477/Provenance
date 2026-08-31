"""Section 9.4: admitting evidence, and the two checks that make it safe.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.4, five steps.
- ``specs/10_DATABASE_DDL.md`` section 12 write rule ``W4`` -- the app holds
  ``INSERT`` on ``evidence_items`` and deliberately **not** ``UPDATE``, because
  only the Memory Kernel may retract evidence.
- ``CANONICAL_DECISIONS.md`` -> *Evidence lifecycle* and -> *Canonical writer*.

Steps 1 and 2 are the reason this module exists
------------------------------------------------
Step 1: every ``block_id`` must exist in the bound artifact's parsed blocks.
Step 2: ``exact_text`` must be a substring of the cited block after whitespace
normalisation. The spec calls step 2 "the deterministic defence against a model
inventing a quotation", and it is the only defence there is: an LLM asked to
quote a document will produce a fluent, plausible, well-formed sentence that is
not in it, and no confidence score distinguishes that from a real quotation.

Both checks are *scoped to the cited block*, not to the artifact. Citing the
new-message block for text that appears only in the quoted-history block is a
false locator, and it is precisely how a four-month-old forwarded promise gets
admitted as a new one.

What happens when the guard cannot run
---------------------------------------
It refuses. :class:`~...ingestion.blocks.ParserOutputUnavailable` reaching
:func:`admissions` raises ``PROVENANCE_UNCHECKABLE`` rather than admitting the
rows unchecked. Admitting them would be ``D-00-005`` inverted -- performing an
action *because* its check could not be performed -- into a table that is
append-only, so nothing downstream could ever tell an unchecked row from a
checked one. Every artifact the seed wrote is in exactly that state, so this is
the ordinary path rather than an edge case.

Step 3 -- the authority this build declines to invent
------------------------------------------------------
The spec assigns ``source_authority`` server-side "from the predicate-aware
authority table using the artifact's source class", and its example prints
``"0.4500"``. ``provenance_domain.authority.AUTHORITY_SCORES`` is a
``(source class x predicate family)`` **grid**, and its docstring says why it
must be one: "a bank knows a charge cleared (PAYMENT 0.97) and knows nothing
about whether an ISP honoured a cancellation (SERVICE_STATUS 0.10). One number
cannot hold both, and averaging them produces a source that is mediocre at
everything and correct at nothing."

An evidence item has no predicate. The *claim* does, and claims are the
Kernel's. So this module derives and records the **source class** -- which is
the input the grid needs and the thing the artifact actually determines -- and
leaves ``source_authority`` NULL, which the column permits. Producing a number
here would be ``authority_from_confidence``'s named mistake in a different
disguise: a score manufactured at a point where its input does not exist.

Recorded as a deviation from section 9.4's printed example rather than smoothed
over, and disclosed in the response so a caller is never told a score was
computed.

Step 4 -- the embedding that cannot be stored
-----------------------------------------------
``evidence_items.embedding`` is ``VECTOR(1024)`` and
``ck_evidence_embedding_model`` admits only
``amazon.titan-embed-text-v2:0``. The embedder this build ships is
``GeminiEmbedder`` over ``gemini-embedding-2``, 1536 wide. Migration ``0009``
widens both and is deliberately unapplied, so the only legal write is
``embedding = NULL`` -- which ``ck_evidence_embedding_provenance`` permits and
which excludes the row from every ANN query.

That is a real cost and it is disclosed rather than hidden: the response
carries ``embedding_status = "NOT_COMPUTED"`` and the reason, so a caller
learns the row will not retrieve at the moment it is created rather than from a
silent absence months later.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from psycopg.types.json import Jsonb

from provenance_contracts.ingestion import ContentBlock, NormalizedContent
from provenance_domain.enums import ArtifactSourceType, SourceClass
from services.control_plane.app.ingestion.blocks import ParserOutputUnavailable

__all__ = [
    "EVIDENCE_BY_TEXT_SHA_SQL",
    "EVIDENCE_INSERT_SQL",
    "EMBEDDING_NOT_COMPUTED_REASON",
    "Admission",
    "EvidenceRefusedError",
    "admissions",
    "insert_params",
    "normalise_span",
    "register_admissions",
    "source_class_for",
]

#: Write rule ``W4``. The app admits evidence; only the Kernel retracts it, so
#: there is no ``UPDATE`` here and there must never be one.
#:
#: ``is_retrieval_eligible`` is absent because it is a generated ``STORED``
#: column: naming it is an error, not a redundancy.
EVIDENCE_INSERT_SQL: Final[str] = """
INSERT INTO evidence_items (
    id, tenant_id, user_id, artifact_id, evidence_type, normalized_text, exact_text,
    source_locator, actor_ref, valid_from, valid_to, observed_at, extraction_confidence,
    source_authority, retraction_status, embedding, embedding_model, embedding_version,
    embedding_generated_at, normalized_text_sha256, created_at
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(artifact_id)s, %(evidence_type)s,
    %(normalized_text)s, %(exact_text)s, %(source_locator)s, %(actor_ref)s,
    %(valid_from)s, %(valid_to)s, %(observed_at)s, %(extraction_confidence)s,
    %(source_authority)s, %(retraction_status)s, %(embedding)s, %(embedding_model)s,
    %(embedding_version)s, %(embedding_generated_at)s, %(normalized_text_sha256)s,
    %(created_at)s
)
ON CONFLICT DO NOTHING
"""

#: Section 9.4 step 5's dedupe key. There is no unique index on it -- migration
#: ``0002``'s ``idx_evidence_text_hash`` is partial on ``embedding IS NOT NULL``
#: and indexes a different tuple -- so dedupe is a read, and the read is scoped
#: by owner and artifact exactly as the step describes.
EVIDENCE_BY_TEXT_SHA_SQL: Final[str] = """
SELECT id, encode(normalized_text_sha256, 'hex') AS text_sha
FROM evidence_items
WHERE tenant_id = %(tenant_id)s
  AND user_id = %(user_id)s
  AND artifact_id = %(artifact_id)s
  AND normalized_text_sha256 = ANY(%(text_shas)s)
"""

EMBEDDING_NOT_COMPUTED_REASON: Final[str] = (
    "evidence_items.embedding is VECTOR(1024) and ck_evidence_embedding_model admits only "
    "amazon.titan-embed-text-v2:0; the shipping profile is gemini-embedding-2 at 1536. "
    "Migration 0009 widens both and is deliberately unapplied, so this row is admitted "
    "with no embedding and will not be returned by ANN retrieval."
)

_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


class EvidenceRefusedError(Exception):
    """A typed refusal carrying section 9.4's reason code and ``details``.

    Same shape as ``ProposalRefusedError`` and ``ActionRefusedError`` so the API
    layer maps a code to an ``ErrorCode`` in one place and this package holds no
    dependency on the API layer.
    """

    def __init__(self, reason_code: str, **details: Any) -> None:
        super().__init__(f"{reason_code}: {details}")
        self.reason_code = reason_code
        self.details: dict[str, Any] = details


@dataclass(frozen=True, slots=True)
class Admission:
    """One candidate that passed steps 1 and 2, carrying the block it cited.

    The block travels with the candidate rather than being looked up again
    later: a second lookup is a second chance for the two to disagree, and the
    thing that was checked must be the thing that is recorded.
    """

    client_ref: str
    evidence_type: str
    block: ContentBlock
    exact_text: str
    normalized_text: str
    actor_ref: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime
    extraction_confidence: Decimal
    caller_locator: Mapping[str, Any] | None

    @property
    def text_sha256(self) -> bytes:
        return hashlib.sha256(self.normalized_text.encode("utf-8")).digest()


def normalise_span(text: str) -> str:
    """Collapse runs of whitespace to a single space, and strip.

    Whitespace **only**. Collapsing it to nothing would admit ``USD186.00`` as
    a quotation of ``USD 186.00``, and from there almost anything: a check that
    ignores separators stops being a containment test.
    """
    return _WHITESPACE.sub(" ", text).strip()


def admissions(
    *,
    candidates: Sequence[Any],
    content: NormalizedContent | ParserOutputUnavailable,
) -> tuple[Admission, ...]:
    """Steps 1 and 2, over every candidate, before any row is written.

    All candidates are checked before any is admitted. A partial admission
    followed by a refusal would leave rows in an append-only table for a
    request the caller was told failed, and the caller would then retry.

    Raises:
        EvidenceRefusedError: ``PROVENANCE_UNCHECKABLE`` when the artifact has
            no readable blocks; ``PROPOSAL_FOREIGN_PROVENANCE`` when a cited
            block does not exist; ``VALIDATION_FAILED`` /
            ``SPAN_NOT_IN_BLOCK`` when a quotation is not in the block it cites.
    """
    if isinstance(content, ParserOutputUnavailable):
        raise EvidenceRefusedError(
            "PROVENANCE_UNCHECKABLE",
            parser_reason_code=content.reason_code,
            parser_status=content.parser_status,
            detail=content.detail,
            guard=(
                "section 9.4 steps 1 and 2 are the deterministic defence against an "
                "invented quotation and they have nothing to check against. "
                "evidence_items is append-only, so a row admitted here could never be "
                "distinguished from one that passed."
            ),
        )

    by_id = {block.block_id: block for block in content.blocks}
    unknown = sorted({c.block_id for c in candidates if c.block_id not in by_id})
    if unknown:
        raise EvidenceRefusedError(
            "PROPOSAL_FOREIGN_PROVENANCE",
            unknown_block_ids=unknown,
            known_block_ids=sorted(by_id),
        )

    admitted: list[Admission] = []
    for candidate in candidates:
        block = by_id[candidate.block_id]
        if normalise_span(candidate.exact_text) not in normalise_span(block.text):
            raise EvidenceRefusedError(
                "VALIDATION_FAILED",
                reason="SPAN_NOT_IN_BLOCK",
                client_ref=candidate.client_ref,
                block_id=candidate.block_id,
                block_sha256=block.content_sha256,
            )
        admitted.append(
            Admission(
                client_ref=candidate.client_ref,
                evidence_type=candidate.evidence_type,
                block=block,
                exact_text=candidate.exact_text,
                normalized_text=candidate.normalized_text,
                actor_ref=candidate.actor_ref,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                observed_at=candidate.observed_at,
                extraction_confidence=Decimal(str(candidate.extraction_confidence)),
                caller_locator=candidate.source_locator,
            )
        )
    return tuple(admitted)


# ---------------------------------------------------------------------------
# Step 3 -- the source class
# ---------------------------------------------------------------------------

#: Artifact source type -> the class an *authenticated* artifact of that kind
#: has. Inbound provider mail is a system notice; anything a user supplied is
#: attributed to the user, because the system observed the upload and not the
#: document's origin.
_CLASS_BY_SOURCE_TYPE: Final[Mapping[ArtifactSourceType, SourceClass]] = {
    ArtifactSourceType.EMAIL_INBOUND: SourceClass.PROVIDER_SYSTEM_NOTICE,
    ArtifactSourceType.UPLOAD_EML: SourceClass.PROVIDER_AGENT_WRITTEN,
    ArtifactSourceType.UPLOAD_PDF: SourceClass.USER_UPLOADED_RECEIPT,
    ArtifactSourceType.UPLOAD_IMAGE: SourceClass.USER_UPLOADED_RECEIPT,
    ArtifactSourceType.UPLOAD_TEXT: SourceClass.USER_STATEMENT,
    ArtifactSourceType.USER_CORRECTION: SourceClass.USER_CORRECTION,
    ArtifactSourceType.SEED_FIXTURE: SourceClass.PROVIDER_SYSTEM_NOTICE,
}

#: The three verdicts that authenticate a sender. ``spam`` and ``virus`` are
#: absent: section 9.1 already refuses on those, so a message that reaches here
#: passed them and they carry no information about authorship.
_AUTHENTICATION_VERDICTS: Final[tuple[str, ...]] = ("spf", "dkim", "dmarc")


def source_class_for(
    source_type: ArtifactSourceType | str, *, ses_verdicts: Mapping[str, Any] | None
) -> SourceClass:
    """The class this artifact's contents are worth, server-side.

    Section 9.4 step 3: "a caller-supplied authority is not accepted; the field
    does not exist in the request schema". The same rule applies to the class,
    which is the input the authority grid takes.

    A failed SPF, DKIM or DMARC does not reject the message -- section 9.1 is
    explicit that a spoofed sender is itself meaningful evidence. It *demotes*
    it: an unauthenticated message claiming to be from a provider is a
    statement someone made, so it drops to ``USER_STATEMENT`` and can no longer
    outrank the provider's own authenticated notices.
    """
    kind = (
        source_type
        if isinstance(source_type, ArtifactSourceType)
        else ArtifactSourceType(str(source_type))
    )
    resolved = _CLASS_BY_SOURCE_TYPE.get(kind, SourceClass.USER_STATEMENT)
    if ses_verdicts and any(
        str(ses_verdicts.get(name, "")).upper() == "FAIL" for name in _AUTHENTICATION_VERDICTS
    ):
        return SourceClass.USER_STATEMENT
    return resolved


# ---------------------------------------------------------------------------
# The row
# ---------------------------------------------------------------------------


def insert_params(
    admission: Admission,
    *,
    evidence_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    source_class: SourceClass,
    created_at: datetime,
) -> dict[str, Any]:
    """Every bind for :data:`EVIDENCE_INSERT_SQL`.

    ``source_locator`` merges the caller's locator **under** the server's, so a
    caller cannot overwrite the block id its span was actually checked against
    or the class the server derived.
    """
    locator: dict[str, Any] = dict(admission.caller_locator or {})
    locator.update(
        {
            "kind": admission.block.source_locator.kind,
            "block_id": admission.block.block_id,
            "block_sha256": admission.block.content_sha256,
            "block_ordinal": admission.block.ordinal,
            "block_kind": str(admission.block.kind),
            "source_class": str(source_class),
        }
    )
    return {
        "id": evidence_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "artifact_id": artifact_id,
        "evidence_type": admission.evidence_type,
        "normalized_text": admission.normalized_text,
        "exact_text": admission.exact_text,
        "source_locator": Jsonb(locator),
        "actor_ref": admission.actor_ref,
        "valid_from": admission.valid_from,
        "valid_to": admission.valid_to,
        "observed_at": admission.observed_at,
        "extraction_confidence": admission.extraction_confidence,
        # See the module docstring, "Step 3": the grid needs a predicate family
        # and an evidence item has none. The class is on the locator instead.
        "source_authority": None,
        "retraction_status": "ACTIVE",
        # See "Step 4". NULL is the only legal value this schema admits, and it
        # is disclosed to the caller rather than left to be discovered.
        "embedding": None,
        "embedding_model": None,
        "embedding_version": None,
        "embedding_generated_at": None,
        "normalized_text_sha256": admission.text_sha256,
        "created_at": created_at,
    }


async def register_admissions(
    conn: Any,
    admitted: Sequence[Admission],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    source_class: SourceClass,
    created_at: datetime,
    new_id: Any = uuid.uuid4,
) -> list[dict[str, Any]]:
    """Step 5's dedupe, then the ``W4`` INSERT for what is genuinely new.

    Returns section 9.4's ``evidence[]`` entries, one per admission and in the
    order they were offered, each carrying ``created`` so the caller can tell a
    new row from a recognised one.
    """
    existing = await _existing_by_text_sha(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        artifact_id=artifact_id,
        shas=[a.text_sha256 for a in admitted],
    )
    results: list[dict[str, Any]] = []
    for admission in admitted:
        sha_hex = admission.text_sha256.hex()
        found = existing.get(sha_hex)
        if found is not None:
            results.append(
                {
                    "client_ref": admission.client_ref,
                    "evidence_id": str(found),
                    "created": False,
                    "source_authority": None,
                    "source_class": str(source_class),
                    "embedding_version": None,
                }
            )
            continue
        evidence_id = new_id()
        params = insert_params(
            admission,
            evidence_id=evidence_id,
            tenant_id=tenant_id,
            user_id=user_id,
            artifact_id=artifact_id,
            source_class=source_class,
            created_at=created_at,
        )
        async with conn.cursor() as cur:
            await cur.execute(EVIDENCE_INSERT_SQL, params)
        # The sha is remembered so two candidates with identical normalized
        # text inside one request deduplicate against each other, not only
        # against rows that were already there.
        existing[sha_hex] = evidence_id
        results.append(
            {
                "client_ref": admission.client_ref,
                "evidence_id": str(evidence_id),
                "created": True,
                "source_authority": None,
                "source_class": str(source_class),
                "embedding_version": None,
            }
        )
    return results


async def _existing_by_text_sha(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    shas: Sequence[bytes],
) -> dict[str, uuid.UUID]:
    if not shas:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            EVIDENCE_BY_TEXT_SHA_SQL,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "artifact_id": artifact_id,
                "text_shas": list(shas),
            },
        )
        rows = await cur.fetchall()
    return {str(row[1]): row[0] for row in rows}
