"""The as-of handle. The only way a model reaches data.

A model is given a view, not a dataset. The view is constructed around one
date and cannot return a row after it, so a model that only reads through the
view cannot see the future -- not because it was reviewed carefully, but
because there is nothing there to see.

The filtering happens here rather than in the model for a reason worth saying
plainly. Every look-ahead bug found in production started as a filter the
researcher meant to apply and did not, or applied to the wrong column, or
applied before a later join put the future back. Moving it below the model
removes the whole family at once.

One subtlety about which column to filter on. Where a source is bitemporal --
a value dated for one day but restated on another -- ``date_field`` must point
at *knowledge* time, when the value became known, and never at event time.
Filtering on event time admits restated figures nobody had on the day, which
is the same leak wearing a respectable suit.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class UndeclaredInput(KeyError):
    """Raised when a model asks for a dataset its spec never declared."""


@dataclass
class Frame:
    """Rows a model is allowed to see, already bounded by the as-of date."""

    name: str
    rows: list[dict[str, Any]]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def column(self, key: str) -> list[Any]:
        return [row[key] for row in self.rows]

    def group_by(self, key: str) -> dict[Any, list[dict[str, Any]]]:
        grouped: dict[Any, list[dict[str, Any]]] = {}
        for row in self.rows:
            grouped.setdefault(row[key], []).append(row)
        return grouped

    def latest(self, key: str) -> dict[Any, dict[str, Any]]:
        """The last row per key, in file order.

        Rows arrive already truncated, so "last" is unambiguous: it is the most
        recent observation the model is entitled to.
        """
        return {row[key]: row for row in self.rows}


@dataclass
class Dataset:
    """A named table with the column that decides what a model may see."""

    name: str
    rows: list[dict[str, Any]]
    #: The column consulted by the as-of filter. Where the source is
    #: bitemporal this must be knowledge time, not event time.
    date_field: str = "date"

    def up_to(self, as_of: date) -> list[dict[str, Any]]:
        return [row for row in self.rows if row[self.date_field] <= as_of]


@dataclass
class AsOfView:
    """What a model receives. It has no way to reach past ``as_of``."""

    as_of: date
    datasets: dict[str, Dataset]
    params: dict[str, Any] = field(default_factory=dict)
    #: Every dataset and parameter the body actually touched. The declaration
    #: probe compares this against the spec, which is how a declaration stays
    #: true rather than merely well intentioned.
    touched_inputs: set[str] = field(default_factory=set)
    touched_params: set[str] = field(default_factory=set)

    def frame(self, name: str) -> Frame:
        self.touched_inputs.add(name)
        if name not in self.datasets:
            raise UndeclaredInput(
                f"{name!r} is not available; declared inputs are "
                f"{sorted(self.datasets) or 'none'}"
            )
        return Frame(name=name, rows=self.datasets[name].up_to(self.as_of))

    def param(self, name: str) -> Any:
        self.touched_params.add(name)
        if name not in self.params:
            raise KeyError(
                f"{name!r} is not a declared parameter; declared are "
                f"{sorted(self.params) or 'none'}"
            )
        return self.params[name]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_csv(path: Path | str, *, date_field: str = "date", name: str | None = None) -> Dataset:
    """Read a CSV into a dataset, parsing dates and numbers.

    Deliberately plain: rows are dictionaries and there is no dataframe
    dependency. A real deployment would hand the researcher whatever their shop
    already uses. What must not change is that the truncation happens before
    the frame is built, whatever the frame is made of.
    """
    path = Path(path)
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key == date_field:
                    row[key] = date.fromisoformat(value)
                else:
                    row[key] = _maybe_number(value)
            rows.append(row)
    return Dataset(name=name or path.stem, rows=rows, date_field=date_field)


def _maybe_number(value: str) -> Any:
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
