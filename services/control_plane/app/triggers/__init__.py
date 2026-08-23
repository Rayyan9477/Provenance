"""Prospective memory: the trigger that wakes months later and re-checks.

The rule this whole package exists to enforce
---------------------------------------------
**A scheduler event is never truth.** A wakeup is an invitation to re-evaluate,
never an instruction to act. The evaluator reloads the case from canonical
state, rebuilds the projection from a single read-only snapshot, runs the
predicate against *that*, and most of the time correctly does nothing. A
trigger that fires because a timer said so, without re-checking, is a false
claim about the world — and the product's whole claim is that it does not make
those.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` — the DSL, the lifecycle, the fire
  transaction, and the closed outcome taxonomy.
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time* and
  -> *Hero dataset canon*.
- ``db/migrations/versions/0006_prospective_memory.py`` — the real column names.

Module map
----------
``config``      the constants of §16, including ``WAKE_MARGIN_SECONDS``.
``ast``         the closed predicate grammar and its parser (§4, §6).
``registry``    the whitelisted field paths (§5, §7.1).
``projection``  canonical rows flattened into the path->value map (§7.3).
``evaluator``   the deterministic three-valued evaluator (§8).
``outcomes``    the closed result/reason taxonomy and the re-arm policy (§9).
``service``     ``evaluate_trigger()`` — the one entry point every wake uses.

Nothing here writes a canonical row. The evaluator is a *proposer*: it
synthesises a deterministic ``TRIGGER_EVALUATION`` proposal and hands it to the
Memory Kernel, which is the only canonical writer. That keeps the audit chain
trigger -> proposal -> decision -> transition unbroken, so a trigger fire is as
explainable as an email-driven change and uses the same State Proof queries.
"""

from __future__ import annotations

__all__ = ["ast", "config", "evaluator", "outcomes", "projection", "registry", "service"]
