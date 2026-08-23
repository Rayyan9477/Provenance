"""Provenance database runtime.

Single responsibility
---------------------
Own the connection pools, the repository functions, and the transaction
wrapper: one pool per SQL role (``pv_migrator``, ``pv_app_reader_writer``,
``pv_kernel_writer``, ``pv_agent_reader``, ``pv_ops_reader``), ``SERIALIZABLE``
transactions with bounded retry on SQLSTATE ``40001``, SQLSTATE-to-outcome
mapping, and the ANN entry point ``provenance_db.repositories.evidence
.ann_search()``.

Two rules this package exists to make true:

* No model call and no network call inside a transaction callback.
  ``provenance_db.retry._IN_KERNEL_TX`` is set for the duration of the
  transaction and every outbound client wrapper calls
  ``assert_no_side_effects()`` first (`quality/20_TDD_STRATEGY.md` section 2.3,
  guard E2).
* An ANN query vector is a literal or a bound parameter, never a correlated
  subquery. Defect D-06-001: a correlated subquery silently produces a full
  scan rather than an index seek.

Forbidden dependencies
----------------------
``provenance_domain.kernel`` must not import this package — the dependency runs
one way only, and ``.importlinter`` contract ``kernel-purity`` enforces it.
This package must not import ``services`` or ``agents``: it is a library, and a
library that knows about its callers is not one.

Module map, as built by Phase 3
-------------------------------
``urls.py``          role -> DSN resolution; no URL is ever a function argument
``pools.py``         one pool per SQL role, role immutable after construction
``retry.py``         the section 7 retry contract and the side-effect guard
``repositories/``    reads, split by domain; no canonical write lives here

Not yet implemented: ``queries/retrieval.py`` (Phase 6, the only module that
may contain ``<=>``) and the bodies of the repository reads, each of which
names the phase that fills it.
"""
