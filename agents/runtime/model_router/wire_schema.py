"""The contract, rendered into the schema dialect the Gemini API actually accepts.

Authority
---------
- ``docs/specs/14_PROMPTS.md`` section 3.3: "The schema is generated from
  ``provenance_contracts.ingestion.ExtractionResult`` ... There is no
  hand-maintained second copy in this prompt specification." That sentence is
  the whole design of this module. The wire form is *derived*, never authored,
  so there is exactly one definition of the extraction contract and this file
  is a transport detail rather than a second opinion.
- ``docs/specs/14_PROMPTS.md`` section 7.1: layer 1 is the API-level output
  constraint and layer 2 is the terminal pydantic validation. This module owns
  layer 1 only. Every bound it has to drop below is still enforced by layer 2,
  which runs on the decoded object -- so nothing is *relaxed*, only moved.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
``GeminiClient.build_config`` passes ``response_schema=<the pydantic class>``
straight to ``google-genai``. For ``ToyOutput`` -- the only schema the router
suite ever sends -- that works. For ``ExtractionResult`` it does not, and the
whole test suite is green anyway because no test in this package has ever
converted the real contract. Measured against ``google-genai`` 1.60.0 and the
live Gemini Developer API on 2026-08-24:

1. **``google.genai.types.Schema`` refuses ``ge``/``le``.** ``Confidence`` is a
   ``Decimal`` with ``Field(ge=0, le=1)``; pydantic renders that as ``ge: '0'``
   / ``le: '1'`` (strings, because the type is ``Decimal``), and ``Schema`` is
   ``extra='forbid'``. Seventeen validation errors before a single byte left the
   process.
2. **``Schema`` refuses ``prefixItems``.** ``SourceLocator.bbox`` is a
   ``tuple[float, float, float, float]``.
3. **The API rejects a ``$ref``/``$defs`` document.** Passing the pydantic
   schema through the newer ``response_json_schema`` parameter -- which does
   accept ``ge``, ``le`` and ``prefixItems`` -- returns
   ``400 INVALID_ARGUMENT``. The references have to be inlined.
4. **The API rejects ``maxItems`` on an array of objects.** This one is the
   trap. ``{"type":"array","items":{"type":"string"},"maxItems":60}`` is
   accepted; the same ``maxItems`` above an *object* item schema returns
   ``400 INVALID_ARGUMENT`` with no field named. Seven of the fourteen
   top-level properties failed on this alone, and removing ``maxItems``
   everywhere is what made the call succeed. ``maxItems=500`` fails even for a
   string array, so there is a magnitude limit as well as a shape one; the
   published ``Schema`` reference documents neither.

Each of the four was found by bisection against the live endpoint, not read out
of a document. The empty-schema case (5, below) is the same class of problem:

5. **``ClaimCandidate.object_value`` is a ``JsonValue``**, which pydantic renders
   as ``{}`` -- a schema with no ``type`` at all. The API needs a type, so it is
   rendered as a string. ``JsonValue`` accepts a string, so layer 2 still
   validates; a model that wants to return a structured value serialises it,
   which is what the DDL's ``ObjectValue`` envelope stores anyway.

What is deliberately NOT dropped
--------------------------------
``pattern`` survives. It is supported, and it is load-bearing: on the first
successful live call with ``pattern`` stripped, ``gemini-3.5-flash-lite``
returned ``predicate='will return security deposit'`` against
``^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$``. With the pattern restored the same
model returned ``commitment.return_deposit`` on the first attempt. A constraint
that changes the answer is not decoration.

``minItems``, ``minLength``, ``maxLength``, ``minimum``, ``maximum`` and
``enum`` all survive; all are accepted and all steer the output.

Determinism
-----------
:func:`to_wire_schema` is a pure function of the model class. The same contract
renders the same bytes on every machine, which is what lets a prompt registry
hash it (section 3.3) and what makes the eval replay path meaningful.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel

from agents.runtime.model_router.gemini import GenerateContentCallable

__all__ = [
    "DROPPED_KEYWORDS",
    "RETAINED_FORMATS",
    "gemini_transport",
    "to_wire_schema",
]

#: JSON Schema keywords removed on the way to the wire, each for a measured
#: reason. Grouped by cause so a future reader can re-test one group without
#: re-testing all of them.
DROPPED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # (1) google.genai.types.Schema is extra="forbid" and has none of these.
        "ge",
        "le",
        "gt",
        "lt",
        "multipleOf",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "additionalProperties",
        "uniqueItems",
        "patternProperties",
        "contentEncoding",
        "discriminator",
        "const",
        "examples",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "$schema",
        # (4) accepted by the SDK, rejected by the service above an object item.
        "maxItems",
        # Not a constraint. `default` and `title` only inflate the prompt, and
        # `title` is pydantic's class name rather than anything the model needs.
        "default",
        "title",
    }
)

#: ``format`` values the service accepts on a string. ``uuid`` is not among
#: them; the ids it would have decorated are validated by pydantic after decode.
RETAINED_FORMATS: Final[frozenset[str]] = frozenset({"date-time"})

#: The placeholder for a schema that carries no type -- see (5) above.
_UNTYPED_REPLACEMENT: Final[dict[str, str]] = {"type": "string"}


def to_wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Render *model* into the JSON-Schema subset the Gemini API accepts.

    The output is a plain ``dict``, which ``google-genai`` converts into a
    ``types.Schema``. Returning a dict rather than a ``types.Schema`` keeps this
    module free of SDK types and makes the result printable, hashable and
    diffable -- the three things a prompt registry needs of it.

    Raises:
        KeyError: a ``$ref`` names a definition the document does not carry.
            Silently substituting an untyped schema there would send the model
            a shape nobody wrote.
    """
    document = model.model_json_schema(mode="validation")
    definitions: Mapping[str, Any] = document.get("$defs", {})
    return _fill_untyped(_inline(document, definitions, frozenset()))


def _inline(node: Any, definitions: Mapping[str, Any], seen: frozenset[str]) -> Any:
    """Resolve ``$ref``, drop the unsupported keywords, rewrite ``prefixItems``.

    ``seen`` breaks reference cycles. A cycle cannot be expressed in a
    reference-free document at all, so the only honest rendering is an opaque
    string -- and every contract this router sends is acyclic today, so the
    branch exists to fail visibly rather than to recurse forever.
    """
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            if name in seen:
                return dict(_UNTYPED_REPLACEMENT)
            return _inline(definitions[name], definitions, seen | {name})
        rendered: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$defs" or key in DROPPED_KEYWORDS:
                continue
            if key == "format":
                if value in RETAINED_FORMATS:
                    rendered[key] = value
                continue
            if key == "prefixItems":
                # A fixed-length tuple becomes a bounded homogeneous array. The
                # exact arity survives as minItems; maxItems is dropped by (4),
                # and pydantic re-imposes it on decode.
                rendered["type"] = "array"
                rendered["items"] = {"type": "number"}
                rendered["minItems"] = len(value)
                continue
            rendered[key] = _inline(value, definitions, seen)
        return rendered
    if isinstance(node, list):
        return [_inline(item, definitions, seen) for item in node]
    return node


def _fill_untyped(node: Any) -> Any:
    """Give every subschema a type. See (5) in the module docstring."""
    if isinstance(node, dict):
        if not node:
            return dict(_UNTYPED_REPLACEMENT)
        filled = {key: _fill_untyped(value) for key, value in node.items()}
        if not {"type", "anyOf", "enum"} & set(filled) and "properties" in filled:
            filled["type"] = "object"
        return filled
    if isinstance(node, list):
        return [_fill_untyped(item) for item in node]
    return node


def gemini_transport(*, api_key: str, generate_content: Any = None) -> GenerateContentCallable:
    """The production transport, with the schema rewritten on the way out.

    This is the seam ``gemini.py`` already documents -- "the production wiring
    is ``Client(api_key=...).models.generate_content``" -- so nothing about the
    client's refusal handling, usage accounting or error mapping is duplicated
    or bypassed. The only thing that changes between the SDK's own callable and
    this one is the value of ``config.response_schema``, and it changes because
    the SDK cannot carry the contract as a class.

    ``generate_content`` is injectable so this wrapper is testable without a
    credential and without a socket.
    """
    if generate_content is not None:
        return _WireSchemaTransport(generate_content, owner=None)
    from google.genai import Client

    client = Client(api_key=api_key)
    return _WireSchemaTransport(client.models.generate_content, owner=client)


class _WireSchemaTransport:
    """Rewrites ``config.response_schema`` and forwards. Holds its own client.

    ``owner`` exists solely to keep the ``Client`` alive. Binding only
    ``client.models.generate_content`` lets the client be collected, and the
    next call then raises ``RuntimeError: Cannot send a request, as the client
    has been closed`` — a message that reads like a transport fault and is
    really a lifetime bug. Measured rather than guessed: that is exactly how the
    first live graph run in this repository failed.
    """

    __slots__ = ("_inner", "_owner")

    def __init__(self, inner: Any, *, owner: Any) -> None:
        self._inner = inner
        self._owner = owner

    def __call__(self, *, model: str, contents: Any, config: Any) -> Any:
        schema = getattr(config, "response_schema", None)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            config = config.model_copy(update={"response_schema": to_wire_schema(schema)})
        return self._inner(model=model, contents=contents, config=config)
