"""The only canonical write path.

Invariant 1: every canonical table is written from here and nowhere else, under
`pv_kernel_writer`, inside a SERIALIZABLE transaction with bounded 40001 retry.
No model call and no network call may occur inside a transaction callback.

Authoritative algorithms: `specs/12_KERNEL_ALGORITHMS.md`.

Module map (`specs/12_KERNEL_ALGORITHMS.md` section 0.4, relocated to this
package by `EXECUTION/70_TASK_PLAN.md` section 7)::

    config.py         KernelConfig - every threshold in the spec  (T4.1)
    preflight.py      PHASE A, before a transaction exists        (T4.2)
    families.py       the closed predicate-family registry        (T4.3)
    propositions.py   normal form, day boundary, entailment       (T4.3)
    contradiction.py  overlap, the M1-M13 matcher table           (T4.4)
    disposition.py    gates H1-H8 and the four dispositions       (T4.4)
    money_ops.py      recompute-never-increment, over-fulfilment  (T4.5)

Everything above is a pure function of (rows already read, proposal payload,
frozen config). None of it imports `provenance_db`, `boto3`, `httpx` or
`asyncio`, so all of it is reachable from a unit test with no network access,
no AWS credentials and no model call - which is the falsifiable form of the
product claim (`quality/20_TDD_STRATEGY.md` section 2.1). The transaction
itself, which does need a connection, lands separately at T4.10.

This module deliberately does **not** import its own submodules eagerly.
Importing the package must stay cheap and pure, so that when `transaction.py`
arrives and does need `provenance_db`, importing `memory_kernel.propositions`
still does not.
"""

from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    SUPPORTED_SCHEMA_VERSIONS,
    KernelConfig,
)

__all__ = [
    "DEFAULT_KERNEL_CONFIG",
    "SUPPORTED_SCHEMA_VERSIONS",
    "KernelConfig",
]
