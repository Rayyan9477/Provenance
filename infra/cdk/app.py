#!/usr/bin/env python3
"""The Provenance CDK application.

``cdk synth``, ``cdk ls`` and ``cdk diff`` are the supported commands here.
``cdk deploy`` and ``cdk bootstrap`` create billable resources and are the
account owner's decision; the deploy order they must run in is
``docs/ops/40_INFRA_IAC.md`` section 2.4, not this file.

Ten stacks, wired props-in. The construction order below IS the dependency
order, and it is acyclic by construction: no stack receives an export from a
stack that receives one of its own.
"""

from __future__ import annotations

import os

import aws_cdk as cdk
from provenance_infra import config as cfg
from provenance_infra.config import PvConfig
from provenance_infra.stacks import (
    PvAgentStack,
    PvApiStack,
    PvComputeStack,
    PvDataStack,
    PvEmailStack,
    PvFoundationStack,
    PvIdentityStack,
    PvMessagingStack,
    PvObservabilityStack,
    PvWebStack,
)


def build(app: cdk.App) -> dict[str, cdk.Stack]:
    """Instantiate every stack against one shared config. Returns them by name.

    Separated from ``main`` so the test suite constructs exactly what a deploy
    would, rather than a hand-assembled subset that could drift from it.
    """
    config = PvConfig.from_scope(app)

    # Account is deliberately not defaulted to a literal. When CDK_DEFAULT_ACCOUNT
    # is unset the stacks synthesise environment-agnostic and every account
    # reference renders as ``{"Ref": "AWS::AccountId"}`` -- which is what keeps
    # G0.3 clean and what the test suite asserts.
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=cfg.REGION)

    foundation = PvFoundationStack(app, "PvFoundationStack", config=config, env=env)
    identity = PvIdentityStack(app, "PvIdentityStack", config=config, env=env)
    data = PvDataStack(app, "PvDataStack", config=config, foundation=foundation.exports, env=env)
    messaging = PvMessagingStack(
        app, "PvMessagingStack", config=config, foundation=foundation.exports, env=env
    )
    compute = PvComputeStack(
        app,
        "PvComputeStack",
        config=config,
        foundation=foundation.exports,
        identity=identity.exports,
        data=data.exports,
        messaging=messaging.exports,
        env=env,
    )
    api = PvApiStack(
        app,
        "PvApiStack",
        config=config,
        foundation=foundation.exports,
        identity=identity.exports,
        data=data.exports,
        messaging=messaging.exports,
        env=env,
    )
    agent = PvAgentStack(
        app,
        "PvAgentStack",
        config=config,
        foundation=foundation.exports,
        identity=identity.exports,
        data=data.exports,
        env=env,
    )
    email = PvEmailStack(
        app,
        "PvEmailStack",
        config=config,
        foundation=foundation.exports,
        data=data.exports,
        messaging=messaging.exports,
        compute=compute.exports,
        env=env,
    )
    web = PvWebStack(app, "PvWebStack", config=config, identity=identity.exports, env=env)
    observability = PvObservabilityStack(
        app, "PvObservabilityStack", config=config, foundation=foundation.exports, env=env
    )

    # PvAgentStack reads /provenance/api/base-url, which PvApiStack writes. That
    # is a deploy-time SSM lookup rather than a CDK reference, so CDK cannot
    # infer the ordering and section 2.4 step 8 -> step 9 has to be stated.
    agent.add_stack_dependency(api)

    # Tags at the App level so nothing escapes them. The teardown verification
    # in section 14.5 greps for Project=Provenance on every taggable resource,
    # and DeleteAfter is a note to a future reader of the bill rather than
    # enforcement -- enforcement is ops/teardown.sh.
    for key, value in config.tags.items():
        cdk.Tags.of(app).add(key, value)

    return {
        stack.stack_name: stack
        for stack in (
            foundation,
            identity,
            data,
            messaging,
            compute,
            api,
            agent,
            email,
            web,
            observability,
        )
    }


def main() -> cdk.App:
    app = cdk.App()
    build(app)
    return app


if __name__ == "__main__":
    main().synth()
