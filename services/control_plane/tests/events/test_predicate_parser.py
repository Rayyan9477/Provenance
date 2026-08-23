"""The predicate parser: closed grammar, whitelisted paths, hard budgets.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` sections 4 (grammar), 5 (registry),
  6 (``ast.py``), 15 (test matrix items 1-6 and 22).
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, "Trigger
  arithmetic": **no general arithmetic nodes**; derived comparisons use named
  projection fields from a reviewed registry.

What these tests are actually defending
---------------------------------------
``predicate_ast`` is authored from attacker-influenceable text and then stored
for months. Every assertion below is about what a hostile or malformed document
*cannot* become: not code, not a query, not a path outside the registry, and
not a tree large enough to be a denial-of-service surface. A parser that fails
open here fails open in six months, on a row nobody remembers writing.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from services.control_plane.app.triggers import ast as trigger_ast
from services.control_plane.app.triggers.ast import (
    AST_SCHEMA_VERSION,
    MAX_ARGS,
    MAX_BINDINGS,
    MAX_DEPTH,
    MAX_NODES,
    BoolNode,
    CompareNode,
    ConstNode,
    FieldNode,
    NullCheckNode,
    TriggerSpecError,
    ValueType,
    parse_spec,
)
from services.control_plane.app.triggers.evaluator import evaluate_predicate
from services.control_plane.app.triggers.registry import (
    COMMITMENT_FIELDS,
    STATIC_FIELDS,
    resolve_field,
)
from services.control_plane.tests.events._support.canon import (
    HERO_COMMITMENT_ID,
    hero_predicate_document,
)

pytestmark = pytest.mark.unit


def _spec(predicate: dict[str, Any], bindings: dict[str, Any] | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"ast_version": AST_SCHEMA_VERSION, "predicate": predicate}
    if bindings is not None:
        doc["bindings"] = bindings
    return doc


def _deposit_bindings() -> dict[str, Any]:
    return {"deposit": {"kind": "COMMITMENT", "id": str(HERO_COMMITMENT_ID)}}


def _true_leaf() -> dict[str, Any]:
    return {"op": "NOT_NULL", "arg": {"op": "FIELD", "path": "clock.now"}}


# ---------------------------------------------------------------------------
# The whitelist — section 5, matrix items 1 and 2.
# ---------------------------------------------------------------------------


def test_unknown_field_rejected() -> None:
    """Matrix item 1. ``users.email`` is not in the registry, so it is not a path.

    Section 5.5: no path reaches ``users``, ``evidence_items``, ``claims`` or
    ``beliefs``. A predicate that could read the evidence plane would let an
    attacker who forwarded a hostile PDF influence what wakes up months later.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec({"op": "NOT_NULL", "arg": {"op": "FIELD", "path": "users.email"}}),
            resolve_field,
        )
    assert excinfo.value.code == "UNKNOWN_FIELD"


@pytest.mark.parametrize(
    "path",
    [
        "evidence_items.count",
        "beliefs.value",
        "outbox_events.status",
        "case.password",
        "commitments.deposit.raw_text",
    ],
)
def test_paths_outside_the_registry_are_all_refused(path: str) -> None:
    with pytest.raises(TriggerSpecError):
        resolve_field(path, {"deposit": HERO_COMMITMENT_ID})


def test_unbound_commitment_rejected() -> None:
    """Matrix item 2. ``commitments.<name>`` needs a declared binding.

    ``deposit`` is a local binding name resolved to one commitment UUID, never
    a ``commitment_type``: a case can hold two commitments of the same type and
    a type selector would silently change meaning when the second is admitted.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        resolve_field("commitments.ghost.status", {"deposit": HERO_COMMITMENT_ID})
    assert excinfo.value.code == "UNBOUND_COMMITMENT"


def test_unknown_commitment_leaf_rejected() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        resolve_field("commitments.deposit.secret_note", {"deposit": HERO_COMMITMENT_ID})
    assert excinfo.value.code == "UNKNOWN_FIELD"


def test_the_registry_is_exactly_the_paths_section_five_prints() -> None:
    """The closed set, asserted as a set rather than spot-checked.

    A registry that grows by accident is the failure this whole design is built
    against: adding a path widens what a model-authored predicate may observe,
    and that is a security review, not a typo.
    """
    assert set(STATIC_FIELDS) == {
        "clock.now",
        "case.status",
        "case.revision",
        "case.attention_level",
        "case.reopened_count",
        "case.opened_at",
        "case.resolved_at",
        "case.last_activity_at",
        "case.days_since_last_activity",
        "case.open_conflict_count",
        "case.needs_human_conflict_count",
        "case.active_commitment_count",
        "case.total_outstanding_amount",
        "case.outstanding_currency",
        "trigger.not_before",
        "trigger.expires_at",
        "trigger.evaluation_version",
        "trigger.basis_case_revision",
    }
    assert set(COMMITMENT_FIELDS) == {
        "status",
        "commitment_type",
        "revision",
        "currency",
        "committed_amount",
        "fulfilled_amount",
        "outstanding_amount",
        "due_at",
        "valid_from",
        "valid_to",
        "days_overdue",
        "has_admitted_fulfillment",
    }


# ---------------------------------------------------------------------------
# No arithmetic — CANONICAL_DECISIONS.md is explicit and this is the check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["ADD", "SUB", "MUL", "DIV", "RATIO", "CALL", "IN", "LIKE"])
def test_there_are_no_arithmetic_or_call_nodes(op: str) -> None:
    """ "No general arithmetic nodes in the trigger DSL."

    ``days_overdue`` and ``outstanding_amount`` are *named projection fields*
    computed once in reviewed Python, not expressions a stored predicate
    re-derives its own way. This test is the structural form of that decision:
    the operator simply does not exist in the grammar.
    """
    assert op not in trigger_ast.ALL_OPS
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": op,
                    "left": {"op": "FIELD", "path": "case.revision"},
                    "right": {"op": "CONST", "type": "INT", "value": 1},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "UNKNOWN_OP"


def test_the_grammar_is_exactly_thirteen_operators() -> None:
    assert (
        frozenset(
            {
                "AND",
                "OR",
                "NOT",
                "EQ",
                "NE",
                "GT",
                "GTE",
                "LT",
                "LTE",
                "IS_NULL",
                "NOT_NULL",
                "FIELD",
                "CONST",
            }
        )
        == trigger_ast.ALL_OPS
    )


# ---------------------------------------------------------------------------
# The type system — section 4.2, matrix items 3, 4 and 5.
# ---------------------------------------------------------------------------


def test_decimal_const_must_be_string() -> None:
    """Matrix item 3. A JSON number for money is a parse error.

    ``0.1 + 0.2 != 0.3`` must never decide whether a user is owed USD 1,800.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "GT",
                    "left": {"op": "FIELD", "path": "case.total_outstanding_amount"},
                    "right": {"op": "CONST", "type": "DECIMAL", "value": 0},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "DECIMAL_MUST_BE_STRING"


def test_decimal_const_as_string_is_accepted_and_is_a_decimal() -> None:
    spec = parse_spec(
        _spec(
            {
                "op": "GT",
                "left": {"op": "FIELD", "path": "case.total_outstanding_amount"},
                "right": {"op": "CONST", "type": "DECIMAL", "value": "1800.0000"},
            }
        ),
        resolve_field,
    )
    root = spec.root
    assert isinstance(root, CompareNode)
    right = root.right
    assert isinstance(right, ConstNode)
    assert str(right.value) == "1800.0000"


def test_string_ordering_rejected() -> None:
    """Matrix item 4. ``STRING`` has no order here, deliberately.

    No locale, no collation, no case-folding decision to get wrong, and no way
    for a stored predicate's meaning to drift when the cluster's collation does.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "GT",
                    "left": {"op": "FIELD", "path": "case.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "RESOLVED"},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "NOT_ORDERED"


def test_type_mismatch_rejected() -> None:
    """Matrix item 5. ``STRING`` versus ``TIMESTAMP`` is not comparable."""
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "EQ",
                    "left": {"op": "FIELD", "path": "case.status"},
                    "right": {"op": "FIELD", "path": "clock.now"},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "TYPE_MISMATCH"


def test_decimal_and_int_interoperate() -> None:
    """``NUMERIC = {DECIMAL, INT}``: the two are mutually comparable."""
    spec = parse_spec(
        _spec(
            {
                "op": "GT",
                "left": {"op": "FIELD", "path": "case.total_outstanding_amount"},
                "right": {"op": "CONST", "type": "INT", "value": 0},
            }
        ),
        resolve_field,
    )
    assert isinstance(spec.root, CompareNode)


def test_naive_timestamp_const_rejected() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "GTE",
                    "left": {"op": "FIELD", "path": "clock.now"},
                    "right": {"op": "CONST", "type": "TIMESTAMP", "value": "2026-06-15T00:00:00"},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "NAIVE_TIMESTAMP"


def test_const_null_rejected() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "EQ",
                    "left": {"op": "FIELD", "path": "case.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": None},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "CONST_NULL"


def test_bool_const_must_be_a_bool_not_an_int() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "EQ",
                    "left": {"op": "FIELD", "path": "commitments.deposit.has_admitted_fulfillment"},
                    "right": {"op": "CONST", "type": "BOOL", "value": 1},
                },
                _deposit_bindings(),
            ),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_BOOL"


def test_int_const_rejects_a_bool() -> None:
    """``isinstance(True, int)`` is ``True`` in Python, so this needs its own guard."""
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "GT",
                    "left": {"op": "FIELD", "path": "case.revision"},
                    "right": {"op": "CONST", "type": "INT", "value": True},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_INT"


# ---------------------------------------------------------------------------
# Budgets — section 4.5, matrix item 6.
# ---------------------------------------------------------------------------


def test_node_budget_enforced() -> None:
    """Matrix item 6. A hostile proposal cannot exhaust memory during validation."""
    args = [_true_leaf() for _ in range(MAX_ARGS)]
    tree: dict[str, Any] = {"op": "AND", "args": args}
    # Nest until the node count is certain to exceed MAX_NODES.
    for _ in range(4):
        tree = {"op": "AND", "args": [tree, dict(tree)]}
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(_spec(tree), resolve_field)
    assert excinfo.value.code == "BUDGET_EXCEEDED"


def test_depth_budget_enforced() -> None:
    tree: dict[str, Any] = _true_leaf()
    for _ in range(MAX_DEPTH + 2):
        tree = {"op": "NOT", "arg": tree}
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(_spec(tree), resolve_field)
    assert excinfo.value.code == "BUDGET_EXCEEDED"


def test_arg_budget_enforced() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec({"op": "AND", "args": [_true_leaf() for _ in range(MAX_ARGS + 1)]}),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_ARGS"


def test_binding_budget_enforced() -> None:
    bindings = {
        f"b{index}": {"kind": "COMMITMENT", "id": str(uuid.uuid4())}
        for index in range(MAX_BINDINGS + 1)
    }
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(_spec(_true_leaf(), bindings), resolve_field)
    assert excinfo.value.code == "BUDGET_EXCEEDED"


def test_const_string_length_budget_enforced() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "EQ",
                    "left": {"op": "FIELD", "path": "case.status"},
                    "right": {"op": "CONST", "type": "STRING", "value": "x" * 300},
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "BUDGET_EXCEEDED"


def test_the_budget_constants_are_the_ones_section_four_five_prints() -> None:
    assert (MAX_NODES, MAX_DEPTH, MAX_ARGS, MAX_BINDINGS) == (128, 12, 16, 8)


# ---------------------------------------------------------------------------
# Shape rules — operands are not predicates, and the root is not an operand.
# ---------------------------------------------------------------------------


def test_operand_may_not_be_the_root() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(_spec({"op": "FIELD", "path": "clock.now"}), resolve_field)
    assert excinfo.value.code == "OPERAND_IN_PREDICATE_POSITION"


def test_operand_may_not_be_a_boolean_argument() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "AND",
                    "args": [_true_leaf(), {"op": "CONST", "type": "BOOL", "value": True}],
                }
            ),
            resolve_field,
        )
    assert excinfo.value.code == "OPERAND_IN_PREDICATE_POSITION"


def test_unsupported_ast_version_rejected() -> None:
    doc = _spec(_true_leaf())
    doc["ast_version"] = "2.0"
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(doc, resolve_field)
    assert excinfo.value.code == "UNSUPPORTED_AST_VERSION"


def test_unused_binding_rejected() -> None:
    """An unreferenced binding is a generation bug, not a harmless extra.

    Letting it through would make the trigger list claim the trigger watches a
    commitment it never reads.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(_spec(_true_leaf(), _deposit_bindings()), resolve_field)
    assert excinfo.value.code == "UNUSED_BINDING"


def test_cross_tenant_binding_shape_is_still_only_a_uuid() -> None:
    """Matrix item 20's parser half: a binding is a UUID and nothing else.

    Ownership is checked by the Kernel at arm time and by ``m.case_id = $1`` in
    the projection query. The parser's job is only to refuse anything that is
    not a bare identifier — a binding carrying a tenant or a table name would
    be the beginning of a query.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {
                    "op": "NOT_NULL",
                    "arg": {"op": "FIELD", "path": "commitments.deposit.due_at"},
                },
                {"deposit": {"kind": "COMMITMENT", "id": "other_tenant.commitments"}},
            ),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_BINDING"


def test_only_commitment_bindings_exist() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _spec(
                {"op": "NOT_NULL", "arg": {"op": "FIELD", "path": "commitments.deposit.due_at"}},
                {"deposit": {"kind": "CASE", "id": str(HERO_COMMITMENT_ID)}},
            ),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_BINDING"


# ---------------------------------------------------------------------------
# The hero predicate parses, and its hash is stable — matrix items 9 and 22.
# ---------------------------------------------------------------------------


def test_hero_predicate_parses_into_the_shape_section_twelve_describes() -> None:
    spec = parse_spec(hero_predicate_document(), resolve_field)
    assert isinstance(spec.root, BoolNode)
    assert spec.root.op == "AND"
    assert len(spec.root.args) == 7
    assert isinstance(spec.root.args[0], NullCheckNode)
    assert spec.binding_ids() == {"deposit": HERO_COMMITMENT_ID}
    assert spec.referenced_paths == (
        "case.status",
        "clock.now",
        "commitments.deposit.due_at",
        "commitments.deposit.outstanding_amount",
        "commitments.deposit.status",
    )


def test_field_nodes_carry_their_registry_type_and_nullability() -> None:
    spec = parse_spec(hero_predicate_document(), resolve_field)
    assert isinstance(spec.root, BoolNode)
    gte = spec.root.args[1]
    assert isinstance(gte, CompareNode)
    left, right = gte.left, gte.right
    assert isinstance(left, FieldNode) and isinstance(right, FieldNode)
    assert (left.value_type, left.nullable) == (ValueType.TIMESTAMP, False)
    assert (right.value_type, right.nullable) == (ValueType.TIMESTAMP, True)


def test_predicate_sha256_is_stable() -> None:
    """Matrix item 22. Key order and whitespace do not change the hash.

    The hash is what a stored evaluation cites months later. If it moved when a
    serializer reordered keys, every historical evaluation would look tampered.
    """
    first = parse_spec(hero_predicate_document(), resolve_field)
    reordered = hero_predicate_document()
    reordered = {
        "predicate": reordered["predicate"],
        "bindings": reordered["bindings"],
        "ast_version": reordered["ast_version"],
    }
    second = parse_spec(reordered, resolve_field)
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64


def test_node_ids_are_a_deterministic_preorder_index() -> None:
    """``nid`` keys the stored trace, so it must not depend on dict iteration."""
    first = parse_spec(hero_predicate_document(), resolve_field)
    second = parse_spec(hero_predicate_document(), resolve_field)
    assert isinstance(first.root, BoolNode)
    assert isinstance(second.root, BoolNode)
    assert first.root.nid == second.root.nid == 0
    assert [arg.nid for arg in first.root.args] == [arg.nid for arg in second.root.args]
    assert first.node_count == second.node_count


# ---------------------------------------------------------------------------
# The contract encoding — ``provenance_contracts.predicates.PredicateNode``.
# ---------------------------------------------------------------------------
#
# The Memory Kernel stores ``PredicateNode.model_dump(mode="json")``, and that
# serialization differs from ``16_TRIGGER_DSL.md`` section 6 in three ways:
# every operand lives in ``args`` rather than in ``left``/``right``/``arg``,
# ``CONST`` carries no ``type`` key, and there is no
# ``{ast_version, bindings, predicate}`` envelope.
#
# The resolution is a widening of the accepted **encoding**, never of the
# **language**. Both spellings produce the same closed grammar, the same
# whitelist, the same budgets, the same type rules and -- critically -- the
# same refusal of a JSON number for money.
# ``test_an_untyped_decimal_const_must_still_be_a_string`` is what keeps the
# second half of that promise honest: an inference that reached for ``float``
# would be exactly the ``0.1 + 0.2`` hazard section 4.2.5 exists to prevent.


def _contract_node(op: str, **kwargs: Any) -> dict[str, Any]:
    """One node exactly as ``PredicateNode.model_dump(mode="json")`` renders it.

    Explicit ``null``s and an empty ``args`` on every node, because that is what
    Pydantic emits and therefore what is actually in the JSONB column.
    """
    return {
        "op": op,
        "path": kwargs.get("path"),
        "value": kwargs.get("value"),
        "args": kwargs.get("args", []),
    }


def _contract_spec(predicate: dict[str, Any]) -> dict[str, Any]:
    """Wrap a contract-shaped node, declaring the deposit binding only if used.

    ``UNUSED_BINDING`` is a real rule and this helper must not tiptoe around it:
    a predicate that reads no commitment declares no binding.
    """
    uses_commitment = "commitments." in json.dumps(predicate)
    bindings = {"deposit": HERO_COMMITMENT_ID} if uses_commitment else {}
    return trigger_ast.build_spec_document(predicate=predicate, bindings=bindings)


def test_the_contract_serialization_is_the_shape_this_parser_must_accept() -> None:
    """Pinned against the contract itself, not against a hand-written sample.

    If ``PredicateNode`` ever grows a ``value_type``, this test fails and the
    inference below can be deleted rather than left as dead defensive code.
    """
    from provenance_contracts.predicates import PredicateNode
    from provenance_domain.enums import PredicateOp

    dumped = PredicateNode(
        op=PredicateOp.GT,
        args=(
            PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.outstanding_amount"),
            PredicateNode(op=PredicateOp.CONST, value="0"),
        ),
    ).model_dump(mode="json")

    assert set(dumped) == {"op", "path", "value", "args"}
    assert "type" not in dumped["args"][1], "the contract carries no CONST type key"
    assert "left" not in dumped
    assert "right" not in dumped


def test_a_bare_predicate_node_is_refused_with_an_actionable_code() -> None:
    """The envelope is not optional, and saying so early is the whole point.

    ``bindings`` has nowhere else to live: ``commitments.<name>`` resolves
    through it, and ``ProposedTrigger`` carries no field that could hold one.
    Accepting a bare node would let a trigger arm successfully and then fail
    months later with ``UNBOUND_COMMITMENT`` -- a silent ``PROJECTION_FAILED``
    on a row nobody remembers writing. Refusing at arm time, with a code that
    names the fix, is the difference between a five-minute defect and an
    unexplained one.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_node("NOT_NULL", args=[_contract_node("FIELD", path="clock.now")]),
            resolve_field,
        )
    assert excinfo.value.code == "MISSING_SPEC_ENVELOPE"
    assert "ast_version" in str(excinfo.value)


def test_build_spec_document_wraps_a_node_into_a_parseable_envelope() -> None:
    """One implementation of the envelope, offered rather than re-typed.

    The Kernel is the component that knows the resolved commitment UUIDs -- it
    just wrote them -- so it is the right place to build the envelope, and this
    keeps the shape owned by the module that parses it.
    """
    from provenance_contracts.predicates import PredicateNode
    from provenance_domain.enums import PredicateOp

    node = PredicateNode(
        op=PredicateOp.GT,
        args=(
            PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.outstanding_amount"),
            PredicateNode(op=PredicateOp.CONST, value="0"),
        ),
    ).model_dump(mode="json")

    document = trigger_ast.build_spec_document(
        predicate=node, bindings={"deposit": HERO_COMMITMENT_ID}
    )
    assert document["ast_version"] == AST_SCHEMA_VERSION
    assert document["bindings"] == {
        "deposit": {"kind": "COMMITMENT", "id": str(HERO_COMMITMENT_ID)}
    }
    spec = parse_spec(document, resolve_field)
    assert spec.binding_ids() == {"deposit": HERO_COMMITMENT_ID}


def test_operands_may_live_in_args_instead_of_left_and_right() -> None:
    spec = parse_spec(
        _contract_spec(
            _contract_node(
                "GTE",
                args=[
                    _contract_node("FIELD", path="clock.now"),
                    _contract_node("FIELD", path="commitments.deposit.due_at"),
                ],
            )
        ),
        resolve_field,
    )
    root = spec.root
    assert isinstance(root, CompareNode)
    assert isinstance(root.left, FieldNode)
    assert root.left.path == "clock.now"
    assert isinstance(root.right, FieldNode)
    assert root.right.path == "commitments.deposit.due_at"


@pytest.mark.parametrize("op", ["IS_NULL", "NOT_NULL"])
def test_unary_operands_may_live_in_args_instead_of_arg(op: str) -> None:
    spec = parse_spec(
        _contract_spec(
            _contract_node(op, args=[_contract_node("FIELD", path="commitments.deposit.due_at")])
        ),
        resolve_field,
    )
    assert isinstance(spec.root, NullCheckNode)


def test_not_may_take_its_operand_from_args() -> None:
    spec = parse_spec(
        _contract_spec(
            _contract_node(
                "NOT",
                args=[
                    _contract_node(
                        "NOT_NULL",
                        args=[_contract_node("FIELD", path="commitments.deposit.due_at")],
                    )
                ],
            )
        ),
        resolve_field,
    )
    assert spec.root.op == "NOT"


def test_a_comparison_with_the_wrong_arity_in_args_is_refused() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(_contract_node("GTE", args=[_contract_node("FIELD", path="clock.now")])),
            resolve_field,
        )
    assert excinfo.value.code == "BAD_ARGS"


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("commitments.deposit.status", "FULFILLED", ValueType.STRING),
        ("commitments.deposit.due_at", "2026-06-15T00:00:00Z", ValueType.TIMESTAMP),
        ("commitments.deposit.has_admitted_fulfillment", True, ValueType.BOOL),
        ("case.revision", 11, ValueType.INT),
        ("commitments.deposit.outstanding_amount", "1800.0000", ValueType.DECIMAL),
    ],
)
def test_an_untyped_const_takes_its_type_from_the_field_it_is_compared_with(
    path: str, value: Any, expected: ValueType
) -> None:
    """The type comes from the reviewed registry, not from the value's shape.

    Guessing from the JSON -- "it looks like a number, so it is a DECIMAL" --
    would silently reinterpret an account number as money the first time one
    happened to be all digits. The registry already knows the type of every
    readable path, and it is the only place in the system with the authority to
    say so.
    """
    spec = parse_spec(
        _contract_spec(
            _contract_node(
                "EQ",
                args=[_contract_node("FIELD", path=path), _contract_node("CONST", value=value)],
            )
        ),
        resolve_field,
    )
    assert isinstance(spec.root, CompareNode)
    right = spec.root.right
    assert isinstance(right, ConstNode)
    assert right.value_type is expected


def test_an_untyped_const_is_inferred_from_either_side() -> None:
    spec = parse_spec(
        _contract_spec(
            _contract_node(
                "EQ",
                args=[
                    _contract_node("CONST", value="FULFILLED"),
                    _contract_node("FIELD", path="commitments.deposit.status"),
                ],
            )
        ),
        resolve_field,
    )
    assert isinstance(spec.root, CompareNode)
    assert isinstance(spec.root.left, ConstNode)
    assert spec.root.left.value_type is ValueType.STRING


def test_an_untyped_decimal_const_must_still_be_a_string() -> None:
    """The money rule survives inference completely intact.

    This is the single assertion that makes accepting the contract encoding
    safe. ``{"value": 0}`` against a DECIMAL field infers DECIMAL and then runs
    the *same* coercion, which refuses a JSON number -- so the widening cannot
    smuggle binary floating point into an obligation calculation by the back
    door. ``scripts/seed/obligations.py`` writes exactly this, and it is a
    defect there rather than something to accommodate here.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(
                _contract_node(
                    "GT",
                    args=[
                        _contract_node("FIELD", path="commitments.deposit.outstanding_amount"),
                        _contract_node("CONST", value=0),
                    ],
                )
            ),
            resolve_field,
        )
    assert excinfo.value.code == "DECIMAL_MUST_BE_STRING"


def test_an_untyped_const_still_refuses_a_naive_timestamp() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(
                _contract_node(
                    "GTE",
                    args=[
                        _contract_node("FIELD", path="commitments.deposit.due_at"),
                        _contract_node("CONST", value="2026-06-15T00:00:00"),
                    ],
                )
            ),
            resolve_field,
        )
    assert excinfo.value.code == "NAIVE_TIMESTAMP"


def test_an_untyped_const_still_refuses_an_unordered_comparison() -> None:
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(
                _contract_node(
                    "GT",
                    args=[
                        _contract_node("FIELD", path="commitments.deposit.status"),
                        _contract_node("CONST", value="ACTIVE"),
                    ],
                )
            ),
            resolve_field,
        )
    assert excinfo.value.code == "NOT_ORDERED"


def test_two_untyped_constants_cannot_be_compared() -> None:
    """Nothing in the registry can say what these mean, so nothing guesses.

    A predicate comparing two constants is meaningless anyway: it is a fact
    about the predicate, not about the world.
    """
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(
                _contract_node(
                    "EQ",
                    args=[
                        _contract_node("CONST", value="ACTIVE"),
                        _contract_node("CONST", value="FULFILLED"),
                    ],
                )
            ),
            resolve_field,
        )
    assert excinfo.value.code == "UNTYPED_CONST"


def test_an_untyped_const_in_a_null_test_is_refused() -> None:
    """``IS_NULL(CONST)`` has no field to infer from, and no meaning either."""
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(
            _contract_spec(
                _contract_node("IS_NULL", args=[_contract_node("CONST", value="ACTIVE")])
            ),
            resolve_field,
        )
    assert excinfo.value.code == "UNTYPED_CONST"


def test_an_explicit_type_still_wins_over_inference() -> None:
    """Section 12.1's spelling is untouched, so the hero predicate's hash is too."""
    spec = parse_spec(hero_predicate_document(), resolve_field)
    assert isinstance(spec.root, BoolNode)
    third = spec.root.args[2]
    assert isinstance(third, CompareNode)
    assert isinstance(third.right, ConstNode)
    assert third.right.value_type is ValueType.DECIMAL


def test_both_encodings_of_one_predicate_agree_on_every_verdict() -> None:
    """The two spellings are one language, and this is how that is checked.

    Encoding and meaning are separated: the trees differ in ``nid`` layout and
    the documents hash differently -- deliberately, because the hash identifies
    the bytes that were stored -- while every node's verdict is identical.
    """
    values = {
        "clock.now": datetime(2026, 9, 18, 13, 0, tzinfo=UTC),
        "commitments.deposit.due_at": datetime(2026, 6, 15, 0, 0, tzinfo=UTC),
    }
    spec_a = parse_spec(
        _spec(
            {
                "op": "GTE",
                "left": {"op": "FIELD", "path": "clock.now"},
                "right": {"op": "FIELD", "path": "commitments.deposit.due_at"},
            },
            _deposit_bindings(),
        ),
        resolve_field,
    )
    spec_b = parse_spec(
        _contract_spec(
            _contract_node(
                "GTE",
                args=[
                    _contract_node("FIELD", path="clock.now"),
                    _contract_node("FIELD", path="commitments.deposit.due_at"),
                ],
            )
        ),
        resolve_field,
    )
    assert spec_a.sha256 != spec_b.sha256, "different bytes, therefore different hashes"
    assert spec_a.referenced_paths == spec_b.referenced_paths
    first = evaluate_predicate(spec_a, values)
    second = evaluate_predicate(spec_b, values)
    assert first.result is second.result
    assert [step.result for step in first.node_trace] == [step.result for step in second.node_trace]
