"""Real artifact bytes for the curated corpus (``T2.8``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> Repository layout canon: "Hero artifact
  bytes. One location: ``demo/artifacts/``. Replaces ``demo_data/the_move/``
  and ``db/demo/``, both retired."
- ``docs/specs/10_DATABASE_DDL.md`` section 17.5 -- each curated item has "a
  real ``source_artifacts`` row (a real ``.eml`` or PDF in ``demo/artifacts/``)
  so the hashes, ``source_locator`` spans, and S3 keys are genuine rather than
  fabricated."

Why the bytes are generated from data in this module
----------------------------------------------------
Two properties have to hold at once: the files must be **real** (a parser can
open them, the sha256 is the sha256 of something) and the seed must be
**deterministic** (two engineers get byte-identical rows). Hand-written files
give the first and lose the second the moment someone fixes a typo without
re-running the loader; generated-at-load bytes give the second and never
produce a file a judge can open.

So the sources live here as data, :func:`materialize` writes them to
``demo/artifacts/``, and every hash the database stores is computed from the
**rendered bytes**, not read back off disk. ``test_seed_canon.py`` then compares
the on-disk file against that in-memory rendering, which is a real assertion:
it fails if a file is hand-edited, truncated, or line-ending-mangled by a
checkout.

Line endings
------------
RFC 5322 messages use CRLF, and the files are written in binary so a Windows
checkout cannot silently rewrite them into something whose sha256 differs from
the one in the database.
"""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

__all__ = [
    "ARTIFACTS_DIR",
    "ArtifactSource",
    "S3_BUCKET",
    "materialize",
    "render",
]

#: ``demo/artifacts/`` at the repository root. ``scripts/seed/`` is two levels
#: down, so the root is ``parents[2]``.
ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "demo" / "artifacts"

#: The bucket name the seeded rows carry. Nothing is uploaded by the seed --
#: ``T13.x`` provisions the real bucket -- but ``ck_source_artifacts_s3_key_shape``
#: requires ``raw/%`` and a demo that renders a key must render a plausible one.
S3_BUCKET = "provenance-artifacts-seed"

_CRLF = "\r\n"


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    """One artifact: its headers, its body, and the file it becomes."""

    slug: str
    filename: str
    mime_type: str
    sender_name: str
    sender_address: str
    recipient_name: str
    recipient_address: str
    subject: str
    received_at: datetime
    body: str
    thread_ref: str | None = None

    @property
    def sender_domain(self) -> str:
        return self.sender_address.split("@", 1)[1]

    @property
    def message_id(self) -> str:
        return f"<{self.slug}@{self.sender_domain}>"


def _body_lines(body: str) -> list[str]:
    """Dedent a triple-quoted literal into the lines a reader would expect.

    Without this every artifact carries the source file's indentation into its
    own bytes, which is invisible in a diff and glaring in a mail client.
    """
    return textwrap.dedent(body).strip().splitlines()


def _render_eml(source: ArtifactSource) -> bytes:
    """An RFC 5322 message, CRLF-terminated, with no generated boundary.

    ``format_datetime`` is a pure function of the instant it is handed, and
    every instant in this seed is an offset from ``DEMO_ANCHOR``, so the bytes
    are identical on every machine and in every year.
    """
    headers = [
        f"Message-ID: {source.message_id}",
        f"Date: {format_datetime(source.received_at)}",
        f'From: "{source.sender_name}" <{source.sender_address}>',
        f'To: "{source.recipient_name}" <{source.recipient_address}>',
        f"Subject: {source.subject}",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
    ]
    if source.thread_ref:
        headers.append(f"References: <{source.thread_ref}>")
    body = _CRLF.join(_body_lines(source.body))
    return (_CRLF.join(headers) + _CRLF + _CRLF + body + _CRLF).encode("utf-8")


def _render_pdf(source: ArtifactSource) -> bytes:
    """A minimal, valid, single-page PDF carrying *source*'s body as text.

    Written by hand rather than with a library for one reason: a PDF writer
    stamps a creation date and a producer string, both of which change between
    runs and between versions, and a seed whose artifact hashes move is a seed
    whose ``uq_source_artifacts_content`` collisions move with it.
    """
    lines = [source.subject, "", *_body_lines(source.body)]
    text_ops = ["BT", "/F1 11 Tf", "54 760 Td", "14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        text_ops.append(f"({escaped}) Tj T*")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def render(source: ArtifactSource) -> bytes:
    """The artifact's bytes, as they are hashed and as they hit disk."""
    if source.mime_type == "application/pdf":
        return _render_pdf(source)
    return _render_eml(source)


def content_sha256(source: ArtifactSource) -> bytes:
    return hashlib.sha256(render(source)).digest()


def s3_key(source: ArtifactSource, tenant_slug: str, user_slug: str) -> str:
    """``raw/{tenant}/{user}/{slug}`` -- ``ck_source_artifacts_s3_key_shape``."""
    return f"raw/{tenant_slug}/{user_slug}/{source.filename}"


def materialize(sources: tuple[ArtifactSource, ...]) -> list[Path]:
    """Write every artifact to ``demo/artifacts/``, in binary. Idempotent."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sources:
        path = ARTIFACTS_DIR / source.filename
        payload = render(source)
        if not path.is_file() or path.read_bytes() != payload:
            path.write_bytes(payload)
        written.append(path)
    return written
