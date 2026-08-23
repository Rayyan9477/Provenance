"""``python -m evals.runner`` -- run the suites and print a scored report.

Exit codes
----------
``0``  every suite passed, or could not run. ``CANNOT RUN`` is not ``FAIL``.
``1``  at least one check FAILED: a measured behaviour contradicts a claim.
``2``  the harness itself could not start -- no database, no ``.env``, wrong
       role. This is deliberately a **third** code. A harness that could not
       connect exiting ``1`` is ``D-00-005`` in the runner rather than in the
       report, and a gate would read it as a failed capability.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Final

from evals.runner import corpus, report
from evals.runner.suites import extraction, memory, retraction, retrieval
from evals.runner.verdict import RunReport, SuiteResult

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
REPORT_DIR: Final[Path] = REPO_ROOT / "evals" / "reports"

SUITES: Final[tuple[str, ...]] = ("retrieval", "retraction", "extraction", "memory")

#: Exit code for "the harness could not start", distinct from a failed check.
EXIT_CANNOT_START: Final[int] = 2


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = completed.stdout.strip()
    return sha or "unknown (nothing committed)"


async def _collect(selected: tuple[str, ...]) -> tuple[RunReport, str]:
    conn = await corpus.connect()
    try:
        census = await corpus.read_census(conn)
        world = await corpus.load_hero_world(conn)
        notes = [
            f"corpus counted at query time: {census.evidence_total} evidence rows "
            f"({census.evidence_active} ACTIVE, {census.evidence_non_active} not), "
            f"{census.hero_artifacts} hero artifacts, "
            f"{census.decoy_artifacts} hero decoys, "
            f"{census.evidence_without_embedding} without an embedding.",
            f"embedding space: {census.embedding_model} version "
            f"{census.embedding_version}, {census.embedding_versions} distinct "
            f"version(s) in the corpus. Nothing here embeds text; every query "
            f"vector is a vector already stored in this corpus.",
            "the harness holds a READ ONLY session and issues only SELECT. It "
            "writes to no table, canonical or otherwise.",
        ]
        suites: list[SuiteResult] = []
        if "retrieval" in selected:
            suites.append(
                await retrieval.run_suite(conn, world, embedding_version=census.embedding_version)
            )
        if "retraction" in selected:
            suites.append(
                await retraction.run_suite(conn, world, embedding_version=census.embedding_version)
            )
        if "extraction" in selected:
            suites.append(await extraction.run_suite(conn, world))
        if "memory" in selected:
            suites.append(await memory.run_suite(conn))
        run = RunReport(
            suites=tuple(suites),
            started_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            git_sha=_git_sha(),
            database=corpus.EVAL_DATABASE,
            notes=tuple(notes),
        )
        return run, census.embedding_version
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.runner",
        description="Run the Provenance evaluation suites and print a scored report.",
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=SUITES,
        help="run only this suite; repeatable. Default: all four.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=REPORT_DIR / "latest.json",
        help="where to write the machine-readable report (default: evals/reports/latest.json)",
    )
    args = parser.parse_args(argv)
    selected = tuple(args.suite) if args.suite else SUITES

    # The Windows console defaults to cp1252 and mangles any non-ASCII
    # character that reaches it from a message this module did not write --
    # an upstream exception, a document quotation. A report is evidence, and
    # mojibake in evidence invites the reader to distrust the numbers too.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    try:
        run, _ = corpus.run(_collect(selected))
    except Exception as error:
        sys.stderr.write(
            "\n  The eval harness could not start, so NOTHING was measured.\n"
            f"  {type(error).__name__}: {error}\n\n"
            "  Exiting 2, not 1. A harness that could not connect exiting 1 is\n"
            "  the same substitution D-00-005 records: an inability to measure\n"
            "  reported as a failed capability, which forces a fallback for a\n"
            "  capability nobody looked at.\n\n"
            "  Needs: a .env carrying the pv_app_reader_writer DSN, and the\n"
            "  provenance database (NOT provenance_ci) reachable.\n\n"
        )
        traceback.print_exc(file=sys.stderr)
        return EXIT_CANNOT_START

    sys.stdout.write(report.render_text(run) + "\n")

    destination: Path = args.json
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report.render_json(run), encoding="utf-8")
    sys.stdout.write(f"\n  machine-readable report: {destination}\n")
    return run.exit_code


def _entry() -> Any:  # pragma: no cover - module entry point
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    _entry()
