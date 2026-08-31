"""The parser, and the column that finally holds what it produces.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.3 -- the content blocks a run reads, and
  the ``kind`` vocabulary they carry.
- ``specs/11_CONTRACTS.md`` section 8 --
  :class:`~provenance_contracts.ingestion.ContentBlock` and
  :class:`~provenance_contracts.ingestion.NormalizedContent`.
- ``db/migrations/versions/0002_evidence_plane.py`` --
  ``source_artifacts.parser_metadata`` is ``JSONB`` and already applied.

The distinction this file exists to hold
-----------------------------------------
``D-00-005``: *absence is not emptiness*. Three states are not two.

1. ``parser_status <> 'PARSED'`` -- nothing has been parsed.
2. ``parser_status = 'PARSED'`` and no stored parser output -- something claims
   a parse happened and its output cannot be read back. **Every
   ``source_artifacts`` row the seed writes is in this state**: it carries
   ``parser_status='PARSED'`` and ``parser_version='seed-1.0.0'`` and no
   ``parser_metadata`` at all.
3. ``parser_status = 'PARSED'`` with a stored, empty block list -- a real parse
   that found nothing.

Only the third is an empty result. A reader that returned ``[]`` for all three
would tell a run "this artifact has no content", and the run would extract
nothing and report success.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from provenance_contracts.ingestion import ContentBlock, NormalizedContent
from provenance_domain.enums import ContentBlockKind, ParserStatus
from services.control_plane.app.ingestion.blocks import (
    BLOCK_STORE_COLUMN,
    PARSER_VERSION,
    ParseOutcome,
    ParserOutputUnavailable,
    parse_artifact,
    parser_metadata_value,
    read_normalized_content,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
HERO_EML = REPO_ROOT / "demo" / "artifacts" / "northline-june-invoice.eml"

ARTIFACT = uuid.UUID("018f9e80-0000-7000-8000-000000000001")


def _hero_bytes() -> bytes:
    assert HERO_EML.is_file(), f"{HERO_EML} is the demo's artifact and must exist"
    return HERO_EML.read_bytes()


def test_the_block_store_column_names_a_column_the_applied_schema_has() -> None:
    """The blocks go somewhere real, and the name is not a guess.

    ``BLOCK_STORE_COLUMN`` is read by
    ``tests/api/test_port_adapters.py::test_a_content_block_can_be_persisted_and_read_back``
    to decide whether section 9.3 has a source, so it must name a column the
    migrations actually declare.
    """
    table, _, column = BLOCK_STORE_COLUMN.partition(".")
    assert table == "source_artifacts" and column == "parser_metadata", BLOCK_STORE_COLUMN
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "db" / "migrations" / "versions").glob("[0-9]*.py"))
    )
    assert "parser_metadata   JSONB" in source or "parser_metadata JSONB" in source, (
        "no migration declares source_artifacts.parser_metadata as JSONB, so the "
        "block store names a column that does not exist"
    )


def test_the_hero_invoice_parses_into_blocks_that_carry_its_numbers() -> None:
    """The demo's artifact, parsed by the stdlib, with nothing mocked."""
    outcome = parse_artifact(artifact_id=ARTIFACT, mime_type="message/rfc822", data=_hero_bytes())
    assert outcome.status is ParserStatus.PARSED
    assert outcome.parser_version == PARSER_VERSION
    assert outcome.reason is None
    assert len(outcome.blocks) >= 3, [b.kind for b in outcome.blocks]

    kinds = {block.kind for block in outcome.blocks}
    assert ContentBlockKind.SUBJECT in kinds
    assert ContentBlockKind.HEADER in kinds
    assert ContentBlockKind.BODY in kinds

    body = "\n".join(b.text for b in outcome.blocks if b.kind is ContentBlockKind.BODY)
    # `00_PRODUCT.md` section 2.3's three facts. If the parser drops the body,
    # the model has nothing to extract and every later assertion is vacuous.
    assert "USD 186.00" in body
    assert "NF-4471-8802" in body
    assert "30 June 2026" in body

    subject = next(b for b in outcome.blocks if b.kind is ContentBlockKind.SUBJECT)
    assert subject.text == "Invoice for June service"


def test_every_block_carries_a_digest_of_its_own_text_and_a_matching_locator() -> None:
    """A block whose sha names other text is a provenance record that lies."""
    outcome = parse_artifact(artifact_id=ARTIFACT, mime_type="message/rfc822", data=_hero_bytes())
    assert outcome.blocks, "nothing parsed; the assertions below would be vacuous"
    for block in outcome.blocks:
        assert block.content_sha256 == hashlib.sha256(block.text.encode("utf-8")).hexdigest()
        assert block.source_locator.block_id == block.block_id
        assert block.artifact_id == ARTIFACT
    ordinals = [block.ordinal for block in outcome.blocks]
    assert ordinals == sorted(ordinals) == list(range(len(ordinals)))
    assert len({b.block_id for b in outcome.blocks}) == len(outcome.blocks)


def test_a_span_locator_indexes_the_block_text_it_names() -> None:
    """``TEXT_SPAN`` offsets are into the block, so section 9.4 step 2 can use them."""
    outcome = parse_artifact(artifact_id=ARTIFACT, mime_type="message/rfc822", data=_hero_bytes())
    spans = [b for b in outcome.blocks if b.source_locator.kind == "TEXT_SPAN"]
    assert spans, "no TEXT_SPAN block was produced; this test measures nothing"
    for block in spans:
        start = block.source_locator.char_start
        end = block.source_locator.char_end
        assert start is not None and end is not None
        assert end - start == len(block.text), (block.block_id, start, end, len(block.text))


def test_a_quoted_reply_is_tagged_quoted_history_once_at_parse_time() -> None:
    """The tag every later stage reads instead of re-deciding.

    A promise found only inside a quoted block is not a new promise. That rule
    is only enforceable if the parser marks the block, so this asserts the
    marking rather than the rule.
    """
    raw = (
        b"Subject: Re: cancellation\r\n"
        b"From: alex@example.invalid\r\n"
        b"To: billing@northlinefiber.example\r\n"
        b"\r\n"
        b"Thanks, that matches my records.\n"
        b"\n"
        b"> We will refund USD 186.00 within 30 days.\n"
        b"> Northline Fiber billing\n"
    )
    outcome = parse_artifact(artifact_id=ARTIFACT, mime_type="message/rfc822", data=raw)
    quoted = [b for b in outcome.blocks if b.kind is ContentBlockKind.QUOTED_HISTORY]
    fresh = [b for b in outcome.blocks if b.kind is ContentBlockKind.BODY]
    assert len(quoted) == 1, [(b.kind, b.text) for b in outcome.blocks]
    assert "refund USD 186.00" in quoted[0].text
    assert len(fresh) == 1 and "matches my records" in fresh[0].text


def test_a_pdf_is_reported_unparsed_rather_than_parsed_into_nothing() -> None:
    """``UNSUPPORTED_MIME`` is a verdict. An empty block list would be a claim.

    No PDF text extractor ships in this build. Reporting ``PARSED`` with zero
    blocks would tell a run the document was empty; the run would extract
    nothing and the artifact would look processed forever.
    """
    outcome = parse_artifact(
        artifact_id=ARTIFACT, mime_type="application/pdf", data=b"%PDF-1.4\n%stub\n"
    )
    assert outcome.status is ParserStatus.UNSUPPORTED_MIME
    assert outcome.blocks == ()
    assert outcome.parser_version is None
    assert outcome.reason is not None and "pdf" in outcome.reason.lower()


def test_parsed_output_round_trips_through_the_jsonb_column() -> None:
    """What goes into ``parser_metadata`` comes back as the same blocks."""
    outcome = parse_artifact(artifact_id=ARTIFACT, mime_type="message/rfc822", data=_hero_bytes())
    stored = parser_metadata_value(outcome)
    assert isinstance(stored, dict) and stored["blocks"], stored

    content = read_normalized_content(
        artifact_id=ARTIFACT, parser_status="PARSED", parser_metadata=stored
    )
    assert isinstance(content, NormalizedContent)
    assert content.parser_version == PARSER_VERSION
    assert len(content.blocks) == len(outcome.blocks)
    for before, after in zip(outcome.blocks, content.blocks, strict=True):
        assert isinstance(after, ContentBlock)
        assert after.block_id == before.block_id
        assert after.text == before.text
        assert after.kind is before.kind
        assert after.content_sha256 == before.content_sha256


def test_a_seeded_row_claiming_parsed_with_no_output_reads_as_unavailable() -> None:
    """The state every seeded artifact is actually in.

    ``scripts/seed/rows.py`` writes ``parser_status='PARSED'`` and
    ``parser_version='seed-1.0.0'`` and no ``parser_metadata``. Returning an
    empty :class:`NormalizedContent` here would tell a run the artifact has no
    content -- indistinguishable from a real empty parse, and believable enough
    that nobody investigates.
    """
    result = read_normalized_content(
        artifact_id=ARTIFACT, parser_status="PARSED", parser_metadata=None
    )
    assert isinstance(result, ParserOutputUnavailable)
    assert result.parser_status == "PARSED"
    assert result.reason_code == "PARSER_OUTPUT_UNAVAILABLE"
    assert not hasattr(result, "blocks"), "an unavailable parse must not offer a block list"


def test_an_unparsed_row_reads_as_unparsed_and_names_its_status() -> None:
    for status in ("PENDING", "PARSING", "FAILED", "UNSUPPORTED_MIME"):
        result = read_normalized_content(
            artifact_id=ARTIFACT, parser_status=status, parser_metadata=None
        )
        assert isinstance(result, ParserOutputUnavailable), status
        assert result.reason_code == "PARSE_NOT_COMPLETE", status
        assert result.parser_status == status


def test_a_real_empty_parse_is_an_empty_result_and_not_an_absence() -> None:
    """The third state. A parse that genuinely found nothing says so."""
    outcome = ParseOutcome(
        status=ParserStatus.PARSED, parser_version=PARSER_VERSION, blocks=(), reason=None
    )
    content = read_normalized_content(
        artifact_id=ARTIFACT,
        parser_status="PARSED",
        parser_metadata=parser_metadata_value(outcome),
    )
    assert isinstance(content, NormalizedContent)
    assert content.blocks == ()


def test_stored_output_that_does_not_deserialise_is_not_silently_empty() -> None:
    """Corrupt JSONB is a defect, not "no blocks"."""
    result = read_normalized_content(
        artifact_id=ARTIFACT,
        parser_status="PARSED",
        parser_metadata={"schema_version": "pv.parser_metadata/1.0", "blocks": [{"nope": 1}]},
    )
    assert isinstance(result, ParserOutputUnavailable)
    assert result.reason_code == "PARSER_OUTPUT_UNREADABLE"
