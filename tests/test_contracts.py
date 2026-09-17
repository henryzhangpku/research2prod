"""The contract probe: what the platform promises is what a signal may use.

Each test builds a small project on disk -- data directory plus ``research2prod.toml``
-- and asks the probe the same question the promotion gate would. The
interesting cases are the ones that pass code review: a column that exists in
the extract but is not promised, and a signal whose declared input has no
owner at all.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from research2prod import Param, model
from research2prod.config import Config, load_config
from research2prod.contracts import Column, DataContract, check_rows
from research2prod.probes import contract

AS_OF = date(2026, 1, 20)

BASE_TOML = """
[project]
name = "t"
[splits]
discovery = {{ start = 2026-01-01, end = 2026-01-15 }}
validation = {{ start = 2026-01-16, end = 2026-02-10 }}
{contracts}
"""

PRICES_CONTRACT = """
[[contracts]]
name = "prices"
owner = "platform"
knowledge_time = "date"
columns = [
  {{ name = "date", kind = "date" }},
  {{ name = "symbol", kind = "str" }},
  {{ name = "close", kind = "{close_kind}" }},
]
"""


def _config(tmp_path: Path, contracts: str) -> Config:
    path = tmp_path / "research2prod.toml"
    path.write_text(BASE_TOML.format(contracts=contracts), encoding="utf-8")
    return load_config(path)


def _honest_model():
    @model(name="m", inputs=["prices"], params=[Param(name="n", default=3, kind="int")])
    def m(ctx):
        rows = ctx.frame("prices")
        n = ctx.param("n")
        return {s: len(r[-n:]) for s, r in rows.group_by("symbol").items()}

    return m


def test_a_conforming_project_passes(world):
    cfg = _config(world.parent, PRICES_CONTRACT.format(close_kind="float"))
    verdict = contract(_honest_model(), AS_OF, data_dir=world, config=cfg)
    assert verdict.passed, verdict.detail


def test_no_config_is_a_failure_not_a_pass(world):
    verdict = contract(_honest_model(), AS_OF, data_dir=world, config=None)
    assert not verdict.passed
    assert "research2prod.toml" in verdict.detail


def test_a_declared_input_without_a_contract_fails(world):
    cfg = _config(world.parent, "")
    verdict = contract(_honest_model(), AS_OF, data_dir=world, config=cfg)
    assert not verdict.passed
    assert "no data contract" in verdict.detail


def test_reading_an_unpromised_column_is_schema_drift(world):
    """The column is in the extract. Production does not promise it."""
    cfg = _config(world.parent, PRICES_CONTRACT.format(close_kind="float"))

    @model(name="drift", inputs=["prices"])
    def drift(ctx):
        # ``close`` is promised. ``symbol`` is promised. Reading a column the
        # research extract happens to carry is the mistake.
        return {row["symbol"]: row["close"] for row in ctx.frame("prices")}

    ok = contract(drift, AS_OF, data_dir=world, config=cfg)
    assert ok.passed, ok.detail

    @model(name="drift2", inputs=["prices"])
    def drift2(ctx):
        return {row["symbol"]: row.get("adj_close", row["close"]) for row in ctx.frame("prices")}

    bad = contract(drift2, AS_OF, data_dir=world, config=cfg)
    assert not bad.passed
    assert "adj_close" in bad.detail


def test_a_type_violation_in_the_data_fails(world):
    cfg = _config(world.parent, PRICES_CONTRACT.format(close_kind="str"))
    verdict = contract(_honest_model(), AS_OF, data_dir=world, config=cfg)
    assert not verdict.passed
    assert "promised str" in verdict.detail


def test_knowledge_time_must_match_the_as_of_column(world):
    cfg = _config(
        world.parent,
        PRICES_CONTRACT.format(close_kind="float").replace('knowledge_time = "date"', 'knowledge_time = "known_at"'),
    )
    verdict = contract(_honest_model(), AS_OF, data_dir=world, config=cfg)
    assert not verdict.passed
    assert "knowledge-time" in verdict.detail


def test_check_rows_reports_missing_and_mistyped():
    c = DataContract(name="x", columns=[Column(name="a", kind="int"), Column(name="b", kind="float")])
    rows = [{"date": date(2026, 1, 1), "a": "oops"}]
    problems = {p.column: p.problem for p in check_rows(c, rows)}
    assert "b" in problems and "missing" in problems["b"]
    assert "a" in problems and "promised int" in problems["a"]


def test_config_rejects_duplicate_contract_names(tmp_path):
    with pytest.raises(ValueError):
        _config(tmp_path, PRICES_CONTRACT.format(close_kind="float") * 2)


def test_config_fingerprint_is_stable_and_content_addressed(tmp_path):
    a = _config(tmp_path, PRICES_CONTRACT.format(close_kind="float")).fingerprint()
    b = _config(tmp_path, PRICES_CONTRACT.format(close_kind="float")).fingerprint()
    c = _config(tmp_path, PRICES_CONTRACT.format(close_kind="int")).fingerprint()
    assert a == b != c
