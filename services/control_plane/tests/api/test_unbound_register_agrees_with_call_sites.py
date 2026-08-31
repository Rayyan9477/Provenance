"""Every ``unbound("...")`` call site must have a register entry.

The defect this closes
-----------------------
``InternalAdapter.submit_proposal`` called ``unbound("internal.submit_proposal")``
and that key was **absent** from ``UNBOUND``. ``unbound()`` looks the key up to
build its message, so the call raised

    KeyError: 'internal.submit_proposal'

instead of the typed ``NotImplementedError`` naming the subsystem it waits on.

That inverts the register's entire purpose. ``unbound.py``'s own docstring gives
two reasons for existing -- "an empty list is a lie" and "a register can be
counted" -- and this defeats both. The caller gets an opaque `KeyError` that
names no subsystem, and `len(UNBOUND)` under-reports the unbound surface,
because the one method missing from the count is the one whose absence is
invisible.

It is also the exact failure mode the register was built to prevent, arrived at
from the other direction: not a method that returns `[]` when it means "not
loaded", but a method whose refusal is unreadable.

Why a cross-check rather than one entry
----------------------------------------
Adding the missing key fixes today. This fails tomorrow, when someone writes a
new ``unbound("...")`` call and forgets the register, or deletes a register line
while binding a method and leaves the body refusing. Both directions are
checked, because both have happened in this repository: the counterfactual
`CF7` in the ingestion round was precisely "delete an UNBOUND line while the
body still refuses".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.control_plane.app.api.adapters.unbound import UNBOUND

pytestmark = pytest.mark.unit

#: `tests/api/<file>` -> parents[2] is `control_plane`. `parents[3]` is
#: `services/`, which has no `app/api` under it, so the walk found four call
#: sites instead of sixteen and `test_every_call_site_has_a_register_entry`
#: passed vacuously. `test_the_scan_is_armed` is what caught it.
ADAPTERS = Path(__file__).resolve().parents[2] / "app" / "api"


def _call_sites() -> dict[str, list[str]]:
    """``unbound("key")`` -> where it is called, across the whole API package."""
    sites: dict[str, list[str]] = {}
    for path in ADAPTERS.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "unbound"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                sites.setdefault(node.args[0].value, []).append(f"{path.name}:{node.lineno}")
    return sites


def test_the_scan_is_armed() -> None:
    """A scan that finds nothing because it looks nowhere proves nothing.

    The guard is on the **walk**, not on how much of the surface is unbound.
    It read ``len(sites) >= 10`` until 2026-08-24 and went red when five
    ingestion ports were bound at once -- correctly reporting that fewer call
    sites exist, and wrongly calling that a broken walk. That is ``STATUS.md``
    section 7's failure exactly: a test pinned to a state fails when the state
    legitimately changes, and the pressure is then to delete it.

    So the two things that would make :func:`test_every_call_site_has_a_register_entry`
    vacuous are asserted directly: that the rglob reached the adapter package
    at all, and that it parsed the module the call sites actually live in. Both
    stay true as the register empties, and both go false the day
    ``parents[2]`` is wrong again.
    """
    scanned = sorted(
        path.name for path in ADAPTERS.rglob("*.py") if "__pycache__" not in path.parts
    )
    assert len(scanned) >= 10, f"the walk reached only {scanned}; ADAPTERS is wrong"
    assert {"unbound.py", "read.py", "write.py", "internal.py"} <= set(scanned), scanned
    assert (ADAPTERS / "adapters" / "unbound.py").is_file(), ADAPTERS


def test_every_call_site_has_a_register_entry() -> None:
    """Otherwise the refusal is a KeyError naming no subsystem."""
    sites = _call_sites()
    missing = {key: where for key, where in sites.items() if key not in UNBOUND}
    assert not missing, (
        "these call `unbound(...)` with a key the register does not hold, so they "
        "raise KeyError instead of the typed NotImplementedError that names what "
        f"they are waiting on: {missing}"
    )


def test_every_register_entry_has_a_call_site() -> None:
    """The other direction: a line left behind after a method was bound.

    A register entry nobody reaches inflates the unbound count, which is the
    number `STATUS.md` quotes to say how much of the surface is live.
    """
    sites = _call_sites()
    orphaned = sorted(key for key in UNBOUND if key not in sites)
    assert not orphaned, (
        f"{orphaned} are in UNBOUND but no adapter calls unbound() with them. "
        "Either the method was bound and the line was not deleted, or the entry "
        "was never wired -- both make the count wrong."
    )


def test_every_entry_names_a_subsystem_rather_than_saying_not_implemented() -> None:
    """`unbound.py`: a message that says "not implemented" sends the reader to
    grep; one that names the thing sends them to the thing that has to exist."""
    for key, reason in UNBOUND.items():
        assert len(reason) > 40, f"{key} has a stub reason: {reason!r}"
        assert (
            "not implemented" not in reason.lower()
        ), f"{key} says 'not implemented', which names nothing"
