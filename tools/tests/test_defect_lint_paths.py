"""`DL08`'s owning-file predicate must accept real repository paths.

The defect this closes
-----------------------
`_looks_like_path` accepted a token only if it contained `/` or `.`:

    return "/" in text or "." in text

So it rejected `Makefile` — a root-level file with no extension and a
perfectly good repository-relative path. `LICENSE`, `NOTICE` and `Dockerfile`
fail the same way, and all four are files this repository actually has and that
a defect can legitimately be owned by.

The rule's purpose is to reject prose — "the seed module", "somewhere in the
kernel" — where a path belongs. Punctuation was a proxy for that. Asking the
filesystem is not a relaxation of the rule, it is the rule's actual intent:
a path that names nothing is no better than prose, and `Makefile` names
something.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.defect_lint import _looks_like_path

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "path",
    ["Makefile", "tools/defect_lint.py", "scripts/run_api.py", "ops/defects/DEFECTS.md"],
)
def test_a_real_repository_path_is_accepted(path: str) -> None:
    assert (REPO_ROOT / path).exists(), f"{path} does not exist; the test premise is stale"
    assert _looks_like_path(path), f"{path} is a real file and was rejected"


@pytest.mark.parametrize(
    "prose",
    ["the seed module", "somewhere in the kernel", "unknown", "several files"],
)
def test_prose_is_still_rejected(prose: str) -> None:
    assert not _looks_like_path(prose), f"{prose!r} is prose and was accepted"


def test_a_path_shaped_token_that_names_nothing_is_still_accepted() -> None:
    """Deliberate: the predicate must not fail on a file deleted by the fix.

    A defect owned by a file the fix *removes* still needs a valid record, so
    existence is sufficient but not necessary. What stays necessary is that the
    token is a single word shaped like a path.
    """
    assert _looks_like_path("services/control_plane/app/retrieval/ann_search.py")
