"""``_fetch_all`` must not turn a row into its own column names.

The defect this closes
-----------------------
``_fetch_all`` built its result with::

    dict(zip(columns, record, strict=True))

``record`` is a tuple under psycopg's default row factory, and that is correct.
But a caller that opens its connection with ``row_factory=dict_row`` hands back
a **mapping**, and iterating a mapping yields its *keys*. The lengths match, so
``strict=True`` never fires, and every value is silently replaced by the name of
its own column::

    columns   = ['id', 'distance', 'text']
    tuple row -> {'id': 7,    'distance': 0.42,       'text': 'hello'}
    dict  row -> {'id': 'id', 'distance': 'distance', 'text': 'text'}

No exception, no warning, correct-looking shape. A retrieval ranking would sort
by the string ``"distance"`` for every candidate -- a total order that is
perfectly stable and carries no information, so the symptom is not a crash but
*plausible, wrong results*.

``strict=True`` is the detail worth dwelling on. It was added to catch exactly
this class -- a row that does not line up with its description -- and it cannot,
because the failure preserves length. A guard that looks like it covers a case
and does not is worse than no guard, because it stops anyone looking again.

Found while building the eval harness, which hit it in a spike.
"""

from __future__ import annotations

import pytest

from provenance_db.repositories._execute import _rows_as_mappings

pytestmark = pytest.mark.unit

COLUMNS = ["id", "distance", "text"]


def test_a_tuple_row_maps_positionally() -> None:
    """The ordinary path, pinned so the repair cannot break it."""
    rows = _rows_as_mappings(COLUMNS, [(7, 0.42, "hello")])
    assert rows == [{"id": 7, "distance": 0.42, "text": "hello"}]


def test_a_mapping_row_keeps_its_values() -> None:
    """The regression. Previously every value became its column name."""
    record = {"id": 7, "distance": 0.42, "text": "hello"}
    rows = _rows_as_mappings(COLUMNS, [record])
    assert rows == [{"id": 7, "distance": 0.42, "text": "hello"}]
    assert rows[0]["distance"] == 0.42, "the value must not be the string 'distance'"


def test_no_value_is_ever_its_own_key() -> None:
    """States the corruption directly, so a future rewrite cannot reintroduce
    it in a different shape and still pass the two tests above."""
    for row in _rows_as_mappings(COLUMNS, [{"id": 7, "distance": 0.42, "text": "hello"}]):
        for key, value in row.items():
            assert value != key, f"{key} was replaced by its own column name"


def test_a_short_tuple_row_still_raises() -> None:
    """The repair must not weaken the check that DID work.

    A genuine length mismatch is a real disagreement between the statement and
    its description, and must stay loud.
    """
    with pytest.raises(ValueError):
        _rows_as_mappings(COLUMNS, [(7, 0.42)])


def test_a_mapping_row_missing_a_column_raises() -> None:
    """The equivalent check on the mapping path.

    Accepting a mapping must not mean accepting *any* mapping -- a row whose
    keys disagree with `cursor.description` is the same disagreement the tuple
    path refuses, and silently returning it would trade one quiet wrong answer
    for another.
    """
    with pytest.raises(ValueError):
        _rows_as_mappings(COLUMNS, [{"id": 7, "distance": 0.42}])


def test_an_empty_result_is_an_empty_list() -> None:
    assert _rows_as_mappings(COLUMNS, []) == []
