"""The one key.

Promotion is the whole product in one verb. A researcher who has a model
working runs it, and either gets a versioned production artifact or gets told
which probe stopped them and why. Nothing in between, and no conversation with
an engineer in either branch.

Two rules make the artifact worth having.

A failed probe refuses. There is no flag to force one through, because a gate
with an override is a suggestion, and the whole reason a researcher can be
trusted to promote their own work is that the gate cannot be talked round.

And promotion appends. Re-promoting writes version two; it never edits version
one. Somebody may have run against version one, and a record you can rewrite
is not a record.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path

from .backtest import backtest
from .config import Config
from .model import DeclaredModel
from .probes import Verdict, check
from .run import run

#: Where promoted artifacts land. Committed, not generated at deploy time --
#: the promotion history is meant to be read in the repository.
DEFAULT_PROMOTED_DIR = Path("promoted")


class PromotionRefused(RuntimeError):
    """Raised when a probe failed. Carries the verdicts that caused it."""

    def __init__(self, verdicts: list[Verdict]):
        self.verdicts = verdicts
        failed = ", ".join(v.probe for v in verdicts if not v.passed)
        super().__init__(f"promotion refused by: {failed}")


@dataclass
class Artifact:
    """A promoted version, as written to disk."""

    model: str
    version: int
    path: Path
    fingerprint: str
    promoted_at: str

    @property
    def label(self) -> str:
        return f"{self.model} v{self.version}"


def promote(
    model: DeclaredModel,
    as_of: date,
    *,
    data_dir: Path | str = "data",
    promoted_dir: Path | str = DEFAULT_PROMOTED_DIR,
    config: Config | None = None,
) -> Artifact:
    """Check, then freeze. Raises :class:`PromotionRefused` if any probe fails.

    The freeze records everything a stranger needs to reproduce the answer:
    the declaration, the body's content hash, the interpreter and library
    versions, the project ground, the contracts, the backtest the researcher
    was satisfied with, and the *golden output* -- the answer for the checked
    date, so a later replay can say whether the world was restated underneath
    the signal.
    """
    verdicts = check(model, as_of, data_dir=data_dir, config=config)
    summary = None
    if config is not None:
        splits = config.splits
        bt = backtest(model, config, start=splits.discovery.start,
                      end=splits.validation.end or as_of, data_dir=data_dir)
        verdicts.extend(bt.gates)
        summary = bt.summary()
    if not all(v.passed for v in verdicts):
        raise PromotionRefused(verdicts)
    golden = run(model, as_of, data_dir=data_dir)

    promoted_dir = Path(promoted_dir)
    target = promoted_dir / model.name
    target.mkdir(parents=True, exist_ok=True)
    version = _next_version(target)
    promoted_at = datetime.now(UTC).isoformat(timespec="seconds")

    payload = {
        "model": model.name,
        "version": version,
        "fingerprint": model.spec.fingerprint(),
        "promoted_at": promoted_at,
        "checked_as_of": as_of.isoformat(),
        "spec": model.spec.model_dump(mode="json"),
        # The ground the signal was checked on. A later certificate with a
        # different project fingerprint changed the world, not the signal.
        "project_fingerprint": config.fingerprint() if config else None,
        "contracts": [
            c.model_dump(mode="json")
            for c in (config.contracts if config else [])
            if c.name in model.spec.input_names
        ],
        "probes": [
            {"probe": v.probe, "passed": v.passed, "detail": v.detail} for v in verdicts
        ],
        "backtest": summary,
        "golden": {"as_of": as_of.isoformat(), "output": golden.output,
                   "sha256": hashlib.sha256(golden.canonical.encode()).hexdigest()},
        "source": _source_record(model),
        "environment": _environment_record(),
    }
    path = target / f"v{version}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    return Artifact(
        model=model.name,
        version=version,
        path=path,
        fingerprint=payload["fingerprint"],
        promoted_at=promoted_at,
    )


def _source_record(model: DeclaredModel) -> dict:
    """Where the body lives and what it hashed to. The declaration fingerprint
    covers the spec; this covers the code."""
    try:
        path = Path(inspect.getsourcefile(model.fn) or "")
        text = path.read_text(encoding="utf-8")
        return {"path": path.name, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "function": model.fn.__name__}
    except (OSError, TypeError):
        return {"path": None, "sha256": None, "function": getattr(model.fn, "__name__", None)}


def _environment_record() -> dict:
    """Enough to rebuild the interpreter a certificate was produced in."""
    packages = {}
    for name in ("asof", "pydantic", "typer", "rich"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "platform": sys.platform, "packages": packages}


def replay(name: str, version: int, *, data_dir: Path | str = "data",
           promoted_dir: Path | str = DEFAULT_PROMOTED_DIR, models_dir: Path | str = "examples") -> dict:
    """Re-run a promoted version on its checked date and compare to the golden.

    Same answer: the world has not moved under the signal. A different answer
    with the same source hash: the data was restated -- which is information
    about the data, not a defect in the signal, and exactly the thing a series
    somebody settles against needs to know about.
    """
    from . import registry

    payload = next((p for p in history(name, promoted_dir=promoted_dir) if p["version"] == version), None)
    if payload is None:
        raise KeyError(f"{name} v{version} was never promoted")
    model = registry.get(name, models_dir)
    now = run(model, date.fromisoformat(payload["golden"]["as_of"]), data_dir=data_dir)
    same = hashlib.sha256(now.canonical.encode()).hexdigest() == payload["golden"]["sha256"]
    source_same = _source_record(model)["sha256"] == payload["source"]["sha256"]
    moved = []
    if not same and isinstance(now.output, dict) and isinstance(payload["golden"]["output"], dict):
        before, after = payload["golden"]["output"], now.output
        moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {"model": name, "version": version, "as_of": payload["golden"]["as_of"],
            "reproduces": same, "source_unchanged": source_same, "moved_keys": moved}


def history(
    name: str, *, promoted_dir: Path | str = DEFAULT_PROMOTED_DIR
) -> list[dict]:
    """Every promoted version of a model, oldest first."""
    target = Path(promoted_dir) / name
    if not target.exists():
        return []
    versions = sorted(target.glob("v*.json"), key=_version_of)
    return [json.loads(p.read_text(encoding="utf-8")) for p in versions]


def _next_version(target: Path) -> int:
    existing = [_version_of(p) for p in target.glob("v*.json")]
    return max(existing, default=0) + 1


def _version_of(path: Path) -> int:
    return int(path.stem.lstrip("v"))
