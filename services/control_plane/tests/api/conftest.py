"""Fixture wiring for the Phase 8 public/internal API suite.

`--import-mode=importlib` does not put a test file's directory on `sys.path`,
so the shared `_support` package is made importable explicitly. The auth suite
does exactly the same, pointing at this directory, so the fakes and fixtures
have one home rather than two that can drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_TESTS = Path(__file__).resolve().parent
if str(_API_TESTS) not in sys.path:
    sys.path.insert(0, str(_API_TESTS))

from _support.fixtures import *  # noqa: E402, F403
from _support.fixtures import close_session_event_loop  # noqa: E402


def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    """Close the event loop ``_support.fixtures`` created at import.

    Leaving it open would trade one unclosed socket pair for another -- the
    point of owning the loop is that somebody closes it.
    """
    del session, exitstatus
    close_session_event_loop()
