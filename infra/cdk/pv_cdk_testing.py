"""Helpers for asserting over synthesised CloudFormation.

Lives beside ``app.py`` rather than inside ``tests/`` for one practical reason:
``tests`` is a name several installed distributions also claim at the top level,
so ``from tests.conftest import ...`` resolves to whichever one is first on
``sys.path``. A uniquely named module cannot be shadowed.

Nothing here builds infrastructure; it only reads templates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Final

STACK_NAMES: Final[tuple[str, ...]] = (
    "PvFoundationStack",
    "PvIdentityStack",
    "PvDataStack",
    "PvMessagingStack",
    "PvComputeStack",
    "PvApiStack",
    "PvAgentStack",
    "PvEmailStack",
    "PvWebStack",
    "PvObservabilityStack",
)

# A bare twelve-digit run is what an AWS account id looks like.
ACCOUNT_ID_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\d.])\d{12}(?![\d.])")

# The shapes G0.3's scanner and G13.6's jq filter look for.
CREDENTIAL_SHAPES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("dsn with password", re.compile(r"postgresql://[^\"\s]*:[^\"\s@]+@")),
    ("secret access key", re.compile(r"aws_secret_access_key", re.IGNORECASE)),
    ("bearer literal", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")),
)


def resources_of(template: dict[str, Any], cfn_type: str) -> dict[str, dict[str, Any]]:
    """Every resource of one CloudFormation type in one template."""
    return {
        logical: body
        for logical, body in template.get("Resources", {}).items()
        if body.get("Type") == cfn_type
    }


def all_resources_of(
    template_json: dict[str, dict[str, Any]], cfn_type: str
) -> dict[str, dict[str, Any]]:
    """Every resource of a type across every stack, keyed ``Stack/Logical``."""
    found: dict[str, dict[str, Any]] = {}
    for stack_name, template in template_json.items():
        for logical, body in resources_of(template, cfn_type).items():
            found[f"{stack_name}/{logical}"] = body
    return found


def policy_statements(template: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(logical_id, resource_type, statement)`` for every IAM statement.

    Covers inline role policies, standalone and managed policies, and the
    resource policies attached to buckets, queues, topics, event buses, and keys.
    """
    for logical, body in template.get("Resources", {}).items():
        cfn_type = body.get("Type", "")
        props = body.get("Properties", {})
        documents: list[Any] = []
        if cfn_type in ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"):
            documents.append(props.get("PolicyDocument", {}))
        elif cfn_type == "AWS::IAM::Role":
            for inline in props.get("Policies") or []:
                documents.append(inline.get("PolicyDocument", {}))
        elif cfn_type in (
            "AWS::S3::BucketPolicy",
            "AWS::SQS::QueuePolicy",
            "AWS::SNS::TopicPolicy",
        ):
            documents.append(props.get("PolicyDocument", {}))
        elif cfn_type == "AWS::KMS::Key":
            documents.append(props.get("KeyPolicy", {}))
        elif cfn_type == "AWS::Events::EventBusPolicy":
            statement = props.get("Statement")
            if statement:
                documents.append({"Statement": [statement]})
        for document in documents:
            if not isinstance(document, dict):
                continue
            for statement in document.get("Statement") or []:
                if isinstance(statement, dict):
                    yield logical, cfn_type, statement


def trust_statements(template: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(logical_id, statement)`` for every role's trust policy."""
    for logical, body in resources_of(template, "AWS::IAM::Role").items():
        document = body.get("Properties", {}).get("AssumeRolePolicyDocument", {})
        for statement in document.get("Statement") or []:
            if isinstance(statement, dict):
                yield logical, statement


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def render(node: Any) -> str:
    """Flatten a template fragment to text so substring assertions are possible."""
    return json.dumps(node, sort_keys=True, default=str)


# Pseudo-parameter substitutions used when flattening a template fragment back
# into readable text. Account is deliberately a placeholder: an environment
# agnostic synth never produces a literal, and asserting on the placeholder is
# what proves it.
_PSEUDO: Final[dict[str, str]] = {
    "AWS::Partition": "aws",
    "AWS::Region": "us-east-1",
    "AWS::AccountId": "<account>",
    "AWS::URLSuffix": "amazonaws.com",
    "AWS::StackName": "<stack>",
    "AWS::NoValue": "",
}


def flatten(node: Any) -> str:
    """Concatenate a template fragment's string leaves into readable text.

    ``Fn::Join`` and ``Ref`` are resolved so an assertion can name a whole ARN
    instead of guessing which half of a join a substring landed in. Anything
    unresolvable becomes ``<ref>``, which is enough to keep the surrounding
    literal text contiguous.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, int | float | bool) or node is None:
        return str(node)
    if isinstance(node, list):
        return "".join(flatten(item) for item in node)
    if isinstance(node, dict):
        if "Fn::Join" in node:
            separator, parts = node["Fn::Join"]
            return flatten(separator).join(flatten(part) for part in parts)
        if "Fn::Sub" in node:
            return flatten(node["Fn::Sub"])
        if "Fn::ImportValue" in node:
            # The export name embeds the producing stack and the producing
            # construct's logical id, which is exactly what a cross-stack
            # assertion needs to name.
            return flatten(node["Fn::ImportValue"])
        if "Ref" in node and isinstance(node["Ref"], str):
            return _PSEUDO.get(node["Ref"], "<ref>")
        if "Fn::GetAtt" in node:
            return "<getatt>"
        return "".join(flatten(value) for value in node.values())
    return str(node)


def key_values(pairs: Any, name_key: str = "Name", value_key: str = "Value") -> dict[str, Any]:
    """Turn a CloudFormation ``[{Name, Value}]`` list into a dict."""
    out: dict[str, Any] = {}
    for item in as_list(pairs):
        if isinstance(item, dict) and name_key in item:
            out[item[name_key]] = item.get(value_key)
    return out
