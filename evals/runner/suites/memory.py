"""MEM -- contradiction as a first-class row, and a deadline as an event.

The product claims (``docs/00_PRODUCT.md`` section 2.2)
-------------------------------------------------------
    "Contradiction is resolved by cosine similarity."  (what RAG does)
    "There is no write path, so there is no state to be wrong about -- and no
    state to be right about either."

Provenance's answer to the first is a ``conflicts`` row with a type, a
severity and a disposition; to the second, a transactional canonical state
written by one deterministic Kernel.

**This suite measures neither, and says so.** What it does instead is count the
rows that would have to exist for the measurement to be possible, so that its
``CANNOT RUN`` verdicts are evidenced rather than asserted. A blocker written
in prose is a guess with a comment on it; a blocker with a row count beside it
is a fact a reader can re-check.

Why the conflict suite cannot run
----------------------------------
``conflicts`` is empty **by design**, not by omission.
``CANONICAL_DECISIONS.md`` fixes the hero conflict as
``VALUE_CONFLICT / BALANCE / NEEDS_HUMAN / HIGH``, produced when the June
invoice arrives -- and ``demo/artifacts/northline-june-invoice.eml`` is
deliberately absent from ``source_artifacts`` because **the demo ingests it
live**. Seeding the conflict would make the demo's first reveal a replay of a
row that was already there.

So a conflict-detection eval needs the live ingestion to have run against a
database it is allowed to write to. Manufacturing one here would mean writing
to ``evidence_items`` and the Kernel tables, which this harness must not do:
the corpus is what ``db/seeds/MANIFEST.json`` asserts exact row counts over and
other lanes are verifying against it.

Why the prospective-memory suite cannot run
--------------------------------------------
Two armed triggers exist. STATUS.md records that
``commit_trigger_evaluation`` is built and **never verified against the
cluster**, and that the curated proposals give every commitment
``local_id="cm_001"`` (``scripts/seed/proposals.py:924``), so a
binding-recovery rule keyed on ``local_id == "cm_<binding>"`` refuses both
curated triggers. Scoring a wake would mean firing one, which writes.
"""

from __future__ import annotations

from typing import Any, Final

import psycopg

from evals.runner.corpus import scalar
from evals.runner.hero import LIVE_INGEST_ARTIFACT
from evals.runner.verdict import Check, Metric, SuiteResult, Verdict

__all__ = ["CLAIM", "SUITE_ID", "run_suite"]

SUITE_ID: Final[str] = "MEM"
CLAIM: Final[str] = (
    "00_PRODUCT.md section 2.2: 'Contradiction is resolved by cosine "
    "similarity' and 'there is no write path, so there is no state to be wrong "
    "about -- and no state to be right about either.'"
)
COMMAND: Final[str] = "make evals   (python -m evals.runner --suite memory)"


async def run_suite(conn: psycopg.AsyncConnection[Any]) -> SuiteResult:
    conflicts = int(await scalar(conn, "SELECT count(*) FROM conflicts") or 0)
    armed = int(
        await scalar(conn, "SELECT count(*) FROM prospective_triggers WHERE state = 'ARMED'") or 0
    )
    triggers = int(await scalar(conn, "SELECT count(*) FROM prospective_triggers") or 0)
    fired = int(
        await scalar(
            conn,
            "SELECT count(*) FROM prospective_triggers WHERE state <> 'ARMED'",
        )
        or 0
    )
    beliefs = int(await scalar(conn, "SELECT count(*) FROM belief_versions") or 0)
    commitments = int(await scalar(conn, "SELECT count(*) FROM commitments") or 0)
    transitions = int(await scalar(conn, "SELECT count(*) FROM state_transitions") or 0)
    june = int(
        await scalar(
            conn,
            "SELECT count(*) FROM source_artifacts WHERE s3_key LIKE %s",
            (f"%{LIVE_INGEST_ARTIFACT}",),
        )
        or 0
    )

    checks = (
        Check(
            check_id="MEM-01",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                f"conflict detection is not scored. Measured: conflicts={conflicts}, "
                f"and source_artifacts rows for {LIVE_INGEST_ARTIFACT}={june}. The "
                f"hero conflict is produced by ingesting that artifact, which the "
                f"demo does live; seeding it would make the first reveal a replay. "
                f"Needs: the live ingestion run against a writable database, then "
                f"an assertion on conflict_type=VALUE_CONFLICT, status=NEEDS_HUMAN, "
                f"severity=HIGH, requires_human=true (CANONICAL_DECISIONS.md, hero "
                f"commit canon). This harness is read-only and will not write to "
                f"evidence_items or any Kernel table to manufacture one."
            ),
            command=COMMAND,
        ),
        Check(
            check_id="MEM-02",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                f"prospective memory is not scored. Measured: "
                f"prospective_triggers={triggers} ({armed} ARMED, {fired} not "
                f"ARMED). Scoring a wake means firing one, which writes "
                f"kernel_decisions, state_transitions and outbox_events. "
                f"STATUS.md also records commit_trigger_evaluation as built and "
                f"never verified against the cluster, and every curated "
                f"commitment carrying local_id='cm_001' "
                f"(scripts/seed/proposals.py:924), which refuses binding "
                f"recovery for both curated triggers. Needs: a disposable "
                f"database or an explicit write authorisation."
            ),
            command=COMMAND,
        ),
        Check(
            check_id="MEM-03",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                "the 51 labelled scenarios of docs/quality/22_EVAL_DATASETS.md "
                "section 4 do not exist. evals/datasets/memory_cases.jsonl is "
                "absent, and Rule E1 requires each expect block to be written "
                "by hand from the specs before the implementation is tuned -- "
                "so it cannot be generated from the running system without "
                "becoming a regression snapshot. Needs: 51 hand-authored "
                "scenarios and a KERNEL_REPLAY mode."
            ),
            command=COMMAND,
        ),
    )

    metrics = (
        Metric.measured("conflicts_in_corpus", float(conflicts), COMMAND, unit="rows"),
        Metric.measured("triggers_armed", float(armed), COMMAND, unit="rows"),
        Metric.measured("belief_versions", float(beliefs), COMMAND, unit="rows"),
        Metric.measured("commitments", float(commitments), COMMAND, unit="rows"),
        Metric.measured("state_transitions", float(transitions), COMMAND, unit="rows"),
        Metric.unmeasured(
            "conflict_detection_accuracy",
            "no conflict has been produced; the hero conflict is created by the "
            "live demo ingestion and this harness does not write",
            COMMAND,
        ),
        Metric.unmeasured(
            "trigger_wake_accuracy",
            "scoring a wake means firing one, which writes to the Kernel tables "
            "db/seeds/MANIFEST.json asserts exact row counts over",
            COMMAND,
        ),
        Metric.unmeasured(
            "scenario_pass_rate",
            "evals/datasets/memory_cases.jsonl does not exist; the 51 scenarios " "are unwritten",
            COMMAND,
        ),
    )

    return SuiteResult(
        suite_id=SUITE_ID,
        title="contradiction as a row, and a deadline as an event",
        claim=CLAIM,
        checks=checks,
        metrics=metrics,
        exclusions=(
            "every check in this suite is CANNOT RUN. The row counts above are "
            "printed so the blockers are evidenced rather than asserted; none "
            "of them is a score for the capability.",
        ),
    )
