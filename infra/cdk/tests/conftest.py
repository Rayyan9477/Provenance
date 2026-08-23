"""Shared fixtures for the CDK template assertions.

Every test in this directory runs against the **synthesised CloudFormation**,
not against the Python constructs. A test that asserts "the stack synthesises"
asserts nothing; these assert the properties the specification and the phase
gates actually depend on.

The app is built exactly once, by ``app.build`` -- the same function a deploy
runs -- so the suite cannot drift from what would be deployed.

The helper functions live in ``infra/cdk/pv_cdk_testing.py`` rather than here,
because ``tests`` is a top-level name several installed distributions also
claim and ``from tests.conftest import ...`` resolves to whichever one is first
on ``sys.path``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

CDK_ROOT = Path(__file__).resolve().parents[1]
if str(CDK_ROOT) not in sys.path:
    sys.path.insert(0, str(CDK_ROOT))

import app as pv_app  # noqa: E402
import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402

# A deterministic sha so template hashes do not move between runs. The real
# value comes from ``git rev-parse HEAD`` at deploy time and G13.2 asserts
# string equality against it.
TEST_GIT_SHA = "0" * 40


@pytest.fixture(scope="session")
def cdk_app() -> Iterator[cdk.App]:
    """One App, built by the same function ``cdk synth`` calls."""
    app = cdk.App(
        context={
            "pv:git_sha": TEST_GIT_SHA,
            "pv:owner": "test",
            "pv:web_repository": "https://github.com/example/provenance",
        }
    )
    pv_app.build(app)
    yield app


@pytest.fixture(scope="session")
def stacks(cdk_app: cdk.App) -> dict[str, cdk.Stack]:
    return {
        stack.stack_name: stack for stack in cdk_app.node.children if isinstance(stack, cdk.Stack)
    }


@pytest.fixture(scope="session")
def templates(stacks: dict[str, cdk.Stack]) -> dict[str, Template]:
    return {name: Template.from_stack(stack) for name, stack in stacks.items()}


@pytest.fixture(scope="session")
def template_json(templates: dict[str, Template]) -> dict[str, dict[str, Any]]:
    return {name: template.to_json() for name, template in templates.items()}
