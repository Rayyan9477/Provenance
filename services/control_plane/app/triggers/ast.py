"""Safe predicate AST for prospective-memory triggers — ``16_TRIGGER_DSL.md`` §6.

This module parses, type-checks and canonicalises predicate specifications. It
performs **no I/O**, reads **no clock**, and imports nothing from
``provenance_db``. Parsing is total: either a valid, fully typed tree is
returned, or :class:`TriggerSpecError` is raised.

Why the stored predicate is data and not code
---------------------------------------------
``16_TRIGGER_DSL.md`` §3 is a four-part argument and the short version is: a
trigger predicate is authored from attacker-influenceable text and then sits in
a database for months. If it were executable, the forwarded PDF would have got
a code-execution primitive with a several-month fuse. What is stored instead is
a closed algebraic term over a whitelisted projection, and this parser is the
gate that keeps it closed.

There is no arithmetic here, and that is the decision
-----------------------------------------------------
``CANONICAL_DECISIONS.md`` -> *Memory, action, and time*: "No general
arithmetic nodes in the trigger DSL. Add reviewed deterministic derived fields
to the projection registry." :data:`ALL_OPS` has thirteen members and none of
them computes. "Days overdue" is ``commitments.<b>.days_overdue`` — a named
field derived once, in reviewed Python, in ``projection.py`` — not an
expression each stored predicate re-derives its own way. A reviewer inspecting
a stored term can therefore tell what it means without also auditing how it
computes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

from services.control_plane.app.triggers.config import AST_SCHEMA_VERSION

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.control_plane.app.triggers.registry import FieldSpec

__all__ = [
    "ALL_OPS",
    "AST_SCHEMA_VERSION",
    "BOOLEAN_OPS",
    "COMPARE_OPS",
    "MAX_ARGS",
    "MAX_BINDINGS",
    "MAX_CONST_STRING_LEN",
    "MAX_DEPTH",
    "MAX_NODES",
    "NULL_OPS",
    "NUMERIC_TYPES",
    "OPERAND_OPS",
    "ORDERED_TYPES",
    "ORDER_OPS",
    "BoolNode",
    "CommitmentBinding",
    "CompareNode",
    "ConstNode",
    "FieldNode",
    "FieldResolver",
    "Node",
    "NotNode",
    "NullCheckNode",
    "PredicateSpec",
    "TriggerSpecError",
    "ValueType",
    "build_spec_document",
    "canonical_json",
    "parse_spec",
]

# ---------------------------------------------------------------------------
# Budgets — §4.5. Enforced before any semantic check, so a hostile proposal
# cannot exhaust memory during validation.
# ---------------------------------------------------------------------------

MAX_NODES: Final[int] = 128
MAX_DEPTH: Final[int] = 12
MAX_ARGS: Final[int] = 16
MAX_CONST_STRING_LEN: Final[int] = 256
MAX_BINDINGS: Final[int] = 8

BINDING_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

BOOLEAN_OPS: Final[frozenset[str]] = frozenset({"AND", "OR", "NOT"})
COMPARE_OPS: Final[frozenset[str]] = frozenset({"EQ", "NE", "GT", "GTE", "LT", "LTE"})
ORDER_OPS: Final[frozenset[str]] = frozenset({"GT", "GTE", "LT", "LTE"})
NULL_OPS: Final[frozenset[str]] = frozenset({"IS_NULL", "NOT_NULL"})
OPERAND_OPS: Final[frozenset[str]] = frozenset({"FIELD", "CONST"})

#: The whole grammar. Thirteen members, no arithmetic, no call, no set
#: membership. Adding one is the change ``CANONICAL_DECISIONS.md`` forbids.
ALL_OPS: Final[frozenset[str]] = BOOLEAN_OPS | COMPARE_OPS | NULL_OPS | OPERAND_OPS


class TriggerSpecError(ValueError):
    """A predicate spec is structurally or semantically invalid.

    Raised only during parse and validation, which happen before storage. It is
    never raised while evaluating an already-stored predicate: a stored spec
    that no longer parses is an operational alarm (``PROJECTION_FAILED``), not
    an exception the evaluator swallows.
    """

    def __init__(self, code: str, message: str, path: str = "$") -> None:
        super().__init__(f"{code} at {path}: {message}")
        self.code = code
        self.message = message
        self.path = path


class ValueType(str, Enum):
    DECIMAL = "DECIMAL"
    INT = "INT"
    STRING = "STRING"
    BOOL = "BOOL"
    TIMESTAMP = "TIMESTAMP"


#: ``DECIMAL`` and ``INT`` are mutually comparable; nothing else crosses.
NUMERIC_TYPES: Final[frozenset[ValueType]] = frozenset({ValueType.DECIMAL, ValueType.INT})

#: ``STRING`` and ``BOOL`` are deliberately absent: there is no locale, no
#: collation and no case-folding decision to get wrong, and no way for a stored
#: predicate's meaning to drift when the cluster's collation changes.
ORDERED_TYPES: Final[frozenset[ValueType]] = frozenset(
    {ValueType.DECIMAL, ValueType.INT, ValueType.TIMESTAMP}
)


class FieldResolver(Protocol):
    """``registry.resolve_field``, passed in rather than imported.

    Injection keeps this module free of the registry and therefore trivially
    testable against a narrowed whitelist, and it makes the dependency arrow
    point one way: the parser knows *that* paths are resolved, never *which*.
    """

    def __call__(self, path: str, bindings: Mapping[str, UUID]) -> FieldSpec: ...


# ---------------------------------------------------------------------------
# Node types. ``nid`` is a deterministic pre-order index assigned during parse
# and is what the evaluation trace keys on, so a stored trace stays
# interpretable after the code that produced it has been redeployed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Node:
    nid: int
    op: str


@dataclass(frozen=True, slots=True)
class FieldNode(Node):
    path: str
    value_type: ValueType
    nullable: bool


@dataclass(frozen=True, slots=True)
class ConstNode(Node):
    value_type: ValueType
    #: Already coerced to ``Decimal`` / ``int`` / ``str`` / ``bool`` /
    #: ``datetime``. A raw JSON scalar never survives the parser.
    value: Any


@dataclass(frozen=True, slots=True)
class CompareNode(Node):
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class NullCheckNode(Node):
    arg: Node


@dataclass(frozen=True, slots=True)
class NotNode(Node):
    arg: Node


@dataclass(frozen=True, slots=True)
class BoolNode(Node):
    args: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class CommitmentBinding:
    """One local name bound to one commitment UUID.

    A binding is a UUID and never a ``commitment_type``: a case can hold two
    commitments of the same type, so a type selector would be ambiguous and
    would silently change meaning the moment a second one is admitted.
    """

    name: str
    commitment_id: UUID


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    ast_version: str
    bindings: tuple[CommitmentBinding, ...]
    root: Node
    node_count: int
    referenced_paths: tuple[str, ...]
    canonical_json: str
    sha256: str

    def binding_ids(self) -> dict[str, UUID]:
        return {binding.name: binding.commitment_id for binding in self.bindings}


# ---------------------------------------------------------------------------
# Const coercion — §4.2.
# ---------------------------------------------------------------------------


def _coerce_const(vtype: ValueType, raw: Any, path: str) -> Any:
    if raw is None:
        raise TriggerSpecError("CONST_NULL", "CONST may not be null; use IS_NULL", path)

    if vtype is ValueType.DECIMAL:
        # Money and quantities travel as strings. A JSON float here would import
        # binary floating point into an obligation calculation, and
        # `0.1 + 0.2 != 0.3` must never decide whether a user is owed USD 1,800.
        if not isinstance(raw, str):
            raise TriggerSpecError(
                "DECIMAL_MUST_BE_STRING",
                'DECIMAL constants must be JSON strings, e.g. "1800.0000"',
                path,
            )
        try:
            return Decimal(raw)
        except InvalidOperation as exc:
            raise TriggerSpecError("BAD_DECIMAL", f"not a decimal: {raw!r}", path) from exc

    if vtype is ValueType.INT:
        # `isinstance(True, int)` is True in Python, so bool needs its own
        # rejection or `{"type": "INT", "value": true}` silently becomes 1.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TriggerSpecError("BAD_INT", f"not an integer: {raw!r}", path)
        if not (-(2**63) <= raw < 2**63):
            raise TriggerSpecError("BAD_INT", "int64 range exceeded", path)
        return raw

    if vtype is ValueType.STRING:
        if not isinstance(raw, str):
            raise TriggerSpecError("BAD_STRING", f"not a string: {raw!r}", path)
        if len(raw) > MAX_CONST_STRING_LEN:
            raise TriggerSpecError("BUDGET_EXCEEDED", "STRING const too long", path)
        return raw

    if vtype is ValueType.BOOL:
        if not isinstance(raw, bool):
            raise TriggerSpecError("BAD_BOOL", f"not a boolean: {raw!r}", path)
        return raw

    if not isinstance(raw, str):
        raise TriggerSpecError("BAD_TIMESTAMP", "TIMESTAMP must be an ISO-8601 string", path)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TriggerSpecError("BAD_TIMESTAMP", f"unparseable: {raw!r}", path) from exc
    if parsed.tzinfo is None:
        raise TriggerSpecError(
            "NAIVE_TIMESTAMP",
            "TIMESTAMP constants must carry an explicit offset (use ...Z)",
            path,
        )
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, bindings: Mapping[str, UUID], resolve_field: FieldResolver) -> None:
        self._bindings = bindings
        self._resolve_field = resolve_field
        self._next_nid = 0
        self.paths: list[str] = []

    @property
    def node_count(self) -> int:
        return self._next_nid

    def _nid(self) -> int:
        self._next_nid += 1
        if self._next_nid > MAX_NODES:
            raise TriggerSpecError("BUDGET_EXCEEDED", f"more than {MAX_NODES} nodes")
        return self._next_nid - 1

    # -- operands -----------------------------------------------------------

    def parse_operand(self, doc: Any, jpath: str, expected: ValueType | None = None) -> Node:
        if not isinstance(doc, Mapping):
            raise TriggerSpecError("NOT_A_NODE", "operand must be an object", jpath)
        op = doc.get("op")
        if op not in OPERAND_OPS:
            raise TriggerSpecError(
                "EXPECTED_OPERAND", f"expected FIELD or CONST, got {op!r}", jpath
            )

        if op == "FIELD":
            path = doc.get("path")
            if not isinstance(path, str):
                raise TriggerSpecError("BAD_FIELD_PATH", "path must be a string", jpath)
            spec = self._resolve_field(path, self._bindings)
            self.paths.append(path)
            return FieldNode(
                nid=self._nid(),
                op="FIELD",
                path=path,
                value_type=spec.value_type,
                nullable=spec.nullable,
            )

        raw_type = doc.get("type")
        if raw_type is None:
            # The contract encoding carries no `type`. Take it from the operand
            # this CONST is compared against, whose type comes from the reviewed
            # registry -- never from the shape of the JSON value. Guessing from
            # the value ("it looks like a number, so it is a DECIMAL") would
            # silently reinterpret an account number as money the first time one
            # happened to be all digits.
            if expected is None:
                raise TriggerSpecError(
                    "UNTYPED_CONST",
                    "CONST carries no type and is not compared against a "
                    "registry-typed field, so nothing can say what it means",
                    jpath,
                )
            vtype = expected
        else:
            try:
                vtype = ValueType(raw_type)
            except ValueError as exc:
                raise TriggerSpecError(
                    "UNKNOWN_TYPE", f"bad CONST type {raw_type!r}", jpath
                ) from exc
        # The SAME coercion either way. That is what keeps the money rule intact
        # under inference: DECIMAL still refuses a JSON number, so the widening
        # cannot smuggle binary floating point into an obligation calculation.
        return ConstNode(
            nid=self._nid(),
            op="CONST",
            value_type=vtype,
            value=_coerce_const(vtype, doc.get("value"), jpath),
        )

    def peek_operand_type(self, doc: Any, jpath: str) -> ValueType | None:
        """The type of an operand, without consuming a node id.

        ``nid`` is a deterministic pre-order index that a stored trace keys on,
        so peeking must not allocate one. A ``FIELD`` resolves through the
        registry -- pure, and it raises the same error the real parse would
        raise, in the same order -- while a ``CONST`` answers only if it
        declared a type.
        """
        del jpath  # part of the signature for symmetry with parse_operand
        if not isinstance(doc, Mapping):
            return None
        op = doc.get("op")
        if op == "FIELD":
            path = doc.get("path")
            if not isinstance(path, str):
                return None
            return self._resolve_field(path, self._bindings).value_type
        if op == "CONST":
            raw_type = doc.get("type")
            if raw_type is None:
                return None
            try:
                return ValueType(raw_type)
            except ValueError:
                return None
        return None

    @staticmethod
    def _comparison_operands(doc: Mapping[str, Any], jpath: str) -> tuple[Any, Any]:
        """``left``/``right`` (section 6) or a two-element ``args`` (the contract).

        Two spellings of one thing. ``left``/``right`` wins when present so the
        section 12.1 document keeps parsing byte for byte, hash included.
        """
        if "left" in doc or "right" in doc:
            if "left" not in doc or "right" not in doc:
                raise TriggerSpecError("BAD_ARGS", "a comparison needs both left and right", jpath)
            return doc["left"], doc["right"]
        args = doc.get("args")
        if not isinstance(args, Sequence) or isinstance(args, str | bytes) or len(args) != 2:
            raise TriggerSpecError("BAD_ARGS", "a comparison takes exactly two operands", jpath)
        return args[0], args[1]

    @staticmethod
    def _single_child(doc: Mapping[str, Any], jpath: str) -> Any:
        """``arg`` (section 6) or a one-element ``args`` (the contract)."""
        if "arg" in doc:
            return doc["arg"]
        args = doc.get("args")
        if not isinstance(args, Sequence) or isinstance(args, str | bytes) or len(args) != 1:
            raise TriggerSpecError("BAD_ARGS", "this operator takes exactly one operand", jpath)
        return args[0]

    @staticmethod
    def _operand_type(node: Node) -> ValueType:
        if isinstance(node, FieldNode | ConstNode):
            return node.value_type
        raise TriggerSpecError("EXPECTED_OPERAND", f"{node.op} is not an operand")

    # -- predicates ---------------------------------------------------------

    def parse_predicate(self, doc: Any, jpath: str, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise TriggerSpecError("BUDGET_EXCEEDED", f"depth exceeds {MAX_DEPTH}", jpath)
        if not isinstance(doc, Mapping):
            raise TriggerSpecError("NOT_A_NODE", "predicate must be an object", jpath)

        op = doc.get("op")
        if op not in ALL_OPS:
            raise TriggerSpecError("UNKNOWN_OP", f"unknown op {op!r}", jpath)
        if op in OPERAND_OPS:
            raise TriggerSpecError(
                "OPERAND_IN_PREDICATE_POSITION",
                f"{op} is an operand and may not be used as a predicate",
                jpath,
            )

        if op in {"AND", "OR"}:
            args = doc.get("args")
            if not isinstance(args, Sequence) or isinstance(args, str | bytes):
                raise TriggerSpecError("BAD_ARGS", f"{op}.args must be an array", jpath)
            if not (2 <= len(args) <= MAX_ARGS):
                raise TriggerSpecError(
                    "BAD_ARGS", f"{op} takes 2..{MAX_ARGS} arguments, got {len(args)}", jpath
                )
            nid = self._nid()
            parsed = tuple(
                self.parse_predicate(arg, f"{jpath}.args[{index}]", depth + 1)
                for index, arg in enumerate(args)
            )
            return BoolNode(nid=nid, op=op, args=parsed)

        if op == "NOT":
            child = self._single_child(doc, jpath)
            nid = self._nid()
            return NotNode(
                nid=nid,
                op="NOT",
                arg=self.parse_predicate(child, f"{jpath}.arg", depth + 1),
            )

        if op in NULL_OPS:
            child = self._single_child(doc, jpath)
            nid = self._nid()
            # No `expected` here, deliberately: a null test has no sibling to
            # infer from, and `IS_NULL(CONST)` has no meaning to infer either.
            return NullCheckNode(nid=nid, op=str(op), arg=self.parse_operand(child, f"{jpath}.arg"))

        left_doc, right_doc = self._comparison_operands(doc, jpath)
        left_hint = self.peek_operand_type(left_doc, f"{jpath}.left")
        right_hint = self.peek_operand_type(right_doc, f"{jpath}.right")
        nid = self._nid()
        left = self.parse_operand(left_doc, f"{jpath}.left", expected=right_hint)
        right = self.parse_operand(right_doc, f"{jpath}.right", expected=left_hint)
        left_type, right_type = self._operand_type(left), self._operand_type(right)

        if left_type in NUMERIC_TYPES and right_type in NUMERIC_TYPES:
            pass  # DECIMAL and INT interoperate
        elif left_type is not right_type:
            raise TriggerSpecError(
                "TYPE_MISMATCH",
                f"cannot compare {left_type.value} with {right_type.value}",
                jpath,
            )

        if op in ORDER_OPS and not (left_type in ORDERED_TYPES and right_type in ORDERED_TYPES):
            raise TriggerSpecError(
                "NOT_ORDERED",
                f"{op} requires ordered types; STRING and BOOL support only EQ/NE",
                jpath,
            )
        return CompareNode(nid=nid, op=str(op), left=left, right=right)


def canonical_json(doc: Mapping[str, Any]) -> str:
    """Sorted keys, no whitespace. The bytes the SHA-256 is taken over."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_bindings(raw_bindings: Any) -> list[CommitmentBinding]:
    if not isinstance(raw_bindings, Mapping):
        raise TriggerSpecError("BAD_BINDINGS", "bindings must be an object", "$.bindings")
    if len(raw_bindings) > MAX_BINDINGS:
        raise TriggerSpecError(
            "BUDGET_EXCEEDED", f"more than {MAX_BINDINGS} bindings", "$.bindings"
        )

    bindings: list[CommitmentBinding] = []
    for name, target in raw_bindings.items():
        jpath = f"$.bindings.{name}"
        if not isinstance(name, str) or not BINDING_NAME_RE.match(name):
            raise TriggerSpecError("BAD_BINDING_NAME", "must match ^[a-z][a-z0-9_]{0,31}$", jpath)
        if not isinstance(target, Mapping) or target.get("kind") != "COMMITMENT":
            raise TriggerSpecError("BAD_BINDING", "only kind=COMMITMENT is supported", jpath)
        try:
            commitment_id = UUID(str(target.get("id")))
        except (ValueError, TypeError) as exc:
            raise TriggerSpecError("BAD_BINDING", "id must be a UUID", jpath) from exc
        bindings.append(CommitmentBinding(name=name, commitment_id=commitment_id))
    return bindings


def build_spec_document(
    *, predicate: Mapping[str, Any], bindings: Mapping[str, UUID]
) -> dict[str, Any]:
    """Wrap a predicate node in the stored ``predicate_ast`` envelope.

    Offered to the Memory Kernel rather than left for it to hand-roll: the
    Kernel is the component that knows the resolved commitment UUIDs, because it
    just wrote them, but the envelope's shape belongs to the module that parses
    it. One implementation, one place to be wrong.

    ``bindings`` maps a local binding name to the commitment it names. The name
    is what a ``commitments.<name>.<field>`` path refers to; the UUID is what
    makes it unambiguous when a case holds two commitments of the same type.
    """
    return {
        "ast_version": AST_SCHEMA_VERSION,
        "bindings": {
            name: {"kind": "COMMITMENT", "id": str(commitment_id)}
            for name, commitment_id in bindings.items()
        },
        "predicate": dict(predicate),
    }


def parse_spec(doc: Mapping[str, Any], resolve_field: FieldResolver) -> PredicateSpec:
    """Parse and fully type-check a predicate spec.

    ``resolve_field(path, bindings) -> FieldSpec`` is supplied by
    ``registry.py``; see :class:`FieldResolver` for why it is injected.
    """
    if not isinstance(doc, Mapping):
        raise TriggerSpecError("NOT_A_SPEC", "spec must be an object")

    version = doc.get("ast_version")
    if version is None and "op" in doc:
        # A bare `PredicateNode` dump. `bindings` has nowhere else to live --
        # `commitments.<name>` resolves through it and `ProposedTrigger` carries
        # no field that could hold one -- so accepting this would let a trigger
        # arm cleanly and then fail months later with UNBOUND_COMMITMENT, on a
        # row nobody remembers writing. Fail here instead, naming the fix.
        raise TriggerSpecError(
            "MISSING_SPEC_ENVELOPE",
            "predicate_ast is a bare predicate node; it must be the "
            "{ast_version, bindings, predicate} envelope of 16_TRIGGER_DSL.md "
            "section 12.1. Use triggers.ast.build_spec_document() to wrap it.",
        )
    if version != AST_SCHEMA_VERSION:
        raise TriggerSpecError(
            "UNSUPPORTED_AST_VERSION", f"expected {AST_SCHEMA_VERSION}, got {version!r}"
        )

    raw_bindings = doc.get("bindings") or {}
    bindings = _parse_bindings(raw_bindings)
    binding_map = {binding.name: binding.commitment_id for binding in bindings}

    parser = _Parser(binding_map, resolve_field)
    root = parser.parse_predicate(doc.get("predicate"), "$.predicate", depth=0)

    if isinstance(root, FieldNode | ConstNode):  # pragma: no cover - defence in depth
        raise TriggerSpecError(
            "OPERAND_ROOT", "root must be a predicate, not an operand", "$.predicate"
        )

    declared = set(binding_map)
    used = {path.split(".", 2)[1] for path in parser.paths if path.startswith("commitments.")}
    unused = declared - used
    if unused:
        # An unreferenced binding is almost always a generation bug, and letting
        # it through would make the trigger list claim the trigger watches a
        # commitment it never reads.
        raise TriggerSpecError(
            "UNUSED_BINDING",
            f"bindings declared but never referenced: {sorted(unused)}",
            "$.bindings",
        )

    canonical = canonical_json(
        {
            "ast_version": version,
            "bindings": dict(raw_bindings),
            "predicate": doc.get("predicate"),
        }
    )
    return PredicateSpec(
        ast_version=version,
        bindings=tuple(bindings),
        root=root,
        node_count=parser.node_count,
        referenced_paths=tuple(sorted(set(parser.paths))),
        canonical_json=canonical,
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
