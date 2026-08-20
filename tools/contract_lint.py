#!/usr/bin/env python3
"""Three AST rules over the shipped ``provenance_contracts`` source.

Usage
-----
::

    python -m tools.contract_lint \\
        --rule no-float-money \\
        --rule schema-version-present \\
        --rule no-sql-in-contracts
    contract_lint: 3 rules, 0 violations

The rule count is printed with the violation count on purpose.
``contract_lint: 0 violations`` on its own is a vacuous pass: it reads the same
whether three rules ran and found nothing or zero rules ran at all.
``EXECUTION/70_TASK_PLAN.md`` T1.6 states that requirement in those words.

Authority
---------
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, last sub-task, which names the three
  rules and the output format.
- ``specs/11_CONTRACTS.md`` section 20.10, whose test this rule set
  generalises, including its ``AgentSafeView`` carve-out.
- ``specs/11_CONTRACTS.md`` section 23 risk 4: this is a structural check on
  the package *source*, not a taint analysis of runtime values. Preventing SQL
  injection is the job of parameterised queries in ``provenance_db``; this tool
  only prevents SQL re-entering the contract layer.

Why the rules are AST rules and not greps
------------------------------------------
Docstrings are allowed to discuss the data model in prose -- the spec depends
on that -- so the scan must be able to tell a docstring from a literal. A grep
cannot. Everything below therefore parses the module and walks nodes.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

PACKAGE_ROOT: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[1]
    / "packages"
    / "python"
    / "provenance_contracts"
    / "src"
    / "provenance_contracts"
)

# ---------------------------------------------------------------------------
# no-sql-in-contracts
# ---------------------------------------------------------------------------

#: The 26 canonical table names, from ``specs/10_DATABASE_DDL.md``.
CANONICAL_TABLES: Final[frozenset[str]] = frozenset(
    {
        "tenants",
        "users",
        "ingest_aliases",
        "counterparties",
        "relationships",
        "contexts",
        "cases",
        "source_artifacts",
        "evidence_items",
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "conflicts",
        "commitments",
        "fulfillments",
        "state_transitions",
        "memory_proposals",
        "kernel_decisions",
        "prospective_triggers",
        "action_intents",
        "action_executions",
        "outbox_events",
        "processed_events",
        "agent_runs",
        "idempotency_records",
    }
)

#: Statement *shapes* rather than bare keywords. ``from`` and ``where`` are
#: ordinary English and appear in error messages; ``SELECT ... FROM`` does not.
SQL_STATEMENT_PATTERNS: Final[tuple[str, ...]] = (
    r"\bselect\b[\s\S]{0,200}?\bfrom\b",
    r"\binsert\s+into\b",
    r"\bupdate\b[\s\S]{0,200}?\bset\b",
    r"\bdelete\s+from\b",
    r"\bupsert\s+into\b",
    r"\b(drop|alter|truncate)\s+(table|view|index|database)\b",
    r"\b(grant|revoke)\s+\w+\s+on\b",
    r"\bunion\s+(all\s+)?select\b",
    r"--\s*$",
    r";\s*(select|insert|update|delete|drop)\b",
)

#: Named, per-file, per-token carve-outs for the **table-name** half of the
#: rule only. Each entry is a word that is spec-mandated vocabulary at that
#: site and happens to collide with a table name.
#:
#: This is the mechanism section 20.10 already grants ``AgentSafeView``,
#: extended and made explicit. Three properties keep it narrow:
#:
#:   1. It is keyed on ``(file, token)``, so carving out "cases" in one module
#:      says nothing about any other module.
#:   2. It suppresses **only** the table-name finding. The SQL-statement half
#:      of the rule is never carved out, so a genuine
#:      ``SELECT ... FROM cases`` in a carved-out file is still a violation.
#:      ``tests/test_no_sql_in_contracts.py`` asserts exactly that.
#:   3. Every entry carries its reason in this table, so a reviewer can push
#:      back on a new one without reading the module.
TABLE_NAME_CARVE_OUTS: Final[Mapping[tuple[str, str], str]] = {
    ("predicates.py", "commitments"): (
        "projection root of the trigger DSL, fixed by specs/11_CONTRACTS.md "
        "section 11 and by the projection registry. It names a read-model "
        "namespace, not a table, and the grammar has no node that could build "
        "a query from it."
    ),
    ("predicates.py", "conflicts"): (
        "projection root of the trigger DSL, same authority and same reasoning "
        "as the commitments root above."
    ),
    ("identity.py", "cases"): (
        "the ordinary English plural in the CapabilityBinding message "
        "reproduced verbatim from specs/11_CONTRACTS.md section 7: 'a single "
        "binding may not span more than 16 cases'. It is prose addressed to a "
        "developer, not a reference to a relation."
    ),
    ("triggers.py", "claims"): (
        "the English verb in the message specs/11_CONTRACTS.md section 17 "
        "prints -- 'DISARMED/CASE_RESOLVED claims the case is resolved' -- "
        "which tests/test_kernel_result.py asserts verbatim. It is a verb, not "
        "the claims relation. Every other module reworded around the collision "
        "rather than spend a carve-out; this one could not, because the "
        "assertion pins the wording."
    ),
}

#: Section 20.10's own carve-out, restated as a rule: read-model view names
#: live in ``provenance_domain.enums.AgentSafeView``, where the enum functions
#: as an allowlist. The contracts package may reference the enum; it may not
#: hard-code a view name as a literal.
#:
#: Section 20.10 prints this as ``assert '"agent_' not in source``, which is a
#: prefix test and is **wrong**: the shipped ``settings.py`` carries
#: ``secret_key="agent_url"``, the Secrets Manager key for the
#: ``pv_agent_reader`` DSN, and a prefix test fails on it. A secret key is not
#: a view. The check below is exact instead of prefixed -- the five enum values
#: themselves, plus the ``agent_<name>_v1`` shape so a sixth view added to the
#: enum is caught before it is added here. Reported, not silently relaxed.
VIEW_NAME_SHAPE: Final[re.Pattern[str]] = re.compile(r"^agent_[a-z0-9_]+_v\d+$")

# ---------------------------------------------------------------------------
# no-float-money
# ---------------------------------------------------------------------------

#: A field whose *name* touches money. Word-boundaried on underscores so
#: ``amount_role`` matches and ``paramount`` does not.
MONEY_FIELD_NAME: Final[re.Pattern[str]] = re.compile(
    r"(^|_)("
    r"amount|amounts|money|price|cost|fee|fees|total|totals|balance|"
    r"outstanding|committed|fulfilled|refund|payment|charge|deposit|currency"
    r")($|_)"
)

# ---------------------------------------------------------------------------
# schema-version-present
# ---------------------------------------------------------------------------

#: The one class permitted to declare ``schema_version``. Declaring it anywhere
#: else is drift: a second default is a second answer to "what version is
#: this", and putting it on ``Contract`` would break section 20.3's assertion
#: that a serialised ``Money`` has exactly two keys.
SCHEMA_VERSION_OWNER: Final[str] = "BoundaryContract"

#: Boundary contracts deliberately absent from ``CONTRACT_REGISTRY``, with the
#: reason. Section 18's registry is reproduced exactly in ``__init__.py``, and
#: this is the one name it does not list.
REGISTRY_CARVE_OUTS: Final[Mapping[str, str]] = {
    "ModelAttribution": (
        "never crosses a boundary alone; it is a component of "
        "ResolutionAssessment, MemoryProposal and DraftAction, each of which "
        "is registered. specs/11_CONTRACTS.md section 18 does not list it."
    ),
}


@dataclass(frozen=True, slots=True)
class Violation:
    """One rule failure, addressed well enough to fix without searching."""

    rule: str
    path: pathlib.Path
    lineno: int
    message: str

    def render(self, root: pathlib.Path) -> str:
        try:
            where = self.path.relative_to(root)
        except ValueError:  # pragma: no cover - defensive
            where = self.path
        return f"{self.rule}: {where}:{self.lineno}: {self.message}"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One parsed module of the package under inspection."""

    path: pathlib.Path
    source: str
    tree: ast.Module

    @property
    def name(self) -> str:
        return self.path.name


def load_sources(root: pathlib.Path = PACKAGE_ROOT) -> tuple[SourceFile, ...]:
    """Every shipped module, parsed once and shared by all rules."""
    files: list[SourceFile] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        files.append(SourceFile(path=path, source=source, tree=ast.parse(source, str(path))))
    return tuple(files)


def string_constants(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string literal in *tree* except docstrings.

    Docstrings are skipped deliberately: they are allowed to discuss the data
    model in prose, and the specification depends on that.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _tokens(text: str) -> set[str]:
    return {token.strip(" .,;:()[]{}\"'") for token in text.split()}


def check_no_sql_in_contracts(files: Sequence[SourceFile]) -> list[Violation]:
    """No canonical table name, SQL statement shape, or view name in a literal."""
    rule = "no-sql-in-contracts"
    found: list[Violation] = []
    for file in files:
        for lineno, text in string_constants(file.tree):
            lowered = text.lower()

            # (a) statement shapes. Never carved out, in any file.
            for pattern in SQL_STATEMENT_PATTERNS:
                if re.search(pattern, lowered, flags=re.MULTILINE):
                    found.append(
                        Violation(
                            rule,
                            file.path,
                            lineno,
                            f"SQL statement shape /{pattern}/ in literal {text!r}",
                        )
                    )

            # (b) canonical table names, tokenised.
            for hit in sorted(_tokens(lowered) & CANONICAL_TABLES):
                if (file.name, hit) in TABLE_NAME_CARVE_OUTS:
                    continue
                found.append(
                    Violation(
                        rule,
                        file.path,
                        lineno,
                        f"canonical table name {hit!r} in literal {text!r}",
                    )
                )

            # (c) read-model view names belong to the domain package's enum.
            if VIEW_NAME_SHAPE.match(text) or text in _agent_safe_view_values():
                found.append(
                    Violation(
                        rule,
                        file.path,
                        lineno,
                        f"hard-coded read-model view name {text!r}; reference "
                        "provenance_domain.enums.AgentSafeView instead",
                    )
                )
    return found


def _agent_safe_view_values() -> frozenset[str]:
    """The allowlist itself, read from the domain package rather than copied.

    A second copy of a list of view names is a second thing to forget to
    update, which is the failure this whole tool exists to prevent.
    """
    from provenance_domain.enums import AgentSafeView

    return frozenset(member.value for member in AgentSafeView)


def _annotation_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            # A stringised annotation such as `"float"`.
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", child.value))
    return names


def check_no_float_money(files: Sequence[SourceFile]) -> list[Violation]:
    """No ``float`` on any field whose name or type touches money."""
    rule = "no-float-money"
    found: list[Violation] = []
    for file in files:
        for node in ast.walk(file.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                target = statement.target
                if not isinstance(target, ast.Name):
                    continue
                names = _annotation_names(statement.annotation)
                touches_money = bool(MONEY_FIELD_NAME.search(target.id)) or "Money" in names
                if touches_money and "float" in names:
                    found.append(
                        Violation(
                            rule,
                            file.path,
                            statement.lineno,
                            f"{node.name}.{target.id} is annotated with float; monetary "
                            "values are Decimal with an explicit currency, and there is "
                            "no float path into an obligation",
                        )
                    )
    return found


def _class_defs(files: Sequence[SourceFile]) -> dict[str, tuple[SourceFile, ast.ClassDef]]:
    defs: dict[str, tuple[SourceFile, ast.ClassDef]] = {}
    for file in files:
        for node in ast.walk(file.tree):
            if isinstance(node, ast.ClassDef):
                defs[node.name] = (file, node)
    return defs


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _inherits(
    name: str,
    ancestor: str,
    defs: Mapping[str, tuple[SourceFile, ast.ClassDef]],
    seen: set[str] | None = None,
) -> bool:
    seen = seen if seen is not None else set()
    if name in seen or name not in defs:
        return False
    seen.add(name)
    for base in _base_names(defs[name][1]):
        if base == ancestor or _inherits(base, ancestor, defs, seen):
            return True
    return False


def _declared_fields(node: ast.ClassDef) -> set[str]:
    return {
        statement.target.id
        for statement in node.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }


def _registry_names(files: Sequence[SourceFile]) -> tuple[set[str], int] | None:
    """The ``CONTRACT_REGISTRY`` values, read straight from ``__init__.py``."""
    for file in files:
        if file.name != "__init__.py":
            continue
        for node in ast.walk(file.tree):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            if not any(isinstance(t, ast.Name) and t.id == "CONTRACT_REGISTRY" for t in targets):
                continue
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            if isinstance(value, ast.Dict):
                return (
                    {v.id for v in value.values if isinstance(v, ast.Name)},
                    file.tree.body[0].lineno if file.tree.body else 1,
                )
    return None


def check_schema_version_present(files: Sequence[SourceFile]) -> list[Violation]:
    """Every boundary model declares ``schema_version``, and only one class does.

    Three checks, because "declares it" is three separable claims:

    1. the owner really declares it, so inheritance has something to carry;
    2. nothing else redeclares it, which is how a second default appears and
       how it would leak onto value objects like ``Money``;
    3. every registered contract actually inherits it, and every boundary
       contract is registered, so the registry and the class hierarchy cannot
       drift apart in either direction.
    """
    rule = "schema-version-present"
    found: list[Violation] = []
    defs = _class_defs(files)

    owner = defs.get(SCHEMA_VERSION_OWNER)
    if owner is None:
        return [
            Violation(
                rule,
                PACKAGE_ROOT,
                1,
                f"{SCHEMA_VERSION_OWNER} is not defined anywhere in the package",
            )
        ]
    owner_file, owner_node = owner
    if "schema_version" not in _declared_fields(owner_node):
        found.append(
            Violation(
                rule,
                owner_file.path,
                owner_node.lineno,
                f"{SCHEMA_VERSION_OWNER} does not declare schema_version, so nothing "
                "inherits it",
            )
        )

    for name, (file, node) in sorted(defs.items()):
        if name == SCHEMA_VERSION_OWNER:
            continue
        if "schema_version" in _declared_fields(node):
            found.append(
                Violation(
                    rule,
                    file.path,
                    node.lineno,
                    f"{name} redeclares schema_version; it belongs to "
                    f"{SCHEMA_VERSION_OWNER} once, and a second default is a second "
                    "answer to what version this is",
                )
            )

    registry = _registry_names(files)
    if registry is None:
        found.append(
            Violation(
                rule,
                PACKAGE_ROOT / "__init__.py",
                1,
                "CONTRACT_REGISTRY could not be read; boundary contracts are untested "
                "by construction without it",
            )
        )
        return found

    registered, lineno = registry
    for name in sorted(registered):
        if name not in defs:
            continue  # imported from elsewhere; the import itself would fail first
        if not _inherits(name, SCHEMA_VERSION_OWNER, defs):
            found.append(
                Violation(
                    rule,
                    PACKAGE_ROOT / "__init__.py",
                    lineno,
                    f"{name} is in CONTRACT_REGISTRY but is not a "
                    f"{SCHEMA_VERSION_OWNER}, so it carries no schema_version",
                )
            )
    for name, (file, node) in sorted(defs.items()):
        if name == SCHEMA_VERSION_OWNER or name in registered:
            continue
        if name in REGISTRY_CARVE_OUTS:
            continue
        if _inherits(name, SCHEMA_VERSION_OWNER, defs):
            found.append(
                Violation(
                    rule,
                    file.path,
                    node.lineno,
                    f"{name} is a {SCHEMA_VERSION_OWNER} but is missing from "
                    "CONTRACT_REGISTRY; a contract that is not registered is not "
                    "round-tripped and is untested by construction",
                )
            )
    return found


Rule = Callable[[Sequence[SourceFile]], list[Violation]]

#: Exactly three rules. The count is printed alongside the violation count so a
#: green run cannot be mistaken for a run that checked nothing.
RULES: Final[Mapping[str, Rule]] = {
    "no-float-money": check_no_float_money,
    "schema-version-present": check_schema_version_present,
    "no-sql-in-contracts": check_no_sql_in_contracts,
}


def run(rule_names: Sequence[str], files: Sequence[SourceFile] | None = None) -> list[Violation]:
    """Run *rule_names* over the package and return every violation."""
    sources = files if files is not None else load_sources()
    violations: list[Violation] = []
    for name in rule_names:
        violations.extend(RULES[name](sources))
    return violations


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="contract_lint",
        description="AST rules over the shipped provenance_contracts source.",
    )
    parser.add_argument(
        "--rule",
        action="append",
        dest="rules",
        choices=sorted(RULES),
        help="a rule to run; repeat the flag to run several. Default: all three.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rule_names: list[str] = list(args.rules) if args.rules else sorted(RULES)
    # De-duplicate while keeping the order the caller asked for, so
    # `--rule x --rule x` is one rule and the printed count stays honest.
    ordered: list[str] = []
    for name in rule_names:
        if name not in ordered:
            ordered.append(name)

    violations = run(ordered)
    for violation in _sorted(violations):
        print(violation.render(PACKAGE_ROOT))
    print(f"contract_lint: {len(ordered)} rules, {len(violations)} violations")
    return 1 if violations else 0


def _sorted(violations: Iterable[Violation]) -> list[Violation]:
    return sorted(violations, key=lambda v: (v.rule, str(v.path), v.lineno, v.message))


if __name__ == "__main__":
    sys.exit(main())
