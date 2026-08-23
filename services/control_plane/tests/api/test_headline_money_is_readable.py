"""A headline is prose, so the amount in it must read as money.

The defect this closes
-----------------------
The attention headline on the dashboard rendered

    The promised date has passed and USD 1800.0000 is still outstanding.

against a screen where every other amount reads ``USD 1,800.00``. The row is
stored at four decimal places -- correctly, because money must not round in the
database -- and the headline interpolated that storage form directly into a
sentence.

It matters more than a cosmetic slip. This is the **first sentence a reader
sees** on the dashboard, and `1800.0000` reads as machine output: it invites the
question "is that eighteen hundred, or has something gone wrong with the
decimal?" on the one number the whole product exists to be trusted about. The
same figure appears formatted three inches below it, so the screen disagrees
with itself.

Why format here rather than send structure
-------------------------------------------
Everywhere else the API sends `{currency, amount}` and the client formats it,
which is right: presentation belongs to the surface. But a headline is composed
server-side by design -- `render.py` is explicit that it is "never
model-generated, and never assembled from free text on the row" -- so the
sentence is already the API's to build, and the amount inside it has to be
built the same way.

The rule is taken from the client's `formatDecimal`, deliberately, so the two
cannot drift: group the integer part in threes, keep at least two decimal
places, drop trailing zeros beyond two, and **keep** significant digits beyond
two, because silently truncating them would change the amount.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.api.adapters.render import format_money_for_prose

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1800.0000", "1,800.00"),
        ("220.0000", "220.00"),
        ("0.0000", "0.00"),
        ("1234567.0000", "1,234,567.00"),
        ("999.9900", "999.99"),
        # Significant digits beyond two places are KEPT. Truncating them would
        # change the amount, which is the one thing a money renderer may never
        # do -- a tenth of a cent dropped from an invoice is still a wrong bill.
        ("10.1234", "10.1234"),
        ("10.1200", "10.12"),
        ("-500.0000", "-500.00"),
    ],
)
def test_it_reads_as_money(amount: str, expected: str) -> None:
    assert format_money_for_prose("USD", amount) == f"USD {expected}"


def test_the_storage_form_never_reaches_the_sentence() -> None:
    """The regression, pinned: four trailing zeros are what this existed to fix."""
    rendered = format_money_for_prose("USD", "1800.0000")
    assert "1800.0000" not in rendered
    assert rendered == "USD 1,800.00"


def test_an_absent_amount_is_not_rendered_as_zero() -> None:
    """`None` means no amount was returned. Zero would be a claim about the
    record, and the opposite one: it says the obligation is discharged."""
    assert format_money_for_prose(None, None) is None
    assert format_money_for_prose("USD", None) is None
    assert format_money_for_prose(None, "1800.0000") is None


def test_it_agrees_with_the_client_formatter() -> None:
    """Both sides implement the same rule; this states it in one place.

    The client's `formatDecimal` is the authority. If these diverge, one screen
    shows `1,800.00` and the sentence above it shows something else, which is
    exactly the disagreement this closes.
    """
    cases = {"1800.0000": "1,800.00", "10.1234": "10.1234", "0.5000": "0.50"}
    for amount, expected in cases.items():
        assert format_money_for_prose("USD", amount) == f"USD {expected}"
