#!/usr/bin/env python3
"""Validate the defect ledger, and print it for a gate reviewer.

Authority: `EXECUTION/72_DEFECT_PROTOCOL.md` sections 5.2 (the record schema),
5.3 (the detail block), 8.1 (rejected findings), 9 (carried debt), 10.1 (the
AG-1 assertion-count rule) and 11.2:

    tools/defect_lint.py | Validates DEFECTS.md and REJECTED.md: ids unique and
    well-formed; `Phase` matches the id; `Status` in the closed set; `CLOSED`
    requires a 40-character fix SHA, a runnable verifying assertion, and a
    close-proof line; `Sev` carries a rule id; `Repro` is non-empty; every
    `DUPLICATE -> D-xx-nnn` target exists. Also implements the AG-1
    assertion-count rule with `--commit`.

Section 11.3 makes this a **binding precondition of every gate verdict**, which
is why `T0.3` builds it in Phase 0 rather than at the first triage round: "a
precondition that no task creates is a check that silently never runs".

USAGE
-----
    python -m tools.defect_lint                      # lint; exit 1 on any violation
    python -m tools.defect_lint --report             # print the ledger, then lint
    python -m tools.defect_lint --report --phase 4   # ... one phase
    python -m tools.defect_lint --debt --check-escalation
    python -m tools.defect_lint --debt --assert-empty        # section 9.4, run at G-14
    python -m tools.defect_lint --merge-inbox --phase 4
    python -m tools.defect_lint --commit <sha> [--id D-04-002]   # AG-1

WHAT THIS TOOL WILL NOT DO
--------------------------
It never writes `DEFECTS.md`. Section 2 gives that act to the triager, and a
linter that edits the ledger it validates can make itself pass. `--merge-inbox`
prints rows to paste; it does not paste them.

It does not soften a rule because the current ledger fails it. A linter that
matches whatever the file happens to contain asserts nothing, which is the
`L-VAC` failure (section 3) applied to the ledger itself.

READING THE COUNT LINE
----------------------
Section 11.3 fixes the last line of `make defects PHASE=<N>`:

    OPEN BLOCKER: 0  OPEN MAJOR: 0  OPEN MINOR: 2  CARRIED: 1  REJECTED: 4

"OPEN" means **blocks a gate**, so it counts `OPEN`, `TRIAGED`, `INCOMPLETE`,
`IN_FIX` and `AWAITING_REVERIFY` alike: section 7.4 is explicit that
`AWAITING_REVERIFY` "blocks the gate exactly as OPEN does", and section 6.3 says
the same of `INCOMPLETE`. `CARRIED` counts open rows in `CARRIED_DEBT.md` when
that ledger exists and `Status: CARRIED` rows otherwise. `REJECTED` counts
`REJECTED.md` rows plus any `Status: REJECTED` row still sitting in `DEFECTS.md`.

HOW A RECORD IS READ
--------------------
Section 5.2 specifies a ten-column table and section 5.3 a detail block beneath
each row. Real ledgers split the fields between the two - a compact index table
with the long fields in the block below. This parser therefore reads **both** and
takes their union: a field satisfied in either place is satisfied. That is not a
relaxation of the schema, it is the schema applied to the record rather than to a
column layout. What it will not do is invent a field that appears in neither.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFECTS_MD = REPO_ROOT / "ops" / "defects" / "DEFECTS.md"
REJECTED_MD = REPO_ROOT / "ops" / "defects" / "REJECTED.md"
CARRIED_DEBT_MD = REPO_ROOT / "ops" / "defects" / "CARRIED_DEBT.md"
INBOX_DIR = REPO_ROOT / "ops" / "defects" / "inbox"

# --- the closed sets, quoted ---------------------------------------------------

# Section 5.2, `Status`.
STATUSES = (
    "OPEN",
    "TRIAGED",
    "INCOMPLETE",
    "IN_FIX",
    "AWAITING_REVERIFY",
    "CLOSED",
    "CARRIED",
    "REJECTED",
)
# Statuses that block a gate. Sections 6.3 and 7.4.
BLOCKING_STATUSES = frozenset({"OPEN", "TRIAGED", "INCOMPLETE", "IN_FIX", "AWAITING_REVERIFY"})

# Section 3, the lens roster. This section is the lens authority.
LENSES = frozenset({"L-INV", "L-BND", "L-VAC", "L-DRIFT", "L-RENDER", "L-TIME"})

# Section 4.1, the ordered decision rule. The severity carries the rule that decided it.
SEVERITIES = frozenset({"BLOCKER", "MAJOR", "MINOR"})
SEVERITY_RULES = frozenset({"B1", "B2", "B3", "B4", "M1", "M2", "M3", "m1"})

# Section 8.1, `Basis` is a closed set.
REJECTION_BASES = frozenset(
    {
        "SPEC_SAYS_OTHERWISE",
        "WORKING_AS_DESIGNED",
        "NOT_REPRODUCIBLE",
        "OUT_OF_SCOPE",
        "ALREADY_KNOWN_DEBT",
    }
)

DEFECT_ID = re.compile(r"\bD-(?P<phase>\d{2})-(?P<seq>\d{3})\b")
REJECTION_ID = re.compile(r"\bR-\d{2}-\d{3}\b")
DEBT_ID = re.compile(r"\bC-\d{2}-\d{3}\b")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GATE_ID = re.compile(r"^(G\d{1,2}\.\d+[a-z]?|S\d{1,2})$")
PYTEST_NODE = re.compile(r"::")
EMPTY_MARKERS = frozenset({"", "-", "--", "—", "n/a", "na", "tbd", "none", "?"})

# Phrases section 5.2 rules out for `Verifying assertion`: "A thing you can run
# ... Never 'manual check', never 'reviewed'."
UNRUNNABLE_PHRASES = ("manual", "reviewed", "by inspection", "eyeball", "looks", "verified by")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"  {self.rule}  {self.where}\n        {self.message}"


@dataclass
class Record:
    """One defect, read from the table row and its detail block together."""

    defect_id: str
    fields: dict[str, str] = field(default_factory=dict)
    reproduction: str | None = None
    has_detail_block: bool = False
    row_line: int = 0
    detail_line: int = 0

    def get(self, key: str) -> str:
        return self.fields.get(key, "").strip()

    @property
    def status(self) -> str:
        return _normalise_status(self.get("status"))

    @property
    def duplicate_target(self) -> str | None:
        raw = self.get("status")
        match = DEFECT_ID.search(raw)
        if raw.upper().startswith("DUPLICATE") and match:
            return match.group(0)
        return None


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


def _clean(cell: str) -> str:
    """Strip the markdown a ledger cell is allowed to carry."""
    text = cell.strip()
    # [D-04-002](#d-04-002) -> D-04-002
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = text.replace("<br>", " ").replace("<br/>", " ")
    return text.strip()


def _normalise_key(key: str) -> str:
    text = _clean(key).lower().rstrip(".:").strip()
    aliases = {
        "severity": "sev",
        "reproduction": "repro",
        "owning-file": "owning file",
        "fix-commit": "fix commit",
        "fix sha": "fix commit",
        "verifying-assertion": "verifying assertion",
        "close proof": "close-proof",
        "closeproof": "close-proof",
    }
    return aliases.get(text, text)


def _normalise_status(raw: str) -> str:
    text = _clean(raw).upper().replace("->", "→")
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("DUPLICATE"):
        return "DUPLICATE"
    return text


def _is_empty(value: str) -> bool:
    return _clean(value).lower() in EMPTY_MARKERS


def parse_tables(text: str) -> list[tuple[list[str], list[tuple[int, dict[str, str]]]]]:
    """Return every markdown pipe table as (header, [(line_no, row)])."""
    tables: list[tuple[list[str], list[tuple[int, dict[str, str]]]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("|") and index + 1 < len(lines):
            separator = lines[index + 1].strip()
            if re.fullmatch(r"\|(\s*:?-{2,}:?\s*\|)+", separator):
                header = [_normalise_key(cell) for cell in _split_row(line)]
                rows: list[tuple[int, dict[str, str]]] = []
                cursor = index + 2
                while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                    cells = _split_row(lines[cursor])
                    if len(cells) >= 2:
                        row = {
                            header[i]: _clean(cells[i]) for i in range(min(len(header), len(cells)))
                        }
                        rows.append((cursor + 1, row))
                    cursor += 1
                tables.append((header, rows))
                index = cursor
                continue
        index += 1
    return tables


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return stripped.split("|")


_BOLD_FIELD = re.compile(r"\*\*(?P<key>[^*\n]+?)\*\*\s*:?\s*(?P<value>[^*\n]*)")


def parse_detail_blocks(text: str) -> dict[str, tuple[int, dict[str, str], str | None]]:
    """Read the section 5.3 detail blocks.

    Both spellings are accepted, because ledgers in the wild use both:

        - **Status:** CLOSED                             (bullet form, section 5.3)
        **Phase** 0 - **Lens** `L-INV` - **Severity** ... (inline form, one line)

    The reproduction is the first fenced code block after a `**Reproduction**`
    marker. Requiring a fence is the mechanical reading of section 5.2's "The
    single command, in backticks" and "A record with no runnable command is not a
    defect record": prose describing a symptom is not a command, and the whole
    point of the field is that the next reader can run it.
    """
    blocks: dict[str, tuple[int, dict[str, str], str | None]] = {}
    lines = text.splitlines()
    headings = [
        (i, line)
        for i, line in enumerate(lines)
        if re.match(r"^#{3,6}\s", line) and DEFECT_ID.search(line)
    ]
    for position, (start, heading) in enumerate(headings):
        match = DEFECT_ID.search(heading)
        if match is None:  # pragma: no cover - guarded by the comprehension
            continue
        defect_id = match.group(0)
        level = len(heading) - len(heading.lstrip("#"))
        end = len(lines)
        for cursor in range(start + 1, len(lines)):
            other = re.match(r"^(#{1,6})\s", lines[cursor])
            if other and len(other.group(1)) <= level:
                end = cursor
                break
        if position + 1 < len(headings):
            end = min(end, headings[position + 1][0])
        body = lines[start + 1 : end]
        blocks[defect_id] = (start + 1, _fields_from_block(body), _reproduction_from_block(body))
    return blocks


def _fields_from_block(body: Sequence[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        for match in _BOLD_FIELD.finditer(stripped):
            key = _normalise_key(match.group("key"))
            value = _clean(match.group("value")).strip(" ·|-")
            # `repro` is deliberately not harvestable from a bold inline field.
            # In the detail block the reproduction is a *section* (section 5.3),
            # and its content is the fenced code beneath the marker. Harvesting
            # the prose that follows `**Reproduction.**` on the same line would
            # let a sentence describing a symptom satisfy the one field section
            # 5.2 says a record cannot exist without: "A record with no runnable
            # command is not a defect record." Prose is exactly what that rule
            # excludes, so it is excluded here.
            if key == "repro":
                continue
            if key and value and key not in fields:
                fields[key] = value
    return fields


def _reproduction_from_block(body: Sequence[str]) -> str | None:
    seen_marker = False
    fence: list[str] | None = None
    for line in body:
        stripped = line.strip()
        if not seen_marker:
            if re.search(r"\*\*reproduction\b", stripped, flags=re.IGNORECASE):
                seen_marker = True
            continue
        if stripped.startswith("```"):
            if fence is None:
                fence = []
                continue
            break
        if fence is not None:
            fence.append(line)
    if fence is None:
        return None
    meaningful = [
        raw.strip().lstrip("$ ").strip()
        for raw in fence
        if raw.strip() and not raw.strip().startswith(("--", "#", "//"))
    ]
    return "\n".join(meaningful) if meaningful else None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_records(path: Path) -> tuple[list[Record], list[Violation]]:
    violations: list[Violation] = []
    if not path.exists():
        violations.append(
            Violation(
                "DL00",
                str(path),
                "the defect ledger does not exist. CANONICAL_DECISIONS.md -> Repository "
                "layout canon names ops/defects/DEFECTS.md as committed execution evidence.",
            )
        )
        return [], violations

    text = path.read_text(encoding="utf-8")
    blocks = parse_detail_blocks(text)
    records: dict[str, Record] = {}

    for header, rows in parse_tables(text):
        if "id" not in header or "status" not in header:
            continue  # not a ledger table
        for line_no, row in rows:
            match = DEFECT_ID.search(row.get("id", ""))
            if match is None:
                raw = row.get("id", "")
                if raw and not _is_empty(raw):
                    violations.append(
                        Violation(
                            "DL01",
                            f"{path.name}:{line_no}",
                            f"{raw!r} is not a well-formed defect id. Section 5.2: "
                            "D-<phase>-<n>, two-digit discovering phase, zero-padded "
                            "three-digit sequence.",
                        )
                    )
                continue
            defect_id = match.group(0)
            if defect_id in records:
                violations.append(
                    Violation(
                        "DL02",
                        f"{path.name}:{line_no}",
                        f"{defect_id} appears more than once. Section 5.2: sequence numbers "
                        "are allocated in triage order and never reused.",
                    )
                )
                continue
            record = Record(defect_id=defect_id, row_line=line_no)
            for key, value in row.items():
                if key != "id" and value and not _is_empty(value):
                    record.fields[key] = value
            records[defect_id] = record

    for defect_id, (line_no, block_fields, reproduction) in blocks.items():
        record = records.get(defect_id)
        if record is None:
            record = Record(defect_id=defect_id, row_line=0)
            records[defect_id] = record
            violations.append(
                Violation(
                    "DL14",
                    f"{path.name}:{line_no}",
                    f"{defect_id} has a detail block but no row in the ledger table. "
                    "Section 5.3: every record in the table has a detail block; a block "
                    "with no row is invisible to `make defects` and to the count line.",
                )
            )
        record.has_detail_block = True
        record.detail_line = line_no
        record.reproduction = reproduction
        for key, value in block_fields.items():
            if key not in record.fields and value and not _is_empty(value):
                record.fields[key] = value

    ordered = sorted(records.values(), key=lambda r: r.defect_id)
    return ordered, violations


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _runnable_assertion(value: str) -> bool:
    text = _clean(value)
    if _is_empty(text):
        return False
    lowered = text.lower()
    if any(phrase in lowered for phrase in UNRUNNABLE_PHRASES):
        return False
    parts = [part.strip() for part in text.split("+")]
    return any(GATE_ID.match(part) or PYTEST_NODE.search(part) for part in parts)


def check_records(records: Iterable[Record], source: str) -> list[Violation]:
    violations: list[Violation] = []
    known = {record.defect_id for record in records}

    for record in records:
        where = f"{source}  {record.defect_id}"

        # DL03 - Phase must equal the phase encoded in the id (section 5.2).
        id_phase = int(record.defect_id.split("-")[1])
        phase_cell = record.get("phase")
        if _is_empty(phase_cell):
            violations.append(
                Violation("DL03", where, "Phase is empty. Section 5.2 makes every field required.")
            )
        else:
            digits = re.search(r"\d+", phase_cell)
            if digits is None or int(digits.group(0)) != id_phase:
                violations.append(
                    Violation(
                        "DL03",
                        where,
                        f"Phase is {phase_cell!r} but the id encodes phase {id_phase:02d}. "
                        "Section 5.2: they must be equal so the table sorts and filters.",
                    )
                )

        # DL04 - Lens, from the section 3 roster.
        lens_cell = record.get("lens")
        if _is_empty(lens_cell):
            violations.append(
                Violation("DL04", where, "Lens is empty. Section 3 makes it a required field.")
            )
        else:
            for lens in (part.strip().upper() for part in lens_cell.split(",")):
                if lens and lens not in LENSES:
                    violations.append(
                        Violation(
                            "DL04",
                            where,
                            f"{lens!r} is not in the section 3 lens roster "
                            f"({', '.join(sorted(LENSES))}).",
                        )
                    )

        # DL05 - Sev carries the rule id that decided it (sections 5.2, 11.2).
        sev_cell = record.get("sev")
        sev_name, sev_rule = _split_severity(sev_cell)
        if sev_name not in SEVERITIES:
            violations.append(
                Violation(
                    "DL05",
                    where,
                    f"Sev is {sev_cell!r}; expected one of {', '.join(sorted(SEVERITIES))}.",
                )
            )
        elif sev_rule is None:
            violations.append(
                Violation(
                    "DL05",
                    where,
                    f"Sev is {sev_cell!r} and carries no rule id. Section 5.2: "
                    "'BLOCKER(B1)'. The rule id is not decoration; it is what makes a "
                    "disputed severity a conversation about a rule rather than about vibes.",
                )
            )
        elif sev_rule not in SEVERITY_RULES:
            violations.append(
                Violation(
                    "DL05",
                    where,
                    f"rule id {sev_rule!r} is not in the section 4.1 ordered rule "
                    f"({', '.join(sorted(SEVERITY_RULES))}).",
                )
            )

        # DL06 - Summary.
        if _is_empty(record.get("summary")):
            violations.append(Violation("DL06", where, "Summary is empty."))

        # DL07 - a reproduction, and a runnable one.
        table_repro = record.get("repro")
        if _is_empty(table_repro) and not record.reproduction:
            violations.append(
                Violation(
                    "DL07",
                    where,
                    "no runnable reproduction. Section 5.2: 'A record with no runnable "
                    "command is not a defect record.' Put the command in the Repro cell "
                    "in backticks, or in a fenced code block under **Reproduction** in "
                    "the detail block. Prose describing the symptom is not a command.",
                )
            )

        # DL08 - exactly one repository-relative path.
        owning = record.get("owning file")
        if _is_empty(owning):
            violations.append(
                Violation(
                    "DL08",
                    where,
                    "Owning file is empty. Section 5.2: if the triager cannot name one, "
                    "the defect is not yet understood and the status stays TRIAGED.",
                )
            )
        elif not _looks_like_path(owning):
            violations.append(
                Violation(
                    "DL08",
                    where,
                    f"Owning file is {owning!r}, which is not a repository-relative path. "
                    "Section 5.2: exactly one path - the file whose change makes the "
                    "reproduction stop reproducing.",
                )
            )

        # DL09 - Status from the closed set.
        status = record.status
        if status not in STATUSES and status != "DUPLICATE":
            violations.append(
                Violation(
                    "DL09",
                    where,
                    f"Status is {record.get('status')!r}, which is not in the section 5.2 "
                    f"closed set ({', '.join(STATUSES)}, DUPLICATE -> D-xx-nnn).",
                )
            )

        # DL13 - a DUPLICATE must point at a record that exists.
        if status == "DUPLICATE":
            target = record.duplicate_target
            if target is None:
                violations.append(Violation("DL13", where, "DUPLICATE with no target id."))
            elif target not in known:
                violations.append(
                    Violation("DL13", where, f"DUPLICATE points at {target}, which does not exist.")
                )

        # DL10 / DL11 / DL12 - what CLOSED costs.
        if status == "CLOSED":
            fix = _clean(record.get("fix commit"))
            if not FULL_SHA.match(fix):
                violations.append(
                    Violation(
                        "DL10",
                        where,
                        f"CLOSED needs a full 40-character fix SHA; found {fix or '<empty>'!r}. "
                        "Section 5.2 rejects a short SHA: gate reports elsewhere in the pack "
                        "use full SHAs and one convention is cheaper than two.",
                    )
                )
            assertion = record.get("verifying assertion")
            if not _runnable_assertion(assertion):
                violations.append(
                    Violation(
                        "DL11",
                        where,
                        f"CLOSED needs a runnable verifying assertion; found "
                        f"{assertion or '<empty>'!r}. Section 5.2: a gate id (G4.2), a "
                        "pytest node id (path::test_name), or both joined by ' + '. "
                        "Never 'manual check', never 'reviewed'.",
                    )
                )
            close_proof = record.get("close-proof")
            if _is_empty(close_proof):
                violations.append(
                    Violation(
                        "DL12",
                        where,
                        "CLOSED with no close-proof line. Section 7.4: 'No close-proof, no "
                        "CLOSED.' Until it is there the status is AWAITING_REVERIFY, which "
                        "blocks the gate exactly as OPEN does. Produce it with "
                        f"`python -m tools.close_proof {record.defect_id}`.",
                    )
                )
            elif "PASS" not in close_proof.upper():
                violations.append(
                    Violation(
                        "DL12",
                        where,
                        f"the close-proof line does not read PASS: {close_proof!r}. A test "
                        "that passes with the fix reverted does not catch this defect.",
                    )
                )

        # DL14 - every row has a detail block; the block is where the record lives.
        if not record.has_detail_block:
            violations.append(
                Violation(
                    "DL14",
                    where,
                    "no detail block. Section 5.3: every record in the table has one, and "
                    "it is where the reproduction actually lives.",
                )
            )

    return violations


def _split_severity(cell: str) -> tuple[str, str | None]:
    text = _clean(cell).strip()
    match = re.match(r"^(?P<name>[A-Za-z]+)\s*\(\s*(?P<rule>[A-Za-z]\d)\s*\)$", text)
    if match:
        return match.group("name").upper(), match.group("rule")
    match = re.match(r"^(?P<name>[A-Za-z]+)\b.*?\b(?P<rule>[BMm]\d)\b", text)
    if match:
        return match.group("name").upper(), match.group("rule")
    return text.upper(), None


def _looks_like_path(value: str) -> bool:
    """One token that names a file, rather than prose describing one.

    The punctuation test below (`/` or `.`) was the whole rule, and it rejected
    `Makefile` — a root-level file with no extension, which is a perfectly good
    repository-relative path and one this repository owns defects in.
    `LICENSE`, `NOTICE` and `Dockerfile` fail identically.

    Asking the filesystem is not a relaxation of `DL08`; it is what `DL08`
    meant. The rule exists to reject "the seed module" where a path belongs,
    and a token that names a real file is exactly what it was asking for.
    Punctuation remains the fallback, because a defect may be owned by a file
    the fix DELETES — `app/retrieval/ann_search.py` was one — and that record
    must stay well-formed after the file is gone.
    """
    text = _clean(value).strip()
    if " " in text and "/" not in text:
        return False
    if len(text.split()) > 1:
        return False
    if (REPO_ROOT / text).exists():
        return True
    return "/" in text or "." in text


# ---------------------------------------------------------------------------
# REJECTED.md and CARRIED_DEBT.md
# ---------------------------------------------------------------------------


def load_simple_rows(path: Path, id_pattern: re.Pattern[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for header, table_rows in parse_tables(path.read_text(encoding="utf-8")):
        if "id" not in header:
            continue
        for _, row in table_rows:
            if id_pattern.search(row.get("id", "")):
                rows.append(row)
    return rows


def check_rejected(rows: Sequence[dict[str, str]]) -> list[Violation]:
    violations: list[Violation] = []
    for row in rows:
        rid = _clean(row.get("id", ""))
        where = f"REJECTED.md  {rid}"
        basis = _clean(row.get("basis", "")).upper()
        if basis not in REJECTION_BASES:
            violations.append(
                Violation(
                    "RJ01",
                    where,
                    f"Basis is {basis or '<empty>'!r}. Section 8.1: Basis is a closed set "
                    f"({', '.join(sorted(REJECTION_BASES))}). 'An open-ended rejection "
                    "reason is a place to write \"looked fine to me\".'",
                )
            )
        if _is_empty(row.get("citation", "")):
            violations.append(
                Violation(
                    "RJ02",
                    where,
                    "Citation is empty. Every basis in section 8.1 must carry one - the "
                    "document and sentence, the decision's location, the failed attempt's "
                    "output and attempt count, the non-goal, or the C-<phase>-<n> id.",
                )
            )
        if _is_empty(row.get("symptom key", "")):
            violations.append(
                Violation(
                    "RJ03",
                    where,
                    "Symptom key is empty. Section 8.2: hunters grep REJECTED.md at the "
                    "start of every round, and a rejection with no symptom key cannot stop "
                    "the re-hunt.",
                )
            )
        if basis == "NOT_REPRODUCIBLE" and not re.search(r"\d+", row.get("citation", "")):
            violations.append(
                Violation(
                    "RJ04",
                    where,
                    "NOT_REPRODUCIBLE with no attempt count. Section 8.3: on anything "
                    "marked concurrency an attempt count below 25 is not a rejection, it "
                    "is an incomplete reproduction attempt.",
                )
            )
    return violations


def check_debt(rows: Sequence[dict[str, str]], *, check_escalation: bool) -> list[Violation]:
    violations: list[Violation] = []
    for row in rows:
        cid = _clean(row.get("id", ""))
        where = f"CARRIED_DEBT.md  {cid}"
        owner = _clean(row.get("owner", ""))
        if _is_empty(owner) or owner.upper() in {"TBD", "TEAM", "FRONTEND", "BACKEND", "TOOLING"}:
            violations.append(
                Violation(
                    "DB01",
                    where,
                    f"Owner is {owner or '<empty>'!r}. Section 9.2: Owner is a name, not a "
                    "role and not TBD. An unowned MINOR is not carriable and blocks the "
                    "gate exactly like a MAJOR.",
                )
            )
        closes_by = _clean(row.get("closes by", ""))
        if not re.match(r"^G-\d{1,2}$", closes_by):
            violations.append(
                Violation(
                    "DB02",
                    where,
                    f"Closes by is {closes_by or '<empty>'!r}. Section 9.2: a gate id, not "
                    "'later' and not 'post-hackathon'.",
                )
            )
        if check_escalation:
            accepted = len(re.findall(r"STILL ACCEPTED", row.get("re-read log", ""), re.IGNORECASE))
            if accepted >= 3:
                violations.append(
                    Violation(
                        "DB03",
                        where,
                        f"marked STILL ACCEPTED at {accepted} consecutive gates. Section "
                        "9.3, the three-gate rule: it is automatically escalated to MAJOR. "
                        "Debt that survives three independent reviews is not debt; it is a "
                        "decision nobody wants to make.",
                    )
                )
    return violations


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(records: Sequence[Record], phase: int | None, out) -> None:
    selected = [r for r in records if phase is None or int(r.defect_id.split("-")[1]) == phase]
    scope = "every phase" if phase is None else f"phase {phase}"
    out.write(f"ops/defects/DEFECTS.md - {len(selected)} record(s), {scope}, grouped by status\n")
    order = [*STATUSES, "DUPLICATE"]
    for status in order:
        group = [r for r in selected if r.status == status]
        if not group:
            continue
        out.write(f"\n{status} ({len(group)})\n")
        for record in group:
            sev = _clean(record.get("sev")) or "SEV?"
            lens = _clean(record.get("lens")) or "LENS?"
            out.write(f"  {record.defect_id}  {sev:<14} {lens:<16} {record.get('summary')}\n")
            owning = record.get("owning file")
            if owning:
                out.write(f"      owning file: {owning}\n")
    unknown = [r for r in selected if r.status not in order]
    if unknown:
        out.write(f"\nUNRECOGNISED STATUS ({len(unknown)})\n")
        for record in unknown:
            out.write(f"  {record.defect_id}  {record.get('status')!r}  {record.get('summary')}\n")
    out.write("\n")


def count_line(
    records: Sequence[Record],
    rejected_rows: Sequence[dict[str, str]],
    debt_rows: Sequence[dict[str, str]],
    phase: int | None = None,
) -> str:
    """The last line section 11.3 fixes, and the reviewer needs before a verdict.

    `phase` scopes the counts, and it must: section 4.3 rejects a gate report
    carrying an open BLOCKER or MAJOR "against its own phase". Unscoped, this
    line answered a different question from the one the rule asks. At G-0 it
    reported 20 open MAJOR when 11 belonged to phase 0 and 9 had been discovered
    in phase 1, so a phase-0 reviewer would read another phase's work as their
    own blockers.

    `print_report` has always filtered; this line did not, and this is the line
    the reviewer pastes into the gate report. Section 11.3 line 452 settles the
    intent: "make defects PHASE=<N> must print OPEN BLOCKER: 0  OPEN MAJOR: 0".
    The scope is now named in the output so the two forms cannot be confused.
    """
    # Same predicate print_report uses (line 771): the phase is encoded in the
    # id, D-<pp>-<nnn>, and DL03 already enforces that the `phase` field agrees
    # with it. Deriving it from the id here keeps the two filters identical, so
    # the body of the report and the line beneath it can never disagree.
    scoped = [r for r in records if phase is None or int(r.defect_id.split("-")[1]) == phase]
    open_records = [r for r in scoped if r.status in BLOCKING_STATUSES]

    def sev_count(name: str) -> int:
        return sum(1 for r in open_records if _split_severity(r.get("sev"))[0] == name)

    if debt_rows:
        carried = sum(
            1 for row in debt_rows if _clean(row.get("status", "")).upper() not in {"CLOSED"}
        )
    else:
        carried = sum(1 for r in scoped if r.status == "CARRIED")
    rejected = len(rejected_rows) + sum(1 for r in scoped if r.status == "REJECTED")
    # The five fields and their order are contract (section 11.3, and every
    # ops/gates/PHASE_NN.md template). The scope is APPENDED, never interleaved,
    # so a reader or a regex keyed on those fields is unaffected.
    scope = "all phases" if phase is None else f"phase {phase}"
    return (
        f"OPEN BLOCKER: {sev_count('BLOCKER')}  "
        f"OPEN MAJOR: {sev_count('MAJOR')}  "
        f"OPEN MINOR: {sev_count('MINOR')}  "
        f"CARRIED: {carried}  "
        f"REJECTED: {rejected}"
        f"   [{scope}]"
    )


def print_debt(rows: Sequence[dict[str, str]], out, *, ledger_exists: bool = True) -> None:
    """Section 9.3: this output is pasted verbatim into every gate report.

    ``ledger_exists`` separates "the ledger says nothing is carried" from "there
    is no ledger". Both used to print the identical `0 carried items`, so the
    line a reviewer pastes into a gate report as proof of no carried debt was
    produced by a tool that had opened no file (``D-00-005``).
    """
    open_rows = [row for row in rows if _clean(row.get("status", "")).upper() != "CLOSED"]
    if not ledger_exists:
        out.write("0 carried items (no CARRIED_DEBT.md ledger exists yet - nothing was read)\n")
        return
    if not open_rows:
        # Section 9.3: "An empty ledger prints `0 carried items` and that line must
        # still appear in the report, because a missing section and a forgotten
        # section are indistinguishable to a reader."
        out.write("0 carried items\n")
        return
    out.write(f"{len(open_rows)} carried item(s) - re-read in full at this gate (section 9.3)\n\n")
    for row in open_rows:
        out.write(
            f"  {_clean(row.get('id', '?'))}  (from {_clean(row.get('source defect', '?'))})\n"
        )
        out.write(f"      {_clean(row.get('description', ''))}\n")
        out.write(
            f"      owner: {_clean(row.get('owner', '?'))}   "
            f"closes by: {_clean(row.get('closes by', '?'))}   "
            f"accepted at: {_clean(row.get('accepted at', '?'))}\n"
        )
        out.write(f"      re-read log: {_clean(row.get('re-read log', ''))}\n")
        out.write(
            f"      if it does not close: {_clean(row.get('consequence if it does not close', ''))}\n"
        )
    out.write(
        "\nMark each: STILL ACCEPTED (with one sentence saying why it is still the right\n"
        "call at this gate), ESCALATED (it becomes a MAJOR and blocks this gate), or\n"
        "CLOSED (with the commit).\n"
    )


# ---------------------------------------------------------------------------
# --merge-inbox
# ---------------------------------------------------------------------------

_INBOX_FINDING = re.compile(r"^##\s+(?P<fid>F-[A-Z\-]+-\d+)\s*$")


def merge_inbox(phase: int, records: Sequence[Record], out) -> int:
    """Read the round's hunter files and print rows for the triager to paste.

    This never writes DEFECTS.md. Section 2 gives the merge to the triager, and a
    tool that both merges and validates can satisfy its own check.
    """
    pattern = f"G-{phase:02d}.*.md"
    files = sorted(INBOX_DIR.glob(pattern)) if INBOX_DIR.exists() else []
    if not files:
        out.write(f"no hunter files matching ops/defects/inbox/{pattern}\n")
        out.write(
            "Section 3.2: a hunter writes one file per lens per round. Zero files means\n"
            "either the round has not run or its output was not kept - and section 3.2 is\n"
            "explicit that inbox files are kept after the merge, because they are the\n"
            "record of what was looked at.\n"
        )
        return 1

    findings: list[tuple[str, str, dict[str, str]]] = []
    for path in files:
        lens = path.stem.split(".", 1)[1] if "." in path.stem else "L-?"
        current: str | None = None
        payload: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            heading = _INBOX_FINDING.match(line.strip())
            if heading:
                if current:
                    findings.append((current, lens, payload))
                current = heading.group("fid")
                payload = {}
                continue
            match = re.match(r"^\s*(summary|repro|observed|expected|file)\s*:\s*(.*)$", line)
            if match and current:
                payload[match.group(1)] = match.group(2).strip()
        if current:
            findings.append((current, lens, payload))

    used = {
        int(r.defect_id.split("-")[2]) for r in records if int(r.defect_id.split("-")[1]) == phase
    }
    next_seq = max(used) + 1 if used else 1

    out.write(f"triage round G-{phase:02d}: {len(findings)} finding(s) in {len(files)} file(s)\n\n")

    # Section 6.2: the same defect regularly arrives twice, and the second lens is
    # signal about blast radius. Collisions are reported, never silently merged.
    by_key: dict[str, list[tuple[str, str, dict[str, str]]]] = {}
    for finding in findings:
        key = re.sub(r"[^a-z0-9]+", " ", finding[2].get("summary", "").lower()).strip()
        by_key.setdefault(key, []).append(finding)

    for key, group in by_key.items():
        if len(group) > 1:
            lenses = ", ".join(sorted({lens for _, lens, _ in group}))
            ids = ", ".join(fid for fid, _, _ in group)
            out.write(
                f"COLLISION  {ids}  lenses [{lenses}]\n"
                f"           {key}\n"
                "           Section 6.2: record BOTH lenses on the merged row, "
                "comma-separated. Dropping the second loses the blast-radius signal.\n\n"
            )

    out.write("Rows to paste into ops/defects/DEFECTS.md, after applying the section 4.1\n")
    out.write("ordered severity rule to each reproduction. Severity is not guessed here:\n")
    out.write("this tool has not read the transcripts.\n\n")
    out.write(
        "| ID | Phase | Lens | Sev | Summary | Repro | Owning file | Status | Fix commit | Verifying assertion |\n"
    )
    out.write("|---|---|---|---|---|---|---|---|---|---|\n")
    for offset, (fid, lens, payload) in enumerate(findings):
        new_id = f"D-{phase:02d}-{next_seq + offset:03d}"
        repro = payload.get("repro", "")
        status = "TRIAGED" if repro else "INCOMPLETE"
        out.write(
            f"| [{new_id}](#{new_id.lower()}) | {phase} | {lens} | `<SEV(rule)>` | "
            f"{payload.get('summary', '')} | `{repro}` | "
            f"`{payload.get('file', '')}` | {status} | — | — |   <!-- {fid} -->\n"
        )
    out.write(
        "\nA finding with no repro line is filed INCOMPLETE and returned to the hunter\n"
        "(section 6.3). Section 4.2 rule 2: insufficient reproduction goes back, never\n"
        "down - it does not become MAJOR because the BLOCKER questions were unanswerable.\n"
    )
    return 0


# ---------------------------------------------------------------------------
# --commit: AG-1, the assertion-count rule (section 10.1 detector 2 and 3)
# ---------------------------------------------------------------------------

_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|[^/]*_test\.py$|\.spec\.[tj]sx?$")
_ASSERTION = re.compile(r"\bassert\b|pytest\.raises|\.toBe\b|\bexpect\(")


def _git(args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def check_commit(sha: str, record: Record | None, out) -> list[Violation]:
    violations: list[Violation] = []
    try:
        changed = [
            p for p in _git(["show", "--pretty=format:", "--name-only", sha]).splitlines() if p
        ]
        message = _git(["show", "-s", "--format=%B", sha])
    except RuntimeError as exc:
        return [Violation("AG1", sha, str(exc))]

    test_files = [p for p in changed if _TEST_PATH.search(p)]
    non_test_files = [p for p in changed if not _TEST_PATH.search(p)]

    added = removed = 0
    if test_files:
        diff = _git(["show", "--unified=0", "--pretty=format:", sha, "--", *test_files])
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++") and _ASSERTION.search(line):
                added += 1
            elif line.startswith("-") and not line.startswith("---") and _ASSERTION.search(line):
                removed += 1

    net = added - removed
    out.write(f"AG-1 assertion count for {sha[:12]}: +{added} -{removed} net {net:+d}\n")
    out.write(f"          test files: {len(test_files)}   non-test files: {len(non_test_files)}\n")

    if net < 0 and "Test-Weakening-Justification:" not in message:
        violations.append(
            Violation(
                "AG1",
                sha,
                f"the fixing commit removes more assertions than it adds (net {net:+d}) and "
                "carries no `Test-Weakening-Justification:` trailer with a second reviewer's "
                "initials. Section 10.1 detector 2: an exact reason_code becoming `is not "
                "None`, or `assert retry_count == 0` becoming `>= 0`, is how a defect closes "
                "without being fixed.",
            )
        )

    # Detector 3 - no test-only closes outside M2.
    if test_files and not non_test_files:
        rule = _split_severity(record.get("sev"))[1] if record else None
        if rule != "M2":
            violations.append(
                Violation(
                    "AG1",
                    sha,
                    "the fixing commit touches only test files. Section 7.2: that is either "
                    "a MAJOR-M2 vacuity defect, where the test IS the owning file, or an "
                    "instance of AG-1. There is no third case. "
                    + (
                        f"This record's rule id is {rule or '<none>'}."
                        if record
                        else "Pass --id <D-xx-nnn> so the rule id can be read."
                    ),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.defect_lint",
        description=(
            "Validate ops/defects/DEFECTS.md against EXECUTION/72_DEFECT_PROTOCOL.md "
            "sections 5.2 and 11.2, and print it for a gate reviewer."
        ),
    )
    parser.add_argument("--report", action="store_true", help="print the ledger grouped by status")
    parser.add_argument("--phase", type=int, default=None, help="restrict --report/--merge-inbox")
    parser.add_argument("--debt", action="store_true", help="print the open carried-debt ledger")
    parser.add_argument(
        "--check-escalation", action="store_true", help="section 9.3 three-gate rule"
    )
    parser.add_argument("--assert-empty", action="store_true", help="section 9.4, run at G-14")
    parser.add_argument(
        "--merge-inbox", action="store_true", help="print rows from the round's inbox"
    )
    parser.add_argument(
        "--commit", default=None, help="AG-1 assertion-count rule over a fix commit"
    )
    parser.add_argument("--id", default=None, help="the defect id --commit is closing")
    parser.add_argument("--defects-file", type=Path, default=DEFECTS_MD)
    parser.add_argument("--rejected-file", type=Path, default=REJECTED_MD)
    parser.add_argument("--debt-file", type=Path, default=CARRIED_DEBT_MD)
    args = parser.parse_args(argv)

    out = sys.stdout
    records, violations = load_records(args.defects_file)
    rejected_rows = load_simple_rows(args.rejected_file, REJECTION_ID)
    debt_rows = load_simple_rows(args.debt_file, DEBT_ID)

    # The "does not exist yet" notes are written BEFORE the --debt branch
    # returns. `make debt` is a binding gate precondition (section 11.3) whose
    # output line is pasted verbatim into the gate report (section 9.3), and
    # until D-00-005 that branch returned at the bottom of this `if`, before
    # the notes below - so a missing CARRIED_DEBT.md printed "0 carried items"
    # and "0 violations" and exited 0. The reviewer read "no carried debt" from
    # a tool that had read no file.
    if not args.rejected_file.exists() and not args.debt:
        out.write(
            f"note: {args.rejected_file} does not exist yet. Section 8.1 creates it at the "
            "first triage round that rejects a finding; there is nothing to validate until "
            "then, so this is a note and not a violation.\n"
        )
    if not args.debt_file.exists():
        out.write(
            f"note: {args.debt_file} does not exist yet. Section 9.2 creates it at the first "
            "gate that accepts a MINOR. The carried-debt ledger is ABSENT, which is not the "
            "same claim as 'the carried-debt ledger is empty'.\n"
        )

    if args.debt:
        print_debt(debt_rows, out, ledger_exists=args.debt_file.exists())
        debt_violations = check_debt(debt_rows, check_escalation=args.check_escalation)
        open_rows = [r for r in debt_rows if _clean(r.get("status", "")).upper() != "CLOSED"]
        if args.assert_empty and open_rows:
            debt_violations.append(
                Violation(
                    "DB04",
                    "CARRIED_DEBT.md",
                    f"{len(open_rows)} item(s) still open. Section 9.4: nothing carries past "
                    "G-14. Each remaining item either closes before G-15 opens, or is "
                    "converted to a disclosed limitation in README.md or SUBMISSION.md, "
                    "both of which S7 greps for.",
                )
            )
        return _finish(debt_violations, out)

    if args.merge_inbox:
        if args.phase is None:
            out.write("--merge-inbox needs --phase <N>\n")
            return 2
        return merge_inbox(args.phase, records, out)

    if args.commit:
        record = next((r for r in records if r.defect_id == args.id), None) if args.id else None
        if args.id and record is None:
            out.write(f"{args.id} is not in {args.defects_file}\n")
            return 2
        return _finish(check_commit(args.commit, record, out), out)

    if args.report:
        print_report(records, args.phase, out)

    violations.extend(check_records(records, args.defects_file.name))
    violations.extend(check_rejected(rejected_rows))
    violations.extend(check_debt(debt_rows, check_escalation=args.check_escalation))

    if args.report:
        out.write(count_line(records, rejected_rows, debt_rows, args.phase) + "\n")

    return _finish(violations, out)


def _finish(violations: Sequence[Violation], out) -> int:
    if not violations:
        out.write("defect_lint: 0 violations\n")
        return 0
    out.write(f"\ndefect_lint: {len(violations)} violation(s)\n\n")
    for violation in violations:
        out.write(f"{violation}\n\n")
    out.write(
        "These are schema violations, not opinions: every rule id above cites the section "
        "of EXECUTION/72_DEFECT_PROTOCOL.md that requires it. Fix the ledger, not the "
        "linter - a linter relaxed to match a malformed ledger asserts nothing, which is "
        "the L-VAC failure applied to the ledger itself.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
