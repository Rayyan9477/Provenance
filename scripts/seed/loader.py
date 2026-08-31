"""The eleven steps, in the mandatory order (``T2.8``).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5 ``T2.8`` sub-tasks 1-11 and
  section 23 -- "the order below is mandatory and is the single most
  failure-prone sequence in the build".
- ``docs/ops/41_RUNBOOK.md`` section 4.2, quoted in section 23.
- ``docs/specs/10_DATABASE_DDL.md`` section 12 -- which role may write what.

The steps
---------
=====  ==================================================  ======================
step   what                                                as
=====  ==================================================  ======================
1      guard: ``APP_ENV`` is ``local`` or ``demo``          -
2      truncate in reverse FK order (``--reset`` only)      pv_migrator
3      tenants 3, users 3, counterparties 5,                pv_migrator
       relationships 6, contexts 1, cases 10
4      ``DROP INDEX evidence_embedding_ann_idx CASCADE``    pv_migrator
5      resolve 18,035 embeddings, cache-first               -
6      bulk-load source_artifacts then evidence_items       pv_app_reader_writer
7      ``CREATE VECTOR INDEX ...``                          pv_migrator
8      ``SHOW JOB WHEN COMPLETE``                           pv_migrator
9      replay curated proposals through the Kernel          pv_kernel_writer
10     apply the 3 retraction fixtures (DDL section 5.6)    pv_kernel_writer
11     run every section 18 verification query              pv_migrator
=====  ==================================================  ======================

Step 9, and the one rule it exists to keep
------------------------------------------
Step 9 replays the curated ``MemoryProposal`` fixtures of
``scripts/seed/proposals.py`` through ``MemoryKernel.commit()`` as
``pv_kernel_writer``. Every canonical row the seed creates is written there and
nowhere else: ``70_TASK_PLAN.md`` T2.8 step 9 says outright that "seeding
canonical rows by raw INSERT to unblock Phase 2 would create a second canonical
writer and is forbidden", and ``tools/write_path_lint`` checks that structurally
against every runtime tree.

It was deferred through Phases 2 and 3 because the Kernel did not exist. It does
now, so ``--profile all`` and ``--profile hero`` run it; ``--profile
schema-only`` still skips it, by request rather than by absence, and
:func:`replay_curated_proposals` says which of those happened in one line the
gate ledger can quote. If the Kernel is not importable at all the step still
*reports* rather than fails, because ``70_TASK_PLAN.md`` section 24 risk 11
requires the deferral to be written down rather than discovered.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from scripts.seed import db as dbmod
from scripts.seed.cases import CASES, CONTEXTS, case_of
from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS
from scripts.seed.decoys import generate_decoys
from scripts.seed.embedding_text import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_VERSION,
    build_embedding_text,
    embedding_text_sha256,
)
from scripts.seed.embeddings import EmbeddingResolver
from scripts.seed.evidence import evidence_of
from scripts.seed.ids import DEMO_ANCHOR_UTC, days_before_anchor, sid
from scripts.seed.retractions import RETRACTION_ARTIFACTS, RETRACTION_FIXTURES
from scripts.seed.rows import SeedArtifact, SeedEvidence
from scripts.seed.tenants import HERO_TENANT, HERO_USER, TENANTS, USERS
from scripts.seed.world import curated_artifacts, curated_evidence

__all__ = [
    "ReplayReport",
    "SeedReport",
    "apply_perturbation",
    "replay_curated_proposals",
    "revert_perturbation",
    "run_replay",
    "run_seed",
]


@dataclass
class SeedReport:
    """What the run actually did, as numbers rather than as claims."""

    profile: str
    reset: bool
    perturb: bool
    index_dropped: bool = False
    index_rebuilt: bool = False
    live_bedrock_calls: int = 0
    embedding_cache_hits: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    rows_pending: int = 0
    perturbation: dict[str, int] = field(default_factory=dict)
    step_9_status: str = "not attempted"
    retractions_applied: int = 0
    seconds: float = 0.0

    def line(self) -> str:
        return (
            f"profile={self.profile} reset={self.reset} perturb={self.perturb} "
            f"bedrock_live={self.live_bedrock_calls} cache_hits={self.embedding_cache_hits} "
            f"index_dropped={self.index_dropped} index_rebuilt={self.index_rebuilt} "
            f"rows_pending={self.rows_pending} step9={self.step_9_status} "
            f"seconds={self.seconds:.1f}"
        )


# ---------------------------------------------------------------------------
# Step 3 -- the small planes, as pv_migrator
# ---------------------------------------------------------------------------


def load_small_planes(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """tenants(3) -> users(3) -> counterparties(5) -> relationships(6) -> contexts(1) -> cases(10).

    Insert order is foreign-key order, not alphabetical: ``fk_users_tenant``,
    ``fk_relationships_counterparty`` and ``fk_cases_relationship`` are all
    validated at statement time.
    """
    counts: dict[str, int] = {}
    now = DEMO_ANCHOR_UTC

    # An account is not created at the instant the demo is set in.
    #
    # `now` is DEMO_ANCHOR, the demo clock, and tenants and users took it for
    # `created_at` as well as `updated_at`. So the settings screen read
    # "In record since 18 SEP 2026" for an account holding evidence from April
    # onwards -- an account younger than its own record, and, against the real
    # clock the same page prints, created in the future.
    #
    # 600 days before the anchor puts it in January 2025, before the earliest
    # seeded artifact of any kind (the decoy field starts 2025-03-27). Derived
    # from the anchor rather than written as a literal, because
    # `test_seed_determinism.py` AST-scans this package for absolute instants
    # and is right to.
    account_opened = days_before_anchor(600)

    counts["tenants"] = dbmod.insert_batches(
        conn,
        "tenants",
        ("id", "name", "slug", "status", "created_at", "updated_at"),
        [(t.id, t.name, t.slug, t.status, account_opened, now) for t in TENANTS],
    )
    counts["users"] = dbmod.insert_batches(
        conn,
        "users",
        (
            "id",
            "tenant_id",
            "cognito_sub",
            "email",
            "display_name",
            "timezone",
            "home_region",
            "app_role",
            "judge_mode_enabled",
            "status",
            "created_at",
            "updated_at",
        ),
        [
            (
                u.id,
                u.tenant_id,
                u.cognito_sub,
                u.email,
                u.display_name,
                u.timezone,
                u.home_region,
                u.app_role,
                u.judge_mode_enabled,
                u.status,
                account_opened,
                now,
            )
            for u in USERS
        ],
    )
    counts["counterparties"] = dbmod.insert_batches(
        conn,
        "counterparties",
        (
            "id",
            "tenant_id",
            "normalized_name",
            "display_name",
            "kind",
            "canonical_domain",
            "known_domains",
            "metadata",
            "created_at",
            "updated_at",
        ),
        [
            (
                c.id,
                c.tenant_id,
                c.normalized_name,
                c.display_name,
                c.kind,
                c.canonical_domain,
                Jsonb(c.known_domains),
                Jsonb({"seeded": True}),
                now,
                now,
            )
            for c in COUNTERPARTIES
        ],
    )
    counts["relationships"] = dbmod.insert_batches(
        conn,
        "relationships",
        (
            "id",
            "tenant_id",
            "user_id",
            "counterparty_id",
            "relationship_type",
            "label",
            "external_account_ref",
            "normalized_identifiers",
            "status",
            "valid_from",
            "valid_to",
            "revision",
            "created_at",
            "updated_at",
        ),
        [
            (
                r.id,
                r.tenant_id,
                r.user_id,
                r.counterparty_id,
                r.relationship_type,
                r.label,
                r.external_account_ref,
                Jsonb({"account_ref": r.external_account_ref}),
                r.status,
                r.valid_from,
                r.valid_to,
                r.revision,
                now,
                now,
            )
            for r in RELATIONSHIPS
        ],
    )
    counts["contexts"] = dbmod.insert_batches(
        conn,
        "contexts",
        (
            "id",
            "tenant_id",
            "user_id",
            "title",
            "context_type",
            "status",
            "started_at",
            "ended_at",
            "created_at",
            "updated_at",
        ),
        [
            (
                c.id,
                c.tenant_id,
                c.user_id,
                c.title,
                c.context_type,
                c.status,
                c.started_at,
                c.ended_at,
                now,
                now,
            )
            for c in CONTEXTS
        ],
    )

    counts["cases"] = dbmod.insert_batches(
        conn,
        "cases",
        (
            "id",
            "tenant_id",
            "user_id",
            "relationship_id",
            "context_id",
            "case_type",
            "title",
            "status",
            "revision",
            "opened_at",
            "resolved_at",
            "last_activity_at",
            "reopened_count",
            "attention_level",
            "created_at",
            "updated_at",
        ),
        [
            (
                c.id,
                c.tenant_id,
                c.user_id,
                c.relationship_id,
                c.context_id,
                c.case_type,
                c.title,
                c.status,
                c.revision,
                c.opened_at,
                c.resolved_at,
                c.last_activity_at,
                c.reopened_count,
                c.attention_level,
                now,
                now,
            )
            for c in CASES
        ],
    )
    return counts


#: The two cases whose open state is what the demo computes an answer from.
PERTURBED_CASES: tuple[str, ...] = ("landlord-deposit", "movers-damage")


def apply_perturbation(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """``make seed-perturb`` -- remove or shift every outcome-bearing row.

    ``70_TASK_PLAN.md`` section 23.1 names four perturbations: conflict deleted,
    case left ``RESOLVED``, commitment already fulfilled, invoice date moved
    outside the terminated period. Two of those live in Kernel-written tables
    that step 9 has not populated yet, so this applies the three that are
    reachable today and the deferral is recorded rather than papered over:

    * **Cases left RESOLVED.** ``landlord-deposit`` and ``movers-damage`` are the
      only two cases the demo computes an outstanding balance from. A suite that
      still reports "USD 1,800 overdue, 95 days" after this is reading the seed
      rather than computing from it.
    * **The retraction fixtures removed.** With nothing retracted, a retrieval
      filter that has silently stopped working returns the same result as one
      that works. V11 drops to zero, which is exactly the failure V11 exists to
      make visible.
    * **The termination date moved outside the terminated period.** The 31 May
      assertion's validity window shifts forward sixty days, so the June invoice
      no longer contradicts anything.

    Applied **in place**, not by truncate-and-reload, and reversed exactly by
    :func:`revert_perturbation`. The reason is arithmetic: a reload of 18,035
    rows crosses the ANN drop threshold and costs a fifty-three-minute index
    rebuild in each direction, which would make the detector too expensive to
    run and therefore not a detector.
    """
    counts = {"cases_resolved": 0, "evidence_removed": 0, "dates_moved": 0}
    fixture_ids = [f.id for f in RETRACTION_FIXTURES]
    artifact_ids = [a.id for a in RETRACTION_ARTIFACTS]
    with conn.cursor() as cur:
        for slug in PERTURBED_CASES:
            case = case_of(slug)
            cur.execute(
                "UPDATE cases SET status = 'RESOLVED', resolved_at = %s, "
                "attention_level = 'NONE' WHERE id = %s AND status <> 'RESOLVED'",
                (case.last_activity_at, case.id),
            )
            counts["cases_resolved"] += cur.rowcount
        cur.execute(
            "UPDATE evidence_items SET retraction_status = 'ACTIVE', retracted_at = NULL, "
            "retracted_by_evidence_id = NULL, retraction_reason_code = NULL WHERE id = ANY(%s)",
            (fixture_ids,),
        )
        cur.execute("DELETE FROM evidence_items WHERE id = ANY(%s)", (fixture_ids,))
        counts["evidence_removed"] = cur.rowcount
        cur.execute("DELETE FROM source_artifacts WHERE id = ANY(%s)", (artifact_ids,))
        # An absolute target, not `valid_from + 60 days`: relative arithmetic
        # makes the perturbation non-idempotent, so running `make seed-perturb`
        # twice would move the date 120 days and `--restore` would only ever
        # undo half of it. Both directions name the instant they want.
        termination = evidence_of("isp-termination-effective-31-may")
        assert termination.valid_from is not None
        cur.execute(
            "UPDATE evidence_items SET valid_from = %s WHERE id = %s",
            (termination.valid_from + timedelta(days=60), termination.id),
        )
        counts["dates_moved"] = cur.rowcount
    conn.commit()
    return counts


def revert_perturbation(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Undo :func:`apply_perturbation` exactly. Row-for-row, not by reload."""
    counts = {"cases_restored": 0, "dates_restored": 0}
    with conn.cursor() as cur:
        for slug in PERTURBED_CASES:
            case = case_of(slug)
            cur.execute(
                "UPDATE cases SET status = %s, resolved_at = %s, attention_level = %s "
                "WHERE id = %s",
                (case.status, case.resolved_at, case.attention_level, case.id),
            )
            counts["cases_restored"] += cur.rowcount
        termination = evidence_of("isp-termination-effective-31-may")
        cur.execute(
            "UPDATE evidence_items SET valid_from = %s WHERE id = %s",
            (termination.valid_from, termination.id),
        )
        counts["dates_restored"] = cur.rowcount
    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# Steps 5 and 6 -- embeddings, then the bulk load, as pv_app_reader_writer
# ---------------------------------------------------------------------------

_ARTIFACT_COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "source_type",
    "s3_bucket",
    "s3_key",
    "content_sha256",
    "size_bytes",
    "mime_type",
    "source_message_id",
    "sender",
    "sender_domain",
    "recipient",
    "subject",
    "received_at",
    "event_time",
    "parser_status",
    "parser_version",
    "created_at",
    "updated_at",
)

_EVIDENCE_COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "artifact_id",
    "evidence_type",
    "normalized_text",
    "exact_text",
    "source_locator",
    "actor_ref",
    "valid_from",
    "valid_to",
    "observed_at",
    "extraction_confidence",
    "source_authority",
    "embedding",
    "embedding_model",
    "embedding_version",
    "embedding_generated_at",
    "normalized_text_sha256",
    "created_at",
)

#: ``embedding`` needs an explicit cast: psycopg sends the literal as an
#: untyped string and CockroachDB will not infer ``VECTOR`` inside a multi-row
#: ``VALUES`` list.
_EVIDENCE_PLACEHOLDERS = tuple(
    "%s::VECTOR" if column == "embedding" else "%s" for column in _EVIDENCE_COLUMNS
)


def _artifact_tuple(artifact: SeedArtifact, now: datetime) -> tuple[Any, ...]:
    return (
        artifact.id,
        artifact.tenant_id,
        artifact.user_id,
        artifact.source_type,
        artifact.s3_bucket,
        artifact.s3_key,
        artifact.content_sha256,
        artifact.size_bytes,
        artifact.mime_type,
        artifact.source_message_id,
        artifact.sender,
        artifact.sender_domain,
        artifact.recipient,
        artifact.subject,
        artifact.received_at,
        artifact.event_time,
        artifact.parser_status,
        artifact.parser_version,
        now,
        now,
    )


def embedding_input(evidence: SeedEvidence) -> str:
    return build_embedding_text(
        evidence_type=evidence.evidence_type,
        counterparty_name=evidence.counterparty_name,
        predicate=evidence.predicate,
        valid_from=evidence.valid_from,
        valid_to=evidence.valid_to,
        currency=evidence.currency,
        amount=evidence.amount,
        has_identifier=evidence.has_identifier,
        normalized_text=evidence.normalized_text,
    )


def _evidence_tuple(
    evidence: SeedEvidence, vectors: dict[bytes, Any], now: datetime
) -> tuple[Any, ...]:
    key = embedding_text_sha256(embedding_input(evidence))
    vector = vectors[key]
    return (
        evidence.id,
        evidence.tenant_id,
        evidence.user_id,
        evidence.artifact_id,
        evidence.evidence_type,
        evidence.normalized_text,
        evidence.exact_text,
        None if evidence.source_locator is None else Jsonb(evidence.source_locator),
        evidence.actor_ref,
        evidence.valid_from,
        evidence.valid_to,
        evidence.observed_at,
        evidence.extraction_confidence,
        evidence.source_authority,
        dbmod.vector_literal(vector.tolist()),
        EMBEDDING_MODEL_ID,
        EMBEDDING_VERSION,
        now,
        hashlib.sha256(evidence.normalized_text.encode("utf-8")).digest(),
        now,
    )


# ---------------------------------------------------------------------------
# Step 9 -- the Kernel replay
# ---------------------------------------------------------------------------
#
# Everything below writes canonical rows through ``MemoryKernel.commit()`` and
# through nothing else. There is no ``INSERT`` here against ``claims``,
# ``beliefs``, ``belief_versions``, ``belief_support``, ``conflicts``,
# ``commitments``, ``fulfillments``, ``state_transitions``,
# ``prospective_triggers`` or ``outbox_events``, and there must never be one:
# ``70_TASK_PLAN.md`` T2.8 step 9 says outright that "seeding canonical rows by
# raw INSERT to unblock Phase 2 would create a second canonical writer and is
# forbidden", and ``tools/write_path_lint`` checks that claim against the AST of
# every runtime tree.
#
# The two statements this module *does* issue are the two DDL section 12 assigns
# to a role other than ``pv_kernel_writer``:
#
#   * ``INSERT INTO memory_proposals`` as ``pv_app_reader_writer``. Section 12
#     grants the app INSERT and the Kernel only UPDATE -- an agent submits a
#     proposal, the Kernel settles it -- which is why
#     ``write_path_lint.APP_INSERT_PERMITTED`` names this table. The Kernel
#     cannot commit a proposal whose row does not exist:
#     ``fk_kernel_decisions_proposal`` refuses the decision row.
#   * ``UPDATE cases SET revision = revision - 1`` as ``pv_migrator``, described
#     under :func:`_position_case_revision`. It is the same table, the same role
#     and the same seeding step that already wrote the row four steps earlier.

_KERNEL_MODULE = "services.control_plane.app.memory_kernel.transaction"


def kernel_is_available() -> bool:
    """Whether ``MemoryKernel.commit()`` can be imported at all.

    Checked by ``find_spec`` rather than by importing, so a seed running with
    ``--profile schema-only`` on a machine without the control plane installed
    reports the deferral instead of dying in an import.
    """
    return importlib.util.find_spec(_KERNEL_MODULE) is not None


#: DDL section 12: the app inserts a proposal, the Kernel only ever updates one.
#: ``ON CONFLICT DO NOTHING`` because every id here is a ``uuid5`` and a reseed
#: re-offers exactly the row that is already there -- the same property that
#: makes steps 3 and 6 idempotent.
_PROPOSAL_INSERT_SQL = """
INSERT INTO memory_proposals (
    id, tenant_id, user_id, trace_id, agent_run_id, schema_version, proposal_type,
    source_artifact_ids, evidence_ids, candidate_relationship_id, candidate_case_id,
    payload, payload_sha256, model_id, prompt_version, status, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SUBMITTED', %s)
ON CONFLICT DO NOTHING
"""

#: The proposals this replay has already settled, read before anything is
#: written. ``uq_kernel_decisions_terminal_per_proposal`` is
#: ``UNIQUE (proposal_id) WHERE decision <> 'RETRYABLE_CONCURRENCY'``, so this
#: selects the at-most-one terminal decision a proposal can have.
_DECIDED_PROPOSALS_SQL = """
SELECT proposal_id FROM kernel_decisions
 WHERE proposal_id = ANY(%s) AND decision <> 'RETRYABLE_CONCURRENCY'
"""

#: See :func:`_position_case_revision`. Guarded on the declared revision, so it
#: is a no-op on a case that has already been positioned or already replayed.
_CASE_REWIND_SQL = """
UPDATE cases SET revision = revision - %s
 WHERE tenant_id = %s AND user_id = %s AND id = %s AND revision = %s
"""

#: The Kernel-minted obligation per case, read back between the two passes.
#: One commitment per case in this seed, so ``case_id`` identifies it without
#: matching on an amount or a description -- both of which a later edit could
#: change without anybody noticing the join had started guessing.
_COMMITMENTS_BY_CASE_SQL = """
SELECT case_id, id FROM commitments
 WHERE tenant_id = %s AND user_id = %s AND case_id IS NOT NULL
"""


@dataclass
class ReplayReport:
    """What step 9 actually did, per proposal rather than in aggregate."""

    profile: str = "all"
    committed: int = 0
    replayed: int = 0
    rejected: int = 0
    positioned_cases: int = 0
    decisions: tuple[str, ...] = ()
    fulfillments_admitted: int = 0
    #: What the proposals carry beyond claims and beliefs. Counted, not
    #: assumed: see :func:`scripts.seed.proposals.proposed_obligation_content`.
    obligations: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        carried = ", ".join(f"{k}={v}" for k, v in sorted(self.obligations.items()))
        return (
            f"replayed {self.committed + self.replayed} proposals through "
            f"MemoryKernel.commit(): committed={self.committed} "
            f"already-decided={self.replayed} rejected={self.rejected} "
            f"cases_positioned={self.positioned_cases}; obligations carried: "
            f"{carried or 'none'}"
        )


def _principal() -> Any:
    from services.control_plane.app.memory_kernel import preflight

    return preflight.Principal(tenant_id=HERO_TENANT.id, user_id=HERO_USER.id)


def _decided_proposal_ids(
    conn: psycopg.Connection[Any], proposal_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    with conn.cursor() as cur:
        cur.execute(_DECIDED_PROPOSALS_SQL, (list(proposal_ids),))
        return {row[0] for row in cur.fetchall()}


def _register_proposals(conn: psycopg.Connection[Any], pending: Sequence[Any]) -> int:
    """Write the ``memory_proposals`` rows, as ``pv_app_reader_writer``."""
    from scripts.seed.proposals import (
        SEED_MODEL_ID,
        SEED_PROMPT_VERSION,
        payload_sha256,
        proposal_payload,
    )

    written = 0
    with conn.cursor() as cur:
        for seeded in pending:
            proposal = seeded.proposal
            cur.execute(
                _PROPOSAL_INSERT_SQL,
                (
                    proposal.proposal_id,
                    HERO_TENANT.id,
                    HERO_USER.id,
                    proposal.trace_id,
                    proposal.agent_run_id,
                    proposal.schema_version,
                    str(proposal.proposal_type),
                    Jsonb([str(a) for a in proposal.source_artifact_ids]),
                    Jsonb([str(e) for e in proposal.evidence_ids]),
                    proposal.identity.relationship_id,
                    proposal.identity.case_id,
                    Jsonb(proposal_payload(proposal)),
                    payload_sha256(proposal),
                    SEED_MODEL_ID,
                    SEED_PROMPT_VERSION,
                    proposal.created_at,
                ),
            )
            written += cur.rowcount
    conn.commit()
    return written


def _position_case_revision(conn: psycopg.Connection[Any], planned: Mapping[str, int]) -> int:
    """Rewind each target case by the revisions its replay is about to spend.

    WHY THIS EXISTS, and why it is not a canonical write.

    ``case_ops.revision_after`` is Kernel rule ``R1``: **exactly one**
    ``cases.revision`` increment per accepted commit, no exceptions and no
    opt-out. *planned* is how many commits each case is about to receive --
    one for its curated proposal, and a second for the two cases that also
    take a fulfillment -- so the step is per case rather than a flat one. Step 3 of this seed already inserted every case at the revision
    ``scripts/seed/cases.py`` declares -- 12 for the hero case -- and those
    numbers are eval ground truth (``22_EVAL_DATASETS.md`` section 2), fixture
    ``the_move_baseline_rev12`` is "Full hero world, **case 1 at revision 12**",
    and ``CANONICAL_DECISIONS.md`` reserves the move to 13 for the demo.

    So the declared revision is the state **after** step 9, and step 3 wrote it
    **before** step 9 runs. Left alone, the hero case would finish this step at
    13 and the demo's reveal would be "13 -> 14" -- which is the same defect as
    seeding the hero event, arriving by way of an off-by-one.

    Three properties make this safe rather than clever:

    * It is the same table, the same role (``pv_migrator``) and the same seeding
      step that inserted the row four steps earlier. ``cases`` is not one of the
      tables step 9 is forbidden to touch; the ten rows it holds are written by
      step 3 by construction.
    * It changes no *meaning*. The case's status, resolution, attention and
      identity are untouched; only the counter is positioned so that the
      Kernel's own increment lands on the declared figure. Every audit row the
      replay then writes agrees with it -- ``kernel_decisions`` records
      ``11 -> 12``, not a fabricated ``12 -> 12``.
    * It is guarded and self-healing. The predicate is the declared revision, so
      it is a no-op on a case already positioned or already replayed, and a run
      that rewinds and then fails leaves the case at ``declared - 1``, where the
      next run's guard does not match and the replay alone brings it home.

    The cleaner home for this is ``scripts/seed/cases.py``, which should declare
    the **pre**-replay revision and let the Kernel produce the published one.
    That file belongs to ``T2.8`` and is not this task's to edit; this is the
    same arithmetic, applied from the step that knows how many commits it is
    about to make. Reported rather than left implicit.
    """
    positioned = 0
    with conn.cursor() as cur:
        for case_slug, commits in sorted(planned.items()):
            case = case_of(case_slug)
            cur.execute(
                _CASE_REWIND_SQL,
                (commits, HERO_TENANT.id, HERO_USER.id, case.id, case.revision),
            )
            positioned += cur.rowcount
    conn.commit()
    return positioned


def _new_event_loop() -> Any:
    """A loop ``psycopg``'s async pool can actually run on.

    ``psycopg`` refuses Windows' ``ProactorEventLoop`` -- ``InterfaceError:
    Psycopg cannot use the 'ProactorEventLoop' to run in async mode`` -- and
    ``asyncio.run`` builds its loop from the process-wide policy. Building the
    loop here keeps the fix local: the seed does not mutate a global policy that
    a caller may have set for its own reasons.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


async def _commit_pending(dsn: str, pending: Sequence[Any]) -> list[tuple[str, Any]]:
    """One ``MemoryKernel.commit()`` per proposal, as ``pv_kernel_writer``.

    Sequential on purpose. Two concurrent commits against one case contend on
    ``cases.revision`` and would spend the retry budget proving a property the
    concurrency suite already proves; a seed is not the place to exercise
    ``40001``.
    """
    from psycopg_pool import AsyncConnectionPool

    from services.control_plane.app.memory_kernel import transaction

    principal = _principal()
    pool = AsyncConnectionPool(str(dsn), min_size=1, max_size=2, open=False)
    await pool.open(wait=True, timeout=30)
    try:
        results: list[tuple[str, Any]] = []
        for seeded in pending:
            result = await transaction.commit_proposal(pool, seeded.proposal, principal=principal)
            results.append((seeded.case_slug, result))
        return results
    finally:
        await pool.close()


def _commitment_ids_by_case(conn: psycopg.Connection[Any]) -> dict[uuid.UUID, uuid.UUID]:
    """``case_id -> commitments.id``, read back between the two passes."""
    with conn.cursor() as cur:
        cur.execute(_COMMITMENTS_BY_CASE_SQL, (HERO_TENANT.id, HERO_USER.id))
        return {row[0]: row[1] for row in cur.fetchall()}


def run_replay(*, database: str | None = None, profile: str = "all") -> ReplayReport:
    """Step 9, executed, in two passes. Every canonical row goes through the Kernel.

    **Pass one** commits the nine curated proposals: claims, beliefs, their
    grounding, and the four obligations. **Pass two** admits the two payments
    against the obligations pass one created.

    The split is forced by the Kernel and is not a stylistic choice.
    ``pipeline._apply_payment`` resolves a payment against the aggregate
    snapshot read at the *start* of the transaction, so a commitment minted by
    the same commit is not there to be paid; and ``pipeline._commitment_row``
    mints its id with ``uuid.uuid4()`` inside the transaction, so no fixture can
    name it in advance. Without pass two every obligation stands at its full
    committed amount and the landing screen renders USD 4,570.00 rather than the
    canonical USD 2,020.00.

    Idempotence is the Kernel's, not a pre-check of this module's: a proposal
    whose id already carries a terminal ``kernel_decisions`` row is a lookup
    (rule ``R6``), a replayed payment is refused by
    ``FULFILLMENT_EVIDENCE_DUPLICATE``, and every unique constraint a replay
    could touch is mapped by ``provenance_db.retry.UNIQUE_VIOLATION_MAP``. The
    one thing this function reads first is which proposals are already decided,
    because the revision positioning must not run twice and must know how many
    commits each case is about to take -- both questions with a row-level
    answer rather than a guess.
    """
    from scripts.seed.proposals import (
        CURATED_PROPOSALS,
        fulfillment_proposal_ids,
        fulfillment_proposals,
        proposed_obligation_content,
    )

    report = ReplayReport(profile=profile, obligations=proposed_obligation_content())

    fulfillment_ids = fulfillment_proposal_ids()
    every_id = [s.proposal.proposal_id for s in CURATED_PROPOSALS] + list(fulfillment_ids.values())
    with dbmod.connect_as("pv_migrator", database=database) as conn:
        decided = _decided_proposal_ids(conn, every_id)

    pending = [s for s in CURATED_PROPOSALS if s.proposal.proposal_id not in decided]
    pending_fulfillments = {
        case_slug for case_slug, pid in fulfillment_ids.items() if pid not in decided
    }
    report.replayed = (
        len(CURATED_PROPOSALS) + len(fulfillment_ids) - len(pending) - len(pending_fulfillments)
    )

    # Every commit this run will make, per case, decided before a row moves.
    planned: dict[str, int] = {}
    for seeded in pending:
        planned[seeded.case_slug] = planned.get(seeded.case_slug, 0) + 1
    for case_slug in pending_fulfillments:
        planned[case_slug] = planned.get(case_slug, 0) + 1

    if planned:
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            report.positioned_cases = _position_case_revision(conn, planned)

    dsn = dbmod.role_dsn("pv_kernel_writer", database=database)
    results: list[tuple[str, Any]] = []

    # --- pass one: claims, beliefs, grounding, obligations ----------------
    if pending:
        with dbmod.connect_as("pv_app_reader_writer", database=database) as conn:
            _register_proposals(conn, pending)
        loop = _new_event_loop()
        try:
            results.extend(loop.run_until_complete(_commit_pending(str(dsn), pending)))
        finally:
            loop.close()

    # --- pass two: the payments, against the obligations pass one made ----
    if pending_fulfillments:
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            minted = _commitment_ids_by_case(conn)
        second = [
            seeded
            for seeded in fulfillment_proposals(minted)
            if seeded.case_slug in pending_fulfillments
        ]
        report.fulfillments_admitted = len(second)
        if second:
            with dbmod.connect_as("pv_app_reader_writer", database=database) as conn:
                _register_proposals(conn, second)
            loop = _new_event_loop()
            try:
                results.extend(loop.run_until_complete(_commit_pending(str(dsn), second)))
            finally:
                loop.close()

    lines: list[str] = []
    for case_slug, result in results:
        decision = str(result.decision)
        lines.append(f"{case_slug}={decision}")
        if decision.startswith("ACCEPTED"):
            report.committed += 1
        elif decision == "NOOP_DUPLICATE":
            report.replayed += 1
        else:
            report.rejected += 1
    report.decisions = tuple(lines)
    return report


def replay_curated_proposals(profile: str, *, database: str | None = None) -> str:
    """Step 9. Reports; never substitutes.

    Returns a status string that ``SeedReport`` carries into the run summary and
    that ``ops/gates/PHASE_02.md`` can quote verbatim.

    What each profile does, stated rather than implied:

    ``all`` / ``hero``
        Replay the curated proposals through ``MemoryKernel.commit()``. This is
        the profile ``make seed`` should use now that Phase 4 has landed.
    ``isolation``
        Skip. The two isolation tenants exist to prove the vector-index prefix
        and the tenancy foreign keys, and their corpora carry no curated cases;
        there is nothing for the Kernel to decide.
    ``schema-only``
        Skip, and say so. The name predates Phase 4 and stays meaningful: it is
        what a run wants when the Kernel is deliberately out of the picture --
        a migration drill, or a rebuild of the evidence corpus alone.
    """
    if profile == "schema-only":
        return (
            "deferred (--profile schema-only): the Kernel replay is skipped by request. "
            "claims, beliefs, belief_versions, belief_support, kernel_decisions and "
            "memory_proposals stay empty. Use --profile all to replay them."
        )
    if profile == "isolation":
        return "skipped (--profile isolation): the isolation tenants carry no curated cases"
    if not kernel_is_available():
        return (
            "DEFERRED: MemoryKernel.commit() is not importable from this environment "
            f"({_KERNEL_MODULE}). claims, beliefs, belief_versions, belief_support, "
            "kernel_decisions and memory_proposals stay empty. Raw INSERTs here would "
            "create a second canonical writer and are forbidden (70_TASK_PLAN.md T2.8 "
            "step 9)."
        )
    return run_replay(database=database, profile=profile).line()


# ---------------------------------------------------------------------------
# Step 10 -- the retraction UPDATE, as pv_kernel_writer
# ---------------------------------------------------------------------------

#: ``10_DATABASE_DDL.md`` section 5.6, transcribed. The trailing
#: ``AND retraction_status = 'ACTIVE'`` is what makes a replayed correction
#: affect zero rows, which is the seed's idempotence on this table.
RETRACTION_SQL = """
UPDATE evidence_items
SET retraction_status        = %s,
    retracted_at             = %s,
    retracted_by_evidence_id = %s,
    retraction_reason_code   = %s
WHERE tenant_id = %s AND user_id = %s AND id = %s
  AND retraction_status = 'ACTIVE'
"""


def apply_retractions(conn: psycopg.Connection[Any]) -> int:
    """Step 10. Embeddings are untouched -- only the status block moves."""
    applied = 0
    with conn.cursor() as cur:
        for fixture in RETRACTION_FIXTURES:
            retracted_by = (
                None
                if fixture.retracted_by_slug is None
                else sid("evidence", fixture.retracted_by_slug)
            )
            cur.execute(
                RETRACTION_SQL,
                (
                    fixture.retraction_status,
                    fixture.observed_at,
                    retracted_by,
                    fixture.retraction_reason_code,
                    fixture.tenant_id,
                    fixture.user_id,
                    fixture.id,
                ),
            )
            applied += cur.rowcount
    conn.commit()
    return applied


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_seed(
    *,
    profile: str = "all",
    reset: bool = False,
    perturb: bool = False,
    restore: bool = False,
    embeddings_mode: str = "live",
    database: str | None = None,
) -> SeedReport:
    started = time.perf_counter()
    report = SeedReport(profile=profile, reset=reset, perturb=perturb)
    now = DEMO_ANCHOR_UTC

    want_hero = profile in {"all", "hero", "schema-only"}
    want_isolation = profile in {"all", "isolation", "schema-only"}

    # --- steps 2 and 3 -- as pv_migrator ----------------------------------
    with dbmod.connect_as("pv_migrator", database=database) as conn:
        if reset:
            print("  [2] truncating in reverse foreign-key order", flush=True)
            dbmod.truncate_all(conn)
        print("  [3] small planes as pv_migrator", flush=True)
        report.rows.update(load_small_planes(conn))

    # --- the corpus this profile wants ------------------------------------
    artifacts: list[SeedArtifact] = []
    evidence: list[SeedEvidence] = []
    if want_hero:
        artifacts.extend(a for a in curated_artifacts() if isinstance(a, SeedArtifact))
        evidence.extend(e for e in curated_evidence() if isinstance(e, SeedEvidence))
    decoys = [
        d
        for d in generate_decoys()
        if (d.bucket == "hero" and want_hero) or (d.bucket != "hero" and want_isolation)
    ]
    artifacts.extend(d.to_artifact() for d in decoys)
    evidence.extend(d.to_evidence() for d in decoys)

    # --- step 4, and the decision that guards it ---------------------------
    #
    # Section 23 makes dropping the ANN index mandatory *before a bulk load*,
    # for two vendor reasons that both concern loading: IMPORT INTO is
    # unsupported on a vector-indexed table, and inserts into one pay ANN
    # partition maintenance per row.
    #
    # Neither reason applies when there is nothing to load. Measured on this
    # cluster, rebuilding this index over 18,035 rows takes 52 minutes 56
    # seconds (job 1202578737981063169, 15:51:45 to 16:44:41 UTC) -- not the
    # "one to two minutes" 41_RUNBOOK.md section 4.2 predicts. Dropping it
    # unconditionally would make `make seed` twice, which is the G2.6
    # idempotence assertion, cost nearly two hours and leave the index absent
    # for most of that window: section 23's own first failure mode, arriving by
    # way of the fix for its second.
    #
    # So the seed asks first. Every id is a uuid5, so "is there work?" is a set
    # difference over primary keys rather than a guess.
    with dbmod.connect_as("pv_migrator", database=database) as conn:
        seen_evidence = dbmod.existing_ids(conn, "evidence_items")
        seen_artifacts = dbmod.existing_ids(conn, "source_artifacts")
    pending_artifacts = [a for a in artifacts if a.id not in seen_artifacts]
    pending_evidence = [e for e in evidence if e.id not in seen_evidence]
    needs_load = bool(pending_artifacts or pending_evidence)
    report.rows_pending = len(pending_evidence)

    if len(pending_evidence) >= dbmod.ANN_DROP_THRESHOLD:
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            print(
                f"  [4] {len(pending_evidence)} evidence rows to load; "
                f"DROP INDEX IF EXISTS {dbmod.ANN_INDEX_NAME} CASCADE",
                flush=True,
            )
            report.index_dropped = dbmod.drop_ann_index(conn)
    elif needs_load:
        print(
            f"  [4] {len(pending_evidence)} evidence rows to load, below the "
            f"{dbmod.ANN_DROP_THRESHOLD}-row threshold: ANN index left in place",
            flush=True,
        )
    else:
        print(
            "  [4] corpus already complete: 0 rows to load, ANN index left in place",
            flush=True,
        )

    # --- step 5 -- embeddings, cache-first ---------------------------------
    print(f"  [5] resolving {len(pending_evidence)} embeddings ({embeddings_mode})", flush=True)
    resolver = EmbeddingResolver(mode=embeddings_mode)
    texts = [embedding_input(e) for e in pending_evidence]
    vectors = resolver.resolve(texts, label="corpus")
    report.live_bedrock_calls = resolver.live_calls
    report.embedding_cache_hits = resolver.cache_hits

    # --- step 6 -- the bulk load, as pv_app_reader_writer ------------------
    #
    # The try/finally is section 23's first failure mode, made unreachable
    # rather than merely documented: a load that raises half way through would
    # otherwise leave the ANN index dropped, the demo would still work on a
    # brute-force scan over 16,035 rows, and G6.2's EXPLAIN would fail days
    # later with nothing pointing at the seed run that caused it.
    try:
        if needs_load:
            with dbmod.connect_as("pv_app_reader_writer", database=database) as conn:
                print(f"  [6] source_artifacts: {len(pending_artifacts)} rows", flush=True)
                report.rows["source_artifacts"] = dbmod.insert_batches(
                    conn,
                    "source_artifacts",
                    _ARTIFACT_COLUMNS,
                    [_artifact_tuple(a, now) for a in pending_artifacts],
                    label="source_artifacts",
                )
                print(f"  [6] evidence_items: {len(pending_evidence)} rows", flush=True)
                report.rows["evidence_items"] = dbmod.insert_batches(
                    conn,
                    "evidence_items",
                    _EVIDENCE_COLUMNS,
                    [_evidence_tuple(e, vectors, now) for e in pending_evidence],
                    placeholders=_EVIDENCE_PLACEHOLDERS,
                    label="evidence_items",
                )
        else:
            print("  [6] nothing to load", flush=True)
    finally:
        # --- steps 7 and 8 -- as pv_migrator ------------------------------
        #
        # Unconditional, and inside `finally`, because the only state worse than
        # a slow seed is a seed that returns success with the ANN index missing:
        # the demo still works on a brute-force scan over 16,035 rows and G6.2
        # fails days later with nothing pointing back here. When the index is
        # already present this costs two reads.
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            if dbmod.ann_index_exists(conn):
                print("  [7] evidence_embedding_ann_idx already present", flush=True)
                report.index_rebuilt = True
            else:
                print("  [7] CREATE VECTOR INDEX evidence_embedding_ann_idx", flush=True)
                dbmod.create_ann_index(conn)
                print("  [8] waiting for the schema-change job", flush=True)
                dbmod.wait_for_index_job(conn)
                report.index_rebuilt = dbmod.ann_index_exists(conn)

    # --- step 9 -- the Kernel replay, as pv_kernel_writer ------------------
    report.step_9_status = replay_curated_proposals(profile, database=database)
    print(f"  [9] {report.step_9_status}", flush=True)

    # --- step 10 -- retractions, as pv_kernel_writer -----------------------
    if want_hero and not perturb:
        with dbmod.connect_as("pv_kernel_writer", database=database) as conn:
            report.retractions_applied = apply_retractions(conn)
        print(f"  [10] retraction fixtures applied: {report.retractions_applied}", flush=True)
    else:
        print("  [10] retraction fixtures skipped for this profile", flush=True)

    # --- the perturbation, applied last so it mutates a complete seed ------
    if perturb:
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            report.perturbation = apply_perturbation(conn)
        print(f"  [perturb] {report.perturbation}", flush=True)
    elif restore:
        with dbmod.connect_as("pv_migrator", database=database) as conn:
            report.perturbation = revert_perturbation(conn)
        print(f"  [restore] {report.perturbation}", flush=True)

    report.seconds = time.perf_counter() - started
    return report
