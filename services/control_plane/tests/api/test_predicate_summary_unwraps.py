"""`predicate_summary` is handed the stored wrapper, not a bare node.

What went wrong
---------------
The column stores a document around the predicate:

    {"ast_version": "1.0", "bindings": {...}, "predicate": {"op": "AND", ...}}

`render.predicate_summary` was written for the node and `trigger_item` passed it
the document. No `op` matched, every branch fell through, and the final line
returned the fallback -- so `GET /v1/triggers` reported

    "predicate_summary": "No predicate recorded."

for two triggers that each carry a seven-clause predicate, and the Watches
screen printed that sentence under the heading for the one feature it exists to
show. The frontend made the mirror error on the same value and rendered the
literal string ``(undefined )`` beside it.

Why this is the worst shape of bug for this project
---------------------------------------------------
`adapters/unbound.py` states the rule it broke: "An empty list is a lie ... the
one thing a memory product must never do is claim confidently that it has
nothing." A false "No predicate recorded." is exactly that, and it was being
served by the deterministic renderer whose whole purpose is to be checkable.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.api.adapters.render import predicate_summary

pytestmark = pytest.mark.unit


#: The shape `GET /v1/triggers` actually returned on the deployed revision.
LIVE_DOCUMENT = {
    "ast_version": "1.0",
    "bindings": {"deposit": {"id": "eb92a426-7ccb-40ee-ad65-fc6e9539e1bd", "kind": "COMMITMENT"}},
    "predicate": {
        "op": "AND",
        "args": [
            {"op": "NOT_NULL", "args": [{"op": "FIELD", "path": "commitments.deposit.due_at"}]},
            {
                "op": "GTE",
                "args": [
                    {"op": "FIELD", "path": "clock.now"},
                    {"op": "FIELD", "path": "commitments.deposit.due_at"},
                ],
            },
            {
                "op": "GT",
                "args": [
                    {"op": "FIELD", "path": "commitments.deposit.outstanding_amount"},
                    {"op": "CONST", "value": "0"},
                ],
            },
        ],
    },
}


def test_the_stored_wrapper_summarises_its_predicate() -> None:
    """The regression. This returned "No predicate recorded." on the live API."""
    summary = predicate_summary(LIVE_DOCUMENT)

    assert summary != "No predicate recorded.", (
        "the wrapper was reported as carrying no predicate while holding three "
        "clauses; a false 'nothing here' is the one claim a memory product may "
        "never make"
    )
    assert "commitments.deposit.due_at" in summary
    assert "clock.now" in summary
    assert " and " in summary, "an AND over three clauses should join them"


def test_a_bare_node_still_summarises() -> None:
    """The shape the function was originally written for must keep working."""
    assert predicate_summary({"op": "FIELD", "path": "case.status"}) == "case.status"
    assert (
        predicate_summary(
            {
                "op": "NE",
                "args": [
                    {"op": "FIELD", "path": "case.status"},
                    {"op": "CONST", "value": "RESOLVED"},
                ],
            }
        )
        == "case.status is not RESOLVED"
    )


def test_a_wrapper_with_no_predicate_says_so() -> None:
    """Absent is still absent. The fix must not invent a predicate either."""
    assert (
        predicate_summary({"ast_version": "1.0", "bindings": {}, "predicate": None})
        == "No predicate recorded."
    )


def test_a_non_mapping_says_so() -> None:
    for value in (None, "", [], 7):
        assert predicate_summary(value) == "No predicate recorded."


def test_the_summary_never_contains_the_word_undefined() -> None:
    """The frontend's mirror of this bug printed the literal string."""
    for value in (LIVE_DOCUMENT, {"op": "AND", "args": []}, {"predicate": {"op": "OR"}}):
        assert "undefined" not in predicate_summary(value).lower()
