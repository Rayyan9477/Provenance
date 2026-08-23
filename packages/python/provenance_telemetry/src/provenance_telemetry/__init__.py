"""Provenance telemetry.

Single responsibility
---------------------
Own correlation and observability plumbing so that no product module has to:
trace and correlation id generation (``ids.py``), the ``contextvars`` binding
that carries them (``context.py``, ``correlation.py``), the OpenTelemetry span
helpers (``spans.py``), the runtime gauges (``gauges.py``), the machine-to-
machine token helper used by the Lambda workers (``m2m.py``), and the test
doubles that prove a model was never in a code path (``testing.py``, which
exports ``ExplodingClient`` — it raises on construction, so a passing test is
proof of absence rather than a promise of it).

No component accepts a trace id as a function parameter. A parameter can be
forgotten; a contextvar cannot be.

Authority: `quality/21_OBSERVABILITY_ANALYTICS.md`.

Forbidden dependencies
----------------------
``provenance_db``, ``services``, ``agents`` — telemetry is imported by every
layer, so it may depend on none of them. It must never read, log or span
raw artifact content: `quality/21_OBSERVABILITY_ANALYTICS.md` forbids artifact
bytes in any log, span attribute or metric label.

Not yet implemented: Phase 5 (T5.4) and Phase 10 (T10.6) fill this package.
Phase 0 creates it importable so ``make bootstrap`` and the import contracts
are real from the first commit.
"""
