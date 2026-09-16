"""Run one model for one as-of date, the same way every time.

There is one execution path. Research runs, probe runs and production runs all
come through here, so there is no second implementation to drift away from the
first -- which is the failure this framework exists to remove. A model that
behaves one way in a notebook and another way in production is not a
deployment problem, it is two models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .data import AsOfView, Dataset, load_csv
from .model import DeclaredModel

#: Where datasets live unless told otherwise. One CSV per declared input,
#: named for it.
DEFAULT_DATA_DIR = Path("data")


@dataclass
class RunResult:
    """One model, one date, and everything needed to compare two runs."""

    model: str
    as_of: date
    output: Any
    touched_inputs: set[str] = field(default_factory=set)
    touched_params: set[str] = field(default_factory=set)

    @property
    def canonical(self) -> str:
        """The output as bytes two runs can be compared on.

        Sorted keys and no whitespace, so an equal result is equal text. Every
        probe in this framework is ultimately a string comparison against this,
        which is what makes their verdicts checkable rather than advisory.
        """
        return json.dumps(self.output, sort_keys=True, separators=(",", ":"), default=str)


def load_datasets(
    model: DeclaredModel, data_dir: Path | str = DEFAULT_DATA_DIR
) -> dict[str, Dataset]:
    """Load exactly the datasets the spec declares, and no others.

    A model cannot read what was never loaded, so the declaration is enforced
    twice: once here by absence, and once by the declaration probe by
    comparison. The first is what stops the mistake; the second is what tells
    the researcher they made it.
    """
    data_dir = Path(data_dir)
    datasets: dict[str, Dataset] = {}
    for declared in model.spec.inputs:
        path = data_dir / f"{declared.name}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"model {model.name!r} declares input {declared.name!r}, "
                f"but {path} does not exist"
            )
        datasets[declared.name] = load_csv(path, name=declared.name)
    return datasets


def run(
    model: DeclaredModel,
    as_of: date,
    *,
    datasets: dict[str, Dataset] | None = None,
    params: dict[str, Any] | None = None,
    data_dir: Path | str = DEFAULT_DATA_DIR,
) -> RunResult:
    """Execute a model. The only way a model is ever executed."""
    resolved = dict(model.spec.defaults)
    resolved.update(params or {})
    view = AsOfView(
        as_of=as_of,
        datasets=datasets if datasets is not None else load_datasets(model, data_dir),
        params=resolved,
    )
    output = model(view)
    return RunResult(
        model=model.name,
        as_of=as_of,
        output=output,
        touched_inputs=set(view.touched_inputs),
        touched_params=set(view.touched_params),
    )
