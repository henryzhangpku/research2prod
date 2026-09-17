"""Cross-sectional momentum. What a correct model looks like.

Twelve-one momentum, shrunk to fit a small committed panel: rank each symbol
on its return over a formation window, skipping the most recent few days
because that stretch tends to reverse.

Nothing here is careful about dates, and that is the point. There is no filter
on the as-of date anywhere in the body, because ``ctx.frame`` has already
applied it. The researcher writes the economics; the framework owns the
calendar.
"""

from __future__ import annotations

from asof import Input, Param, model


@model(
    name="momentum",
    description="Cross-sectional price momentum over a formation window, skipping the recent reversal.",
    inputs=[Input(name="prices", description="Daily closes, one row per symbol per day.")],
    params=[
        Param(
            name="lookback_days",
            default=60,
            kind="int",
            description="Length of the formation window, in observations.",
        ),
        Param(
            name="skip_days",
            default=5,
            kind="int",
            description="Most recent observations left out, where short-horizon reversal lives.",
        ),
        Param(
            name="min_history",
            default=40,
            kind="int",
            description="Symbols with less history than this are not scored at all.",
        ),
    ],
    output="score",
)
def momentum(ctx):
    prices = ctx.frame("prices")
    lookback = ctx.param("lookback_days")
    skip = ctx.param("skip_days")
    min_history = ctx.param("min_history")

    scores: dict[str, float] = {}
    for symbol, rows in prices.group_by("symbol").items():
        closes = [row["close"] for row in rows]

        # Drop the reversal window, then take the formation window from what is
        # left. Both are counted back from the most recent observation the view
        # was willing to hand over, which is the as-of date by construction.
        formation = closes[: len(closes) - skip][-lookback:]
        if len(formation) < min_history or formation[0] <= 0:
            continue

        scores[symbol] = round(formation[-1] / formation[0] - 1.0, 6)

    return scores
