"""Removal policy for stateful resources (40_INFRA_IAC.md section 2.3).

Stateful resources carry ``RETAIN`` **during the build** and become ``DESTROY``
only under ``PV_TEARDOWN=1``, which ``ops/teardown.sh`` sets and nothing else
may. A ``cdk destroy`` run by accident in week two must not delete the seeded
artifact bucket or the Cognito pool the demo users live in.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy

from provenance_infra.config import PvConfig


def stateful_removal(config: PvConfig) -> RemovalPolicy:
    """``DESTROY`` only under teardown; ``RETAIN`` every other time."""
    return RemovalPolicy.DESTROY if config.teardown else RemovalPolicy.RETAIN


def auto_delete_objects(config: PvConfig) -> bool:
    """Empty a bucket on delete only under teardown.

    ``autoDeleteObjects`` provisions a custom-resource Lambda with
    ``s3:DeleteObject*`` on the bucket. Provisioning it during the build would
    put a delete-capable principal next to an append-only evidence store for no
    benefit, so it is created only when the account is being torn down.
    """
    return config.teardown
