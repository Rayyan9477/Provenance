"""The artifact bytes the counterfactual reads, found by their own digest.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero artifact bytes*: "One location:
  ``demo/artifacts/``."
- ``docs/specs/15_API_SPEC.md`` section 8.31 -- ``artifact_sha256`` is a parity
  field, so the identity of the bytes both sides read is already a first-class
  part of this endpoint's contract.

What this is, and what it deliberately is not
----------------------------------------------
It is a **read-only, local-mode** source of the bytes a ``source_artifacts``
row already describes. It is **not** the object-store client
``write.upload_intent`` and ``internal.ingest_artifact`` are waiting on: there
is no bucket, no pre-signed PUT, no ``HeadObject``, no upload of any kind, and
nothing here can create a ``source_artifacts`` row. It is also **not**
``internal.artifact_content``: section 9.3 hands a graph parser output with
stable offsets that span citations are validated against, and this produces no
such thing -- ``draft_reading`` cites no spans, and offering these blocks to a
node that did would be worse than offering nothing.

Found by digest, not by path
-----------------------------
``source_artifacts.s3_key`` is deliberately never projected by
``provenance_db.repositories.artifacts`` -- "returning the key would put an
object path into a browser payload". So the lookup is by
``content_sha256``: the file whose bytes hash to the digest the database
recorded **is** the artifact, and a file that hashes to something else is not
it. That makes "these are the right bytes" a computation rather than a
convention about filenames, and it is the same digest that lands in the parity
block.

Absence is not emptiness
-------------------------
``D-00-005``. Every failure below raises :class:`ArtifactBytesUnavailableError`
carrying the reason. Nothing here returns an empty block tuple: a reading
produced from no document would describe the prompt.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final

from agents.runtime.prompts.render import content_block
from provenance_contracts.ingestion import ContentBlock, SourceLocator
from provenance_domain.enums import ContentBlockKind

__all__ = [
    "ARTIFACTS_ROOT",
    "READABLE_MIME_TYPES",
    "ArtifactBytesUnavailableError",
    "LocalArtifactSource",
]

#: ``services/control_plane/app/counterfactual/`` -> repository root.
ARTIFACTS_ROOT: Final[Path] = Path(__file__).resolve().parents[4] / "demo" / "artifacts"

#: The types this source can turn into text. A PDF is refused rather than
#: decoded into replacement characters: ``harborview-lease-deposit-clause.pdf``
#: would otherwise reach a model as several kilobytes of ``�`` and produce
#: a confident reading of nothing.
READABLE_MIME_TYPES: Final[frozenset[str]] = frozenset({"message/rfc822", "text/plain"})


class ArtifactBytesUnavailableError(RuntimeError):
    """The artifact's bytes could not be read, and this says why."""


@lru_cache(maxsize=1)
def _digest_index(root: str) -> Mapping[str, str]:
    """``sha256`` -> file path, over every file under *root*.

    Cached because the digest of a file on disk does not change under a running
    process, and because the alternative -- hashing thirty files per request --
    is a cost paid on the demo's most latency-visible endpoint.
    """
    index: dict[str, str] = {}
    directory = Path(root)
    if not directory.is_dir():
        return index
    for path in sorted(directory.iterdir()):
        if path.is_file():
            index[hashlib.sha256(path.read_bytes()).hexdigest()] = str(path)
    return index


class LocalArtifactSource:
    """Bytes for an artifact row, located by the digest that row records."""

    __slots__ = ("_root",)

    def __init__(self, root: Path = ARTIFACTS_ROOT) -> None:
        self._root = root

    def blocks_for(
        self,
        *,
        artifact_id: uuid.UUID,
        content_sha256: str,
        mime_type: str,
        subject: str | None,
    ) -> tuple[ContentBlock, ...]:
        """The artifact as fenced content blocks, or a refusal that names why."""
        if mime_type not in READABLE_MIME_TYPES:
            raise ArtifactBytesUnavailableError(
                f"artifact {artifact_id} is {mime_type}; this source reads "
                f"{sorted(READABLE_MIME_TYPES)} only, and there is no parser in this build "
                "(internal.artifact_content names the same gap). Decoding it anyway would "
                "hand the model replacement characters and get a confident reading of them"
            )
        index = _digest_index(str(self._root))
        path = index.get(content_sha256.lower())
        if path is None:
            raise ArtifactBytesUnavailableError(
                f"no file under {self._root} hashes to {content_sha256[:12]}..., which is "
                f"the content_sha256 source_artifacts records for {artifact_id}. The bytes "
                "for this artifact are not on this machine; there is no object-store client "
                "in the control plane to fetch them from (write.upload_intent names it)"
            )
        raw = Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact {artifact_id} is not valid UTF-8 ({exc.reason} at byte {exc.start})"
            ) from exc
        return _fence(artifact_id=artifact_id, text=text, subject=subject)


def _fence(*, artifact_id: uuid.UUID, text: str, subject: str | None) -> tuple[ContentBlock, ...]:
    """Subject and body, two blocks.

    Two rather than one because ``QUOTED_HISTORY`` and ``SUBJECT`` are the two
    distinctions the fence header carries, and rather than many because this is
    not the parser: a block per paragraph would imply an offset model that
    nothing here validates against.
    """
    _headers, separator, body = text.partition("\r\n\r\n" if "\r\n\r\n" in text else "\n\n")
    if not separator:
        body = text
    body = body.replace("\r\n", "\n").strip()
    if not body:
        raise ArtifactBytesUnavailableError(
            f"artifact {artifact_id} decoded to headers and no body; there is nothing to read"
        )

    blocks: list[ContentBlock] = []
    ordinal = 0
    if subject:
        blocks.append(
            content_block(
                artifact_id=artifact_id,
                block_id="blk_0001",
                ordinal=ordinal,
                kind=ContentBlockKind.SUBJECT,
                text=subject,
                source_locator=SourceLocator(kind="EMAIL_PART", block_id="blk_0001", mime_part="0"),
            )
        )
        ordinal += 1
    block_id = f"blk_{ordinal + 1:04d}"
    blocks.append(
        content_block(
            artifact_id=artifact_id,
            block_id=block_id,
            ordinal=ordinal,
            kind=ContentBlockKind.BODY,
            text=body,
            source_locator=SourceLocator(
                kind="TEXT_SPAN", block_id=block_id, char_start=0, char_end=len(body)
            ),
        )
    )
    return tuple(blocks)
