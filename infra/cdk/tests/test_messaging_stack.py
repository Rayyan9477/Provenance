"""``PvMessagingStack``: one bus, seven queues, five rules across two stacks.

The rule patterns are verbatim from ``specs/15_API_SPEC.md`` section 11.2. A
drift here does not fail loudly -- EventBridge simply stops matching, and the
consumer goes quiet. That is why the patterns are asserted field by field.
"""

from __future__ import annotations

from typing import Any

from provenance_infra import config as cfg
from provenance_infra.stacks.messaging_stack import (
    ADVOCATE_DETAIL_TYPES,
    EVENT_SOURCE,
    NOTIFICATION_DETAIL_TYPES,
    TRIGGER_SCHEDULE_DETAIL_TYPES,
)
from pv_cdk_testing import all_resources_of, flatten, policy_statements, resources_of


def _queues(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        body["Properties"]["QueueName"]: body["Properties"]
        for body in resources_of(template, "AWS::SQS::Queue").values()
    }


def _rules(template_json: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        body["Properties"]["Name"]: body["Properties"]
        for body in all_resources_of(template_json, "AWS::Events::Rule").values()
        if "Name" in body["Properties"]
    }


def test_one_custom_bus_and_the_default_bus_is_never_used(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """An AWS service event can never match a rule written for a Provenance
    event, and ``source`` is a second filter on every rule rather than the only
    one.
    """
    buses = resources_of(template_json["PvMessagingStack"], "AWS::Events::EventBus")
    assert {b["Properties"]["Name"] for b in buses.values()} == {cfg.DOMAIN_BUS_NAME}
    for props in _rules(template_json).values():
        assert "EventBusName" in props, "a rule on the default bus"
        assert props["EventPattern"]["source"] == [EVENT_SOURCE]


def test_seven_queues_two_work_and_five_dead_letter(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 2.1 says "four SQS queues, four DLQs"; section 6.2 defines two work
    queues and five DLQs. The specific section wins, and the count matters
    because the ``provenance-dlq-depth`` alarm has to cover all of them.
    (D-13-005.)
    """
    queues = _queues(template_json["PvMessagingStack"])
    assert set(queues) == {cfg.ADVOCATE_QUEUE_NAME, cfg.ACTION_QUEUE_NAME, *cfg.DLQ_NAMES}
    assert len(queues) == 7


def test_every_queue_requires_tls_and_server_side_encryption(
    template_json: dict[str, dict[str, Any]],
) -> None:
    queues = _queues(template_json["PvMessagingStack"])
    for name, props in queues.items():
        # QueueEncryption.KMS_MANAGED renders as the AWS-managed SQS key.
        assert props.get("KmsMasterKeyId") == "alias/aws/sqs", name
    # enforceSSL renders as an aws:SecureTransport deny in the queue policy.
    denies = [
        statement
        for _logical, cfn_type, statement in policy_statements(template_json["PvMessagingStack"])
        if cfn_type == "AWS::SQS::QueuePolicy" and statement.get("Effect") == "Deny"
    ]
    assert len(denies) == 7
    for statement in denies:
        assert statement["Condition"]["Bool"]["aws:SecureTransport"] == "false"


def test_the_action_queue_gives_up_after_two_receives(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Every other failure in this system is safe to retry.

    A queued outbound message is the one place where automatic replay could send
    a letter the user no longer wants, so it gets two attempts and a human
    decides.
    """
    queues = _queues(template_json["PvMessagingStack"])
    advocate = queues[cfg.ADVOCATE_QUEUE_NAME]["RedrivePolicy"]
    action = queues[cfg.ACTION_QUEUE_NAME]["RedrivePolicy"]
    assert advocate["maxReceiveCount"] == 3
    assert action["maxReceiveCount"] == 2


def test_work_queue_visibility_is_six_times_the_consumer_timeout(
    template_json: dict[str, dict[str, Any]],
) -> None:
    queues = _queues(template_json["PvMessagingStack"])
    assert queues[cfg.ADVOCATE_QUEUE_NAME]["VisibilityTimeout"] == 180
    assert queues[cfg.ACTION_QUEUE_NAME]["VisibilityTimeout"] == 180


def test_every_dlq_retains_for_fourteen_days(
    template_json: dict[str, dict[str, Any]],
) -> None:
    queues = _queues(template_json["PvMessagingStack"])
    for name in cfg.DLQ_NAMES:
        assert queues[name]["MessageRetentionPeriod"] == 14 * 24 * 3600, name


def test_each_redrive_allow_policy_names_only_its_own_source_queue(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Stops a future mis-wired queue from dumping unrelated messages into a DLQ
    an alarm is watching.
    """
    queues = _queues(template_json["PvMessagingStack"])
    for dlq_name, source_name in (
        (cfg.ADVOCATE_DLQ_NAME, cfg.ADVOCATE_QUEUE_NAME),
        (cfg.ACTION_DLQ_NAME, cfg.ACTION_QUEUE_NAME),
    ):
        policy = queues[dlq_name]["RedriveAllowPolicy"]
        assert policy["redrivePermission"] == "byQueue"
        (arn,) = policy["sourceQueueArns"]
        assert flatten(arn).endswith(f":{source_name}")


def test_all_five_canonical_rules_exist_across_the_two_stacks(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Section 2.1 credits all five to this stack.

    Two of them target Lambda functions in ``PvComputeStack``, and a rule plus
    its Lambda target reference each other, so keeping them here would make the
    two stacks mutually dependent. Only the stack boundary moves; the five names
    are unchanged. (D-13-003.)
    """
    assert set(_rules(template_json)) == {
        "provenance-advocate-rule",
        "provenance-action-execute-rule",
        "provenance-notification-rule",
        "provenance-telemetry-rule",
        "provenance-trigger-schedule-rule",
    }


def test_the_advocate_rule_filters_on_attention_level(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Keeps trivial state changes from waking an LLM.

    ``specs/15_API_SPEC.md`` section 17.8 flags this as fragile: every routed
    detail-type must actually carry ``attention_level``, because EventBridge
    silently does not match when a required field is absent. The mechanical
    controls are the ``spec_lint`` check and the contract test in section 6.3,
    neither of which this suite can substitute for.
    """
    pattern = _rules(template_json)["provenance-advocate-rule"]["EventPattern"]
    assert pattern["detail-type"] == ADVOCATE_DETAIL_TYPES
    assert pattern["detail"]["schema_version"] == ["1.0"]
    assert pattern["detail"]["payload"]["attention_level"] == [{"anything-but": ["NONE"]}]


def test_only_approved_actions_reach_the_action_queue(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """There is no rule that routes ``action.proposed.v1`` to the executor, and
    adding one would break invariant 4.
    """
    pattern = _rules(template_json)["provenance-action-execute-rule"]["EventPattern"]
    assert pattern["detail-type"] == ["action.approved.v1"]

    for name, props in _rules(template_json).items():
        if name in ("provenance-telemetry-rule", "provenance-notification-rule"):
            continue
        targets = flatten(props.get("Targets", []))
        if cfg.ACTION_QUEUE_NAME in targets or "ActionQueue" in targets:
            assert props["EventPattern"]["detail-type"] == ["action.approved.v1"], name


def test_the_notification_and_trigger_schedule_patterns_are_verbatim(
    template_json: dict[str, dict[str, Any]],
) -> None:
    rules = _rules(template_json)
    assert rules["provenance-notification-rule"]["EventPattern"]["detail-type"] == (
        NOTIFICATION_DETAIL_TYPES
    )
    assert rules["provenance-trigger-schedule-rule"]["EventPattern"]["detail-type"] == (
        TRIGGER_SCHEDULE_DETAIL_TYPES
    )


def test_telemetry_routes_everything_to_a_log_group_and_nothing_else(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Never a second source of truth: the log group is an observation, not a
    consumer.
    """
    props = _rules(template_json)["provenance-telemetry-rule"]
    assert set(props["EventPattern"]) == {"source"}

    (target,) = props["Targets"]
    # The target names the log group by logical id, so resolve it and assert on
    # the name the Memory Trace and the teardown script both use.
    logical = target["Arn"]["Fn::Join"][1][-1]["Ref"]
    log_group = template_json["PvMessagingStack"]["Resources"][logical]
    assert log_group["Type"] == "AWS::Logs::LogGroup"
    assert log_group["Properties"]["LogGroupName"] == cfg.LOG_GROUP_DOMAIN_EVENTS


def test_two_schedule_groups_so_teardown_cannot_take_out_a_system_sweep(
    template_json: dict[str, dict[str, Any]],
) -> None:
    groups = resources_of(template_json["PvMessagingStack"], "AWS::Scheduler::ScheduleGroup")
    assert {b["Properties"]["Name"] for b in groups.values()} == {
        cfg.TRIGGER_SCHEDULE_GROUP,
        cfg.SYSTEM_SCHEDULE_GROUP,
    }


def test_the_scheduler_role_can_invoke_exactly_two_functions(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Notably absent from the wakeup path: ``scheduler:*``.

    ``trigger_schedule_manager`` owns schedule lifecycle, so a compromised
    wakeup cannot arm a new schedule.
    """
    invoke = [
        statement
        for _logical, _cfn_type, statement in policy_statements(template_json["PvMessagingStack"])
        if statement.get("Sid") == "InvokeTheTwoScheduledWorkers"
    ]
    assert len(invoke) == 1
    resources = flatten(invoke[0]["Resource"])
    assert "provenance-trigger-wakeup" in resources
    assert "provenance-outbox-dispatch" in resources
    assert "provenance-ses-ingest" not in resources
    assert "provenance-action-execute" not in resources
