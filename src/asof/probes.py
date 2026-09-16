"""The promotion gate. Three questions, asked mechanically.

A code review asks whether a model looks like it peeks. These ask whether it
does, by changing the world underneath it and watching whether the answer
moves. That difference is the whole argument for the framework: a convention
holds until somebody is in a hurry, and a probe holds afterwards.

None of the three is clever. Each is a string comparison between two runs, and
each fails closed -- an error inside a probe is a failed probe, never a passed
one, because the alternative is a gate that quietly approves whatever it could
not evaluate.
"""

from __future__ import annotations

import contextlib
import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import DeclaredModel
from .run import DEFAULT_DATA_DIR, load_datasets, run


@dataclass
class Verdict:
    """One probe's answer, with the reason attached.

    The reason is not decoration. A gate that says "failed" and nothing else
    gets argued with; one that says which row moved gets fixed.
    """

    probe: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# 1. Look-ahead
# ---------------------------------------------------------------------------


def look_ahead(
    model: DeclaredModel, as_of: date, *, data_dir: Path | str = DEFAULT_DATA_DIR
) -> Verdict:
    """Does the answer change when the future is physically removed?

    The model is run twice: once against the real data directory, and once in a
    working directory whose data has been truncated at the as-of date. A model
    that reads through ``ctx.frame`` sees identical rows both times, because
    the view had already bounded them. A model that reached around the view --
    opening the CSV itself, which is exactly what a notebook prototype does --
    sees a different file the second time, and its answer moves.

    What this does not catch, stated plainly: a model reading an absolute path,
    or fetching over the network. The probe swaps the working directory, not
    the universe. It catches the mistake researchers actually make, which is
    leaving ``read_csv("data/prices.csv")`` in the body after wrapping it.
    """
    data_dir = Path(data_dir).resolve()
    try:
        honest = run(model, as_of, data_dir=data_dir)
        with _truncated_world(model, as_of, data_dir) as sandbox:
            with _chdir(sandbox):
                censored = run(model, as_of, data_dir=sandbox / data_dir.name)
    except Exception as exc:  # noqa: BLE001 - a probe that errors has not passed
        return Verdict("look-ahead", False, f"probe could not complete: {exc}")

    if honest.canonical == censored.canonical:
        return Verdict(
            "look-ahead",
            True,
            "removing every row after the as-of date did not move the answer",
        )
    return Verdict(
        "look-ahead",
        False,
        "the answer changed when rows after the as-of date were removed, so the "
        "model read data it was not entitled to -- most likely by opening a file "
        "directly instead of going through ctx.frame()",
    )


# ---------------------------------------------------------------------------
# 2. Determinism
# ---------------------------------------------------------------------------


def determinism(
    model: DeclaredModel, as_of: date, *, data_dir: Path | str = DEFAULT_DATA_DIR
) -> Verdict:
    """Does the same input produce the same bytes twice?

    Catches unseeded randomness, iteration over a set, and anything that reads
    the wall clock. All three are survivable in research and none is survivable
    in a series somebody settles against, because a value nobody can reproduce
    cannot be defended when it is disputed.
    """
    try:
        first = run(model, as_of, data_dir=data_dir)
        second = run(model, as_of, data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001
        return Verdict("determinism", False, f"probe could not complete: {exc}")

    if first.canonical == second.canonical:
        return Verdict("determinism", True, "two runs produced identical output")
    return Verdict(
        "determinism",
        False,
        "two runs of the same model on the same date disagreed -- look for "
        "randomness without a seed, iteration over a set, or a call to the clock",
    )


# ---------------------------------------------------------------------------
# 3. Declaration
# ---------------------------------------------------------------------------


def declaration(
    model: DeclaredModel, as_of: date, *, data_dir: Path | str = DEFAULT_DATA_DIR
) -> Verdict:
    """Did the body use exactly what the spec declared?

    Reaching for an undeclared input already raises, so this probe is really
    about the other direction: an input or parameter that is declared and never
    touched. That is not cosmetic. The declaration is what a reviewer reads
    instead of the body, and what the planned composer would render as a field.
    A knob that does nothing is a lie in both places.
    """
    try:
        result = run(model, as_of, data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001
        return Verdict("declaration", False, f"probe could not complete: {exc}")

    unused_inputs = sorted(model.spec.input_names - result.touched_inputs)
    unused_params = sorted(model.spec.param_names - result.touched_params)
    if not unused_inputs and not unused_params:
        return Verdict(
            "declaration",
            True,
            f"{len(result.touched_inputs)} input(s) and "
            f"{len(result.touched_params)} parameter(s), all declared and all used",
        )

    parts = []
    if unused_inputs:
        parts.append(f"inputs declared but never read: {', '.join(unused_inputs)}")
    if unused_params:
        parts.append(f"parameters declared but never used: {', '.join(unused_params)}")
    return Verdict("declaration", False, "; ".join(parts))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

PROBES = (look_ahead, determinism, declaration)


def check(
    model: DeclaredModel, as_of: date, *, data_dir: Path | str = DEFAULT_DATA_DIR
) -> list[Verdict]:
    """Run every probe. All must pass before a model may be promoted."""
    return [probe(model, as_of, data_dir=data_dir) for probe in PROBES]


# ---------------------------------------------------------------------------
# Building the censored world
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _truncated_world(model: DeclaredModel, as_of: date, data_dir: Path):
    """A temporary directory holding the same data with the future cut off."""
    with tempfile.TemporaryDirectory(prefix="asof-probe-") as tmp:
        sandbox = Path(tmp)
        target = sandbox / data_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for name, dataset in load_datasets(model, data_dir).items():
            rows = dataset.up_to(as_of)
            path = target / f"{name}.csv"
            if not rows:
                path.write_text("", encoding="utf-8")
                continue
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
        yield sandbox


@contextlib.contextmanager
def _chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
