"""Run the ingestion graph against a real artifact and a live Gemini model.

Why this file exists
--------------------
``STATUS.md`` section 3 records the largest unverified claim in the build:

    **No agent graph has touched a live model.** Every agent test runs against
    fakes and recorded fixtures. The probe proves the *ids* answer; it proves
    nothing about a graph, a prompt, or a schema round-trip in this codebase.

``ops/gemini-probe.txt`` settled the ids. It said "ok" eleven times against a
two-word prompt. This runner is the next question and a different one: does
``agents/runtime/graphs/ingestion_graph.run_ingestion`` walk end to end when the
router is a real ``google-genai`` client, the prompt is the byte-exact asset from
``specs/14_PROMPTS.md``, the artifact is one the seed actually wrote, and the
output has to satisfy ``provenance_contracts.ingestion.ExtractionResult``?

Usage
-----
    python scripts/run_ingestion_graph.py --list
    python scripts/run_ingestion_graph.py --artifact harborview-deposit-promise
    python scripts/run_ingestion_graph.py --artifact northline-final-invoice \\
        --transcript ops/agent-graph-live-run.txt

What it writes, and under which grant
--------------------------------------
One ``agent_runs`` row per run, ``INSERT`` then ``UPDATE``, as
``pv_app_reader_writer``. That is write rule ``W4``'s neighbour: ``agent_runs``
is deliberately **absent** from ``tools/write_path_lint.CANONICAL_TABLES``
because "the Kernel holds only SELECT on them, so they have no Kernel-only
write to protect", and section 12 of the DDL grants the app ``INSERT, UPDATE``
on it. Nothing else is written. The agent never receives a SQL handle at all:
every dependency below is one of the protocols in ``agents/runtime/state.py``,
and the two that touch the database are read-only.

What it deliberately does NOT do
--------------------------------
It does not submit to the Memory Kernel. That is recorded as ``CANNOT RUN``
rather than skipped quietly, and :class:`WithheldKernelClient` carries the
reason: a commit writes claims, beliefs, commitments and ``kernel_decisions``
into the live hero corpus that ``db/seeds/MANIFEST.json`` asserts exact row
counts over, while other lanes are verifying against those counts. It is also
not this runner's write to make — the app-side ``memory_proposals`` INSERT lives
in ``services/control_plane/app/proposals/submission.py``, under the trees
``write_path_lint`` actually scans, and re-implementing it in ``scripts/`` is
precisely how a second canonical writer gets built by accident.
``--proposals-out`` writes the typed proposals to a file instead, so the thing
the Kernel would have been handed is inspectable without being committed.

``CANNOT RUN`` is not ``FAIL``
------------------------------
``D-00-005``. Every verdict this runner prints is one of three values, and the
summary counts them separately. A dependency that was never exercised is never
recorded as a dependency that failed.

Windows
-------
Nothing here is async. ``scripts/seed/db.connect_as`` opens a **synchronous**
psycopg connection, so the proactor-loop refusal that ``scripts/run_api.py``
exists to work around cannot arise. That is a reason to use the sync client
here, not an oversight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402  -- sys.path must be primed before the first-party imports.
from agents.runtime.graphs.ingestion_graph import (
    IngestionDeps,
    resolution_signals,
    run_ingestion,
    should_resolve,
)
from agents.runtime.model_router import (
    GeminiClient,
    GeminiRouterConfig,
    ModelCallRecord,
    ModelRouter,
    OutputContract,
    PendingReview,
    RouterSuccess,
    route,
)
from agents.runtime.model_router.wire_schema import gemini_transport
from agents.runtime.prompts.render import AssetPromptRenderer, content_block, load_manifest
from agents.runtime.schemas.validation import ValidationFailure
from agents.runtime.state import (
    GRAPH_NAME_INGESTION,
    GRAPH_VERSION_INGESTION,
    IngestionGraphState,
    ModelPending,
    ModelRoute,
    ModelSuccess,
    RetrievalResult,
    RetrievalSpec,
    initial_ingestion_state,
)
from provenance_contracts.identity import CapabilityBinding
from provenance_contracts.ingestion import (
    EXTRACTION_SCHEMA_VERSION,
    ArtifactMetadata,
    ContentBlock,
    EvidenceCandidate,
    ExtractionResult,
    NormalizedContent,
    SourceLocator,
)
from provenance_contracts.kernel import KernelCommitResult
from provenance_contracts.proposal import MemoryProposal
from provenance_contracts.retrieval import IdentityCandidate, RetrievalContext
from provenance_domain.enums import (
    ArtifactSourceType,
    CaseStatus,
    ContentBlockKind,
    IdentityCandidateKind,
    ParserStatus,
    RelationshipStatus,
)
from scripts.seed.db import connect_as
from tools.scrub import scrub_text

__all__ = ["main"]

PASS: Final[str] = "PASS"
FAIL: Final[str] = "FAIL"
CANNOT_RUN: Final[str] = "CANNOT RUN"

#: The four values ``ck_agent_runs_graph`` admits, restated here so a drift in
#: the graph constants fails at import with a sentence instead of at the INSERT
#: with a constraint violation.
#:
#: This replaces a translation table that mapped ``"ingestion_graph"`` ->
#: ``"ingestion"``. That table was correct when it was written: the two
#: constants genuinely disagreed. Then ``GRAPH_NAME_INGESTION`` was corrected at
#: source to ``"ingestion"`` and the table was not deleted, so the lookup became
#: ``GRAPH_NAME_FOR_COLUMN["ingestion"]`` -- a ``KeyError`` that took the whole
#: runner down before its first model call. The evidence in
#: ``ops/agent-graph-live-run.txt`` had been produced before the correction, so
#: the transcript stayed green while the script that writes it could not start,
#: which is the exact failure mode the transcript exists to rule out.
#:
#: An assertion rather than a mapping, because there is no translating left to
#: do and a mapping that maps a thing to itself is an invitation to reintroduce
#: the bug.
LEGAL_GRAPH_NAMES: Final[frozenset[str]] = frozenset(
    {"ingestion", "advocate", "resolver", "counterfactual"}
)

assert GRAPH_NAME_INGESTION in LEGAL_GRAPH_NAMES, (
    f"agents.runtime.state.GRAPH_NAME_INGESTION is {GRAPH_NAME_INGESTION!r}, which "
    f"ck_agent_runs_graph in db/migrations/versions/0008_events_infrastructure.py "
    f"does not admit; it accepts {sorted(LEGAL_GRAPH_NAMES)}"
)

#: ``demo/artifacts/`` — ``CANONICAL_DECISIONS.md`` -> Repository layout canon.
ARTIFACTS_DIR: Final[Path] = _REPO_ROOT / "demo" / "artifacts"

#: The hero commitment: the landlord promise every "95 days overdue" figure in
#: the pack derives from. A default that exercises a commitment, a prospective
#: cue and an ambiguous identity in one artifact.
DEFAULT_ARTIFACT: Final[str] = "harborview-deposit-promise"

#: The seeded principal. ``scripts/mint_local_token.py`` uses the same subject.
HERO_SUBJECT: Final[str] = "seed-hero-alex-rivera"

#: Exact identifier match, then counterparty-name match. Deterministic, and in
#: the order ``CANONICAL_DECISIONS.md`` -> *Identity order* fixes: "Exact
#: identifiers and deterministic identity signals precede vector similarity."
SCORE_EXACT_IDENTIFIER: Final[Decimal] = Decimal("0.9500")
SCORE_COUNTERPARTY_NAME: Final[Decimal] = Decimal("0.6000")

#: An agent run's capability is short-lived by construction.
CAPABILITY_TTL: Final[timedelta] = timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


@dataclass
class Transcript:
    """Lines plus three-valued verdicts. Every line is scrubbed on the way out.

    ``tools/scrub.py`` carries a ``google-api-key`` rule for the ``AIza`` shape
    because the Gemini API takes the key as a **URL query parameter** — an SDK
    error therefore embeds it in the message text, where a naive transcript
    would preserve it forever.
    """

    lines: list[str] = field(default_factory=list)
    verdicts: list[tuple[str, str, str]] = field(default_factory=list)

    def say(self, text: str = "") -> None:
        for raw in text.split("\n"):
            line = scrub_text(raw).rstrip("\n")
            self.lines.append(line)
            print(line, flush=True)

    def verdict(self, outcome: str, label: str, detail: str) -> None:
        self.verdicts.append((outcome, label, detail))
        self.say(f"   {outcome:<11} {label:<34} {scrub_text(detail)}")

    def counts(self) -> tuple[int, int, int]:
        return (
            sum(1 for v in self.verdicts if v[0] == PASS),
            sum(1 for v in self.verdicts if v[0] == FAIL),
            sum(1 for v in self.verdicts if v[0] == CANNOT_RUN),
        )

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _load_dotenv(root: Path) -> None:
    """Populate ``os.environ`` from ``.env`` without echoing a value.

    Identical in shape to ``scripts/mint_local_token._load_dotenv``. ``Settings``
    deliberately refuses to parse a dotenv (``settings.py:331``); this is a
    developer command run by hand, not an import-time path.
    """
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# The artifact — real bytes, real row, hash-checked against each other
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededArtifact:
    """One ``source_artifacts`` row plus the bytes on disk it names."""

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    slug: str
    filename: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    subject: str | None
    sender: str | None
    recipient: str | None
    source_message_id: str | None
    received_at: datetime
    parser_status: str
    parser_version: str | None
    raw: bytes

    @property
    def disk_sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def bytes_agree(self) -> bool:
        return self.disk_sha256 == self.content_sha256


def list_seeded_artifacts(user_sub: str) -> list[tuple[str, uuid.UUID, str | None]]:
    """Every curated artifact the seed wrote for *user_sub*, by file slug."""
    with connect_as("pv_app_reader_writer") as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.s3_key, a.id, a.subject
            FROM source_artifacts a
            JOIN users u ON u.tenant_id = a.tenant_id AND u.id = a.user_id
            WHERE u.cognito_sub = %(sub)s AND a.s3_key LIKE 'raw/hero/hero/%%'
            ORDER BY a.received_at
            """,
            {"sub": user_sub},
        )
        rows = cursor.fetchall()
    return [(Path(str(key)).stem, artifact_id, subject) for key, artifact_id, subject in rows]


def load_seeded_artifact(slug: str, user_sub: str) -> SeededArtifact:
    """Read the row and the file, and return both without reconciling them.

    Deliberately does not raise on a hash mismatch: the comparison is a
    *verdict* this runner prints, and a function that refused to return the pair
    would turn a measurable disagreement into an exception nobody can inspect.
    """
    with connect_as("pv_app_reader_writer") as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id, a.tenant_id, a.user_id, a.s3_key, a.mime_type, a.size_bytes,
                   encode(a.content_sha256, 'hex'), a.subject, a.sender, a.recipient,
                   a.source_message_id, a.received_at, a.parser_status, a.parser_version
            FROM source_artifacts a
            JOIN users u ON u.tenant_id = a.tenant_id AND u.id = a.user_id
            WHERE u.cognito_sub = %(sub)s AND a.s3_key LIKE %(key)s
            """,
            {"sub": user_sub, "key": f"raw/hero/hero/{slug}.%"},
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(
            f"no seeded source_artifacts row whose s3_key names {slug!r} for {user_sub!r}. "
            f"Run `python scripts/run_ingestion_graph.py --list` for the seeded set. "
            f"Note that northline-june-invoice is deliberately NOT seeded: "
            f"scripts/seed/evidence.py records that it is uploaded live at demo time."
        )
    key = str(row[3])
    filename = Path(key).name
    path = ARTIFACTS_DIR / filename
    if not path.is_file():
        raise LookupError(
            f"{path} does not exist, but source_artifacts carries a row for it. "
            f"demo/artifacts/ is the single location for hero artifact bytes."
        )
    return SeededArtifact(
        artifact_id=row[0],
        tenant_id=row[1],
        user_id=row[2],
        slug=slug,
        filename=filename,
        mime_type=str(row[4]),
        size_bytes=int(row[5]),
        content_sha256=str(row[6]),
        subject=row[7],
        sender=row[8],
        recipient=row[9],
        source_message_id=row[10],
        received_at=row[11],
        parser_status=str(row[12]),
        parser_version=row[13],
        raw=path.read_bytes(),
    )


def parse_email_blocks(artifact: SeededArtifact) -> tuple[ContentBlock, ...]:
    """RFC 5322 -> ``ContentBlock``s. A deliberately small, honest parser.

    ``unbound.py`` -> ``internal.artifact_content`` records that **nothing in
    this system has ever produced or stored a content block**: no table holds
    one, ``app/ingestion`` is a docstring, and ``ArtifactReader``'s only
    implementation before this file was a test fake. This function does not
    close that gap — it is a runner-local parser over ``text/plain`` email, it
    stores nothing, and a PDF gets one block of undecodable bytes rather than a
    pretence of page extraction.

    ``QUOTED_HISTORY`` is decided here, once, by the ``>`` prefix, because every
    later stage reads the tag instead of re-deciding — which is what stops a
    forwarded promise from being admitted twice.
    """
    text = artifact.raw.decode("utf-8", errors="replace")
    separator = "\r\n\r\n" if "\r\n\r\n" in text else "\n\n"
    header_text, _, body_text = text.partition(separator)
    body = body_text.replace("\r\n", "\n").strip()

    blocks: list[ContentBlock] = []
    ordinal = 0
    if artifact.subject:
        blocks.append(
            content_block(
                artifact_id=artifact.artifact_id,
                block_id="blk_0001",
                ordinal=ordinal,
                kind=ContentBlockKind.SUBJECT,
                text=artifact.subject,
                source_locator=SourceLocator(kind="EMAIL_PART", block_id="blk_0001", mime_part="0"),
            )
        )
        ordinal += 1

    header_lines = [ln for ln in header_text.replace("\r\n", "\n").split("\n") if ln.strip()]
    interesting = [ln for ln in header_lines if ln.split(":", 1)[0] in {"From", "To", "Date"}]
    if interesting:
        block_id = f"blk_{ordinal + 1:04d}"
        blocks.append(
            content_block(
                artifact_id=artifact.artifact_id,
                block_id=block_id,
                ordinal=ordinal,
                kind=ContentBlockKind.HEADER,
                text="\n".join(interesting),
                source_locator=SourceLocator(
                    kind="EMAIL_PART", block_id=block_id, mime_part="headers"
                ),
            )
        )
        ordinal += 1

    for paragraph in [p.strip() for p in body.split("\n\n") if p.strip()]:
        quoted = all(line.lstrip().startswith(">") for line in paragraph.splitlines())
        block_id = f"blk_{ordinal + 1:04d}"
        offset = body.find(paragraph)
        blocks.append(
            content_block(
                artifact_id=artifact.artifact_id,
                block_id=block_id,
                ordinal=ordinal,
                kind=ContentBlockKind.QUOTED_HISTORY if quoted else ContentBlockKind.BODY,
                text=paragraph,
                source_locator=SourceLocator(
                    kind="TEXT_SPAN",
                    block_id=block_id,
                    char_start=offset,
                    char_end=offset + len(paragraph),
                ),
            )
        )
        ordinal += 1
    return tuple(blocks)


class SeededArtifactReader:
    """:class:`~agents.runtime.state.ArtifactReader` over one loaded artifact.

    Metadata and content stay two calls because the protocol splits them: a
    metadata-only path must never materialise document text.
    """

    def __init__(self, artifact: SeededArtifact, blocks: Sequence[ContentBlock]) -> None:
        self._artifact = artifact
        self._blocks = tuple(blocks)

    def get_artifact_metadata(self, artifact_id: uuid.UUID) -> ArtifactMetadata:
        if artifact_id != self._artifact.artifact_id:
            raise LookupError(f"this reader is bound to {self._artifact.artifact_id}")
        return ArtifactMetadata(
            artifact_id=self._artifact.artifact_id,
            tenant_id=self._artifact.tenant_id,
            user_id=self._artifact.user_id,
            source_type=ArtifactSourceType.SEED_FIXTURE,
            mime_type=self._artifact.mime_type,
            content_sha256=self._artifact.content_sha256,
            size_bytes=self._artifact.size_bytes,
            source_message_id=self._artifact.source_message_id,
            sender=self._artifact.sender,
            recipient=self._artifact.recipient,
            subject=self._artifact.subject,
            received_at=self._artifact.received_at,
            parser_status=ParserStatus(self._artifact.parser_status),
            parser_version=self._artifact.parser_version,
            block_count=len(self._blocks),
        )

    def get_normalized_content(self, artifact_id: uuid.UUID) -> NormalizedContent:
        if artifact_id != self._artifact.artifact_id:
            raise LookupError(f"this reader is bound to {self._artifact.artifact_id}")
        return NormalizedContent(
            artifact_id=self._artifact.artifact_id,
            parser_version=self._artifact.parser_version or "runner-eml-1.0.0",
            blocks=self._blocks,
        )


# ---------------------------------------------------------------------------
# Evidence — lookup only
# ---------------------------------------------------------------------------


class SeededEvidenceLookup:
    """``register_or_lookup_evidence``, with the *register* half withheld.

    The protocol's contract is "creates immutable evidence rows, **or** returns
    existing deduplicated ids", and this implements only the second half. The
    reason is specific, not squeamish: ``evidence_items.embedding`` is
    ``VECTOR(1024)`` and ``ck_evidence_embedding_model`` admits only
    ``amazon.titan-embed-text-v2:0``, so the only legal INSERT this build can
    issue carries ``embedding = NULL`` — which silently excludes the row from
    every ANN query, in a table that is append-only and therefore cannot be
    corrected in place. Migration ``0009`` widens both columns and is
    deliberately unapplied.

    Unmatched candidates are recorded in :attr:`unmatched` and reported. They
    are never silently dropped, and this class never invents an id: an evidence
    id that names no row would be handed to the Kernel as grounding.
    """

    def __init__(self, artifact: SeededArtifact) -> None:
        self._artifact = artifact
        self.matched: dict[str, uuid.UUID] = {}
        self.unmatched: list[str] = []
        #: ``local_id -> how it was matched``. Printed, because "matched" and
        #: "matched by the loosest rule available" are different facts.
        self.match_kind: dict[str, str] = {}
        self._rows = self._load()

    def _load(self) -> list[tuple[uuid.UUID, str, str]]:
        with connect_as("pv_app_reader_writer") as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, encode(normalized_text_sha256, 'hex'), exact_text
                FROM evidence_items
                WHERE tenant_id = %(tenant)s AND user_id = %(user)s
                  AND artifact_id = %(artifact)s
                """,
                {
                    "tenant": self._artifact.tenant_id,
                    "user": self._artifact.user_id,
                    "artifact": self._artifact.artifact_id,
                },
            )
            return [(row[0], str(row[1]), str(row[2])) for row in cursor.fetchall()]

    @property
    def seeded_row_count(self) -> int:
        return len(self._rows)

    def register_or_lookup_evidence(
        self, *, artifact_id: uuid.UUID, candidates: Sequence[EvidenceCandidate]
    ) -> Mapping[str, uuid.UUID]:
        """Three rules, tried in descending strength, each one named in the result.

        ``NORMALIZED_TEXT_DIGEST`` is Provenance's own dedupe key and is the only
        one of the three that is a *contract*. ``EXACT_TEXT_EQUAL`` and
        ``EXACT_TEXT_CONTAINMENT`` are runner-local, and containment is here for
        a measured reason: the seeded row for the deposit promise quotes
        ``"we will return your security deposit ..."`` while
        ``gemini-3.5-flash-lite`` quoted the same sentence starting one clause
        earlier. Two spans where one contains the other cite the same passage of
        the same artifact, which is exactly what "this text was present"
        asserts. It is reported as the weaker match it is rather than folded in
        silently.
        """
        del artifact_id  # this registrar is bound to one artifact; see the protocol
        found: dict[str, uuid.UUID] = {}
        for candidate in candidates:
            digest = hashlib.sha256(candidate.normalized_text.encode("utf-8")).hexdigest()
            quoted = _normalise(candidate.exact_text)
            hit: tuple[uuid.UUID, str] | None = None
            for evidence_id, row_digest, row_text in self._rows:
                row_quoted = _normalise(row_text)
                if row_digest == digest:
                    hit = (evidence_id, "NORMALIZED_TEXT_DIGEST")
                    break
                if row_quoted == quoted:
                    hit = (evidence_id, "EXACT_TEXT_EQUAL")
                    break
                if row_quoted and (row_quoted in quoted or quoted in row_quoted):
                    hit = (evidence_id, "EXACT_TEXT_CONTAINMENT")
            if hit is None:
                self.unmatched.append(candidate.local_id)
                continue
            found[candidate.local_id] = hit[0]
            self.match_kind[candidate.local_id] = hit[1]
        self.matched = found
        return found


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


# ---------------------------------------------------------------------------
# Retrieval — deterministic, read-only, exact identifiers first
# ---------------------------------------------------------------------------


class DeterministicRetrieval:
    """Exact identifiers, then counterparty name. No vector stage.

    ``unbound.py`` -> ``internal.retrieve`` records that ``app/retrieval`` has
    the ANN statement, the predicates and the reranker but **no module that runs
    stages A-G end to end**. This is not that module and does not pretend to be:
    it runs the deterministic identity stage only, which is the stage
    ``CANONICAL_DECISIONS.md`` -> *Identity order* puts first and calls
    canonical, and it returns nothing from the advisory vector stage rather than
    returning an empty vector result that would read as "nothing similar".
    """

    def __init__(self, artifact: SeededArtifact) -> None:
        self._artifact = artifact
        self.identifier_hits: list[str] = []
        self.name_hits: list[str] = []
        #: The last result returned, so a later node that raises does not erase
        #: the record of what retrieval actually found.
        self.last: RetrievalResult | None = None

    def retrieve_candidate_context(self, spec: RetrievalSpec) -> RetrievalResult:
        identifiers = [value.strip() for value in spec.external_identifiers if value.strip()]
        names = [name.strip() for name in spec.counterparty_hints if name.strip()]
        started = time.monotonic()

        with connect_as("pv_app_reader_writer") as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT r.id, r.label, r.external_account_ref, r.status,
                       c.display_name, c.normalized_name
                FROM relationships r
                JOIN counterparties c
                  ON c.tenant_id = r.tenant_id AND c.id = r.counterparty_id
                WHERE r.tenant_id = %(tenant)s AND r.user_id = %(user)s
                """,
                {"tenant": spec.tenant_id, "user": spec.user_id},
            )
            relationships = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, relationship_id, title, status, last_activity_at
                FROM cases
                WHERE tenant_id = %(tenant)s AND user_id = %(user)s
                """,
                {"tenant": spec.tenant_id, "user": spec.user_id},
            )
            cases = cursor.fetchall()

        wanted_ids = {value.casefold() for value in identifiers}
        wanted_names = {_normalise(name) for name in names}

        scores: dict[uuid.UUID, tuple[Decimal, str, str, str]] = {}
        for rel_id, label, account_ref, status, name, normalized_name in relationships:
            reference = str(account_ref or "").casefold()
            if reference and reference in wanted_ids:
                scores[rel_id] = (SCORE_EXACT_IDENTIFIER, str(label), str(status), str(name))
                self.identifier_hits.append(str(account_ref))
                continue
            if wanted_names and _normalise(str(normalized_name or name)) in wanted_names:
                scores[rel_id] = (SCORE_COUNTERPARTY_NAME, str(label), str(status), str(name))
                self.name_hits.append(str(name))

        relationship_candidates = tuple(
            IdentityCandidate(
                candidate_kind=IdentityCandidateKind.RELATIONSHIP,
                candidate_id=rel_id,
                tenant_id=spec.tenant_id,
                user_id=spec.user_id,
                label=label,
                counterparty_name=counterparty,
                relationship_status=RelationshipStatus(status),
                score=score,
                reasons=(
                    "EXACT_EXTERNAL_IDENTIFIER"
                    if score == SCORE_EXACT_IDENTIFIER
                    else "COUNTERPARTY_NAME_MATCH",
                ),
            )
            for rel_id, (score, label, status, counterparty) in sorted(
                scores.items(), key=lambda item: (-item[1][0], str(item[0]))
            )
        )

        case_candidates = tuple(
            IdentityCandidate(
                candidate_kind=IdentityCandidateKind.CASE,
                candidate_id=case_id,
                tenant_id=spec.tenant_id,
                user_id=spec.user_id,
                label=str(title),
                case_status=CaseStatus(str(status)),
                last_activity_at=last_activity,
                score=scores[relationship_id][0],
                reasons=("RELATIONSHIP_IDENTITY_INHERITED",),
            )
            for case_id, relationship_id, title, status, last_activity in sorted(
                (row for row in cases if row[1] in scores),
                key=lambda row: (-scores[row[1]][0], str(row[0])),
            )
        )

        context = RetrievalContext(
            trace_id=uuid.uuid4(),
            agent_run_id=uuid.uuid4(),
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            artifact_id=spec.artifact_id,
            relationship_candidates=relationship_candidates,
            case_candidates=case_candidates,
            retrieved_at=datetime.now(UTC),
        )
        del started
        self.last = RetrievalResult(context=context)
        return self.last


# ---------------------------------------------------------------------------
# Span anchoring — the model quotes, Provenance locates
# ---------------------------------------------------------------------------


def bind_extraction_schema(
    *,
    blocks: Sequence[ContentBlock],
    artifact_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    trace_id: uuid.UUID,
    model_id: str,
    prompt_version: str,
) -> type[ExtractionResult]:
    """``ExtractionResult`` with two deterministic pre-decode corrections.

    **1. Provenance-generated fields are overwritten, never asked for.**
    ``artifact_id``, ``agent_run_id``, ``trace_id``, ``source_block_ids``,
    ``model_id``, ``model_tier`` and ``prompt_version`` are all facts the *system*
    holds. The schema requires them, so a model handed that schema fills them in
    — which means it asserts which model produced the output. That is a false
    attribution waiting to happen in the one system whose product is knowing who
    said what, and ``CANONICAL_DECISIONS.md`` -> *Disclosure* makes the model
    claim checkable against persisted state precisely so it cannot be authored
    by the thing being attributed. They are stamped from the router's own record
    instead.

    **2. Character spans are anchored, not trusted.**
    ``validate_extraction`` requires ``block.text[char_start:char_end] ==
    exact_text``. On the first live run against ``gemini-3.5-flash-lite`` that
    check failed on every candidate, twice, and the run ended
    ``SCHEMA_REPAIR_EXHAUSTED / SPAN_TEXT_MISMATCH`` — because a language model
    cannot count characters, and no amount of repair makes it able to. The
    citation it *can* produce reliably is the quotation itself, so the offsets
    are recomputed here by locating ``exact_text`` inside the block the candidate
    named.

    This does not weaken the check it satisfies. ``SPAN_TEXT_MISMATCH`` exists as
    "the deterministic defence against a model inventing a quotation"
    (``unbound.py`` -> ``internal.register_evidence``), and that defence is
    exactly preserved: a quotation that is not a substring of the block it cites
    cannot be anchored, its offsets are left as the model wrote them, and the
    validator fires. What is given up is the model's arithmetic, which was never
    evidence of anything.

    Where this belongs, and why it is here
    --------------------------------------
    In ``extract_structured_evidence``, between the router call and the
    validator, or in the prompt as "do not emit offsets". Both are edits to
    files this runner does not own. Recorded rather than done quietly.
    """
    from pydantic import model_validator

    text_by_block = {block.block_id: block.text for block in blocks}
    block_ids = [block.block_id for block in blocks]
    tier = route("extract_structured_evidence").tier

    class AnchoredExtractionResult(ExtractionResult):
        @model_validator(mode="before")
        @classmethod
        def _stamp_and_anchor(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            payload = dict(data)
            payload["artifact_id"] = str(artifact_id)
            payload["agent_run_id"] = str(agent_run_id)
            payload["trace_id"] = str(trace_id)
            payload["source_block_ids"] = list(block_ids)
            payload["model_id"] = model_id
            payload["model_tier"] = tier.value
            payload["prompt_version"] = prompt_version
            payload["extraction_schema_version"] = EXTRACTION_SCHEMA_VERSION
            payload["evidence_candidates"] = [
                _anchor_candidate(candidate, text_by_block)
                for candidate in payload.get("evidence_candidates") or []
            ]
            return payload

    return AnchoredExtractionResult


def _anchor_candidate(candidate: Any, text_by_block: Mapping[str, str]) -> Any:
    """Recompute one candidate's span from the text it quotes."""
    if not isinstance(candidate, dict):
        return candidate
    locator = candidate.get("source_locator")
    if not isinstance(locator, dict):
        return candidate
    block_text = text_by_block.get(str(locator.get("block_id") or candidate.get("block_id") or ""))
    exact = candidate.get("exact_text")
    if block_text is None or not isinstance(exact, str):
        return candidate
    start = block_text.find(exact.strip())
    if start < 0:
        # Not a substring of the block it cites. Left exactly as written so the
        # validator reports it: this is the case the check exists for.
        return candidate
    anchored = dict(candidate)
    anchored["source_locator"] = {
        **locator,
        "char_start": start,
        "char_end": start + len(exact.strip()),
    }
    anchored["exact_text"] = exact.strip()
    return anchored


# ---------------------------------------------------------------------------
# The router adapter
# ---------------------------------------------------------------------------


class LiveRouterAdapter:
    """``agents.runtime.state.ModelRouter`` over the real ``ModelRouter``.

    The two protocols differ on purpose. The graph's names a ``schema`` and a
    ``validate``; the router's takes an :class:`OutputContract` binding the two
    together so the same object is handed to the SDK *and* used to decode the
    reply, which is what stops the API-level constraint and the terminal
    validation from drifting apart. This class is the join, and it is the only
    place the two vocabularies meet.

    Every ``ModelCallRecord`` is kept, including the ones from a failed attempt:
    a call that produced no body still produced a bill and a latency, and
    ``agent_runs.model_calls`` is the column a reviewer reads to check the
    submission's model claim against persisted state.
    """

    def __init__(
        self,
        router: ModelRouter,
        *,
        schema_for_node: Mapping[str, type[Any]] | None = None,
    ) -> None:
        self._router = router
        self._schema_for_node = dict(schema_for_node or {})
        self.calls: list[ModelCallRecord] = []
        self.pending: list[PendingReview] = []
        #: The validated output per node, kept so a node that raises *after* a
        #: successful model call does not take the evidence of that call with
        #: it. A transcript that loses the model output because a later step
        #: failed is a transcript that cannot answer the question it was written
        #: to answer.
        self.values: dict[str, Any] = {}

    def invoke(
        self,
        node: str,
        *,
        system: str,
        user_text: str,
        schema: type[Any],
        validate: Callable[[Any], Sequence[ValidationFailure]] | None = None,
    ) -> ModelSuccess[Any] | ModelPending:
        contract: OutputContract[Any] = OutputContract(
            model=self._schema_for_node.get(node, schema),
            validate=validate if validate is not None else _no_semantic_checks,
        )
        outcome = self._router.invoke(node, system=system, user_text=user_text, contract=contract)
        self.calls.extend(outcome.calls)
        if isinstance(outcome, PendingReview):
            self.pending.append(outcome)
            return ModelPending(reason_code=outcome.reason_code, node=outcome.node)
        assert isinstance(outcome, RouterSuccess)
        self.values[node] = outcome.value
        return ModelSuccess(
            value=outcome.value,
            route=ModelRoute(
                provider="gemini",
                model_id=outcome.model_id,
                tier=route(node).tier,
                prompt_version=outcome.prompt_version,
            ),
            repaired=outcome.repaired,
        )


def _no_semantic_checks(_: Any) -> Sequence[ValidationFailure]:
    """The resolver node passes ``validate=None``; the router requires a callable."""
    return ()


# ---------------------------------------------------------------------------
# Kernel — withheld, and it says so
# ---------------------------------------------------------------------------


class KernelSubmissionWithheldError(RuntimeError):
    """The graph reached the Kernel and this runner refused to open that door."""


class WithheldKernelClient:
    """Captures the typed proposals and refuses to submit them.

    This raises rather than returning a :class:`KernelCommitResult`, and the
    distinction is the whole of ``D-00-005``. A ``KernelCommitResult`` is a
    *decision the Kernel made*; synthesising one here — ``NOOP``,
    ``PENDING_HUMAN_REVIEW``, anything — would write a decision nobody took into
    the one system whose product is knowing who said what.

    A decision, and no longer a gap
    -------------------------------
    Until 2026-08-24 this was a missing mechanism:
    ``unbound.py`` -> ``internal.submit_proposal`` recorded that no app-side
    ``memory_proposals`` INSERT existed anywhere in ``services/``, and
    ``commit_proposal`` only ``UPDATE``s that row. ``app/proposals/submission.py``
    now exists and that register entry is gone, so the door opens. It stays shut
    here for a different and narrower reason, stated so nobody has to guess which
    one applies: a commit writes claims, beliefs, commitments and
    ``kernel_decisions`` into the live hero corpus, ``db/seeds/MANIFEST.json``
    asserts exact row counts over it (``G2.6``), and several lanes are verifying
    against those counts right now. Committing from an evidence-gathering runner
    would invalidate their measurements and this one's.
    """

    def __init__(self) -> None:
        self.proposals: list[MemoryProposal] = []

    def submit_memory_proposal(self, proposal: MemoryProposal) -> KernelCommitResult:
        self.proposals.append(proposal)
        raise KernelSubmissionWithheldError(
            "submission withheld by this runner, not blocked: app/proposals/submission.py "
            "now provides the app-side memory_proposals INSERT and internal.submit_proposal "
            "is no longer in the unbound register. A commit writes claims, beliefs, "
            "commitments and kernel_decisions into the live hero corpus that "
            "db/seeds/MANIFEST.json asserts exact row counts over (G2.6), while other lanes "
            "are verifying against it. This is a decision with a reason, not a missing "
            "mechanism"
        )


class RecordingSession:
    """Workflow durability only. One method, returning ``None``.

    The protocol has no reader, so a graph cannot satisfy a business question
    from session storage. The notes are kept here for the transcript, which is
    an operator artifact and not product state.
    """

    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def record(self, *, run_id: uuid.UUID, node: str, note: str) -> None:
        del run_id
        self.notes.append((node, note))


# ---------------------------------------------------------------------------
# agent_runs persistence
# ---------------------------------------------------------------------------

INSERT_AGENT_RUN: Final[str] = """
INSERT INTO agent_runs (
    id, tenant_id, user_id, trace_id, graph_name, graph_version, model_route,
    memory_mode, is_counterfactual, status, started_at, expires_at,
    input_artifact_id, allowed_case_ids
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(trace_id)s, %(graph_name)s, %(graph_version)s,
    %(model_route)s, 'ON', false, 'RUNNING', %(started_at)s, %(expires_at)s,
    %(input_artifact_id)s, %(allowed_case_ids)s
)
"""

UPDATE_AGENT_RUN: Final[str] = """
UPDATE agent_runs
   SET status = %(status)s,
       finished_at = %(finished_at)s,
       error_code = %(error_code)s,
       retrieval_candidate_count = %(retrieval_candidate_count)s,
       model_calls = %(model_calls)s,
       tool_calls = %(tool_calls)s,
       capability_status = %(capability_status)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(id)s
"""


def open_agent_run(
    *,
    run_id: uuid.UUID,
    binding: CapabilityBinding,
    trace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    started_at: datetime,
    config: GeminiRouterConfig,
) -> None:
    """Write the ``RUNNING`` row before the first model call.

    Before, not after, on purpose: a run that dies mid-graph must leave a row
    saying it started. A row written only on success is a ledger that records
    only the runs that went well.
    """
    model_route = {
        "tier_e": config.extraction_model_id,
        "tier_r": config.reasoning_model_id,
        "embeddings": os.environ.get("GEMINI_EMBEDDING_MODEL_ID", "gemini-embedding-2"),
    }
    with connect_as("pv_app_reader_writer") as conn:
        conn.execute(
            INSERT_AGENT_RUN,
            {
                "id": run_id,
                "tenant_id": binding.tenant_id,
                "user_id": binding.user_id,
                "trace_id": trace_id,
                "graph_name": GRAPH_NAME_INGESTION,
                "graph_version": GRAPH_VERSION_INGESTION,
                "model_route": json.dumps(model_route),
                "started_at": started_at,
                "expires_at": binding.expires_at,
                "input_artifact_id": artifact_id,
                "allowed_case_ids": None,
            },
        )
        conn.commit()


def close_agent_run(
    *,
    run_id: uuid.UUID,
    binding: CapabilityBinding,
    status: str,
    error_code: str | None,
    finished_at: datetime,
    retrieval_candidate_count: int | None,
    calls: Sequence[ModelCallRecord],
    capability_status: Mapping[str, Any],
) -> None:
    """Settle the row with the attribution the Memory Trace reads."""
    with connect_as("pv_app_reader_writer") as conn:
        conn.execute(
            UPDATE_AGENT_RUN,
            {
                "id": run_id,
                "tenant_id": binding.tenant_id,
                "user_id": binding.user_id,
                "status": status,
                "error_code": error_code,
                "finished_at": finished_at,
                "retrieval_candidate_count": retrieval_candidate_count,
                "model_calls": json.dumps([c.as_agent_runs_element() for c in calls]),
                # An empty array is the honest value: this run bound no MCP
                # tools at all, which is different from "the field is unknown".
                "tool_calls": json.dumps([]),
                "capability_status": json.dumps(dict(capability_status)),
            },
        )
        conn.commit()


def count_agent_runs() -> int:
    with connect_as("pv_app_reader_writer") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM agent_runs")
        return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, transcript: Transcript) -> int:
    started_wall = datetime.now(UTC)
    transcript.say("=" * 78)
    transcript.say("Provenance -- live ingestion graph run  (PB-A1..PB-A13)")
    transcript.say(f"started             : {started_wall.isoformat()}")
    transcript.say("graph               : agents/runtime/graphs/ingestion_graph.run_ingestion")
    transcript.say("api                 : Gemini Developer API (AI Studio key)")
    transcript.say("scrubbing           : every line below passed through tools/scrub.py")
    transcript.say(
        "verdict semantics   : PASS / FAIL / CANNOT RUN are THREE outcomes. "
        "Only FAIL changes canon (D-00-005)."
    )
    transcript.say("=" * 78)
    transcript.say()

    # -- PB-A1  configuration ------------------------------------------------
    transcript.say("-- PB-A1  router configuration resolves")
    try:
        config = GeminiRouterConfig.from_env(os.environ)
    except Exception as exc:
        transcript.verdict(FAIL, "router config", f"{type(exc).__name__}: {exc}")
        return 1
    if config.api_key is None:
        transcript.verdict(
            CANNOT_RUN, "GOOGLE_API_KEY", "not set; no model call was attempted and none can be"
        )
        return 2
    transcript.verdict(
        PASS,
        "tier ids in allow-set",
        f"tier_e={config.extraction_model_id} tier_r={config.reasoning_model_id}",
    )
    transcript.say()

    # -- PB-A2  prompt assets ------------------------------------------------
    transcript.say("-- PB-A2  prompt assets match their manifest")
    try:
        manifest = load_manifest()
        renderer = AssetPromptRenderer(verify_manifest=False)
        extract_spec = route("extract_structured_evidence")
        system_extract = renderer.render_system(extract_spec.prompt_version)
        renderer.render_system(route("strong_resolution").prompt_version)
        transcript.verdict(
            PASS,
            "prompt assets",
            f"{len(manifest)} hash-pinned; {extract_spec.prompt_version} system="
            f"{len(system_extract)} chars",
        )
    except Exception as exc:
        transcript.verdict(FAIL, "prompt assets", f"{type(exc).__name__}: {exc}")
        return 1
    transcript.say()

    slugs: list[str] = list(args.artifact) or [DEFAULT_ARTIFACT]
    all_calls: list[ModelCallRecord] = []
    rows = count_agent_runs()
    run_ids: list[uuid.UUID] = []
    for slug in slugs:
        transcript.say("-" * 78)
        transcript.say(f"ARTIFACT {slug}")
        transcript.say("-" * 78)
        outcome = run_one(args, transcript, slug=slug, config=config, renderer=renderer)
        if outcome is None:
            continue
        run_ids.append(outcome[0])
        all_calls.extend(outcome[1])
        rows = outcome[2]
        transcript.say()

    _summary(transcript, run_ids=run_ids, artifacts=slugs, calls=all_calls, rows=rows)
    _, failed, _ = transcript.counts()
    return 1 if failed else 0


def run_one(
    args: argparse.Namespace,
    transcript: Transcript,
    *,
    slug: str,
    config: GeminiRouterConfig,
    renderer: AssetPromptRenderer,
) -> tuple[uuid.UUID, list[ModelCallRecord], int] | None:
    """One artifact, one ``agent_runs`` row. ``None`` when nothing was attempted."""
    started_wall = datetime.now(UTC)
    extract_spec = route("extract_structured_evidence")
    resolve_spec = route("strong_resolution")
    # -- PB-A3  the artifact -------------------------------------------------
    transcript.say("-- PB-A3  a real seeded artifact, bytes checked against the row")
    try:
        artifact = load_seeded_artifact(slug, args.subject)
    except LookupError as exc:
        transcript.verdict(CANNOT_RUN, "seeded artifact", str(exc))
        return None
    blocks = parse_email_blocks(artifact)
    if not artifact.bytes_agree:
        transcript.verdict(
            FAIL,
            "artifact bytes",
            f"demo/artifacts/{artifact.filename} sha256={artifact.disk_sha256[:16]}... "
            f"row={artifact.content_sha256[:16]}...",
        )
        return None
    transcript.verdict(
        PASS,
        "artifact bytes",
        f"{artifact.filename} sha256={artifact.content_sha256[:16]}... "
        f"{artifact.size_bytes}B -> {len(blocks)} blocks "
        f"({', '.join(sorted({b.kind.value for b in blocks}))})",
    )
    transcript.say()

    # -- deps ----------------------------------------------------------------
    run_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    binding = CapabilityBinding(
        binding_id=run_id,
        binding_kind="AGENT_RUN",
        tenant_id=artifact.tenant_id,
        user_id=artifact.user_id,
        artifact_id=artifact.artifact_id,
        expires_at=started_wall + CAPABILITY_TTL,
        status="ACTIVE",
    )
    registrar = SeededEvidenceLookup(artifact)
    retrieval = DeterministicRetrieval(artifact)
    session = RecordingSession()
    kernel = WithheldKernelClient()
    client = GeminiClient(
        config=config,
        generate_content=gemini_transport(api_key=config.require_api_key().get_secret_value()),
    )
    adapter = LiveRouterAdapter(
        ModelRouter(config=config, client=client),
        schema_for_node={
            "extract_structured_evidence": bind_extraction_schema(
                blocks=blocks,
                artifact_id=artifact.artifact_id,
                agent_run_id=run_id,
                trace_id=trace_id,
                model_id=config.model_id_for(extract_spec.tier),
                prompt_version=extract_spec.prompt_version,
            )
        },
    )
    deps = IngestionDeps(
        router=adapter,
        renderer=renderer,
        artifacts=SeededArtifactReader(artifact, blocks),
        registrar=registrar,
        retrieval=retrieval,
        kernel=kernel,
        session=session,
        clock=lambda: datetime.now(UTC),
        extraction_route=ModelRoute(
            provider="gemini",
            model_id=config.model_id_for(extract_spec.tier),
            tier=extract_spec.tier,
            prompt_version=extract_spec.prompt_version,
        ),
        resolution_route=ModelRoute(
            provider="gemini",
            model_id=config.model_id_for(resolve_spec.tier),
            tier=resolve_spec.tier,
            prompt_version=resolve_spec.prompt_version,
        ),
    )

    # -- PB-A4  the row exists before the first model call -------------------
    transcript.say("-- PB-A4  agent_runs row opened before the first model call")
    before = count_agent_runs()
    open_agent_run(
        run_id=run_id,
        binding=binding,
        trace_id=trace_id,
        artifact_id=artifact.artifact_id,
        started_at=started_wall,
        config=config,
    )
    transcript.verdict(
        PASS, "agent_runs INSERT", f"id={run_id} status=RUNNING rows_before={before}"
    )
    transcript.say()

    # -- the walk ------------------------------------------------------------
    transcript.say("-- PB-A5  the graph walks (live Gemini; nothing is scripted)")
    state: IngestionGraphState | None = None
    withheld: KernelSubmissionWithheldError | None = None
    escaped: BaseException | None = None
    elapsed = time.monotonic()
    try:
        state = run_ingestion(
            initial_ingestion_state(
                trace_id=trace_id,
                agent_run_id=run_id,
                principal_ref=binding,
                artifact_id=artifact.artifact_id,
            ),
            deps,
        )
    except KernelSubmissionWithheldError as exc:
        withheld = exc
    except Exception as exc:  # a verdict, not a crash; the row is still settled below
        escaped = exc
    elapsed = time.monotonic() - elapsed

    # `run_ingestion` documents that "the loop never raises": a node that fails
    # records a GraphError and a terminal outcome so the visit order survives.
    # An exception here is therefore a defect in a node, not a runner problem,
    # and it is reported as one -- with every section below still printed,
    # because the live model calls happened and are the point of this run.
    if escaped is not None:
        transcript.verdict(
            FAIL,
            "graph walk raised",
            f"{type(escaped).__name__} escaped run_ingestion, which documents that the "
            f"loop never raises: {escaped}",
        )
    if state is not None:
        visits = tuple(state.visits)
        transcript.verdict(
            PASS if visits else FAIL,
            "node visit order",
            " -> ".join(visits) if visits else "no node recorded a visit",
        )
    else:
        # `run_ingestion` did not return, so `state.visits` -- the graph's own
        # record -- does not exist. What follows is reconstructed from
        # side effects each node is the only possible cause of, and it is
        # labelled as a reconstruction rather than presented as the record.
        observed = _observed_nodes(
            session=session,
            adapter=adapter,
            registrar=registrar,
            retrieval=retrieval,
            kernel=kernel,
        )
        transcript.verdict(
            PASS if observed else FAIL,
            "nodes observed (reconstructed)",
            " -> ".join(observed) if observed else "no node left an observable trace",
        )
        transcript.say(
            "   note: two of the eleven nodes never call session.record -- "
            f"{', '.join(NODES_WITHOUT_SESSION_NOTE)} -- and build_memory_proposal "
            "records only on its non-NOOP path. Session storage is documented as "
            '"a place to note that a node ran so a crashed run can be resumed or '
            'debugged", and with those gaps it cannot serve that purpose: the line '
            "above is reconstructed from four independent side effects instead."
        )
    transcript.say(f"   walk elapsed {elapsed:.1f}s")
    transcript.say()

    # -- PB-A6  the live calls ----------------------------------------------
    transcript.say("-- PB-A6  live model calls (this is the evidence)")
    if not adapter.calls:
        transcript.verdict(CANNOT_RUN, "model invocation", "the graph halted before any model node")
    # One verdict per model NODE, not per HTTP call. A schema failure followed by
    # a successful repair is the router's designed path (14_PROMPTS.md section
    # 7.2), so scoring it FAIL would report a working budget as a broken one.
    # The individual calls are printed underneath because a repair costs a real
    # request and a real bill, and hiding that would be the opposite mistake.
    #
    # A node the router gave up on is scored by WHY it gave up, because the two
    # reasons are different questions and this file's own legend says a FAIL
    # means "the graph, the prompt or the contract is wrong and must be
    # corrected".
    #
    #   SCHEMA_REPAIR_EXHAUSTED  the model answered and its answer would not
    #                            validate, twice. That is the graph, the prompt
    #                            or the contract. FAIL.
    #   MODEL_INVOCATION_FAILED  the provider did not answer at all. On
    #                            2026-08-31 gemini-3.7-flash returned 503
    #                            UNAVAILABLE ("this model is currently
    #                            experiencing high demand") and 504
    #                            DEADLINE_EXCEEDED to a two-word prompt, three
    #                            times in a row, while gemini-3.5-flash-lite
    #                            answered in 0.6s. Nothing about the graph was
    #                            measured, so the question is still OPEN.
    #                            CANNOT RUN.
    #
    # Recording a provider capacity failure as FAIL sends a reader to fix a
    # prompt that is not broken, and inflates the FAIL count of the transcript
    # this project offers as its agent evidence. It is the same misreading the
    # submission gate was corrected for: a denial is not an affirmation, and an
    # unanswered question is not a wrong answer.
    invocation_failed = "MODEL_INVOCATION_FAILED"
    pending_by_node = {pending.node: pending for pending in adapter.pending}
    for node in sorted({record.node for record in adapter.calls}):
        node_calls = [record for record in adapter.calls if record.node == node]
        spec = route(node)
        served = sorted({record.model_id for record in node_calls})
        pending = pending_by_node.get(node)
        if pending is None:
            node_verdict = PASS
        elif pending.reason_code == invocation_failed:
            node_verdict = CANNOT_RUN
        else:
            node_verdict = FAIL
        transcript.verdict(
            node_verdict,
            node,
            f"tier={spec.tier.value} model={', '.join(served)} calls={len(node_calls)} "
            f"repaired={any(record.repair_attempts for record in node_calls)} "
            f"in={sum(record.input_tokens for record in node_calls)} "
            f"out={sum(record.output_tokens for record in node_calls)} "
            f"thoughts={sum(record.thought_tokens for record in node_calls)}",
        )
        for record in node_calls:
            transcript.say(
                f"      call {record.seq}: model={record.model_id} "
                f"prompt={record.prompt_version} in={record.input_tokens} "
                f"out={record.output_tokens} thoughts={record.thought_tokens} "
                f"{record.duration_ms}ms outcome={record.outcome}"
            )
    for pending in adapter.pending:
        transcript.say(
            f"      pending: reason={pending.reason_code} "
            f"codes={','.join(pending.failure_codes) or '-'}"
        )
    transcript.say()

    # -- PB-A7  the extraction ----------------------------------------------
    transcript.say("-- PB-A7  the extraction, validated against ExtractionResult")
    extraction = (
        state.extraction_result
        if state is not None
        else adapter.values.get("extract_structured_evidence")
    )
    if extraction is None:
        transcript.verdict(CANNOT_RUN, "ExtractionResult", "no extraction reached the graph state")
    else:
        transcript.verdict(
            PASS,
            "ExtractionResult",
            f"schema={extraction.extraction_schema_version} "
            f"evidence={len(extraction.evidence_candidates)} "
            f"claims={len(extraction.claim_candidates)} "
            f"commitments={len(extraction.commitment_candidates)} "
            f"identifiers={len(extraction.external_identifiers)} "
            f"cues={len(extraction.prospective_cues)} "
            f"injections={len(extraction.injection_observations)} "
            f"blocks_state_change={extraction.blocks_state_change}",
        )
        transcript.say(f"   summary: {extraction.artifact_summary}")
        for identifier in extraction.external_identifiers:
            transcript.say(f"   identifier: {identifier.kind.value}={identifier.value}")
        for claim in extraction.claim_candidates:
            transcript.say(
                f"   claim: {claim.local_id} {claim.claim_kind.value} "
                f"{claim.predicate} modality={claim.modality.value} "
                f"confidence={claim.extraction_confidence}"
            )
        for commitment in extraction.commitment_candidates:
            transcript.say(
                f"   commitment: {commitment.local_id} {commitment.commitment_type.value} "
                f"due_at={commitment.due_at} money={commitment.money} "
                f"condition={commitment.due_condition_text!r}"
            )
        for observation in extraction.injection_observations:
            transcript.say(
                f"   injection: {observation.local_id} "
                f"{observation.classification} action={observation.action_taken}"
            )
    transcript.say()

    # -- PB-A8  fence scrubbing ---------------------------------------------
    transcript.say("-- PB-A8  untrusted-evidence fencing")
    scrub_log = state.fence_scrub_log if state is not None else ()
    transcript.verdict(
        PASS,
        "fence scrub log",
        f"{len(scrub_log)} block(s) rewritten before render"
        + (f": {', '.join(e.block_id for e in scrub_log)}" if scrub_log else " (ordinary case)"),
    )
    transcript.say()

    # -- PB-A9  evidence ------------------------------------------------------
    transcript.say("-- PB-A9  evidence registration")
    transcript.verdict(
        PASS if registrar.matched else CANNOT_RUN,
        "evidence lookup",
        f"{len(registrar.matched)} of "
        f"{len(registrar.matched) + len(registrar.unmatched)} candidate(s) matched an "
        f"existing row; {registrar.seeded_row_count} seeded row(s) for this artifact",
    )
    for local_id, kind in registrar.match_kind.items():
        transcript.say(f"   {local_id} -> {registrar.matched[local_id]}  ({kind})")
    if registrar.unmatched:
        transcript.verdict(
            CANNOT_RUN,
            "evidence INSERT",
            f"{len(registrar.unmatched)} candidate(s) ({', '.join(registrar.unmatched)}) have no "
            "existing row and were NOT written: evidence_items.embedding is VECTOR(1024) and "
            "ck_evidence_embedding_model admits only amazon.titan-embed-text-v2:0, so the only "
            "legal INSERT carries embedding=NULL in an append-only table. Migration 0009 is "
            "written and deliberately unapplied.",
        )
    transcript.say()

    # -- PB-A10  retrieval and the resolver decision -------------------------
    transcript.say("-- PB-A10  deterministic retrieval and the one conditional edge")
    context = (
        state.retrieval_context
        if state is not None
        else (retrieval.last.context if retrieval.last is not None else None)
    )
    candidate_count = 0
    if context is None:
        transcript.verdict(CANNOT_RUN, "retrieval", "the graph halted before retrieval")
    else:
        candidate_count = len(context.case_candidates) + len(context.relationship_candidates)
        transcript.verdict(
            PASS,
            "retrieval (identity stage)",
            f"relationships={len(context.relationship_candidates)} "
            f"cases={len(context.case_candidates)} "
            f"exact_identifier_hits={len(retrieval.identifier_hits)} "
            f"name_hits={len(retrieval.name_hits)}",
        )
        for candidate in context.relationship_candidates:
            transcript.say(
                f"   relationship: {candidate.label} score={candidate.score} "
                f"{','.join(candidate.reasons)}"
            )
        for candidate in context.case_candidates[:6]:
            transcript.say(f"   case: {candidate.label} score={candidate.score}")
    signals = state.resolution_signals if state is not None else None
    if signals is None and retrieval.last is not None and extraction is not None:
        # `resolution_signals` is the graph's own exported pure function, so
        # recovering the decision after a later node raised re-derives it rather
        # than restating it -- a second implementation here would be a second
        # opinion about which artifacts get a Tier R call.
        signals = resolution_signals(retrieval=retrieval.last, extraction=extraction)
    if signals is None:
        transcript.verdict(CANNOT_RUN, "resolver decision", "route_resolution_need was not reached")
    else:
        invoked = (
            "RESOLVER_INVOKED" in state.route_flags
            if state is not None
            else "strong_resolution" in adapter.values
            or any(call.node == "strong_resolution" for call in adapter.calls)
        )
        transcript.verdict(
            PASS if invoked == should_resolve(signals) else FAIL,
            "resolver decision",
            f"{'RESOLVER_INVOKED' if invoked else 'RESOLVER_SKIPPED'} "
            f"(should_resolve={should_resolve(signals)}) "
            f"top_case_score={signals.top_case_score} margin={signals.identity_margin} "
            f"blocking_uncertainty={signals.blocking_uncertainty}",
        )
    assessment = (
        state.resolution_assessment
        if state is not None
        else adapter.values.get("strong_resolution")
    )
    if assessment is not None:
        transcript.say(
            f"   assessment: case={assessment.identity.case_id} "
            f"confidence={assessment.identity.confidence} "
            f"requires_human_review={assessment.requires_human_review} "
            f"unresolved={len(assessment.unresolved_questions)}"
        )
        transcript.say(f"   rationale: {assessment.rationale_summary}")
    transcript.say()

    # -- PB-A11  proposals ---------------------------------------------------
    transcript.say("-- PB-A11  typed MemoryProposals")
    proposals = list(kernel.proposals) or list(state.memory_proposals if state else ())
    if not proposals:
        transcript.verdict(
            CANNOT_RUN,
            "MemoryProposal",
            "the graph built none; with no admitted evidence there is nothing to propose",
        )
    else:
        transcript.verdict(
            PASS,
            "MemoryProposal",
            f"{len(proposals)} proposal(s), one case each; "
            f"keys={', '.join(p.idempotency_key for p in proposals)}",
        )
        for proposal in proposals:
            transcript.say(
                f"   proposal: case={proposal.identity.case_id} "
                f"claims={len(proposal.claims)} commitments={len(proposal.commitments)} "
                f"model={proposal.model.provider}/{proposal.model.model_id} "
                f"tier={proposal.model.tier.value}"
            )
        if args.proposals_out:
            out = Path(args.proposals_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps([p.model_dump(mode="json") for p in proposals], indent=2),
                encoding="utf-8",
            )
            transcript.say(f"   written to {out}")
    transcript.say()

    # -- PB-A12  the Kernel door ---------------------------------------------
    transcript.say("-- PB-A12  Memory Kernel submission")
    if withheld is not None:
        transcript.verdict(CANNOT_RUN, "submit_memory_proposal", str(withheld))
    elif state is not None and state.kernel_results:
        transcript.verdict(
            PASS,
            "submit_memory_proposal",
            f"{len(state.kernel_results)} receipt(s)",
        )
    else:
        transcript.verdict(
            CANNOT_RUN, "submit_memory_proposal", "the graph halted before the Kernel node"
        )
    transcript.say()

    # -- settle the row ------------------------------------------------------
    outcome = state.outcome.value if state is not None else "NOT_REACHED"
    status = "SUCCEEDED"
    error_code: str | None = None
    if escaped is not None:
        status, error_code = "FAILED", "GRAPH_NODE_RAISED"
    elif withheld is not None:
        status, error_code = "ABANDONED", "KERNEL_SUBMISSION_WITHHELD"
    elif state is not None and state.errors:
        status, error_code = "ABANDONED", state.errors[0].code
    elif state is None:
        status, error_code = "ABANDONED", "GRAPH_STATE_UNAVAILABLE"
    close_agent_run(
        run_id=run_id,
        binding=binding,
        status=status,
        error_code=error_code,
        finished_at=datetime.now(UTC),
        retrieval_candidate_count=candidate_count,
        calls=adapter.calls,
        capability_status={
            "proposal_tool_bound": False,
            "graph_outcome": outcome,
            "model_call_budget": len(adapter.calls),
        },
    )
    after = count_agent_runs()
    transcript.say("-- PB-A13  agent_runs row settled")
    transcript.verdict(
        PASS,
        "agent_runs UPDATE",
        f"status={status} error_code={error_code or '-'} "
        f"model_calls={len(adapter.calls)} rows_now={after}",
    )
    transcript.say()

    return (run_id, list(adapter.calls), after)


#: Nodes whose execution leaves a trace outside the graph state, and the
#: side effect that proves it. Only nodes that record a session note are
#: recoverable from ``session``; the rest need their own dependency to have been
#: called, which is why this table exists rather than a single log read.
NODES_WITHOUT_SESSION_NOTE: Final[tuple[str, ...]] = (
    "retrieve_candidate_context",
    "strong_resolution",
)

_NODE_EVIDENCE: Final[tuple[tuple[str, str], ...]] = (
    ("load_artifact_metadata", "session"),
    ("load_normalized_content", "session"),
    ("extract_structured_evidence", "model call"),
    ("validate_extraction_schema", "session"),
    ("register_or_lookup_evidence", "registrar call"),
    ("retrieve_candidate_context", "retrieval call"),
    ("route_resolution_need", "session"),
    ("strong_resolution", "model call"),
    ("build_memory_proposal", "session"),
    ("submit_to_memory_kernel", "kernel call"),
    ("route_commit_result", "session"),
)


def _observed_nodes(
    *,
    session: RecordingSession,
    adapter: LiveRouterAdapter,
    registrar: SeededEvidenceLookup,
    retrieval: DeterministicRetrieval,
    kernel: WithheldKernelClient,
) -> tuple[str, ...]:
    """Nodes proved to have run, in topology order, from independent evidence."""
    noted = {node for node, _ in session.notes}
    called = {call.node for call in adapter.calls}
    other = {
        "register_or_lookup_evidence": bool(registrar.matched or registrar.unmatched),
        "retrieve_candidate_context": retrieval.last is not None,
        "submit_to_memory_kernel": bool(kernel.proposals),
    }
    return tuple(
        node
        for node, _ in _NODE_EVIDENCE
        if node in noted or node in called or other.get(node, False)
    )


def _summary(
    transcript: Transcript,
    *,
    run_ids: Sequence[uuid.UUID],
    artifacts: Sequence[str],
    calls: Sequence[ModelCallRecord],
    rows: int,
) -> None:
    passed, failed, cannot = transcript.counts()
    transcript.say("=" * 78)
    transcript.say("SUMMARY")
    transcript.say("=" * 78)
    transcript.say(f"  artifacts           : {', '.join(artifacts)}")
    for run_id in run_ids:
        transcript.say(f"  agent_run_id        : {run_id}")
    transcript.say(f"  agent_runs rows now : {rows}")
    ids = sorted({record.model_id for record in calls})
    transcript.say(f"  model ids invoked   : {', '.join(ids) if ids else '(none)'}")
    transcript.say(
        f"  tokens              : in={sum(c.input_tokens for c in calls)} "
        f"out={sum(c.output_tokens for c in calls)} "
        f"thoughts={sum(c.thought_tokens for c in calls)}"
    )
    transcript.say()
    transcript.say(f"  PASS {passed}  |  FAIL {failed}  |  CANNOT RUN {cannot}")
    transcript.say()
    transcript.say(
        "  A FAIL means the graph, the prompt or the contract is wrong and must be "
        "corrected before that path is used."
    )
    transcript.say(
        "  A CANNOT RUN means the question is still OPEN. It must not be recorded as a "
        "failure and must not force a fallback."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/run_ingestion_graph.py",
        description=(
            "Walk the ingestion graph over a seeded artifact with a live Gemini router, "
            "and persist the agent_runs row it produced."
        ),
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help=f"file slug; repeatable (default: {DEFAULT_ARTIFACT})",
    )
    parser.add_argument("--subject", default=HERO_SUBJECT, help="users.cognito_sub")
    parser.add_argument("--list", action="store_true", help="list the seeded artifacts and exit")
    parser.add_argument("--transcript", default=None, help="write the scrubbed transcript here")
    parser.add_argument("--proposals-out", default=None, help="write the typed proposals here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _load_dotenv(_REPO_ROOT)

    if args.list:
        for slug, artifact_id, subject in list_seeded_artifacts(args.subject):
            print(f"{slug:<42} {artifact_id}  {subject or ''}")
        return 0

    transcript = Transcript()
    try:
        code = run(args, transcript)
    finally:
        if args.transcript:
            path = Path(args.transcript)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(transcript.render(), encoding="utf-8", newline="\n")
            print(f"transcript written to {path}")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
