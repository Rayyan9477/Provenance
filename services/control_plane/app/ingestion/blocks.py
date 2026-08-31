"""The parser, and the column that holds what it produced.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.3 -- the content blocks a run reads, and
  the closed ``kind`` vocabulary they carry.
- ``specs/11_CONTRACTS.md`` section 8 --
  :class:`~provenance_contracts.ingestion.ContentBlock` and
  :class:`~provenance_contracts.ingestion.NormalizedContent`, which this module
  produces rather than re-declares.
- ``db/migrations/versions/0002_evidence_plane.py`` --
  ``source_artifacts.parser_metadata`` is ``JSONB`` and is already applied.

Where blocks live, and why that needed no migration
-----------------------------------------------------
``adapters/unbound.py`` recorded that "no migration creates a table or a column
that holds a block". That was read too narrowly: ``parser_metadata JSONB`` has
existed since ``0002`` and is exactly the shape a parser's output has -- a
versioned document belonging to one artifact, read whole, written once. Storing
blocks there needs **no schema change**, which matters because a schema change
cannot be applied against this cluster from here.

Section 8.18 also names an object-store location for parser output
(``normalized/{...}/parser-v{n}.json``), and both are legitimate. The column is
what section 9.3 reads on the request path: one row read already scoped by
``(tenant_id, user_id)``, with no second round trip to a store that may be
unreachable. :func:`normalized_key` remains the location for the same document
when a deployment wants it addressable outside the database.

Three states, not two
---------------------
``D-00-005``. :func:`read_normalized_content` returns either a
:class:`~provenance_contracts.ingestion.NormalizedContent` -- a real parse, with
however many blocks it found, possibly zero -- or a
:class:`ParserOutputUnavailable`, which carries a reason code and **has no
block list at all**. It cannot: a type with a ``blocks`` attribute would
eventually be read as one, and the whole point is that "not parsed" and "parsed
and empty" must not be the same value.

The reason codes are three, and they are different failures:

``PARSE_NOT_COMPLETE``
    ``parser_status`` is not ``PARSED``. Nothing was claimed and nothing is
    missing.
``PARSER_OUTPUT_UNAVAILABLE``
    ``parser_status = 'PARSED'`` and there is no stored output. **Every
    ``source_artifacts`` row the seed writes is in this state**, because
    ``scripts/seed/rows.py`` sets ``parser_status='PARSED'`` and
    ``parser_version='seed-1.0.0'`` and writes no ``parser_metadata``. The
    column asserts a parse whose output nobody can read back, and this code
    says so instead of inventing an empty document for it.
``PARSER_OUTPUT_UNREADABLE``
    Stored output that does not deserialise into blocks. A defect, not an
    empty result.

What this parser does and does not handle
------------------------------------------
``message/rfc822`` and ``text/plain``, with the standard library's ``email``
module. No Textract, no OCR, no external service and no credential -- which is
why the demo's artifact, ``demo/artifacts/northline-june-invoice.eml``, goes
end to end on a laptop.

``application/pdf``, ``image/png`` and ``image/jpeg`` are reported
``UNSUPPORTED_MIME`` with a reason. That is a verdict, and it is deliberately
not ``PARSED`` with zero blocks: a run handed an empty block list extracts
nothing and reports success, and the artifact looks processed forever. No PDF
text extractor ships in this build, so the honest status is the one that says
the parser could not read it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any, Final

from provenance_contracts.ingestion import ContentBlock, NormalizedContent, SourceLocator
from provenance_domain.enums import ContentBlockKind, ParserStatus

__all__ = [
    "BLOCK_STORE_COLUMN",
    "PARSABLE_MIME_TYPES",
    "PARSER_METADATA_SCHEMA_VERSION",
    "PARSER_NAME",
    "PARSER_VERSION",
    "ParseOutcome",
    "ParserOutputUnavailable",
    "artifact_headers",
    "parse_artifact",
    "parser_metadata_value",
    "read_normalized_content",
]

#: Where a parsed block is persisted, as ``table.column``.
#:
#: Named rather than inlined because a test reads it: the register entry under
#: ``internal.artifact_content`` claimed nothing persists a block, and the
#: assertion that flips that claim has to point at a specific column so it can
#: check the column exists in the migrations. A grep for "block" in the DDL
#: would not find this one -- the column is called ``parser_metadata``.
BLOCK_STORE_COLUMN: Final[str] = "source_artifacts.parser_metadata"

#: The stored document's own version, independent of ``PARSER_VERSION``. A
#: reader that met an unknown value must refuse rather than guess at the shape.
PARSER_METADATA_SCHEMA_VERSION: Final[str] = "pv.parser_metadata/1.0"

PARSER_NAME: Final[str] = "pv-eml"

#: ``source_artifacts.parser_version``. Bumped when the block *shape* changes,
#: because ``ck_source_artifacts_parsed_has_version`` makes this column the
#: record of which parser produced the stored output.
PARSER_VERSION: Final[str] = "pv-eml-1.0.0"

#: What :func:`parse_artifact` can actually read. Everything else on section
#: 8.18's upload allowlist is reported ``UNSUPPORTED_MIME``.
PARSABLE_MIME_TYPES: Final[frozenset[str]] = frozenset({"message/rfc822", "text/plain"})

#: The headers section 9.3's ``HEADER`` block carries. Not every header: a
#: parsed ``Received:`` chain is a routing record, not evidence, and it is the
#: bulkiest part of a forwarded message.
_HEADER_FIELDS: Final[tuple[str, ...]] = ("From", "To", "Cc", "Date", "Reply-To")

#: Blocks are capped by ``NormalizedContent.blocks`` at 500 and text at 100,000
#: characters by ``ContentBlock.text``. Both are enforced here rather than left
#: to a pydantic error at the boundary, so a large artifact truncates with
#: ``truncated=True`` instead of failing to parse at all.
_MAX_BLOCKS: Final[int] = 500
_MAX_BLOCK_CHARS: Final[int] = 100_000


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """What one parse produced, including the case where it produced nothing.

    ``status`` is the value that goes into ``source_artifacts.parser_status``
    and ``parser_version`` the value that goes into
    ``source_artifacts.parser_version``. They travel together because
    ``ck_source_artifacts_parsed_has_version`` requires it:
    ``parser_status <> 'PARSED' OR parser_version IS NOT NULL``.
    """

    status: ParserStatus
    parser_version: str | None
    blocks: tuple[ContentBlock, ...]
    reason: str | None
    truncated: bool = False

    def __post_init__(self) -> None:
        if self.status is ParserStatus.PARSED and self.parser_version is None:
            raise ValueError(
                "PARSED without a parser_version is refused by "
                "ck_source_artifacts_parsed_has_version"
            )
        if self.status is not ParserStatus.PARSED and self.blocks:
            raise ValueError(f"{self.status} carries blocks, which asserts a parse that failed")


@dataclass(frozen=True, slots=True)
class ParserOutputUnavailable:
    """No readable parser output. **Deliberately carries no block list.**

    A type with an empty ``blocks`` attribute is a type somebody eventually
    iterates, and the resulting screen says "this artifact has no content"
    about an artifact nobody parsed. There is nothing to iterate here.
    """

    reason_code: str
    parser_status: str
    detail: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _block(
    *,
    artifact_id: uuid.UUID,
    ordinal: int,
    kind: ContentBlockKind,
    text: str,
    locator: SourceLocator,
) -> ContentBlock:
    return ContentBlock(
        block_id=locator.block_id,
        artifact_id=artifact_id,
        ordinal=ordinal,
        kind=kind,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_locator=locator,
    )


def _block_id(ordinal: int) -> str:
    return f"blk_{ordinal + 1:04d}"


def _body_text(message: EmailMessage) -> str:
    """The first ``text/plain`` part, or the whole payload as text.

    ``get_body`` walks ``multipart/alternative`` correctly; a message with only
    an HTML part yields nothing here and the artifact ends with no BODY block,
    which reads as a parse that found no text rather than as a failure -- the
    headers and subject were still read.
    """
    part = message.get_body(preferencelist=("plain",))
    if part is None:
        return ""
    content = part.get_content()
    return content if isinstance(content, str) else ""


def _is_quoted(paragraph: str) -> bool:
    """``>``-prefixed on every line.

    Decided here, once. Section 9.3: ``QUOTED_HISTORY`` tagging "is what lets
    the Interpreter distinguish a newly asserted promise from a quoted old
    one", and a rule re-decided at each stage is a rule that disagrees with
    itself. A four-month-old forwarded promise is admitted twice the first time
    two stages disagree.
    """
    lines = [line for line in paragraph.splitlines() if line.strip()]
    return bool(lines) and all(line.lstrip().startswith(">") for line in lines)


def _paragraph_blocks(
    *, artifact_id: uuid.UUID, body: str, start_ordinal: int
) -> tuple[list[ContentBlock], bool]:
    """Body paragraphs, in order, each with offsets into the normalised body.

    The offsets are into ``body`` -- the same string every locator in this
    artifact indexes -- so section 9.4 step 2's containment test has one
    coordinate system rather than one per block.
    """
    blocks: list[ContentBlock] = []
    ordinal = start_ordinal
    truncated = False
    cursor = 0
    for raw in body.split("\n\n"):
        paragraph = raw.strip()
        if not paragraph:
            cursor += len(raw) + 2
            continue
        offset = body.find(paragraph, cursor)
        if offset < 0:  # pragma: no cover - `paragraph` is a slice of `body`
            offset = cursor
        cursor = offset + len(paragraph)
        if len(paragraph) > _MAX_BLOCK_CHARS:
            paragraph = paragraph[:_MAX_BLOCK_CHARS]
            truncated = True
        if ordinal >= _MAX_BLOCKS:
            truncated = True
            break
        block_id = _block_id(ordinal)
        blocks.append(
            _block(
                artifact_id=artifact_id,
                ordinal=ordinal,
                kind=(
                    ContentBlockKind.QUOTED_HISTORY
                    if _is_quoted(paragraph)
                    else ContentBlockKind.BODY
                ),
                text=paragraph,
                locator=SourceLocator(
                    kind="TEXT_SPAN",
                    block_id=block_id,
                    char_start=offset,
                    char_end=offset + len(paragraph),
                ),
            )
        )
        ordinal += 1
    return blocks, truncated


def artifact_headers(data: bytes) -> dict[str, str | None]:
    """``subject``, ``sender``, ``recipient`` and ``source_message_id``.

    Read from the bytes rather than taken from the caller. Section 9.1 lets the
    SES worker *declare* these and section 8.18 does not carry them at all; the
    message itself is the authority, and a declared subject that disagrees with
    the message is exactly the kind of unverified assertion the ingest path is
    supposed to eliminate.
    """
    message = message_from_bytes(data, policy=policy.default)

    def _one(name: str) -> str | None:
        value = message.get(name)
        return None if value is None else str(value).strip() or None

    return {
        "subject": _one("Subject"),
        "sender": _one("From"),
        "recipient": _one("To"),
        "source_message_id": _one("Message-ID"),
    }


def parse_artifact(*, artifact_id: uuid.UUID, mime_type: str, data: bytes) -> ParseOutcome:
    """Bytes to blocks, or a verdict saying why not.

    Never raises for content it cannot read: an unreadable artifact is a state
    the row records, not an exception the request path has to translate.
    """
    if mime_type not in PARSABLE_MIME_TYPES:
        return ParseOutcome(
            status=ParserStatus.UNSUPPORTED_MIME,
            parser_version=None,
            blocks=(),
            reason=(
                f"{PARSER_NAME} reads {sorted(PARSABLE_MIME_TYPES)} and this build ships no "
                f"text extractor for {mime_type}. A PDF or an image therefore has no blocks, "
                "and reporting PARSED with none would tell a run the document was empty."
            ),
        )

    if mime_type == "text/plain":
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
        blocks, truncated = _paragraph_blocks(artifact_id=artifact_id, body=text, start_ordinal=0)
        return ParseOutcome(
            status=ParserStatus.PARSED,
            parser_version=PARSER_VERSION,
            blocks=tuple(blocks),
            reason=None,
            truncated=truncated,
        )

    message = message_from_bytes(data, policy=policy.default)
    blocks: list[ContentBlock] = []
    ordinal = 0

    subject = message.get("Subject")
    if subject is not None and str(subject).strip():
        block_id = _block_id(ordinal)
        blocks.append(
            _block(
                artifact_id=artifact_id,
                ordinal=ordinal,
                kind=ContentBlockKind.SUBJECT,
                text=str(subject).strip(),
                locator=SourceLocator(kind="EMAIL_PART", block_id=block_id, mime_part="subject"),
            )
        )
        ordinal += 1

    headers = [
        f"{name}: {str(message[name]).strip()}" for name in _HEADER_FIELDS if message.get(name)
    ]
    if headers:
        block_id = _block_id(ordinal)
        blocks.append(
            _block(
                artifact_id=artifact_id,
                ordinal=ordinal,
                kind=ContentBlockKind.HEADER,
                text="\n".join(headers),
                locator=SourceLocator(kind="EMAIL_PART", block_id=block_id, mime_part="headers"),
            )
        )
        ordinal += 1

    body = _body_text(message).replace("\r\n", "\n").strip()
    body_blocks, truncated = _paragraph_blocks(
        artifact_id=artifact_id, body=body, start_ordinal=ordinal
    )
    blocks.extend(body_blocks)

    return ParseOutcome(
        status=ParserStatus.PARSED,
        parser_version=PARSER_VERSION,
        blocks=tuple(blocks),
        reason=None,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# The stored document
# ---------------------------------------------------------------------------


def parser_metadata_value(
    outcome: ParseOutcome, *, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The ``source_artifacts.parser_metadata`` value for *outcome*.

    ``extra`` merges caller-owned keys -- section 9.1 preserves
    ``ses_verdicts`` here -- without letting them collide with the parser's
    own, which is why they are merged under and not over.
    """
    document: dict[str, Any] = {
        "schema_version": PARSER_METADATA_SCHEMA_VERSION,
        "parser": {"name": PARSER_NAME, "version": outcome.parser_version},
        "truncated": outcome.truncated,
        "blocks": [block.model_dump(mode="json") for block in outcome.blocks],
    }
    if outcome.reason is not None:
        document["reason"] = outcome.reason
    if extra:
        return {**dict(extra), **document}
    return document


def read_normalized_content(
    *,
    artifact_id: uuid.UUID,
    parser_status: str,
    parser_metadata: Mapping[str, Any] | None,
) -> NormalizedContent | ParserOutputUnavailable:
    """Section 9.3's payload, or a named reason it cannot be produced.

    The three reason codes are documented on the module. The one that costs
    something to get right is ``PARSER_OUTPUT_UNAVAILABLE``: the row says
    ``PARSED``, and the honest answer is still that there is nothing to read.
    """
    if parser_status != ParserStatus.PARSED.value:
        return ParserOutputUnavailable(
            reason_code="PARSE_NOT_COMPLETE",
            parser_status=parser_status,
            detail=(
                f"parser_status is {parser_status!r}; section 9.3 is defined only for "
                "PARSED, and an empty block list would read as an artifact with no content."
            ),
        )
    blocks_raw = None if parser_metadata is None else parser_metadata.get("blocks")
    if blocks_raw is None:
        return ParserOutputUnavailable(
            reason_code="PARSER_OUTPUT_UNAVAILABLE",
            parser_status=parser_status,
            detail=(
                f"the row claims PARSED and {BLOCK_STORE_COLUMN} holds no blocks. Every "
                "seeded artifact is in this state: scripts/seed/rows.py writes "
                "parser_status='PARSED' and parser_version='seed-1.0.0' and no parser "
                "output, so the column asserts a parse nobody can read back."
            ),
        )
    if not isinstance(blocks_raw, Sequence) or isinstance(blocks_raw, str | bytes):
        return ParserOutputUnavailable(
            reason_code="PARSER_OUTPUT_UNREADABLE",
            parser_status=parser_status,
            detail=f"{BLOCK_STORE_COLUMN}.blocks is {type(blocks_raw).__name__}, not a list",
        )

    parser = parser_metadata.get("parser") if parser_metadata else None
    version = parser.get("version") if isinstance(parser, Mapping) else None
    try:
        return NormalizedContent(
            artifact_id=artifact_id,
            parser_version=str(version or PARSER_VERSION),
            blocks=tuple(_blocks_from(blocks_raw)),
            truncated=bool(parser_metadata.get("truncated", False)),
        )
    except ValueError as exc:
        return ParserOutputUnavailable(
            reason_code="PARSER_OUTPUT_UNREADABLE",
            parser_status=parser_status,
            detail=f"stored blocks do not satisfy the ContentBlock contract: {exc}",
        )


def _blocks_from(raw: Iterable[Any]) -> list[ContentBlock]:
    return [ContentBlock.model_validate(entry) for entry in raw]
