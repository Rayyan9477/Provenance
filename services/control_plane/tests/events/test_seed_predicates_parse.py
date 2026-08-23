"""Every predicate the seed arms must parse with the real parser.

The defect this closes
-----------------------
`scripts/seed/obligations.py` wrote `_DEPOSIT_PREDICATE` and `_DAMAGE_PREDICATE`
as bare AST nodes -- `{"op": "AND", "args": [...]}` with `{"node": "FIELD"}`
operands -- while `16_TRIGGER_DSL.md` §6 and §12.1 specify an envelope,
`{ast_version, bindings, predicate}`, with binary operators taking `left` and
`right` rather than an `args` list. `parse_spec` rejects the seed's form with
`UNSUPPORTED_AST_VERSION`.

It was latent, and that is the interesting part. `prospective_triggers` is empty
by design until the Kernel's arm path runs, so nothing ever fed these documents
to the parser. They are *data* in a seed module: no import fails, no type
checker objects, no test touches them. The first thing that would have parsed
them is the hero demo arming its own trigger -- which is the second reveal, on
stage, in a recorded video.

Why this test rather than a type
---------------------------------
A `TypedDict` would catch a missing key and miss a wrong `ast_version`, a
misspelled `op`, or an operand shape the resolver cannot bind. The parser is
the only thing that knows all of those, so the test runs the parser. It needs
no database: `parse_spec` is pure over a document and a field resolver.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.control_plane.app.triggers import ast as trigger_ast
from services.control_plane.app.triggers import registry

pytestmark = pytest.mark.unit


def _seeded_predicates() -> dict[str, dict[str, Any]]:
    """Every predicate document the seed arms, by name.

    Imported from the seed module rather than restated, so a predicate added
    there is covered here without anybody remembering to add it.
    """
    from scripts.seed import obligations

    return {
        name: value
        for name, value in vars(obligations).items()
        if name.endswith("_PREDICATE") and isinstance(value, dict)
    }


#: The REAL resolver, not a stub. It validates each path against the projection
#: whitelist, so this test also catches a seeded predicate that reads a field
#: the registry does not publish -- which a permissive stub would wave through
#: and the trigger would then fail on at arm time, which is the whole failure
#: class this file exists for.
_resolver = registry.resolve_field


def test_the_seed_arms_at_least_one_predicate() -> None:
    """Vacuity guard. An empty mapping would make every test below pass."""
    found = _seeded_predicates()
    assert found, "no *_PREDICATE documents found in scripts/seed/obligations.py"
    assert (
        "_DEPOSIT_PREDICATE" in found
    ), "the hero deposit predicate is gone; the demo's second reveal has no trigger"


@pytest.mark.parametrize("name", sorted(_seeded_predicates()))
def test_every_seeded_predicate_parses(name: str) -> None:
    """The whole point: run the real parser over the real document."""
    document = _seeded_predicates()[name]
    try:
        trigger_ast.parse_spec(document, _resolver)
    except trigger_ast.TriggerSpecError as exc:
        pytest.fail(
            f"{name} does not parse: {type(exc).__name__}: {exc}\n"
            "16_TRIGGER_DSL.md section 6 requires the "
            "{ast_version, bindings, predicate} envelope, with binary operators "
            "taking `left`/`right` and operands shaped {'op': 'FIELD', 'path': ...}."
        )


@pytest.mark.parametrize("name", sorted(_seeded_predicates()))
def test_every_seeded_predicate_declares_the_envelope(name: str) -> None:
    """Checked separately from parsing so a failure says which half is wrong.

    A parser error names a symptom; this names the cause.
    """
    document = _seeded_predicates()[name]
    for key in ("ast_version", "bindings", "predicate"):
        assert key in document, (
            f"{name} has no {key!r}. It is a bare AST node, not the envelope "
            "section 6 specifies -- the shape that was latent until arm time."
        )


def test_the_parser_would_reject_the_old_shape() -> None:
    """Counterfactual. Without this the tests above could pass vacuously if
    ``parse_spec`` ever became permissive."""
    old_shape = {
        "op": "AND",
        "args": [
            {"op": "GT", "args": [{"node": "FIELD", "path": "x"}, {"node": "CONST", "value": 0}]}
        ],
    }
    with pytest.raises(trigger_ast.TriggerSpecError):
        trigger_ast.parse_spec(old_shape, _resolver)
