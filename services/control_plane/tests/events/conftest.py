"""Fixture wiring for the Phase 10 lane — outbox dispatch and prospective memory.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` sections 4-13, 15 and 16.
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time* and
  -> *Hero dataset canon*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 13, ``T10.1``-``T10.5``.

The shared helpers are imported by their **fully qualified** path
(``services.control_plane.tests.events._support``) rather than through a
``sys.path`` insert. ``tests/api/`` already owns a package called ``_support``,
and putting a second directory of that name on the path makes whichever
conftest loads first win — a collision that presents as
``ImportError: cannot import name 'canon' from '_support'`` in a file that never
mentions the API suite. The dotted path cannot collide.

Every test in this directory carries the ``unit`` marker. The root
``conftest.py`` guard then strips credentials from the environment and refuses
any outbound socket — which is exactly the point. The trigger evaluator is pure
and the dispatcher speaks to a transport Protocol, so neither has any business
reaching a network. A test here that needs a socket is a design defect, not a
lane mismatch.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.control_plane.tests.events._support import canon

pytestmark = pytest.mark.unit


@pytest.fixture
def hero_spec_document() -> dict[str, Any]:
    return canon.hero_predicate_document()
