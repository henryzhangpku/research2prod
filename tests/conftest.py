"""Shared fixtures.

The registry is process-global on purpose -- a module registers what it
declares when it is imported -- which means a test that declares a model would
leak it into every test that runs afterwards. The autouse fixture below makes
that impossible, because a suite whose result depends on file order is worse
than no suite.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

import pytest

from asof.model import REGISTRY

#: A short panel with two symbols moving in opposite directions, so that any
#: model reading past its as-of date produces a visibly different answer rather
#: than a slightly different one.
SYMBOLS = ("UP", "DOWN")
START = date(2026, 1, 1)
DAYS = 40


@pytest.fixture(autouse=True)
def clean_registry():
    """Give every test the registry it started with."""
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


@pytest.fixture
def panel_rows() -> list[dict]:
    rows = []
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        rows.append({"date": day, "symbol": "UP", "close": 100.0 + offset})
        rows.append({"date": day, "symbol": "DOWN", "close": 100.0 - offset})
    return rows


@pytest.fixture
def world(tmp_path, panel_rows):
    """A data directory on disk, which the look-ahead probe needs to censor."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "close"])
        writer.writeheader()
        for row in panel_rows:
            writer.writerow({**row, "date": row["date"].isoformat()})
    return data_dir
