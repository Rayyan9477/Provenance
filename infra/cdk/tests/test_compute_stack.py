"""``PvComputeStack``: eight thin workers, sized and retried as specified.

"Thin by design" is a security property rather than a style: no worker holds a
SQL credential, so every worker's effect on canonical state goes through
``/internal/v1`` and the control plane opens the transaction. These tests assert
the absences that make that true.
"""

from __future__ import annotations

from typing import Any

import pytest
from provenance_infra import config as cfg
from provenance_infra import workers as wk
from pv_cdk_testing import as_list, flatten, policy_statements, resources_of


def _functions(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        body["Properties"]["FunctionName"]: body["Properties"]
        for body in resources_of(template, "AWS::Lambda::Function").values()
        if isinstance(body["Properties"].get("FunctionName"), str)
    }


def _statements_for(template: dict[str, Any], fragment: str) -> list[dict[str, Any]]:
    return [
        statement
        for logical, _cfn_type, statement in policy_statements(template)
        if fragment in logical
    ]


def test_the_eight_compute_workers_exist_under_their_canonical_names(
    template_json: dict[str, dict[str, Any]],
) -> None:
    functions = _functions(template_json["PvComputeStack"])
    assert set(functions) == {spec.function_name for spec in wk.COMPUTE_WORKERS}


@pytest.mark.parametrize("spec", wk.COMPUTE_WORKERS, ids=lambda s: s.module)
def test_worker_sizing_matches_section_seven(
    template_json: dict[str, dict[str, Any]], spec: wk.WorkerSpec
) -> None:
    """Memory, timeout, and reserved concurrency are cost and latency decisions
    with written reasons; a drift here is a silent bill or a silent timeout.
    """
    props = _functions(template_json["PvComputeStack"])[spec.function_name]
    assert props["MemorySize"] == spec.memory_mb
    assert props["Timeout"] == spec.timeout_seconds
    if spec.reserved_concurrency is None:
        assert "ReservedConcurrentExecutions" not in props
    else:
        assert props["ReservedConcurrentExecutions"] == spec.reserved_concurrency


@pytest.mark.parametrize("spec", wk.COMPUTE_WORKERS, ids=lambda s: s.module)
def test_every_worker_is_python_312_on_arm64_with_active_tracing(
    template_json: dict[str, dict[str, Any]], spec: wk.WorkerSpec
) -> None:
    """arm64 is roughly 20% cheaper per ms and is what AgentCore Runtime
    requires, so one base image and one dependency set serve both.
    """
    props = _functions(template_json["PvComputeStack"])[spec.function_name]
    assert props["Runtime"] == "python3.12"
    assert props["Architectures"] == ["arm64"]
    assert props["TracingConfig"] == {"Mode": "Active"}
    assert props["LoggingConfig"]["LogFormat"] == "JSON"
    assert props["LoggingConfig"]["ApplicationLogLevel"] == "INFO"


def test_each_worker_writes_only_its_own_log_group(
    template_json: dict[str, dict[str, Any]],
) -> None:
    groups = resources_of(template_json["PvComputeStack"], "AWS::Logs::LogGroup")
    names = {b["Properties"]["LogGroupName"] for b in groups.values()}
    for spec in wk.COMPUTE_WORKERS:
        expected = f"/provenance/{spec.module.replace('_', '-')}"
        assert expected in names, spec.module
    for body in groups.values():
        assert body["Properties"]["RetentionInDays"] == 30


def test_async_workers_have_an_on_failure_destination(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A poisoned event must reach a DLQ instead of vanishing.

    The two SQS consumers are deliberately absent from this list: their failure
    path is the queue's own redrive policy (3 receives to the advocate DLQ, 2 to
    the action DLQ), and giving them an async destination as well would put the
    same failure in two places with two different counts.
    """
    expected_dlq = {
        "SesIngest": "WorkerDlq",
        "TextractComplete": "WorkerDlq",
        "TriggerScheduleManager": "WorkerDlq",
        "OutboxDispatch": "SchedulerDlq",
        "TriggerWakeup": "SchedulerDlq",
        "NotificationDispatch": "NotificationDlq",
    }
    configs = resources_of(template_json["PvComputeStack"], "AWS::Lambda::EventInvokeConfig")
    seen: dict[str, str] = {}
    for logical, body in configs.items():
        destination = body["Properties"].get("DestinationConfig", {}).get("OnFailure")
        for fragment, dlq in expected_dlq.items():
            if not logical.startswith(fragment):
                continue
            assert destination is not None, logical
            assert dlq in flatten(destination), f"{logical} -> {dlq}"
            seen[fragment] = dlq
    assert seen == expected_dlq

    for fragment in ("AdvocateDispatch", "ActionExecute"):
        matching = [b for logical, b in configs.items() if logical.startswith(fragment)]
        assert matching, fragment
        for body in matching:
            assert "DestinationConfig" not in body["Properties"], fragment


def test_retry_counts_match_the_documented_posture(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Retry accounting lives in exactly one place per worker.

    ``trigger_wakeup`` sets Lambda async retries to 0 because the Scheduler
    target owns them; ``outbox_dispatch`` sets 0 because the next tick is the
    retry and a second concurrent claimer is pointless.
    """
    configs = resources_of(template_json["PvComputeStack"], "AWS::Lambda::EventInvokeConfig")
    expected = {
        "TriggerWakeup": 0,
        "OutboxDispatch": 0,
        "SesIngest": 2,
        "TextractComplete": 2,
        "NotificationDispatch": 2,
        "TriggerScheduleManager": 2,
    }
    seen = {}
    for logical, body in configs.items():
        for fragment in expected:
            if logical.startswith(fragment):
                seen[fragment] = body["Properties"]["MaximumRetryAttempts"]
    assert seen == expected


def test_ses_ingest_reaches_only_the_staging_prefix_and_the_ses_prefix(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A fully compromised ``ses_ingest`` can inject a forged inbound artifact
    for a *known* alias, which the Kernel then types as a ``COUNTERPARTY_CLAIM``
    requiring grounding. It cannot make it a fact and cannot make it another
    user's.
    """
    statements = _statements_for(template_json["PvComputeStack"], "SesIngest")
    read = [s for s in statements if s.get("Sid") == "ReadStagedInboundObject"]
    write = [s for s in statements if s.get("Sid") == "WriteCanonicalInboundCopy"]
    assert len(read) == 1 and len(write) == 1
    assert "ses/incoming/*" in flatten(read[0]["Resource"])
    assert "ses/*" in flatten(write[0]["Resource"])
    assert write[0]["Condition"]["StringEquals"]["s3:x-amz-server-side-encryption"] == "aws:kms"

    granted = {
        action for s in statements for action in as_list(s.get("Action")) if isinstance(action, str)
    }
    for forbidden in ("s3:DeleteObject", "events:PutEvents", "ses:SendEmail"):
        assert forbidden not in granted
    assert not any(a.startswith("bedrock") for a in granted)


def test_ses_ingest_is_the_only_worker_holding_the_capability_hmac_key(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 15.6, asserted rather than remembered.

    Inbound mail has no preceding control-plane request, so this worker computes
    its own capability proof and therefore needs ``provenance/crypto``. That
    widens the blast radius of a key already identified as a single point of
    compromise, so a second worker acquiring it must fail a test.
    """
    holders = set()
    for logical, _cfn_type, statement in policy_statements(template_json["PvComputeStack"]):
        if "secretsmanager:GetSecretValue" not in as_list(statement.get("Action")):
            continue
        # Cross-stack, so the resource renders as an Fn::ImportValue whose
        # export name embeds the producing construct's logical id.
        if "CryptoSecret" in flatten(statement.get("Resource")):
            holders.add(logical.split("Role")[0])
    assert holders == {"SesIngest"}, holders


def test_kms_use_by_workers_is_confined_to_s3(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``kms:ViaService`` means a compromised worker cannot use the CMK to
    decrypt anything that is not an S3 object -- the ECR image layers share it.
    """
    statements = [
        s
        for _logical, _cfn_type, s in policy_statements(template_json["PvComputeStack"])
        if s.get("Sid") == "UseArtifactKeyThroughS3Only"
    ]
    assert len(statements) == 2
    for statement in statements:
        assert statement["Condition"]["StringEquals"]["kms:ViaService"] == (
            f"s3.{cfg.REGION}.amazonaws.com"
        )


def test_the_notification_worker_can_only_send_from_the_notification_address(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """It can never send an ActionIntent: that comes from ``disputes@`` and only
    the control plane holds it.
    """
    statements = _statements_for(template_json["PvComputeStack"], "NotificationDispatch")
    ses = [s for s in statements if s.get("Sid") == "SendUserNotificationsOnly"]
    assert len(ses) == 1
    condition = ses[0]["Condition"]["StringEquals"]
    assert condition["ses:FromAddress"] == "notifications@provenance.app"
    assert condition["ses:ConfigurationSetName"] == cfg.SES_CONFIGURATION_SET


def test_the_schedule_manager_owns_schedule_lifecycle_and_nothing_else(
    template_json: dict[str, dict[str, Any]],
) -> None:
    statements = _statements_for(template_json["PvComputeStack"], "TriggerScheduleManager")
    scheduler = [s for s in statements if s.get("Sid") == "ManageOneTimeTriggerSchedulesOnly"]
    assert len(scheduler) == 1
    resource = flatten(scheduler[0]["Resource"])
    assert f"schedule/{cfg.TRIGGER_SCHEDULE_GROUP}/*" in resource
    assert cfg.SYSTEM_SCHEDULE_GROUP not in resource


def test_the_only_pass_role_in_compute_is_the_scheduler_one(
    template_json: dict[str, dict[str, Any]],
) -> None:
    pass_roles = [
        (logical, statement)
        for logical, _cfn_type, statement in policy_statements(template_json["PvComputeStack"])
        if "iam:PassRole" in as_list(statement.get("Action"))
    ]
    assert len(pass_roles) == 1
    logical, statement = pass_roles[0]
    assert "TriggerScheduleManager" in logical
    assert statement["Condition"]["StringEquals"]["iam:PassedToService"] == (
        "scheduler.amazonaws.com"
    )


def test_the_two_queue_consumers_are_wired_by_event_source_mapping(
    template_json: dict[str, dict[str, Any]],
) -> None:
    mappings = resources_of(template_json["PvComputeStack"], "AWS::Lambda::EventSourceMapping")
    assert len(mappings) == 2
    for body in mappings.values():
        assert body["Properties"]["BatchSize"] == 1


def test_the_outbox_sweep_fires_once_a_minute_and_sweeps_twice(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``specs/15_API_SPEC.md`` section 13.6 wants every 30 seconds; Scheduler's
    minimum ``rate()`` granularity is one minute.

    Rather than quietly weakening the guarantee, the schedule fires once a minute
    and the handler sweeps twice, 30 seconds apart, inside one invocation. The
    behaviour matches the specification; the mechanism does not match a naive
    reading of it, and that is recorded in section 15.9.
    """
    schedules = resources_of(template_json["PvComputeStack"], "AWS::Scheduler::Schedule")
    assert len(schedules) == 1
    props = next(iter(schedules.values()))["Properties"]
    assert props["Name"] == "provenance-outbox-sweep"
    assert props["GroupName"] == cfg.SYSTEM_SCHEDULE_GROUP
    assert props["ScheduleExpression"] == "rate(1 minute)"
    assert props["FlexibleTimeWindow"] == {"Mode": "OFF"}
    payload = props["Target"]["Input"]
    assert '"passes":2' in payload
    assert '"pass_gap_seconds":30' in payload
    assert props["Target"]["RetryPolicy"]["MaximumRetryAttempts"] == 0
    assert "DeadLetterConfig" in props["Target"]


def test_the_textract_publish_role_can_only_publish_to_one_topic(
    template_json: dict[str, dict[str, Any]],
) -> None:
    statements = _statements_for(template_json["PvComputeStack"], "TextractPublishRole")
    publish = [s for s in statements if s.get("Sid") == "PublishJobCompletion"]
    assert len(publish) == 1
    assert publish[0]["Action"] == "sns:Publish"
    assert "*" not in as_list(publish[0]["Resource"])


def test_pending_worker_modules_are_visible_rather_than_silent() -> None:
    """Phases 8 through 10 write the handlers.

    Until a handler exists the function is bundled from a placeholder that
    raises, so a deploy of an unwritten worker fails loudly on first invocation
    instead of returning a plausible success. This test records which ones are
    still pending at the time it runs; it does not require the list to be empty,
    because Phase 13 is authored ahead of Phases 8 to 10 on purpose.
    """
    pending = wk.pending_worker_modules()
    known = {spec.module for spec in wk.ALL_WORKERS}
    assert set(pending) <= known
    # The four named in 00_IMPLEMENTATION_MAP.md section 5 already have
    # directories; the placeholder decision is about handler.py, not the dir.
    assert wk.PLACEHOLDER_DIR.joinpath("handler.py").is_file()
