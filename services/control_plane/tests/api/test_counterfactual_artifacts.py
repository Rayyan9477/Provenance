"""The counterfactual's artifact source: right bytes, or a refusal that says why.

Authority
---------
- ``services/control_plane/app/counterfactual/artifacts.py``.
- ``CANONICAL_DECISIONS.md`` -> *Hero artifact bytes*: "One location:
  ``demo/artifacts/``."

Why the digest is the lookup key, and why that is worth a test
----------------------------------------------------------------
``source_artifacts.s3_key`` is never projected by the repositories, so a source
that resolved bytes by filename would be reconstructing a path from a
convention. Resolving by ``content_sha256`` means the file whose bytes hash to
the recorded digest **is** the artifact -- and it means a file that has been
edited since the row was written cannot be substituted for it, which is the
property ``artifact_sha256`` in section 8.31's parity block rests on.

The refusals are the interesting half
--------------------------------------
``D-00-005``. A PDF with no parser, a digest no local file matches, and bytes
that are not UTF-8 are three different absences, and each raises with its own
reason rather than returning an empty block tuple. An empty tuple would reach
``bind_memory``, which would report ``CANNOT_RUN`` -- correctly, but naming
nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from provenance_domain.enums import ContentBlockKind
from services.control_plane.app.counterfactual.artifacts import (
    ARTIFACTS_ROOT,
    ArtifactBytesUnavailableError,
    LocalArtifactSource,
    _digest_index,
)

pytestmark = pytest.mark.unit

ARTIFACT = uuid.UUID("efd261e6-1a78-5cca-8c90-2d3579cc385a")

EMAIL = (
    "Message-ID: <x@example.invalid>\n"
    "From: billing@example.invalid\n"
    "Subject: Final invoice\n"
    "\n"
    "Amount due USD 74.20 by 30 June 2026.\n"
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """``_digest_index`` is cached on the root path; each test builds its own."""
    _digest_index.cache_clear()


def _root(tmp_path: Path, name: str, body: bytes) -> tuple[Path, str]:
    (tmp_path / name).write_bytes(body)
    return tmp_path, hashlib.sha256(body).hexdigest()


def test_the_bytes_are_found_by_the_digest_the_row_records(tmp_path: Path) -> None:
    root, digest = _root(tmp_path, "anything-at-all.eml", EMAIL.encode("utf-8"))
    blocks = LocalArtifactSource(root).blocks_for(
        artifact_id=ARTIFACT,
        content_sha256=digest,
        mime_type="message/rfc822",
        subject="Final invoice",
    )
    assert [block.kind for block in blocks] == [ContentBlockKind.SUBJECT, ContentBlockKind.BODY]
    assert blocks[1].text == "Amount due USD 74.20 by 30 June 2026."
    assert "Message-ID" not in blocks[1].text, "the headers reached the model as body text"


def test_the_filename_is_not_the_key(tmp_path: Path) -> None:
    """A file renamed since the row was written is still found; a file edited
    since is not. Both follow from keying on the digest rather than the path."""
    root, digest = _root(tmp_path, "renamed-since.eml", EMAIL.encode("utf-8"))
    source = LocalArtifactSource(root)
    assert source.blocks_for(
        artifact_id=ARTIFACT, content_sha256=digest, mime_type="text/plain", subject=None
    )
    (root / "renamed-since.eml").write_bytes(EMAIL.replace("74.20", "99.99").encode("utf-8"))
    _digest_index.cache_clear()
    with pytest.raises(ArtifactBytesUnavailableError) as refusal:
        source.blocks_for(
            artifact_id=ARTIFACT, content_sha256=digest, mime_type="text/plain", subject=None
        )
    assert "hashes to" in str(refusal.value)


def test_a_type_with_no_parser_is_refused_rather_than_decoded(tmp_path: Path) -> None:
    root, digest = _root(tmp_path, "lease.pdf", b"%PDF-1.7\n\xff\xfe binary")
    with pytest.raises(ArtifactBytesUnavailableError) as refusal:
        LocalArtifactSource(root).blocks_for(
            artifact_id=ARTIFACT,
            content_sha256=digest,
            mime_type="application/pdf",
            subject="Lease",
        )
    assert "application/pdf" in str(refusal.value)
    assert "internal.artifact_content" in str(refusal.value)


def test_a_digest_no_local_file_matches_names_the_missing_client(tmp_path: Path) -> None:
    with pytest.raises(ArtifactBytesUnavailableError) as refusal:
        LocalArtifactSource(tmp_path).blocks_for(
            artifact_id=ARTIFACT,
            content_sha256="0" * 64,
            mime_type="message/rfc822",
            subject=None,
        )
    assert "object-store client" in str(refusal.value)


def test_headers_with_no_body_are_refused(tmp_path: Path) -> None:
    root, digest = _root(tmp_path, "empty.eml", b"Subject: nothing\n\n\n")
    with pytest.raises(ArtifactBytesUnavailableError) as refusal:
        LocalArtifactSource(root).blocks_for(
            artifact_id=ARTIFACT, content_sha256=digest, mime_type="message/rfc822", subject=None
        )
    assert "nothing to read" in str(refusal.value)


def test_the_shipped_hero_artifacts_resolve_against_the_real_root() -> None:
    """A guard on the root itself.

    ``ARTIFACTS_ROOT`` is computed with ``parents[4]``, which is the kind of
    constant that is silently wrong the day a module moves one directory. The
    index over the real directory must find the hero corpus.
    """
    index = _digest_index(str(ARTIFACTS_ROOT))
    assert len(index) >= 30, f"{ARTIFACTS_ROOT} yielded {len(index)} files"
    invoice = ARTIFACTS_ROOT / "northline-final-invoice.eml"
    digest = hashlib.sha256(invoice.read_bytes()).hexdigest()
    blocks = LocalArtifactSource().blocks_for(
        artifact_id=ARTIFACT,
        content_sha256=digest,
        mime_type="message/rfc822",
        subject="Final invoice",
    )
    assert "USD 74.20" in blocks[-1].text
