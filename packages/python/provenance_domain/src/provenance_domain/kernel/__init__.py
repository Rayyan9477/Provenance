"""Memory Kernel algorithms — pure functions only.

Single responsibility
---------------------
Decide, from an in-memory proposal plus an in-memory view of current canonical
state, exactly what must change: propositions, family matching, contradiction
detection, disposition, money arithmetic, the case machine, revision
arithmetic, temporal reasoning, and the resulting ``ChangePlan``. Every
function here is deterministic and total. Persistence of the plan is
``services/control_plane/app/memory_kernel/``'s job, not this package's.

Authority: `specs/12_KERNEL_ALGORITHMS.md`.

Forbidden dependencies
----------------------
``provenance_db``, ``boto3``, ``botocore``, ``anthropic``, ``httpx``,
``requests``, ``psycopg``, ``asyncio`` — and, inherited from the parent
package, ``pydantic``. Enforced by ``.importlinter`` contract
``kernel-purity``.

If a test of this package cannot be written without a model, a database or a
clock, the boundary is wrong and `quality/20_TDD_STRATEGY.md` section 2.4 names
the five diagnoses and their fixed remedies. None of them is "mock the model".

Nothing under this subpackage may ever be added to ``omit`` in ``.coveragerc``.

Not yet implemented: this subpackage is created in Phase 0 as an empty,
importable module so that the ``kernel-purity`` contract is executable from the
first commit rather than being decorative. Its modules land in Phase 4 (T4.1
onward).
"""
