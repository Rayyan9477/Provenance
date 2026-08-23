"""EXT -- an invoice is a claim by an interested party, not a fact.

The product claim (``docs/00_PRODUCT.md`` section 2.2)
------------------------------------------------------
    "The invoice is treated as a *fact* because it is in the index, not as a
    *claim by an interested party*."

and section 2.3, which is the same sentence with the arithmetic in it:

    "The invoice arriving does not make $186 owed. It makes $186 **claimed**,
    by a party with a financial interest, about a period that begins one day
    after a termination the same party confirmed in writing."

Two independent measurements
-----------------------------
``EXT-01`` scores the model's extraction against hand-written gold, from
``ops/agent-graph-live-run.txt`` -- a recorded live Gemini run. **Three of the
34 hero artifacts are covered**, because three is how many that run walked. The
other 31 are ``EXT-02``: ``CANNOT RUN``, naming the 31 live calls they need. A
mean over three artifacts is reported with its n beside it and should not be
read as a rate.

``EXT-03`` and ``EXT-04`` are corpus-wide and need no model at all. They ask
whether the record, as committed, keeps the distinction: does every assertion
made by a counterparty about value or status carry an interested-party
``claim_kind``, and does every claim cite the evidence it came from?

That split matters. ``EXT-01`` measures the model. ``EXT-03`` measures the
system. A model that types an invoice correctly on a good day and a schema that
cannot represent the difference at all are very different failures, and only
the second one is architectural.
"""

from __future__ import annotations

from typing import Any, Final

import psycopg

from evals.runner.corpus import HeroWorld, scalar
from evals.runner.gold import load_gold, score_extraction
from evals.runner.hero import HERO_KEY_PREFIX
from evals.runner.transcript import LIVE_RUN_TRANSCRIPT, parse_transcript
from evals.runner.verdict import Check, Metric, SuiteResult, Verdict

__all__ = ["CLAIM", "INTERESTED_PARTY_KINDS", "SUITE_ID", "run_suite"]

SUITE_ID: Final[str] = "EXT"
CLAIM: Final[str] = (
    "00_PRODUCT.md section 2.2: 'The invoice is treated as a fact because it is "
    "in the index, not as a claim by an interested party.'"
)
COMMAND: Final[str] = "make evals   (python -m evals.runner --suite extraction)"

#: The ``ClaimKind`` members that record an assertion as an interested party's,
#: rather than as the record's own observation. ``OBSERVATION`` and
#: ``INFERENCE`` are deliberately absent: they are what an invoice must not be.
INTERESTED_PARTY_KINDS: Final[tuple[str, ...]] = (
    "COUNTERPARTY_CLAIM",
    "COMMITMENT_CLAIM",
    "FULFILLMENT_CLAIM",
    "POLICY_TERM",
    "CORRECTION",
)

#: Evidence types that assert a value or a status -- the ones where treating an
#: interested party's word as a fact actually changes what the system believes.
#: A ``STATEMENT`` or a ``CONFIRMATION`` carries no figure to be wrong about.
ASSERTIVE_EVIDENCE_TYPES: Final[tuple[str, ...]] = (
    "INVOICE_LINE",
    "AMOUNT_ASSERTION",
    "SERVICE_STATUS_ASSERTION",
    "PAYMENT_RECORD",
    "COMMITMENT_STATEMENT",
    "POLICY_TERM_TEXT",
    "RECEIPT",
    "DATE_ASSERTION",
)

#: The hero prefix is bound as a parameter rather than interpolated. A literal
#: ``%`` in a statement that also carries placeholders is parsed as a
#: placeholder by psycopg and the statement never reaches the planner.
TYPING_SQL: Final[str] = """
SELECT c.claim_kind, count(*)
FROM claims c
JOIN evidence_items e ON e.id = c.evidence_id
JOIN source_artifacts a ON a.id = e.artifact_id
WHERE a.s3_key LIKE %(hero)s
  AND c.actor_type = 'COUNTERPARTY'
  AND e.evidence_type = ANY(%(types)s)
GROUP BY 1 ORDER BY 2 DESC
"""

UNGROUNDED_CLAIMS_SQL: Final[str] = """
SELECT count(*) FROM claims WHERE evidence_id IS NULL
"""

COUNTERPARTY_AS_USER_SQL: Final[str] = """
SELECT count(*) FROM claims
WHERE actor_type = 'COUNTERPARTY' AND claim_kind = 'USER_CLAIM'
"""


async def run_suite(
    conn: psycopg.AsyncConnection[Any],
    world: HeroWorld,
) -> SuiteResult:
    checks: list[Check] = []
    metrics: list[Metric] = []
    exclusions: list[str] = []

    # ---- EXT-01 / EXT-02: the recorded live run ----------------------------
    labels = load_gold()
    try:
        recorded = parse_transcript()
    except FileNotFoundError as error:
        recorded = {}
        checks.append(
            Check(
                check_id="EXT-01",
                verdict=Verdict.CANNOT_RUN,
                detail=str(error),
                command=COMMAND,
            )
        )

    scored = {name: label for name, label in labels.items() if name in recorded}
    unscored = sorted(set(labels) - set(recorded))

    if scored:
        field_scores = [
            score
            for name, label in sorted(scored.items())
            for score in score_extraction(label, recorded[name])
        ]
        wrong = [score for score in field_scores if not score.ok]
        artifacts_exact = sum(
            1
            for name in scored
            if all(score.ok for score in score_extraction(scored[name], recorded[name]))
        )
        checks.append(
            Check(
                check_id="EXT-01",
                verdict=Verdict.PASS if not wrong else Verdict.FAIL,
                detail=(
                    f"{len(field_scores) - len(wrong)}/{len(field_scores)} gold "
                    f"expectations met across {len(scored)} artifact(s) with a "
                    f"recorded extraction; {artifacts_exact}/{len(scored)} "
                    f"artifacts met every expectation"
                    + (
                        "; missed: "
                        + "; ".join(
                            f"{s.artifact}.{s.operator} expected {s.expected!r} but {s.detail}"
                            for s in wrong
                        )
                        if wrong
                        else ""
                    )
                ),
                command=COMMAND,
            )
        )
        metrics.append(
            Metric.measured(
                "gold_expectations_met",
                (len(field_scores) - len(wrong)) / len(field_scores),
                COMMAND,
            )
        )
        metrics.append(
            Metric.measured(
                "artifacts_exactly_right",
                artifacts_exact / len(scored),
                COMMAND,
            )
        )
        metrics.append(
            Metric.measured("artifacts_scored", float(len(scored)), COMMAND, unit="artifacts")
        )

    hero_artifacts = len(world.hero_artifact_ids)
    unscored_total = hero_artifacts - len(scored)
    checks.append(
        Check(
            check_id="EXT-02",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                f"{unscored_total} of the {hero_artifacts} hero artifacts have no "
                f"recorded extraction. {LIVE_RUN_TRANSCRIPT.name} walked three. "
                f"Scoring the rest needs {unscored_total} live Gemini calls "
                f"(tier E gemini-3.5-flash-lite plus tier R gemini-3.7-flash, "
                f"about 12k tokens each on the recorded run) and a hand-written "
                f"gold label per artifact -- not a code change."
                + (f" Gold exists but is unmatched for: {', '.join(unscored)}." if unscored else "")
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.unmeasured(
            "extraction_accuracy_over_all_hero_artifacts",
            f"only {len(scored)} of {hero_artifacts} artifacts have a recorded "
            f"extraction; a mean over 3 is not a rate",
            COMMAND,
        )
    )
    exclusions.append(
        f"EXT-01 covers {len(scored)} of {hero_artifacts} hero artifacts. Its "
        f"numbers are reported with n beside them and must not be read as "
        f"extraction accuracy for the corpus."
    )
    exclusions.append(
        "InjectionObservation.action_taken is Literal['TREATED_AS_DATA'] in "
        "provenance_contracts.ingestion -- one admissible value -- so an "
        "assertion that an injection was 'treated as data' cannot fail and is "
        "not scored. What is scored is that the injected artifact produced no "
        "commitment, which the type system does not guarantee."
    )

    # ---- EXT-03: the record keeps the distinction --------------------------
    typing_rows = []
    async with conn.cursor() as cur:
        await cur.execute(
            TYPING_SQL,
            {"hero": f"{HERO_KEY_PREFIX}%", "types": list(ASSERTIVE_EVIDENCE_TYPES)},
        )
        typing_rows = list(await cur.fetchall())
    by_kind = {str(kind): int(count) for kind, count in typing_rows}
    total = sum(by_kind.values())
    interested = sum(count for kind, count in by_kind.items() if kind in INTERESTED_PARTY_KINDS)
    as_fact = {kind: count for kind, count in by_kind.items() if kind not in INTERESTED_PARTY_KINDS}
    checks.append(
        Check(
            check_id="EXT-03",
            verdict=Verdict.PASS if total and not as_fact else Verdict.FAIL,
            detail=(
                f"{interested}/{total} counterparty-authored claims over "
                f"value-or-status evidence carry an interested-party claim_kind "
                f"({', '.join(f'{k}={v}' for k, v in sorted(by_kind.items()))})"
                + (f"; typed as the record's own: {as_fact}" if as_fact else "")
                if total
                else "no counterparty-authored claims over value-or-status "
                "evidence exist, so there is nothing to type"
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.measured("interested_party_typing_rate", interested / total, COMMAND)
        if total
        else Metric.unmeasured(
            "interested_party_typing_rate",
            "no counterparty-authored claims over value-or-status evidence",
            COMMAND,
        )
    )

    # ---- EXT-04: grounding, and the one inversion that would be fatal ------
    ungrounded = int(await scalar(conn, UNGROUNDED_CLAIMS_SQL) or 0)
    misattributed = int(await scalar(conn, COUNTERPARTY_AS_USER_SQL) or 0)
    checks.append(
        Check(
            check_id="EXT-04",
            verdict=Verdict.PASS if ungrounded == 0 and misattributed == 0 else Verdict.FAIL,
            detail=(
                f"{ungrounded} claim(s) cite no evidence; {misattributed} "
                f"counterparty-authored claim(s) are typed USER_CLAIM. The "
                f"second inversion would put the counterparty's assertion into "
                f"the user's mouth, which is the asymmetry this product exists "
                f"to correct."
            ),
            command=COMMAND,
        )
    )
    metrics.append(Metric.measured("ungrounded_claims", float(ungrounded), COMMAND, unit="claims"))

    return SuiteResult(
        suite_id=SUITE_ID,
        title="an invoice is a claim, not a fact",
        claim=CLAIM,
        checks=tuple(checks),
        metrics=tuple(metrics),
        exclusions=tuple(exclusions),
    )
