"""The backtest harness. One execution path, many dates.

A backtest here is nothing more than :func:`research2prod.run.run` called once per
as-of date, followed by a fixed portfolio rule and a fixed cost. The signal
does not know it is being backtested, which is the point: there is no
"backtest mode" whose behaviour could differ from production, because there is
no second implementation for it to live in.

What the harness owns, and the signal does not, is the future. Realising a
return needs tomorrow's price; the harness reads it from the universe dataset
*after* the signal has answered for today. The signal's view stayed bounded.
That asymmetry -- evaluator sees ahead, model cannot -- is the entire
difference between a backtest and a leak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config
from .model import DeclaredModel
from .probes import Verdict
from .run import DEFAULT_DATA_DIR, load_datasets, run


@dataclass
class Metrics:
    days: int = 0
    mean_bps: float = 0.0
    ann_sharpe: float = 0.0
    hit_rate: float = 0.0
    max_drawdown: float = 0.0
    avg_turnover: float = 0.0
    total_return: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Day:
    as_of: date
    split: str | None
    scores: dict[str, float]
    weights: dict[str, float]
    turnover: float
    gross: float | None  # None when no next price exists yet (the live edge)
    net: float | None


@dataclass
class BacktestResult:
    model: str
    start: date
    end: date
    project_fingerprint: str
    days: list[Day] = field(default_factory=list)
    by_split: dict[str, Metrics] = field(default_factory=dict)
    gates: list[Verdict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(v.passed for v in self.gates)

    @property
    def overall(self) -> Metrics:
        return _metrics([d for d in self.days if d.net is not None])

    def summary(self) -> dict[str, Any]:
        """What the certificate keeps. Numbers, never the series."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "project_fingerprint": self.project_fingerprint,
            "overall": self.overall.as_dict(),
            "by_split": {k: v.as_dict() for k, v in self.by_split.items()},
            "gates": [{"probe": g.probe, "passed": g.passed, "detail": g.detail} for g in self.gates],
        }


# ---------------------------------------------------------------------------


def backtest(
    model: DeclaredModel,
    config: Config,
    *,
    start: date,
    end: date,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    params: dict[str, Any] | None = None,
) -> BacktestResult:
    datasets = load_datasets(model, data_dir)
    universe = config.universe
    if universe.source not in datasets:
        raise ValueError(
            f"the universe source {universe.source!r} is not among the signal's inputs "
            f"{sorted(datasets)}; the harness needs it to realise returns"
        )
    prices = _price_table(datasets[universe.source].rows, universe.key, universe.price,
                          datasets[universe.source].date_field)

    result = BacktestResult(model.name, start, end, config.fingerprint())
    previous: dict[str, float] = {}
    for day in config.calendar.dates(start, end):
        scores = run(model, day, datasets=datasets, params=params).output
        if not isinstance(scores, dict):
            raise TypeError("a backtestable signal returns {key: score}")
        clean = {k: float(v) for k, v in scores.items() if v is not None and math.isfinite(float(v))}
        weights = _weights(clean, config.backtest.portfolio, config.backtest.quantile)
        turnover = _turnover(previous, weights)
        gross = _realise(weights, prices, day)
        net = None if gross is None else gross - turnover * config.costs.bps_per_trade / 1e4
        result.days.append(Day(day, config.splits.of(day), clean, weights, turnover, gross, net))
        previous = weights

    for name in config.splits.named():
        rows = [d for d in result.days if d.split == name and d.net is not None]
        if rows:
            result.by_split[name] = _metrics(rows)
    result.gates = _apply_gates(config, result.by_split.get("validation"))
    return result


# ---------------------------------------------------------------------------
# The fixed rules
# ---------------------------------------------------------------------------


def _weights(scores: dict[str, float], portfolio: str, quantile: float) -> dict[str, float]:
    if not scores:
        return {}
    ranked = sorted(scores, key=lambda k: (scores[k], k))
    n = max(1, int(round(len(ranked) * quantile)))
    top, bottom = ranked[-n:], ranked[:n]
    weights = {k: 1.0 / n for k in top}
    if portfolio == "long_short":
        if len(ranked) < 2:
            return {}
        for k in bottom:
            weights[k] = weights.get(k, 0.0) - 1.0 / n
    return {k: w for k, w in weights.items() if abs(w) > 1e-12}


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    keys = set(previous) | set(current)
    return sum(abs(current.get(k, 0.0) - previous.get(k, 0.0)) for k in keys) / 2.0


def _price_table(rows, key: str, price: str, date_field: str) -> dict[str, list[tuple[date, float]]]:
    table: dict[str, list[tuple[date, float]]] = {}
    for row in rows:
        value = row.get(price)
        if value is None or value == "":
            continue
        table.setdefault(row[key], []).append((row[date_field], float(value)))
    for series in table.values():
        series.sort()
    return table


def _realise(weights: dict[str, float], prices: dict[str, list[tuple[date, float]]], day: date) -> float | None:
    """Return from today's price to the next available one, weight-summed."""
    if not weights:
        return 0.0
    total = 0.0
    any_next = False
    for key, weight in weights.items():
        series = prices.get(key, [])
        today = next((p for d, p in reversed(series) if d <= day), None)
        nxt = next((p for d, p in series if d > day), None)
        if today is None or nxt is None or today <= 0:
            continue
        any_next = True
        total += weight * (nxt / today - 1.0)
    return total if any_next else None


def _metrics(rows: list[Day]) -> Metrics:
    nets = [d.net for d in rows if d.net is not None]
    if not nets:
        return Metrics()
    n = len(nets)
    mean = sum(nets) / n
    var = sum((x - mean) ** 2 for x in nets) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else 0.0
    equity, peak, dd = 1.0, 1.0, 0.0
    for x in nets:
        equity *= 1.0 + x
        peak = max(peak, equity)
        dd = min(dd, equity / peak - 1.0)
    return Metrics(
        days=n,
        mean_bps=mean * 1e4,
        ann_sharpe=sharpe,
        hit_rate=sum(x > 0 for x in nets) / n,
        max_drawdown=dd,
        avg_turnover=sum(d.turnover for d in rows) / n,
        total_return=equity - 1.0,
    )


def _apply_gates(config: Config, validation: Metrics | None) -> list[Verdict]:
    gates = config.backtest.gates
    out: list[Verdict] = []
    if gates.min_days <= 0 and gates.min_sharpe is None and gates.min_hit_rate is None and gates.max_drawdown is None:
        return out
    if validation is None:
        return [Verdict("backtest", False, "no validation-split results to gate on")]
    if gates.min_days > 0:
        ok = validation.days >= gates.min_days
        out.append(Verdict("backtest.min_days", ok, f"validation days {validation.days} vs floor {gates.min_days}"))
    if gates.min_sharpe is not None:
        ok = validation.ann_sharpe >= gates.min_sharpe
        out.append(Verdict("backtest.min_sharpe", ok, f"validation Sharpe {validation.ann_sharpe:.2f} vs floor {gates.min_sharpe}"))
    if gates.min_hit_rate is not None:
        ok = validation.hit_rate >= gates.min_hit_rate
        out.append(Verdict("backtest.min_hit_rate", ok, f"validation hit rate {validation.hit_rate:.2%} vs floor {gates.min_hit_rate:.0%}"))
    if gates.max_drawdown is not None:
        ok = validation.max_drawdown >= -abs(gates.max_drawdown)
        out.append(Verdict("backtest.max_drawdown", ok, f"validation drawdown {validation.max_drawdown:.2%} vs limit {-abs(gates.max_drawdown):.0%}"))
    return out
