"""The harness is the same run() many times, plus a rule and a cost.

The panel has one symbol rising a point a day and one falling a point a day.
A signal that scores the riser above the faller is long the right one and
short the wrong one every day, so the harness must report a positive return
on every priced day -- and a signal that scores them the other way round must
lose on every one. Anything else is a bug in the harness, not the signal.
"""

from __future__ import annotations

from datetime import date

import pytest

from asof import model
from asof.backtest import backtest
from asof.config import load_config

TOML = """
[project]
name = "t"
[universe]
source = "prices"
key = "symbol"
price = "close"
[costs]
bps_per_trade = {bps}
[backtest]
portfolio = "{portfolio}"
quantile = 0.5
[backtest.gates]
min_days = {min_days}
min_sharpe = {min_sharpe}
[splits]
discovery = {{ start = 2026-01-01, end = 2026-01-20 }}
validation = {{ start = 2026-01-21, end = 2026-02-09 }}
[[contracts]]
name = "prices"
knowledge_time = "date"
columns = [ {{ name = "date", kind = "date" }}, {{ name = "symbol", kind = "str" }}, {{ name = "close", kind = "float" }} ]
"""


def _cfg(tmp_path, *, bps=0.0, portfolio="long_short", min_days=0, min_sharpe="0.0"):
    path = tmp_path / "asof.toml"
    path.write_text(TOML.format(bps=bps, portfolio=portfolio, min_days=min_days, min_sharpe=min_sharpe), encoding="utf-8")
    return load_config(path)


def _last_close_signal(sign: float):
    @model(name=f"last_close_{'up' if sign > 0 else 'down'}", inputs=["prices"])
    def signal(ctx):
        latest = ctx.frame("prices").latest("symbol")
        return {s: sign * row["close"] for s, row in latest.items()}

    return signal


def test_a_correct_signal_wins_every_day_and_a_reversed_one_loses(world):
    cfg = _cfg(world.parent)
    good = backtest(_last_close_signal(+1), cfg, start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    bad = backtest(_last_close_signal(-1), cfg, start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    priced = [d for d in good.days if d.net is not None]
    assert priced, "the harness realised no returns at all"
    assert all(d.net > 0 for d in priced)
    assert all(d.net < 0 for d in bad.days if d.net is not None)
    assert good.overall.hit_rate == 1.0
    assert good.overall.ann_sharpe > 0 > bad.overall.ann_sharpe


def test_metrics_are_reported_per_split(world):
    cfg = _cfg(world.parent)
    result = backtest(_last_close_signal(+1), cfg, start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    assert set(result.by_split) == {"discovery", "validation"}
    assert result.by_split["validation"].days > 0
    assert result.summary()["by_split"]["validation"]["hit_rate"] == 1.0


def test_costs_come_off_turnover_not_the_signal(world):
    free = backtest(_last_close_signal(+1), _cfg(world.parent, bps=0.0), start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    dear = backtest(_last_close_signal(+1), _cfg(world.parent, bps=50.0), start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    # Day one builds the book (turnover 1.0 for long-short); afterwards the
    # book does not change, so only the first day pays.
    assert dear.days[0].net < free.days[0].net
    assert dear.days[1].net == pytest.approx(free.days[1].net)


def test_the_live_edge_has_no_realised_return_yet(world):
    cfg = _cfg(world.parent)
    result = backtest(_last_close_signal(+1), cfg, start=date(2026, 2, 5), end=date(2026, 2, 9), data_dir=world)
    assert result.days[-1].net is None  # the panel ends 2026-02-09; nothing to realise against
    assert result.days[-1].scores  # but the signal still answered


def test_gates_are_applied_to_the_validation_split(world):
    passing = backtest(_last_close_signal(+1), _cfg(world.parent, min_days=5, min_sharpe="1.0"), start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    assert passing.passed, [g.detail for g in passing.gates]
    failing = backtest(_last_close_signal(-1), _cfg(world.parent, min_days=5, min_sharpe="1.0"), start=date(2026, 1, 5), end=date(2026, 2, 5), data_dir=world)
    assert not failing.passed
    assert any("Sharpe" in g.detail for g in failing.gates if not g.passed)


def test_long_only_never_holds_a_short(world):
    cfg = _cfg(world.parent, portfolio="long_only")
    result = backtest(_last_close_signal(+1), cfg, start=date(2026, 1, 5), end=date(2026, 1, 12), data_dir=world)
    assert all(w > 0 for d in result.days for w in d.weights.values())
