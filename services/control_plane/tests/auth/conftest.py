"""Fixture wiring for the Phase 8 auth suite.

See `tests/api/conftest.py`: the `_support` package lives beside the API suite
and both directories import it from there, so there is one copy of the fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_TESTS = Path(__file__).resolve().parents[1] / "api"
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
