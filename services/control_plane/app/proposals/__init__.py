"""The app side of the agent -> Kernel boundary.

One module, one statement, one rule. ``specs/10_DATABASE_DDL.md`` section 12
grants the app ``INSERT`` on ``memory_proposals`` and the Memory Kernel only
``UPDATE``: an agent *submits* a proposal, the Kernel *settles* it. That split
is write rule ``W4`` in ``tools/write_path_lint.py``, and this package is where
the app's half of it lives.

It is deliberately **not** under ``app/memory_kernel``. Putting it there would
make ``write_path_lint`` count the statement as a Kernel write, which is a
claim about ownership rather than about location:
``memory_kernel.transaction.CANONICAL_WRITE_STATEMENTS`` enumerates every
canonical write the Kernel holds, ``tests/kernel/test_obligations.py`` pins the
linter's count against that tuple, and this statement is not one of them.
"""

from __future__ import annotations

from services.control_plane.app.proposals.submission import (
    PROPOSAL_INSERT_SQL,
    KernelProposalWriter,
    ProposalRefusedError,
    build_proposal,
    insert_params,
    payload_sha256,
    proposal_payload,
    register_proposal,
    resolve_attribution,
)

__all__ = [
    "PROPOSAL_INSERT_SQL",
    "KernelProposalWriter",
    "ProposalRefusedError",
    "build_proposal",
    "insert_params",
    "payload_sha256",
    "proposal_payload",
    "register_proposal",
    "resolve_attribution",
]
