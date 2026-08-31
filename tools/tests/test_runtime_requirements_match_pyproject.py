"""``requirements-runtime.txt`` and the ``runtime`` extra are one list — T13.1.

Why two copies exist at all
---------------------------
``requirements-dev.txt`` resolves through ``-e .[dev]`` precisely so the dev set
has one home. The runtime set cannot do that, because a container installs it
**before** the source tree is copied in — that is what makes the dependency
layer cacheable, and it is the difference between a thirty-second rebuild and a
four-minute one on every code change.

So there are two copies, and the rule the repository already learned applies:
*two registries for one fact drift, and the drift is discovered late.* This test
is what makes the second copy safe. It fails if either list gains, loses or
repins an entry the other does not.

What it deliberately does not check
-----------------------------------
That the pins are *installable together*. That is what building the image does,
and asserting it here would need a resolver and a network. This test asserts
only that the two declarations agree — which is the failure a human makes when
bumping one pin in a hurry, and the one no build catches, because the image
build reads only one of the two files.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RUNTIME_TXT: Final[Path] = REPO_ROOT / "requirements-runtime.txt"
PYPROJECT: Final[Path] = REPO_ROOT / "pyproject.toml"

#: A dependency line, ignoring comments and blank lines. Requirement specifiers
#: are normalised only by case and by the `_`/`-` equivalence PEP 503 declares,
#: because `psycopg[binary,pool]==3.3.4` must compare equal to itself and not to
#: `psycopg==3.3.4` — the extras are part of what is installed.
_COMMENT: Final[re.Pattern[str]] = re.compile(r"#.*$")


def _normalise(spec: str) -> str:
    return spec.strip().lower().replace("_", "-")


def _requirements_txt() -> tuple[str, ...]:
    lines = RUNTIME_TXT.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for raw in lines:
        stripped = _COMMENT.sub("", raw).strip()
        if stripped:
            out.append(_normalise(stripped))
    return tuple(out)


def _runtime_extra() -> tuple[str, ...]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    return tuple(_normalise(spec) for spec in extras["runtime"])


@pytest.mark.unit
def test_the_two_runtime_declarations_hold_the_same_requirements() -> None:
    from_txt = set(_requirements_txt())
    from_toml = set(_runtime_extra())

    only_txt = sorted(from_txt - from_toml)
    only_toml = sorted(from_toml - from_txt)

    # Named, not counted. A count admits any substitute; naming the members
    # fails on a third entry AND on the right one being swapped for the wrong
    # one. (STATUS.md section 7: "Assert which, not how many.")
    assert not only_txt and not only_toml, (
        "requirements-runtime.txt and pyproject [project.optional-dependencies].runtime "
        "have drifted.\n"
        f"  only in requirements-runtime.txt: {only_txt or 'none'}\n"
        f"  only in pyproject runtime extra:  {only_toml or 'none'}\n"
        "The serving image installs the .txt; `pip install .[runtime]` installs the "
        "extra. A difference means the container and the developer run different code."
    )


@pytest.mark.unit
def test_every_runtime_requirement_is_pinned_exactly() -> None:
    """A range makes the image a function of the day it was built.

    The one thing a judge can check about a deployment is that it is the same
    thing as the repository. ``>=`` breaks that claim silently: two builds of
    the same commit resolve differently and nothing reports it.
    """
    unpinned = [spec for spec in _requirements_txt() if "==" not in spec]
    assert not unpinned, (
        f"unpinned runtime requirements: {unpinned}. "
        "Serving pins are exact; the dev extra is where ranges belong."
    )


@pytest.mark.unit
def test_the_dev_extra_includes_the_runtime_extra_rather_than_restating_it() -> None:
    """The dev lane must serve the same application it tests.

    If ``dev`` re-listed fastapi at its own pin, a developer could reproduce a
    green suite against a version the container never runs — which is the shape
    of D-12-003, where a fixture and a type written by the same hand agreed with
    each other and proved nothing.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev = [_normalise(spec) for spec in data["project"]["optional-dependencies"]["dev"]]
    assert "provenance[runtime]" in dev, (
        "the dev extra must include provenance[runtime] so there is one pin per "
        "runtime dependency"
    )

    runtime_names = {re.split(r"[\[=<>!~]", spec, maxsplit=1)[0] for spec in _runtime_extra()}
    dev_names = {
        re.split(r"[\[=<>!~]", spec, maxsplit=1)[0]
        for spec in dev
        if not spec.startswith("provenance")
    }
    overlap = sorted(runtime_names & dev_names)
    assert not overlap, (
        f"these are declared in BOTH the runtime and dev extras: {overlap}. "
        "Two pins for one package is the drift this file exists to prevent; "
        "remove the dev copy and let provenance[runtime] supply it."
    )
