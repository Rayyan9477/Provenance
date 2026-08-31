"""RET -- retrieval under 18,000 adversarial decoys.

The product claim (``docs/00_PRODUCT.md`` section 2.2)
------------------------------------------------------
    "A chunk that says '$186 due' and a chunk that says 'service terminated 31
    May' are both just chunks. Whichever ranks higher wins the answer.
    Contradiction is resolved by cosine similarity."

That claim is about the *field*, so the measurement has to be about the field:
how hard is it, in this corpus, for the right document to outrank 18,000
documents built to look like it? The decoys are not filler. They were generated
as near-neighbours on purpose, and the sample proves it -- "Deposit terms for
the tenancy administered by Winterbourne Rentals" sits 0.418 from the hero
landlord's deposit promise.

What is measured, and what it is not
------------------------------------
There is **no Titan credential on this machine** and the corpus is Titan
``amazon.titan-embed-text-v2:0`` at 1024 dimensions. A fresh query embedded by
any other model lands in a different space: the cosine distances stay numbers,
stay ordered, and stop meaning anything. So this suite does not embed anything.

``RET-01`` takes each hero evidence row's **own stored vector** as the query
and asks whether the other rows of the same case outrank the decoy field. The
query row itself is removed before scoring -- it is at distance 0.0 by
construction and scoring it would be scoring the corpus against itself.

This is a **document-to-document** probe. It is not natural-language query
recall, which is what a user actually issues, and the two are not
interchangeable: a document vector carries the whole page, a query vector
carries a question. ``RET-02`` is the natural-language measurement and it is
reported ``CANNOT RUN``, naming the credential it waits on. Reporting RET-01's
number as if it answered RET-02's question would be the more comfortable lie.

Gold labels come from the record
---------------------------------
Two hero rows are "the same case" when the Kernel-written ``claims`` over them
point at the same ``cases.id``. ``evidence_items`` carries no ``case_id``: the
level that binds an observation to a case is the claim, which is exactly the
separation section 2.3 describes. A hand-written gold list would encode one
reading of the corpus; the join encodes the corpus.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import psycopg

from evals.runner import scoring
from evals.runner.corpus import HeroWorld, ann_probe
from evals.runner.verdict import Check, Metric, SuiteResult, Verdict

__all__ = ["CAP", "CUTOFFS", "CLAIM", "SUITE_ID", "run_suite"]

SUITE_ID: Final[str] = "RET"
CLAIM: Final[str] = (
    "00_PRODUCT.md section 2.2: 'Whichever ranks higher wins the answer. "
    "Contradiction is resolved by cosine similarity.'"
)

#: How deep the ranking is read. Every metric below is bounded by it, and the
#: bound is printed with the number: "not in the top 100" is a measurement,
#: "absent from the corpus" is a different claim.
CAP: Final[int] = 100

#: The cutoffs recall is reported at. 20 is ``K_FINAL`` -- what retrieval
#: actually returns in production -- so it is the one that describes the
#: shipped system; the others are context for it.
CUTOFFS: Final[tuple[int, ...]] = (1, 5, 10, 20, 50, 100)

COMMAND: Final[str] = "make evals   (python -m evals.runner --suite retrieval)"


@dataclass(frozen=True, slots=True)
class _Thresholds:
    """The bars RET-01 asserts, as declared in ``evals/thresholds.yaml``."""

    recall_at_20_min: float
    decoy_share_at_20_max: float


def _load_thresholds() -> _Thresholds:
    """Read the declared bars, or fail rather than invent one.

    The file is the authority and the defaults below are not a second opinion:
    they are the same numbers, restated so the suite still runs on a machine
    without PyYAML installed. A mismatch between the two is caught by
    ``evals/tests/test_thresholds_match_the_declaration.py``, because two places
    holding a threshold is exactly how a threshold gets quietly lowered.
    """
    declared = Path(__file__).resolve().parents[2] / "thresholds.yaml"
    try:
        import yaml
    except ImportError:
        return _Thresholds(recall_at_20_min=0.50, decoy_share_at_20_max=0.95)
    with declared.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    retrieval = loaded["retrieval"]
    return _Thresholds(
        recall_at_20_min=float(retrieval["recall_at_20_min"]),
        decoy_share_at_20_max=float(retrieval["decoy_share_at_20_max"]),
    )


THRESHOLDS: Final[_Thresholds] = _load_thresholds()

TITAN_BLOCKER: Final[str] = (
    "no Titan credential on this machine. The 18,035 stored vectors are "
    "amazon.titan-embed-text-v2:0 at 1024 dimensions; a query embedded by any "
    "other model lands in a different space and its distances stay ordered "
    "while meaning nothing. Needs AWS credentials with bedrock:InvokeModel on "
    "amazon.titan-embed-text-v2:0 in us-east-1, or the 0009 re-embed applied "
    "so the corpus and a Gemini query share a space."
)


async def run_suite(
    conn: psycopg.AsyncConnection[Any],
    world: HeroWorld,
    *,
    embedding_version: str,
) -> SuiteResult:
    checks: list[Check] = []
    metrics: list[Metric] = []
    exclusions: list[str] = []

    queries = [row for row in world.active_rows if world.gold_for(row)]
    skipped = [row for row in world.active_rows if not world.gold_for(row)]
    exclusions.append(
        f"{len(skipped)} of {len(world.active_rows)} ACTIVE hero rows are not "
        f"queried: no other ACTIVE hero row shares a case with them, so recall "
        f"has no denominator. Naming them rather than averaging a zero in: "
        f"{', '.join(sorted(row.artifact_name for row in skipped)) or 'none'}"
    )
    exclusions.append(
        f"{len(world.non_active_rows)} non-ACTIVE hero rows are excluded from "
        f"both the query set and every gold set. They are the retraction "
        f"fixtures and the RTR suite is where they are measured."
    )
    exclusions.append(
        f"ranks deeper than {CAP} are not read. A gold row that did not appear "
        f"within {CAP} contributes 0 to MRR and is counted in "
        f"'gold_never_ranked', which is a miss inside a stated bound and not a "
        f"claim that the row is absent."
    )

    if not queries:
        checks.append(
            Check(
                check_id="RET-01",
                verdict=Verdict.CANNOT_RUN,
                detail=(
                    "no ACTIVE hero row shares a case with another. The gold "
                    "sets come from claims.case_id; with 0 claims over hero "
                    "evidence there is nothing to score."
                ),
                command=COMMAND,
            )
        )
        return SuiteResult(
            suite_id=SUITE_ID,
            title="retrieval under adversarial decoys",
            claim=CLAIM,
            checks=tuple(checks),
            metrics=tuple(metrics),
            exclusions=tuple(exclusions),
        )

    per_cutoff: dict[int, list[float | None]] = {k: [] for k in CUTOFFS}
    reciprocal: list[float | None] = []
    decoy_share_at_20: list[float | None] = []
    gold_total = 0
    gold_never_ranked = 0
    returned_lengths: list[float | None] = []
    #: Queries where the decoy field buried every case-mate. A mean of 0.77 and
    #: a named list of the queries that scored 0 are different pieces of
    #: information, and only the second one can be acted on.
    buried: list[str] = []
    deep_first_hit: list[str] = []

    for row in queries:
        gold = world.gold_for(row)
        rows = await ann_probe(
            conn,
            world.principal,
            row.embedding,
            k=CAP,
            embedding_version=embedding_version,
        )
        ranked_ids: list[uuid.UUID] = [record["id"] for record in rows]
        artifacts: dict[uuid.UUID, uuid.UUID] = {
            record["id"]: record["artifact_id"] for record in rows
        }
        ranked = scoring.exclude(ranked_ids, {row.evidence_id})
        returned_lengths.append(float(len(ranked)))

        for cutoff in CUTOFFS:
            per_cutoff[cutoff].append(scoring.recall_at_k(ranked, gold, cutoff))
        reciprocal.append(scoring.reciprocal_rank(ranked, gold, CAP))

        gold_total += len(gold)
        missed = gold - set(ranked)
        gold_never_ranked += len(missed)

        first = scoring.first_gold_rank(ranked, gold)
        if first is None:
            buried.append(
                f"{row.artifact_name}: 0 of {len(gold)} case-mates within the " f"top {CAP}"
            )
        elif first > 20:
            deep_first_hit.append(
                f"{row.artifact_name}: first case-mate at rank {first} of "
                f"{len(ranked)} returned"
            )

        top20 = ranked[:20]
        if top20:
            decoys = sum(1 for item in top20 if artifacts.get(item) not in world.hero_artifact_ids)
            decoy_share_at_20.append(decoys / len(top20))

    for cutoff in CUTOFFS:
        value = scoring.mean(per_cutoff[cutoff])
        metrics.append(
            Metric.measured(f"recall@{cutoff}", value, COMMAND)
            if value is not None
            else Metric.unmeasured(f"recall@{cutoff}", "no query had a non-empty gold set", COMMAND)
        )
    mrr = scoring.mean(reciprocal)
    metrics.append(
        Metric.measured(f"MRR@{CAP}", mrr, COMMAND)
        if mrr is not None
        else Metric.unmeasured(f"MRR@{CAP}", "no query had a non-empty gold set", COMMAND)
    )
    share = scoring.mean(decoy_share_at_20)
    metrics.append(
        Metric.measured("decoy_share@20", share, COMMAND)
        if share is not None
        else Metric.unmeasured("decoy_share@20", "no query returned any row", COMMAND)
    )
    metrics.append(Metric.measured("queries", float(len(queries)), COMMAND, unit="queries"))
    metrics.append(Metric.measured("gold_pairs", float(gold_total), COMMAND, unit="pairs"))
    metrics.append(
        Metric.measured(
            f"gold_never_ranked_within_{CAP}",
            float(gold_never_ranked),
            COMMAND,
            unit="pairs",
        )
    )

    # RET-01's verdict is computed. It used to be the literal ``Verdict.PASS``
    # with these numbers interpolated into the detail string, which meant it
    # reported a pass at recall@20 = 0.77 and would have reported the identical
    # pass at 0.00 -- the only suite in the battery with no reachable FAIL. That
    # is the failure `STATUS.md` names outright: a hardcoded verdict beside a
    # computed fact is worse than no verdict, because the computed fact makes it
    # look measured.
    #
    # The bars come from `evals/thresholds.yaml`, which also records why each was
    # chosen and, separately, why G6.5's natural-language numbers are NOT
    # asserted here: they are RET-02's question and RET-02 cannot run.
    recall_at_20 = scoring.mean(per_cutoff[20])
    breaches: list[str] = []
    if recall_at_20 is None:
        breaches.append("recall@20 is unmeasured: no query had a non-empty gold set")
    elif recall_at_20 < THRESHOLDS.recall_at_20_min:
        breaches.append(
            f"recall@20={_fmt(recall_at_20)} is below the "
            f"{THRESHOLDS.recall_at_20_min:.2f} floor"
        )
    if share is not None and share > THRESHOLDS.decoy_share_at_20_max:
        breaches.append(
            f"decoy_share@20={_fmt(share)} is above the "
            f"{THRESHOLDS.decoy_share_at_20_max:.2f} ceiling"
        )

    measured = (
        f"{len(queries)} corpus-vector queries against "
        f"{len(world.rows)} hero rows in an 18,035-row field; "
        f"recall@20={_fmt(recall_at_20)} "
        f"MRR@{CAP}={_fmt(mrr)} decoy_share@20={_fmt(share)}"
    )
    checks.append(
        Check(
            check_id="RET-01",
            verdict=Verdict.PASS if not breaches else Verdict.FAIL,
            detail=(
                f"{measured}; asserted against recall@20 >= "
                f"{THRESHOLDS.recall_at_20_min:.2f} and decoy_share@20 <= "
                f"{THRESHOLDS.decoy_share_at_20_max:.2f} "
                f"(evals/thresholds.yaml)"
                if not breaches
                else f"{measured}; {'; '.join(breaches)}"
            ),
            command=COMMAND,
        )
    )
    checks.append(
        Check(
            check_id="RET-02",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                "natural-language query recall (the measurement a user's query "
                f"would produce) is not taken: {TITAN_BLOCKER}"
            ),
            command=COMMAND,
        )
    )
    checks.append(
        Check(
            check_id="RET-03",
            verdict=Verdict.CANNOT_RUN,
            detail=(
                "the hero June invoice cannot be ranked: "
                "demo/artifacts/northline-june-invoice.eml is deliberately "
                "absent from source_artifacts because the demo ingests it live "
                "to create the conflict. Ranking any row that merely mentions "
                "June and 186 ranks a decoy -- an earlier attempt did exactly "
                "that and reported FAIL, rank 2254 of 18035, against Aster Line "
                "Internet. Needs the live ingestion to have run."
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.unmeasured(
            "natural_language_recall@20",
            TITAN_BLOCKER,
            COMMAND,
        )
    )

    metrics.append(
        Metric.measured(
            "queries_with_no_gold_in_top_20",
            float(len(buried) + len(deep_first_hit)),
            COMMAND,
            unit="queries",
        )
    )
    findings = tuple(
        [
            f"{len(buried)} of {len(queries)} queries returned NO case-mate "
            f"within the top {CAP}; {len(deep_first_hit)} more found their "
            f"first only past rank 20 (K_FINAL, what retrieval returns in "
            f"production). Named rather than averaged away:"
        ]
        + [f"  buried: {line}" for line in sorted(buried)]
        + [f"  deep:   {line}" for line in sorted(deep_first_hit)]
    )

    return SuiteResult(
        suite_id=SUITE_ID,
        title="retrieval under adversarial decoys",
        claim=CLAIM,
        checks=tuple(checks),
        metrics=tuple(metrics),
        exclusions=tuple(exclusions),
        findings=findings,
    )


def _fmt(value: float | None) -> str:
    return "CANNOT RUN" if value is None else f"{value:.4f}"
