"""The alarm catalogue, as data.

``quality/21_OBSERVABILITY_ANALYTICS.md`` section 8.3 keeps the alarms in YAML at
``ops/observability/alarms.yaml`` and prints an eight-line loader. That file is
outside this builder's write boundary, so the same contract is expressed here as
frozen dataclasses and the loader lives in ``observability_stack.py``. The YAML
is the contract; this is a second rendering of it, and the two must be
reconciled when ``ops/observability/`` is created. (Reported as D-13-004.)

Every threshold carries the reasoning that produced it, because none of them was
derived from observed production behaviour -- there is none. The first person to
see a false page should be able to evaluate the reasoning rather than guess at
intent.

``treat_missing_data: notBreaching`` is set on every alarm precisely so a quiet
system reports ``OK`` rather than ``INSUFFICIENT_DATA``, which would otherwise
fail ``G13.7`` on a system that is behaving perfectly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from provenance_infra import config as cfg

GREATER = "GreaterThanThreshold"
LESS = "LessThanThreshold"


@dataclass(frozen=True)
class MetricRef:
    """One CloudWatch metric, fully qualified."""

    namespace: str
    metric: str
    statistic: str = "Sum"
    period_seconds: int = 300
    dimensions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MathSpec:
    """A metric-math alarm: an expression over named metric variables."""

    expression: str
    variables: dict[str, MetricRef]


@dataclass(frozen=True)
class AlarmSpec:
    name: str
    severity: str
    threshold: float
    comparison: str
    evaluation_periods: int
    reason: str
    runbook: str
    metric: MetricRef | None = None
    math: MathSpec | None = None

    def __post_init__(self) -> None:
        if (self.metric is None) == (self.math is None):
            raise ValueError(f"{self.name}: exactly one of metric/math must be set")


# ---------------------------------------------------------------------------
# P1 correctness. These mean the record may be wrong. Threshold zero on every
# one: they are not tuned, they are invariants, and a non-zero value means
# something the architecture says cannot happen has happened.
# ---------------------------------------------------------------------------
_P1: Final[tuple[AlarmSpec, ...]] = (
    AlarmSpec(
        name="provenance-retrieval-retracted-leakage",
        severity="P1",
        metric=MetricRef("Provenance/Retrieval", "retracted_leakage", period_seconds=60),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "Retracted, superseded, or quarantined evidence reached a caller. "
            "Corrected evidence resurfacing and re-grounding a belief is a silent, "
            "plausible-looking failure."
        ),
        runbook=(
            "Stop ingestion. Check the retraction predicate in "
            "agent_evidence_retrieval_v1 and in the repository query."
        ),
    ),
    AlarmSpec(
        name="provenance-retrieval-cross-user-rows",
        severity="P1",
        metric=MetricRef("Provenance/Retrieval", "cross_user_rows", period_seconds=60),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "ANN or rerank returned another user's row. The user_id vector prefix "
            "makes this a schema property, so a non-zero value means the prefix was "
            "bypassed."
        ),
        runbook="Revoke pv_agent_reader, run the isolation suite, EXPLAIN for the prefix column.",
    ),
    AlarmSpec(
        name="provenance-state-proof-ungrounded-belief",
        severity="P1",
        metric=MetricRef("Provenance/StateProof", "ungrounded_belief"),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "A canonical belief version rendered with zero grounding edges. The "
            "Kernel refuses to create one and the CHECK constraint refuses to store "
            "one, so this is unreachable in a correct system."
        ),
        runbook="Data-integrity incident. Run make db-verify; V2 identifies the row.",
    ),
    AlarmSpec(
        name="provenance-auth-tenant-mismatch",
        severity="P1",
        metric=MetricRef("Provenance/Auth", "tenant_mismatch"),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "A cross-tenant access attempt. In a correct system this never fires, so "
            "a single occurrence is a bug or an attack."
        ),
        runbook="Log Insights on client_id and route.",
    ),
    AlarmSpec(
        name="provenance-proposal-foreign-provenance",
        severity="P1",
        metric=MetricRef(
            "Provenance/Agent",
            "proposal_decision",
            dimensions={"decision": "REJECTED_INVALID_PROVENANCE"},
        ),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "A proposal named a user or referenced evidence outside its capability. "
            "The tripwire is intentional: a correct system cannot produce one."
        ),
        runbook="Compare MemoryProposal.user_id to the agent_runs binding.",
    ),
    AlarmSpec(
        name="provenance-model-tier-r-fallback",
        severity="P1",
        metric=MetricRef("Provenance/Model", "fallback", dimensions={"from_tier": "R"}),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "Tier R never downgrades to a weaker model. A Tier R fallback is a router "
            "violation of a frozen decision, not a degradation."
        ),
        runbook="Roll back the router change. Failure must produce PENDING_HUMAN_REVIEW.",
    ),
)

# ---------------------------------------------------------------------------
# Delivery and durability.
# ---------------------------------------------------------------------------
_DLQ_VARIABLES: Final[dict[str, MetricRef]] = {
    f"q{index}": MetricRef(
        "AWS/SQS",
        "ApproximateNumberOfMessagesVisible",
        statistic="Maximum",
        dimensions={"QueueName": queue_name},
    )
    for index, queue_name in enumerate(cfg.DLQ_NAMES)
}

_DELIVERY: Final[tuple[AlarmSpec, ...]] = (
    AlarmSpec(
        name="provenance-outbox-dead",
        severity="P1",
        metric=MetricRef("Provenance/Outbox", "dead_count", statistic="Maximum"),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason="An event exhausted 1s/5s/30s/2m/10m and will never be delivered.",
        runbook="Inspect last_error, fix the cause, then replay; processed_events dedupes.",
    ),
    AlarmSpec(
        name="provenance-outbox-pending-age",
        severity="P2",
        metric=MetricRef(
            "Provenance/Outbox",
            "oldest_pending_age_seconds",
            statistic="Maximum",
            period_seconds=60,
        ),
        threshold=120,
        comparison=GREATER,
        evaluation_periods=2,
        reason=(
            "The scheduled sweep runs every 30 s, so 120 s is four missed sweeps -- "
            "past any plausible jitter and short of a demo-ruining backlog."
        ),
        runbook="POST /internal/v1/events/outbox/sweep manually.",
    ),
    AlarmSpec(
        name="provenance-dlq-depth",
        severity="P1",
        # 21_OBSERVABILITY_ANALYTICS.md section 8.3 dimensions this alarm on a
        # queue named ``provenance-events-dlq``, which no stack creates:
        # 40_INFRA_IAC.md section 6.2 defines five DLQs and none of them has
        # that name. Pointing an alarm at a non-existent queue would leave it in
        # INSUFFICIENT_DATA forever and would fail G13.7 while looking correct,
        # so this alarm is the MAX across the five DLQs that actually exist.
        # (Reported as D-13-005.)
        math=MathSpec(
            expression=f"MAX([{','.join(_DLQ_VARIABLES)}])",
            variables=_DLQ_VARIABLES,
        ),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason="A poisoned message stopped retrying on one of the five DLQs.",
        runbook=(
            "Read provenance-trace-id and provenance-event-id from the message "
            "attributes and open the trace; the payload may be malformed, so do not "
            "parse it first."
        ),
    ),
    AlarmSpec(
        name="provenance-outbox-attempt-p99",
        severity="P3",
        metric=MetricRef("Provenance/Outbox", "attempt_count_p99", statistic="Maximum"),
        threshold=2,
        comparison=GREATER,
        evaluation_periods=3,
        reason="Publish failures becoming routine rather than exceptional.",
        runbook="Check EventBridge PutEvents throttling and the bus name.",
    ),
)

# ---------------------------------------------------------------------------
# Transactional health.
# ---------------------------------------------------------------------------
_TRANSACTIONAL: Final[tuple[AlarmSpec, ...]] = (
    AlarmSpec(
        name="provenance-kernel-retry-rate",
        severity="P2",
        math=MathSpec(
            expression="IF(commits > 0, retries / commits, 0)",
            variables={
                "retries": MetricRef(
                    "Provenance/Db",
                    "serialization_retry",
                    dimensions={"txn_name": "kernel_commit"},
                ),
                "commits": MetricRef(
                    "Provenance/Agent",
                    "proposal_decision",
                    dimensions={"decision": "ACCEPTED"},
                ),
            },
        ),
        threshold=0.5,
        comparison=GREATER,
        evaluation_periods=3,
        reason=(
            "SQLSTATE 40001 retries are normal under contention on one hot case; the "
            "budget is five attempts. More than one retry per two commits means the "
            "contention set has grown."
        ),
        runbook="Check retry_count distribution per case.",
    ),
    AlarmSpec(
        name="provenance-kernel-retry-exhausted",
        severity="P2",
        metric=MetricRef("Provenance/Db", "retry_exhausted"),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason="The five-attempt budget ran out and the caller received 503.",
        runbook="Expected under deliberate contention tests; unexpected otherwise.",
    ),
    AlarmSpec(
        name="provenance-db-pool-saturation",
        severity="P2",
        math=MathSpec(
            expression="MAX(in_use / size)",
            variables={
                "in_use": MetricRef(
                    "Provenance/Db", "pool_in_use", statistic="Maximum", period_seconds=60
                ),
                "size": MetricRef(
                    "Provenance/Db", "pool_size", statistic="Maximum", period_seconds=60
                ),
            },
        ),
        threshold=0.85,
        comparison=GREATER,
        evaluation_periods=5,
        reason="One pool per SQL role; saturation on any role stalls that role's callers.",
        runbook="Identify the role dimension. Kernel saturation is the one that blocks commits.",
    ),
)

# ---------------------------------------------------------------------------
# Actions.
# ---------------------------------------------------------------------------
_ACTIONS: Final[tuple[AlarmSpec, ...]] = (
    AlarmSpec(
        name="provenance-action-abort-rate",
        severity="P2",
        math=MathSpec(
            expression="IF(exec > 0, aborted / exec, 0)",
            variables={
                "aborted": MetricRef("Provenance/Action", "aborted_stale", period_seconds=3600),
                "exec": MetricRef(
                    "Provenance/Action",
                    "intent_transition",
                    dimensions={"to_status": "EXECUTED"},
                    period_seconds=3600,
                ),
            },
        ),
        threshold=0.2,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "A stale approval aborting is correct behaviour, not a failure. A sustained "
            "high rate means approvals are racing commits."
        ),
        runbook="Check stale_reason. CASE_REVISION_MOVED on every execution is the bug.",
    ),
    AlarmSpec(
        name="provenance-action-failed-final",
        severity="P1",
        metric=MetricRef(
            "Provenance/Action",
            "intent_transition",
            dimensions={"to_status": "FAILED_FINAL"},
        ),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason="A human-approved action did not reach the counterparty and will not retry.",
        runbook="Check provider and error_code. SES suppression and identity issues look alike.",
    ),
)

# ---------------------------------------------------------------------------
# Quality drift and availability.
# ---------------------------------------------------------------------------
_QUALITY: Final[tuple[AlarmSpec, ...]] = (
    AlarmSpec(
        name="provenance-extraction-schema-invalid-rate",
        severity="P3",
        math=MathSpec(
            expression="IF(calls > 0, invalid / calls, 0)",
            variables={
                "invalid": MetricRef(
                    "Provenance/Agent", "extraction_schema_invalid", period_seconds=1800
                ),
                "calls": MetricRef(
                    "Provenance/Model",
                    "invocations",
                    dimensions={"node": "extract_structured_evidence"},
                    period_seconds=1800,
                ),
            },
        ),
        threshold=0.10,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "One repair attempt is budgeted per node. Above 10% the repair budget is "
            "absorbing a systematic problem rather than an occasional one."
        ),
        runbook="Diff prompt_version and model_id against the last known-good run.",
    ),
    AlarmSpec(
        name="provenance-retrieval-latency",
        severity="P3",
        metric=MetricRef(
            "Provenance/Retrieval",
            "latency_ms",
            statistic="p95",
            dimensions={"stage": "TOTAL"},
        ),
        threshold=400,
        comparison=GREATER,
        evaluation_periods=1,
        reason="The eight-stage p95 budget is 210 ms; 400 ms is roughly double.",
        runbook="Break down by stage. Stage D is ANN; stage F is grounding expansion.",
    ),
    AlarmSpec(
        name="provenance-retrieval-index-not-used",
        severity="P2",
        metric=MetricRef("Provenance/Retrieval", "vector.index_used", dimensions={"used": "false"}),
        threshold=0,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "A full scan may return correct results and is still a failure: the sponsor "
            "claim is distributed vector indexing, and G6.2 treats a full-scan plan as a "
            "gate failure even when the answer is right."
        ),
        runbook="EXPLAIN the retrieval query. Confirm evidence_embedding_ann_idx by name.",
    ),
    AlarmSpec(
        name="provenance-identity-unresolved-share",
        severity="P3",
        math=MathSpec(
            expression="IF(total > 0, unresolved / total, 0)",
            variables={
                "unresolved": MetricRef(
                    "Provenance/Retrieval",
                    "identity.status",
                    dimensions={"status": "UNRESOLVED"},
                    period_seconds=3600,
                ),
                "total": MetricRef("Provenance/Retrieval", "identity.status", period_seconds=3600),
            },
        ),
        threshold=0.30,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "Abstention is cheap and over-abstaining is tolerable, but a third of traffic "
            "unresolvable means the identity extractors stopped matching real documents."
        ),
        runbook="Check retrieval.identity.exact_match_count first.",
    ),
    AlarmSpec(
        name="provenance-exact-identifier-hits-zero",
        severity="P2",
        metric=MetricRef("Provenance/Retrieval", "identity.exact_match_count", period_seconds=3600),
        threshold=1,
        comparison=LESS,
        evaluation_periods=1,
        reason="The earliest possible warning that a counterparty changed its invoice format.",
        runbook="Run the four-cause table in 13_RETRIEVAL_SPEC.md section 15.3.",
    ),
    AlarmSpec(
        name="provenance-prompt-cache-cold",
        severity="P3",
        # 21_OBSERVABILITY_ANALYTICS.md dimensions this on
        # ``anthropic.claude-opus-5``, which is denied on this account and is not
        # invocable in any form. The dimension is the Tier R id actually in
        # force. (Reported as D-13-007.)
        metric=MetricRef(
            "Provenance/Model",
            "cache_read_input_tokens",
            dimensions={"model_id": cfg.BEDROCK_REASONING_MODEL_ID},
            period_seconds=3600,
        ),
        threshold=1,
        comparison=LESS,
        evaluation_periods=1,
        reason=(
            "Verification is a metric, not an assumption. A sustained zero on a Tier R "
            "node means something is silently invalidating the stable system prefix."
        ),
        runbook="Diff the rendered system block across two invocations; it must be identical.",
    ),
    AlarmSpec(
        name="provenance-cost-per-artifact",
        severity="P3",
        metric=MetricRef(
            "Provenance/Cost", "artifact_usd_micros", statistic="p95", period_seconds=3600
        ),
        threshold=250000,
        comparison=GREATER,
        evaluation_periods=1,
        reason=(
            "USD 0.25 per artifact. Not a hard economic limit -- it is the point at which "
            "the model route deserves a second look."
        ),
        runbook="Break down by tier and node.",
    ),
    AlarmSpec(
        name="provenance-api-5xx-rate",
        severity="P2",
        math=MathSpec(
            expression="IF(total > 0, errors / total, 0)",
            variables={
                "errors": MetricRef(
                    "Provenance/Api", "requests", dimensions={"status_class": "5xx"}
                ),
                "total": MetricRef("Provenance/Api", "requests"),
            },
        ),
        threshold=0.02,
        comparison=GREATER,
        evaluation_periods=2,
        reason="2% of requests failing is visible to a user and certain to happen on camera.",
        runbook="Group by route. A single route means a handler; all routes means a dependency.",
    ),
    AlarmSpec(
        name="provenance-db-connection-errors",
        severity="P2",
        metric=MetricRef("Provenance/Db", "connection_errors"),
        threshold=2,
        comparison=GREATER,
        evaluation_periods=1,
        reason="CockroachDB Cloud reachability. Three failures in five minutes is not transient.",
        runbook="ccloud cluster list; check the certificate and the cluster state.",
    ),
)

ALARMS: Final[tuple[AlarmSpec, ...]] = _P1 + _DELIVERY + _TRANSACTIONAL + _ACTIONS + _QUALITY

# One red light for the presenter. Everything in this rule breaks the hero flow
# visibly; nothing in it is a slow-burning quality signal.
COMPOSITE_NAME: Final[str] = "provenance-demo-not-ready"
COMPOSITE_MEMBERS: Final[tuple[str, ...]] = (
    "provenance-api-5xx-rate",
    "provenance-db-connection-errors",
    "provenance-outbox-pending-age",
    "provenance-dlq-depth",
    "provenance-retrieval-index-not-used",
)

# The four G13.7 names the gate calls out explicitly.
G13_7_REQUIRED: Final[tuple[str, ...]] = (
    "provenance-outbox-pending-age",
    "provenance-dlq-depth",
    "provenance-kernel-retry-rate",
    "provenance-action-abort-rate",
)
