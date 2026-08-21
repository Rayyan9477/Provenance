"""The nine Lambda workers: their sizing, their retry posture, and their code.

Sizing and retry values are copied from ``40_INFRA_IAC.md`` sections 7.6 and 7.7
and must not diverge; each one carries the reason it is what it is.

Code resolution is deliberately explicit. ``workers/<module>/`` is the source of
truth when it exists. Phases 8 through 10 write the handlers; until a handler
exists the function is bundled from ``infra/cdk/assets/pending_worker``, whose
``handler`` raises. That is visible in the synthesised template (the asset hash
differs), it is reported by :func:`pending_worker_modules`, and it is asserted by
``tests/test_compute_stack.py`` -- a placeholder that quietly looked like a real
handler would be exactly the kind of thing this pack exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_

# infra/cdk/provenance_infra/workers.py -> infra/cdk -> infra -> <repo root>
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
WORKERS_DIR: Final[Path] = REPO_ROOT / "workers"
PLACEHOLDER_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "assets" / "pending_worker"

# Every worker is thin by design. 01_SYSTEM_ARCHITECTURE_DETAILED.md section 16
# forbids async handlers that mutate several invariant-linked rows without a
# database transaction, so **no worker holds a SQL credential**. Every worker's
# effect on canonical state goes through ``/internal/v1`` with a Cognito M2M
# token and a capability id, and the control plane opens the transaction.
#
# The single exception is ``cognito_post_confirmation``, which writes tenants,
# users, and ingest_aliases in one transaction as ``pv_app_reader_writer``
# because no authenticated principal exists yet for it to call the API with.
HOLDS_SQL_CREDENTIAL: Final[frozenset[str]] = frozenset({"cognito_post_confirmation"})


@dataclass(frozen=True)
class WorkerSpec:
    """One Lambda function, sized and retried per section 7.6 / 7.7."""

    module: str
    function_name: str
    construct_id: str
    memory_mb: int
    timeout_seconds: int
    reserved_concurrency: int | None
    reason: str

    @property
    def timeout(self) -> Duration:
        return Duration.seconds(self.timeout_seconds)


SES_INGEST = WorkerSpec(
    module="ses_ingest",
    function_name="provenance-ses-ingest",
    construct_id="SesIngest",
    memory_mb=1024,
    timeout_seconds=60,
    reserved_concurrency=5,
    reason=(
        "Streams up to 20 MiB of MIME through a SHA-256 and a CopyObject. "
        "Reserved concurrency of 5 is a cost guard: an unbounded function "
        "fronting a mail server is an unbounded bill."
    ),
)
TEXTRACT_COMPLETE = WorkerSpec(
    module="textract_complete",
    function_name="provenance-textract-complete",
    construct_id="TextractComplete",
    memory_mb=1024,
    timeout_seconds=120,
    reserved_concurrency=3,
    reason="Pages the whole Textract block set and writes two normalized objects.",
)
OUTBOX_DISPATCH = WorkerSpec(
    module="outbox_dispatch",
    function_name="provenance-outbox-dispatch",
    construct_id="OutboxDispatch",
    memory_mb=512,
    timeout_seconds=120,
    reserved_concurrency=2,
    reason=(
        "A clock, not a dispatcher. Two sweeps 30 s apart inside one invocation; "
        "the state machine lives in the control plane."
    ),
)
TRIGGER_WAKEUP = WorkerSpec(
    module="trigger_wakeup",
    function_name="provenance-trigger-wakeup",
    construct_id="TriggerWakeup",
    memory_mb=256,
    timeout_seconds=30,
    reserved_concurrency=10,
    reason="One HTTPS POST. Retry accounting belongs to the Scheduler target, not here.",
)
ADVOCATE_DISPATCH = WorkerSpec(
    module="advocate_dispatch",
    function_name="provenance-advocate-dispatch",
    construct_id="AdvocateDispatch",
    memory_mb=512,
    timeout_seconds=30,
    reserved_concurrency=None,
    reason=(
        "Calls POST /internal/v1/events/deliveries and lets the control plane "
        "start the agent run, so the queue consumer never holds an agent capability."
    ),
)
ACTION_EXECUTE = WorkerSpec(
    module="action_execute",
    function_name="provenance-action-execute",
    construct_id="ActionExecute",
    memory_mb=512,
    timeout_seconds=60,
    reserved_concurrency=None,
    reason=(
        "Holds scope action/execute and NO ses:SendEmail. Invariant 4 in IAM: the "
        "only code path to SendEmail runs after the control plane's revalidation "
        "query returns a row."
    ),
)
NOTIFICATION_DISPATCH = WorkerSpec(
    module="notification_dispatch",
    function_name="provenance-notification-dispatch",
    construct_id="NotificationDispatch",
    memory_mb=256,
    timeout_seconds=20,
    reserved_concurrency=None,
    reason="User notification email only, pinned by an ses:FromAddress condition.",
)
TRIGGER_SCHEDULE_MANAGER = WorkerSpec(
    module="trigger_schedule_manager",
    function_name="provenance-trigger-schedule-manager",
    construct_id="TriggerScheduleManager",
    memory_mb=256,
    timeout_seconds=20,
    reserved_concurrency=None,
    reason=(
        "Owns scheduler:CreateSchedule/DeleteSchedule on the provenance-triggers "
        "group and holds the one iam:PassRole grant in the account."
    ),
)
COGNITO_POST_CONFIRMATION = WorkerSpec(
    module="cognito_post_confirmation",
    function_name="provenance-cognito-post-confirmation",
    construct_id="PostConfirmation",
    memory_mb=512,
    timeout_seconds=20,
    reserved_concurrency=None,
    reason=(
        "Writes tenants, users, and ingest_aliases in ONE transaction. A failure "
        "here fails the sign-up, which is correct: a confirmed Cognito user with "
        "no users row is permanently broken, and a retryable sign-up is not."
    ),
)

# ``COGNITO_POST_CONFIRMATION`` is the ninth function and is built in
# ``PvIdentityStack``. See props.ComputeExports for why.
COMPUTE_WORKERS: Final[tuple[WorkerSpec, ...]] = (
    SES_INGEST,
    TEXTRACT_COMPLETE,
    OUTBOX_DISPATCH,
    TRIGGER_WAKEUP,
    ADVOCATE_DISPATCH,
    ACTION_EXECUTE,
    NOTIFICATION_DISPATCH,
    TRIGGER_SCHEDULE_MANAGER,
)
ALL_WORKERS: Final[tuple[WorkerSpec, ...]] = (*COMPUTE_WORKERS, COGNITO_POST_CONFIRMATION)


def has_real_handler(spec: WorkerSpec) -> bool:
    """True when ``workers/<module>/handler.py`` exists in the working tree."""
    return (WORKERS_DIR / spec.module / "handler.py").is_file()


def pending_worker_modules() -> tuple[str, ...]:
    """The workers whose handler has not been written yet.

    Reported by ``tests/test_compute_stack.py`` so the count is visible rather
    than discovered at deploy time.
    """
    return tuple(spec.module for spec in ALL_WORKERS if not has_real_handler(spec))


def resolve_code(spec: WorkerSpec) -> lambda_.Code:
    """The real worker directory when it exists, the raising placeholder otherwise."""
    source = WORKERS_DIR / spec.module
    if has_real_handler(spec):
        return lambda_.Code.from_asset(str(source))
    return lambda_.Code.from_asset(str(PLACEHOLDER_DIR))


# Every worker uses the same entry point so the template does not encode which
# ones are still placeholders in the handler string.
HANDLER: Final[str] = "handler.handler"
RUNTIME: Final[lambda_.Runtime] = lambda_.Runtime.PYTHON_3_12
# arm64: roughly 20% cheaper per ms, and every dependency is pure Python or has
# an arm wheel. It is also the architecture AgentCore Runtime requires, so one
# base image and one dependency set serve both.
ARCHITECTURE: Final[lambda_.Architecture] = lambda_.Architecture.ARM_64
