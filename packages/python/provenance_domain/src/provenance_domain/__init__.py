"""Provenance domain — the pure core.

Single responsibility
---------------------
Hold the closed vocabularies and the deterministic rules that decide what is
legal, in a form that can be executed with no database, no network, no
credentials and no clock: enums, state-machine transition tables,
``legal_transition()``, invariant functions, authority bands, and the
derivation registry. `specs/11_CONTRACTS.md` section 1 is the authority for
this package's contents; `specs/12_KERNEL_ALGORITHMS.md` is the authority for
the ``kernel`` subpackage.

Forbidden dependencies
----------------------
The whole package:

* ``pydantic`` — this package is importable from the Kernel's hot path, from
  Lambda workers with tight cold-start budgets, and from test fixtures,
  without pulling in a validation framework. Boundary models live in
  ``provenance_contracts``.

``provenance_domain.kernel`` additionally:

* ``provenance_db``, ``boto3``, ``botocore``, ``anthropic``, ``httpx``,
  ``requests``, ``psycopg``, ``asyncio``.

``asyncio`` is on that list deliberately: a pure function that needs an event
loop is a pure function that is about to make a call.

Both rules are enforced by ``.importlinter`` (``quality/20_TDD_STRATEGY.md``
section 2.3, contract E1), not by discipline. Run ``make lint`` to check them.
"""
