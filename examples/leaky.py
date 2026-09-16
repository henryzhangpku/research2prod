"""The same model with one line left over from the notebook.

This exists to be rejected. It is not a straw man -- it is the single most
common way a research model reaches past its as-of date, and it survives code
review routinely because the offending line looks like housekeeping.

The researcher did most of it right. The universe comes through ``ctx.frame``,
so the declaration is honest. But the price history is still loaded the way it
was loaded in the notebook, with a path and a reader, and that loader knows
nothing about an as-of date. Every run sees the whole file, including rows
dated after the day being priced.

Nobody notices, because the numbers look fine. The backtest looks better than
fine. ``asof check leaky`` notices in about a second.
"""

from __future__ import annotations

import csv
from pathlib import Path

from asof import Input, Param, model


@model(
    name="leaky",
    description="Momentum with a notebook loader left in. Fails the look-ahead probe.",
    inputs=[Input(name="prices", description="Daily closes, one row per symbol per day.")],
    params=[
        Param(name="lookback_days", default=60, kind="int", description="Formation window."),
    ],
    output="score",
)
def leaky(ctx):
    # Honest: the universe is taken from the view, so it respects the as-of date.
    universe = sorted({row["symbol"] for row in ctx.frame("prices")})
    lookback = ctx.param("lookback_days")

    # Not honest: this reads the file. It has no idea what day it is being asked
    # about, so it happily returns rows the model has no business seeing.
    closes: dict[str, list[float]] = {}
    with Path("data/prices.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            closes.setdefault(row["symbol"], []).append(float(row["close"]))

    scores: dict[str, float] = {}
    for symbol in universe:
        series = closes.get(symbol, [])[-lookback:]
        if len(series) < 2 or series[0] <= 0:
            continue
        scores[symbol] = round(series[-1] / series[0] - 1.0, 6)

    return scores
