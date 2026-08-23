"""Read `ops/agent-graph-live-run.txt` -- a recorded live run -- as data.

Why parse a transcript instead of calling the model
---------------------------------------------------
``ops/agent-graph-live-run.txt`` is a real run against the Gemini Developer API
on 2026-08-24: three artifacts, both tiers, 37,050 input and 7,922 output
tokens, and 21 ``agent_runs`` rows in the cluster to match. Re-running it to
score it would spend budget to re-derive what is already recorded, and would
produce *different* output -- the model is nondeterministic and the same three
artifacts have produced between one and three FAILs across seven runs. Scoring
the recording scores a thing that happened; scoring a fresh call scores a thing
that happens once.

The cost is stated rather than hidden: **three of the 34 hero artifacts have a
recorded extraction.** The other 31 are reported ``CANNOT RUN``, not zero, and
the report names what they would need.

`CANNOT RUN` inside the transcript
-----------------------------------
The transcript's own verdict vocabulary is ``PASS`` / ``FAIL`` / ``CANNOT
RUN``, and its footer says so explicitly. This parser preserves all three. A
parser that split on "not PASS" would convert every recorded ``CANNOT RUN``
into a failure, which is the defect the transcript was written to avoid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "LIVE_RUN_TRANSCRIPT",
    "RecordedClaim",
    "RecordedCommitment",
    "RecordedExtraction",
    "RecordedInjection",
    "parse_transcript",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The recorded run this harness scores.
LIVE_RUN_TRANSCRIPT: Final[Path] = REPO_ROOT / "ops" / "agent-graph-live-run.txt"

_ARTIFACT = re.compile(r"^ARTIFACT\s+(?P<name>\S+)\s*$")
_SUMMARY = re.compile(r"^\s*summary:\s*(?P<text>.+)$")
_CLAIM = re.compile(
    r"^\s*claim:\s*(?P<local_id>\S+)\s+(?P<kind>[A-Z_]+)\s+(?P<predicate>\S+)"
    r"(?:\s+modality=(?P<modality>[A-Z_]+))?"
)
_COMMITMENT = re.compile(
    r"^\s*commitment:\s*(?P<local_id>\S+)\s+(?P<type>[A-Z_]+)"
    r".*?money=(?P<currency>[A-Z]{3})\s+(?P<amount>[0-9.,]+)"
)
_INJECTION = re.compile(
    r"^\s*injection:\s*(?P<local_id>\S+)\s+(?P<classification>[A-Z_]+)"
    r"\s+action=(?P<action>[A-Z_]+)"
)
_EXTRACTION_VERDICT = re.compile(
    r"^\s*(?P<verdict>PASS|FAIL|CANNOT RUN)\s+ExtractionResult\s+(?P<fields>.+)$"
)
_FIELD = re.compile(r"(\w+)=(\S+)")


@dataclass(frozen=True)
class RecordedClaim:
    local_id: str
    claim_kind: str
    predicate: str
    modality: str | None


@dataclass(frozen=True)
class RecordedCommitment:
    local_id: str
    commitment_type: str
    currency: str
    amount: str

    @property
    def money(self) -> str:
        """``"USD 1800.00"`` -- thousands separators removed, so a gold label
        written from the document (``USD 1,800.00``) compares equal."""
        return f"{self.currency} {self.amount.replace(',', '')}"


@dataclass(frozen=True)
class RecordedInjection:
    local_id: str
    classification: str
    action: str


@dataclass(frozen=True)
class RecordedExtraction:
    """One artifact's extraction, as the recorded run produced it."""

    artifact: str
    verdict: str
    summary: str
    claims: tuple[RecordedClaim, ...] = ()
    commitments: tuple[RecordedCommitment, ...] = ()
    injections: tuple[RecordedInjection, ...] = ()
    counts: dict[str, str] = field(default_factory=dict)

    @property
    def claim_kinds(self) -> frozenset[str]:
        return frozenset(claim.claim_kind for claim in self.claims)

    @property
    def claim_modalities(self) -> frozenset[str]:
        return frozenset(claim.modality for claim in self.claims if claim.modality)

    @property
    def commitment_types(self) -> frozenset[str]:
        return frozenset(item.commitment_type for item in self.commitments)

    @property
    def monies(self) -> frozenset[str]:
        return frozenset(item.money for item in self.commitments)


def parse_transcript(path: Path | None = None) -> dict[str, RecordedExtraction]:
    """Every artifact block in the transcript, keyed by artifact name.

    An artifact whose block carries no ``ExtractionResult`` verdict is omitted
    rather than recorded as an empty extraction: an empty extraction scores as
    a total miss, and "the run never reached this stage" is a different claim.
    """
    source = path if path is not None else LIVE_RUN_TRANSCRIPT
    if not source.is_file():
        raise FileNotFoundError(
            f"{source} does not exist. It is the recorded live run this suite "
            f"scores; without it the extraction suite reports CANNOT RUN rather "
            f"than a zero."
        )

    parsed: dict[str, RecordedExtraction] = {}
    current: str | None = None
    verdict: str | None = None
    counts: dict[str, str] = {}
    summary = ""
    claims: list[RecordedClaim] = []
    commitments: list[RecordedCommitment] = []
    injections: list[RecordedInjection] = []

    def flush() -> None:
        nonlocal verdict, summary, claims, commitments, injections, counts
        if current is not None and verdict is not None:
            parsed[current] = RecordedExtraction(
                artifact=current,
                verdict=verdict,
                summary=summary,
                claims=tuple(claims),
                commitments=tuple(commitments),
                injections=tuple(injections),
                counts=dict(counts),
            )
        verdict, summary, counts = None, "", {}
        claims, commitments, injections = [], [], []

    for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
        header = _ARTIFACT.match(raw.strip())
        if header:
            flush()
            current = header.group("name")
            continue
        if current is None:
            continue
        if (match := _EXTRACTION_VERDICT.match(raw)) is not None:
            verdict = match.group("verdict")
            counts = dict(_FIELD.findall(match.group("fields")))
            continue
        if (match := _SUMMARY.match(raw)) is not None:
            summary = match.group("text").strip()
            continue
        if (match := _CLAIM.match(raw)) is not None:
            claims.append(
                RecordedClaim(
                    local_id=match.group("local_id"),
                    claim_kind=match.group("kind"),
                    predicate=match.group("predicate"),
                    modality=match.group("modality"),
                )
            )
            continue
        if (match := _COMMITMENT.match(raw)) is not None:
            commitments.append(
                RecordedCommitment(
                    local_id=match.group("local_id"),
                    commitment_type=match.group("type"),
                    currency=match.group("currency"),
                    amount=match.group("amount"),
                )
            )
            continue
        if (match := _INJECTION.match(raw)) is not None:
            injections.append(
                RecordedInjection(
                    local_id=match.group("local_id"),
                    classification=match.group("classification"),
                    action=match.group("action"),
                )
            )
    flush()
    return parsed
