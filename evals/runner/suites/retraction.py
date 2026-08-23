"""RTR -- retracted evidence must not resurface, and must not be deleted either.

The product claim (``docs/00_PRODUCT.md`` section 2.2)
------------------------------------------------------
    "Retracted or superseded documents keep their embeddings and keep
    resurfacing."

This is the cheapest real signal in the repository and the sharpest, because
the failure it guards has **no symptom**. A retracted item keeps its embedding.
An unfiltered vector search returns it, ranks it plausibly -- a correction is
by construction about the same subject as the thing it corrects -- and grounds
a new belief on a fact the user already disowned. No error, no warning, and a
State Proof that looks entirely reasonable.

The probe is deliberately the hardest one available
----------------------------------------------------
``RTR-02`` queries with the retracted row's **own vector**. That puts the row
at cosine distance 0.0 from the query: in an unfiltered search it is rank 1 by
construction, ahead of all 18,034 other rows. If the lifecycle filter is
missing, weakened, or applied to a column the outer SELECT does not project,
this probe cannot fail to notice. A probe using some other query vector could
miss the row for the ordinary reason that it was not similar enough, and would
report a pass it had not earned.

``RTR-03`` is the control that keeps ``RTR-02`` honest. Filtering and deleting
produce the same empty result set, and they are opposite claims: evidence is
append-only, so a retracted row must still be *there*, with its bytes, its
embedding and its historical support edges intact. A suite that only checked
absence would give a green log to a system that had destroyed the record.
"""

from __future__ import annotations

from typing import Any, Final

import psycopg

from evals.runner.corpus import HeroWorld, ann_probe, scalar
from evals.runner.verdict import Check, Metric, SuiteResult, Verdict
from provenance_db.repositories.evidence import RetractionFilterMissingError

__all__ = ["CLAIM", "SUITE_ID", "run_suite"]

SUITE_ID: Final[str] = "RTR"
CLAIM: Final[str] = (
    "00_PRODUCT.md section 2.2: 'Retracted or superseded documents keep their "
    "embeddings and keep resurfacing.'"
)
COMMAND: Final[str] = "make evals   (python -m evals.runner --suite retraction)"

#: How deep the ranking is read for the self-vector probe. A retracted row is
#: at distance 0.0 from its own vector, so if it can appear at all it appears
#: first; the depth is generous rather than necessary.
CAP: Final[int] = 100

#: ``13_RETRIEVAL_SPEC.md`` section 13.3 layer 3's tripwire, spelled as
#: ``provenance_db.repositories.evidence.RETRACTION_PREDICATE`` spells it.
LIFECYCLE_PREDICATE: Final[str] = "retraction_status = 'ACTIVE'"

FLAG_CONSISTENCY_SQL: Final[str] = """
SELECT count(*) FROM evidence_items
WHERE is_retrieval_eligible <> (retraction_status = 'ACTIVE')
"""

GROUNDED_ON_RETRACTED_SQL: Final[str] = """
SELECT count(*) FROM claims c
JOIN evidence_items e ON e.id = c.evidence_id
WHERE e.retraction_status <> 'ACTIVE'
"""

SURVIVING_BYTES_SQL: Final[str] = """
SELECT count(*) FROM evidence_items
WHERE retraction_status <> 'ACTIVE'
  AND embedding IS NOT NULL
  AND normalized_text IS NOT NULL
"""


async def run_suite(
    conn: psycopg.AsyncConnection[Any],
    world: HeroWorld,
    *,
    embedding_version: str,
) -> SuiteResult:
    checks: list[Check] = []
    metrics: list[Metric] = []
    exclusions: list[str] = []

    fixtures = world.non_active_rows
    exclusions.append(
        f"the self-vector probe covers the {len(fixtures)} non-ACTIVE rows that "
        f"exist in this corpus, one per lifecycle state. There is no fourth "
        f"state to probe; RetractionStatus has four members and ACTIVE is the "
        f"one being filtered for."
    )

    # ---- RTR-01: the stored flag agrees with the lifecycle column -----------
    mismatches = int(await scalar(conn, FLAG_CONSISTENCY_SQL) or 0)
    checks.append(
        Check(
            check_id="RTR-01",
            verdict=Verdict.PASS if mismatches == 0 else Verdict.FAIL,
            detail=(
                f"{mismatches} row(s) where is_retrieval_eligible disagrees with "
                f"(retraction_status = 'ACTIVE') across the whole corpus"
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.measured("lifecycle_flag_mismatches", float(mismatches), COMMAND, unit="rows")
    )

    # ---- RTR-02: the hardest possible resurfacing probe ---------------------
    #
    # The layer-3 tripwire (`RetractionFilterMissingError`) refuses to execute
    # the statement when the lifecycle predicate is gone. That refusal is
    # caught here and recorded as **FAIL**, not as CANNOT RUN: the filter being
    # absent is exactly the defect this suite exists to detect, and letting the
    # exception escape would exit the harness with "could not start" -- hiding
    # the one finding it was run for. `CANNOT RUN is not FAIL` cuts both ways.
    resurfaced: list[str] = []
    tripwire: str | None = None
    try:
        for row in fixtures:
            rows = await ann_probe(
                conn,
                world.principal,
                row.embedding,
                k=CAP,
                embedding_version=embedding_version,
            )
            if any(record["id"] == row.evidence_id for record in rows):
                position = next(
                    index for index, record in enumerate(rows, 1) if record["id"] == row.evidence_id
                )
                resurfaced.append(
                    f"{row.artifact_name} ({row.retraction_status}) at rank {position}"
                )
    except RetractionFilterMissingError as error:
        tripwire = str(error)

    if tripwire is not None:
        checks.append(
            Check(
                check_id="RTR-02",
                verdict=Verdict.FAIL,
                detail=(
                    "the ANN entry point refused to execute: the lifecycle "
                    f"predicate is missing from the statement. {tripwire}"
                ),
                command=COMMAND,
            )
        )
        metrics.append(
            Metric.unmeasured(
                "retracted_rows_resurfaced",
                "the statement was refused before execution because it had lost "
                "its lifecycle predicate; the count is unknown and the defect is "
                "certain",
                COMMAND,
            )
        )
    else:
        checks.append(
            Check(
                check_id="RTR-02",
                verdict=Verdict.PASS if not resurfaced else Verdict.FAIL,
                detail=(
                    f"{len(fixtures)} non-ACTIVE row(s) queried with their own vector "
                    f"(cosine distance 0.0, rank 1 in any unfiltered search); "
                    f"{len(resurfaced)} resurfaced within the top {CAP}"
                    + (f": {'; '.join(resurfaced)}" if resurfaced else "")
                ),
                command=COMMAND,
            )
        )
        metrics.append(
            Metric.measured(
                "retracted_rows_resurfaced", float(len(resurfaced)), COMMAND, unit="rows"
            )
        )

    # ---- RTR-03: filtered, not deleted -------------------------------------
    surviving = int(await scalar(conn, SURVIVING_BYTES_SQL) or 0)
    checks.append(
        Check(
            check_id="RTR-03",
            verdict=Verdict.PASS if surviving == len(fixtures) else Verdict.FAIL,
            detail=(
                f"{surviving} of {len(fixtures)} non-ACTIVE rows still hold their "
                f"embedding and normalized_text. Evidence is append-only: a "
                f"filter and a delete produce the same empty result set and are "
                f"opposite claims about the record."
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.measured("retracted_rows_retained", float(surviving), COMMAND, unit="rows")
    )

    # ---- RTR-04: the layer-3 tripwire is in the statement that shipped ------
    from services.control_plane.app.retrieval import ann

    sql = ann.render_ann_sql()
    present = LIFECYCLE_PREDICATE in sql
    checks.append(
        Check(
            check_id="RTR-04",
            verdict=Verdict.PASS if present else Verdict.FAIL,
            detail=(
                f"the rendered ANN statement {'carries' if present else 'LACKS'} "
                f"{LIFECYCLE_PREDICATE!r}. Read from "
                f"services.control_plane.app.retrieval.ann.render_ann_sql(), "
                f"which is the statement ann_search() executes -- not a copy."
            ),
            command=COMMAND,
        )
    )

    # ---- RTR-05: nothing canonical is grounded on a retracted row ----------
    grounded = int(await scalar(conn, GROUNDED_ON_RETRACTED_SQL) or 0)
    checks.append(
        Check(
            check_id="RTR-05",
            verdict=Verdict.PASS if grounded == 0 else Verdict.FAIL,
            detail=(
                f"{grounded} claim(s) cite evidence whose retraction_status is "
                f"not ACTIVE. Only ACTIVE evidence may ground a new belief."
            ),
            command=COMMAND,
        )
    )
    metrics.append(
        Metric.measured("claims_on_non_active_evidence", float(grounded), COMMAND, unit="claims")
    )

    return SuiteResult(
        suite_id=SUITE_ID,
        title="retracted evidence does not resurface",
        claim=CLAIM,
        checks=tuple(checks),
        metrics=tuple(metrics),
        exclusions=tuple(exclusions),
    )
