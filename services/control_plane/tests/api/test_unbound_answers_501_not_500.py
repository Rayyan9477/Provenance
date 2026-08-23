"""An unbuilt capability answers 501, not 500.

The defect this closes
-----------------------
Every unbound port method raises ``NotImplementedError`` -- deliberately, so
that a missing subsystem cannot be mistaken for an empty result. The catch-all
handler then mapped it, along with everything else, to::

    500 INTERNAL_ERROR
    "Something went wrong on our side. Nothing was committed."

Nothing went wrong. The capability does not exist yet, and the register knows
exactly which subsystem it is waiting on. Judge Mode rendered the consequence
verbatim::

    Trace unreadable.
    GET /v1/traces/f216e462-... returned 500 INTERNAL_ERROR

A reader sees a crash. The truth is a boundary, and this repository has a
founding rule about the difference: ``D-00-005`` -- ``CANNOT RUN`` is not
``FAIL``. A probe that could not connect once reported that a capability had
*failed*, which would have forced a working capability into a permanent
fallback. This is the same confusion one layer up, and it is being shown to a
judge rather than written in a log.

The message is also actively misleading in its second half. "Nothing was
committed" implies a write was attempted and rolled back; for an unbound *read*
nothing was attempted at all.

What 501 buys
-------------
The register's value already names the subsystem in a sentence written to be
read by a person -- "the trace assembler ... needs app/observability to persist
spans first". Carrying that to the client turns an error state into a statement
about what exists, which is the only kind of error state this product should
produce about itself.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.api.errors import DEFAULT_HTTP_STATUS, ErrorCode

pytestmark = pytest.mark.unit


def test_there_is_a_code_for_a_capability_that_does_not_exist_yet() -> None:
    assert hasattr(ErrorCode, "NOT_IMPLEMENTED")


def test_it_maps_to_501_rather_than_500() -> None:
    """501 is 'the server does not support the functionality required'.
    500 is 'the server encountered an unexpected condition'. Only one of those
    is true of a method that has never been written."""
    assert DEFAULT_HTTP_STATUS[ErrorCode.NOT_IMPLEMENTED] == 501


def test_it_is_distinct_from_internal_error() -> None:
    """Collapsing the two is the defect. A test that allowed them to share a
    status would pass on the thing it exists to prevent."""
    assert DEFAULT_HTTP_STATUS[ErrorCode.INTERNAL_ERROR] == 500
    assert (
        DEFAULT_HTTP_STATUS[ErrorCode.NOT_IMPLEMENTED]
        != DEFAULT_HTTP_STATUS[ErrorCode.INTERNAL_ERROR]
    )


def test_its_default_message_does_not_claim_something_went_wrong() -> None:
    from services.control_plane.app.api.errors import DEFAULT_MESSAGE

    message = DEFAULT_MESSAGE[ErrorCode.NOT_IMPLEMENTED]
    assert "went wrong" not in message.lower(), (
        "an unbuilt capability is not a fault; saying so sends a reader to look "
        "for a crash that never happened"
    )
    assert "nothing was committed" not in message.lower(), (
        "that phrasing implies a write was attempted and rolled back; for an "
        "unbound read nothing was attempted at all"
    )
    assert message.strip(), "an empty message names nothing"
