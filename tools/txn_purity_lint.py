#!/usr/bin/env python
"""No model call and no network call inside a transaction callback — T3.4.

Authority
---------
- ``quality/23_PHASE_GATES.md`` section 9, ``G3.5``::

      python -m tools.txn_purity_lint services packages workers
      #   -> "scanned NN transaction callbacks, 0 network constructs found"

- ``EXECUTION/70_TASK_PLAN.md`` T3.4 — walk every function decorated
  ``@in_transaction`` and every lambda or function passed to the transaction
  wrapper; follow aliases, so ``import boto3 as b`` is caught; print the
  **scanned** count, not only the violation count.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 1.3 — the calls that are a build
  defect inside PHASE B, and why: the callback runs once per retry, so a model
  call inside it is charged again on every attempt, and an e-mail, an S3 put or
  an EventBridge publish inside it cannot be rolled back when the transaction
  is. That is what the outbox exists to avoid.
- ``CANONICAL_DECISIONS.md`` -> *Transaction isolation*.

Why the scanned count is printed
--------------------------------
``0 violations`` over ``0`` scanned callbacks is the classic vacuous pass: a
lint that silently stops finding callbacks reports success forever. The gate's
expected output names both numbers for that reason, and
``test_txn_purity_lint.py::test_the_repository_tree_is_clean`` asserts that the
scanned count is above zero.

What counts as a callback
-------------------------
1. A function or method decorated ``@in_transaction`` (however the decorator is
   imported or qualified).
2. A lambda passed to one of :data:`WRAPPER_CALLS`.
3. A function *referenced* by name as an argument to one of those calls, when
   that function is defined in the same module. A reference the linter cannot
   resolve is reported by name rather than counted as scanned — counting an
   unread callback would be the same lie as counting zero.

What counts as a violation
--------------------------
Inside such a callback: an ``import`` of a banned module, or an attribute chain
rooted at a name bound to one. Name resolution follows aliases in both
directions (``import boto3 as b``, ``from httpx import get as fetch``), because
a lint that only matches the literal module name is defeated by one keyword.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "BANNED_MODULE_PREFIXES",
    "BANNED_ROOTS",
    "DEFAULT_TARGETS",
    "WRAPPER_CALLS",
    "ScanResult",
    "Violation",
    "banned_module",
    "main",
    "scan_paths",
    "scan_source",
    "summary_line",
]

#: Top-level modules no transaction callback may reach. ``urllib`` and
#: ``urllib3`` are here because "we only used the standard library" is not a
#: defence: the transaction is still holding locks while the socket waits.
BANNED_ROOTS: frozenset[str] = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "botocore",
        # The Gemini SDK, added 2026-08-24 with the pivot. `google` rather than
        # `google.genai` because the ban resolves on the top-level root, and
        # every `google.*` client here is an outbound call.
        "google",
        "httpx",
        "requests",
        "urllib",
        "urllib3",
    }
)

#: Dotted prefixes of the project's own outbound wrappers. The Bedrock client
#: wrapper is named by T3.4 explicitly; these modules arrive in Phases 6 and 7
#: and the ban is in place before the first call site is written.
BANNED_MODULE_PREFIXES: frozenset[str] = frozenset(
    {
        "agents.runtime.model_router",
        "agents.runtime.tools",
        "provenance_telemetry.bedrock",
    }
)

#: The transaction wrapper, under both spellings the pack uses.
#: ``23_PHASE_GATES.md`` ``G3.5`` says ``run_in_transaction``;
#: ``12_KERNEL_ALGORITHMS.md`` section 7.2 prints ``run_in_serializable_tx``.
#: The linter answers to both, so the discrepancy cannot hide a callback.
WRAPPER_CALLS: frozenset[str] = frozenset({"run_in_transaction", "run_in_serializable_tx"})

#: The decorator that marks a callback, matched on the final attribute so that
#: ``@retry.in_transaction`` and ``@in_transaction`` both count.
DECORATOR_NAME = "in_transaction"

DEFAULT_TARGETS: tuple[str, ...] = ("services", "packages", "workers")


@dataclass(frozen=True, slots=True)
class Violation:
    """One banned construct inside one transaction callback."""

    path: str
    lineno: int
    callback: str
    construct: str
    root: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.lineno}: {self.construct} inside transaction "
            f"callback {self.callback!r} (reaches {self.root})"
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What one scan found."""

    scanned: int = 0
    violations: tuple[Violation, ...] = ()
    unresolved: tuple[str, ...] = ()
    files: int = 0

    def merge(self, other: ScanResult) -> ScanResult:
        return ScanResult(
            scanned=self.scanned + other.scanned,
            violations=self.violations + other.violations,
            unresolved=self.unresolved + other.unresolved,
            files=self.files + other.files,
        )


def banned_module(dotted: str) -> str | None:
    """The ban *dotted* trips, or ``None``.

    Matches the first segment against :data:`BANNED_ROOTS` and any dotted
    prefix against :data:`BANNED_MODULE_PREFIXES`, so ``boto3.client`` and
    ``agents.runtime.model_router.invoke`` are both caught while
    ``agents.runtime.graphs`` is not.
    """
    segments = dotted.split(".")
    if segments[0] in BANNED_ROOTS:
        return segments[0]
    for size in range(1, len(segments) + 1):
        prefix = ".".join(segments[:size])
        if prefix in BANNED_MODULE_PREFIXES:
            return prefix
    return None


def _alias_map(nodes: Iterable[ast.AST]) -> dict[str, str]:
    """Bound name -> the dotted module path it refers to."""
    aliases: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                aliases[bound] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                bound = alias.asname or alias.name
                aliases[bound] = f"{node.module}.{alias.name}"
    return aliases


def _chain(node: ast.expr) -> tuple[str, list[str]] | None:
    """Split an attribute chain rooted at a bare name into ``(root, attrs)``.

    ``b.client`` -> ``("b", ["client"])``. A chain whose base is a call —
    ``b.client("s3").get_object`` — returns ``None``, so the same construct is
    reported once, at its root, rather than at every link.
    """
    attrs: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        return current.id, list(reversed(attrs))
    return None


def _decorated_as_callback(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == DECORATOR_NAME:
            return True
        if isinstance(target, ast.Attribute) and target.attr == DECORATOR_NAME:
            return True
    return False


def _wrapper_call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name) and node.func.id in WRAPPER_CALLS:
        return node.func.id
    if isinstance(node.func, ast.Attribute) and node.func.attr in WRAPPER_CALLS:
        return node.func.attr
    return None


def _functions_by_name(
    tree: ast.AST,
) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function definition in the module, keyed by name.

    A *list*, not a single node: ``callback`` is defined several times in the
    same test module at different nesting depths, and picking one would leave
    the others unscanned while the count claimed otherwise. Resolving a
    reference to every definition of that name over-scans rather than
    under-scans, which is the safe direction for a guard.
    """
    found: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.setdefault(node.name, []).append(node)
    return found


@dataclass
class _Collector:
    path: str
    aliases: dict[str, str]
    violations: list[Violation] = field(default_factory=list)

    def scan(self, body: Iterable[ast.AST], callback: str) -> None:
        nodes = [child for node in body for child in ast.walk(node)]
        local = dict(self.aliases)
        local.update(_alias_map(nodes))

        for node in nodes:
            if isinstance(node, ast.Import | ast.ImportFrom):
                self._check_import(node, callback)

        consumed: set[int] = set()
        for node in nodes:
            if not isinstance(node, ast.Attribute):
                continue
            split = _chain(node)
            if split is None:
                continue
            root_name, attrs = split
            dotted = ".".join([local.get(root_name, root_name), *attrs])
            ban = banned_module(dotted)
            if ban is None:
                continue
            consumed.add(id(self._root_node(node)))
            self.violations.append(
                Violation(
                    path=self.path,
                    lineno=node.lineno,
                    callback=callback,
                    construct=".".join([root_name, *attrs]),
                    root=ban,
                )
            )

        for node in nodes:
            if not isinstance(node, ast.Name) or id(node) in consumed:
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            ban = banned_module(local.get(node.id, node.id))
            if ban is None:
                continue
            self.violations.append(
                Violation(
                    path=self.path,
                    lineno=node.lineno,
                    callback=callback,
                    construct=node.id,
                    root=ban,
                )
            )

    @staticmethod
    def _root_node(node: ast.Attribute) -> ast.expr:
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            current = current.value
        return current

    def _check_import(self, node: ast.Import | ast.ImportFrom, callback: str) -> None:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            names = [f"{node.module}.{alias.name}" for alias in node.names if node.module]
        for name in names:
            ban = banned_module(name)
            if ban is not None:
                self.violations.append(
                    Violation(
                        path=self.path,
                        lineno=node.lineno,
                        callback=callback,
                        construct=f"import {name}",
                        root=ban,
                    )
                )


def scan_source(source: str, path: str) -> ScanResult:
    """Scan one module's text.

    Raises nothing on a syntax error: a file that does not parse is reported as
    unresolved rather than crashing a lint that runs on every push.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:  # pragma: no cover - defensive
        return ScanResult(unresolved=(f"{path}: does not parse ({error.msg})",), files=1)

    aliases = _alias_map(tree.body)
    collector = _Collector(path=path, aliases=aliases)
    by_name = _functions_by_name(tree)
    seen: set[int] = set()
    unresolved: list[str] = []
    scanned = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not _decorated_as_callback(node) or id(node) in seen:
            continue
        seen.add(id(node))
        scanned += 1
        collector.scan(node.body, node.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        wrapper = _wrapper_call_name(node)
        if wrapper is None:
            continue
        argument = _callback_argument(node)
        if argument is None:
            unresolved.append(f"{path}:{node.lineno}: {wrapper}(...) has no callback argument")
            continue
        if isinstance(argument, ast.Lambda):
            scanned += 1
            collector.scan([argument.body], "<lambda>")
            continue
        name = _referenced_name(argument)
        if name is None:
            unresolved.append(f"{path}:{argument.lineno}: {wrapper}(...) callback is an expression")
            continue
        targets = by_name.get(name)
        if not targets:
            unresolved.append(f"{path}:{argument.lineno}: {name} (defined outside this module)")
            continue
        for target in targets:
            if id(target) in seen:
                continue
            seen.add(id(target))
            scanned += 1
            collector.scan(target.body, target.name)

    return ScanResult(
        scanned=scanned,
        violations=tuple(collector.violations),
        unresolved=tuple(unresolved),
        files=1,
    )


#: Keyword names the callback may be passed under.
CALLBACK_KEYWORDS: frozenset[str] = frozenset({"callback", "fn", "func"})


def _callback_argument(node: ast.Call) -> ast.expr | None:
    """The argument that is the transaction callback, or ``None``.

    Both wrapper spellings take the callback second — ``run_in_serializable_tx(
    pool, callback, ...)`` — so that is the positional rule, with the keyword
    forms accepted as well. A call that matches neither is reported as
    unresolved rather than skipped silently: the linter saying "I could not
    find the callback here" is information, and saying nothing is not.
    """
    for keyword in node.keywords:
        if keyword.arg in CALLBACK_KEYWORDS:
            return keyword.value
    if len(node.args) >= 2:
        return node.args[1]
    return None


def _referenced_name(node: ast.expr) -> str | None:
    """The callable a reference argument names, if it is a plain reference."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan_paths(paths: Sequence[Path]) -> ScanResult:
    """Scan every ``.py`` file under *paths*, recursively."""
    result = ScanResult()
    for target in paths:
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for file in files:
            if "__pycache__" in file.parts or ".venv" in file.parts:
                continue
            result = result.merge(scan_source(file.read_text(encoding="utf-8"), file.as_posix()))
    return result


def summary_line(result: ScanResult) -> str:
    """The line ``G3.5`` reads. Both numbers, always."""
    return (
        f"scanned {result.scanned} transaction callbacks, "
        f"{len(result.violations)} network constructs found"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. ``0`` clean, ``1`` violations found, ``2`` bad usage."""
    names = list(argv) if argv is not None else sys.argv[1:]
    targets = [Path(name) for name in (names or DEFAULT_TARGETS)]
    missing = [str(path) for path in targets if not path.exists()]
    if missing:
        print(f"no such path: {', '.join(missing)}", file=sys.stderr)
        return 2

    result = scan_paths(targets)
    for violation in result.violations:
        print(str(violation))
    for reference in result.unresolved:
        print(f"note: callback reference not resolved: {reference}")
    print(summary_line(result))
    return 1 if result.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
