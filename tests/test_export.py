"""The bundle runs on its own, and the sink honours the output contract.

The strongest test of "instant" is to export a promoted signal into a fresh
directory and execute the generated runner as a subprocess, the way a
scheduler would, with nothing but the bundle and the data. If that produces
contract rows in the sink, there was no engineering step left to do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from asof.config import load_config
from asof.export import export
from asof.promote import promote
from asof.sink import Sink, SignalRow, publish, rows_from_output

TOML = """
[project]
name = "t"
[universe]
source = "prices"
[backtest]
quantile = 0.5
[sink]
kind = "file"
target = "signals"
[splits]
discovery = { start = 2026-01-01, end = 2026-01-20 }
validation = { start = 2026-01-21, end = 2026-02-05 }
[[contracts]]
name = "prices"
owner = "data-team"
knowledge_time = "date"
columns = [ { name = "date", kind = "date" }, { name = "symbol", kind = "str" }, { name = "close", kind = "float" } ]
"""

SIGNAL = '''
from asof import model

@model(name="last", inputs=["prices"])
def last(ctx):
    return {s: row["close"] for s, row in ctx.frame("prices").latest("symbol").items()}
'''


@pytest.fixture
def promoted_project(world):
    root = world.parent
    (root / "asof.toml").write_text(TOML, encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "last.py").write_text(SIGNAL, encoding="utf-8")
    from asof import registry

    model = registry.load_file(root / "models" / "last.py")["last"]
    cfg = load_config(root / "asof.toml")
    promote(model, date(2026, 2, 5), data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)
    return root, cfg


def test_export_lays_out_a_complete_bundle(promoted_project):
    root, cfg = promoted_project
    bundle = export("last", config=cfg, models_dir=root / "models", promoted_dir=root / "promoted",
                    config_path=root / "asof.toml")
    names = {p.relative_to(bundle.path).as_posix() for p in bundle.path.rglob("*") if p.is_file()}
    assert {"run.py", "last.py", "asof.toml", "certificate.json", "requirements.txt", "Dockerfile",
            "README.md", ".github/workflows/last.yml"} <= names
    readme = (bundle.path / "README.md").read_text(encoding="utf-8")
    assert "data-team" in readme and "`prices.csv`" in readme
    assert "pydantic==" in (bundle.path / "requirements.txt").read_text(encoding="utf-8")


def test_the_generated_runner_runs_as_a_subprocess_and_publishes_contract_rows(promoted_project):
    root, cfg = promoted_project
    bundle = export("last", config=cfg, models_dir=root / "models", promoted_dir=root / "promoted",
                    config_path=root / "asof.toml")
    # A scheduler mounts the data; here we copy it in.
    (bundle.path / "data" / "prices.csv").write_bytes((root / "data" / "prices.csv").read_bytes())

    completed = subprocess.run(
        [sys.executable, "run.py", "--as-of", "2026-02-05"],
        cwd=bundle.path, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    published = bundle.path / "signals" / "last.jsonl"
    rows = [json.loads(line) for line in published.read_text(encoding="utf-8").splitlines()]
    assert {r["key"] for r in rows} == {"UP", "DOWN"}
    assert all(set(r) == {"as_of", "key", "score", "model", "version", "fingerprint", "published_at"} for r in rows)
    assert all(r["as_of"] == "2026-02-05" and r["model"] == "last" and r["version"] == 1 for r in rows)


def test_a_non_pricing_day_is_a_clean_no_op(promoted_project):
    root, cfg = promoted_project
    bundle = export("last", config=cfg, models_dir=root / "models", promoted_dir=root / "promoted",
                    config_path=root / "asof.toml")
    completed = subprocess.run([sys.executable, "run.py", "--as-of", "2026-02-07"],  # a Saturday
                               cwd=bundle.path, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0
    assert "not a pricing day" in completed.stdout
    assert not (bundle.path / "signals" / "last.jsonl").exists()


def test_rows_from_output_refuses_anything_but_key_score():
    with pytest.raises(TypeError):
        rows_from_output([1, 2, 3], as_of=date(2026, 1, 1), model="m", version=1, fingerprint="f")
    rows = rows_from_output({"B": 2.0, "A": None, "C": 1}, as_of=date(2026, 1, 1), model="m", version=1, fingerprint="f")
    assert [r.key for r in rows] == ["B", "C"] and rows[1].score == 1.0


def test_custom_sink_receives_the_rows(tmp_path, monkeypatch):
    received = {}
    module = tmp_path / "mysink.py"
    module.write_text("def take(rows, sink):\n    import json; open('got.json','w').write(json.dumps([r.as_dict() for r in rows]))\n    return 'took'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rows = [SignalRow("2026-01-01", "X", 1.5, "m", 1, "f", "now")]
    assert publish(rows, Sink(kind="custom", target="mysink:take")) == "took"
    assert json.loads((tmp_path / "got.json").read_text())[0]["key"] == "X"
