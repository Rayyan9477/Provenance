"""Section 9.4 steps 1 and 2: the deterministic defence against an invented quotation.

Authority
---------
- ``specs/15_API_SPEC.md`` section 9.4. Step 1: "Every ``block_id`` must exist
  in the bound artifact's parsed blocks -> else ``422
  PROPOSAL_FOREIGN_PROVENANCE`` with ``details.unknown_block_ids[]``". Step 2:
  "``exact_text`` must be a substring of the cited block after whitespace
  normalisation -> else ``422 VALIDATION_FAILED`` with ``reason:
  'SPAN_NOT_IN_BLOCK'``. **This is the deterministic defence against a model
  inventing a quotation.**"
- ``specs/10_DATABASE_DDL.md`` section 12 write rule ``W4`` -- the app holds
  ``INSERT`` on ``evidence_items`` and deliberately not ``UPDATE``.
- ``CANONICAL_DECISIONS.md`` -> *Evidence lifecycle*: the table is append-only,
  so a row admitted on a guard that did not run cannot be corrected in place.

The test this module exists for
--------------------------------
``test_evidence_is_refused_when_the_artifact_has_no_readable_blocks``. Admitting
evidence while unable to run the guard is ``D-00-005`` inverted -- performing
the action *because* its check could not be performed. Every seeded artifact is
in exactly that state (``parser_status='PARSED'``, no stored parser output), so
this is the ordinary path and not an edge case.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from provenance_contracts.ingestion import ContentBlock, NormalizedContent, SourceLocator
from provenance_domain.enums import ArtifactSourceType, ContentBlockKind, SourceClass
from services.control_plane.app.ingestion.blocks import ParserOutputUnavailable
from services.control_plane.app.ingestion.evidence import (
    EVIDENCE_INSERT_SQL,
    EvidenceRefusedError,
    admissions,
    insert_params,
    normalise_span,
    source_class_for,
)

pytestmark = pytest.mark.unit

ARTIFACT = uuid.UUID("018f9e80-0000-7000-8000-000000000001")
TENANT = uuid.UUID("018f7a00-0000-7000-8000-000000000001")
USER = uuid.UUID("018f7a01-0000-7000-8000-000000000001")
OBSERVED = datetime(2026, 6, 14, 8, 0, tzinfo=UTC)

BODY = (
    "Invoice for internet service on account NF-4471-8802 covering\n"
    "1 June 2026 through 30 June 2026. Amount due USD 186.00 by 30 June 2026."
)


def _block(block_id: str, text: str, ordinal: int = 0) -> ContentBlock:
    return ContentBlock(
        block_id=block_id,
        artifact_id=ARTIFACT,
        ordinal=ordinal,
        kind=ContentBlockKind.BODY,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id=block_id, char_start=0, char_end=len(text)
        ),
    )


def _content(*blocks: ContentBlock) -> NormalizedContent:
    return NormalizedContent(
        artifact_id=ARTIFACT, parser_version="pv-eml-1.0.0", blocks=tuple(blocks)
    )


class _Candidate:
    """The shape ``RegisterEvidenceRequest.candidates[]`` has, minus pydantic.

    Written by hand rather than imported so this suite exercises the *checks*
    and not the request schema, which has its own tests. A fixture and the type
    it mirrors, written by the same hand, agree with each other and prove
    nothing -- so the fields here are named from section 9.4's printed body.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.client_ref = kwargs.pop("client_ref", "c1")
        self.evidence_type = kwargs.pop("evidence_type", "INVOICE_LINE")
        self.block_id = kwargs.pop("block_id", "blk_0001")
        self.exact_text = kwargs.pop("exact_text", "Amount due USD 186.00")
        self.normalized_text = kwargs.pop("normalized_text", "[amount=186.00 USD] invoice line")
        self.source_locator = kwargs.pop("source_locator", None)
        self.actor_ref = kwargs.pop("actor_ref", "billing@northlinefiber.example")
        self.valid_from = kwargs.pop("valid_from", None)
        self.valid_to = kwargs.pop("valid_to", None)
        self.observed_at = kwargs.pop("observed_at", OBSERVED)
        self.extraction_confidence = kwargs.pop("extraction_confidence", Decimal("0.97"))
        assert not kwargs, kwargs


# ---------------------------------------------------------------------------
# Step 1 -- the block must exist
# ---------------------------------------------------------------------------


def test_a_candidate_citing_a_block_the_artifact_does_not_have_is_refused() -> None:
    """Step 1, and the refusal names every unknown id rather than the first."""
    content = _content(_block("blk_0001", BODY))
    with pytest.raises(EvidenceRefusedError) as exc:
        admissions(
            candidates=[
                _Candidate(client_ref="c1", block_id="blk_0001"),
                _Candidate(client_ref="c2", block_id="blk_0009"),
                _Candidate(client_ref="c3", block_id="b2"),
            ],
            content=content,
        )
    assert exc.value.reason_code == "PROPOSAL_FOREIGN_PROVENANCE"
    assert sorted(exc.value.details["unknown_block_ids"]) == ["b2", "blk_0009"]


def test_a_candidate_citing_a_block_the_artifact_has_is_admitted() -> None:
    """The vacuity guard on the test above: the check has to pass sometimes."""
    content = _content(_block("blk_0001", BODY))
    admitted = admissions(candidates=[_Candidate()], content=content)
    assert [a.client_ref for a in admitted] == ["c1"]
    assert admitted[0].block.block_id == "blk_0001"


# ---------------------------------------------------------------------------
# Step 2 -- the span must be inside the block
# ---------------------------------------------------------------------------


def test_an_invented_quotation_is_refused_even_when_the_block_is_real() -> None:
    """The defence, in the shape a model actually breaks it.

    The text below is plausible, well-formed, cites a block that exists and
    concerns the right invoice. It is simply not in the document.
    """
    content = _content(_block("blk_0001", BODY))
    with pytest.raises(EvidenceRefusedError) as exc:
        admissions(
            candidates=[
                _Candidate(exact_text="Amount due USD 286.00", client_ref="c9"),
            ],
            content=content,
        )
    assert exc.value.reason_code == "VALIDATION_FAILED"
    assert exc.value.details["reason"] == "SPAN_NOT_IN_BLOCK"
    assert exc.value.details["client_ref"] == "c9"
    assert exc.value.details["block_id"] == "blk_0001"


def test_a_quotation_differing_only_in_whitespace_is_admitted() -> None:
    """Section 9.4 step 2 normalises whitespace, and only whitespace.

    A model that reflows a line break into a space has still quoted the
    document. One that changes a digit has not.
    """
    content = _content(_block("blk_0001", BODY))
    admitted = admissions(
        candidates=[
            _Candidate(exact_text="covering 1 June 2026 through 30 June 2026."),
        ],
        content=content,
    )
    assert len(admitted) == 1
    # ...and the stored `exact_text` is what the caller sent, not the
    # normalised form: the row records the quotation as it was offered.
    assert admitted[0].exact_text == "covering 1 June 2026 through 30 June 2026."


def test_normalise_span_collapses_whitespace_and_nothing_else() -> None:
    assert normalise_span("a \n b\t\tc ") == "a b c"
    assert normalise_span("USD 186.00") == "USD 186.00"
    assert normalise_span("USD  186.00") != "USD186.00", (
        "collapsing whitespace to nothing would admit 'USD186.00' as a quotation "
        "of 'USD 186.00', and then admit almost anything"
    )


def test_the_span_check_is_scoped_to_the_cited_block_not_the_whole_artifact() -> None:
    """Citing block A for text that appears only in block B is a false locator.

    It is the failure that matters most in a forwarded message: the quoted
    history block holds the old promise, and attributing it to the new body
    block is exactly how a four-month-old promise is admitted as new.
    """
    content = _content(
        _block("blk_0001", "Thanks, that matches my records.", ordinal=0),
        _block("blk_0002", "> We will refund USD 186.00 within 30 days.", ordinal=1),
    )
    with pytest.raises(EvidenceRefusedError) as exc:
        admissions(
            candidates=[
                _Candidate(block_id="blk_0001", exact_text="We will refund USD 186.00"),
            ],
            content=content,
        )
    assert exc.value.details["reason"] == "SPAN_NOT_IN_BLOCK"
    # ...and the same text against the block that holds it is admitted.
    assert (
        len(
            admissions(
                candidates=[
                    _Candidate(block_id="blk_0002", exact_text="We will refund USD 186.00"),
                ],
                content=content,
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# The guard that cannot run
# ---------------------------------------------------------------------------


def test_evidence_is_refused_when_the_artifact_has_no_readable_blocks() -> None:
    """``D-00-005`` inverted: the action performed *because* its check could not be.

    ``evidence_items`` is append-only. A row admitted without the span check
    cannot be corrected in place, and nothing downstream can tell it apart from
    one that passed.
    """
    unavailable = ParserOutputUnavailable(
        reason_code="PARSER_OUTPUT_UNAVAILABLE",
        parser_status="PARSED",
        detail="the row claims PARSED and no blocks are stored",
    )
    with pytest.raises(EvidenceRefusedError) as exc:
        admissions(candidates=[_Candidate()], content=unavailable)
    assert exc.value.reason_code == "PROVENANCE_UNCHECKABLE"
    assert exc.value.details["parser_reason_code"] == "PARSER_OUTPUT_UNAVAILABLE"
    assert "PARSER_OUTPUT_UNAVAILABLE" in str(exc.value)


def test_no_candidates_is_an_empty_admission_and_not_a_refusal() -> None:
    """Zero is a real answer here: a run that extracted nothing extracted nothing."""
    assert admissions(candidates=[], content=_content(_block("blk_0001", BODY))) == ()


# ---------------------------------------------------------------------------
# Steps 3 and 4 -- what the server assigns, and what it refuses to invent
# ---------------------------------------------------------------------------


def test_the_source_class_is_derived_from_the_artifact_and_never_from_the_caller() -> None:
    """Step 3. The request schema has no field for it and this is why.

    A spoofed sender is meaningful evidence and is not rejected (section 9.1
    step 3); it lowers the class instead, which is what "lower the artifact's
    source authority band" means in a value the Kernel can read.
    """
    passing = {"spf": "PASS", "dkim": "PASS", "dmarc": "PASS"}
    failing = {**passing, "dmarc": "FAIL"}

    assert (
        source_class_for(ArtifactSourceType.EMAIL_INBOUND, ses_verdicts=passing)
        is SourceClass.PROVIDER_SYSTEM_NOTICE
    )
    assert (
        source_class_for(ArtifactSourceType.EMAIL_INBOUND, ses_verdicts=failing)
        is SourceClass.USER_STATEMENT
    ), "a failed DMARC must not keep provider authority"
    assert (
        source_class_for(ArtifactSourceType.USER_CORRECTION, ses_verdicts=None)
        is SourceClass.USER_CORRECTION
    )
    assert (
        source_class_for(ArtifactSourceType.UPLOAD_PDF, ses_verdicts=None)
        is SourceClass.USER_UPLOADED_RECEIPT
    )


def test_the_row_carries_no_authority_score_and_records_why() -> None:
    """Step 3, and the one place this build declines to produce a number.

    ``provenance_domain.authority.authority_for`` is a
    ``(predicate family, source class)`` grid, and an evidence item has no
    predicate -- the *claim* does. Writing one number would be
    ``authority_from_confidence``'s named mistake wearing a different hat: a
    score invented at a point where the input it needs does not exist. The
    column is nullable, the derived class is recorded, and the response says so.
    """
    content = _content(_block("blk_0001", BODY))
    # A hostile locator: it names another block and claims a class the server
    # did not derive. Both must lose. A candidate with `source_locator=None`
    # would make the merge order below unfalsifiable -- there would be nothing
    # to overwrite the server's values with, and swapping the merge direction
    # would pass.
    admitted = admissions(
        candidates=[
            _Candidate(
                source_locator={
                    "block_id": "blk_9999",
                    "source_class": "SIGNED_AGREEMENT",
                    "page": 4,
                }
            )
        ],
        content=content,
    )
    params = insert_params(
        admitted[0],
        evidence_id=uuid.uuid4(),
        tenant_id=TENANT,
        user_id=USER,
        artifact_id=ARTIFACT,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        created_at=OBSERVED,
    )
    assert params["source_authority"] is None
    assert params["embedding"] is None
    assert params["embedding_model"] is None
    assert params["embedding_version"] is None
    assert params["retraction_status"] == "ACTIVE"
    assert (
        params["normalized_text_sha256"]
        == hashlib.sha256(admitted[0].normalized_text.encode("utf-8")).digest()
    )
    assert len(params["normalized_text_sha256"]) == 32, "ck_evidence_text_sha_len is length 32"
    locator = params["source_locator"].obj  # psycopg Jsonb wrapper
    assert locator["block_id"] == "blk_0001", "the caller's block id overwrote the server's"
    assert locator["source_class"] == "PROVIDER_SYSTEM_NOTICE", (
        "the caller's claimed source class overwrote the server-derived one, which is "
        "section 9.4 step 3's whole point"
    )
    assert locator["block_sha256"] == content.blocks[0].content_sha256
    # The caller's own fields survive where they do not collide.
    assert locator["page"] == 4


def test_the_insert_names_only_columns_the_applied_table_has() -> None:
    """A statement that names a column the table lacks fails at the database.

    Checked against the migration rather than against a list here, so the day a
    column is renamed this fails instead of the first live INSERT.
    """
    import re
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[4]
        / "db"
        / "migrations"
        / "versions"
        / "0002_evidence_plane.py"
    ).read_text(encoding="utf-8")
    ddl = migration.split("CREATE TABLE evidence_items", 1)[1]
    declared = set(
        re.findall(
            r"^\s{4}(\w+)\s+(?:UUID|STRING|JSONB|BYTES|TIMESTAMPTZ|DECIMAL|BOOL|VECTOR)", ddl, re.M
        )
    )
    assert len(declared) > 15, sorted(declared)

    columns = re.search(r"INSERT INTO evidence_items\s*\(([^)]*)\)", EVIDENCE_INSERT_SQL, re.S)
    assert columns is not None, EVIDENCE_INSERT_SQL
    named = {part.strip() for part in columns.group(1).split(",") if part.strip()}
    assert named, "the INSERT names no columns"
    assert named <= declared, sorted(named - declared)
    # `is_retrieval_eligible` is a generated STORED column; writing it is an error.
    assert "is_retrieval_eligible" not in named
