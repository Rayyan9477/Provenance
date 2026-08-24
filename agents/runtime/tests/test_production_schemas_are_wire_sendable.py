"""The schemas this build actually sends must be sendable.

The defect this closes
-----------------------
`ExtractionResult` — the response schema the ingestion graph uses on every
extraction — **cannot be given to `google.genai` at all**. `types.Schema` is
`extra="forbid"` and rejects the `ge`/`le` that `Confidence` emits and the
`prefixItems` that `bbox` emits: seventeen validation errors, raised before any
request leaves the process.

The 252-test model-router suite is green, and `ExtractionResult` appears in it
**zero times**. Every test sends a `ToyOutput` defined in the test file. So the
suite proved that the router can send *a* Pydantic model, and the one model
production actually sends was never tried. The first live extraction failed
instantly.

That is the vacuity failure in its purest form: not an assertion that checks
nothing, but a whole suite checking a stand-in for the thing under test. A
reader counting 252 green tests concludes the transport works.

Why this test rather than more router tests
--------------------------------------------
The router tests are about the router: retries, budgets, finish reasons. They
are right to use a small schema. What was missing is a check that the *real*
schemas survive the conversion, and that belongs in one place that enumerates
them rather than scattered through transport tests.

`to_wire_schema` is what the runner uses to make these sendable — it inlines
`$ref`s and strips the keywords `types.Schema` forbids. This asserts the
production schemas round-trip through it, so a new field with an unsupported
constraint fails here rather than at the end of a paid model call.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agents.runtime.model_router.wire_schema import to_wire_schema
from provenance_contracts.ingestion import ExtractionResult

pytestmark = pytest.mark.unit

#: Every Pydantic model this build hands to a model as a response schema.
#: Named explicitly rather than discovered: a discovery rule that found none
#: would make this file pass while proving nothing, which is the failure it
#: exists to close.
PRODUCTION_RESPONSE_SCHEMAS: tuple[type[BaseModel], ...] = (ExtractionResult,)

#: Keywords `google.genai.types.Schema` forbids. `extra="forbid"` means any one
#: of these anywhere in the document is a hard validation error, not a warning
#: and not a silently-ignored field.
FORBIDDEN_KEYWORDS = (
    "$ref",
    "$defs",
    "prefixItems",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "additionalProperties",
    "allOf",
    "const",
)


def _walk(node: object) -> list[str]:
    """Every forbidden keyword present anywhere in the document."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYWORDS:
                found.append(key)
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


def test_there_is_at_least_one_production_schema() -> None:
    """A vacuity guard on the guard. An empty tuple passes everything below."""
    assert PRODUCTION_RESPONSE_SCHEMAS


def test_the_raw_pydantic_schema_is_the_thing_that_could_not_be_sent() -> None:
    """Pins the defect rather than only the repair.

    If `ExtractionResult` were ever simplified to the point where its raw
    JSON Schema became sendable, this would fail — and that is the correct
    outcome: it would mean `to_wire_schema` is no longer load-bearing and the
    test below has quietly stopped proving anything.
    """
    raw = ExtractionResult.model_json_schema()
    assert _walk(raw), (
        "the raw schema now contains no forbidden keyword, so the conversion "
        "below is a no-op and this file no longer guards anything"
    )


@pytest.mark.parametrize("model", PRODUCTION_RESPONSE_SCHEMAS, ids=lambda m: m.__name__)
def test_it_converts_to_a_document_gemini_accepts(model: type[BaseModel]) -> None:
    wire = to_wire_schema(model)
    offending = sorted(set(_walk(wire)))
    assert not offending, (
        f"{model.__name__} still carries {offending} after conversion. "
        "`types.Schema` is extra=forbid, so this raises before the request is "
        "sent — and the router suite cannot catch it, because every test there "
        "sends a ToyOutput instead."
    )


@pytest.mark.parametrize("model", PRODUCTION_RESPONSE_SCHEMAS, ids=lambda m: m.__name__)
def test_the_converted_document_still_describes_the_model(model: type[BaseModel]) -> None:
    """Stripping keywords must not strip the schema.

    An empty object satisfies the assertion above perfectly, and would send
    cleanly while telling the model nothing about the shape it must return.
    """
    wire = to_wire_schema(model)
    assert wire.get("type") == "object", wire
    properties = wire.get("properties")
    assert isinstance(properties, dict) and properties, "conversion produced no properties"
    for field in model.model_fields:
        assert field in properties, f"{field} was lost in conversion"
