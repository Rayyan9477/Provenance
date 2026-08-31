"""Every table the Kernel writes must be granted to the Kernel's role.

The defect this closes
-----------------------
``commit_trigger_evaluation`` makes its idempotency claim the **first** statement
of the transaction, deliberately: that is what closes the window in which the
effect commits and the key does not. Against the live cluster it fails::

    psycopg.errors.InsufficientPrivilege: user pv_kernel_writer does not have
    SELECT privilege on relation idempotency_records

Migration ``0008`` revokes it, with a reason::

    # The Kernel can never send anything, and can never mint an approval.
    "REVOKE ALL ON TABLE action_executions, ingest_aliases, idempotency_records,
     processed_events FROM pv_kernel_writer"

Both halves are defensible and they contradict each other, so this is a design
conflict rather than a typo. The resolution is narrow: ``idempotency_records``
was grouped with the *action* tables because idempotency used to be an
API-request concern, and a trigger evaluation's dedupe is neither sending nor
approving. The Kernel gets ``SELECT, INSERT`` — enough to claim a key and read
its own claim back, not enough to rewrite anyone else's. ``action_intents`` and
``action_executions`` stay revoked, so the sentence in that comment stays true.

Why the whole class, not the one table
---------------------------------------
Nothing caught this until a request reached the database, because the Kernel's
statements and the grant list are two files that nobody compares. Every unit
test drives a fake connection, and a fake grants everything. The consequence is
the worst shape a failure can take: it is invisible until the exact moment the
capability is exercised for real, and the demo's prospective-memory step is the
first thing that exercises it.

So this compares the two **statically**, in both directions. It is the same
instrument as ``test_graph_names_match_the_schema.py`` — parse the authority,
compare the code against it — applied to privileges instead of CHECK
constraints.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[4]
_VERSIONS = _REPO_ROOT / "db" / "migrations" / "versions"
_KERNEL = _REPO_ROOT / "services" / "control_plane" / "app" / "memory_kernel"

KERNEL_ROLE = "pv_kernel_writer"

#: Tables the Kernel legitimately reads without writing are still grants it needs.
_STATEMENT = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.I
)
_GRANT = re.compile(
    r"GRANT\s+(?P<privs>[A-Z, ]+?)\s+ON\s+(?:TABLE\s+)?(?P<tables>[a-z_,\s]+?)\s+TO\s+(?P<role>[a-z_]+)",
    re.I | re.S,
)
_REVOKE = re.compile(
    r"REVOKE\s+(?P<privs>[A-Z, ]+?)\s+ON\s+(?:TABLE\s+)?(?P<tables>[a-z_,\s]+?)\s+FROM\s+(?P<role>[a-z_]+)",
    re.I | re.S,
)

#: Not tables. SQL keywords and CTE names the crude statement scan picks up.
_NOT_A_TABLE = frozenset(
    {
        "select",
        "values",
        "set",
        "where",
        "returning",
        "on",
        "as",
        "q",
        "ranked",
        "updated",
        "dual",
    }
)


def _upgrade_ddl(path: Path) -> list[str]:
    """Only the DDL a revision's ``upgrade()`` actually executes.

    Three things in a migration file look like DDL to a regex and are not:

    * the **module docstring**, which in ``0009b`` quotes ``0008``'s revoke
      verbatim in order to explain it;
    * the constants ``downgrade()`` uses -- ``REVOKE_DDL`` is module-level and
      only the rollback path runs it;
    * commented-out blocks (``0002`` keeps one deliberately).

    The first draft of this file read all three and concluded that
    ``idempotency_records`` was still revoked, one line below the grant that
    grants it. A guard that reads documentation and rollback code as live
    privilege is worse than no guard, because it reports a real grant as
    missing.

    So the AST is walked instead: find ``upgrade()``, collect the names it
    references and the literals it passes, and resolve those names against
    module-level string assignments.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    constants: dict[str, list[str]] = {}
    for node in tree.body:
        # BOTH forms. The grant tuples are annotated -- `_GRANTS: Final[tuple[str, ...]] = (...)`
        # -- which is an AnnAssign, not an Assign. Collecting only Assign found
        # four statements in 0008 instead of the whole grant block, and the scan
        # reported almost nothing granted.
        if isinstance(node, ast.Assign) and node.targets:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
            value = node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        literals = [
            n.value
            for n in ast.walk(value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        if literals:
            constants[target.id] = literals

    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None
    )
    if upgrade is None:
        return []

    statements: list[str] = []
    for node in ast.walk(upgrade):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            statements.append(node.value)
        elif isinstance(node, ast.Name):
            statements.extend(constants.get(node.id, []))
    return statements


def _tables(blob: str) -> set[str]:
    return {t.strip() for t in blob.replace("\n", " ").split(",") if t.strip()}


def _granted_to_kernel() -> set[str]:
    """Tables the Kernel role can reach, applying GRANT and REVOKE **in order**.

    Order matters and the first draft got it wrong: it applied every GRANT then
    every REVOKE, so `0008`'s blanket revoke silently undid a grant made by a
    *later* revision.

    Revisions are walked in chain order rather than filename order, because
    `0009_gemini_embedding_plane` sorts before `0009a` and `0009b` while running
    after both.
    """
    granted: set[str] = set()
    for path in _revisions_in_chain_order():
        events: list[tuple[int, bool, str]] = []
        for index, statement in enumerate(_upgrade_ddl(path)):
            for match in _GRANT.finditer(statement):
                if match.group("role").lower() == KERNEL_ROLE:
                    events.append((index, True, match.group("tables")))
            for match in _REVOKE.finditer(statement):
                if (
                    match.group("role").lower() == KERNEL_ROLE
                    and "ALL" in match.group("privs").upper()
                ):
                    events.append((index, False, match.group("tables")))
        for _, is_grant, blob in events:
            if is_grant:
                granted |= _tables(blob)
            else:
                granted -= _tables(blob)
    return granted


def _revisions_in_chain_order() -> list[Path]:
    """Revision paths ordered by ``down_revision``, not by filename."""
    by_revision: dict[str, tuple[str | None, Path]] = {}
    for path in _VERSIONS.glob("0*.py"):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision = "([^"]+)"', text, re.M)
        down = re.search(r'^down_revision = "([^"]+)"', text, re.M)
        if rev:
            by_revision[rev.group(1)] = (down.group(1) if down else None, path)

    ordered: list[Path] = []
    seen: set[str] = set()

    def walk(name: str) -> None:
        if name in seen or name not in by_revision:
            return
        seen.add(name)
        down, path = by_revision[name]
        if down:
            walk(down)
        ordered.append(path)

    for name in by_revision:
        walk(name)
    return ordered


def _tables_the_kernel_touches() -> set[str]:
    touched: set[str] = set()
    for path in _KERNEL.glob("*.py"):
        for match in _STATEMENT.finditer(path.read_text(encoding="utf-8")):
            name = match.group(1).lower()
            if name not in _NOT_A_TABLE:
                touched.add(name)
    return touched


def test_the_grant_scan_is_armed() -> None:
    """A regex that matched nothing would make the comparison vacuous."""
    granted = _granted_to_kernel()
    assert len(granted) >= 10, f"only parsed {sorted(granted)} as granted; the pattern is wrong"
    assert "cases" in granted and "claims" in granted


def test_the_statement_scan_is_armed() -> None:
    touched = _tables_the_kernel_touches()
    assert len(touched) >= 8, f"only found {sorted(touched)}; the Kernel writes more than that"
    assert "cases" in touched


def test_every_table_the_kernel_touches_is_granted_to_its_role() -> None:
    """The regression.

    A statement the Kernel cannot execute is not a slow failure — it raises
    `InsufficientPrivilege` at the database, at the moment the capability is
    first exercised for real.
    """
    touched = _tables_the_kernel_touches()
    granted = _granted_to_kernel()
    # Only compare against tables the schema actually has; the scan is crude.
    known = {t for t in touched if t in _known_tables()}
    missing = sorted(known - granted)
    assert not missing, (
        f"{KERNEL_ROLE} has no grant on {missing}, but the Kernel's own statements "
        "read or write them. This raises InsufficientPrivilege against the live "
        "cluster and no unit test can see it, because every unit test drives a "
        "fake connection and a fake grants everything."
    )


def _known_tables() -> set[str]:
    """Every table the migrations create."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in _VERSIONS.glob("0*.py"))
    return {
        m.group(1).lower()
        for m in re.finditer(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?([a-z_]+)", source, re.I)
    }


def test_the_kernel_still_cannot_send_or_approve() -> None:
    """The property `0008`'s comment asserts, kept true by the repair.

    Granting `idempotency_records` must not quietly widen the Kernel into the
    action plane. If this ever fails, the grant went too far.
    """
    granted = _granted_to_kernel()
    for forbidden in ("action_executions", "ingest_aliases"):
        assert forbidden not in granted, (
            f"{KERNEL_ROLE} was granted {forbidden}. `0008`: 'The Kernel can never "
            "send anything, and can never mint an approval.'"
        )
