"""The one guarantee, tested directly.

Everything else in this framework is a convenience. If the view can be made to
return a row after its as-of date -- by an off-by-one at the boundary, by a
dataset whose rows arrive out of order, by an empty frame silently becoming a
full one -- then the probes are checking a property that does not hold and the
promoted artifacts are worthless.

So these tests are deliberately unglamorous and deliberately exhaustive about
the edges.
"""

from __future__ import annotations

from datetime import date

import pytest

from research2prod.data import AsOfView, Dataset, UndeclaredInput


def view(rows, as_of, **params):
    return AsOfView(
        as_of=as_of,
        datasets={"prices": Dataset(name="prices", rows=rows)},
        params=params,
    )


def test_no_row_after_the_as_of_date_is_ever_returned(panel_rows):
    as_of = date(2026, 1, 20)
    frame = view(panel_rows, as_of).frame("prices")
    assert frame.rows
    assert all(row["date"] <= as_of for row in frame)


def test_the_as_of_date_itself_is_included(panel_rows):
    """A boundary that excludes its own day prices yesterday and calls it today."""
    as_of = date(2026, 1, 20)
    frame = view(panel_rows, as_of).frame("prices")
    assert any(row["date"] == as_of for row in frame)


def test_a_date_before_all_history_returns_nothing_rather_than_everything(panel_rows):
    frame = view(panel_rows, date(2025, 1, 1)).frame("prices")
    assert len(frame) == 0


def test_rows_out_of_order_are_still_bounded():
    """Order is a property of the file, not a precondition of the guarantee."""
    rows = [
        {"date": date(2026, 3, 1), "symbol": "UP", "close": 3.0},
        {"date": date(2026, 1, 1), "symbol": "UP", "close": 1.0},
        {"date": date(2026, 2, 1), "symbol": "UP", "close": 2.0},
    ]
    frame = view(rows, date(2026, 2, 1)).frame("prices")
    assert sorted(frame.column("close")) == [1.0, 2.0]


def test_an_undeclared_input_raises_rather_than_returning_empty():
    """Silently returning nothing would look like a thin market, not a bug."""
    with pytest.raises(UndeclaredInput):
        view([], date(2026, 1, 1)).frame("volumes")


def test_an_undeclared_parameter_raises(panel_rows):
    with pytest.raises(KeyError):
        view(panel_rows, date(2026, 1, 20), lookback=5).param("skip")


def test_the_view_records_what_was_touched(panel_rows):
    handle = view(panel_rows, date(2026, 1, 20), lookback=5)
    handle.frame("prices")
    handle.param("lookback")
    assert handle.touched_inputs == {"prices"}
    assert handle.touched_params == {"lookback"}


def test_a_failed_lookup_still_counts_as_touched(panel_rows):
    """Otherwise the declaration probe would score a broken model as tidy."""
    handle = view(panel_rows, date(2026, 1, 20))
    with pytest.raises(UndeclaredInput):
        handle.frame("volumes")
    assert "volumes" in handle.touched_inputs


def test_bitemporal_sources_are_filtered_on_knowledge_time():
    """A value restated later was not knowable on the day it is dated for."""
    rows = [
        {"date": date(2026, 1, 1), "known_at": date(2026, 1, 1), "close": 1.0},
        {"date": date(2026, 1, 2), "known_at": date(2026, 3, 1), "close": 2.0},
    ]
    dataset = Dataset(name="prices", rows=rows, date_field="known_at")
    handle = AsOfView(as_of=date(2026, 1, 5), datasets={"prices": dataset})
    assert handle.frame("prices").column("close") == [1.0]
