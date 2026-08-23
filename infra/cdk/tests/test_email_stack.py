"""``PvEmailStack``: inbound receiving, outbound sending, and the sandbox.

The action-order assertion is the one that would otherwise fail as an
intermittent: SES receipt actions run in order, and the Lambda expects the S3
object to already exist. A reversed pair works whenever the write happens to
land first.
"""

from __future__ import annotations

from typing import Any

from provenance_infra import config as cfg
from pv_cdk_testing import flatten, resources_of


def _rule(template: dict[str, Any]) -> dict[str, Any]:
    rules = resources_of(template, "AWS::SES::ReceiptRule")
    assert len(rules) == 1
    return next(iter(rules.values()))["Properties"]["Rule"]


def test_three_identities_are_verified(template_json: dict[str, dict[str, Any]]) -> None:
    """``demo-sink`` is verified precisely so ``ACTION_RECIPIENT_MODE=DEMO_SINK``
    works inside the SES sandbox with no production-access request on the
    critical path.
    """
    identities = resources_of(template_json["PvEmailStack"], "AWS::SES::EmailIdentity")
    assert {b["Properties"]["EmailIdentity"] for b in identities.values()} == {
        "in.provenance.app",
        "provenance.app",
        "demo-sink.provenance.app",
    }
    for body in identities.values():
        assert body["Properties"]["DkimAttributes"]["SigningEnabled"] is True


def test_one_receipt_rule_for_the_whole_ingest_domain(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Per-address receipt rules would mean a CDK deploy on every user sign-up.

    The alias is resolved in ``ingest_aliases`` by HMAC, so the routing decision
    lives in the database where it can be rotated and disabled in one statement.
    """
    rule = _rule(template_json["PvEmailStack"])
    assert rule["Name"] == cfg.SES_RULE_NAME
    assert rule["Recipients"] == ["in.provenance.app"]
    assert rule["Enabled"] is True


def test_scanning_is_on_so_the_admission_verdicts_exist(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Without ``ScanEnabled``, ``spamVerdict`` and ``virusVerdict`` are absent
    and section 9.1 step 3's rejection rule has nothing to evaluate.
    """
    assert _rule(template_json["PvEmailStack"])["ScanEnabled"] is True


def test_tls_is_optional_which_is_the_recorded_demo_concession(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """``Require`` would bounce mail from any MTA that will not do STARTTLS.

    That is the right production setting and the wrong demo setting: a bounced
    hero artifact is unrecoverable in a live demo. The SPF, DKIM, and DMARC
    verdicts are captured either way and feed the source-authority band. Asserted
    rather than left implicit so that flipping it in production is a visible
    change (section 15.5).
    """
    assert _rule(template_json["PvEmailStack"])["TlsPolicy"] == "Optional"


def test_the_s3_action_runs_before_the_lambda_action(
    template_json: dict[str, dict[str, Any]],
) -> None:
    actions = _rule(template_json["PvEmailStack"])["Actions"]
    assert len(actions) == 2
    assert "S3Action" in actions[0]
    assert "LambdaAction" in actions[1]
    assert actions[0]["S3Action"]["ObjectKeyPrefix"] == "ses/incoming/"
    assert "KmsKeyArn" in actions[0]["S3Action"]
    # Async: SES must not wait on the control plane.
    assert actions[1]["LambdaAction"]["InvocationType"] == "Event"


def test_ses_may_invoke_the_ingest_worker_only_from_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    permissions = resources_of(template_json["PvEmailStack"], "AWS::Lambda::Permission")
    ses_permissions = [
        b["Properties"]
        for b in permissions.values()
        if b["Properties"].get("Principal") == "ses.amazonaws.com"
    ]
    assert len(ses_permissions) == 1
    assert ses_permissions[0]["SourceAccount"] == {"Ref": "AWS::AccountId"}


def test_the_rule_depends_on_the_rule_set(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Creating the rule set is not enough and ordering is not implied.

    Activating it is a separate ``set-active-receipt-rule-set`` call that CDK
    cannot make, which is the single most common reason inbound SES appears
    configured and silently does nothing.
    """
    rules = resources_of(template_json["PvEmailStack"], "AWS::SES::ReceiptRule")
    body = next(iter(rules.values()))
    assert body.get("DependsOn")


def test_the_configuration_set_suppresses_bounces_and_does_not_rewrite_links(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Open and click tracking would alter the exact bytes whose SHA-256 the
    human approved, breaking the ``approval_draft_sha256`` binding. That is a
    correctness constraint, not a privacy preference.
    """
    sets = resources_of(template_json["PvEmailStack"], "AWS::SES::ConfigurationSet")
    assert len(sets) == 1
    props = next(iter(sets.values()))["Properties"]
    assert props["Name"] == cfg.SES_CONFIGURATION_SET
    assert set(props["SuppressionOptions"]["SuppressedReasons"]) == {"BOUNCE", "COMPLAINT"}
    assert "TrackingOptions" not in props


def test_delivery_events_reach_the_domain_bus(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """Without these the action plane cannot tell a delivered dispute from a
    bounced one.
    """
    destinations = resources_of(
        template_json["PvEmailStack"], "AWS::SES::ConfigurationSetEventDestination"
    )
    assert len(destinations) == 1
    destination = next(iter(destinations.values()))["Properties"]["EventDestination"]
    assert destination["Enabled"] is True
    assert set(destination["MatchingEventTypes"]) == {
        "SEND",
        "DELIVERY",
        "BOUNCE",
        "COMPLAINT",
        "REJECT",
        "RENDERING_FAILURE",
        "DELIVERY_DELAY",
    }
    assert "EventBridgeDestination" in destination


def test_the_bus_accepts_ses_events_only_from_this_account(
    template_json: dict[str, dict[str, Any]],
) -> None:
    """A custom bus does not accept ``PutEvents`` from a service principal by
    default; without the policy the destination is created and silently delivers
    nothing.
    """
    policies = resources_of(template_json["PvEmailStack"], "AWS::Events::EventBusPolicy")
    assert len(policies) == 1
    statement = next(iter(policies.values()))["Properties"]["Statement"]
    assert statement["Principal"] == {"Service": "ses.amazonaws.com"}
    assert statement["Action"] == "events:PutEvents"
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == {"Ref": "AWS::AccountId"}


def test_the_sending_domain_has_a_custom_mail_from(
    template_json: dict[str, dict[str, Any]],
) -> None:
    identities = resources_of(template_json["PvEmailStack"], "AWS::SES::EmailIdentity")
    sending = next(
        b["Properties"]
        for b in identities.values()
        if b["Properties"]["EmailIdentity"] == "provenance.app"
    )
    assert sending["MailFromAttributes"]["MailFromDomain"] == "mail.provenance.app"
    assert sending["FeedbackAttributes"]["EmailForwardingEnabled"] is False


def test_the_inbound_object_is_written_with_the_artifact_key(
    template_json: dict[str, dict[str, Any]],
) -> None:
    action = _rule(template_json["PvEmailStack"])["Actions"][0]["S3Action"]
    assert "KmsKeyArn" in action
    assert flatten(action["BucketName"]) != ""
