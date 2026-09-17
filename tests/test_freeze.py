"""Promotion is a freeze, and replay tells you when the world moved.

A certificate must carry enough for a stranger to reproduce the answer:
the golden output for the checked date, the body's content hash, the
interpreter, the ground and the backtest. Replay then has one job -- say
whether today's run still matches the golden -- and to distinguish "the data
was restated" from "somebody changed the code."
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from asof.config import load_config
from asof.promote import PromotionRefused, promote, replay

AS_OF = date(2026, 2, 5)

TOML = """
[project]
name = "t"
[universe]
source = "prices"
[backtest]
quantile = 0.5
[backtest.gates]
min_days = {min_days}
[splits]
discovery = {{ start = 2026-01-01, end = 2026-01-20 }}
validation = {{ start = 2026-01-21, end = 2026-02-05 }}
[[contracts]]
name = "prices"
knowledge_time = "date"
columns = [ {{ name = "date", kind = "date" }}, {{ name = "symbol", kind = "str" }}, {{ name = "close", kind = "float" }} ]
"""

SIGNAL = '''
from asof import model

@model(name="last", inputs=["prices"])
def last(ctx):
    return {s: row["close"] for s, row in ctx.frame("prices").latest("symbol").items()}
'''


@pytest.fixture
def project(world):
    root = world.parent
    (root / "asof.toml").write_text(TOML.format(min_days=0), encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "last.py").write_text(SIGNAL, encoding="utf-8")
    from asof import registry

    model = registry.load_file(root / "models" / "last.py")["last"]
    return root, model


def test_the_certificate_freezes_everything_a_stranger_needs(project):
    root, model = project
    cfg = load_config(root / "asof.toml")
    artifact = promote(model, AS_OF, data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)
    payload = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert payload["golden"]["as_of"] == AS_OF.isoformat()
    assert payload["golden"]["output"] == {"UP": 135.0, "DOWN": 65.0}
    assert len(payload["golden"]["sha256"]) == 64
    assert payload["source"]["path"] == "last.py" and len(payload["source"]["sha256"]) == 64
    assert payload["environment"]["python"] and "pydantic" in payload["environment"]["packages"]
    assert payload["project_fingerprint"] == cfg.fingerprint()
    assert [c["name"] for c in payload["contracts"]] == ["prices"]
    assert payload["backtest"]["by_split"]["validation"]["days"] > 0
    assert {p["probe"] for p in payload["probes"]} >= {"look-ahead", "determinism", "declaration", "contract"}


def test_backtest_gates_are_part_of_the_gate(project):
    root, model = project
    (root / "asof.toml").write_text(TOML.format(min_days=10_000), encoding="utf-8")
    cfg = load_config(root / "asof.toml")
    with pytest.raises(PromotionRefused) as refused:
        promote(model, AS_OF, data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)
    assert any(v.probe == "backtest.min_days" and not v.passed for v in refused.value.verdicts)


def test_replay_reproduces_then_notices_a_restatement(project):
    root, model = project
    cfg = load_config(root / "asof.toml")
    artifact = promote(model, AS_OF, data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)

    report = replay("last", artifact.version, data_dir=root / "data", promoted_dir=root / "promoted", models_dir=root / "models")
    assert report["reproduces"] and report["source_unchanged"] and report["moved_keys"] == []

    # The data owner restates one close that was already known on the as-of date.
    path = root / "data" / "prices.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    for row in rows:
        if row["symbol"] == "UP" and row["date"] == AS_OF.isoformat():
            row["close"] = "999.0"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "close"])
        writer.writeheader()
        writer.writerows(rows)

    report = replay("last", artifact.version, data_dir=root / "data", promoted_dir=root / "promoted", models_dir=root / "models")
    assert not report["reproduces"]
    assert report["source_unchanged"]
    assert report["moved_keys"] == ["UP"]


def test_replay_blames_the_code_when_the_source_changed(project):
    root, model = project
    cfg = load_config(root / "asof.toml")
    artifact = promote(model, AS_OF, data_dir=root / "data", promoted_dir=root / "promoted", config=cfg)
    (root / "models" / "last.py").write_text(SIGNAL.replace('row["close"]', 'row["close"] * 2'), encoding="utf-8")
    from asof import registry

    registry.load_file(root / "models" / "last.py")  # re-register the edited body
    report = replay("last", artifact.version, data_dir=root / "data", promoted_dir=root / "promoted", models_dir=root / "models")
    assert not report["reproduces"]
    assert not report["source_unchanged"]
