"""Provenance AWS CDK application.

Owning specification: ``docs/ops/40_INFRA_IAC.md``.
Authority order: ``docs/CANONICAL_DECISIONS.md`` > ``docs/ops/40_INFRA_IAC.md`` >
``docs/quality/23_PHASE_GATES.md`` > ``docs/EXECUTION/70_TASK_PLAN.md``.

Nothing in this package has been deployed. Every construct is written to be
synthesised and reviewed; ``cdk deploy`` and ``cdk bootstrap`` are the account
owner's decision and are billable.
"""

from provenance_infra.config import PvConfig

__all__ = ["PvConfig"]
