"""The project's fixed facts: ``asof.toml``.

A signal is written against a universe, a calendar, a cost model and a set of
date splits. None of those belong in the signal, because none of them are the
researcher's to decide per-model -- they are the ground the whole project
stands on, and a backtest that quietly used different ground from production
is the classic way a good-looking result fails to reproduce.

So they live in one file at the project root, are read once, and are frozen
into every certificate. Change them and every promoted version says so.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .contracts import DataContract
from .sink import Sink

DEFAULT_CONFIG = Path("asof.toml")

Frequency = Literal["daily", "weekly", "monthly"]


class Split(BaseModel):
    """A named, closed date range. ``end`` of ``None`` means open-ended."""

    start: date
    end: date | None = None

    def contains(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


class Calendar(BaseModel):
    frequency: Frequency = "daily"
    #: Only weekdays are priced unless a project says otherwise. Holidays are
    #: a data-contract matter: a day with no rows is simply not priced.
    weekdays_only: bool = True

    def dates(self, start: date, end: date) -> list[date]:
        """Every as-of date in ``[start, end]`` at this frequency."""
        from datetime import timedelta

        out: list[date] = []
        day = start
        while day <= end:
            if not (self.weekdays_only and day.weekday() >= 5):
                if self.frequency == "daily":
                    out.append(day)
                elif self.frequency == "weekly" and day.weekday() == 4:
                    out.append(day)
                elif self.frequency == "monthly":
                    nxt = day + timedelta(days=1)
                    if nxt.month != day.month:
                        out.append(day)
            day += timedelta(days=1)
        return out


class Universe(BaseModel):
    """Which dataset and columns define the things a signal scores and how they pay off."""

    source: str = "prices"
    key: str = "symbol"
    #: The column the harness realises returns from. The signal never sees a
    #: future value of it; the harness does, because that is what a backtest is.
    price: str = "close"


class Costs(BaseModel):
    """One number, applied by the harness, never by the signal."""

    bps_per_trade: float = 5.0


class Gates(BaseModel):
    """What a backtest must clear on the validation split before promotion.

    Absent gates mean the researcher judges the backtest. Present gates are
    enforced mechanically, and the certificate records which applied.
    """

    min_days: int = 0
    min_sharpe: float | None = None
    min_hit_rate: float | None = None
    max_drawdown: float | None = None


class Backtest(BaseModel):
    """How scores become a portfolio. Fixed per project, never per signal."""

    portfolio: Literal["long_short", "long_only"] = "long_short"
    #: Fraction of the ranked universe on each side.
    quantile: float = 0.2
    gates: Gates = Field(default_factory=Gates)


class Splits(BaseModel):
    discovery: Split
    validation: Split
    holdout: Split | None = None

    def named(self) -> dict[str, Split]:
        out = {"discovery": self.discovery, "validation": self.validation}
        if self.holdout is not None:
            out["holdout"] = self.holdout
        return out

    def of(self, day: date) -> str | None:
        for name, split in self.named().items():
            if split.contains(day):
                return name
        return None


class Project(BaseModel):
    name: str
    description: str = ""
    #: Where signals and data live, relative to asof.toml. A scaffolded
    #: project uses ``signals/`` and ``data/``; this repository's examples use
    #: ``examples/``.
    models: str = "signals"
    data: str = "data"
    promoted: str = "promoted"


class Config(BaseModel):
    """Everything in ``asof.toml``, validated."""

    project: Project
    calendar: Calendar = Field(default_factory=Calendar)
    universe: Universe = Field(default_factory=Universe)
    costs: Costs = Field(default_factory=Costs)
    backtest: Backtest = Field(default_factory=Backtest)
    sink: Sink = Field(default_factory=Sink)
    #: When the deployed job runs, as five-field cron in UTC. The bundle's
    #: workflow uses it verbatim.
    schedule: str = "30 22 * * 1-5"
    splits: Splits
    contracts: list[DataContract] = Field(default_factory=list)

    @field_validator("contracts")
    @classmethod
    def _unique_names(cls, value: list[DataContract]) -> list[DataContract]:
        names = [c.name for c in value]
        if len(names) != len(set(names)):
            raise ValueError("data contracts must have unique names")
        return value

    def contract(self, name: str) -> DataContract | None:
        return next((c for c in self.contracts if c.name == name), None)

    def fingerprint(self) -> str:
        """Content hash of the ground the project stands on."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    path = Path(path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return Config.model_validate(raw)


def find_config(start: Path | str = ".") -> Path | None:
    """Walk up from ``start`` to the nearest ``asof.toml``."""
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        if (candidate / DEFAULT_CONFIG.name).is_file():
            return candidate / DEFAULT_CONFIG.name
    return None
