"""Print the single head revision of the Alembic chain, or fail loudly.

Why this is a file rather than a line in the deploy script
----------------------------------------------------------
It began as inline Python inside a double-quoted shell assignment in
``deploy/cloudrun.sh``, which meant the regex needed both quote characters
inside a shell string that was already using both. That is the kind of quoting
that works on the machine where it was written and produces an empty string
somewhere else -- and an empty string here is indistinguishable from the bug it
was added to fix, because ``schema_revision`` arriving empty is precisely the
symptom.

So it is a script with tests. `deploy/cloudrun.sh` calls it, and if it cannot
determine a unique head it exits non-zero rather than printing nothing, because
a deploy that silently reports the wrong schema revision is worse than one that
stops and says why.

Why not ``alembic heads``
-------------------------
That needs the Alembic runtime, a config file and an importable env module. This
needs a directory of files. The deploy already has the repository and may not
have the virtualenv, and this answer does not change with either.
"""

from __future__ import annotations

import pathlib
import re
import sys

#: Matches ``revision = "x"``, ``revision: str = "x"`` and the single-quoted
#: forms. Anchored at the start of a line so a mention inside a docstring or a
#: comment cannot be mistaken for the assignment.
_REVISION = re.compile(r"""^revision(?::[^=]+)?\s*=\s*["']([^"']+)["']""", re.M)
_DOWN = re.compile(r"""^down_revision(?::[^=]+)?\s*=\s*["']([^"']+)["']""", re.M)

DEFAULT_VERSIONS_DIR = pathlib.Path("db/migrations/versions")


def head_revision(versions_dir: pathlib.Path = DEFAULT_VERSIONS_DIR) -> str:
    """The one revision nothing else points down to.

    Raises ``ValueError`` when there is not exactly one, because both other
    answers are real problems: zero heads means the chain is a cycle, and more
    than one means two migrations were authored against the same parent and
    whichever ran second is not recorded anywhere.
    """
    if not versions_dir.is_dir():
        raise ValueError(f"{versions_dir} is not a directory")

    revisions: dict[str, str] = {}
    parents: set[str] = set()
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = _REVISION.search(text)
        parent = _DOWN.search(text)
        if revision is not None:
            revisions[revision.group(1)] = path.name
        if parent is not None:
            parents.add(parent.group(1))

    if not revisions:
        raise ValueError(f"no migration in {versions_dir} declares a revision")

    heads = sorted(set(revisions) - parents)
    if len(heads) != 1:
        named = ", ".join(f"{h} ({revisions[h]})" for h in heads) or "none"
        raise ValueError(
            f"expected exactly one head in {versions_dir}, found {len(heads)}: {named}"
        )
    return heads[0]


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path()
    try:
        print(head_revision(root / DEFAULT_VERSIONS_DIR))
    except ValueError as exc:
        print(f"schema_head: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
