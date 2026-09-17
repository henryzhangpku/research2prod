"""``asof new``: a project a researcher can start writing in immediately.

The scaffold answers every "where do I put it" before it is asked: signals go
in ``signals/``, data in ``data/``, the ground in ``asof.toml``. It ships a
synthetic price panel so ``asof check`` and ``asof backtest`` work before any
real data is wired in, one correct signal to copy from, and a test that runs
the promotion gate over every signal in the project -- so CI refuses what
promotion would refuse, on every push.

Nothing here is a template in the code-generation sense. The example signal
is the same file it would be in production, because that is the only kind of
signal this project knows how to hold.
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

ASOF_TOML = '''# The ground this project stands on. Read once, frozen into every certificate.
# Nothing in here belongs in a signal.

[project]
name = "{name}"
description = ""
models = "signals"
data = "data"
promoted = "promoted"

[calendar]
frequency = "daily"
weekdays_only = true

[universe]
source = "prices"     # the dataset whose keys are scored
key = "symbol"
price = "close"       # what the harness realises returns from

[costs]
bps_per_trade = 5.0

[backtest]
portfolio = "long_short"   # or "long_only"
quantile = 0.2

# Gates a backtest must clear on the validation split before promotion.
# Remove a line to leave that judgement to the researcher.
[backtest.gates]
min_days = 20
min_sharpe = 0.0

# Where published rows go. "file" appends signals/<model>.jsonl; "http" POSTs
# the same rows; "custom" names package.module:function.
[sink]
kind = "file"
target = "signals"

# When the deployed job runs (cron, UTC). 22:30 UTC is after the US close.
schedule = "30 22 * * 1-5"

[splits]
discovery = {{ start = {disc_start}, end = {disc_end} }}
validation = {{ start = {val_start}, end = {val_end} }}
holdout = {{ start = {hold_start} }}

# ---------------------------------------------------------------------------
# Data contracts: what the platform promises. Owned by whoever fixes it.
# A signal may read only promised columns; the contract probe enforces it.
# ---------------------------------------------------------------------------

[[contracts]]
name = "prices"
owner = "platform"
description = "Daily closes, one row per symbol per trading day."
knowledge_time = "date"
freshness = "T+0 18:00 America/New_York"
columns = [
  {{ name = "date", kind = "date", description = "Trading day; also knowledge time for a close." }},
  {{ name = "symbol", kind = "str" }},
  {{ name = "close", kind = "float", description = "Official close, unadjusted." }},
]
'''

EXAMPLE_SIGNAL = '''"""Cross-sectional momentum: the signal to copy from.

Rank each symbol on its return over a formation window, skipping the most
recent days where short-horizon reversal lives. Nothing here filters on the
as-of date -- ``ctx.frame`` has already done that -- and nothing here knows
whether it is being backtested or run for real. Those two facts are what make
this file production code as written.
"""

from __future__ import annotations

from asof import Input, Param, model


@model(
    name="momentum",
    description="Cross-sectional momentum over a formation window, skipping the recent reversal.",
    inputs=[Input(name="prices", description="Daily closes, one row per symbol per day.")],
    params=[
        Param(name="lookback_days", default=60, kind="int", description="Formation window, in observations."),
        Param(name="skip_days", default=5, kind="int", description="Most recent observations left out."),
        Param(name="min_history", default=40, kind="int", description="Symbols with less history are not scored."),
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
        formation = closes[: len(closes) - skip][-lookback:]
        if len(formation) < min_history or formation[0] <= 0:
            continue
        scores[symbol] = round(formation[-1] / formation[0] - 1.0, 6)
    return scores
'''

TEST_SIGNALS = '''"""CI runs the promotion gate. What promotion would refuse, the build refuses.

One test per signal in signals/, parametrised at collection time. A new
signal is covered the moment the file exists; there is nothing to register.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from asof import registry
from asof.config import load_config
from asof.probes import check

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "asof.toml")
SIGNALS = registry.load_dir(ROOT / CONFIG.project.models)
AS_OF = CONFIG.splits.validation.end or date.today()


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_every_signal_passes_the_promotion_gate(name):
    verdicts = check(SIGNALS[name], AS_OF, data_dir=ROOT / CONFIG.project.data, config=CONFIG)
    failed = [f"{v.probe}: {v.detail}" for v in verdicts if not v.passed]
    assert not failed, "\\n".join(failed)
'''

README = '''# {name}

A quant research project on [asof](https://github.com/henryzhangpku/asof): write the
signal once, and it is already production code.

## The five commands

```bash
asof status                                  # where every signal is, and what to do next
asof check momentum --as-of {val_end}        # would promotion accept it?  (look-ahead, determinism, declaration, contract)
asof backtest momentum                       # discovery / validation splits from asof.toml, same code path as production
asof promote momentum --as-of {val_end}      # freeze: certificate with golden output, source hash, environment, backtest
asof export momentum                         # the deployable bundle: runner, Dockerfile, schedule, operator README
```

`git push` after `export` and the job runs on the schedule in `asof.toml`,
publishing `(as_of, key, score, model, version, fingerprint)` rows to the sink.

## Write a signal

Copy `signals/momentum.py`. Declare inputs and parameters; read data only
through `ctx.frame(...)`; return `{{key: score}}`. That is the whole interface.

## Wire real data

Replace `data/prices.csv` with your own, honouring the contract in `asof.toml`
(columns, types, knowledge-time column). Add a `[[contracts]]` block and a
`data/<name>.csv` for every new input. The contract probe refuses a signal that
reads anything the contract does not promise.

## Tests

`pytest` runs the promotion gate over every signal in `signals/`. What
promotion would refuse, CI refuses.
'''

GITIGNORE = '''__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.env

# Exported bundles are committed; the data they run on and what they publish are not
promoted/*/v*/data/
promoted/*/v*/signals/
signals/*.jsonl
'''

PYPROJECT = '''[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["asof"]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''


def synthetic_panel(*, symbols: int = 8, days: int = 400, seed: int = 11, end: date | None = None) -> list[dict]:
    """A committed sample panel so the project works before real data does.

    Random walks with a small, symbol-specific drift, so momentum has
    something to find and the gates have something to refuse.
    """
    rng = random.Random(seed)
    end = end or date.today()
    names = [f"SYM{i:02d}" for i in range(symbols)]
    drifts = {n: rng.gauss(0.0, 0.0012) for n in names}
    prices = {n: 100.0 * math.exp(rng.gauss(0, 0.2)) for n in names}
    rows: list[dict] = []
    day = end - timedelta(days=int(days * 7 / 5) + 7)
    while day <= end:
        if day.weekday() < 5:
            for n in names:
                prices[n] *= math.exp(rng.gauss(drifts[n], 0.012))
                rows.append({"date": day.isoformat(), "symbol": n, "close": round(prices[n], 4)})
        day += timedelta(days=1)
    return rows


def scaffold(name: str, target: Path, *, today: date | None = None) -> list[str]:
    today = today or date.today()
    val_end = today
    val_start = today - timedelta(days=182)
    disc_end = val_start - timedelta(days=1)
    disc_start = today - timedelta(days=548)
    hold_start = today + timedelta(days=1)

    files = {
        "asof.toml": ASOF_TOML.format(name=name, disc_start=disc_start, disc_end=disc_end,
                                      val_start=val_start, val_end=val_end, hold_start=hold_start),
        "signals/momentum.py": EXAMPLE_SIGNAL,
        "tests/test_signals.py": TEST_SIGNALS,
        "README.md": README.format(name=name, val_end=val_end),
        ".gitignore": GITIGNORE,
        "pyproject.toml": PYPROJECT.format(name=name),
        "data/README.md": "One CSV per declared input, named for it. Each must honour its contract in asof.toml.\n",
        "promoted/.gitkeep": "",
    }
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    panel = target / "data" / "prices.csv"
    with panel.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "close"])
        writer.writeheader()
        writer.writerows(synthetic_panel(end=today))
    return sorted([*files, "data/prices.csv"])
