"""`asof new` produces a project whose first five commands succeed.

This is the promise of the whole tool, so it is tested as one flow: scaffold,
check, backtest (gates pass on the shipped sample panel), promote, export,
and the exported bundle runs as a subprocess and publishes contract rows.
If any step here needs a human, the scaffold has a bug.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

from asof import registry
from asof.backtest import backtest
from asof.config import load_config
from asof.export import export
from asof.probes import check
from asof.promote import promote, replay
from asof.scaffold import scaffold

TODAY = date(2026, 9, 16)  # pinned: the sample panel's dates depend on it


def test_the_first_five_commands_succeed(tmp_path):
    root = tmp_path / "alpha-lab"
    created = scaffold("alpha-lab", root, today=TODAY)
    assert {"asof.toml", "signals/momentum.py", "tests/test_signals.py", "data/prices.csv", "README.md"} <= set(created)

    cfg = load_config(root / "asof.toml")
    assert cfg.project.models == "signals" and cfg.contract("prices") is not None
    model = registry.load_dir(root / "signals")["momentum"]

    # check
    verdicts = check(model, TODAY, data_dir=root / "data", config=cfg)
    assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]

    # backtest: the shipped panel must let the shipped signal clear the shipped gates
    result = backtest(model, cfg, start=cfg.splits.discovery.start, end=TODAY, data_dir=root / "data")
    assert result.passed, [g.detail for g in result.gates if not g.passed]
    assert result.by_split["validation"].days >= 20

    # promote
    artifact = promote(model, TODAY, data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)
    assert artifact.version == 1

    # export: bundle plus the root workflow GitHub actually runs
    bundle = export("momentum", config=cfg, models_dir=root / "signals", promoted_dir=root / "promoted",
                    config_path=root / "asof.toml")
    workflow = root / ".github" / "workflows" / "asof-momentum.yml"
    assert workflow.is_file()
    assert "working-directory: promoted/momentum/v1" in workflow.read_text(encoding="utf-8")

    # the bundle runs like a scheduler would run it
    (bundle.path / "data" / "prices.csv").write_bytes((root / "data" / "prices.csv").read_bytes())
    completed = subprocess.run([sys.executable, "run.py", "--as-of", TODAY.isoformat()],
                               cwd=bundle.path, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr
    rows = [json.loads(l) for l in (bundle.path / "signals" / "momentum.jsonl").read_text().splitlines()]
    assert len(rows) == 8 and all(r["version"] == 1 for r in rows)

    # and the promoted answer reproduces
    assert replay("momentum", 1, data_dir=root / "data", promoted_dir=root / "promoted", models_dir=root / "signals")["reproduces"]


def test_scaffold_refuses_nothing_but_is_deterministic(tmp_path):
    a = scaffold("a", tmp_path / "a", today=TODAY)
    b = scaffold("b", tmp_path / "b", today=TODAY)
    assert a == b
    assert (tmp_path / "a" / "data" / "prices.csv").read_bytes() == (tmp_path / "b" / "data" / "prices.csv").read_bytes()
