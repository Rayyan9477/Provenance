"""``PB-G1``..``PB-G6`` -- the Gemini capability probes.

Why this file exists
--------------------
``docs/CANONICAL_DECISIONS.md`` -> *Bedrock model id canon* records the single
most expensive lesson of the previous build:

    ``list-foundation-models`` returns ids that are **not invocable**.

Every Anthropic id frozen from documentation was wrong. Anthropic chat models
needed ``us.``/``global.`` inference-profile prefixes; every other provider
needed bare ids -- the mirror image, so one uniform rule could not call both
families. That cost a full re-probe and a canon rewrite.

The Gemini ids now in ``CANONICAL_DECISIONS.md`` -> *Gemini model id canon* are
in exactly the same state those were: transcribed from documentation, never
invoked. This script is what moves them from *documented* to *established*, and
nothing else does. ``client.models.list()`` is run here for reference and is
**explicitly not counted as proof** -- listing is the trap, not the evidence.

The three-valued result, which is the other hard-won lesson
------------------------------------------------------------
``D-00-005``: a probe that could not connect once reported that a capability
had *failed*, which would have forced a working capability into a permanent
fallback. ``PASS``, ``FAIL`` and ``CANNOT RUN`` are therefore three distinct
outcomes here and they lead to three different decisions:

===========  ==========================================================
``PASS``     the call was made and returned what canon claims
``FAIL``     the call was made and returned something else -- canon is wrong
``CANNOT``   the call was never made -- canon is neither confirmed nor denied
===========  ==========================================================

A ``CANNOT RUN`` never changes canon. Only a ``FAIL`` does.

Secrets
-------
The API key is read from the environment, never printed, never echoed, and
every transcript line is passed through ``tools.scrub`` before it reaches disk
or the console. ``D-00-042`` is why there is no path-based exemption anywhere
near this file: ``allowlist.paths`` in ``.gitleaks.toml`` turned out to be a
whole-file skip, so a live credential in a transcript at an allowlisted path
scanned zero bytes.

Usage
-----
    GOOGLE_API_KEY=... python -m ops.probes.gemini_probe
    python ops/probes/gemini_probe.py --out ops/gemini-probe.txt

Exit codes: ``0`` all probes PASS; ``1`` at least one FAIL; ``2`` at least one
CANNOT RUN and no FAIL.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.scrub import scrub_text  # noqa: E402

Outcome = Literal["PASS", "FAIL", "CANNOT RUN"]

#: Candidate ids, from https://ai.google.dev/gemini-api/docs/models (2026-08).
#: The hackathon floor is "Gemini 3.5 or newer". ``gemini-3.1-pro-preview`` is
#: deliberately absent: it is version 3.1, *below* the floor, so no Pro model
#: qualifies and both tiers are Flash-class.
CHAT_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("gemini-3.7-flash", "Tier R canon -- resolution, contradiction, advocacy"),
    ("gemini-3.5-flash-lite", "Tier E canon -- extraction, classification"),
    ("gemini-3.6-flash", "Tier R fallback (GA)"),
    ("gemini-3.5-flash", "alternate Tier E"),
)

#: The models page prints ``gemini-embedding-2-preview``; the embeddings page
#: prints ``gemini-embedding-2``. Both are probed because we do not know which
#: is invocable, and guessing is the exact failure this file exists to prevent.
EMBEDDING_CANDIDATES: Final[tuple[str, ...]] = (
    "gemini-embedding-2",
    "gemini-embedding-2-preview",
    "gemini-embedding-001",
)

TARGET_DIMENSIONS: Final[int] = 1536
NORM_TOLERANCE: Final[float] = 1e-6

#: The chat budget. This was 16, and 16 produced three false PASSes.
#:
#: Every Flash tier above Lite thinks by default, and ``max_output_tokens`` is
#: ONE allowance shared between thinking and the visible answer -- it is not a
#: cap on the reply. Measured against the live API on 2026-08-24, answering the
#: single word ``ok`` cost 133 thinking tokens on ``gemini-3.7-flash``, 128 on
#: ``gemini-3.6-flash`` and 84 on ``gemini-3.5-flash``. At 16 the allowance was
#: gone before the first visible token: ``candidates_token_count`` came back
#: ``None`` and ``finish_reason`` ``MAX_TOKENS``, so ``response.text`` was the
#: empty string -- and an empty string is not an exception, so the probe called
#: it PASS. ``gemini-3.5-flash-lite`` reports ``thoughts_token_count=None`` and
#: was the only id that answered, which is what made the pattern legible.
CHAT_MAX_OUTPUT_TOKENS: Final[int] = 2048

#: A 64x64 PNG, red over blue. PB-G6 used a 1x1 transparent pixel and the API
#: answered ``400 INVALID_ARGUMENT: Unable to process input image`` -- recorded
#: as a capability FAIL. An 8x8 solid red PNG of *the same 75 bytes* succeeds,
#: so the rejection was of the fixture and never of the capability. Two tones
#: rather than one so that a correct reply has to describe something the model
#: actually saw; a solid square can be guessed from the word "image" alone.
PROBE_IMAGE: Final[bytes] = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000004000000040080200000025"
    "0be689000000554944415478daedcf410d0030080430e49c12b44fc4c4a0"
    "812749931a68fde4b4121010101010101010101010101010101010101010"
    "10105807d2ef340101010101010101010101010101010101010101010181"
    "b5018b88111ecbc16fbc0000000049454e44ae426082"
)


def probe_image_size() -> tuple[int, int]:
    """Width and height read from the PNG IHDR, not from a comment.

    A comment claiming the fixture is 64x64 is a sentence; this reads the
    bytes. The 1x1 regression is only caught if the assertion looks at what
    will actually be uploaded.
    """
    width = int.from_bytes(PROBE_IMAGE[16:20], "big")
    height = int.from_bytes(PROBE_IMAGE[20:24], "big")
    return width, height


def chat_verdict(text: str, finish_reason: str | None) -> tuple[Outcome, str]:
    """Decide PB-G2 from what came back, not from the absence of an exception.

    Extracted so that the decision which edits canon is testable with no key,
    no network and no SDK -- the probe passes ``finish.name``.

    The three-valued discipline matters most in the middle branch. An empty
    body with ``MAX_TOKENS`` says the probe's own budget was too small; it says
    nothing about the id. Calling that FAIL would licence demoting a working
    Tier R model to a fallback on the strength of our own misconfiguration,
    which is ``D-00-005`` exactly.
    """
    if text:
        return "PASS", "reply=" + repr(text)
    if finish_reason == "MAX_TOKENS":
        return "CANNOT RUN", (
            "empty reply, finish_reason=MAX_TOKENS: the "
            + str(CHAT_MAX_OUTPUT_TOKENS)
            + "-token budget was consumed by thinking before a visible token was "
            "emitted. This measures the budget, NOT the model id."
        )
    return "FAIL", (
        "empty reply with finish_reason=" + (finish_reason or "NONE") + " (not truncated)"
    )


@dataclass
class ProbeResult:
    """One probe, its verdict, and the evidence for that verdict."""

    probe_id: str
    title: str
    outcome: Outcome
    detail: str
    lines: list[str] = field(default_factory=list)


class Transcript:
    """Scrubbed, append-only. Nothing reaches disk unscrubbed."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._buf: list[str] = []

    def write(self, line: str = "") -> None:
        clean = scrub_text(line)
        self._buf.append(clean)
        print(clean, flush=True)

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(self._buf) + "\n", encoding="utf-8")


def _client(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)


def probe_g1_listing(client: Any, t: Transcript) -> ProbeResult:
    """``PB-G1`` -- enumerate, and record that enumeration proves nothing."""
    t.write("-- PB-G1  model listing (REFERENCE ONLY, NOT PROOF)")
    try:
        names = sorted(m.name or "" for m in client.models.list())
    except Exception as exc:
        return ProbeResult("PB-G1", "model listing", "CANNOT RUN", f"{type(exc).__name__}: {exc}")
    for n in names:
        t.write(f"   {n}")
    t.write(
        f"   listed={len(names)}  -- a listed id is NOT an invocable id "
        "(CANONICAL_DECISIONS.md -> Bedrock model id canon)"
    )
    return ProbeResult("PB-G1", "model listing", "PASS", f"{len(names)} models listed")


def probe_g2_chat(client: Any, t: Transcript) -> list[ProbeResult]:
    """``PB-G2`` -- **invoke** each chat candidate. This is the proof."""
    from google.genai import types

    results: list[ProbeResult] = []
    t.write()
    t.write("-- PB-G2  chat invocation (this is the evidence)")
    for model_id, role in CHAT_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_id,
                contents="Reply with the single word: ok",
                config=types.GenerateContentConfig(
                    max_output_tokens=CHAT_MAX_OUTPUT_TOKENS, temperature=0.0
                ),
            )
            text = (response.text or "").strip()
            usage = getattr(response, "usage_metadata", None)
            tokens = getattr(usage, "total_token_count", None) if usage else None
            thoughts = getattr(usage, "thoughts_token_count", None) if usage else None
            candidates = getattr(response, "candidates", None) or []
            finish = getattr(candidates[0], "finish_reason", None) if candidates else None
            finish_name = getattr(finish, "name", None) if finish is not None else None
            verdict, detail = chat_verdict(text, finish_name)
            t.write(
                f"   {verdict:11s} {model_id:28s} reply={text!r} tokens={tokens} "
                f"thoughts={thoughts} finish={finish_name}  ({role})"
            )
            results.append(ProbeResult("PB-G2", f"invoke {model_id}", verdict, detail))
        except Exception as exc:
            t.write(f"   FAIL  {model_id:28s} {type(exc).__name__}: {exc}")
            results.append(
                ProbeResult("PB-G2", f"invoke {model_id}", "FAIL", f"{type(exc).__name__}: {exc}")
            )
    return results


def probe_g3_structured(client: Any, t: Transcript) -> ProbeResult:
    """``PB-G3`` -- structured output, on which the extraction contract rests.

    ``specs/14_PROMPTS.md`` section 7 allows exactly one schema-repair attempt.
    That budget is only affordable if the model honours a response schema in
    the first place.
    """
    from google.genai import types
    from pydantic import BaseModel

    class Extracted(BaseModel):
        amount: str
        currency: str

    t.write()
    t.write("-- PB-G3  structured output (response_schema)")
    model_id = CHAT_CANDIDATES[1][0]
    try:
        response = client.models.generate_content(
            model=model_id,
            contents="Extract the amount from: 'Amount due USD 186.00'",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Extracted,
                temperature=0.0,
            ),
        )
        parsed = getattr(response, "parsed", None)
        t.write(f"   PASS  {model_id} parsed={parsed!r}")
        return ProbeResult("PB-G3", "structured output", "PASS", f"parsed={parsed!r}")
    except Exception as exc:
        t.write(f"   FAIL  {model_id} {type(exc).__name__}: {exc}")
        return ProbeResult("PB-G3", "structured output", "FAIL", f"{type(exc).__name__}: {exc}")


def probe_g4_embeddings(client: Any, t: Transcript) -> list[ProbeResult]:
    """``PB-G4`` -- embedding id spelling, width, and **measured L2 norm**.

    The norm is the load-bearing measurement. This stack ranks by cosine.
    ``gemini-embedding-2`` is documented to auto-normalize truncated widths and
    ``gemini-embedding-001`` is documented to require manual normalization --
    but a missed normalization is silent: the distances stay numbers, stay
    ordered, and stop meaning anything. Measuring it here is the only thing
    that will ever notice.
    """
    from google.genai import types

    results: list[ProbeResult] = []
    t.write()
    t.write(f"-- PB-G4  embeddings at output_dimensionality={TARGET_DIMENSIONS}")
    for model_id in EMBEDDING_CANDIDATES:
        try:
            response = client.models.embed_content(
                model=model_id,
                contents="Service period 2026-06-01 through 2026-06-30",
                config=types.EmbedContentConfig(output_dimensionality=TARGET_DIMENSIONS),
            )
            values = list(response.embeddings[0].values or [])
            dims = len(values)
            norm = math.sqrt(sum(v * v for v in values))
            normalised = abs(norm - 1.0) < NORM_TOLERANCE
            ok = dims == TARGET_DIMENSIONS
            verdict: Outcome = "PASS" if ok else "FAIL"
            t.write(
                f"   {verdict:4s}  {model_id:28s} dims={dims} "
                f"l2_norm={norm:.7f} unit={normalised}"
            )
            if ok and not normalised:
                t.write(
                    "         NOTE: not unit-normalised. Cosine ranking requires "
                    "normalisation; the caller MUST normalise or recall degrades "
                    "silently."
                )
            results.append(
                ProbeResult(
                    "PB-G4",
                    f"embed {model_id}",
                    verdict,
                    f"dims={dims} l2_norm={norm:.7f} unit_normalised={normalised}",
                )
            )
        except Exception as exc:
            t.write(f"   FAIL  {model_id:28s} {type(exc).__name__}: {exc}")
            results.append(
                ProbeResult("PB-G4", f"embed {model_id}", "FAIL", f"{type(exc).__name__}: {exc}")
            )
    return results


def probe_g5_determinism(client: Any, t: Transcript) -> ProbeResult:
    """``PB-G5`` -- the same text embeds to the same vector twice.

    ``G6.6`` requires that clearing the embedding cache yields the identical
    vector recomputed: the cache is a performance device and never a
    correctness dependency. That guarantee is worthless if the model is not
    deterministic, so it is measured rather than assumed.
    """
    from google.genai import types

    t.write()
    t.write("-- PB-G5  embedding determinism (same text twice)")
    model_id = EMBEDDING_CANDIDATES[0]
    text = "Amount due USD 186.00"
    try:
        vectors = []
        for _ in range(2):
            response = client.models.embed_content(
                model=model_id,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=TARGET_DIMENSIONS),
            )
            vectors.append(tuple(response.embeddings[0].values or []))
        identical = vectors[0] == vectors[1]
        drift = max((abs(a - b) for a, b in zip(vectors[0], vectors[1], strict=True)), default=0.0)
        verdict: Outcome = "PASS" if identical else "FAIL"
        t.write(f"   {verdict:4s}  {model_id} identical={identical} max_drift={drift:.3e}")
        return ProbeResult(
            "PB-G5", "embedding determinism", verdict, f"identical={identical} drift={drift:.3e}"
        )
    except Exception as exc:
        t.write(f"   CANNOT RUN  {model_id} {type(exc).__name__}: {exc}")
        return ProbeResult(
            "PB-G5", "embedding determinism", "CANNOT RUN", f"{type(exc).__name__}: {exc}"
        )


def probe_g6_multimodal(client: Any, t: Transcript) -> ProbeResult:
    """``PB-G6`` -- native multimodal ingestion, which replaces Textract.

    If this passes, the ingestion pipeline loses an external OCR service *and*
    the submission gains the Best Multimodal UX category. If it fails, the
    parser-first path stands unchanged and nothing downstream breaks.
    """
    from google.genai import types

    t.write()
    t.write("-- PB-G6  native multimodal input (replaces Textract)")
    # PROBE_IMAGE, not a 1x1 pixel. The previous fixture was rejected by the
    # API itself -- see the note on PROBE_IMAGE. This establishes that the
    # request shape is accepted AND that a reply describes the image; it is
    # still not a claim about extraction quality on a real document.
    png = PROBE_IMAGE
    model_id = CHAT_CANDIDATES[1][0]
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[
                types.Part.from_bytes(data=png, mime_type="image/png"),
                "Describe this image in three words.",
            ],
            config=types.GenerateContentConfig(max_output_tokens=32, temperature=0.0),
        )
        text = (response.text or "").strip()
        t.write(f"   PASS  {model_id} accepted image/png; reply={text!r}")
        return ProbeResult("PB-G6", "multimodal input", "PASS", f"reply={text!r}")
    except Exception as exc:
        t.write(f"   FAIL  {model_id} {type(exc).__name__}: {exc}")
        return ProbeResult("PB-G6", "multimodal input", "FAIL", f"{type(exc).__name__}: {exc}")


def exit_code_for(outcomes: list[str]) -> int:
    """Map probe outcomes onto a process exit code.

    Extracted so the three-valued verdict is testable without a network call,
    and so collapsing two outcomes onto one code fails a test rather than
    quietly changing what a caller concludes.

    ``FAIL`` outranks ``CANNOT RUN``: one established wrong answer is more
    actionable than one unanswered question, and only the former licenses a
    change to canon.
    """
    if "FAIL" in outcomes:
        return 1
    if "CANNOT RUN" in outcomes:
        return 2
    return 0


def _load_dotenv(root: Path) -> None:
    """Populate os.environ from .env without ever echoing a value."""
    if os.environ.get("PV_PROBE_NO_DOTENV"):
        return
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="ops/gemini-probe.txt", type=Path)
    args = parser.parse_args(argv)

    _load_dotenv(_REPO_ROOT)
    t = Transcript(_REPO_ROOT / args.out)
    started = datetime.now(UTC).isoformat(timespec="seconds")

    t.write("=" * 78)
    t.write("Provenance -- Gemini capability probe  (PB-G1..PB-G6)")
    t.write(f"started             : {started}")
    t.write("api                 : Gemini Developer API (AI Studio key)")
    t.write("sdk                 : google-genai")
    t.write("scrubbing           : every line below passed through tools/scrub.py")
    t.write(
        "verdict semantics   : PASS / FAIL / CANNOT RUN are THREE outcomes. "
        "Only FAIL changes canon (D-00-005)."
    )
    t.write("=" * 78)
    t.write()

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        t.write(
            "CANNOT RUN  GOOGLE_API_KEY is not set. No probe was attempted, so no "
            "id in CANONICAL_DECISIONS.md -> Gemini model id canon is either "
            "confirmed or denied. This is NOT a failure of any model."
        )
        t.flush()
        return 2

    try:
        client = _client(api_key)
    except Exception as exc:
        t.write(f"CANNOT RUN  client construction failed: {type(exc).__name__}: {exc}")
        t.write(scrub_text(traceback.format_exc()))
        t.flush()
        return 2

    results: list[ProbeResult] = []
    results.append(probe_g1_listing(client, t))
    results.extend(probe_g2_chat(client, t))
    results.append(probe_g3_structured(client, t))
    results.extend(probe_g4_embeddings(client, t))
    results.append(probe_g5_determinism(client, t))
    results.append(probe_g6_multimodal(client, t))

    t.write()
    t.write("=" * 78)
    t.write("SUMMARY")
    t.write("=" * 78)
    for r in results:
        t.write(f"  {r.outcome:11s} {r.probe_id:6s} {r.title:34s} {r.detail}")

    failed = [r for r in results if r.outcome == "FAIL"]
    cannot = [r for r in results if r.outcome == "CANNOT RUN"]
    passed = [r for r in results if r.outcome == "PASS"]
    t.write()
    t.write(f"  PASS {len(passed)}  |  FAIL {len(failed)}  |  CANNOT RUN {len(cannot)}")
    t.write()
    t.write(
        "  A FAIL means canon is WRONG and CANONICAL_DECISIONS.md -> Gemini model "
        "id canon must be corrected before that id is used."
    )
    t.write(
        "  A CANNOT RUN means the question is still OPEN. It must not be recorded "
        "as a failure and must not force a fallback."
    )
    t.flush()

    return exit_code_for([r.outcome for r in results])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
